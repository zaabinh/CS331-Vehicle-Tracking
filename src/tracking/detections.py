"""Lossless detector adaptation and the upstream ground-plane noise model."""

from pathlib import Path

import numpy as np
import pandas as pd

from .common import CLASS_NAMES, fingerprint, sha256_file, write_json

SOURCE_COLUMNS = ["frame_id", "class_id", "confidence", "x1", "y1", "x2", "y2"]
MOT_COLUMNS = [
    "frame_id",
    "det_id",
    "x",
    "y",
    "w",
    "h",
    "score",
    "class_id",
    "unused_1",
    "unused_2",
]


def normalize_detections(path, sequence, num_frames):
    """Convert xyxy to xywh without filtering, clipping, NMS or deduplication."""
    source = pd.read_csv(path)
    missing = set(SOURCE_COLUMNS) - set(source.columns)
    if missing:
        raise ValueError(f"{sequence}: missing columns {sorted(missing)}")
    values = (
        source[SOURCE_COLUMNS].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    )

    def fail(mask, reason):
        bad = np.flatnonzero(mask)
        if len(bad):
            raise ValueError(
                f"{sequence}: {reason}; source CSV rows {(bad[:10] + 2).tolist()}"
            )

    fail(~np.isfinite(values).all(axis=1), "nonfinite/nonnumeric value")
    frame, category, score, x1, y1, x2, y2 = values.T
    fail(frame != np.floor(frame), "noninteger frame_id")
    fail((frame < 1) | (frame > num_frames), "frame outside timeline")
    fail(category != np.floor(category), "noninteger class_id")
    fail(~np.isin(category, list(CLASS_NAMES)), "unknown class_id")
    fail((x2 <= x1) | (y2 <= y1), "bbox width/height must be positive")
    rows = np.column_stack(
        [
            frame,
            np.arange(1, len(values) + 1),
            x1,
            y1,
            x2 - x1,
            y2 - y1,
            score,
            category,
            np.full(len(values), -1),
            np.full(len(values), -1),
        ]
    )
    return rows[np.argsort(frame, kind="stable")]


def load_cached_detections(path, sequence, num_frames, cache_dir):
    """Cache normalized arrays against source contents and adapter code."""
    provenance = dict(
        source_sha256=sha256_file(path),
        sequence=sequence,
        num_frames=int(num_frames),
        adapter_sha256=sha256_file(__file__),
    )
    key = fingerprint(provenance)
    target = Path(cache_dir) / f"{sequence}_{key}.npz"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        with np.load(target, allow_pickle=False) as archive:
            rows = archive["detections"]
    else:
        rows = normalize_detections(path, sequence, num_frames)
        tmp = target.with_suffix(".tmp")
        with tmp.open("wb") as stream:
            np.savez_compressed(stream, detections=rows)
        tmp.replace(target)
        write_json(target.with_suffix(".json"), provenance)
    return rows, provenance


def detections_by_frame(rows):
    """Index only populated frames; callers supply [] for every other frame."""
    if not len(rows):
        return {}
    boundaries = np.flatnonzero(np.diff(rows[:, 0])) + 1
    return {int(group[0, 0]): group for group in np.split(rows, boundaries)}


def write_mot_detections(path, rows):
    """Write deterministic ten-column upstream-compatible detection records."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(
        path,
        rows,
        delimiter=",",
        fmt=[
            "%d",
            "%d",
            "%.17g",
            "%.17g",
            "%.17g",
            "%.17g",
            "%.17g",
            "%d",
            "%d",
            "%d",
        ],
    )


class GroundMapper:
    """Map bbox bottom centers with the unchanged upstream measurement model."""

    def __init__(self, ground_to_image):
        self.A = np.asarray(ground_to_image, dtype=float)
        if self.A.shape != (3, 3) or not np.isfinite(self.A).all():
            raise ValueError("A_ground_to_image must be finite 3x3")
        try:
            self.H = np.linalg.inv(self.A)
        except np.linalg.LinAlgError as exc:
            raise ValueError("Singular A_ground_to_image") from exc

    def uv2xy(self, uv, covariance):
        """Propagate image covariance with the inverse-homography Jacobian."""
        projected = self.H @ np.r_[np.asarray(uv).reshape(2), 1.0]
        if not np.isfinite(projected).all() or abs(projected[2]) < 1e-12:
            raise ValueError("Ground projection is at infinity")
        position = projected[:2] / projected[2]
        jacobian = (self.H[:2, :2] - position[:, None] * self.H[2, :2]) / projected[2]
        propagated = jacobian @ covariance @ jacobian.T
        if not np.isfinite(propagated).all():
            raise ValueError("Nonfinite mapped covariance")
        return position.reshape(2, 1), propagated

    def xy2uv(self, x, y):
        """Project a ground point to the source image."""
        point = self.A @ [x, y, 1.0]
        if not np.isfinite(point).all() or abs(point[2]) < 1e-12:
            raise ValueError("Image projection is at infinity")
        return point[0] / point[2], point[1] / point[2]

    def mapto(self, bbox, image_size=None, boundary_sigma=0.0):
        """Keep upstream noise by default; optionally model clipped footpoints."""
        x, y, w, h = bbox
        covariance = np.diag(
            [np.clip(0.05 * h, 2, 13) ** 2, np.clip(0.05 * h, 2, 10) ** 2]
        )
        if boundary_sigma:
            if not np.isfinite(boundary_sigma) or boundary_sigma < 0:
                raise ValueError("boundary_sigma must be finite and nonnegative")
            if image_size is None:
                raise ValueError("Boundary noise requires image_size")
            width, height = image_size
            # A clipped box censors the footpoint on the affected image axis.
            if x <= 2 or x + w >= width - 2:
                covariance[0, 0] = max(covariance[0, 0], boundary_sigma**2)
            if y + h >= height - 2:
                covariance[1, 1] = max(covariance[1, 1], boundary_sigma**2)
        return self.uv2xy([x + w / 2, y + h], covariance)
