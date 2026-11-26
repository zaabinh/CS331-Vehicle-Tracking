"""Camera preparation, detection adaptation, UCMC tracking and evaluation."""

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import yaml
    from src.tracking.common import (
        CLASS_NAMES,
        read_sequences,
        sha256_file,
        write_json,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "prepare-camera",
            "status",
            "adapt",
            "run",
            "evaluate",
            "smoke",
            "tune",
        ],
    )
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "outputs/ua_detrac/dataset_v1.1"
    )
    parser.add_argument(
        "--detections",
        type=Path,
        default=ROOT / "outputs/detections_full_yolo26l_E17",
    )
    parser.add_argument(
        "--calibration",
        type=Path,
        default=ROOT / "outputs/tracking/calibration/registry.json",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--upstream", type=Path, default=ROOT / "UCMC/UCMCTrack"
    )
    parser.add_argument(
        "--method", choices=["homography", "krt"], default="homography"
    )
    parser.add_argument(
        "--split", choices=["train", "val", "test", "all"], default="val"
    )
    parser.add_argument("--sequences", nargs="+")
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs/tracking/baseline.yaml"
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--allow-unreviewed",
        action="store_true",
        help="Experimental run only: use draft camera matrices without marking them reviewed",
    )
    parser.add_argument("--max-trials", type=int, default=15)
    args = parser.parse_args()
    if args.allow_unreviewed and args.command != "run":
        parser.error(
            "--allow-unreviewed is restricted to experimental run commands"
        )
    from src.tracking.calibration import prepare_registry, resolve_calibration

    if args.command == "prepare-camera":
        if args.calibration.name != "registry.json":
            parser.error(
                "prepare-camera writes registry.json; use that calibration filename"
            )
        print(
            json.dumps(
                prepare_registry(args.dataset, args.calibration.parent),
                indent=2,
            )
        )
        return 0
    metadata = read_sequences(args.dataset)
    names = args.sequences or sorted(
        s
        for s, info in metadata.items()
        if args.split == "all" or info["split"] == args.split
    )
    if args.command == "smoke":
        from src.tracking.tuning import SMOKE_SEQUENCES

        if args.sequences and set(args.sequences) != set(SMOKE_SEQUENCES):
            parser.error("Smoke uses the three fixed smoke sequences")
        names = SMOKE_SEQUENCES
    if (
        not names
        or len(set(names)) != len(names)
        or set(names) - set(metadata)
    ):
        parser.error("Invalid sequence selection")
    if args.command == "status":
        status = []
        for name in names:
            try:
                camera = resolve_calibration(
                    args.calibration, name, args.method
                )
                status.append(
                    dict(
                        sequence=name,
                        method=args.method,
                        status=camera["status"],
                    )
                )
            except (ValueError, KeyError, FileNotFoundError) as exc:
                status.append(
                    dict(
                        sequence=name,
                        method=args.method,
                        status="needs_review",
                        reason=str(exc),
                    )
                )
        print(json.dumps(status, indent=2))
        return 0 if all(r["status"] == "reviewed" for r in status) else 2
    output = (
        args.output
        or ROOT / "outputs/tracking" / f"{args.command}_{args.method}"
    )
    if args.command == "adapt":
        from src.tracking.detections import (
            normalize_detections,
            write_mot_detections,
        )

        handoff = json.loads(
            (args.detections / "detection_handoff_config.json").read_text()
        )
        if handoff.get("class_mapping") != {
            str(k): v for k, v in CLASS_NAMES.items()
        }:
            raise ValueError("Detector handoff class mapping mismatch")
        if (
            output.resolve() == args.detections.resolve()
            or args.detections.resolve() in output.resolve().parents
        ):
            raise ValueError(
                "Adapted output must be outside source detections"
            )
        output.mkdir(parents=True, exist_ok=True)
        sources = {}
        for name in names:
            source = args.detections / f"{name}.csv"
            rows = normalize_detections(
                source, name, int(metadata[name]["num_frames"])
            )
            write_mot_detections(output / f"{name}.txt", rows)
            sources[name] = dict(
                sha256=sha256_file(source),
                output_sha256=sha256_file(output / f"{name}.txt"),
                rows=len(rows),
                frames=int(metadata[name]["num_frames"]),
            )
        from src.tracking import detections as adapter

        result = dict(
            sequences=names,
            sources=sources,
            handoff=handoff,
            adapter_sha256=sha256_file(adapter.__file__),
            output_columns=adapter.MOT_COLUMNS,
            id_policy="1-based original source row, stable frame sort",
        )
        write_json(output / "adapter_manifest.json", result)
    elif args.command == "evaluate":
        from src.tracking.evaluation import evaluate_run

        result = evaluate_run(
            args.dataset, output, args.upstream, args.sequences
        )
    elif args.command == "run":
        from src.tracking.runner import run_tracking

        config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
        result = run_tracking(
            args.dataset,
            args.detections,
            args.calibration,
            output,
            config,
            args.method,
            names,
            args.resume,
            args.upstream,
            allow_unreviewed=args.allow_unreviewed,
        )
    elif args.command == "smoke":
        from src.tracking.tuning import run_smoke

        result = run_smoke(
            args.dataset,
            args.detections,
            args.calibration,
            output,
            args.upstream,
            args.resume,
        )
    else:
        from src.tracking.tuning import tune

        val = {s for s, info in metadata.items() if info["split"] == "val"}
        if args.split != "val" or set(names) != val:
            parser.error("Tuning requires exactly the full fixed val split")
        result = tune(
            args.dataset,
            args.detections,
            args.calibration,
            output,
            args.method,
            args.upstream,
            args.resume,
            args.max_trials,
        )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[TRACKING] %(message)s")
    try:
        sys.exit(main())
    except (ValueError, KeyError, FileNotFoundError, FileExistsError) as exc:
        logging.error("%s", exc)
        sys.exit(2)
