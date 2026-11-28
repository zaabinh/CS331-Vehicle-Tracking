"""Check clipped-footpoint noise and class-flicker recovery without new boxes."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.tracking.common import CLASS_NAMES, resolve_config, sha256_file
from src.tracking.detections import GroundMapper, SOURCE_COLUMNS
from src.tracking.runner import run_tracking


class RefinementTests(unittest.TestCase):
    def test_boundary_noise_changes_only_the_censored_axis(self):
        mapper = GroundMapper(np.eye(3))
        box = [20, 80, 20, 20]
        position, original = mapper.mapto(box)
        new_position, adjusted = mapper.mapto(box, [200, 100], 10)
        np.testing.assert_array_equal(position, new_position)
        self.assertEqual(original[0, 0], adjusted[0, 0])
        self.assertEqual(adjusted[1, 1], 100)
        interior = [20, 20, 20, 20]
        np.testing.assert_array_equal(
            mapper.mapto(interior)[1], mapper.mapto(interior, [200, 100], 10)[1]
        )
        self.assertEqual(mapper.mapto([0, 20, 20, 20], [200, 100], 10)[1][0, 0], 100)
        with self.assertRaises(ValueError):
            mapper.mapto(box, boundary_sigma=10)
        for settings in ({"boundary_sigma": -1}, {"class_mode": "unknown"}):
            with self.assertRaises(ValueError):
                resolve_config(settings)

    def test_shared_vote_keeps_identity_across_class_flicker_and_empty_frames(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset, detections = root / "dataset", root / "detections"
            (dataset / "metadata").mkdir(parents=True)
            detections.mkdir()
            pd.DataFrame(
                [
                    dict(
                        sequence="S1",
                        num_frames=8,
                        width=200,
                        height=100,
                        fps=25,
                        split="val",
                    )
                ]
            ).to_csv(dataset / "metadata/sequences.csv", index=False)
            rows = []
            for frame, category in zip(range(2, 8), [0, 0, 2, 0, 0, 0]):
                rows.extend(
                    [
                        [frame, category, 0.9, 20, 20, 40, 40],
                        [frame, 1, 0.9, 140, 20, 160, 40],
                    ]
                )
            source = detections / "S1.csv"
            pd.DataFrame(rows, columns=SOURCE_COLUMNS).to_csv(source, index=False)
            before = sha256_file(source)
            (detections / "detection_handoff_config.json").write_text(
                json.dumps(
                    dict(
                        conf_threshold=0.05,
                        class_mapping={str(k): v for k, v in CLASS_NAMES.items()},
                    )
                )
            )
            camera = dict(
                A_ground_to_image=np.eye(3).tolist(),
                image_size=[200, 100],
                status="reviewed",
            )
            with patch("src.tracking.runner.resolve_calibration", return_value=camera):
                manifest = run_tracking(
                    dataset,
                    detections,
                    root / "camera.json",
                    root / "run",
                    {"hp": True, "class_mode": "shared_vote"},
                    fix_coasting=True,
                )
                resumed = run_tracking(
                    dataset,
                    detections,
                    root / "camera.json",
                    root / "run",
                    {"hp": True, "class_mode": "shared_vote"},
                    fix_coasting=True,
                    resume=True,
                )
                with self.assertRaisesRegex(ValueError, "different"):
                    run_tracking(
                        dataset,
                        detections,
                        root / "camera.json",
                        root / "run",
                        {"hp": True, "class_mode": "shared_vote", "boundary_sigma": 10},
                        fix_coasting=True,
                        resume=True,
                    )
            table = pd.read_csv(root / "run/tracks/S1.csv")
            self.assertEqual(table.track_id.nunique(), 2)
            self.assertEqual(set(table.class_id), {0, 1})
            self.assertTrue(table.groupby("track_id").class_id.nunique().eq(1).all())
            self.assertEqual(
                table.loc[table.frame_id.eq(4), "class_id"].tolist(), [0, 1]
            )
            self.assertEqual((table.frame_id.min(), table.frame_id.max()), (2, 7))
            self.assertEqual(manifest["sequence_reports"]["S1"]["frames_processed"], 8)
            self.assertEqual(manifest, resumed)
            self.assertEqual(sha256_file(source), before)
            self.assertEqual(len(table), len(rows))
            np.testing.assert_array_equal(
                table[["x", "y", "w", "h"]].to_numpy(),
                np.tile([[20, 20, 20, 20], [140, 20, 20, 20]], (6, 1)),
            )


if __name__ == "__main__":
    unittest.main()
