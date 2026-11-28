"""A confirmed track must expire on empty frames once max_age is reached."""

import unittest
from types import SimpleNamespace

import numpy as np

from src.tracking.common import UPSTREAM
from src.tracking.lifecycle import update_tracker
from src.tracking.runner import _upstream


class LifecycleTest(unittest.TestCase):
    def test_empty_frames_expire_tracks_without_changing_initial_matches(self):
        tracker_type, kalman = _upstream(UPSTREAM)
        survivors = []
        for enabled in (False, True):
            kalman.count = 1
            tracker = tracker_type(
                40, 40, 10, 10, 15, 3, 25, "UA-DETRAC", 0.6, False, None
            )
            for frame in range(1, 4):
                detection = SimpleNamespace(
                    conf=0.9,
                    y=np.zeros((2, 1)),
                    R=np.eye(2),
                    bb_width=20,
                    bb_height=20,
                    track_id=0,
                )
                update_tracker(tracker, [detection], frame, enabled)
            self.assertEqual(len(tracker.confirmed_idx), 1)
            self.assertEqual(tracker.trackers[0].id, 1)
            for frame in range(4, 10):
                update_tracker(tracker, [], frame, enabled)
            survivors.append(len(tracker.trackers))
        self.assertEqual(survivors, [1, 0])
