"""Road-plane calibration with explicit evidence and human review."""

import copy
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageOps

from .common import fingerprint, read_sequences, write_json

METHODS = ("homography", "krt")


def _review_hash(record):
    """Bind approval to geometry and evidence, independent of resolution source."""
    return fingerprint(
        {
            k: v
            for k, v in record.items()
            if k not in ("review", "status", "calibration_source")
        }
    )


def read_registry(path):
    """Load a versioned registry without inferring calibration values."""
    registry = json.loads(Path(path).read_text(encoding="utf-8"))
    if registry.get("schema_version") != 1:
        raise ValueError("Unsupported calibration registry schema")
    return registry


def _matrix(value):
    matrix = np.asarray(value, dtype=float)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError("Expected finite 3x3 matrix")
    if np.linalg.matrix_rank(matrix) < 3:
        raise ValueError("Singular projective matrix")
    scale = (
        matrix[2, 2] if abs(matrix[2, 2]) > 1e-12 else np.linalg.norm(matrix)
    )
    return matrix / scale


def project_points(matrix, points):
    points = np.asarray(points, dtype=float)
    if (
        points.ndim != 2
        or points.shape[1] != 2
        or not np.isfinite(points).all()
    ):
        raise ValueError("Expected finite Nx2 points")
    result = (
        np.column_stack([points, np.ones(len(points))]) @ _matrix(matrix).T
    )
    if (np.abs(result[:, 2]) < 1e-12).any():
        raise ValueError("Selected point projects to infinity")
    result = result[:, :2] / result[:, 2, None]
    if not np.isfinite(result).all():
        raise ValueError("Nonfinite projection")
    return result


def _rectangle(points, width_m, length_m):
    points = np.asarray(points, dtype=float)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        raise ValueError("Four finite ordered image points are required")
    edges = np.roll(points, -1, axis=0) - points
    next_edges = np.roll(edges, -1, axis=0)
    cross = edges[:, 0] * next_edges[:, 1] - edges[:, 1] * next_edges[:, 0]
    if not ((cross > 0).all() or (cross < 0).all()):
        raise ValueError(
            "Points must follow a convex boundary without crossed edges"
        )
    if (
        not np.isfinite([width_m, length_m]).all()
        or min(width_m, length_m) <= 0
    ):
        raise ValueError("Enter explicit positive width AND length in metres")
    return points, np.array(
        [[0, 0], [width_m, 0], [width_m, length_m], [0, length_m]], float
    )


def fit_homography(image_points, width_m, length_m, image_size=None):
    """Fit a plane from four rectangle marks; scale is an explicit human prior."""
    import cv2

    points, ground = _rectangle(image_points, width_m, length_m)
    h = _matrix(
        cv2.getPerspectiveTransform(
            points.astype(np.float32), ground.astype(np.float32)
        )
    )
    return dict(
        method="homography",
        status="draft",
        units="m",
        image_size=list(image_size) if image_size is not None else None,
        image_points=points.tolist(),
        ground_points=ground.tolist(),
        dimensions_m=dict(width=float(width_m), length=float(length_m)),
        A_ground_to_image=_matrix(np.linalg.inv(h)).tolist(),
        H_image_to_ground=h.tolist(),
        assumptions=[
            "Planar road patch",
            "Rectangle dimensions are supplied priors",
        ],
        geometry_confidence="manual_prior",
        evidence="",
    )


