"""Run upstream UCMC with explicit, reproducible optional adaptations."""

import importlib
import json
import logging
import sys
import time
import types
from pathlib import Path

import numpy as np
import pandas as pd

from .calibration import resolve_calibration
from .common import (
    CLASS_NAMES,
    UPSTREAM,
    find_detection_csv,
    find_sequence_images,
    environment_versions,
    fingerprint,
    image_fingerprint,
    read_sequences,
    resolve_config,
    sha256_file,
    write_json,
)
from .detections import (
    GroundMapper,
    detections_by_frame,
    load_cached_detections,
)
from .lifecycle import update_tracker

TRACK_COLUMNS = [
    "frame_id",
    "track_id",
    "class_id",
    "x",
    "y",
    "w",
    "h",
    "confidence",
]


def _upstream(upstream):
    """Isolate the clone's tracker package from project module names."""
    name = "_ua_ucmc_" + fingerprint(str(Path(upstream).resolve()))[:12]
    if name not in sys.modules:
        package = types.ModuleType(name)
        package.__path__ = [str((Path(upstream) / "tracker").resolve())]
        sys.modules[name] = package
    return (
        importlib.import_module(name + ".ucmc").UCMCTrack,
        importlib.import_module(name + ".kalman").KalmanTracker,
    )


class _Provider:
    def __init__(self, mapper, transforms):
        self.mapper, self.transforms = mapper, transforms

    def cmc(self, x, y, w, h, frame_id):
        """Match upstream Detector.cmc's forward image-box compensation."""
        u, v = self.mapper.xy2uv(x, y)
        affine = self.transforms[frame_id]
        center = affine[:, :2] @ [u, v - h / 2] + affine[:, 2]
        wh = affine[:, :2] @ [w, h]
        position, _ = self.mapper.uv2xy([center[0], center[1] + wh[1] / 2], np.eye(2))
        return float(position[0, 0]), float(position[1, 0])


def interpolate_tracks(table, max_age):
    """Upstream DTI: >3 observations and 1 < frame distance < max_age."""
    extra = []
    for (_, _), group in table.groupby(["class_id", "track_id"]):
        group = group.sort_values("frame_id")
        if len(group) <= 3:
            continue
        records = list(group.itertuples(index=False))
        for left, right in zip(records, records[1:]):
            gap = right.frame_id - left.frame_id
            if 1 < gap < max_age:
                for frame in range(left.frame_id + 1, right.frame_id):
                    weight = (frame - left.frame_id) / gap
                    box = [
                        (1 - weight) * getattr(left, c) + weight * getattr(right, c)
                        for c in ["x", "y", "w", "h"]
                    ]
                    extra.append([frame, left.track_id, left.class_id, *box, -1.0])
    return pd.concat(
        [table, pd.DataFrame(extra, columns=TRACK_COLUMNS)], ignore_index=True
    ).sort_values(["frame_id", "track_id"])


def _preview(dataset, sequence, table, output, num_frames):
    import cv2

    images = find_sequence_images(dataset, sequence)
    sample = cv2.imread(str(images[1]))
    height, width = sample.shape[:2]
    path = Path(output) / "previews" / f"{sequence}.mp4"
    path.parent.mkdir(exist_ok=True)
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 25, (width, height)
    )
    if not writer.isOpened():
        raise RuntimeError(f"Cannot write preview video: {path}")
    groups = dict(tuple(table.groupby("frame_id")))
    try:
        for frame in range(1, min(num_frames, len(images)) + 1):
            image = cv2.imread(str(images[frame]))
            if image is None:
                raise ValueError(f"Unreadable preview frame {frame}")
            for row in groups.get(frame, table.iloc[:0]).itertuples():
                cv2.rectangle(
                    image,
                    (int(row.x), int(row.y)),
                    (int(row.x + row.w), int(row.y + row.h)),
                    (0, 255, 0),
                    2,
                )
                cv2.putText(
                    image,
                    f"{CLASS_NAMES[row.class_id]} #{row.track_id}",
                    (int(row.x), max(15, int(row.y) - 3)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 0),
                    1,
                )
            writer.write(image)
    finally:
        writer.release()


