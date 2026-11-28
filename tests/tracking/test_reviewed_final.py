"""Check frozen camera context and val-only selection for the final experiment."""

import tempfile
import os
import unittest
from pathlib import Path

from scripts.compare_tracking_cmc import rank
from scripts.finalize_reviewed_tracking import candidate_configs, lock_json
from src.tracking.common import (
    fingerprint,
    image_fingerprint,
    resolve_config,
    sha256_file,
)


class ReviewedFinalTests(unittest.TestCase):
    def test_parallel_hash_preserves_order_and_reads_changed_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            images = {}
            for frame in (10, 2, 1):
                path = Path(temporary) / f"{frame}.jpg"
                path.write_bytes(bytes([frame]) * 100)
                images[frame] = path
            expected = fingerprint(
                [sha256_file(images[f]) for f in (1, 2, 10)]
            )
            self.assertEqual(image_fingerprint(images), expected)
            # Same size/timestamp must not hide a changed input from the cache key.
            path = images[2]
            stat = path.stat()
            path.write_bytes(b"x" * 100)
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            self.assertNotEqual(image_fingerprint(images), expected)

    def test_candidates_do_not_retune_numeric_parameters(self):
        base = resolve_config(
            {
                "cmc": True,
                "hp": True,
                "class_mode": "shared_vote",
                "boundary_sigma": 10,
                "a1": 40,
                "a2": 40,
                "wx": 10,
                "wy": 10,
                "vmax": 15,
            }
        )
        candidates = candidate_configs(base)
        off, fix = candidates["v3_no_cmc"]
        self.assertTrue(fix)
        self.assertEqual({k for k in base if base[k] != off[k]}, {"cmc"})
        original, fix = candidates["upstream_hp_cmc"]
        self.assertFalse(fix)
        for key in ("a1", "a2", "wx", "wy", "vmax", "conf", "high_score"):
            self.assertEqual(base[key], original[key])
        v3 = dict(HOTA=80, IDF1=99, IDS=1, runtime_seconds=1)
        original_score = dict(HOTA=81, IDF1=80, IDS=100, runtime_seconds=10)
        self.assertEqual(min([v3, original_score], key=rank), original_score)

    def test_frozen_context_rejects_changed_camera_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "lock.json"
            context = dict(camera_sha256="reviewed_v2", selected_on="val_only")
            lock_json(path, context)
            lock_json(path, context)
            with self.assertRaisesRegex(
                ValueError, "Frozen experiment changed"
            ):
                lock_json(path, {**context, "camera_sha256": "edited_v3"})


if __name__ == "__main__":
    unittest.main()
