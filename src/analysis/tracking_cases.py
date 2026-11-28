"""Explain saved tracking results without changing runs or source annotations."""

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from scipy.optimize import linear_sum_assignment

from src.data.ignore_regions import filter_ignored_boxes
from src.tracking.common import CLASS_NAMES, find_sequence_images
from src.tracking.evaluation import _iou, filter_evaluation_boxes

BOX = ["x", "y", "w", "h"]
COLORS = {
    "TP": "#26d07c",
    "FP": "#ff4545",
    "FN": "#ffb347",
    "IDS": "#ff55ee",
    "excluded": "#aaaaaa",
    "GT": "#46aaff",
}


def diagnose(
    gt, pred, annotated, num_frames, regions, width, height, protocol_version=2
):
    """Reproduce CLEAR matching at IoU 0.7 and retain frame-level evidence.

    Continuity is maintained across unannotated frames, as in evaluation.
    Prediction indices refer to the original CSV, including excluded boxes.
    """
    gt, pred = gt.copy(), pred.copy().reset_index(drop=True)
    raw_gt = gt.copy()
    if protocol_version not in (1, 2):
        raise ValueError(f"Unsupported protocol version: {protocol_version}")
    if protocol_version == 2:
        gt = filter_evaluation_boxes(gt, regions, width, height)
    annotated = set(map(int, annotated))
    candidates = pred[pred.frame_id.isin(annotated)]
    boxes = [
        [*box, index]
        for box, index in zip(candidates[BOX].to_numpy(float), candidates.index)
    ]
    kept = {
        int(b[-1])
        for b in filter_ignored_boxes(boxes, regions, int(width), int(height), 0.5)
    }
    excluded = set(candidates.index) - kept
    active = pred.loc[sorted(kept)]
    events = []

    # Convert once per frame instead of selecting DataFrame columns in the loop.
    def frame_arrays(table):
        return {
            int(f): (
                t.track_id.to_numpy(int),
                t[BOX].to_numpy(float),
                t.index.to_numpy(),
            )
            for f, t in table.groupby("frame_id")
        }

    empty = (np.empty(0, int), np.empty((0, 4)), np.empty(0, int))
    for class_id in CLASS_NAMES:
        gg = frame_arrays(gt[gt.class_id == class_id])
        pp = frame_arrays(active[active.class_id == class_id])
        previous, last, last_frame = {}, {}, {}
        for frame in sorted(annotated):
            gids, gboxes, _ = gg.get(frame, empty)
            pids, pboxes, indices = pp.get(frame, empty)
            similarity = _iou(gboxes, pboxes)
            continuity = np.array([previous.get(int(i), -1) for i in gids])
            scores = 1000 * (continuity[:, None] == pids[None, :]) + similarity
            scores[similarity < 0.7 - np.finfo(float).eps] = 0
            rows, cols = linear_sum_assignment(-scores)
            valid = scores[rows, cols] > np.finfo(float).eps
            rows, cols = rows[valid], cols[valid]
            current = {}
            for r, c in zip(rows, cols):
                gid, pid = int(gids[r]), int(pids[c])
                old = last.get(gid)
                switched = old is not None and old != pid
                events.append(
                    dict(
                        frame_id=frame,
                        class_id=class_id,
                        gt_id=gid,
                        pred_id=pid,
                        pred_index=int(indices[c]),
                        kind="IDS" if switched else "TP",
                        iou=float(similarity[r, c]),
                        previous_pred_id=old,
                        previous_match_frame=last_frame.get(gid),
                        fragment=old is not None and gid not in previous,
                    )
                )
                current[gid] = pid
                last[gid], last_frame[gid] = pid, frame
            for r in set(range(len(gids))) - set(rows):
                events.append(
                    dict(
                        frame_id=frame,
                        class_id=class_id,
                        gt_id=int(gids[r]),
                        kind="FN",
                        fragment=False,
                    )
                )
            for c in set(range(len(pids))) - set(cols):
                events.append(
                    dict(
                        frame_id=frame,
                        class_id=class_id,
                        pred_id=int(pids[c]),
                        pred_index=int(indices[c]),
                        kind="FP",
                        fragment=False,
                    )
                )
            previous = current
    columns = [
        "frame_id",
        "class_id",
        "gt_id",
        "pred_id",
        "pred_index",
        "kind",
        "iou",
        "previous_pred_id",
        "previous_match_frame",
        "fragment",
    ]
    events = pd.DataFrame(events).reindex(columns=columns)
    stats = pd.DataFrame(index=pd.Index(range(1, num_frames + 1), name="frame_id"))
    stats["scored"] = stats.index.isin(annotated)
    for kind in ["TP", "FP", "FN", "IDS"]:
        mask = events.kind.isin(["TP", "IDS"]) if kind == "TP" else events.kind.eq(kind)
        stats[kind] = (
            events[mask].groupby("frame_id").size().reindex(stats.index, fill_value=0)
        )
    stats["Frag"] = (
        events[events.fragment.eq(True)]
        .groupby("frame_id")
        .size()
        .reindex(stats.index, fill_value=0)
    )
    stats["GT"] = stats.TP + stats.FN
    stats.loc[~stats.scored, ["TP", "FP", "FN", "IDS", "Frag", "GT"]] = np.nan
    tracks = []
    for (class_id, gid), table in events.dropna(subset=["gt_id"]).groupby(
        ["class_id", "gt_id"]
    ):
        matched = table[table.kind.isin(["TP", "IDS"])]
        tracks.append(
            dict(
                class_id=int(class_id),
                gt_id=int(gid),
                observations=len(table),
                matches=len(matched),
                coverage=len(matched) / len(table),
                prediction_ids=matched.pred_id.nunique(),
                IDS=int(table.kind.eq("IDS").sum()),
                median_iou=matched.iou.median(),
            )
        )
    return dict(
        raw_gt=raw_gt,
        protocol_version=protocol_version,
        gt=gt,
        pred=pred,
        events=events,
        frames=stats,
        tracks=pd.DataFrame(tracks),
        excluded=excluded,
        regions=regions,
    )


