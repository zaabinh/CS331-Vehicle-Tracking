"""Check diagnostic frame policies and identity evidence."""

import unittest

import pandas as pd

from src.analysis.tracking_cases import diagnose, window_scores


class TrackingCasesTest(unittest.TestCase):
    """Protect class zero, unknown frames, empty frames and ignore policy."""

    def test_identity_across_unknown_and_empty_frames(self):
        gt = pd.DataFrame(
            [
                [1, 0, 0, 10, 10, 30, 30],
                [3, 0, 0, 10, 10, 30, 30],
                [5, 0, 0, 10, 10, 30, 30],
            ],
            columns=["frame_id", "track_id", "class_id", "x", "y", "w", "h"],
        )
        pred = pd.DataFrame(
            [
                [1, 0, 0, 10, 10, 30, 30],
                [2, 99, 0, 10, 10, 30, 30],
                [3, 1, 0, 10, 10, 30, 30],
                [4, 1, 0, 10, 10, 30, 30],
                [5, 1, 0, 10, 10, 30, 30],
            ],
            columns=gt.columns,
        )
        result = diagnose(gt, pred, [1, 3, 4, 5], 5, [], 100, 100)
        totals = result["frames"][["FP", "FN", "IDS", "Frag"]].sum()
        self.assertEqual(totals.to_dict(), dict(FP=1, FN=0, IDS=1, Frag=1))
        self.assertTrue(pd.isna(result["frames"].loc[2, "FP"]))
        switch = result["events"].query("kind == 'IDS'").iloc[0]
        self.assertEqual(switch.previous_pred_id, 0)
        self.assertEqual(switch.previous_match_frame, 1)
        self.assertTrue(window_scores(result["frames"], 5, 1, 1, 0.8).shape[0] == 1)

    def test_class_mismatch_and_ignore_union(self):
        columns = ["frame_id", "track_id", "class_id", "x", "y", "w", "h"]
        gt = pd.DataFrame([[1, 0, 0, 0, 0, 10, 10]], columns=columns)
        pred = pd.DataFrame([[1, 3, 1, 0, 0, 10, 10]], columns=columns)
        result = diagnose(gt, pred, [1], 1, [], 100, 100)
        self.assertEqual(set(result["events"].kind), {"FP", "FN"})
        # Two overlapping masks cover only 40%, not 80%.
        result = diagnose(gt, pred, [1], 1, [[0, 0, 4, 10]] * 2, 100, 100)
        self.assertEqual(result["excluded"], set())
        result = diagnose(gt, pred, [1], 1, [[0, 0, 6, 10]], 100, 100)
        self.assertEqual(result["excluded"], {0})
        self.assertTrue(result["events"].empty)
        legacy = diagnose(
            gt, pred, [1], 1, [[0, 0, 6, 10]], 100, 100, protocol_version=1
        )
        self.assertEqual(legacy["events"].kind.tolist(), ["FN"])


if __name__ == "__main__":
    unittest.main()