def focal_from_vanishing_lines(lines, image_size):
    """Two orthogonal directions, each marked by two image line segments."""
    lines = np.asarray(lines, float)
    if lines.shape != (2, 2, 2, 2) or not np.isfinite(lines).all():
        raise ValueError("Vanishing lines require shape (2,2,2,2)")
    vps = []
    for direction in lines:
        equations = []
        for segment in direction:
            line = np.cross(*np.column_stack([segment, np.ones(2)]))
            if np.linalg.norm(line[:2]) < 1e-12:
                raise ValueError("Zero-length line segment")
            equations.append(line)
        vp = np.cross(*equations)
        if abs(vp[2]) < 1e-12:
            raise ValueError(
                "Vanishing point at infinity; enter a focal prior"
            )
        vps.append(vp[:2] / vp[2])
    centered = np.asarray(vps) - np.asarray(image_size) / 2
    squared = -float(centered[0] @ centered[1])
    if not np.isfinite(squared) or squared <= 0:
        raise ValueError(
            "Marks do not support a real centered square-pixel focal length"
        )
    return float(np.sqrt(squared)), np.asarray(vps)


def fit_krt(
    image_points,
    width_m,
    length_m,
    image_size,
    vanishing_lines=None,
    focal=None,
):
    """Fit positive-depth planar pose with vanishing-point or explicit focal."""
    import cv2

    points, ground = _rectangle(image_points, width_m, length_m)
    if len(image_size) != 2 or min(image_size) <= 0:
        raise ValueError("Invalid image resolution")
    source = "manual_prior"
    vps = None
    if focal is None:
        focal, vps = focal_from_vanishing_lines(vanishing_lines, image_size)
        source = "orthogonal_vanishing_points"
    if not np.isfinite(focal) or focal <= 0:
        raise ValueError("Focal length must be positive")
    width, height = image_size
    k = np.array([[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1.0]])
    world = np.column_stack([ground, np.zeros(4)])
    solutions = cv2.solvePnPGeneric(
        world, points, k, np.zeros(5), flags=cv2.SOLVEPNP_IPPE
    )
    candidates = []
    for rv, tv in zip(solutions[1], solutions[2]):
        rotation = cv2.Rodrigues(rv)[0]
        translation = tv.reshape(3)
        if ((world @ rotation.T + translation)[:, 2] <= 0).any():
            continue
        a = _matrix(k @ np.column_stack([rotation[:, :2], translation]))
        error = float(
            np.sqrt(np.mean((project_points(a, ground) - points) ** 2))
        )
        candidates.append((error, rotation, translation, a))
    if not candidates:
        raise ValueError("No finite positive-depth camera pose")
    error, rotation, translation, a = min(candidates, key=lambda item: item[0])
    record = fit_homography(points, width_m, length_m, image_size)
    record.update(
        method="krt",
        K=k.tolist(),
        R=rotation.tolist(),
        T=translation.tolist(),
        A_ground_to_image=a.tolist(),
        H_image_to_ground=_matrix(np.linalg.inv(a)).tolist(),
        focal_px=float(focal),
        focal_source=source,
        reprojection_rmse_px=error,
    )
    record["assumptions"] += [
        "Centered principal point",
        "Square pixels, zero skew",
        "Zero lens distortion",
    ]
    if vps is not None:
        record.update(
            vanishing_points=vps.tolist(),
            vanishing_lines=np.asarray(vanishing_lines).tolist(),
        )
    return record


def adjust_krt(record, rotation_deg=(0, 0, 0), translation_m=(0, 0, 0)):
    """Adjust camera pose; any edit discards the previous review."""
    import cv2

    record = copy.deepcopy(record)
    if record.get("method") != "krt":
        raise ValueError("Pose adjustments require KRT")
    angles, delta = np.asarray(rotation_deg, float), np.asarray(
        translation_m, float
    )
    if (
        angles.shape != (3,)
        or delta.shape != (3,)
        or not np.isfinite([angles, delta]).all()
    ):
        raise ValueError("Finite XYZ adjustment triples required")
    rotation = np.eye(3)
    for axis, angle in zip(np.eye(3), np.radians(angles)):
        rotation = cv2.Rodrigues(axis * angle)[0] @ rotation
    rotation = rotation @ np.asarray(record["R"])
    translation = np.asarray(record["T"]) + delta
    world = np.column_stack([record["ground_points"], np.zeros(4)])
    if ((world @ rotation.T + translation)[:, 2] <= 0).any():
        raise ValueError("Adjustment puts a marked point behind the camera")
    a = _matrix(
        np.asarray(record["K"])
        @ np.column_stack([rotation[:, :2], translation])
    )
    record.update(
        status="draft",
        R=rotation.tolist(),
        T=translation.tolist(),
        A_ground_to_image=a.tolist(),
        H_image_to_ground=_matrix(np.linalg.inv(a)).tolist(),
    )
    record["reprojection_rmse_px"] = float(
        np.sqrt(
            np.mean(
                (
                    project_points(a, record["ground_points"])
                    - record["image_points"]
                )
                ** 2
            )
        )
    )
    record["manual_adjustment"] = dict(
        rotation_deg=angles.tolist(), translation_m=delta.tolist()
    )
    record.pop("review", None)
    return record


