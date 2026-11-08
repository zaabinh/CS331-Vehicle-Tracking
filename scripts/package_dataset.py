"""Package validated UA-DETRAC exports into portable TAR artifacts.

By default, trusts the existing PASSED validation report and avoids expensive
full-image revalidation. Use --revalidate to run the full export validation.
Hashes each TAR as it is written (including TAR headers, padding and trailers).
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import tarfile
import time
from pathlib import Path

import pandas as pd
from tqdm.auto import tqdm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.split import load_splits
from src.data.utils import read_yaml, write_json
from src.data.validate import validate_exports

LOGGER = logging.getLogger(__name__)


class HashingWriter:
    """Hash exactly the bytes successfully written to the destination file."""

    def __init__(self, fileobj):
        self.fileobj = fileobj
        self._hash = hashlib.sha256()

    def write(self, data):
        count = self.fileobj.write(data)
        if count is None:
            raise OSError("Destination stream returned an unknown byte count")
        if count:
            self._hash.update(memoryview(data)[:count])
        return count

    def flush(self):
        return self.fileobj.flush()

    def tell(self):
        return self.fileobj.tell()

    def hexdigest(self):
        return self._hash.hexdigest()


def _contents(source, arcname):
    """Return TAR members (including directories) and total regular-file bytes.

    Resolve symlinks to files as tarfile(..., dereference=True) would do.
    Directory symlinks are deliberately excluded to avoid accidental cycles.
    """
    if not source.is_dir():
        raise NotADirectoryError(source)

    members = [(source, arcname, 0)]
    total_bytes = 0
    for root, dirs, files in os.walk(source, followlinks=False):
        dirs.sort()
        files.sort()
        base = Path(root)
        # Add explicit directory records so empty dirs survive extraction.
        for dirname in list(dirs):
            path = base / dirname
            if path.is_symlink():
                # Dereferenced directory symlinks would be recursive and may
                # escape the dataset tree, so reject them explicitly.
                raise ValueError(f"Directory symlinks are not supported: {path}")
            members.append((path, f"{arcname}/{path.relative_to(source).as_posix()}", 0))
        for filename in files:
            path = base / filename
            if not path.is_file():
                raise ValueError(f"Expected a regular file: {path}")
            size = path.stat().st_size
            members.append((path, f"{arcname}/{path.relative_to(source).as_posix()}", size))
            total_bytes += size
    return members, total_bytes


def _archive_contents(dataset, folder):
    members, total = _contents(dataset / folder, folder)
    if folder != "metadata":
        extras = [
            (dataset / "metadata/manifest.json", f"{folder}/manifest.json"),
            (dataset / "canonical/ignored_regions.csv", f"{folder}/ignored_regions.csv"),
        ]
        for source, arcname in extras:
            if not source.is_file():
                raise FileNotFoundError(source)
            size = source.stat().st_size
            members.append((source, arcname, size))
            total += size
    else:
        canonical, canonical_total = _contents(dataset / "canonical", "canonical")
        members.extend(canonical)
        total += canonical_total
    return members, total


def _write_archive(path, members, total_bytes, description):
    """Write a TAR and return its complete SHA256 without reading it again."""
    started = time.monotonic()
    with path.open("xb") as raw:
        writer = HashingWriter(raw)
        with tqdm(
            total=total_bytes,
            desc=description,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            dynamic_ncols=True,
        ) as bar:
            # Stream mode makes all TAR writes sequential, including final
            # blocks produced as the TarFile context manager exits.
            with tarfile.open(fileobj=writer, mode="w|", dereference=True) as archive:
                for source, arcname, size in members:
                    archive.add(source, arcname=arcname, recursive=False)
                    if size:
                        bar.update(size)
        # Exiting tarfile.open has written EOF records and any final padding.
        writer.flush()
        digest = writer.hexdigest()
    return {"sha256": digest, "bytes": path.stat().st_size}, time.monotonic() - started


def _full_revalidation(dataset, metadata):
    LOGGER.info("Revalidating complete dataset (including JPEG decode)...")
    sequences = pd.read_csv(metadata / "sequences.csv").set_index("sequence").to_dict("index")
    annotated_frames = pd.read_csv(metadata / "annotated_frames.csv")
    for sequence, rows in annotated_frames.groupby("sequence"):
        sequences[sequence]["annotated_frame_ids"] = rows.frame_id.astype(int).tolist()
    images = {
        sequence: {
            frame: dataset / "tracking" / sequence / "img1" / f"{frame:06d}.jpg"
            for frame in range(1, int(meta["num_frames"]) + 1)
        }
        for sequence, meta in sequences.items()
    }
    classes = read_yaml(metadata / "classes.yaml")["classes"]
    validate_exports(
        dataset,
        pd.read_csv(dataset / "canonical/annotations.csv").fillna({"occlusion_xml": ""}),
        images,
        sequences,
        load_splits(metadata),
        read_yaml(metadata / "preprocessing.yaml"),
        classes,
    )


def package(dataset, artifacts, *, revalidate=False):
    dataset, artifacts = Path(dataset).resolve(), Path(artifacts).resolve()
    metadata = dataset / "metadata"
    if dataset == artifacts or dataset in artifacts.parents or artifacts in dataset.parents:
        raise ValueError("Dataset and artifact directories must be separate trees")
    report = json.loads((metadata / "validation_report.json").read_text(encoding="utf-8"))
    if report.get("status") != "PASSED" and not revalidate:
        raise ValueError("Packaging requires validation PASSED; use --revalidate to run validation again")

    if revalidate:
        _full_revalidation(dataset, metadata)
    else:
        LOGGER.info("Skipping full revalidation (existing validation: PASSED)")

    manifest = json.loads((metadata / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("visual_inspection") != "PASSED":
        raise ValueError("Visual inspection must be PASSED before packaging")
    version = str(manifest.get("version") or "")
    if not version or any(c not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ._-" for c in version):
        raise ValueError("Invalid version")
    suffix = f"v{version}" + ("_smoke" if manifest["scope"] == "smoke" else "")
    destinations = {
        folder: artifacts / f"ua_detrac_{name}_{suffix}.tar"
        for folder, name in (("detection", "detection"), ("tracking", "tracking_gt"), ("metadata", "metadata"))
    }
    manifest_dest = artifacts / f"manifest_{suffix}.json"
    if any(p.exists() for p in (*destinations.values(), manifest_dest)):
        raise FileExistsError("Artifact version already exists")

    # Enumerate source files once to calculate byte-weighted progress.
    plans = {folder: _archive_contents(dataset, folder) for folder in destinations}
    artifacts.mkdir(parents=True, exist_ok=True)

    # Use temporary files so a failed run never looks like a complete release.
    temps = {folder: p.with_name(p.name + ".partial") for folder, p in destinations.items()}
    if any(p.exists() for p in temps.values()):
        raise FileExistsError("Leftover .partial artifact exists; inspect/remove it before retrying")

    checksums = {}
    produced = []
    try:
        for index, (folder, dest) in enumerate(destinations.items(), 1):
            members, total = plans[folder]
            LOGGER.info("[%d/3] %s: %d entries, %.2f GiB input", index, folder,
                        len(members), total / (1024 ** 3))
            info, elapsed = _write_archive(temps[folder], members, total, f"Packaging {folder}")
            checksums[dest.name] = info
            LOGGER.info("Completed %s: %.2f GiB, SHA256 %s, %.1fs", dest.name,
                        info["bytes"] / (1024 ** 3), info["sha256"], elapsed)
        # Publish only after all TARs have been written successfully.
        # Individual renames are atomic, but publication of the whole set is not.
        for folder, dest in destinations.items():
            os.replace(temps[folder], dest)
            produced.append(dest)
        write_json(manifest_dest, dict(manifest, artifacts=checksums))
        LOGGER.info("Packaging complete: %s", manifest_dest)
    except BaseException:
        for partial in temps.values():
            partial.unlink(missing_ok=True)
        # A failed manifest write may have left incomplete output.
        manifest_dest.unlink(missing_ok=True)
        for dest in produced:
            dest.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="Prepared dataset directory")
    parser.add_argument("--artifacts", type=Path, default=ROOT / "data/artifacts")
    parser.add_argument("--revalidate", action="store_true",
                        help="Run full export validation again before packaging")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    package(args.dataset, args.artifacts, revalidate=args.revalidate)


if __name__ == "__main__":
    main()
