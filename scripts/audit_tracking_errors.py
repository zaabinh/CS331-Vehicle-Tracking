"""Attribute saved-run FN to observable detector candidate categories."""

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from src.analysis.tracking_cases import diagnose
from src.tracking.evaluation import GT_COLUMNS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "outputs/ua_detrac/dataset_v1.1"
    )
    parser.add_argument("--run", type=Path)
    parser.add_argument(
        "--detector-audit",
        type=Path,
        default=ROOT / "outputs/tracking/validation_v2/detection_audit",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs/tracking/validation_v2/reference_error_audit",
    )
    args = parser.parse_args()
    dataset = args.dataset
    output = ROOT / "outputs/tracking/validation_v2"
    if args.run:
        source = args.run
        evaluation = source / "evaluation"
    else:
        selection = json.loads((output / "selected_saved_config.json").read_text())
        config_id = selection["config_id"]
        source = ROOT / "outputs/tracking/tuning_h_approved_v1/runs" / config_id
        evaluation = output / "reevaluation" / config_id
    expected = pd.read_csv(evaluation / "per_sequence.csv").set_index("sequence")
    metadata = pd.read_csv(dataset / "metadata/sequences.csv").set_index("sequence")
    gt = pd.concat(
        [
            c[c.sequence.isin(expected.index)]
            for c in pd.read_csv(
                dataset / "canonical/annotations.csv",
                usecols=GT_COLUMNS,
                chunksize=200000,
            )
        ]
    )
    frames = pd.read_csv(dataset / "metadata/annotated_frames.csv")
    regions = pd.read_csv(dataset / "canonical/ignored_regions.csv")
    destination = args.output
    destination.mkdir(parents=True, exist_ok=True)
    totals = []
    for sequence in expected.index:
        info = metadata.loc[sequence]
        result = diagnose(
            gt[gt.sequence.eq(sequence)],
            pd.read_csv(source / "tracks" / f"{sequence}.csv"),
            frames.loc[frames.sequence.eq(sequence), "frame_id"],
            int(info.num_frames),
            regions.loc[regions.sequence.eq(sequence), ["x", "y", "w", "h"]].to_numpy(
                float
            ),
            info.width,
            info.height,
            protocol_version=2,
        )
        counts = result["frames"][["FP", "FN", "IDS", "Frag"]].sum().astype(int)
        assert counts.equals(expected.loc[sequence, counts.index].astype(int)), sequence
        result["events"].to_csv(destination / f"{sequence}_events.csv", index=False)
        result["frames"].to_csv(destination / f"{sequence}_frames.csv")
        result["tracks"].to_csv(destination / f"{sequence}_gt_tracks.csv", index=False)
        candidates = pd.read_csv(args.detector_audit / f"{sequence}.csv")
        misses = (
            result["events"]
            .query("kind == 'FN'")
            .merge(
                candidates, on=["frame_id", "class_id", "gt_id"], validate="one_to_one"
            )
        )
        assert len(misses) == int(counts.FN), sequence
        misses["category"] = np.select(
            [
                misses.assigned_same_iou07,
                misses.max_same_iou.ge(0.7),
                misses.max_any_iou.ge(0.7),
                misses.max_same_iou.ge(0.5),
            ],
            [
                "available_one_to_one_candidate",
                "same_class_candidate_contested",
                "other_class_candidate",
                "same_class_localization_05_07",
            ],
            default="no_same_class_candidate_iou05",
        )
        misses.to_csv(destination / f"{sequence}_fn_candidates.csv", index=False)
        totals.extend(
            dict(sequence=sequence, category=category, count=int(count))
            for category, count in misses.category.value_counts().items()
        )
        logging.info("Verified and attributed %s: %d FN", sequence, len(misses))
    pd.DataFrame(totals).to_csv(destination / "fn_categories.csv", index=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    main()