def validate_calibration_record(record, require_reviewed=True):
    """Check invertible geometry, unit conventions and review binding."""
    record = copy.deepcopy(record)
    if record.get("status") not in ("draft", "needs_review", "reviewed"):
        raise ValueError("Unknown review status")
    if require_reviewed and record.get("status") != "reviewed":
        raise ValueError("Calibration requires explicit human review")
    if require_reviewed and record.get("review", {}).get(
        "calibration_sha256"
    ) != _review_hash(record):
        raise ValueError(
            "Calibration changed after review; review the current geometry again"
        )
    if record.get("units") != "m":
        raise ValueError(
            "Ground coordinates must use explicitly declared metres"
        )
    a = _matrix(record.get("A_ground_to_image"))
    size = np.asarray(record.get("image_size"), float)
    if (
        size.shape != (2,)
        or not np.isfinite(size).all()
        or (size <= 0).any()
        or (size != np.floor(size)).any()
    ):
        raise ValueError("Calibration requires integer image width/height")
    h = _matrix(np.linalg.inv(a))
    if "H_image_to_ground" in record and not np.allclose(
        _matrix(record["H_image_to_ground"]), h
    ):
        raise ValueError("H and A are not mutual inverses")
    if record.get("method") not in METHODS:
        raise ValueError("Unknown calibration method")
    if record["method"] == "krt":
        k, rotation, translation = [
            np.asarray(record[key], float) for key in ("K", "R", "T")
        ]
        if (
            k.shape != (3, 3)
            or rotation.shape != (3, 3)
            or translation.shape != (3,)
        ):
            raise ValueError("KRT requires 3x3 K/R and a three-vector T")
        if not all(
            np.isfinite(value).all() for value in (k, rotation, translation)
        ):
            raise ValueError("Nonfinite KRT parameters")
        if not np.allclose(
            rotation.T @ rotation, np.eye(3), atol=1e-6
        ) or not np.isclose(np.linalg.det(rotation), 1.0):
            raise ValueError("R must be a proper rotation")
        rebuilt = _matrix(k @ np.column_stack([rotation[:, :2], translation]))
        if not np.allclose(a, rebuilt):
            raise ValueError("A differs from the stored KRT parameters")
        ground = np.asarray(record["ground_points"], float)
        if (
            (
                np.column_stack([ground, np.zeros(len(ground))]) @ rotation.T
                + translation
            )[:, 2]
            <= 0
        ).any():
            raise ValueError("KRT puts a marked point behind the camera")
    record.update(
        A_ground_to_image=a.tolist(),
        H_image_to_ground=h.tolist(),
        image_size=size.astype(int).tolist(),
    )
    return record


