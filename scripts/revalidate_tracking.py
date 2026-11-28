"""Re-score saved val trials under protocol v2 and run focused follow-ups."""

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ProcessPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.tracking.common import sha256_file, write_json
from src.tracking.evaluation import PROTOCOL, evaluate_run, evaluation_fingerprint
from src.tracking.runner import run_tracking

import pandas as pd


def score_trial(record, dataset, source, output_root, sequences, expected):
    trial = SimpleNamespace(**record)
    args = SimpleNamespace(dataset=dataset, source=source, output=output_root)
    run = args.source / "runs" / trial.config_id
    output = args.output / "reevaluation" / trial.config_id
    summary = output / "summary.json"
    logging.info("Re-score: %s", trial.role)
    checksums = {s: sha256_file(run / "tracks" / f"{s}.csv") for s in sequences}
    result = json.loads(summary.read_text()) if summary.is_file() else None
    if (
        not result
        or result.get("evaluation_fingerprint") != expected
        or result.get("prediction_checksums") != checksums
    ):
        result = evaluate_run(args.dataset, run, sequences=sequences, output_dir=output)
    row = dict(
        config_id=trial.config_id,
        role=trial.role,
        config_json=trial.config_json,
        source_run=str(run),
        evaluation_dir=str(output),
        runtime_seconds=trial.runtime_seconds,
    )
    row.update(
        {
            k: result[k]
            for k in [
                "HOTA",
                "IDF1",
                "MOTA",
                "AssA",
                "DetA",
                "IDS",
                "FP",
                "FN",
                "Frag",
            ]
        }
    )
    row.update(HOTA_v1=trial.HOTA, IDF1_v1=trial.IDF1, FN_v1=trial.FN)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "outputs/ua_detrac/dataset_v1.1"
    )
    parser.add_argument(
        "--source", type=Path, default=ROOT / "outputs/tracking/tuning_h_approved_v1"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "outputs/tracking/validation_v2"
    )
    parser.add_argument(
        "--calibration",
        type=Path,
        default=ROOT / "outputs/tracking/calibration/registry_approved_h_v1.json",
    )
    parser.add_argument(
        "--detections", type=Path, default=ROOT / "outputs/detections_full_yolo26l_E17"
    )
    parser.add_argument(
        "--followups",
        action="store_true",
        help="Reproduce winner; test confidence and an HP high-score control on full val",
    )
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("workers must be positive")
    if args.source.resolve() == args.output.resolve():
        parser.error("Output must differ from the historical experiment")
    args.output.mkdir(parents=True, exist_ok=True)
    trials = pd.read_csv(args.source / "leaderboard.csv")
    metadata = pd.read_csv(args.dataset / "metadata/sequences.csv")
    sequences = sorted(metadata.loc[metadata.split.eq("val"), "sequence"])
    if len(sequences) != 12:
        raise ValueError(f"Expected 12 val sequences, found {len(sequences)}")
    expected = evaluation_fingerprint(args.dataset, selected=sequences)
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        jobs = [
            pool.submit(
                score_trial,
                record,
                args.dataset,
                args.source,
                args.output,
                sequences,
                expected,
            )
            for record in trials.to_dict("records")
        ]
        for job in as_completed(jobs):
            row = job.result()
            rows.append(row)
            board = pd.DataFrame(rows).sort_values(
                ["HOTA", "IDF1", "IDS", "runtime_seconds"],
                ascending=[False, False, True, True],
            )
            board.to_csv(args.output / "leaderboard.csv", index=False)
            logging.info(
                "Scored %d/%d %s: HOTA %.4f",
                len(rows),
                len(trials),
                row["role"],
                row["HOTA"],
            )
    winner = board.iloc[0]
    write_json(
        args.output / "selected_saved_config.json",
        dict(
            config_id=winner.config_id,
            config=json.loads(winner.config_json),
            protocol=PROTOCOL,
        ),
    )
    if not args.followups:
        return
    # Reuse optical-flow/detection caches; inputs and cache keys are still verified.
    source_cache = args.source / "runs/cache"
    if source_cache.is_dir():
        shutil.copytree(source_cache, args.output / "runs/cache", dirs_exist_ok=True)
    base = json.loads(winner.config_json)
    configs = [("reproduce_v2_winner", base, False)]
    configs.extend(
        (f"conf_{conf:.2f}", {**base, "conf": conf}, False)
        for conf in [0.10, 0.20]
        if base["conf"] != conf
    )
    hp_control = json.loads(
        board.loc[board.role.eq("ablation_hp"), "config_json"].iloc[0]
    )
    configs.append(("high_score_040_hp", {**hp_control, "high_score": 0.4}, False))
    configs.append(("coasting_fix_hp", hp_control, True))
    followups = []
    for role, config, fix_coasting in configs:
        logging.info("Full-val tracking: %s", role)
        run = args.output / "runs" / role
        focused = args.output / "focused_followups.csv"
        if focused.is_file():
            for record in pd.read_csv(focused).itertuples():
                fixed = (
                    getattr(record, "algorithm_variant", "upstream") == "coasting_fix"
                )
                if json.loads(record.config_json) == config and fixed == fix_coasting:
                    run = Path(record.run_dir)
                    break
        manifest = run_tracking(
            args.dataset,
            args.detections,
            args.calibration,
            run,
            config=config,
            sequences=sequences,
            resume=True,
            trace=role == "reproduce_v2_winner",
            fix_coasting=fix_coasting,
        )
        summary = run / "evaluation/summary.json"
        result = json.loads(summary.read_text()) if summary.is_file() else None
        checksums = {s: sha256_file(run / "tracks" / f"{s}.csv") for s in sequences}
        if (
            not result
            or result.get("evaluation_fingerprint") != expected
            or result.get("prediction_checksums") != checksums
        ):
            result = evaluate_run(args.dataset, run, sequences=sequences)
        if role == "reproduce_v2_winner":
            previous = args.source / "runs" / winner.config_id
            equal = {
                s: sha256_file(run / "tracks" / f"{s}.csv")
                == sha256_file(previous / "tracks" / f"{s}.csv")
                for s in sequences
            }
            write_json(args.output / "reproduction_check.json", equal)
            if not all(equal.values()):
                raise AssertionError("Reproduction differs from saved tracker output")
        followups.append(
            dict(
                role=role,
                algorithm_variant="coasting_fix" if fix_coasting else "upstream",
                config_json=json.dumps(config),
                run_dir=str(run),
                **{
                    k: result[k]
                    for k in ["HOTA", "IDF1", "MOTA", "IDS", "FP", "FN", "Frag"]
                },
                runtime_seconds=manifest["runtime_seconds"],
            )
        )
        pd.DataFrame(followups).to_csv(args.output / "followups.csv", index=False)
    finalize(args.source, args.output)


