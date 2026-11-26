"""Class-aware UA-DETRAC evaluation using the bundled TrackEval metrics."""

import hashlib
import importlib
import json
import logging
import sys
import time
import types
from pathlib import Path

import numpy as np
import pandas as pd

from src.data.ignore_regions import filter_ignored_boxes
from .common import CLASS_NAMES, UPSTREAM, fingerprint, sha256_file, write_json

DEFAULT_UPSTREAM = UPSTREAM
GT_COLUMNS = [
    "sequence",
    "frame_id",
    "track_id",
    "class_id",
    "x",
    "y",
    "w",
    "h",
]
PRED_COLUMNS = [
    "frame_id",
    "track_id",
    "class_id",
    "x",
    "y",
    "w",
    "h",
    "confidence",
]
PROTOCOL = dict(
    version=1,
    clear_empty_frame_reset=True,
    official_pr_mota=False,
    clear_iou_threshold=0.7,
    identity_iou_threshold=0.7,
    hota_thresholds=list(np.arange(5, 100, 5) / 100),
    frame_policy="Compact XML-declared frames in order; preserve IDs across gaps",
    ignore_policy="Predictions: visible union overlap >= 0.5; GT unchanged",
    aggregation="TrackEval combine_sequences then combine_classes_det_averaged",
    score_scale="0-100",
    count_scale="integer",
    gt_extra_filters=False,
)


def _metrics(upstream):
    """Avoid importing unrelated benchmark adapters and optional dependencies."""
    directory = Path(upstream) / "eval/TrackEval/trackeval"
    if not (directory / "metrics/hota.py").is_file():
        raise FileNotFoundError(directory)
    name = (
        "_detrac_te_"
        + hashlib.sha256(str(directory.resolve()).encode()).hexdigest()[:12]
    )
    for package, path in [
        (name, directory),
        (name + ".metrics", directory / "metrics"),
    ]:
        if package not in sys.modules:
            module = types.ModuleType(package)
            module.__path__ = [str(path.resolve())]
            sys.modules[package] = module
    return {
        "HOTA": importlib.import_module(name + ".metrics.hota").HOTA(),
        "CLEAR": importlib.import_module(name + ".metrics.clear").CLEAR(
            {"THRESHOLD": 0.7, "PRINT_CONFIG": False}
        ),
        "Identity": importlib.import_module(
            name + ".metrics.identity"
        ).Identity({"THRESHOLD": 0.7, "PRINT_CONFIG": False}),
    }


def evaluation_fingerprint(dataset, upstream=UPSTREAM, selected=None):
    """Cover the GT, masks, protocol and metric implementations in cache keys."""
    dataset, upstream = Path(dataset), Path(upstream or UPSTREAM)
    paths = [
        dataset / "canonical/annotations.csv",
        dataset / "canonical/ignored_regions.csv",
        dataset / "metadata/sequences.csv",
        dataset / "metadata/annotated_frames.csv",
        Path(__file__),
        Path(sys.modules[filter_ignored_boxes.__module__].__file__),
    ]
    te = upstream / "eval/TrackEval/trackeval"
    paths += [
        te / name
        for name in [
            "metrics/hota.py",
            "metrics/identity.py",
            "metrics/clear.py",
            "metrics/_base_metric.py",
            "utils.py",
            "_timing.py",
        ]
    ]
    return fingerprint(
        dict(
            protocol=PROTOCOL,
            sequences=selected,
            checksums=[sha256_file(path) for path in paths],
        )
    )


def _validate_boxes(table, num_frames, context):
    """Reject invalid identifiers or numeric geometry before masking any frames."""
    numeric = ["frame_id", "track_id", "class_id", "x", "y", "w", "h"]
    if "confidence" in table:
        numeric.append("confidence")
    for column in numeric:
        table[column] = pd.to_numeric(table[column], errors="coerce")
    if not np.isfinite(table[numeric].to_numpy(float)).all():
        raise ValueError(f"{context}: nonfinite or nonnumeric record")
    for column in ["frame_id", "track_id", "class_id"]:
        if not (table[column] == np.floor(table[column])).all():
            raise ValueError(f"{context}: noninteger {column}")
        table[column] = table[column].astype(int)
    if not table.frame_id.between(1, num_frames).all():
        raise ValueError(f"{context}: frame_id outside timeline")
    if (table.track_id < 0).any() or not table.class_id.isin(
        CLASS_NAMES
    ).all():
        raise ValueError(f"{context}: invalid track/class ID")
    if ((table.w <= 0) | (table.h <= 0)).any():
        raise ValueError(f"{context}: nonpositive box size")
    if table.duplicated(["frame_id", "track_id"]).any():
        raise ValueError(f"{context}: duplicate track_id within a frame")


