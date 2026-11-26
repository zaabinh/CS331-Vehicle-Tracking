"""Geometry, timeline, metric and provenance regressions for UA-DETRAC tracking."""

import ast
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pandas as pd
from PIL import Image

from src.tracking.calibration import (
    adjust_krt,
    confirm_camera_group,
    export_krt,
    fit_homography,
    fit_krt,
    focal_from_vanishing_lines,
    prepare_registry,
    project_points,
    read_registry,
    resolve_calibration,
    save_calibration,
    save_group_calibration,
)
from src.tracking.common import (
    BASELINE,
    CLASS_NAMES,
    ROOT,
    UPSTREAM,
    sha256_file,
)
from src.tracking.detections import (
    GroundMapper,
    normalize_detections,
    load_cached_detections,
    SOURCE_COLUMNS,
)
from src.tracking.evaluation import GT_COLUMNS, PRED_COLUMNS, evaluate_run
from src.tracking.gmc import (
    IDENTITY,
    estimate_affine,
    foreground_mask,
    load_or_estimate_gmc,
)
from src.tracking.runner import _Provider, interpolate_tracks, run_tracking
from src.tracking.tuning import SMOKE_SEQUENCES, run_smoke, tune


def box(frame=1, track=1, category=0, x=20, y=20, w=20, h=20):
    return dict(
        frame_id=frame,
        track_id=track,
        class_id=category,
        x=x,
        y=y,
        w=w,
        h=h,
        confidence=0.9,
    )


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset, self.det, self.run = [
            self.root / p for p in ["dataset", "detections", "run"]
        ]
        for folder in [
            self.dataset / "metadata",
            self.dataset / "canonical",
            self.det,
        ]:
            folder.mkdir(parents=True)
        self.seq = "S1"
        self.points = [[0, 0], [200, 0], [200, 100], [0, 100]]

    def fixture(
        self, gt=None, pred=None, annotated=None, frames=6, images=False
    ):
        pd.DataFrame(
            [
                dict(
                    sequence=self.seq,
                    num_frames=frames,
                    width=200,
                    height=100,
                    fps=25,
                    split="val",
                )
            ]
        ).to_csv(self.dataset / "metadata/sequences.csv", index=False)
        annotated = (
            list(range(1, frames + 1)) if annotated is None else annotated
        )
        pd.DataFrame(
            [dict(sequence=self.seq, frame_id=f) for f in annotated]
        ).to_csv(self.dataset / "metadata/annotated_frames.csv", index=False)
        pd.DataFrame(
            [
                dict(
                    sequence=self.seq,
                    **{k: v for k, v in r.items() if k in GT_COLUMNS},
                )
                for r in (gt or [])
            ],
            columns=GT_COLUMNS,
        ).to_csv(self.dataset / "canonical/annotations.csv", index=False)
        pd.DataFrame(columns=["sequence", "x", "y", "w", "h"]).to_csv(
            self.dataset / "canonical/ignored_regions.csv", index=False
        )
        if pred is not None:
            (self.run / "tracks").mkdir(parents=True, exist_ok=True)
            pd.DataFrame(pred, columns=PRED_COLUMNS).to_csv(
                self.run / "tracks/S1.csv", index=False
            )
            (self.run / "run_manifest.json").write_text(
                json.dumps(dict(sequences=[self.seq], runtime_seconds=1.0))
            )
        if images:
            folder = self.dataset / "tracking/S1/img1"
            folder.mkdir(parents=True, exist_ok=True)
            for f in range(1, frames + 1):
                Image.new("RGB", (200, 100), "gray").save(
                    folder / f"{f:06d}.jpg"
                )
        return self.dataset

    def camera(self, reviewed=True):
        self.fixture(images=True)
        folder = self.root / "cameras"
        prepare_registry(self.dataset, folder)
        registry = folder / "registry.json"
        record = fit_homography(self.points, 20, 10, [200, 100])
        record["evidence"] = "Synthetic control rectangle; known dimensions"
        save_calibration(
            registry, self.seq, record, reviewed, "Synthetic test"
        )
        return registry

    def metric(self, gt, pred, **kwargs):
        self.fixture(gt, pred, **kwargs)
        return evaluate_run(self.dataset, self.run)

    def test_homography_roundtrip_and_degenerate_points(self):
        record = fit_homography(self.points, 20, 10, [200, 100])
        ground = project_points(record["H_image_to_ground"], self.points)
        np.testing.assert_allclose(ground, record["ground_points"], atol=1e-6)
        np.testing.assert_allclose(
            project_points(record["A_ground_to_image"], ground),
            self.points,
            atol=1e-6,
        )
        for points, length in [
            ([[0, 0], [1, 1], [2, 2], [3, 3]], 10),
            (self.points, 0),
        ]:
            with self.assertRaises(ValueError):
                fit_homography(points, 20, length)

    def test_krt_positive_depth_and_mm_export(self):
        k = np.array([[1200, 0, 640], [0, 1200, 360], [0, 0, 1.0]])
        r = cv2.Rodrigues(np.array([0.5, -0.1, 0.05]))[0]
        t = np.array([-1.0, -2.0, 18.0])
        ground = [[0, 0], [3.5, 0], [3.5, 12], [0, 12]]
        points = project_points(k @ np.column_stack([r[:, :2], t]), ground)
        record = fit_krt(points, 3.5, 12, [1280, 720], focal=1200)
        self.assertLess(record["reprojection_rmse_px"], 1e-5)
        path = self.root / "camera.txt"
        export_krt(record, path)
        lines = path.read_text().splitlines()
        np.testing.assert_allclose(
            np.fromstring(
                lines[lines.index("TranslationVectors") + 1], sep=" "
            ),
            np.array(record["T"]) * 1000,
        )
        with self.assertRaises(ValueError):
            adjust_krt(record, translation_m=[0, 0, -1000])

    def test_covariance_matches_finite_difference(self):
        mapper = GroundMapper([[30, 2, 100], [1, 40, 200], [0.01, 0.02, 1]])
        point, covariance = np.array([125.0, 240.0]), np.diag([9.0, 16.0])
        _, actual = mapper.uv2xy(point, covariance)
        jacobian = np.column_stack(
            [
                (
                    mapper.uv2xy(point + e * 1e-4, covariance)[0].ravel()
                    - mapper.uv2xy(point - e * 1e-4, covariance)[0].ravel()
                )
                / 2e-4
                for e in np.eye(2)
            ]
        )
        np.testing.assert_allclose(
            actual, jacobian @ covariance @ jacobian.T, rtol=1e-6
        )

    def test_orthogonal_vanishing_points_recover_focal(self):
        k = np.array([[1200, 0, 640], [0, 1200, 360], [0, 0, 1.0]])
        rotation = cv2.Rodrigues(np.array([0.5, -0.1, 0.05]))[0]
        lines = []
        for axis in [0, 1]:
            vp = k @ rotation[:, axis]
            vp = vp[:2] / vp[2]
            lines.append(
                [
                    [anchor, anchor + 0.2 * (vp - anchor)]
                    for anchor in [
                        np.array([10.0, 30.0]),
                        np.array([110.0, 170.0]),
                    ]
                ]
            )
        focal, _ = focal_from_vanishing_lines(lines, [1280, 720])
        self.assertAlmostEqual(focal, 1200.0, places=6)
        with self.assertRaises(ValueError):
            focal_from_vanishing_lines(np.zeros((2, 2, 2, 2)), [1280, 720])

    def test_review_boundary_and_group_confirmation(self):
        registry = self.camera(False)
        with self.assertRaises(ValueError):
            resolve_calibration(registry, self.seq, "homography")
        record = resolve_calibration(registry, self.seq, "homography", False)
        with self.assertRaises(ValueError):
            save_calibration(registry, self.seq, record, True, "")
        save_calibration(registry, self.seq, record, True, "Test")
        prepare_registry(self.dataset, registry.parent, overwrite=True)
        self.assertEqual(
            resolve_calibration(registry, self.seq, "homography")["status"],
            "reviewed",
        )
        save_group_calibration(registry, self.seq, "homography", "cam1")
        contents = read_registry(registry)
        contents["sequences"][self.seq]["methods"]["homography"] = dict(
            status="needs_review"
        )
        registry.write_text(json.dumps(contents))
        with self.assertRaises(ValueError):
            resolve_calibration(registry, self.seq, "homography")
        confirm_camera_group(registry, self.seq, "cam1", "Test")
        self.assertEqual(
            resolve_calibration(registry, self.seq, "homography")[
                "calibration_source"
            ],
            "group:cam1",
        )

    def test_geometry_edits_invalidate_review_and_group_membership(self):
        registry = self.camera()
        contents = read_registry(registry)
        contents["sequences"][self.seq]["methods"]["homography"][
            "dimensions_m"
        ]["width"] = 21
        registry.write_text(json.dumps(contents))
        with self.assertRaisesRegex(ValueError, "changed after review"):
            resolve_calibration(registry, self.seq, "homography")
        result = prepare_registry(self.dataset, registry.parent)
        self.assertEqual(result["pending_review"], 1)
        record = contents["sequences"][self.seq]["methods"]["homography"]
        save_calibration(registry, self.seq, record, True, "Test")
        save_group_calibration(registry, self.seq, "homography", "cam1")
        confirm_camera_group(registry, self.seq, "cam1", "Test")
        record["evidence"] = "Updated synthetic control dimensions"
        save_calibration(registry, self.seq, record, True, "Test")
        save_group_calibration(registry, self.seq, "homography", "cam1")
        contents = read_registry(registry)
        contents["sequences"][self.seq]["methods"]["homography"] = dict(
            status="needs_review"
        )
        registry.write_text(json.dumps(contents))
        with self.assertRaisesRegex(
            ValueError, "confirm the assignment again"
        ):
            resolve_calibration(registry, self.seq, "homography")

    def test_detections_preserve_values_and_cache_invalidation(self):
        path = self.det / "S1.csv"

        def write(score):
            pd.DataFrame(
                [[3, 0, score, 10.5, 20.25, 30.75, 50.5]],
                columns=SOURCE_COLUMNS,
            ).to_csv(path, index=False)

        write(0.05)
        rows, before = load_cached_detections(
            path, self.seq, 6, self.root / "cache"
        )
        np.testing.assert_allclose(
            rows[0], [3, 1, 10.5, 20.25, 20.25, 30.25, 0.05, 0, -1, -1]
        )
        write(0.75)
        _, after = load_cached_detections(
            path, self.seq, 6, self.root / "cache"
        )
        self.assertNotEqual(before["source_sha256"], after["source_sha256"])
        with self.assertRaisesRegex(ValueError, "CSV rows"):
            normalize_detections(path, self.seq, 2)

    def test_perfect_metrics_include_class_zero(self):
        rows = [box(frame=f) for f in [1, 2, 3]]
        result = self.metric(rows, rows)
        for key in ["HOTA", "IDF1", "MOTA", "AssA", "DetA"]:
            self.assertAlmostEqual(result[key], 100.0)
        self.assertEqual(result["IDS"], 0)

    def test_fp_fn_class_mismatch_and_id_switch(self):
        rows = [box(frame=f) for f in [1, 2, 3]]
        fp = self.metric(rows, rows + [box(frame=2, track=2, x=120)])
        self.assertEqual(fp["FP"], 1)
        fn = self.metric(rows, [rows[0], rows[2]])
        self.assertEqual(fn["FN"], 1)
        self.assertEqual(fn["Frag"], 1)
        switched = self.metric(rows, [rows[0], rows[1], box(frame=3, track=2)])
        self.assertEqual(switched["IDS"], 1)
        self.assertLess(switched["IDF1"], 100)
        mismatch = self.metric(
            rows, [box(frame=f, category=1) for f in [1, 2, 3]]
        )
        self.assertEqual(mismatch["HOTA"], 0)
        self.assertEqual(mismatch["FP"], 3)

    def test_unknown_frame_vs_declared_empty_frame(self):
        gt = [box(frame=1), box(frame=3)]
        pred = gt + [box(frame=2, track=99, x=120)]
        masked = self.metric(gt, pred, annotated=[1, 3])
        self.assertEqual(masked["HOTA"], 100)
        mapping = pd.read_csv(self.run / "evaluation/frame_map.csv")
        self.assertEqual(mapping.frame_id.tolist(), [1, 3])
        declared = self.metric(gt, pred, annotated=[1, 2, 3])
        self.assertEqual(declared["FP"], 1)

    def test_clear_identity_iou_point_seven(self):
        gt = [box(x=0, y=0, w=17, h=10)]
        result = self.metric(gt, [box(x=3, y=0, w=17, h=10)])
        self.assertEqual(result["IDF1"], 100)
        result = self.metric(gt, [box(x=3.01, y=0, w=17, h=10)])
        self.assertEqual(result["IDF1"], 0)
        self.assertEqual(result["FN"], 1)

    def test_ignore_union_and_no_gt_filter(self):
        self.fixture([], [box(x=0, y=0, w=21, h=10)])
        regions = pd.DataFrame(
            [dict(sequence="S1", x=x, y=0, w=6, h=10) for x in [0, 4]]
        )
        regions.to_csv(
            self.dataset / "canonical/ignored_regions.csv", index=False
        )
        self.assertEqual(evaluate_run(self.dataset, self.run)["FP"], 1)
        pd.DataFrame([box(x=0, y=0, w=20, h=10)]).to_csv(
            self.run / "tracks/S1.csv", index=False
        )
        self.assertEqual(evaluate_run(self.dataset, self.run)["FP"], 0)
        self.fixture([box(x=0, y=0, w=10, h=10)], [])
        regions.to_csv(
            self.dataset / "canonical/ignored_regions.csv", index=False
        )
        self.assertEqual(evaluate_run(self.dataset, self.run)["FN"], 1)

    def test_detection_weighted_aggregation(self):
        gt = [box(), box(track=2, category=1, x=120)]
        result = self.metric(gt, [gt[0]], frames=1)
        self.assertAlmostEqual(result["HOTA"], math.sqrt(0.5) * 100)
        self.assertAlmostEqual(result["macro_average"]["HOTA"], 50)

    def test_gmc_direction_identity_and_mask(self):
        rng = np.random.default_rng(17)
        previous = rng.integers(0, 256, (240, 320), dtype=np.uint8)
        expected = np.array([[1.0, 0, 6], [0, 1.0, -4]])
        current = cv2.warpAffine(previous, expected, (320, 240))
        actual, reason = estimate_affine(previous, current)
        self.assertIsNone(reason)
        np.testing.assert_allclose(actual, expected, atol=0.3)
        actual, reason = estimate_affine(
            np.zeros((20, 20), np.uint8), np.zeros((20, 20), np.uint8)
        )
        self.assertIsNotNone(reason)
        np.testing.assert_array_equal(actual, IDENTITY)
        provider = _Provider(
            GroundMapper(np.diag([10.0, 20.0, 1.0])),
            np.array([IDENTITY, [[1, 0, 5], [0, 1, -10]]]),
        )
        np.testing.assert_allclose(provider.cmc(2, 3, 10, 20, 1), [2.5, 2.5])
        self.assertEqual(foreground_mask((20, 30), [[-5, 2, 15, 7]])[5, 5], 0)

    def test_runner_complete_timeline_hp_and_resume(self):
        registry = self.camera()
        sources = [
            [f, category, 0.9, 10 + category * 30, 10, 30 + category * 30, 30]
            for f in [2, 3, 4, 5]
            for category in CLASS_NAMES
        ]
        pd.DataFrame(sources, columns=SOURCE_COLUMNS).to_csv(
            self.det / "S1.csv", index=False
        )
        (self.det / "detection_handoff_config.json").write_text(
            json.dumps(
                dict(
                    conf_threshold=0.05,
                    class_mapping={str(k): v for k, v in CLASS_NAMES.items()},
                )
            )
        )
        normal = run_tracking(
            self.dataset, self.det, registry, self.run, sequences=[self.seq]
        )
        self.assertEqual(
            normal["sequence_reports"][self.seq]["frames_processed"], 6
        )
        track = pd.read_csv(self.run / "tracks/S1.csv")
        self.assertEqual(track.frame_id.min(), 4)
        self.assertEqual(track.track_id.nunique(), 4)
        self.assertTrue(
            (track.groupby("track_id").class_id.nunique() == 1).all()
        )
        repeated = self.root / "repeat"
        run_tracking(
            self.dataset, self.det, registry, repeated, sequences=[self.seq]
        )
        self.assertEqual(
            sha256_file(self.run / "tracks/S1.csv"),
            sha256_file(repeated / "tracks/S1.csv"),
        )
        resumed = run_tracking(
            self.dataset,
            self.det,
            registry,
            self.run,
            sequences=[self.seq],
            resume=True,
        )
        self.assertEqual(normal, resumed)
        hp_run = self.root / "hp"
        run_tracking(
            self.dataset,
            self.det,
            registry,
            hp_run,
            {"hp": True},
            sequences=[self.seq],
        )
        padded = pd.read_csv(hp_run / "tracks/S1.csv")
        self.assertEqual(padded.frame_id.min(), 2)
        self.assertEqual(set(padded.class_id), set(CLASS_NAMES))
        with self.assertRaisesRegex(ValueError, "different"):
            run_tracking(
                self.dataset,
                self.det,
                registry,
                self.run,
                {"conf": 0.2},
                sequences=[self.seq],
                resume=True,
            )
        (self.run / "tracks/S1.csv").write_text("changed")
        with self.assertRaises(ValueError):
            run_tracking(
                self.dataset,
                self.det,
                registry,
                self.run,
                sequences=[self.seq],
                resume=True,
            )

    def test_missing_review_preflight_does_not_create_a_run(self):
        registry = self.camera(False)
        with self.assertRaisesRegex(ValueError, "no tracking started"):
            run_tracking(
                self.dataset,
                self.det,
                registry,
                self.run,
                sequences=[self.seq],
            )
        self.assertFalse(self.run.exists())
        pd.DataFrame(columns=SOURCE_COLUMNS).to_csv(
            self.det / "S1.csv", index=False
        )
        (self.det / "detection_handoff_config.json").write_text(
            json.dumps(
                dict(
                    conf_threshold=0.05,
                    class_mapping={str(k): v for k, v in CLASS_NAMES.items()},
                )
            )
        )
        result = run_tracking(
            self.dataset,
            self.det,
            registry,
            self.run,
            sequences=[self.seq],
            allow_unreviewed=True,
        )
        self.assertFalse(result["provenance"]["calibration_reviewed"])
        self.assertTrue(result["provenance"]["allow_unreviewed"])

    def test_resume_skips_completed_sequence_after_interruption(self):
        registry = self.camera()
        metadata = pd.read_csv(self.dataset / "metadata/sequences.csv")
        other = metadata.copy()
        other["sequence"] = "S2"
        pd.concat([metadata, other]).to_csv(
            self.dataset / "metadata/sequences.csv", index=False
        )
        contents = read_registry(registry)
        contents["sequences"]["S2"] = contents["sequences"]["S1"]
        registry.write_text(json.dumps(contents))
        sources = [[f, 0, 0.9, 10, 10, 30, 30] for f in range(1, 7)]
        for sequence in ["S1", "S2"]:
            pd.DataFrame(sources, columns=SOURCE_COLUMNS).to_csv(
                self.det / f"{sequence}.csv", index=False
            )
        (self.det / "detection_handoff_config.json").write_text(
            json.dumps(
                dict(
                    conf_threshold=0.05,
                    class_mapping={str(k): v for k, v in CLASS_NAMES.items()},
                )
            )
        )

        def interrupted(path, sequence, *args):
            if sequence == "S2":
                raise RuntimeError("Synthetic interruption")
            return load_cached_detections(path, sequence, *args)

        with patch(
            "src.tracking.runner.load_cached_detections",
            side_effect=interrupted,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "Synthetic interruption"
            ):
                run_tracking(
                    self.dataset,
                    self.det,
                    registry,
                    self.run,
                    sequences=["S1", "S2"],
                )
        before = sha256_file(self.run / "tracks/S1.csv")
        with patch(
            "src.tracking.runner.load_cached_detections",
            wraps=load_cached_detections,
        ) as loader:
            result = run_tracking(
                self.dataset,
                self.det,
                registry,
                self.run,
                sequences=["S1", "S2"],
                resume=True,
            )
        self.assertEqual(loader.call_count, 1)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(before, sha256_file(self.run / "tracks/S1.csv"))

    def test_cmc_cache_invalidates_on_image_changes(self):
        self.fixture(frames=2, images=True)
        images = {
            f: self.dataset / f"tracking/S1/img1/{f:06d}.jpg" for f in [1, 2]
        }
        cache = self.root / "gmc_cache"
        before, first = load_or_estimate_gmc(
            self.seq, images, {}, 2, cache, "source"
        )
        reused, _ = load_or_estimate_gmc(
            self.seq, images, {}, 2, cache, "source"
        )
        np.testing.assert_array_equal(before, reused)
        Image.new("RGB", (200, 100), "white").save(images[2])
        _, second = load_or_estimate_gmc(
            self.seq, images, {}, 2, cache, "source"
        )
        self.assertNotEqual(first["images_sha256"], second["images_sha256"])
        self.assertEqual(len(list(cache.glob("*.npz"))), 2)

    def test_notebooks_parse_and_environment_cells_run_locally(self):
        import nbformat
        import ipywidgets
        from src.tracking.review import calibration_widget

        for path in sorted((ROOT / "notebooks/tracking").glob("*.ipynb")):
            notebook = nbformat.read(path, as_version=4)
            nbformat.validate(notebook)
            for i, cell in enumerate(notebook.cells):
                self.assertTrue(cell.source.isascii(), f"{path.name}:cell{i}")
                if cell.cell_type == "code":
                    ast.parse(cell.source)
            namespace = {}
            with patch.dict(
                "os.environ",
                {
                    "UA_DETRAC_PROJECT": str(ROOT),
                    "UA_DETRAC_DATASET": str(self.dataset),
                    "UA_DETRAC_DETECTIONS": str(self.det),
                },
            ):
                exec(notebook.cells[2].source, namespace)
            self.assertEqual(namespace["ROOT"], ROOT)
            self.assertEqual(namespace["DATASET"], self.dataset)
        registry = self.camera()
        self.assertIsInstance(
            calibration_widget(self.dataset, registry), ipywidgets.VBox
        )

    def test_interpolation_keeps_class_and_upstream_gap_policy(self):
        records = [box(frame=f, category=2, x=f) for f in [1, 2, 4, 5]]
        result = interpolate_tracks(pd.DataFrame(records)[PRED_COLUMNS], 10)
        self.assertEqual(result.frame_id.tolist(), [1, 2, 3, 4, 5])
        row = result[result.frame_id == 3].iloc[0]
        self.assertEqual(row.class_id, 2)
        self.assertEqual(row.x, 3)

    def test_mouse_corner_selection_undo_clear_and_frame_reset(self):
        import ipywidgets as widgets
        from src.tracking.review import calibration_widget

        registry = self.camera()
        panel = calibration_widget(self.dataset, registry)
        controls, pending = [], [panel]
        while pending:
            child = pending.pop()
            controls.append(child)
            pending.extend(getattr(child, "children", ()))
        points = next(
            c
            for c in controls
            if getattr(c, "description", "") == "Tọa độ JSON:"
        )
        image = next(c for c in controls if isinstance(c, widgets.Image))
        frame = next(
            c
            for c in controls
            if getattr(c, "description", "") == "Ảnh tham chiếu:"
        )
        confirm = next(
            c
            for c in controls
            if getattr(c, "description", "")
            == "Tôi đã kiểm tra điểm, kích thước và cả 3 ảnh preview."
        )
        buttons = {
            c.description: c for c in controls if isinstance(c, widgets.Button)
        }

        def click(x, y, button=0):
            image._corner_events._handle_mouse_msg(
                None,
                dict(
                    type="click",
                    button=button,
                    dataX=x,
                    dataY=y,
                    relativeX=x / 2,
                    relativeY=y / 2,
                    boundingRectWidth=100,
                    boundingRectHeight=50,
                ),
                None,
            )

        click(10, 20, button=2)
        click(float("nan"), 20)
        click(201, 20)
        self.assertEqual(json.loads(points.value), [])
        expected = [[10.25, 20.5], [150, 20], [150, 80], [10, 80]]
        for x, y in expected:
            click(x, y)
        self.assertEqual(json.loads(points.value), expected)
        self.assertTrue(bytes(image.value).startswith(b"\x89PNG"))
        click(50, 50)
        self.assertEqual(json.loads(points.value), expected)
        confirm.value = True
        buttons["Bỏ điểm cuối"].click()
        self.assertEqual(json.loads(points.value), expected[:-1])
        self.assertFalse(confirm.value)
        buttons["Chọn lại từ đầu"].click()
        self.assertEqual(json.loads(points.value), [])
        click(25, 30)
        frame.value = frame.options[0][1]
        self.assertEqual(json.loads(points.value), [])
        buttons["Nạp bản đã lưu"].click()
        self.assertEqual(json.loads(points.value), self.points)
        self.assertFalse(confirm.value)
        self.assertTrue(buttons["Lưu và xác nhận đã duyệt"].disabled)
        buttons["Xem thử phép chiếu"].click()
        self.assertFalse(confirm.disabled)
        reviewer = next(
            c
            for c in controls
            if getattr(c, "description", "") == "Người duyệt:"
        )
        reviewer.value = "Synthetic reviewer"
        self.assertFalse(confirm.disabled)
        confirm.value = True
        self.assertFalse(buttons["Lưu và xác nhận đã duyệt"].disabled)
        points.value = json.dumps(expected)
        self.assertTrue(confirm.disabled)
        self.assertTrue(buttons["Lưu và xác nhận đã duyệt"].disabled)

    def test_tuning_schedule_is_bounded_and_ties_are_deterministic(self):
        names = [f"S{i}" for i in range(12)]

        def trial(*args):
            config, role = args[4], args[-1]
            return dict(
                config_id=str(sorted(config.items())),
                role=role,
                config=config,
                run_dir="synthetic",
                HOTA=50.0,
                IDF1=60.0,
                MOTA=40.0,
                AssA=50.0,
                DetA=50.0,
                IDS=1,
                FP=1,
                FN=1,
                Frag=1,
                runtime_seconds=1.0,
            )

        with patch("src.tracking.tuning._val", return_value=names), patch(
            "src.tracking.tuning._preflight", return_value={}
        ), patch(
            "src.tracking.tuning.evaluation_fingerprint", return_value="test"
        ), patch(
            "src.tracking.tuning._trial", side_effect=trial
        ) as mocked:
            result = tune(self.dataset, self.det, "unused", self.root / "tune")
        self.assertEqual(mocked.call_count, 15)
        self.assertTrue(result["full_schedule_completed"])
        self.assertLessEqual(result["unique_configurations"], 15)

    def test_smoke_runs_both_methods_with_real_tracker_and_video_writer(self):
        """Exercise end-to-end orchestration with explicitly synthetic cameras."""
        self.fixture()
        metadata, annotated, gt = [], [], []
        for sequence in SMOKE_SEQUENCES:
            metadata.append(
                dict(
                    sequence=sequence,
                    num_frames=6,
                    width=200,
                    height=100,
                    fps=25,
                    split="val",
                )
            )
            folder = self.dataset / "tracking" / sequence / "img1"
            folder.mkdir(parents=True)
            for frame in range(1, 7):
                Image.new("RGB", (200, 100), "gray").save(
                    folder / f"{frame:06d}.jpg"
                )
                annotated.append(dict(sequence=sequence, frame_id=frame))
                gt.append(dict(sequence=sequence, **box(frame=frame)))
            source = [[f, 0, 0.9, 20, 20, 40, 40] for f in range(1, 7)]
            pd.DataFrame(source, columns=SOURCE_COLUMNS).to_csv(
                self.det / f"{sequence}.csv", index=False
            )
        pd.DataFrame(metadata).to_csv(
            self.dataset / "metadata/sequences.csv", index=False
        )
        pd.DataFrame(annotated).to_csv(
            self.dataset / "metadata/annotated_frames.csv", index=False
        )
        pd.DataFrame(gt)[GT_COLUMNS].to_csv(
            self.dataset / "canonical/annotations.csv", index=False
        )
        (self.det / "detection_handoff_config.json").write_text(
            json.dumps(
                dict(
                    conf_threshold=0.05,
                    class_mapping={str(k): v for k, v in CLASS_NAMES.items()},
                )
            )
        )
        cameras = self.root / "smoke_cameras"
        prepare_registry(self.dataset, cameras)
        registry = cameras / "registry.json"
        for sequence in SMOKE_SEQUENCES:
            for record in [
                fit_homography(self.points, 20, 10, [200, 100]),
                fit_krt(self.points, 20, 10, [200, 100], focal=1000),
            ]:
                record["evidence"] = (
                    "Synthetic known rectangle for orchestration test"
                )
                save_calibration(
                    registry, sequence, record, True, "Synthetic test"
                )
        with patch("src.tracking.tuning._val", return_value=SMOKE_SEQUENCES):
            result = run_smoke(self.dataset, self.det, registry, self.run)
        self.assertEqual(len(result["comparisons"]), 2)
        for trial in result["comparisons"]:
            run = Path(trial["run_dir"])
            self.assertEqual(len(list((run / "previews").glob("*.mp4"))), 3)
            summary = json.loads((run / "evaluation/summary.json").read_text())
            self.assertEqual(summary["sequence_count"], 3)
            self.assertGreater(summary["HOTA"], 0)


if __name__ == "__main__":
    unittest.main()
