"""Candidate counts must not reuse a detection for two GT observations."""

import unittest

import numpy as np

from src.analysis.detection_audit import assigned_rows


class DetectionAuditTest(unittest.TestCase):
    def test_one_detection_cannot_cover_two_gt(self):
        similarity = np.array([[0.9], [0.8]])
        self.assertEqual(assigned_rows(similarity, np.ones_like(similarity, bool)), {0})
        self.assertEqual(
            assigned_rows(similarity, np.zeros_like(similarity, bool)), set()
        )
