"""Select on val and evaluate test with an immutable reviewed camera snapshot."""

import argparse
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import yaml

from scripts.compare_tracking_cmc import (
    METRICS,
    compare_sequences,
    rank,
    read_json,
    upstream_config,
    verified_result,
)
from src.tracking.calibration import resolve_calibration
from src.tracking.common import read_sequences, sha256_file, write_json
from src.tracking.runner import run_tracking
from src.tracking.subsets import aggregate_saved, difficulty_lists


def candidate_configs(base):
    """Keep numeric parameters fixed and compare the two v3 CMC states."""
    return {
        "v3_no_cmc": ({**base, "cmc": False}, True),
        "v3_cmc": ({**base, "cmc": True}, True),
        "upstream_hp_cmc": (upstream_config(base), False),
    }


def lock_json(path, context):
    """Reject resume when the frozen experiment context has changed."""
    if path.exists() and read_json(path) != context:
        raise ValueError(f"Frozen experiment changed: {path}")
    write_json(path, context)


def evaluate_trial(args, metadata, split, role, config, fix_coasting):
    """Run one entire split, enforcing reviewed cameras and the same protocol."""
    names = sorted(s for s, row in metadata.items() if row["split"] == split)
    run = args.output / "runs" / f"{split}_{role}"
    begin = time.perf_counter()
    logging.info("Starting reviewed %s/%s", split, role)
    manifest = run_tracking(
        args.dataset,
        args.detections,
        args.calibration,
        run,
        config=config,
        sequences=names,
        resume=True,
        fix_coasting=fix_coasting,
    )
    if not manifest["provenance"]["calibration_reviewed"]:
        raise AssertionError("Unreviewed calibration entered the final run")
    result = verified_result(args.dataset, run, names)
    trial = run / "trial.json"
    previous = read_json(trial) if trial.exists() else None
    result.update(
        role=role,
        config=manifest["config"],
        fix_coasting=fix_coasting,
        wall_seconds=(
            previous["wall_seconds"]
            if previous
            else time.perf_counter() - begin
        ),
    )
    write_json(trial, result)
    logging.info("%s/%s: %s", split, role, {k: result[k] for k in METRICS})
    return result, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "outputs/ua_detrac/dataset_v1.1"
    )
    parser.add_argument(
        "--detections",
        type=Path,
        default=ROOT / "outputs/detections_full_yolo26l_E17",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs/tracking/final_reviewed_h100",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(args.output / "run.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logging.getLogger().addHandler(handler)
    begin = time.perf_counter()
    metadata = read_sequences(args.dataset)
    split_counts = {
        k: sum(r["split"] == k for r in metadata.values())
        for k in ("train", "val", "test")
    }
    if len(metadata) != 100 or split_counts != {
        "train": 48,
        "val": 12,
        "test": 40,
    }:
        raise ValueError("Require the original fixed 100-sequence split")
    # Validate all cameras, even though training sequences are not tracked here.
    cameras = {}
    for sequence, info in metadata.items():
        camera = resolve_calibration(args.calibration, sequence, "homography")
        if camera["image_size"] != [info["width"], info["height"]]:
            raise ValueError(f"Camera resolution differs: {sequence}")
        cameras[sequence] = camera
    selected_path = ROOT / "outputs/tracking/cmc_v3/val_selection.json"
    base = read_json(selected_path)["config"]
    configs = candidate_configs(base)
    names = sorted(s for s, row in metadata.items() if row["split"] != "train")
    sources = [
        args.calibration,
        selected_path,
        args.detections / "detection_handoff_config.json",
    ]
    sources += [args.detections / f"{s}.csv" for s in names]
    sources += [
        args.dataset / relative
        for relative in (
            "metadata/sequences.csv",
            "metadata/annotated_frames.csv",
            "canonical/annotations.csv",
            "canonical/ignored_regions.csv",
        )
    ]
    hashes = {str(p.resolve()): sha256_file(p) for p in sources}
    context = dict(
        sources=hashes,
        configs=configs,
        split_counts=split_counts,
        reviewed_cameras=100,
        selection="HOTA, IDF1, fewer IDS, lower runtime",
        script_sha256=sha256_file(__file__),
        helper_sha256=sha256_file(ROOT / "scripts/compare_tracking_cmc.py"),
        test_metrics_used_for_selection=False,
    )
    # JSON normalizes candidate tuples into lists when checking a resumed lock.
    lock_json(
        args.output / "experiment_lock.json", json.loads(json.dumps(context))
    )
    write_json(args.output / "resolved_cameras.json", cameras)
    cache = args.output / "runs/cache/gmc"
    cache.mkdir(parents=True, exist_ok=True)
    # Image motion is independent of homography; the loader still checks inputs.
    old_cache = ROOT / "outputs/tracking/cmc_v3/runs/cache/gmc"
    for path in old_cache.glob("*"):
        if path.is_file() and not (cache / path.name).exists():
            shutil.copy2(path, cache / path.name)
    rows, paths = [], {}
    for role, (config, fix) in configs.items():
        result, run = evaluate_trial(args, metadata, "val", role, config, fix)
        rows.append(result)
        paths[role] = run
        pd.DataFrame(rows).to_csv(
            args.output / "val_comparison.csv", index=False
        )
    compare_sequences(args.output, "val", paths, metadata)
    v3_best = min(rows[:2], key=rank)
    best = min(rows, key=rank)
    selection = dict(
        selected_on="new_homography_val_only",
        config_id=best["role"],
        config=best["config"],
        fix_coasting=best["fix_coasting"],
        v3_config_id=v3_best["role"],
        calibration_sha256=hashes[str(args.calibration.resolve())],
        val_comparison_sha256=sha256_file(args.output / "val_comparison.csv"),
        **{k: best[k] for k in METRICS},
    )
    lock_json(args.output / "val_selection.json", selection)
    (args.output / "best_config.yaml").write_text(
        "# Selected on val with all 100 reviewed homographies.\n"
        f"# fix_coasting={best['fix_coasting']}\n"
        + yaml.safe_dump(best["config"], sort_keys=True),
        encoding="utf-8",
    )
    lock_json(
        args.output / "test_selection_lock.json",
        dict(
            selection_sha256=sha256_file(args.output / "val_selection.json"),
            calibration_sha256=selection["calibration_sha256"],
            v3_config=v3_best["config"],
            upstream_config=configs["upstream_hp_cmc"][0],
            test_metrics_used_for_selection=False,
            calibration_reviewed=True,
        ),
    )
    rows, paths = [], {}
    for role in (v3_best["role"], "upstream_hp_cmc"):
        config, fix = configs[role]
        result, run = evaluate_trial(args, metadata, "test", role, config, fix)
        rows.append(result)
        paths[role] = run
        pd.DataFrame(rows).to_csv(
            args.output / "test_comparison.csv", index=False
        )
    compare_sequences(args.output, "test", paths, metadata)
    names = sorted(s for s, row in metadata.items() if row["split"] == "test")
    subsets, audits = [], {}
    for system, folder in (
        ("tracking", "DETRAC-Test-Tra"),
        ("detection", "DETRAC-Test-Det"),
    ):
        groups, audits[system] = difficulty_lists(
            ROOT / "data/raw/Toolkit" / folder / "evaluation/seqs", names
        )
        for role, run in paths.items():
            for level, members in groups.items():
                result = aggregate_saved(run / "evaluation", members)
                subsets.append(
                    dict(
                        system=system,
                        role=role,
                        difficulty=level,
                        sequences=len(members),
                        **{k: result[k] for k in METRICS},
                    )
                )
    pd.DataFrame(subsets).to_csv(
        args.output / "test_difficulty.csv", index=False
    )
    write_json(args.output / "difficulty_sources.json", audits)
    changed = [p for p, digest in hashes.items() if sha256_file(p) != digest]
    if changed:
        raise AssertionError(f"Frozen experiment sources changed: {changed}")
    write_json(
        args.output / "completion.json",
        dict(
            status="complete",
            reviewed_cameras=100,
            detector_rerun=False,
            calibration_reviewed=True,
            source_files_unchanged=True,
            source_files_checked=len(hashes),
            selected_on="val_only",
            selected_config_id=best["role"],
            selected_test_run=str(paths[best["role"]]),
            wall_seconds=time.perf_counter() - begin,
        ),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[REVIEWED] %(message)s")
    main()