def resolve_calibration(
    registry_path, sequence, method, require_reviewed=True
):
    """Use sequence overrides first, then explicitly confirmed camera groups."""
    if method not in METHODS:
        raise ValueError(f"Unknown calibration method: {method}")
    registry = read_registry(registry_path)
    entry = registry["sequences"][sequence]
    record = entry.get("methods", {}).get(method, {})
    source = f"sequence:{sequence}"
    if "A_ground_to_image" not in record:
        group = entry.get("camera_group")
        if not group or not entry.get("camera_group_confirmed"):
            raise ValueError(
                f"{sequence}/{method}: no calibration or confirmed camera group"
            )
        record = registry["groups"][group]["methods"][method]
        source = f"group:{group}"
        approved = entry.get("group_review", {}).get("calibration_sha256", {})
        if require_reviewed and approved.get(method) != _review_hash(record):
            raise ValueError(
                f"{sequence}: group calibration changed; confirm the assignment again"
            )
    record = validate_calibration_record(record, require_reviewed)
    if entry.get("image_size") != record["image_size"]:
        raise ValueError(f"{sequence}: calibration image size differs")
    record["calibration_source"] = source
    return record


def save_calibration(
    registry_path,
    sequence,
    record,
    reviewed=False,
    reviewer="",
    review_notes="",
):
    """Only explicit saves with evidence and reviewer can mark a camera reviewed."""
    registry = read_registry(registry_path)
    record = copy.deepcopy(record)
    method = record["method"]
    if method not in METHODS or sequence not in registry["sequences"]:
        raise ValueError("Unknown method or sequence")
    record["status"] = "reviewed" if reviewed else "draft"
    record.pop("review", None)
    record = validate_calibration_record(record, False)
    if reviewed and (
        not reviewer.strip() or not record.get("evidence", "").strip()
    ):
        raise ValueError(
            "Reviewed cameras require reviewer and dimension evidence"
        )
    if registry["sequences"][sequence]["image_size"] != record["image_size"]:
        raise ValueError("Camera dimensions differ from sequence")
    record["review"] = dict(
        reviewer=reviewer,
        notes=review_notes,
        reviewed_at=(
            datetime.now(timezone.utc).isoformat() if reviewed else None
        ),
        calibration_sha256=_review_hash(record) if reviewed else None,
    )
    registry["sequences"][sequence]["methods"][method] = record
    write_json(registry_path, registry)
    return record


def save_group_calibration(registry_path, sequence, method, group_id):
    """Publish a reviewed reference; members need separate confirmation."""
    registry = read_registry(registry_path)
    camera = resolve_calibration(registry_path, sequence, method)
    group = registry["groups"].setdefault(
        group_id, dict(reference_sequence=sequence, methods={})
    )
    if (
        not group_id.strip()
        or group.get("image_size", camera["image_size"])
        != camera["image_size"]
    ):
        raise ValueError("Invalid group name or incompatible image dimensions")
    group["image_size"] = camera["image_size"]
    group["methods"][method] = camera
    write_json(registry_path, registry)


def confirm_camera_group(
    registry_path, sequence, group_id, reviewer, notes=""
):
    registry = read_registry(registry_path)
    group = registry["groups"][group_id]
    entry = registry["sequences"][sequence]
    if not reviewer.strip() or entry["image_size"] != group["image_size"]:
        raise ValueError(
            "Group confirmation requires reviewer and matching resolution"
        )
    reviewed = {}
    for method, record in group["methods"].items():
        if record.get("status") == "reviewed":
            validate_calibration_record(record)
            reviewed[method] = _review_hash(record)
    if not reviewed:
        raise ValueError("Group has no reviewed reference")
    entry.update(
        camera_group=group_id,
        camera_group_confirmed=True,
        group_review=dict(
            reviewer=reviewer,
            notes=notes,
            calibration_sha256=reviewed,
            reviewed_at=datetime.now(timezone.utc).isoformat(),
        ),
    )
    write_json(registry_path, registry)