def _iou(gt, pred):
    """Full-box xywh IoU; canonical GT coordinates remain unchanged."""
    if not len(gt) or not len(pred):
        return np.zeros((len(gt), len(pred)))
    left = np.maximum(gt[:, None, :2], pred[None, :, :2])
    right = np.minimum(
        gt[:, None, :2] + gt[:, None, 2:],
        pred[None, :, :2] + pred[None, :, 2:],
    )
    intersection = np.maximum(right - left, 0).prod(axis=2)
    return intersection / (
        gt[:, 2:].prod(axis=1)[:, None]
        + pred[:, 2:].prod(axis=1)[None, :]
        - intersection
    )


def _metric_data(gt, pred, frames):
    """Map IDs to contiguous metric indices without breaking identity at gaps."""
    gt_map = {value: i for i, value in enumerate(sorted(gt.track_id.unique()))}
    pred_map = {
        value: i for i, value in enumerate(sorted(pred.track_id.unique()))
    }
    gg, pg = dict(tuple(gt.groupby("frame_id"))), dict(
        tuple(pred.groupby("frame_id"))
    )
    data = dict(
        num_timesteps=len(frames),
        num_gt_ids=len(gt_map),
        num_tracker_ids=len(pred_map),
        num_gt_dets=len(gt),
        num_tracker_dets=len(pred),
        gt_ids=[],
        tracker_ids=[],
        gt_dets=[],
        tracker_dets=[],
        similarity_scores=[],
    )
    for frame in frames:
        g, p = gg.get(frame, gt.iloc[:0]), pg.get(frame, pred.iloc[:0])
        gb, pb = g[["x", "y", "w", "h"]].to_numpy(float), p[
            ["x", "y", "w", "h"]
        ].to_numpy(float)
        data["gt_ids"].append(
            np.array([gt_map[x] for x in g.track_id], dtype=int)
        )
        data["tracker_ids"].append(
            np.array([pred_map[x] for x in p.track_id], dtype=int)
        )
        data["gt_dets"].append(gb)
        data["tracker_dets"].append(pb)
        data["similarity_scores"].append(_iou(gb, pb))
    return data


def _summary(results):
    """Expose score percentages and integer counts."""
    h, c, i = results["HOTA"], results["CLEAR"], results["Identity"]
    return dict(
        HOTA=float(np.mean(h["HOTA"])) * 100,
        IDF1=float(i["IDF1"]) * 100,
        MOTA=float(c["MOTA"]) * 100,
        AssA=float(np.mean(h["AssA"])) * 100,
        DetA=float(np.mean(h["DetA"])) * 100,
        IDS=int(c["IDSW"]),
        FP=int(c["CLR_FP"]),
        FN=int(c["CLR_FN"]),
        Frag=int(c["Frag"]),
    )


def _combine(metrics, results, macro=False):
    method = (
        "combine_classes_class_averaged"
        if macro
        else "combine_classes_det_averaged"
    )
    return {
        name: getattr(metric, method)(
            {key: value[name] for key, value in results.items()}
        )
        for name, metric in metrics.items()
    }


