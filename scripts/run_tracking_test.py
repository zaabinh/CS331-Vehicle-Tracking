"""Freeze the val-selected tracker, run test once, and report saved-metric subsets."""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.tracking.common import read_sequences, sha256_file, write_json
from src.tracking.evaluation import evaluate_run, evaluation_fingerprint
from src.tracking.runner import run_tracking
from src.tracking.subsets import aggregate_saved, difficulty_lists

METRICS = ["HOTA", "IDF1", "MOTA", "AssA", "DetA", "FP", "FN", "IDS", "Frag"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "outputs/ua_detrac/dataset_v1.1"
    )
    parser.add_argument(
        "--detections", type=Path, default=ROOT / "outputs/detections_full_yolo26l_E17"
    )
    parser.add_argument(
        "--selection",
        type=Path,
        default=ROOT / "outputs/tracking/refinement_v3/best_config.json",
    )
    parser.add_argument(
        "--calibration",
        type=Path,
        default=ROOT / "outputs/tracking/test_calibration_draft/registry.json",
    )
    parser.add_argument("--toolkit", type=Path, default=ROOT / "data/raw/Toolkit")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "outputs/tracking/test_final_v3"
    )
    args = parser.parse_args()
    metadata = read_sequences(args.dataset)
    sequences = sorted(s for s, info in metadata.items() if info["split"] == "test")
    if len(sequences) != 40:
        raise ValueError("Require the fixed 40-sequence test split")
    selected = json.loads(args.selection.read_text())
    expected = evaluation_fingerprint(args.dataset, selected=sequences)
    groups, audits = {}, {}
    for system, folder in [
        ("tracking", "DETRAC-Test-Tra"),
        ("detection", "DETRAC-Test-Det"),
    ]:
        groups[system], audits[system] = difficulty_lists(
            args.toolkit / folder / "evaluation/seqs", sequences
        )
    lock = dict(
        selected_on="val_only",
        selected_config_id=selected["config_id"],
        config=selected["config"],
        fix_coasting=selected["fix_coasting"],
        selection_sha256=sha256_file(args.selection),
        calibration_sha256=sha256_file(args.calibration),
        evaluation_fingerprint=expected,
        sequences=sequences,
        difficulty=groups,
        difficulty_sources=audits,
        test_metrics_used_for_selection=False,
        calibration_review="10 reviewed, 30 new drafts; provisional test geometry",
    )
    args.output.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(args.output / "run.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)
    path = args.output / "selection_lock.json"
    if path.exists() and json.loads(path.read_text()) != lock:
        raise ValueError(
            "Locked test selection/input changed; do not retune this test run"
        )
    write_json(path, lock)
    run = args.output / "run"
    manifest = run_tracking(
        args.dataset,
        args.detections,
        args.calibration,
        run,
        config=selected["config"],
        sequences=sequences,
        resume=True,
        fix_coasting=selected["fix_coasting"],
        allow_unreviewed=True,
    )
    path = run / "evaluation/summary.json"
    result = json.loads(path.read_text()) if path.exists() else None
    checksums = {s: sha256_file(run / "tracks" / f"{s}.csv") for s in sequences}
    if (
        not result
        or result.get("evaluation_fingerprint") != expected
        or result.get("prediction_checksums") != checksums
    ):
        result = evaluate_run(args.dataset, run, sequences=sequences)
    recombined = aggregate_saved(run / "evaluation", sequences)
    for key in METRICS:
        if abs(recombined[key] - result[key]) > 1e-9:
            raise AssertionError(f"Saved metric aggregation differs: {key}")
    rows = []
    for system, membership in groups.items():
        for level, names in membership.items():
            subset = aggregate_saved(run / "evaluation", names)
            write_json(args.output / "difficulty" / system / f"{level}.json", subset)
            rows.append(
                dict(
                    system=system,
                    difficulty=level,
                    **{key: subset[key] for key in METRICS},
                    sequences=len(names),
                    frames=subset["frames"],
                    evaluated_frames=subset["evaluated_frames"],
                    gt_observations=subset["evaluated_gt_count"],
                )
            )
    pd.DataFrame(rows).to_csv(args.output / "difficulty_summary.csv", index=False)
    write_json(
        args.output / "completion.json",
        dict(
            status="complete",
            sequences=40,
            detector_rerun=False,
            selection_locked_before_test=True,
            saved_aggregation_matches=True,
            calibration_reviewed=manifest["provenance"]["calibration_reviewed"],
            **{key: result[key] for key in METRICS},
        ),
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[TEST] %(message)s")
    main()
