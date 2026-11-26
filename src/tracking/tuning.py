"""Bounded validation tuning and smoke comparison of camera methods."""

import json
from pathlib import Path

import pandas as pd

from .calibration import resolve_calibration
from .common import BASELINE, UPSTREAM, fingerprint, read_sequences, write_json
from .evaluation import evaluate_run, evaluation_fingerprint
from .runner import run_tracking

SMOKE_SEQUENCES = ["MVI_20012", "MVI_39811", "MVI_63521"]
OFAT_VARIANTS = [
    ("conf_005", "conf", {"conf": 0.05}),
    ("conf_020", "conf", {"conf": 0.2}),
    ("high_040", "high_score", {"high_score": 0.4}),
    ("a_020", "association", {"a1": 20.0, "a2": 20.0}),
    ("a_040", "association", {"a1": 40.0, "a2": 40.0}),
    ("w_001", "noise", {"wx": 1.0, "wy": 1.0}),
    ("w_010", "noise", {"wx": 10.0, "wy": 10.0}),
    ("vmax_015", "vmax", {"vmax": 15.0}),
    ("age_060", "max_age", {"max_age": 60}),
]


def _val(dataset):
    metadata = read_sequences(dataset)
    selected = sorted(
        s for s, info in metadata.items() if info["split"] == "val"
    )
    if len(selected) != 12:
        raise ValueError(
            "Full validation requires the fixed 12-sequence val split"
        )
    manifest = json.loads(
        (Path(dataset) / "metadata/manifest.json").read_text()
    )
    if set(selected) != set(manifest["splits"]["val"]):
        raise ValueError(
            "Validation metadata differs from the dataset manifest"
        )
    return selected


def _preflight(calibration, selected, methods):
    records, errors = {}, []
    for method in methods:
        records[method] = {}
        for sequence in selected:
            try:
                records[method][sequence] = resolve_calibration(
                    calibration, sequence, method
                )
            except (ValueError, KeyError, FileNotFoundError) as exc:
                errors.append(f"{method}/{sequence}: {exc}")
    if errors:
        raise ValueError(
            "Camera review is pending; no experiment started:\n"
            + "\n".join(errors)
        )
    return records


def _start(output, context, resume):
    output = Path(output)
    path = output / "experiment.json"
    identity = fingerprint(context)
    if path.is_file():
        previous = json.loads(path.read_text())
        if not resume or previous["fingerprint"] != identity:
            raise ValueError(
                "Experiment inputs/calibration/protocol changed or resume disabled; use new output"
            )
    elif output.exists() and any(output.iterdir()):
        raise FileExistsError("Experiment directory is not empty")
    output.mkdir(parents=True, exist_ok=True)
    manifest = dict(status="running", fingerprint=identity, context=context)
    write_json(path, manifest)
    return output, manifest


def _rank(result):
    return (
        -result["HOTA"],
        -result["IDF1"],
        result["IDS"],
        result["runtime_seconds"],
        result["config_id"],
    )