def window_scores(stats, window=125, stride=25, min_gt=30, min_coverage=0.8):
    """Rank full-length windows by errors/GT; these are not HOTA scores."""
    result = []
    for start in range(1, len(stats) - window + 2, stride):
        table = stats.loc[start : start + window - 1]
        totals = table[["TP", "FP", "FN", "IDS", "GT"]].sum()
        coverage = table.scored.mean()
        if totals.GT < min_gt or coverage < min_coverage:
            continue
        result.append(
            dict(
                start=start,
                end=start + window - 1,
                annotation_coverage=coverage,
                **totals.to_dict(),
                error_rate=(totals.FP + totals.FN + totals.IDS) / totals.GT,
                local_f1=2 * totals.TP / max(1, 2 * totals.TP + totals.FP + totals.FN),
            )
        )
    return pd.DataFrame(result)


def draw_frame(path, result, frame, title="", class_id=None):
    """Draw GT, matched tracks and error evidence on the original image."""
    image = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    draw.font = ImageFont.load_default(size=14)
    for x, y, w, h in result["regions"]:
        draw.rectangle((x, y, x + w, y + h), fill=(150, 150, 150, 55))
    scored = bool(result["frames"].loc[frame, "scored"])
    event = result["events"]
    event = event[event.frame_id.eq(frame)]
    gt = result["gt"][result["gt"].frame_id.eq(frame)]
    pred = result["pred"][result["pred"].frame_id.eq(frame)]
    if class_id is not None:
        gt, pred = gt[gt.class_id.eq(class_id)], pred[pred.class_id.eq(class_id)]
    for row in gt.itertuples():
        matches = event[event.gt_id.eq(row.track_id) & event.class_id.eq(row.class_id)]
        missed = matches.kind.eq("FN").any()
        color = COLORS["FN" if missed else "GT"]
        draw.rectangle(
            (row.x, row.y, row.x + row.w, row.y + row.h), outline=color, width=2
        )
        draw.text((row.x, max(48, row.y - 12)), f"G{row.track_id}", fill=color)
    for index, row in pred.iterrows():
        matches = event[event.pred_index.eq(index)]
        kind = matches.iloc[0].kind if len(matches) else "excluded"
        color = COLORS[kind]
        label = f"P{int(row.track_id)} {CLASS_NAMES[int(row.class_id)]}"
        if kind == "IDS":
            label += (
                f" IDS {int(matches.iloc[0].previous_pred_id)}->{int(row.track_id)}"
            )
        draw.rectangle(
            (row.x, row.y, row.x + row.w, row.y + row.h), outline=color, width=3
        )
        position = (max(0, row.x), min(image.height - 18, max(48, row.y + row.h)))
        draw.rectangle(draw.textbbox(position, label), fill=(0, 0, 0, 210))
        draw.text(position, label, fill=color)
    draw.rectangle((0, 0, image.width, 46), fill=(0, 0, 0, 235))
    status = "SCORED" if scored else "UNANNOTATED - NOT SCORED"
    draw.text((8, 5), f"{title} | frame {frame} | {status}", fill="white")
    draw.text(
        (8, 24),
        "GT blue | match green | FP red | FN orange | IDS magenta | excluded gray",
        fill="white",
    )
    return image


def comparison_frame(dataset, sequence, results, frame, class_id=None):
    """Return a baseline/best comparison with shared frame and image scale."""
    path = find_sequence_images(dataset, sequence)[int(frame)]
    panels = [
        draw_frame(path, value, int(frame), f"{sequence} / {name}", class_id)
        for name, value in results.items()
    ]
    canvas = Image.new("RGB", (sum(p.width for p in panels), panels[0].height))
    left = 0
    for panel in panels:
        canvas.paste(panel, (left, 0))
        left += panel.width
    return canvas


def export_clip(
    dataset, sequence, results, start, end, destination, fps=10, width=1280
):
    """Save a browser-friendly GIF; sample frames at their original timing."""
    frames = []
    for frame in range(int(start), int(end) + 1, max(1, round(25 / fps))):
        panel = comparison_frame(dataset, sequence, results, frame)
        panel.thumbnail((width, width))
        frames.append(panel)
    step = max(1, round(25 / fps))
    frames[0].save(
        destination,
        save_all=True,
        append_images=frames[1:],
        duration=round(1000 * step / 25),
        loop=0,
    )
    return destination
