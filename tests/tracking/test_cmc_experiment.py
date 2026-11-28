"""Guard the CMC-only comparison and its val-only selection rule."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from scripts.compare_tracking_cmc import rank, upstream_config
from src.tracking.common import CLASS_NAMES, resolve_config
from src.tracking.detections import SOURCE_COLUMNS
from src.tracking.gmc import IDENTITY
from src.tracking.runner import run_tracking


class CmcExperimentTests(unittest.TestCase):
    def test_upstream_counterpart_preserves_numeric_parameters(self):
        v3 = resolve_config(
            {
                "class_mode": "shared_vote",
                "boundary_sigma": 10,
                "hp": True,
                "a1": 40,
                "a2": 40,
                "wx": 10,
                "wy": 10,
                "vmax": 15,
            }
        )
        original = upstream_config(v3)
        self.assertEqual(
            {k for k in v3 if v3[k] != original[k]},
            {"class_mode", "boundary_sigma", "cmc"},
        )
        self.assertTrue(original["hp"])
        first = dict(HOTA=80, IDF1=88, IDS=50, runtime_seconds=100)
        second = {**first, "HOTA": 79, "IDF1": 99}
        self.assertLess(rank(first), rank(second))
        self.assertLess(rank({**first, "IDS": 49}), rank(first))

    def test_identity_cmc_matches_v3_without_cmc(self):
        """CMC must compose with shared voting, clipped boxes and empty frames."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset, detections = root / "dataset", root / "detections"
            (dataset / "metadata").mkdir(parents=True)
            detections.mkdir()
            images = dataset / "tracking/S1/img1"
            images.mkdir(parents=True)
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
            for frame in range(1, 9):
                (images / f"{frame:06d}.jpg").write_bytes(
                    b"fingerprint fixture"
                )
            rows = [
                [f, c, 0.9, 0, 80, 20, 100]
                for f, c in zip([2, 3, 4, 6, 7], [0, 0, 2, 0, 0])
            ]
            pd.DataFrame(rows, columns=SOURCE_COLUMNS).to_csv(
                detections / "S1.csv", index=False
            )
            (detections / "detection_handoff_config.json").write_text(
                json.dumps(
                    {
                        "conf_threshold": 0.05,
                        "class_mapping": {
                            str(k): v for k, v in CLASS_NAMES.items()
                        },
                    }
                )
            )
            camera = dict(
                A_ground_to_image=np.eye(3).tolist(),
                image_size=[200, 100],
                status="reviewed",
            )
            config = dict(class_mode="shared_vote", boundary_sigma=10, hp=True)
            transforms = np.repeat(IDENTITY[None], 9, axis=0)
            with patch(
                "src.tracking.runner.resolve_calibration", return_value=camera
            ), patch(
                "src.tracking.gmc.load_or_estimate_gmc",
                return_value=(transforms, {"failures": []}),
            ):
                for enabled in (False, True):
                    run_tracking(
                        dataset,
                        detections,
                        root / "camera.json",
                        root / str(enabled),
                        {**config, "cmc": enabled},
                        fix_coasting=True,
                    )
            left = pd.read_csv(root / "False/tracks/S1.csv")
            right = pd.read_csv(root / "True/tracks/S1.csv")
            pd.testing.assert_frame_equal(left, right)
            self.assertEqual(left.frame_id.tolist(), [2, 3, 4, 6, 7])
            self.assertEqual(left.class_id.unique().tolist(), [0])


if __name__ == "__main__":
    unittest.main()