def run_tracking(
    dataset,
    detections,
    calibration,
    output,
    config=None,
    method="homography",
    sequences=None,
    resume=False,
    upstream=None,
    allow_unreviewed=False,
    trace=False,
    fix_coasting=False,
):
    """Run full timelines; shared association and offline class voting are opt-in."""
    upstream, dataset, detections, output = (
        Path(upstream or UPSTREAM),
        Path(dataset),
        Path(detections),
        Path(output),
    )
    config = resolve_config(config)
    metadata = read_sequences(dataset)
    selected = sorted(
        sequences or [s for s, info in metadata.items() if info["split"] == "val"]
    )
    if (
        not selected
        or len(set(selected)) != len(selected)
        or set(selected) - set(metadata)
    ):
        raise ValueError("Invalid sequence selection")
    cameras, errors = {}, []
    if allow_unreviewed:
        logging.warning("Experimental run: camera approval is not enforced")
    for sequence in selected:
        try:
            cameras[sequence] = resolve_calibration(
                calibration,
                sequence,
                method,
                require_reviewed=not allow_unreviewed,
            )
            info = metadata[sequence]
            if cameras[sequence]["image_size"] != [
                int(info["width"]),
                int(info["height"]),
            ]:
                raise ValueError("Camera resolution differs from dataset")
            if float(info["fps"]) != 25:
                raise ValueError("Dataset FPS differs from 25")
        except (ValueError, KeyError, FileNotFoundError) as exc:
            errors.append(f"{sequence}/{method}: {exc}")
    if errors:
        raise ValueError(
            "Camera preflight failed; no tracking started:\n" + "\n".join(errors)
        )
    handoff_path = detections / "detection_handoff_config.json"
    handoff = json.loads(handoff_path.read_text())
    expected = {str(k): v for k, v in CLASS_NAMES.items()}
    if handoff.get("class_mapping") != expected:
        raise ValueError("Detector handoff class mapping differs from dataset")
    if config["conf"] < float(handoff["conf_threshold"]):
        raise ValueError("Requested confidence is below the detector export floor")
    files = {s: find_detection_csv(detections, s) for s in selected}
    code_files = list((upstream / "tracker").glob("*.py")) + list(
        Path(__file__).parent.glob("*.py")
    )
    provenance = dict(
        trace=trace,
        allow_unreviewed=allow_unreviewed,
        calibration_reviewed=not allow_unreviewed
        and all(c.get("status") == "reviewed" for c in cameras.values()),
        config=config,
        method=method,
        cameras=cameras,
        sequences=selected,
        environment=environment_versions(),
        detections={s: sha256_file(p) for s, p in files.items()},
        handoff=handoff,
        metadata=sha256_file(dataset / "metadata/sequences.csv"),
        code={str(p.resolve()): sha256_file(p) for p in sorted(code_files)},
    )
    if config["cmc"]:
        from .gmc import load_or_estimate_gmc

        provenance["images"] = {
            s: image_fingerprint(find_sequence_images(dataset, s))
            for s in selected
        }
    if fix_coasting:
        provenance["fix_coasting"] = True
    key = fingerprint(provenance)
    manifest_path = output / "run_manifest.json"
    previous = (
        json.loads(manifest_path.read_text()) if manifest_path.is_file() else None
    )
    if previous:
        if not resume or previous.get("fingerprint") != key:
            raise ValueError(
                "Run exists with different inputs/configuration or resume is disabled; choose a new output"
            )
        if previous.get("status") == "complete":
            for sequence in selected:
                for relative, digest in previous["outputs"][sequence].items():
                    if sha256_file(output / relative) != digest:
                        raise ValueError(f"Saved output changed: {relative}")
            logging.info("Reusing matching completed run: %s", output)
            return previous
    elif output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Nonempty run directory without a manifest: {output}")
    output.mkdir(parents=True, exist_ok=True)
    for folder in ["tracks", "raw_tracks", "mot", "sequence_reports"]:
        (output / folder).mkdir(exist_ok=True)
    manifest = dict(
        status="running",
        fingerprint=key,
        provenance=provenance,
        config=config,
        sequences=selected,
        method=method,
        outputs=(previous or {}).get("outputs", {}),
        sequence_reports=(previous or {}).get("sequence_reports", {}),
    )
    write_json(manifest_path, manifest)
    cache = output.parent / "cache"
    try:
        tracker_cls, kalman_cls = _upstream(upstream)
        for index, sequence in enumerate(selected, 1):
            if sequence in manifest["outputs"]:
                for relative, digest in manifest["outputs"][sequence].items():
                    if sha256_file(output / relative) != digest:
                        raise ValueError(f"Saved output changed: {relative}")
                logging.info("Resuming after completed sequence %s", sequence)
                continue
            info, begin = metadata[sequence], time.perf_counter()
            logging.info("Tracking %s (%d/%d)", sequence, index, len(selected))
            rows, _ = load_cached_detections(
                files[sequence],
                sequence,
                int(info["num_frames"]),
                cache / "detections",
            )
            by_frame = detections_by_frame(rows)
            adapt_seconds = time.perf_counter() - begin
            mapper = GroundMapper(cameras[sequence]["A_ground_to_image"])
            transforms = None
            cmc_started = time.perf_counter()
            if config["cmc"]:
                transforms, gmc_manifest = load_or_estimate_gmc(
                    sequence,
                    find_sequence_images(dataset, sequence),
                    by_frame,
                    int(info["num_frames"]),
                    cache / "gmc",
                    provenance["detections"][sequence],
                )
                write_json(
                    output / "sequence_reports" / f"{sequence}_gmc.json",
                    gmc_manifest,
                )
            cmc_seconds = time.perf_counter() - cmc_started if config["cmc"] else 0.0
            provider = _Provider(mapper, transforms)
            kalman_cls.count = 1
            categories = (
                [None] if config["class_mode"] == "shared_vote" else CLASS_NAMES
            )
            trackers = {
                c: tracker_cls(
                    config["a1"],
                    config["a2"],
                    config["wx"],
                    config["wy"],
                    config["vmax"],
                    config["max_age"],
                    25,
                    "UA-DETRAC",
                    config["high_score"],
                    config["cmc"],
                    provider,
                )
                for c in categories
            }
            raw, histories, activated = [], {}, set()
            class_votes = {}
            trace_rows, trace_costs = [], []
            mapped_seconds, update_seconds = 0.0, 0.0
            for frame in range(1, int(info["num_frames"]) + 1):
                clock = time.perf_counter()
                class_dets = {c: [] for c in categories}
                for row in by_frame.get(frame, []):
                    if row[6] < config["conf"]:
                        continue
                    detection = types.SimpleNamespace(
                        id=int(row[1]),
                        conf=float(row[6]),
                        bb_left=row[2],
                        bb_top=row[3],
                        bb_width=row[4],
                        bb_height=row[5],
                        track_id=0,
                        class_id=int(row[7]),
                    )
                    try:
                        detection.y, detection.R = mapper.mapto(
                            row[2:6],
                            cameras[sequence]["image_size"],
                            config["boundary_sigma"],
                        )
                    except ValueError as exc:
                        raise ValueError(
                            f"{sequence}/frame {frame}/det {int(row[1])}: {exc}"
                        ) from exc
                    category = (
                        None if config["class_mode"] == "shared_vote" else int(row[7])
                    )
                    class_dets[category].append(detection)
                mapped_seconds += time.perf_counter() - clock
                clock = time.perf_counter()
                for category, tracker in trackers.items():
                    dets = class_dets[category]
                    # Record actual costs without changing upstream assignment.
                    tracing = trace and (
                        sequence == "MVI_39771"
                        and (frame <= 60 or 370 <= frame <= 505)
                        or sequence == "MVI_20052"
                        and 520 <= frame <= 650
                    )
                    previous_ids = {t.id for t in tracker.trackers}
                    originals = []
                    if tracing:
                        lookup = {d.y.tobytes(): d.id for d in dets}
                        for track in tracker.trackers:
                            original = track.distance
                            originals.append((track, original))

                            def distance(
                                y, covariance, original=original, tid=track.id
                            ):
                                cost = original(y, covariance)
                                trace_costs.append(
                                    [
                                        frame,
                                        category,
                                        tid,
                                        lookup.get(y.tobytes()),
                                        float(cost),
                                    ]
                                )
                                return cost

                            track.distance = distance
                    update_tracker(tracker, dets, frame, fix_coasting)
                    if config["class_mode"] == "shared_vote":
                        for track in tracker.trackers:
                            if 0 <= track.detidx < len(dets):
                                d = dets[track.detidx]
                                votes = class_votes.setdefault(
                                    track.id, np.zeros(len(CLASS_NAMES))
                                )
                                votes[d.class_id] += d.conf
                    for track, original in originals:
                        track.distance = original
                    if tracing:
                        for track in tracker.trackers:
                            if 0 <= track.detidx < len(dets):
                                d = dets[track.detidx]
                                trace_rows.append(
                                    [
                                        frame,
                                        category,
                                        d.id,
                                        track.id,
                                        track.status.name,
                                        track.id not in previous_ids,
                                        d.conf,
                                        float(d.y[0, 0]),
                                        float(d.y[1, 0]),
                                        float(d.R[0, 0]),
                                        float(d.R[1, 1]),
                                        d.bb_left,
                                        d.bb_top,
                                        d.bb_width,
                                        d.bb_height,
                                    ]
                                )
                    for tidx in tracker.confirmed_idx:
                        track = tracker.trackers[tidx]
                        if not 0 <= track.detidx < len(dets):
                            continue
                        d = dets[track.detidx]
                        record = [
                            frame,
                            track.id,
                            d.class_id,
                            d.bb_left,
                            d.bb_top,
                            d.bb_width,
                            d.bb_height,
                            d.conf,
                        ]
                        raw.append(record)
                        activated.add(track.id)
                    if config["hp"]:
                        for tidx in tracker.tentative_idx + tracker.confirmed_idx:
                            track = tracker.trackers[tidx]
                            if 0 <= track.detidx < len(dets):
                                d = dets[track.detidx]
                                histories.setdefault(track.id, {})[frame] = [
                                    frame,
                                    track.id,
                                    d.class_id,
                                    d.bb_left,
                                    d.bb_top,
                                    d.bb_width,
                                    d.bb_height,
                                    d.conf,
                                ]
                update_seconds += time.perf_counter() - clock
                if frame % 250 == 0 or frame == int(info["num_frames"]):
                    logging.info("%s frame %d/%d", sequence, frame, info["num_frames"])
            raw_table = pd.DataFrame(raw, columns=TRACK_COLUMNS).sort_values(
                ["frame_id", "track_id"]
            )
            raw_table.to_csv(output / "raw_tracks" / f"{sequence}.csv", index=False)
            if trace_rows:
                folder = output / "association_trace"
                folder.mkdir(exist_ok=True)
                pd.DataFrame(
                    trace_rows,
                    columns=[
                        "frame_id",
                        "class_id",
                        "det_id",
                        "track_id",
                        "status",
                        "new_track",
                        "confidence",
                        "ground_x",
                        "ground_y",
                        "variance_x",
                        "variance_y",
                        "x",
                        "y",
                        "w",
                        "h",
                    ],
                ).to_csv(folder / f"{sequence}_assignments.csv", index=False)
                pd.DataFrame(
                    trace_costs,
                    columns=[
                        "frame_id",
                        "class_id",
                        "track_id",
                        "det_id",
                        "cost",
                    ],
                ).to_csv(folder / f"{sequence}_costs.csv", index=False)
            post_started = time.perf_counter()
            if config["hp"]:
                padded = [
                    r
                    for track in sorted(activated)
                    for r in histories.get(track, {}).values()
                ]
                table = pd.DataFrame(padded, columns=TRACK_COLUMNS)
            else:
                table = raw_table.copy()
            if config["class_mode"] == "shared_vote":
                # Offline output: one confidence-weighted class per complete track.
                labels = {tid: int(np.argmax(v)) for tid, v in class_votes.items()}
                table["class_id"] = table.track_id.map(labels).astype(int)
                write_json(
                    output / "sequence_reports" / f"{sequence}_class_votes.json",
                    {str(tid): v.tolist() for tid, v in class_votes.items()},
                )
            if config["interpolation"]:
                table = interpolate_tracks(table, config["max_age"])
            table = table.sort_values(["frame_id", "track_id"])
            post_seconds = time.perf_counter() - post_started
            table.to_csv(output / "tracks" / f"{sequence}.csv", index=False)
            mot = table[
                ["frame_id", "track_id", "x", "y", "w", "h", "confidence"]
            ].copy()
            mot["u1"], mot["u2"], mot["u3"] = -1, -1, -1
            mot.to_csv(output / "mot" / f"{sequence}.txt", header=False, index=False)
            preview_started = time.perf_counter()
            if config["preview_frames"]:
                _preview(dataset, sequence, table, output, config["preview_frames"])
            report = dict(
                frames_processed=int(info["num_frames"]),
                adapter_seconds=adapt_seconds,
                cmc_seconds=cmc_seconds,
                mapping_seconds=mapped_seconds,
                tracker_seconds=update_seconds,
                postprocess_seconds=post_seconds,
                preview_seconds=time.perf_counter() - preview_started,
                total_seconds=time.perf_counter() - begin,
                output_rows=len(table),
                track_fps=int(info["num_frames"]) / max(update_seconds, 1e-12),
            )
            write_json(output / "sequence_reports" / f"{sequence}.json", report)
            manifest["sequence_reports"][sequence] = report
            manifest["outputs"][sequence] = {
                f"{folder}/{sequence}.{ext}": sha256_file(
                    output / folder / f"{sequence}.{ext}"
                )
                for folder, ext in [
                    ("tracks", "csv"),
                    ("raw_tracks", "csv"),
                    ("mot", "txt"),
                ]
            }
            write_json(manifest_path, manifest)
        manifest.update(
            status="complete",
            runtime_seconds=sum(
                r["total_seconds"] for r in manifest["sequence_reports"].values()
            ),
        )
        write_json(manifest_path, manifest)
        return manifest
    except Exception as exc:
        manifest.update(status="failed", error=str(exc))
        write_json(manifest_path, manifest)
        raise
