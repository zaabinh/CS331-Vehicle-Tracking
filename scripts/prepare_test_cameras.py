"""Fit explicitly unreviewed test camera proposals without changing val cameras."""

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.tracking.calibration import (
    create_calibration_previews,
    fit_homography,
    read_registry,
    resolve_calibration,
)
from src.tracking.common import read_sequences, sha256_file, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "outputs/ua_detrac/dataset_v1.1"
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "outputs/tracking/calibration/registry_approved_h_v1.json",
    )
    parser.add_argument(
        "--spec", type=Path, default=ROOT / "configs/tracking/test_camera_drafts.json"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "outputs/tracking/test_calibration_draft"
    )
    args = parser.parse_args()
    registry = copy.deepcopy(read_registry(args.source))
    specification = json.loads(args.spec.read_text())
    metadata = read_sequences(args.dataset)
    test = {s for s, info in metadata.items() if info["split"] == "test"}
    if set(specification["points"]) - test:
        raise ValueError("Draft proposals must be confined to the fixed test split")
    for sequence, points in specification["points"].items():
        entry = registry["sequences"][sequence]
        existing = entry["methods"]["homography"]
        if existing.get("status") == "reviewed":
            raise ValueError(f"Preserve reviewed calibration: {sequence}")
        image = args.dataset / "tracking" / sequence / "img1/000001.jpg"
        record = fit_homography(
            points,
            specification["width_m"],
            specification["length_m"],
            entry["image_size"],
        )
        record.update(
            status="draft",
            source_frame=1,
            source_image_sha256=sha256_file(image),
            evidence=specification["source"],
            dimension_source=specification["dimensions_source"],
            limitations="Single planar approximation at an intersection or road segment; physical scale unmeasured; not human reviewed.",
        )
        entry["methods"]["homography"] = record
        create_calibration_previews(
            args.dataset, sequence, record, args.output / "previews" / sequence
        )
    path = args.output / "registry.json"
    write_json(path, registry)
    for sequence in sorted(test):
        resolve_calibration(path, sequence, "homography", require_reviewed=False)
    write_json(
        args.output / "provenance.json",
        dict(
            source_registry_sha256=sha256_file(args.source),
            specification_sha256=sha256_file(args.spec),
            source_script_sha256=sha256_file(__file__),
            reviewed_sequences=sorted(test - set(specification["points"])),
            unreviewed_sequences=sorted(specification["points"]),
            test_gt_used=False,
            test_metrics_used=False,
            calibration_status="provisional_test_requires_camera_review",
        ),
    )
    print(
        f"Prepared {len(test)} test cameras; {len(specification['points'])} remain draft"
    )


if __name__ == "__main__":
    main()
