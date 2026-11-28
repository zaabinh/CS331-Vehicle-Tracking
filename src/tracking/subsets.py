"""Aggregate saved TrackEval statistics without averaging sequence scores."""

import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from .common import CLASS_NAMES, UPSTREAM, sha256_file
from .evaluation import _combine, _metrics, _summary


def difficulty_lists(directory, sequences):
    """Deduplicate source lists and expose missing membership instead of guessing."""
    directory, expected = Path(directory), set(sequences)
    groups, audit, seen = {}, {}, set()
    for level in ("easy", "medium", "hard"):
        path = directory / f"testlist-{level}.txt"
        names = path.read_text(encoding="utf-8-sig").split()
        unique = set(names)
        if unique - expected or unique & seen:
            raise ValueError(f"Invalid or overlapping difficulty membership: {path}")
        groups[level] = sorted(unique)
        audit[level] = dict(
            source=str(path),
            sha256=sha256_file(path),
            lines=len(names),
            unique=len(unique),
            duplicates={s: n for s, n in Counter(names).items() if n > 1},
        )
        seen.update(unique)
    if expected - seen:
        groups["unassigned"] = sorted(expected - seen)
    return groups, audit


def aggregate_saved(evaluation_dir, sequences, upstream=UPSTREAM):
    """Combine per-sequence metric internals, retaining HOTA threshold arrays."""
    directory = Path(evaluation_dir)
    selected = sorted(sequences)
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("Expected a nonempty unique subset")

    def restore(value):
        if isinstance(value, list):
            return np.asarray(value)
        if isinstance(value, dict):
            return {key: restore(item) for key, item in value.items()}
        return value

    source = directory / "metrics.json"
    results = restore(json.loads(source.read_text()))
    if set(selected) - set(results):
        raise ValueError("Subset contains sequences absent from saved metrics")
    metrics = _metrics(upstream)
    combined = {
        name: {
            key: metric.combine_sequences({s: results[s][name][key] for s in selected})
            for key, metric in metrics.items()
        }
        for name in CLASS_NAMES.values()
    }
    active = {
        name: value
        for name, value in combined.items()
        if sum(value["Identity"][key] for key in ("IDTP", "IDFP", "IDFN")) > 0
    }
    table = (
        pd.read_csv(directory / "per_sequence.csv").set_index("sequence").loc[selected]
    )
    return dict(
        **_summary(_combine(metrics, combined)),
        macro_average=_summary(_combine(metrics, active or combined, macro=True)),
        per_class=[
            dict(class_id=i, class_name=name, **_summary(combined[name]))
            for i, name in CLASS_NAMES.items()
        ],
        sequences=selected,
        sequence_count=len(selected),
        frames=int(table.num_frames.sum()),
        evaluated_frames=int(table.evaluated_frames.sum()),
        evaluated_gt_count=int(table.evaluated_gt_count.sum()),
        metrics_sha256=sha256_file(source),
        aggregation="TrackEval combine_sequences then combine_classes_det_averaged",
    )
