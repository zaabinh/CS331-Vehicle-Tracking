"""Export manually marked road-plane proposals for human review."""

import argparse
import copy
import html
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.tracking.calibration import (
    create_calibration_previews,
    fit_homography,
    project_points,
    read_registry,
    validate_calibration_record,
)
from src.tracking.common import sha256_file, write_json


def propose(dataset, config, registry_path, output, install=False):
    """Keep physical priors and provisional grouping separate from approval."""
    dataset, output = Path(dataset), Path(output)
    specification = json.loads(Path(config).read_text(encoding="utf-8"))
    registry = read_registry(registry_path)
    rows, cards, records = [], [], {}
    views = specification["views"]
    if len(views) != 24 or len({v["view_id"] for v in views}) != 24:
        raise ValueError("Expected 24 unique proposed views")
    # Preflight before publishing files or replacing existing human work.
    for view in views:
        sequence = view["reference_sequence"]
        existing = registry["sequences"][sequence]["methods"].get(
            "homography", {}
        )
        if (
            install
            and "A_ground_to_image" in existing
            and (
                existing.get("status") == "reviewed"
                or existing.get("proposal_batch") != specification["batch_id"]
            )
        ):
            raise ValueError(f"Preserve existing calibration: {sequence}")
    output.mkdir(parents=True, exist_ok=True)
    overview = Image.new("RGB", (1920, 1800), "white")
    for index, view in enumerate(views, 1):
        sequence, view_id = view["reference_sequence"], view["view_id"]
        entry = registry["sequences"][sequence]
        source = (
            dataset
            / "tracking"
            / sequence
            / "img1"
            / f"{view['source_frame']:06d}.jpg"
        )
        with Image.open(source) as opened:
            image = opened.convert("RGB")
        record = fit_homography(
            view["image_points"], view["width_m"], view["length_m"], image.size
        )
        record.update(
            status="needs_review",
            source_frame=view["source_frame"],
            source_image_sha256=sha256_file(source),
            proposal_batch=specification["batch_id"],
            proposed_view_id=view_id,
            geometry_confidence="low_unmeasured_priors",
            evidence=f"Width: {specification['width_source']}. Length: {specification['length_source']}.",
            width_source=specification["width_source"],
            length_source=specification["length_source"],
            corner_source=specification["corner_source"],
            limitations=view["notes"],
            grouping_status="proposed_reference_only_not_official_camera_id",
        )
        record = validate_calibration_record(record, False)
        recovered = project_points(
            record["H_image_to_ground"], view["image_points"]
        )
        error = float(
            np.max(np.abs(recovered - np.asarray(record["ground_points"])))
        )
        if not np.isfinite(error) or error > 1e-4:
            raise ValueError(
                f"{sequence}: failed four-point consistency check"
            )
        record["qa"] = dict(
            max_corner_error_m=error,
            note="Algebraic four-point consistency, not independent metric accuracy",
        )
        folder = output / view_id
        folder.mkdir(exist_ok=True)
        write_json(folder / "calibration.json", record)
        for name, key in [
            ("A", "A_ground_to_image"),
            ("H", "H_image_to_ground"),
        ]:
            matrix = np.asarray(record[key])
            np.save(folder / f"{name}.npy", matrix)
            np.savetxt(folder / f"{name}.txt", matrix, fmt="%.17g")
        draw = ImageDraw.Draw(image, "RGBA")
        corners = [tuple(p) for p in view["image_points"]]
        draw.polygon(corners, fill=(255, 0, 0, 35))
        draw.line(corners + corners[:1], fill="red", width=4)
        for number, (x, y) in enumerate(corners, 1):
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill="red")
            draw.text(
                (x + 10, y - 12),
                str(number),
                fill="yellow",
                stroke_width=2,
                stroke_fill="black",
            )
        image.save(folder / "corners.jpg", quality=95)
        x, y = ((index - 1) % 4) * 480, ((index - 1) // 4) * 300
        overview.paste(image.resize((480, 270)), (x, y + 30))
        ImageDraw.Draw(overview).text(
            (x + 6, y + 6),
            f"{view_id} {sequence} W={view['width_m']} L={view['length_m']} PRIOR",
            fill="black",
        )
        previews = create_calibration_previews(
            dataset,
            sequence,
            record,
            folder / "previews",
            ROOT / "outputs/detections_full_yolo26l_E17" / f"{sequence}.csv",
        )
        entry["methods"]["homography"] = record
        entry.update(camera_group=view_id, camera_group_confirmed=False)
        registry["groups"][view_id] = dict(
            reference_sequence=sequence,
            image_size=list(image.size),
            status="needs_review",
            scope=specification["scope"],
            methods=dict(homography=copy.deepcopy(record)),
        )
        records[view_id] = dict(reference_sequence=sequence, **record)
        rows.append(
            dict(
                view_id=view_id,
                sequence=sequence,
                source_frame=view["source_frame"],
                width_m=view["width_m"],
                length_m=view["length_m"],
                status="needs_review",
                image_points=json.dumps(view["image_points"]),
                max_corner_error_m=error,
                limitations=view["notes"],
            )
        )
        preview_links = " ".join(
            f'<a href="{view_id}/previews/{Path(p).name}">Frame {Path(p).stem[6:]}</a>'
            for p in previews
        )
        cards.append(
            f'<section id="{view_id}"><h2>{view_id}: {sequence}</h2>'
            f'<p>NEEDS REVIEW | W={view["width_m"]} m, L={view["length_m"]} m: unmeasured priors.</p>'
            f'<p>{html.escape(view["notes"])}</p><img src="{view_id}/corners.jpg">'
            f"<p>Grid, rectification and detector footpoints: {preview_links}</p>"
            f"<details><summary>Points and A (ground metres to image pixels)</summary>"
            f'<pre>{html.escape(json.dumps(dict(points=view["image_points"], A=record["A_ground_to_image"]),indent=2))}</pre></details></section>'
        )
        print(f"[CAMERA] {index}/24 {view_id} {sequence}", flush=True)
    write_json(output / "registry.json", registry)
    write_json(output / "homographies.json", records)
    write_json(output / "selection_specification.json", specification)
    pd.DataFrame(rows).to_csv(output / "review_summary.csv", index=False)
    overview.save(output / "all_proposed_corners.jpg", quality=95)
    write_json(
        output / "geometry_checks.json",
        dict(
            views=len(records),
            A_files=24,
            H_files=24,
            preview_images=72,
            max_algebraic_corner_error_m=max(
                r["max_corner_error_m"] for r in rows
            ),
            numerical_roundtrip="passed",
            independent_physical_accuracy="Not established: dimensions and transverse alignments require review",
        ),
    )
    header = (
        '<!doctype html><meta charset="utf-8"><title>24 road-plane proposals</title>'
        "<style>body{font:16px sans-serif;max-width:1100px;margin:30px auto;padding:16px}"
        "img{max-width:100%}section{border-top:2px solid #bbb;margin-top:30px}pre{overflow:auto}</style>"
        "<h1>24 representative view proposals: review required</h1>"
        "<p>These are not certified physical camera IDs. Width and length are unmeasured priors. "
        "Review selected corners, both dimensions, geometry and sequence grouping before tracking. "
        "A is ground-to-image; H=A inverse is image-to-ground. All ground coordinates use metres. "
        "No calibration is marked reviewed.</p>"
        '<p><a href="all_proposed_corners.jpg">Overview of all 24 proposed quadrilaterals</a></p>'
        "<p>Only the 24 reference sequences receive numerical proposals. The other 76 sequences "
        "are not assigned a shared matrix without verification of crop, zoom and geometry.</p>"
    )
    (output / "review.html").write_text(
        header + "\n".join(cards), encoding="utf-8"
    )
    if install:
        backup = (
            Path(registry_path).parent
            / "backups"
            / f"registry_before_{sha256_file(registry_path)[:12]}.json"
        )
        backup.parent.mkdir(exist_ok=True)
        if not backup.exists():
            backup.write_bytes(Path(registry_path).read_bytes())
        write_json(registry_path, registry)
    summary = dict(
        proposed_views=24,
        numeric_reference_sequences=24,
        remaining_sequences_without_new_matrix=len(registry["sequences"]) - 24,
        reviewed=0,
        status="needs_review",
        physical_scale="unmeasured_priors",
        view_mapping="provisional_not_official",
        installed=install,
        selection_sha256=sha256_file(config),
        generator_sha256=sha256_file(__file__),
        review_page=str((output / "review.html").resolve()),
    )
    write_json(output / "proposal_report.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "outputs/ua_detrac/dataset_v1.1"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/tracking/camera_proposals_24.json",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=ROOT / "outputs/tracking/calibration/registry.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs/tracking/calibration/proposals_24_v1",
    )
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            propose(
                args.dataset,
                args.config,
                args.registry,
                args.output,
                args.install,
            ),
            indent=2,
        )
    )