def _plain(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def evaluate_run(dataset, run_dir, upstream=None, sequences=None):
    """Evaluate a saved class-aware tracking run with the explicit project protocol."""
    started = time.perf_counter()
    dataset, run_dir, upstream = (
        Path(dataset),
        Path(run_dir),
        Path(upstream or UPSTREAM),
    )
    manifest_path = run_dir / "run_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text())
        if manifest_path.is_file()
        else {}
    )
    selected = sorted(sequences or manifest.get("sequences", []))
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("Evaluation requires a unique nonempty sequence list")
    metadata = pd.read_csv(dataset / "metadata/sequences.csv").set_index(
        "sequence"
    )
    if metadata.index.duplicated().any() or set(selected) - set(
        metadata.index
    ):
        raise ValueError("Invalid or missing sequence metadata")
    annotated = pd.read_csv(dataset / "metadata/annotated_frames.csv")
    if annotated.duplicated(["sequence", "frame_id"]).any():
        raise ValueError("Duplicate annotated frame entries")
    parts = [
        chunk[chunk.sequence.isin(selected)]
        for chunk in pd.read_csv(
            dataset / "canonical/annotations.csv",
            usecols=GT_COLUMNS,
            chunksize=100_000,
        )
    ]
    canonical = pd.concat(parts, ignore_index=True)
    ignored = pd.read_csv(dataset / "canonical/ignored_regions.csv")
    metrics, all_results, sequence_rows, frame_rows = (
        _metrics(upstream),
        {},
        [],
        [],
    )
    activity = {name: 0 for name in CLASS_NAMES.values()}
    for index, sequence in enumerate(selected, 1):
        logging.info("Evaluating %s (%d/%d)", sequence, index, len(selected))
        info = metadata.loc[sequence]
        frames = sorted(
            annotated.loc[annotated.sequence == sequence, "frame_id"].tolist()
        )
        if (
            not frames
            or not np.isfinite(frames).all()
            or any(
                f != int(f) or not 1 <= f <= info.num_frames for f in frames
            )
        ):
            raise ValueError(f"{sequence}: invalid annotation timeline")
        gt = canonical[canonical.sequence == sequence].copy()
        pred = pd.read_csv(
            run_dir / "tracks" / f"{sequence}.csv", usecols=PRED_COLUMNS
        )
        _validate_boxes(gt, info.num_frames, f"{sequence} GT")
        _validate_boxes(pred, info.num_frames, f"{sequence} prediction")
        count = len(pred)
        gt = gt[gt.frame_id.isin(frames)]
        pred = pred[pred.frame_id.isin(frames)].reset_index(drop=True)
        unknown = count - len(pred)
        regions = ignored.loc[
            ignored.sequence == sequence, ["x", "y", "w", "h"]
        ].to_numpy(float)
        boxes = [
            [r.x, r.y, r.w, r.h, i] for i, r in enumerate(pred.itertuples())
        ]
        kept = filter_ignored_boxes(
            boxes, regions, int(info.width), int(info.height), 0.5
        )
        removed = len(pred) - len(kept)
        pred = pred.iloc[[int(box[-1]) for box in kept]]
        results = {}
        for class_id, name in CLASS_NAMES.items():
            g, p = gt[gt.class_id == class_id], pred[pred.class_id == class_id]
            activity[name] += len(g) + len(p)
            data = _metric_data(g, p, frames)
            results[name] = {
                key: metric.eval_sequence(data)
                for key, metric in metrics.items()
            }
        all_results[sequence] = results
        sequence_rows.append(
            dict(
                sequence=sequence,
                **_summary(_combine(metrics, results)),
                num_frames=int(info.num_frames),
                evaluated_frames=len(frames),
                unannotated_prediction_count=unknown,
                ignored_prediction_count=removed,
            )
        )
        frame_rows.extend(
            dict(sequence=sequence, evaluation_frame_id=i, frame_id=int(f))
            for i, f in enumerate(frames, 1)
        )
    combined = {
        name: {
            key: metric.combine_sequences(
                {s: all_results[s][name][key] for s in selected}
            )
            for key, metric in metrics.items()
        }
        for name in CLASS_NAMES.values()
    }
    active = {
        name: result for name, result in combined.items() if activity[name]
    }
    class_rows = [
        dict(class_id=i, class_name=name, **_summary(combined[name]))
        for i, name in CLASS_NAMES.items()
    ]
    result = dict(
        **_summary(_combine(metrics, combined)),
        macro_average=_summary(
            _combine(metrics, active or combined, macro=True)
        ),
        per_class=class_rows,
        sequences=selected,
        sequence_count=len(selected),
        evaluated_frame_count=len(frame_rows),
        runtime_seconds=manifest.get("runtime_seconds"),
        evaluation_fingerprint=evaluation_fingerprint(
            dataset, upstream, selected
        ),
        prediction_checksums={
            s: sha256_file(run_dir / "tracks" / f"{s}.csv") for s in selected
        },
        evaluation_seconds=time.perf_counter() - started,
        official_pr_mota=False,
        calibration_reviewed=manifest.get("provenance", {}).get(
            "calibration_reviewed", True
        ),
    )
    output = run_dir / "evaluation"
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(sequence_rows).to_csv(
        output / "per_sequence.csv", index=False
    )
    pd.DataFrame(class_rows).to_csv(output / "per_class.csv", index=False)
    pd.DataFrame(
        [
            dict(
                sequence=sequence,
                class_id=class_id,
                class_name=name,
                **_summary(all_results[sequence][name]),
            )
            for sequence in selected
            for class_id, name in CLASS_NAMES.items()
        ]
    ).to_csv(output / "per_sequence_class.csv", index=False)
    pd.DataFrame(frame_rows).to_csv(output / "frame_map.csv", index=False)
    write_json(output / "protocol.json", PROTOCOL)
    write_json(output / "metrics.json", _plain(all_results))
    write_json(output / "summary.json", result)
    return result
