"""Revalidate exports, then create portable, self-contained tar artifacts."""
import argparse
import json
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from src.data.split import load_splits
from src.data.utils import read_yaml, sha256, write_json
from src.data.validate import validate_exports


def package(dataset, artifacts):
    dataset, artifacts = Path(dataset).resolve(), Path(artifacts).resolve()
    metadata = dataset / "metadata"
    if json.loads((metadata / "validation_report.json").read_text())["status"] != "PASSED":
        raise ValueError("Packaging requires validation PASSED")
    manifest = json.loads((metadata / "manifest.json").read_text())
    sequences = pd.read_csv(metadata / "sequences.csv").set_index("sequence").to_dict("index")
    annotated_frames = pd.read_csv(metadata / "annotated_frames.csv")
    for sequence, rows in annotated_frames.groupby("sequence"):
        sequences[sequence]["annotated_frame_ids"] = rows.frame_id.astype(int).tolist()
    images = {s: {f: dataset / "tracking" / s / "img1" / f"{f:06d}.jpg"
                  for f in range(1, m["num_frames"] + 1)} for s, m in sequences.items()}
    classes = read_yaml(metadata / "classes.yaml")["classes"]
    validate_exports(dataset, pd.read_csv(dataset / "canonical/annotations.csv").fillna({"occlusion_xml": ""}),
        images, sequences, load_splits(metadata), read_yaml(metadata / "preprocessing.yaml"), classes)
    if manifest["visual_inspection"] != "PASSED":
        raise ValueError("Run visual notebook and explicitly record visual inspection PASSED before release")
    artifacts.mkdir(parents=True, exist_ok=True)
    version = manifest["version"]
    if not version or any(c not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ._-" for c in version):
        raise ValueError("Invalid version")
    suffix = f"v{version}" + ("_smoke" if manifest["scope"] == "smoke" else "")
    paths = [artifacts / f"ua_detrac_{name}_{suffix}.tar" for name in ("detection", "tracking_gt", "metadata")]
    if any(p.exists() for p in paths) or (artifacts / f"manifest_{suffix}.json").exists():
        raise FileExistsError("Artifact version already exists")
    for path, folder in zip(paths, ("detection", "tracking", "metadata")):
        with tarfile.open(path, "w", dereference=True) as archive:
            archive.add(dataset / folder, arcname=folder)
            if folder != "metadata":
                archive.add(metadata / "manifest.json", arcname=f"{folder}/manifest.json")
                archive.add(dataset / "canonical/ignored_regions.csv", arcname=f"{folder}/ignored_regions.csv")
            if folder == "metadata":
                archive.add(dataset / "canonical", arcname="canonical")
        print(f"Created {path}", flush=True)
    write_json(artifacts / f"manifest_{suffix}.json", dict(manifest,
        artifacts={p.name: dict(sha256=sha256(p), bytes=p.stat().st_size) for p in paths}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--artifacts", type=Path, default=ROOT / "data/artifacts")
    args = parser.parse_args()
    package(args.dataset, args.artifacts)
