"""Run bounded full-val ablations using the existing detector CSVs only."""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# Small Kalman matrices do not benefit from nested BLAS thread pools.
for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import yaml

from src.tracking.common import read_sequences, sha256_file, write_json
from src.tracking.evaluation import evaluate_run, evaluation_fingerprint
from src.tracking.runner import run_tracking

METRICS = ["HOTA", "IDF1", "MOTA", "AssA", "DetA", "IDS", "FP", "FN", "Frag"]
VARIANTS = {
    "control": {},
    "boundary_6": {"boundary_sigma": 6.0},
    "boundary_10": {"boundary_sigma": 10.0},
    "shared_vote": {"class_mode": "shared_vote"},
    "shared_vote_boundary_10": {"class_mode": "shared_vote", "boundary_sigma": 10.0},
    "interpolation": {"interpolation": True},
    "max_age_15": {"max_age": 15},
    "max_age_60": {"max_age": 60},
    "gate_60": {"a1": 60.0, "a2": 60.0},
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "outputs/ua_detrac/dataset_v1.1"
    )
    parser.add_argument(
        "--detections", type=Path, default=ROOT / "outputs/detections_full_yolo26l_E17"
    )
    parser.add_argument(
        "--calibration",
        type=Path,
        default=ROOT / "outputs/tracking/calibration/registry_approved_h_v1.json",
    )
    parser.add_argument(
        "--reference",
        type=Path,
        default=ROOT / "outputs/tracking/validation_v2/runs/coasting_fix_hp",
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "outputs/tracking/refinement_v3"
    )
    parser.add_argument(
        "--trials", nargs="+", choices=list(VARIANTS), default=list(VARIANTS)
    )
    args = parser.parse_args()
    metadata = read_sequences(args.dataset)
    sequences = sorted(s for s, info in metadata.items() if info["split"] == "val")
    if len(sequences) != 12:
        raise ValueError("Refinement requires the full fixed 12-sequence val split")
    if args.reference.resolve() == args.output.resolve():
        raise ValueError("Experiment must not overwrite the reference")
    args.output.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(args.output / "run.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)
    reference = json.loads((args.reference / "run_manifest.json").read_text())
    if reference["status"] != "complete" or not reference["provenance"].get(
        "fix_coasting"
    ):
        raise ValueError("Expected the completed lifecycle-corrected reference")
    base = reference["config"]
    if base["cmc"]:
        raise ValueError("These controlled ablations require the no-CMC reference")
    inputs = [args.calibration, args.detections / "detection_handoff_config.json"]
    inputs += [args.detections / f"{s}.csv" for s in sequences]
    inputs += [
        args.dataset / p
        for p in (
            "metadata/sequences.csv",
            "metadata/annotated_frames.csv",
            "canonical/annotations.csv",
            "canonical/ignored_regions.csv",
        )
    ]
    inputs += [args.reference / "tracks" / f"{s}.csv" for s in sequences]
    hashes = {str(p.resolve()): sha256_file(p) for p in inputs}
    checksum_path = args.output / "input_checksums.json"
    if checksum_path.exists() and json.loads(checksum_path.read_text()) != hashes:
        raise ValueError("Experiment inputs changed; use a new output directory")
    write_json(checksum_path, hashes)
    expected = evaluation_fingerprint(args.dataset, selected=sequences)
    manifest_path = args.output / "experiment.json"
    experiment = dict(
        status="running",
        variants=VARIANTS,
        sequences=sequences,
        reference=str(args.reference),
        detection_inference=False,
        fix_coasting=True,
        selection="HOTA, IDF1, fewer IDS, lower runtime",
        blas_threads=1,
        script_sha256=sha256_file(__file__),
    )
    write_json(manifest_path, experiment)
    for role in args.trials:
        begin = time.perf_counter()
        logging.info("Full-val refinement: %s", role)
        run = args.output / "runs" / role
        manifest = run_tracking(
            args.dataset,
            args.detections,
            args.calibration,
            run,
            config={**base, **VARIANTS[role]},
            sequences=sequences,
            resume=True,
            fix_coasting=True,
        )
        if role == "control":
            equal = {
                s: sha256_file(run / "tracks" / f"{s}.csv")
                == sha256_file(args.reference / "tracks" / f"{s}.csv")
                for s in sequences
            }
            write_json(args.output / "reproduction_check.json", equal)
            if not all(equal.values()):
                raise AssertionError("Control differs from the selected v2 predictions")
        predictions = {s: sha256_file(run / "tracks" / f"{s}.csv") for s in sequences}
        summary = run / "evaluation/summary.json"
        result = json.loads(summary.read_text()) if summary.exists() else None
        if (
            not result
            or result.get("evaluation_fingerprint") != expected
            or result.get("prediction_checksums") != predictions
        ):
            result = evaluate_run(args.dataset, run, sequences=sequences)
        if result["sequence_count"] != 12:
            raise AssertionError("Incomplete val evaluation")
        config_dir = args.output / "configs"
        config_dir.mkdir(exist_ok=True)
        (config_dir / f"{role}.yaml").write_text(
            "# Requires --fix-coasting. Selected/evaluated on val only.\n"
            + yaml.safe_dump(manifest["config"], sort_keys=True),
            encoding="utf-8",
        )
        write_json(
            run / "trial.json",
            dict(
                role=role,
                fix_coasting=True,
                **{k: result[k] for k in METRICS},
                run_dir=str(run),
                config=manifest["config"],
                runtime_seconds=manifest["runtime_seconds"],
                evaluation_seconds=result["evaluation_seconds"],
                wall_seconds=time.perf_counter() - begin,
            ),
        )
        finalize(args.output)
        logging.info(
            "%s: HOTA %.4f, IDF1 %.4f, FP %d, FN %d, IDS %d",
            role,
            result["HOTA"],
            result["IDF1"],
            result["FP"],
            result["FN"],
            result["IDS"],
        )
    after = {path: sha256_file(path) for path in hashes}
    write_json(
        args.output / "preservation_check.json",
        dict(
            all_unchanged=after == hashes,
            files=len(hashes),
            changed=[path for path in hashes if hashes[path] != after[path]],
        ),
    )
    if after != hashes:
        raise AssertionError("Source inputs or historical tracking changed")
    experiment["status"] = (
        "complete"
        if all(
            (args.output / "runs" / role / "trial.json").exists() for role in VARIANTS
        )
        else "partial"
    )
    write_json(manifest_path, experiment)


def finalize(output):
    """Rank completed trials and point to the actual selected run without copying."""
    rows = [
        json.loads(path.read_text()) for path in (output / "runs").glob("*/trial.json")
    ]
    board = pd.DataFrame(rows)
    board["config_json"] = board.pop("config").map(
        lambda c: json.dumps(c, sort_keys=True)
    )
    board = board.sort_values(
        ["HOTA", "IDF1", "IDS", "runtime_seconds"], ascending=[False, False, True, True]
    )
    board.to_csv(output / "leaderboard.csv", index=False)
    trials = board.assign(config_id=board.role).copy()
    trials.loc[trials.role.eq("control"), "role"] = "baseline"
    trials.to_csv(output / "trials.csv", index=False)
    best = board.iloc[0]
    write_json(
        output / "best_config.json",
        dict(
            config_id=best.role,
            config=json.loads(best.config_json),
            fix_coasting=True,
            artifact_dir=f"runs/{best.role}",
            **{
                k: (
                    float(best[k])
                    if k not in ("IDS", "FP", "FN", "Frag")
                    else int(best[k])
                )
                for k in METRICS
            },
        ),
    )
    (output / "best_config.yaml").write_text(
        "# Requires --fix-coasting; val development result, not test.\n"
        + yaml.safe_dump(json.loads(best.config_json), sort_keys=True),
        encoding="utf-8",
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[REFINE] %(message)s")
    main()
