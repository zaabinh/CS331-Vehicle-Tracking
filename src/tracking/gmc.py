"""Cached previous-to-current background motion for UCMCTrack+."""

import json
import logging
from pathlib import Path

import cv2
import numpy as np

from .common import fingerprint, sha256_file, write_json

IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])


def foreground_mask(shape, boxes):
    """Exclude all exported vehicle boxes from feature extraction."""
    height, width = shape[:2]
    mask = np.full((height, width), 255, np.uint8)
    for x, y, w, h in boxes:
        left, top = max(0, min(width, int(np.floor(x)))), max(
            0, min(height, int(np.floor(y)))
        )
        right, bottom = max(0, min(width, int(np.ceil(x + w)))), max(
            0, min(height, int(np.ceil(y + h)))
        )
        mask[top:bottom, left:right] = 0
    return mask


def estimate_affine(previous, current, previous_boxes=(), current_boxes=()):
    """Estimate a partial affine using LK tracks and RANSAC on background."""
    if previous.shape != current.shape:
        raise ValueError("Camera motion images have different dimensions")
    previous = (
        previous
        if previous.ndim == 2
        else cv2.cvtColor(previous, cv2.COLOR_BGR2GRAY)
    )
    current = (
        current
        if current.ndim == 2
        else cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
    )
    points = cv2.goodFeaturesToTrack(
        previous,
        1500,
        0.01,
        8,
        mask=foreground_mask(previous.shape, previous_boxes),
    )
    if points is None or len(points) < 4:
        return IDENTITY.copy(), "insufficient_background_features"
    moved, status, _ = cv2.calcOpticalFlowPyrLK(
        previous, current, points, None, winSize=(21, 21), maxLevel=3
    )
    if moved is None or status is None:
        return IDENTITY.copy(), "optical_flow_failed"
    source, target = points.reshape(-1, 2), moved.reshape(-1, 2)
    height, width = current.shape
    keep = status.ravel().astype(bool) & np.isfinite(target).all(axis=1)
    keep &= (
        (target[:, 0] >= 0)
        & (target[:, 0] < width)
        & (target[:, 1] >= 0)
        & (target[:, 1] < height)
    )
    indices = np.flatnonzero(keep)
    xy = np.floor(target[indices]).astype(int)
    keep[indices] &= (
        foreground_mask(current.shape, current_boxes)[xy[:, 1], xy[:, 0]] > 0
    )
    if keep.sum() < 4:
        return IDENTITY.copy(), "insufficient_tracked_features"
    affine, inliers = cv2.estimateAffinePartial2D(
        source[keep],
        target[keep],
        method=cv2.RANSAC,
        ransacReprojThreshold=3.0,
    )
    if affine is None or not np.isfinite(affine).all() or inliers.sum() < 3:
        return IDENTITY.copy(), "ransac_failed"
    return affine, None


def load_or_estimate_gmc(
    sequence, images, by_frame, num_frames, cache_dir, detections_sha256
):
    """Fingerprint every image so camera-motion caches survive only matching inputs."""
    if set(range(1, num_frames + 1)) - set(images):
        raise ValueError(f"{sequence}: incomplete CMC image timeline")
    provenance = dict(
        code=sha256_file(__file__),
        opencv_version=cv2.__version__,
        sequence=sequence,
        num_frames=num_frames,
        detections_sha256=detections_sha256,
        direction="previous_to_current",
        images_sha256=fingerprint(
            [sha256_file(images[f]) for f in range(1, num_frames + 1)]
        ),
    )
    path = Path(cache_dir) / f"{sequence}_{fingerprint(provenance)}.npz"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        with np.load(path, allow_pickle=False) as data:
            return data["transforms"], json.loads(
                path.with_suffix(".json").read_text()
            )
    transforms = np.repeat(IDENTITY[None], num_frames + 1, axis=0)
    failures, previous, previous_boxes = [], None, []
    cv2.setRNGSeed(42)
    for frame in range(1, num_frames + 1):
        current = cv2.imread(str(images[frame]), cv2.IMREAD_GRAYSCALE)
        if current is None:
            raise ValueError(f"{sequence}: unreadable frame {frame}")
        rows = by_frame.get(frame)
        boxes = [] if rows is None else rows[:, 2:6]
        if previous is not None:
            transforms[frame], reason = estimate_affine(
                previous, current, previous_boxes, boxes
            )
            if reason:
                failures.append(dict(frame_id=frame, reason=reason))
                logging.warning(
                    "CMC %s frame %d: %s; identity fallback",
                    sequence,
                    frame,
                    reason,
                )
        previous, previous_boxes = current, boxes
        if frame % 100 == 0 or frame == num_frames:
            logging.info("CMC %s %d/%d", sequence, frame, num_frames)
    temp = path.with_suffix(".tmp")
    with temp.open("wb") as stream:
        np.savez_compressed(stream, transforms=transforms)
    temp.replace(path)
    manifest = dict(**provenance, failures=failures)
    write_json(path.with_suffix(".json"), manifest)
    return transforms, manifest
