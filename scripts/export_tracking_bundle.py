"""Export class-aware evaluation inputs and preprocessed per-class MOT files."""

import argparse
import configparser
import csv
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.tracking.common import CLASS_NAMES, sha256_file, write_json
from src.tracking.evaluation import GT_COLUMNS, PROTOCOL, filter_evaluation_boxes


def compact_frames(table, mapping):
    """Keep only scored frames, including their original gaps in a separate map."""
    result = table[table.frame_id.isin(mapping)].copy()
    result["frame_id"] = result.frame_id.map(mapping).astype(int)
    return result


def copy_csv_subset(source, target, sequences, columns=None):
    """Retain numeric text exactly so export cannot perturb IoU threshold ties."""
    with Path(source).open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = columns or reader.fieldnames
        with Path(target).open("w", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=columns)
            writer.writeheader()
            for row in reader:
                if row["sequence"] in sequences:
                    writer.writerow({key: row[key] for key in columns})


def camera_review_note(manifest):
    """Describe the exported run's actual review state instead of an old count."""
    provenance = manifest.get("provenance", {})
    cameras = provenance.get("cameras", {})
    if not cameras:
        return "Camera review metadata was not recorded; inspect run provenance. "
    reviewed = sum(c.get("status") == "reviewed" for c in cameras.values())
    total = len(cameras)
    if reviewed == total and provenance.get("calibration_reviewed"):
        return f"All {reviewed}/{total} test cameras passed reviewed preflight. "
    return (
        f"{total - reviewed}/{total} test camera records remain unreviewed, "
        "or reviewed preflight was not enforced; camera quality is provisional. "
    )


