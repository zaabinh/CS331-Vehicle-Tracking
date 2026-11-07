"""Lọc prediction hoặc track nằm chủ yếu trong vùng DETRAC ignore."""
import math

import numpy as np


def filter_ignored_boxes(boxes, regions, width, height, threshold=0.5):
    """Remove boxes whose visible area overlaps ignore regions by threshold."""
    if len(regions) == 0:
        return list(boxes)
    if not 0 < threshold <= 1:
        raise ValueError("threshold must be in (0, 1]")

    mask = np.zeros((height, width), dtype=np.uint8)
    for x, y, box_width, box_height in regions:
        left = max(0, int(math.floor(x)))
        top = max(0, int(math.floor(y)))
        right = min(width, int(math.ceil(x + box_width)))
        bottom = min(height, int(math.ceil(y + box_height)))
        if right > left and bottom > top:
            mask[top:bottom, left:right] = 1

    # Integral image tính giao box-ignore nhanh mà không đếm vùng chồng lặp.
    integral = np.pad(mask.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    kept = []
    for box in boxes:
        x, y, box_width, box_height = box[:4]
        left = max(0, int(math.floor(x)))
        top = max(0, int(math.floor(y)))
        right = min(width, int(math.ceil(x + box_width)))
        bottom = min(height, int(math.ceil(y + box_height)))
        area = max(0, right - left) * max(0, bottom - top)
        if area == 0:
            continue
        overlap = (
            integral[bottom, right] - integral[top, right]
            - integral[bottom, left] + integral[top, left]
        )
        if overlap / area < threshold:
            kept.append(box)
    return kept