def finalize(source, output):
    """Make the best/baseline artifacts directly usable by the analysis notebook."""
    source, output = Path(source), Path(output)
    saved = pd.read_csv(output / "leaderboard.csv")
    extra = pd.read_csv(output / "followups.csv")
    focused = output / "focused_followups.csv"
    if focused.is_file():
        extra = pd.concat([extra, pd.read_csv(focused)], ignore_index=True)
    if "algorithm_variant" not in extra:
        extra["algorithm_variant"] = "upstream"
    extra["algorithm_variant"] = extra.algorithm_variant.fillna("upstream")
    keys = (
        extra.config_json.map(lambda s: json.dumps(json.loads(s), sort_keys=True))
        + "|"
        + extra.algorithm_variant
    )
    extra = extra.loc[~keys.duplicated()].copy()
    extra["config_id"] = extra.role
    extra["source_run"] = extra.run_dir
    extra["evaluation_dir"] = extra.run_dir.map(lambda p: str(Path(p) / "evaluation"))
    board = pd.concat([saved, extra], ignore_index=True).sort_values(
        ["HOTA", "IDF1", "IDS", "runtime_seconds"], ascending=[False, False, True, True]
    )
    board["algorithm_variant"] = board.algorithm_variant.fillna("upstream")
    keys = (
        board.config_json.map(lambda s: json.dumps(json.loads(s), sort_keys=True))
        + "|"
        + board.algorithm_variant
    )
    board = board.loc[~keys.duplicated()].copy()
    board.to_csv(output / "all_trials.csv", index=False)
    selected = board.iloc[0]
    baseline = board[board.role.eq("baseline")].iloc[0]
    for row in (selected, baseline):
        original = Path(row.source_run)
        target = output / "runs" / row.config_id
        if target.resolve() != original.resolve():
            target.mkdir(parents=True, exist_ok=True)
            for folder in (
                "tracks",
                "raw_tracks",
                "mot",
                "sequence_reports",
                "association_trace",
            ):
                if (original / folder).is_dir():
                    shutil.copytree(
                        original / folder, target / folder, dirs_exist_ok=True
                    )
            shutil.copy2(original / "run_manifest.json", target / "run_manifest.json")
            shutil.copytree(
                row.evaluation_dir, target / "evaluation", dirs_exist_ok=True
            )
    board.to_csv(output / "trials.csv", index=False)
    write_json(
        output / "best_config.json",
        dict(
            config_id=selected.config_id,
            config=json.loads(selected.config_json),
            HOTA=float(selected.HOTA),
            IDF1=float(selected.IDF1),
            protocol=PROTOCOL,
            artifact_dir=f"runs/{selected.config_id}",
            fix_coasting=selected.algorithm_variant == "coasting_fix",
        ),
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    main()