def export_bundle(dataset, runs, output):
    """Keep original outputs and provide explicitly class-separated MOT inputs."""
    dataset, output = Path(dataset), Path(output)
    if output.exists():
        raise FileExistsError(f"Choose a fresh export directory: {output}")
    metadata = pd.read_csv(dataset / "metadata/sequences.csv")
    annotations = pd.read_csv(dataset / "metadata/annotated_frames.csv")
    ignored = pd.read_csv(dataset / "canonical/ignored_regions.csv")
    manifests, selected = {}, set()
    for split, run in runs.items():
        manifest = json.loads((Path(run) / "run_manifest.json").read_text())
        names = set(manifest["sequences"])
        expected = set(metadata.loc[metadata.split.eq(split), "sequence"])
        if names != expected or manifest["status"] != "complete":
            raise ValueError(f"Expected a complete fixed {split} run")
        if names & selected:
            raise ValueError("Run sequence sets overlap")
        selected.update(names)
        manifests[split] = manifest
    canonical = pd.concat(
        [
            chunk[chunk.sequence.isin(selected)]
            for chunk in pd.read_csv(
                dataset / "canonical/annotations.csv",
                usecols=GT_COLUMNS,
                chunksize=100_000,
            )
        ],
        ignore_index=True,
    )
    data = output / "evaluation_input"
    (data / "canonical").mkdir(parents=True)
    (data / "metadata").mkdir()
    for relative, columns in [
        ("canonical/annotations.csv", GT_COLUMNS),
        ("canonical/ignored_regions.csv", None),
        ("metadata/annotated_frames.csv", None),
        ("metadata/sequences.csv", None),
    ]:
        copy_csv_subset(dataset / relative, data / relative, selected, columns)
    indexed = metadata.set_index("sequence")
    mapping_rows, counts = [], []
    for split, run in runs.items():
        run = Path(run)
        target = output / "runs" / split
        for folder in ["tracks", "mot", "evaluation"]:
            shutil.copytree(run / folder, target / folder)
        shutil.copy2(run / "run_manifest.json", target / "run_manifest.json")
        for sequence in sorted(manifests[split]["sequences"]):
            info = indexed.loc[sequence]
            frames = sorted(
                annotations.loc[annotations.sequence.eq(sequence), "frame_id"]
            )
            mapping = {int(frame): i for i, frame in enumerate(frames, 1)}
            mapping_rows.extend(
                dict(sequence=sequence, split=split, frame_id=f, evaluation_frame_id=i)
                for f, i in mapping.items()
            )
            regions = ignored.loc[
                ignored.sequence.eq(sequence), ["x", "y", "w", "h"]
            ].to_numpy()
            gt = canonical[canonical.sequence.eq(sequence)]
            pred = pd.read_csv(run / "tracks" / f"{sequence}.csv")
            gt = filter_evaluation_boxes(
                gt[gt.frame_id.isin(mapping)], regions, info.width, info.height
            )
            pred = filter_evaluation_boxes(
                pred[pred.frame_id.isin(mapping)], regions, info.width, info.height
            )
            gt, pred = compact_frames(gt, mapping), compact_frames(pred, mapping)
            counts.append(
                dict(
                    sequence=sequence,
                    split=split,
                    gt=len(gt),
                    pred=len(pred),
                    frames=len(frames),
                )
            )
            for class_id, name in CLASS_NAMES.items():
                benchmark = f"UADETRAC-{name}-{split}"
                seq = output / "mot_challenge/gt" / benchmark / sequence
                (seq / "gt").mkdir(parents=True)
                config = configparser.ConfigParser()
                config["Sequence"] = dict(
                    name=sequence,
                    imDir="img1",
                    frameRate="25",
                    seqLength=str(len(frames)),
                    imWidth=str(int(info.width)),
                    imHeight=str(int(info.height)),
                    imExt=".jpg",
                )
                with (seq / "seqinfo.ini").open("w") as stream:
                    config.write(stream)
                columns = ["frame_id", "track_id", "x", "y", "w", "h"]
                g = gt.loc[gt.class_id.eq(class_id), columns].copy()
                # Each folder is one UA class; 1 is the stock loader's placeholder.
                g["valid"], g["class"], g["visibility"] = 1, 1, 1
                g.to_csv(
                    seq / "gt/gt.txt", header=False, index=False, float_format="%.17g"
                )
                p = pred.loc[
                    pred.class_id.eq(class_id), columns + ["confidence"]
                ].copy()
                p["u1"], p["u2"], p["u3"] = -1, -1, -1
                folder = (
                    output / "mot_challenge/trackers" / benchmark / "UCMCTrack/data"
                )
                folder.mkdir(parents=True, exist_ok=True)
                p.to_csv(
                    folder / f"{sequence}.txt",
                    header=False,
                    index=False,
                    float_format="%.17g",
                )
        for name in CLASS_NAMES.values():
            path = output / "mot_challenge/seqmaps" / f"UADETRAC-{name}-{split}.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "name\n" + "\n".join(sorted(manifests[split]["sequences"])) + "\n"
            )
    pd.DataFrame(mapping_rows).to_csv(
        output / "original_to_evaluation_frames.csv", index=False
    )
    pd.DataFrame(counts).to_csv(output / "preprocessing_counts.csv", index=False)
    write_json(output / "protocol.json", PROTOCOL)
    write_json(
        output / "class_mapping.json", {str(k): v for k, v in CLASS_NAMES.items()}
    )
    (output / "README.md").write_text(
        "# UA-DETRAC UCMCTrack: shared TrackEval inputs\n\n"
        "Original 25-FPS frame IDs are retained in runs/{val,test}/tracks/*.csv "
        "and mot/*.txt. CSV class IDs are 0=car, 1=bus, 2=van, 3=others. "
        "MOT TXT is classless: do not use it alone for class-aware evaluation.\n\n"
        "## Reproduce the project evaluator\n\nFrom the accompanying code checkout:\n\n"
        "```\npython scripts/tracking_pipeline.py evaluate --dataset BUNDLE/evaluation_input "
        "--output BUNDLE/runs/test\n```\n\nRepeat with runs/val. No images, detector, "
        "tracker or calibration fitting are needed for scoring saved outputs. "
        "evaluation_input contains the selected canonical GT columns, annotation "
        "timeline, sequence metadata and ignore regions. It is an evaluation copy, "
        "not a replacement for the source dataset.\n\n"
        "## Common MOTChallenge/TrackEval loader\n\n"
        "mot_challenge separates the four UA classes into four benchmarks. "
        "GT/predictions are already filtered symmetrically by union ignore at "
        "50% visible overlap and scored-frame membership. Frame IDs are compacted "
        "using original_to_evaluation_frames.csv; empty XML frames remain. "
        "IDs persist across unannotated gaps. Do not apply ignore filtering again.\n\n"
        "Use DO_PREPROC=False, CLEAR/Identity THRESHOLD=0.7, HOTA standard 0.05:0.05:0.95. "
        "Use seqmaps/UADETRAC-car-test.txt, benchmark UADETRAC-car and split test "
        "(and similarly bus/van/others and val). GT class=1 and visibility=1 are "
        "loader placeholders inside each class folder, not UA class IDs or measured "
        "occlusion. All original class information is in CSV/folder names. "
        "Combine underlying metric statistics across classes with TrackEval "
        "combine_classes_det_averaged; do not average four HOTA scores.\n\n"
        "The accompanying evaluator includes the documented CLEAR empty-frame "
        "state-reset patch (docs/UCMC_LOCAL_PATCHES.md). All compared trackers "
        "must use the same evaluator/patch; unpatched CLEAR counts can differ.\n\n"
        "Test is a single val-selected offline configuration, without test tuning. "
        + camera_review_note(manifests.get("test", {}))
        + "See the reports. "
        "This is project protocol v2, not official UA-DETRAC PR-MOTA.\n",
        encoding="utf-8",
    )
    checksums = {
        str(p.relative_to(output)).replace("\\", "/"): sha256_file(p)
        for p in output.rglob("*")
        if p.is_file()
    }
    write_json(output / "checksums.json", checksums)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "outputs/ua_detrac/dataset_v1.1"
    )
    parser.add_argument(
        "--val-run",
        type=Path,
        default=ROOT / "outputs/tracking/refinement_v3/runs/shared_vote_boundary_10",
    )
    parser.add_argument(
        "--test-run", type=Path, default=ROOT / "outputs/tracking/test_final_v3/run"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs/tracking/delivery_v3/trackeval_bundle",
    )
    args = parser.parse_args()
    rows = export_bundle(
        args.dataset, {"val": args.val_run, "test": args.test_run}, args.output
    )
    print(f"Exported {len(rows)} sequences")


if __name__ == "__main__":
    main()
