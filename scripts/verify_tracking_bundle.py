"""Check both portable CSV evaluation and class-separated MOT against saved scores."""

import argparse
import configparser
import json
import logging
import os
import sys
from pathlib import Path

for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from src.tracking.common import CLASS_NAMES, UPSTREAM, write_json
from src.tracking.evaluation import _metric_data, _metrics, _summary, evaluate_run

FIELDS = ["HOTA", "IDF1", "MOTA", "AssA", "DetA", "IDS", "FP", "FN", "Frag"]


def read_mot(path, columns):
    """Use the float conversion expected by the standard MOT text loader."""
    rows = (
        np.loadtxt(path, delimiter=",", ndmin=2)
        if path.stat().st_size
        else np.empty((0, columns))
    )
    table = pd.DataFrame(
        rows[:, :6], columns=["frame_id", "track_id", "x", "y", "w", "h"]
    )
    table[["frame_id", "track_id"]] = table[["frame_id", "track_id"]].astype(int)
    return table, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle",
        type=Path,
        default=ROOT / "outputs/tracking/delivery_v3/trackeval_bundle",
    )
    args = parser.parse_args()
    bundle = args.bundle
    destination = bundle.parent / "bundle_verification"
    metrics, checks = _metrics(UPSTREAM), {}
    for split in ("val", "test"):
        run = bundle / "runs" / split
        expected = json.loads((run / "evaluation/summary.json").read_text())
        actual = evaluate_run(
            bundle / "evaluation_input", run, output_dir=destination / split
        )
        for key in FIELDS:
            if abs(actual[key] - expected[key]) > 1e-9:
                raise AssertionError(f"Portable CSV score differs: {split}/{key}")
        checks[split + "/csv"] = True
        expected_classes = pd.read_csv(
            run / "evaluation/per_sequence_class.csv"
        ).set_index(["sequence", "class_id"])
        for sequence in expected["sequences"]:
            logging.info("Verify MOT %s/%s", split, sequence)
            for class_id, name in CLASS_NAMES.items():
                benchmark = f"UADETRAC-{name}-{split}"
                gt_dir = bundle / "mot_challenge/gt" / benchmark / sequence
                config = configparser.ConfigParser()
                config.read(gt_dir / "seqinfo.ini")
                length = config.getint("Sequence", "seqLength")
                gt, rows = read_mot(gt_dir / "gt/gt.txt", 9)
                if len(rows) and not np.all(rows[:, 7] == 1):
                    raise AssertionError("Wrong stock-loader placeholder class")
                pred, _ = read_mot(
                    bundle
                    / "mot_challenge/trackers"
                    / benchmark
                    / "UCMCTrack/data"
                    / f"{sequence}.txt",
                    10,
                )
                data = _metric_data(gt, pred, list(range(1, length + 1)))
                actual_class = _summary(
                    {key: metric.eval_sequence(data) for key, metric in metrics.items()}
                )
                reference = expected_classes.loc[(sequence, class_id)]
                for key in FIELDS:
                    if abs(actual_class[key] - reference[key]) > 1e-8:
                        raise AssertionError(
                            f"MOT score differs: {split}/{sequence}/{name}/{key}"
                        )
                checks[f"{split}/{sequence}/{name}/mot"] = True
    write_json(
        bundle.parent / "bundle_verification.json",
        dict(
            all_match=all(checks.values()),
            checks=checks,
            class_sequence_checks=sum(k.endswith("/mot") for k in checks),
            csv_aggregate_checks=sum(k.endswith("/csv") for k in checks),
        ),
    )
    print(
        "Portable evaluation verified: 2 aggregates and 208 class/sequence MOT checks"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[VERIFY] %(message)s")
    main()
