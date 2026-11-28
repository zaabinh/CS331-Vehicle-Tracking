"""One-to-one detector coverage audit under the current evaluation protocol."""

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from src.tracking.evaluation import _iou, filter_evaluation_boxes

BOX = ["x", "y", "w", "h"]


def assigned_rows(similarity, allowed, threshold=0.7):
    """Return GT rows matched one-to-one, maximizing cardinality before IoU."""
    score = np.where(allowed & (similarity >= threshold), 1000 + similarity, 0)
    rows, cols = linear_sum_assignment(-score)
    return set(rows[score[rows, cols] > 0])


def audit_sequence(gt, detections, frames, regions, width, height):
    """Retain per-GT evidence; candidate coverage and assignment are distinct."""
    gt = filter_evaluation_boxes(gt, regions, width, height)
    pred = detections.copy()
    pred["x"], pred["y"] = pred.x1, pred.y1
    pred["w"], pred["h"] = pred.x2 - pred.x1, pred.y2 - pred.y1
    pred = filter_evaluation_boxes(pred, regions, width, height)
    pg = {f: t for f, t in pred.groupby("frame_id")}
    output = []
    for frame, g in gt[gt.frame_id.isin(frames)].groupby("frame_id"):
        p = pg.get(frame, pred.iloc[:0])
        similarity = _iou(g[BOX].to_numpy(float), p[BOX].to_numpy(float))
        same = g.class_id.to_numpy()[:, None] == p.class_id.to_numpy()[None, :]
        strict = assigned_rows(similarity, same)
        loose = assigned_rows(similarity, same, 0.5)
        any_class = assigned_rows(similarity, np.ones_like(same, dtype=bool))
        for index, row in enumerate(g.itertuples()):
            max_same = similarity[index][same[index]].max(initial=0)
            max_any = similarity[index].max(initial=0)
            output.append(
                dict(
                    frame_id=int(frame),
                    gt_id=int(row.track_id),
                    class_id=int(row.class_id),
                    x=row.x,
                    y=row.y,
                    w=row.w,
                    h=row.h,
                    assigned_same_iou07=index in strict,
                    assigned_same_iou05=index in loose,
                    assigned_any_iou07=index in any_class,
                    max_same_iou=float(max_same),
                    max_any_iou=float(max_any),
                )
            )
    return pd.DataFrame(output)