def _trial(
    dataset,
    detections,
    calibration,
    output,
    config,
    method,
    selected,
    upstream,
    resume,
    role,
):
    key = fingerprint(dict(method=method, config=config))[:16]
    run = Path(output) / "runs" / key
    # Always ask the runner to verify provenance, even when a result already exists.
    manifest = run_tracking(
        dataset,
        detections,
        calibration,
        run,
        config,
        method,
        selected,
        resume,
        upstream,
    )
    metrics = evaluate_run(dataset, run, upstream, selected)
    return dict(
        config_id=key,
        role=role,
        config=config,
        run_dir=str(run.resolve()),
        **{
            name: metrics[name]
            for name in [
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
        },
        runtime_seconds=manifest["runtime_seconds"],
    )


def run_smoke(
    dataset,
    detections,
    calibration,
    output,
    upstream=None,
    resume=True,
    config=None,
):
    """Compare both reviewed camera methods on the same three complete videos."""
    upstream = Path(upstream or UPSTREAM)
    if set(SMOKE_SEQUENCES) - set(_val(dataset)):
        raise ValueError("Smoke sequences differ from the fixed val split")
    cameras = _preflight(calibration, SMOKE_SEQUENCES, ["homography", "krt"])
    config = {**BASELINE, "preview_frames": 150, **(config or {})}
    context = dict(
        cameras=cameras,
        config=config,
        sequences=SMOKE_SEQUENCES,
        evaluation=evaluation_fingerprint(dataset, upstream, SMOKE_SEQUENCES),
    )
    output, manifest = _start(output, context, resume)
    results = []
    for method in ["homography", "krt"]:
        result = _trial(
            dataset,
            detections,
            calibration,
            output,
            config,
            method,
            SMOKE_SEQUENCES,
            upstream,
            resume,
            method,
        )
        result["method"] = method
        results.append(result)
        pd.DataFrame(
            [{k: v for k, v in r.items() if k != "config"} for r in results]
        ).to_csv(output / "camera_comparison.csv", index=False)
    best = min(results, key=lambda r: (-r["HOTA"], -r["IDF1"]))
    recommendation = dict(
        recommended_method=best["method"],
        comparisons=results,
        selection="HOTA then IDF1; exact tie selects homography",
        next_step="Review and freeze selected method on all 12 val sequences",
    )
    write_json(output / "selected_method_recommendation.json", recommendation)
    metadata = read_sequences(dataset)
    smoke_frames = sum(int(metadata[s]["num_frames"]) for s in SMOKE_SEQUENCES)
    val_frames = sum(int(metadata[s]["num_frames"]) for s in _val(dataset))
    write_json(
        output / "runtime_estimate.json",
        dict(
            smoke_frames=smoke_frames,
            validation_frames=val_frames,
            full_val_seconds={
                r["method"]: r["runtime_seconds"] * val_frames / smoke_frames
                for r in results
            },
            limitations="Linear estimate from measured smoke tracking+I/O; excludes evaluation and cold CMC cache.",
        ),
    )
    manifest.update(status="complete", recommended_method=best["method"])
    write_json(output / "experiment.json", manifest)
    return recommendation


def tune(
    dataset,
    detections,
    calibration,
    output,
    method="homography",
    upstream=None,
    resume=True,
    max_trials=15,
):
    """One baseline, nine OFAT variants, one combination and four ablations."""
    if not isinstance(max_trials, int) or not 1 <= max_trials <= 15:
        raise ValueError("max_trials must be an integer in 1..15")
    upstream, selected = Path(upstream or UPSTREAM), _val(dataset)
    cameras = _preflight(calibration, selected, [method])
    context = dict(
        cameras=cameras,
        method=method,
        sequences=selected,
        evaluation=evaluation_fingerprint(dataset, upstream, selected),
    )
    output, manifest = _start(output, context, resume)
    history, unique = [], {}

    def execute(role, config):
        if len(history) >= max_trials:
            return None
        result = _trial(
            dataset,
            detections,
            calibration,
            output,
            config,
            method,
            selected,
            upstream,
            resume or bool(history),
            role,
        )
        history.append(result)
        unique[result["config_id"]] = result
        ordered = sorted(unique.values(), key=_rank)
        rows = [
            {
                **{k: v for k, v in r.items() if k != "config"},
                "config_json": json.dumps(r["config"], sort_keys=True),
            }
            for r in ordered
        ]
        pd.DataFrame(rows).to_csv(output / "leaderboard.csv", index=False)
        pd.DataFrame(
            [{k: v for k, v in r.items() if k != "config"} for r in history]
        ).to_csv(output / "trials.csv", index=False)
        write_json(
            output / "best_config.json",
            dict(method=method, **ordered[0], dataset_role="development"),
        )
        print(
            f"[TUNE] {len(history)}/15 {role}: HOTA={result['HOTA']:.3f}, IDF1={result['IDF1']:.3f}",
            flush=True,
        )
        return result

    try:
        baseline = execute("baseline", BASELINE)
        tested = []
        for role, group, change in OFAT_VARIANTS:
            trial = execute(role, {**BASELINE, **change})
            if trial is not None:
                tested.append((group, change, trial))
        combined = dict(BASELINE)
        for group in dict.fromkeys(g for g, _, _ in tested):
            improving = [
                (change, r)
                for g, change, r in tested
                if g == group and r["HOTA"] > baseline["HOTA"]
            ]
            if improving:
                change, _ = min(improving, key=lambda pair: _rank(pair[1]))
                combined.update(change)
        reference = execute("combined_parameters", combined)
        if reference:
            enabled = {}
            for option in ["hp", "interpolation", "cmc"]:
                trial = execute(
                    f"ablation_{option}", {**combined, option: True}
                )
                enabled[option] = bool(
                    trial and trial["HOTA"] > reference["HOTA"]
                )
            execute("ablation_combined_improvements", {**combined, **enabled})
        manifest.update(
            status="complete",
            trial_slots=len(history),
            unique_configurations=len(unique),
            full_schedule_completed=len(history) == 15,
            best=sorted(unique.values(), key=_rank)[0],
        )
    except Exception as exc:
        manifest.update(
            status="failed", error=str(exc), trial_slots=len(history)
        )
        write_json(output / "experiment.json", manifest)
        raise
    write_json(output / "experiment.json", manifest)
    return manifest
