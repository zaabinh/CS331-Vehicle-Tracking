"""Saved-metric subsets must preserve global aggregation and expose source defects."""

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.tracking.evaluation import GT_COLUMNS, PRED_COLUMNS, evaluate_run
from src.tracking.subsets import aggregate_saved, difficulty_lists


class SubsetTests(unittest.TestCase):
    def test_duplicate_difficulty_is_not_silently_filled(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for level, text in [("easy", "A"), ("medium", "B\nB"), ("hard", "C")]:
                (root / f"testlist-{level}.txt").write_text(text)
            groups, audit = difficulty_lists(root, ["A", "B", "C", "D"])
            self.assertEqual(groups["medium"], ["B"])
            self.assertEqual(groups["unassigned"], ["D"])
            self.assertEqual(audit["medium"]["duplicates"], {"B": 2})

    def test_combining_all_saved_results_reproduces_evaluation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset, run = root / "dataset", root / "run"
            for path in [dataset / "metadata", dataset / "canonical", run / "tracks"]:
                path.mkdir(parents=True)
            metadata, gt, annotated = [], [], []
            for sequence, count in [("A", 1), ("B", 3)]:
                metadata.append(
                    dict(
                        sequence=sequence,
                        num_frames=2,
                        width=200,
                        height=100,
                        fps=25,
                        split="test",
                    )
                )
                rows = []
                for frame in (1, 2):
                    annotated.append(dict(sequence=sequence, frame_id=frame))
                    for track in range(1, count + 1):
                        record = dict(
                            frame_id=frame,
                            track_id=track,
                            class_id=0,
                            x=track * 30,
                            y=20,
                            w=20,
                            h=20,
                        )
                        gt.append(dict(sequence=sequence, **record))
                        rows.append(
                            dict(
                                **{**record, "class_id": 0 if sequence == "A" else 2},
                                confidence=0.9,
                            )
                        )
                pd.DataFrame(rows, columns=PRED_COLUMNS).to_csv(
                    run / "tracks" / f"{sequence}.csv", index=False
                )
            pd.DataFrame(metadata).to_csv(
                dataset / "metadata/sequences.csv", index=False
            )
            pd.DataFrame(annotated).to_csv(
                dataset / "metadata/annotated_frames.csv", index=False
            )
            pd.DataFrame(gt, columns=GT_COLUMNS).to_csv(
                dataset / "canonical/annotations.csv", index=False
            )
            pd.DataFrame(columns=["sequence", "x", "y", "w", "h"]).to_csv(
                dataset / "canonical/ignored_regions.csv", index=False
            )
            (run / "run_manifest.json").write_text(
                json.dumps(dict(sequences=["A", "B"]))
            )
            result = evaluate_run(dataset, run)
            aggregate = aggregate_saved(run / "evaluation", ["A", "B"])
            for metric in [
                "HOTA",
                "IDF1",
                "MOTA",
                "AssA",
                "DetA",
                "FP",
                "FN",
                "IDS",
                "Frag",
            ]:
                self.assertAlmostEqual(result[metric], aggregate[metric], places=10)
            perfect = aggregate_saved(run / "evaluation", ["A"])
            self.assertEqual(perfect["HOTA"], 100)
            self.assertEqual(perfect["FN"], 0)
            self.assertNotAlmostEqual(aggregate["HOTA"], 50)


if __name__ == "__main__":
    unittest.main()
