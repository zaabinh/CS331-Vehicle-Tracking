"""Compare v3 +/- CMC on val, then conditionally evaluate the frozen test config."""

import argparse
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

# Keep small Kalman operations and optical flow from oversubscribing the CPU.
for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import yaml

from src.tracking.common import fingerprint, read_sequences, sha256_file, write_json
from src.tracking.evaluation import evaluate_run, evaluation_fingerprint
from src.tracking.runner import run_tracking
from src.tracking.subsets import aggregate_saved, difficulty_lists

METRICS = ["HOTA", "IDF1", "MOTA", "AssA", "DetA", "IDS", "FP", "FN", "Frag"]


def read_json(path):
    """Read an experiment artifact without changing it."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def upstream_config(v3):
    """Use upstream HP/CMC and tuned parameters, without our algorithm changes."""
    return {**v3, "class_mode": "separate", "boundary_sigma": 0.0, "cmc": True}


def rank(result):
    """Apply the existing val selection rule, with lower counts/time as ties."""
    return (-result["HOTA"], -result["IDF1"], result["IDS"], result["runtime_seconds"])


def verified_result(dataset, run, sequences):
    """Reuse metrics only when protocol, predictions and completed outputs agree."""
    manifest = read_json(run / "run_manifest.json")
    if manifest["status"] != "complete" or manifest["sequences"] != sequences:
        raise ValueError(f"Incomplete reference run: {run}")
    for name in sequences:
        for relative, digest in manifest["outputs"][name].items():
            if sha256_file(run / relative) != digest:
                raise ValueError(f"Changed saved output: {run / relative}")
    hashes = {s: sha256_file(run / "tracks" / f"{s}.csv") for s in sequences}
    path = run / "evaluation/summary.json"
    result = read_json(path) if path.exists() else None
    if (not result or result["prediction_checksums"] != hashes
            or result["evaluation_fingerprint"] != evaluation_fingerprint(
                dataset, selected=sequences)):
        result = evaluate_run(dataset, run, sequences=sequences)
    return dict(
        role=run.name, run_dir=str(run.resolve()),
        **{k: result[k] for k in METRICS},
        runtime_seconds=manifest["runtime_seconds"],
        evaluation_seconds=result["evaluation_seconds"],
        sequence_count=result["sequence_count"],
    )


def compare_sequences(output, split, paths, metadata):
    """Export paired differences and camera-state groups from saved metric arrays."""
    tables = []
    for role, run in paths.items():
        table = pd.read_csv(run / "evaluation/per_sequence.csv")
        table.insert(0, "role", role)
        tables.append(table)
    pd.concat(tables).to_csv(output / f"{split}_per_sequence.csv", index=False)
    groups = {}
    for name in paths:
        run = paths[name]
        sequences = read_json(run / "run_manifest.json")["sequences"]
        if "camera_state" in next(iter(metadata.values())):
            for state in sorted({metadata[s]["camera_state"] for s in sequences}):
                selected = [s for s in sequences if metadata[s]["camera_state"] == state]
                groups[f"{name}/{state}"] = aggregate_saved(run / "evaluation", selected)
    write_json(output / f"{split}_camera_groups.json", groups)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["val", "test"])
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/tracking/cmc_v3")
    args = parser.parse_args()
    dataset = ROOT / "outputs/ua_detrac/dataset_v1.1"
    detections = ROOT / "outputs/detections_full_yolo26l_E17"
    reference = ROOT / "outputs/tracking/refinement_v3/runs/shared_vote_boundary_10"
    metadata = read_sequences(dataset)
    sequences = sorted(s for s, info in metadata.items() if info["split"] == args.stage)
    if len(sequences) != (12 if args.stage == "val" else 40):
        raise ValueError("Require the full fixed split")
    base = read_json(reference / "run_manifest.json")["config"]
    candidate = yaml.safe_load((ROOT / "configs/tracking/v3_cmc.yaml").read_text())
    if base["cmc"] or candidate != {**base, "cmc": True}:
        raise ValueError("The CMC ablation must change only the CMC switch")
    calibration = ROOT / (
        "outputs/tracking/calibration/registry_approved_h_v1.json"
        if args.stage == "val" else "outputs/tracking/test_calibration_draft/registry.json"
    )
    args.output.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(args.output / f"{args.stage}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logging.getLogger().addHandler(handler)
    # Hash all source tables, calibration and historical references before execution.
    sources = [calibration, detections / "detection_handoff_config.json"]
    sources += [detections / f"{s}.csv" for s in sequences]
    sources += [dataset / p for p in (
        "metadata/sequences.csv", "metadata/annotated_frames.csv",
        "canonical/annotations.csv", "canonical/ignored_regions.csv",
    )]
    old_run = reference if args.stage == "val" else ROOT / "outputs/tracking/test_final_v3/run"
    sources += [old_run / "tracks" / f"{s}.csv" for s in sequences]
    hashes = {str(p.resolve()): sha256_file(p) for p in sources}
    lock = dict(
        stage=args.stage, sequences=sequences, sources=hashes,
        candidate=candidate, upstream=upstream_config(base),
        evaluation=evaluation_fingerprint(dataset, selected=sequences),
        selection_rule="HOTA, IDF1, fewer IDS, lower runtime; val only",
        script_sha256=sha256_file(__file__),
    )
    if args.stage == "test":
        selection = read_json(args.output / "val_selection.json")
        if not selection["cmc_selected"]:
            raise ValueError("CMC did not win on val; test CMC run is not authorized by the gate")
        lock["val_selection_sha256"] = sha256_file(args.output / "val_selection.json")
        lock["test_metrics_used_for_selection"] = False
        lock["camera_review"] = "10 reviewed, 30 draft; same provisional geometry as test v3"
    path = args.output / f"{args.stage}_lock.json"
    if path.exists() and read_json(path) != lock:
        raise ValueError("Experiment inputs changed; use a different output directory")
    write_json(path, lock)
    # Seed old CMC arrays; the existing loader verifies complete image/code provenance.
    cache = args.output / "runs/cache/gmc"
    cache.mkdir(parents=True, exist_ok=True)
    for path in (ROOT / "outputs/tracking/validation_v2/runs/cache/gmc").glob("*"):
        if path.is_file() and not (cache / path.name).exists():
            shutil.copy2(path, cache / path.name)
    start = time.perf_counter()
    rows = [dict(verified_result(dataset, old_run, sequences), role="v3_no_cmc")]
    paths = {"v3_no_cmc": old_run}
    for role, config, fix in (
        ("v3_cmc", candidate, True),
        ("upstream_hp_cmc", upstream_config(base), False),
    ):
        run = args.output / "runs" / f"{args.stage}_{role}"
        logging.info("Starting %s/%s", args.stage, role)
        run_tracking(
            dataset, detections, calibration, run, config=config,
            sequences=sequences, resume=True, fix_coasting=fix,
            allow_unreviewed=args.stage == "test",
        )
        rows.append(dict(verified_result(dataset, run, sequences), role=role))
        paths[role] = run
        pd.DataFrame(rows).to_csv(args.output / f"{args.stage}_comparison.csv", index=False)
        logging.info("%s: %s", role, {k: rows[-1][k] for k in METRICS})
    if args.stage == "val":
        selected = min(rows[:2], key=rank)
        write_json(args.output / "val_selection.json", dict(
            cmc_selected=selected["role"] == "v3_cmc", selected_on="val_only",
            config=candidate if selected["role"] == "v3_cmc" else base,
            fix_coasting=True, config_id=selected["role"],
            comparison_sha256=sha256_file(args.output / "val_comparison.csv"),
            **{k: selected[k] for k in METRICS},
        ))
    compare_sequences(args.output, args.stage, paths, metadata)
    if args.stage == "test":
        groups, audit = difficulty_lists(
            ROOT / "data/raw/Toolkit/DETRAC-Test-Tra/evaluation/seqs", sequences
        )
        subsets = []
        for role, run in paths.items():
            for level, names in groups.items():
                result = aggregate_saved(run / "evaluation", names)
                subsets.append(dict(role=role, difficulty=level, sequences=len(names),
                                    **{k: result[k] for k in METRICS}))
        pd.DataFrame(subsets).to_csv(args.output / "test_difficulty.csv", index=False)
        write_json(args.output / "test_difficulty_sources.json", audit)
    changed = [p for p, digest in hashes.items() if sha256_file(p) != digest]
    write_json(args.output / f"{args.stage}_completion.json", dict(
        status="complete", sequence_count=len(sequences), detector_rerun=False,
        source_files_unchanged=not changed, changed=changed,
        wall_seconds=time.perf_counter() - start,
        source_fingerprint=fingerprint(hashes), rows=rows,
    ))
    if changed:
        raise AssertionError("Source inputs or reference predictions changed")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[CMC] %(message)s")
    main()
