"""Regression for symmetric evaluation-only ignore filtering."""

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.tracking.evaluation import GT_COLUMNS, PRED_COLUMNS, evaluate_run


class IgnoreProtocolTest(unittest.TestCase):
    def test_ignored_gt_does_not_create_false_negative(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "metadata").mkdir()
            (root / "canonical").mkdir()
            (root / "run/tracks").mkdir(parents=True)
            pd.DataFrame(
                [dict(sequence="S", num_frames=1, width=100, height=100)]
            ).to_csv(root / "metadata/sequences.csv", index=False)
            pd.DataFrame([dict(sequence="S", frame_id=1)]).to_csv(
                root / "metadata/annotated_frames.csv", index=False
            )
            gt = dict(
                sequence="S", frame_id=1, track_id=0, class_id=0, x=10, y=10, w=20, h=20
            )
            source = root / "canonical/annotations.csv"
            pd.DataFrame([gt], columns=GT_COLUMNS).to_csv(source, index=False)
            original = source.read_bytes()
            pd.DataFrame([dict(sequence="S", x=0, y=0, w=50, h=50)]).to_csv(
                root / "canonical/ignored_regions.csv", index=False
            )
            pd.DataFrame([{**gt, "confidence": 0.9}], columns=PRED_COLUMNS).to_csv(
                root / "run/tracks/S.csv", index=False
            )
            result = evaluate_run(root, root / "run", sequences=["S"])
            self.assertEqual(result["FN"], 0)
            self.assertEqual(result["FP"], 0)
            self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
