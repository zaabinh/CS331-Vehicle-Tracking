"""Portable MOT files retain class zero and empty XML frames after masking."""

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.export_tracking_bundle import export_bundle
from src.tracking.common import sha256_file
from src.tracking.evaluation import GT_COLUMNS, PRED_COLUMNS, evaluate_run


class BundleTests(unittest.TestCase):
    def test_export_compacts_only_unannotated_frames_and_preserves_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset, run, bundle = root / "dataset", root / "run", root / "bundle"
            for path in [
                dataset / "canonical",
                dataset / "metadata",
                run / "tracks",
                run / "mot",
            ]:
                path.mkdir(parents=True)
            pd.DataFrame(
                [
                    dict(
                        sequence="S",
                        num_frames=4,
                        width=200,
                        height=100,
                        fps=25,
                        split="val",
                    )
                ]
            ).to_csv(dataset / "metadata/sequences.csv", index=False)
            pd.DataFrame([dict(sequence="S", frame_id=f) for f in [1, 2, 4]]).to_csv(
                dataset / "metadata/annotated_frames.csv", index=False
            )
            pd.DataFrame([dict(sequence="S", x=100, y=0, w=50, h=50)]).to_csv(
                dataset / "canonical/ignored_regions.csv", index=False
            )
            gt, pred = [], []
            for frame in [1, 4]:
                for track, x in [(1, 10), (2, 110)]:
                    box = dict(
                        frame_id=frame,
                        track_id=track,
                        class_id=0,
                        x=x,
                        y=10,
                        w=20,
                        h=20,
                    )
                    gt.append(dict(sequence="S", **box))
                    pred.append(dict(**box, confidence=0.9))
            pred.append(
                dict(
                    frame_id=3,
                    track_id=9,
                    class_id=0,
                    x=20,
                    y=30,
                    w=20,
                    h=20,
                    confidence=0.9,
                )
            )
            gt_path = dataset / "canonical/annotations.csv"
            pd.DataFrame(gt, columns=GT_COLUMNS).to_csv(gt_path, index=False)
            pd.DataFrame(pred, columns=PRED_COLUMNS).to_csv(
                run / "tracks/S.csv", index=False
            )
            (run / "mot/S.txt").write_text("original-timeline-placeholder")
            (run / "run_manifest.json").write_text(
                json.dumps(dict(status="complete", sequences=["S"]))
            )
            expected = evaluate_run(dataset, run)
            before = sha256_file(gt_path)
            counts = export_bundle(dataset, {"val": run}, bundle)
            self.assertEqual(counts[0]["frames"], 3)
            self.assertEqual(counts[0]["gt"], 2)
            self.assertEqual(counts[0]["pred"], 2)
            path = bundle / "mot_challenge/gt/UADETRAC-car-val/S/gt/gt.txt"
            exported = pd.read_csv(path, header=None)
            self.assertEqual(exported[0].tolist(), [1, 3])
            self.assertEqual(exported[7].tolist(), [1, 1])
            mapping = pd.read_csv(bundle / "original_to_evaluation_frames.csv")
            self.assertEqual(mapping.frame_id.tolist(), [1, 2, 4])
            result = evaluate_run(bundle / "evaluation_input", bundle / "runs/val")
            self.assertEqual(result["HOTA"], expected["HOTA"])
            self.assertEqual(result["FN"], 0)
            self.assertEqual(sha256_file(gt_path), before)
            with self.assertRaises(FileExistsError):
                export_bundle(dataset, {"val": run}, bundle)


if __name__ == "__main__":
    unittest.main()