def export_krt(record, output_path):
    """Upstream text uses millimetres; our JSON uses metres."""
    record = validate_calibration_record(record, False)
    if record.get("method") != "krt":
        raise ValueError("A homography alone does not define K/R/T")
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    chunks = []
    for name, array in [
        ("RotationMatrices", record["R"]),
        ("TranslationVectors", np.asarray(record["T"]).reshape(1, 3) * 1000),
        ("IntrinsicMatrix", record["K"]),
    ]:
        chunks.append(
            name
            + "\n"
            + "\n".join(" ".join(f"{x:.12g}" for x in row) for row in array)
        )
    path.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")


def reference_images(dataset, sequence):
    """Select first, middle and final images from dataset metadata."""
    info = read_sequences(dataset)[sequence]
    frames = list(
        dict.fromkeys(
            [1, int(info["num_frames"]) // 2 + 1, int(info["num_frames"])]
        )
    )
    return [
        Path(dataset) / "tracking" / sequence / "img1" / f"{f:06d}.jpg"
        for f in frames
    ]


def frame_number(path):
    return int(Path(path).stem)


def prepare_registry(dataset, output, overwrite=False):
    """Generate contact sheets and similarity suggestions; never auto-review."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "registry.json"
    registry = (
        read_registry(path)
        if path.exists()
        else dict(schema_version=1, sequences={}, groups={})
    )
    metadata, features, tiles, statuses = read_sequences(dataset), {}, [], []
    ordered = sorted(
        metadata, key=lambda s: (metadata[s].get("split") != "val", s)
    )
    for index, sequence in enumerate(ordered, 1):
        info = metadata[sequence]
        entry = registry["sequences"].setdefault(
            sequence,
            dict(
                camera_group=None,
                camera_group_confirmed=False,
                methods={m: dict(status="needs_review") for m in METHODS},
                image_size=[int(info["width"]), int(info["height"])],
                split=info["split"],
            ),
        )
        logging.info(
            "Camera inventory %s (%d/%d)", sequence, index, len(metadata)
        )
        refs, error = reference_images(dataset, sequence), ""
        try:
            sheet = Image.new("RGB", (1200, 255), "white")
            for j, ref in enumerate(refs):
                with Image.open(ref) as image:
                    if list(image.size) != entry["image_size"]:
                        raise ValueError(
                            "Image resolution differs from metadata"
                        )
                    sheet.paste(
                        ImageOps.pad(image.convert("RGB"), (400, 225)),
                        (j * 400, 30),
                    )
                    if j == 0:
                        gray = np.asarray(
                            image.convert("L").resize((32, 18)), float
                        )
                        gray = (gray - gray.mean()) / max(gray.std(), 1.0)
                        feat = np.r_[
                            gray.ravel(),
                            np.diff(gray, axis=0).ravel(),
                            np.diff(gray, axis=1).ravel(),
                        ]
                        features[sequence] = feat / max(
                            np.linalg.norm(feat), 1e-12
                        )
                ImageDraw.Draw(sheet).text(
                    (j * 400 + 5, 8),
                    f"{sequence} frame {ref.stem}",
                    fill="black",
                )
            destination = output / "contact_sheets" / f"{sequence}.jpg"
            destination.parent.mkdir(exist_ok=True)
            if overwrite or not destination.exists():
                sheet.save(destination, quality=90)
            tile = Image.new("RGB", (240, 165), "white")
            tile.paste(
                sheet.crop((0, 30, 400, 255)).resize((240, 135)), (0, 30)
            )
            ImageDraw.Draw(tile).text((4, 8), sequence, fill="black")
            tiles.append(tile)
            entry.setdefault(
                "reference_frames", [frame_number(p) for p in refs]
            )
        except (OSError, ValueError) as exc:
            error = str(exc)
        row = dict(sequence=sequence, split=info["split"], image_error=error)
        for method in METHODS:
            try:
                row[method] = (
                    resolve_calibration(path, sequence, method)["status"]
                    if path.exists()
                    else "needs_review"
                )
            except (ValueError, KeyError):
                stored = entry["methods"][method].get("status", "needs_review")
                row[method] = "draft" if stored == "draft" else "needs_review"
        statuses.append(row)
    write_json(path, registry)
    if tiles:
        overview = Image.new(
            "RGB", (2400, ((len(tiles) + 9) // 10) * 165), "white"
        )
        for i, tile in enumerate(tiles):
            overview.paste(tile, ((i % 10) * 240, (i // 10) * 165))
        overview.save(output / "all_sequences.jpg", quality=90)
    suggestions = []
    for sequence, feature in features.items():
        compatible = [
            (float(feature @ other), name)
            for name, other in features.items()
            if name != sequence
            and registry["sequences"][name]["image_size"]
            == registry["sequences"][sequence]["image_size"]
        ]
        for rank, (score, candidate) in enumerate(
            sorted(compatible, reverse=True)[:3], 1
        ):
            suggestions.append(
                dict(
                    sequence=sequence,
                    candidate_sequence=candidate,
                    rank=rank,
                    similarity=score,
                    status="unconfirmed",
                )
            )
    pd.DataFrame(statuses).to_csv(output / "review_status.csv", index=False)
    pd.DataFrame(suggestions).to_csv(
        output / "group_suggestions.csv", index=False
    )
    summary = dict(
        registry_path=str(path.resolve()),
        sequences=len(metadata),
        sequences_with_reference_images=len(features),
        pending_review=sum(
            not any(r[m] == "reviewed" for m in METHODS) for r in statuses
        ),
        status_csv=str(output / "review_status.csv"),
    )
    write_json(output / "inventory_report.json", summary)
    return summary


def create_calibration_previews(
    dataset, sequence, record, output, detections_csv=None
):
    """Save first/middle/last road grids, rectification and detector footpoints."""
    import cv2

    record = validate_calibration_record(record, False)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    detections = (
        pd.read_csv(detections_csv)
        if detections_csv and Path(detections_csv).is_file()
        else None
    )
    w, length = (
        record["dimensions_m"]["width"],
        record["dimensions_m"]["length"],
    )
    scale = min(600 / w, 1200 / length)
    paths = []
    for source in reference_images(dataset, sequence):
        with Image.open(source) as opened:
            image = opened.convert("RGB")
        if list(image.size) != record["image_size"]:
            raise ValueError("Preview resolution differs from calibration")
        rectified = cv2.warpPerspective(
            np.asarray(image),
            np.diag([scale, scale, 1.0])
            @ np.asarray(record["H_image_to_ground"]),
            (max(2, int(w * scale)), max(2, int(length * scale))),
        )
        draw = ImageDraw.Draw(image)
        for x in np.linspace(0, w, 8):
            points = project_points(
                record["A_ground_to_image"], [[x, 0], [x, length]]
            )
            draw.line([tuple(p) for p in points], fill="cyan", width=2)
        for y in np.linspace(0, length, 12):
            points = project_points(
                record["A_ground_to_image"], [[0, y], [w, y]]
            )
            draw.line([tuple(p) for p in points], fill="yellow", width=2)
        if detections is not None:
            for row in detections[
                detections.frame_id == frame_number(source)
            ].itertuples():
                x, y = (row.x1 + row.x2) / 2, row.y2
                draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill="red")
        canvas = Image.new(
            "RGB",
            (
                image.width + rectified.shape[1],
                max(image.height, rectified.shape[0]) + 30,
            ),
            "white",
        )
        canvas.paste(image, (0, 30))
        canvas.paste(Image.fromarray(rectified), (image.width, 30))
        ImageDraw.Draw(canvas).text(
            (5, 8),
            f"{sequence} {source.stem} {record['method']} {record['status']}",
            fill="black",
        )
        destination = output / f"frame_{source.stem}.jpg"
        canvas.save(destination, quality=90)
        paths.append(str(destination.resolve()))
    write_json(
        output / "provenance.json",
        dict(
            calibration=record,
            calibration_sha256=fingerprint(record),
            previews=paths,
        ),
    )
    return paths
