"""Save detector coverage diagnostics for a fixed val or test split."""

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.analysis.detection_audit import audit_sequence
from src.tracking.evaluation import GT_COLUMNS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "outputs/ua_detrac/dataset_v1.1"
    )
    parser.add_argument(
        "--detections", type=Path, default=ROOT / "outputs/detections_full_yolo26l_E17"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs/tracking/validation_v2/detection_audit",
    )
    parser.add_argument("--split", choices=["val", "test"], default="val")
    args = parser.parse_args()
    metadata = pd.read_csv(args.dataset / "metadata/sequences.csv")
    names = metadata.loc[metadata.split.eq(args.split), "sequence"].tolist()
    metadata = metadata.set_index("sequence")
    gt = pd.concat(
        [
            c[c.sequence.isin(names)]
            for c in pd.read_csv(
                args.dataset / "canonical/annotations.csv",
                usecols=GT_COLUMNS,
                chunksize=200000,
            )
        ]
    )
    frames = pd.read_csv(args.dataset / "metadata/annotated_frames.csv")
    regions = pd.read_csv(args.dataset / "canonical/ignored_regions.csv")
    args.output.mkdir(parents=True, exist_ok=True)
    tables = []
    for sequence in names:
        logging.info("Audit detector %s", sequence)
        info = metadata.loc[sequence]
        result = audit_sequence(
            gt[gt.sequence.eq(sequence)],
            pd.read_csv(args.detections / f"{sequence}.csv"),
            frames.loc[frames.sequence.eq(sequence), "frame_id"],
            regions.loc[regions.sequence.eq(sequence), ["x", "y", "w", "h"]].to_numpy(
                float
            ),
            info.width,
            info.height,
        )
        result["sequence"] = sequence
        result["height_bin"] = pd.cut(
            result.h,
            [0, 16, 32, 64, float("inf")],
            labels=["0-16", "16-32", "32-64", "64+"],
        )
        result.to_csv(args.output / f"{sequence}.csv", index=False)
        tables.append(result)
    all_gt = pd.concat(tables)
    fields = ["assigned_same_iou07", "assigned_same_iou05", "assigned_any_iou07"]
    for keys, filename in [
        (["sequence"], "per_sequence.csv"),
        (["class_id", "height_bin"], "per_class_size.csv"),
    ]:
        grouped = all_gt.groupby(keys, observed=True)
        result = grouped[fields].mean().join(grouped.size().rename("gt_observations"))
        result.to_csv(args.output / filename)
    logging.info("Audited %d GT observations", len(all_gt))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    main()
