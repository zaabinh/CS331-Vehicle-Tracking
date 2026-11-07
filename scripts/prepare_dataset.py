"""Tạo canonical, Detection và Tracking từ raw mà không sửa dữ liệu gốc."""
import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.canonical import build_canonical
from src.data.export_mot import export_mot
from src.data.export_yolo import export_yolo
from src.data.parse_detrac import inspect_raw, parse_detrac
from src.data.split import load_splits
from src.data.statistics import generate_statistics
from src.data.utils import read_yaml, sha256, write_csv, write_json
from src.data.validate import validate_canonical, validate_exports


def prepare(raw, output, split_dir, config_dir, smoke=False, version="1.1"):
    # Không cho output nằm trong raw hoặc ghi đè dataset version đã tạo.
    raw, output = Path(raw).resolve(), Path(output).resolve()
    if output == raw or raw in output.parents or output in raw.parents:
        raise ValueError("Raw and output must be separate trees")
    if output.exists():
        raise FileExistsError(f"Choose a new output/version; refusing overwrite: {output}")
    classes = read_yaml(config_dir / "classes.yaml")["classes"]
    config = read_yaml(config_dir / "preprocessing.yaml")
    if sorted(classes) != list(range(len(classes))) or len(set(classes.values())) != len(classes):
        raise ValueError("Classes must be unique and numbered from zero")
    if (config["resize"] or not config["clip_bbox_for_detection_export"]
            or not config["fail_on_invalid_bbox"]):
        raise ValueError("Preserve original images and fail on invalid boxes")
    for part in ("train", "val", "test"):
        if type(config[f"{part}_stride"]) is not int or config[f"{part}_stride"] < 1:
            raise ValueError("Stride must be a positive integer")
    # Split đã chốt trong Git; chạy lại pipeline không được tự chia sequence khác.
    splits = load_splits(split_dir)
    official = read_yaml(config_dir / "official_sequences.yaml")
    if set(splits["test"]) != set(official["test"]) or set(splits["train"] + splits["val"]) != set(official["train"]):
        raise ValueError("Fixed splits must preserve official membership")
    output.mkdir(parents=True)
    metadata = output / "metadata"
    metadata.mkdir()
    try:
        # Ghi inventory trước export để lỗi raw vẫn có báo cáo đầy đủ.
        logging.info("Inspecting actual raw XML and image frames")
        inventory, xmls, images = inspect_raw(raw, official["train"] + official["test"])
        write_json(metadata / "raw_inventory.json", inventory)
        logging.info("Structurally valid sequences: %s/%s; partial annotation: %s",
            inventory['ready_sequences'], inventory['expected_sequences'],
            inventory['partial_annotation_sequences'])
        if smoke:
            # Smoke dùng sequence đủ frame đầu tiên của mỗi split, giữ nguyên fixed split.
            selected_splits = {part: [s for s in names if s in images][:1] for part, names in splits.items()}
            empty = [part for part, names in selected_splits.items() if not names]
            if empty:
                raise ValueError(f"No complete sequence in fixed splits {empty}. See {metadata / 'raw_inventory.json'}; fixed splits were not changed.")
            splits = selected_splits
            logging.info("Smoke selection (complete sequences only): %s", splits)
        elif inventory['ready_sequences'] != inventory['expected_sequences'] or inventory['unexpected_sequences']:
            raise ValueError(
                "Full dataset requires structurally valid raw sequences. "
                f"See {metadata / 'raw_inventory.json'}"
            )
        selected = sorted(s for names in splits.values() for s in names)
        images = {s: images[s] for s in selected}
        # Tạo canonical trước, sau đó mới xuất hai format YOLO và MOT từ cùng nguồn.
        tables, regions, sequences = [], [], {}
        for s in selected:
            logging.info("Parsing %s", s)
            table, ignored, meta = parse_detrac(xmls[s], num_frames=len(images[s]))
            tables.append(table)
            regions.extend(ignored)
            sequences[s] = dict(meta, width=config["expected_width"], height=config["expected_height"],
                fps=config["fps"], split=next(p for p, names in splits.items() if s in names))
        # Canonical là nguồn chung để Detection và Tracking dùng cùng annotation.
        canonical = output / "canonical"
        annotations = build_canonical(tables, classes, canonical)
        write_csv(canonical / "ignored_regions.csv", regions, ["sequence", "x", "y", "w", "h"])
        overlap_rows = []
        for row in annotations.itertuples():
            for region in json.loads(row.occlusion_regions_json):
                overlap_rows.append(
                    {
                        "sequence": row.sequence,
                        "frame_id": row.frame_id,
                        "track_id": row.track_id,
                        **region,
                    }
                )
        write_csv(
            canonical / "occlusion_regions.csv",
            overlap_rows,
            ["sequence", "frame_id", "track_id", "left", "top", "width",
             "height", "occlusion_id", "occlusion_status", "attributes_json"],
        )
        logging.info("Validating raw images and canonical annotations")
        report = validate_canonical(annotations, images, sequences, classes)
        if report["errors"]:
            raise ValueError(str(report["errors"][:10]))
        for p, names in splits.items():
            (metadata / f"{p}_sequences.txt").write_text("\n".join(names) + "\n")
        for path in config_dir.glob("*.yaml"):
            shutil.copyfile(path, metadata / path.name)
        # Keep the per-sequence table compact; store frame IDs as a child table.
        sequence_rows = [
            {key: value for key, value in meta.items()
             if key != "annotated_frame_ids"}
            for meta in sequences.values()
        ]
        write_csv(metadata / "sequences.csv", sequence_rows,
                  ["sequence", "num_frames", "num_annotated_frames", "unannotated_frames",
                   "annotation_coverage", "weather", "xml_path", "width", "height", "fps", "split"])
        annotated_frames = [
            {"sequence": sequence, "frame_id": frame}
            for sequence, meta in sequences.items()
            for frame in meta["annotated_frame_ids"]
        ]
        write_csv(
            metadata / "annotated_frames.csv",
            annotated_frames,
            ["sequence", "frame_id"],
        )
        unannotated_frames = []
        for sequence, meta in sequences.items():
            annotated = set(meta["annotated_frame_ids"])
            unannotated_frames.extend(
                {"sequence": sequence, "frame_id": frame}
                for frame in range(1, meta["num_frames"] + 1)
                if frame not in annotated
            )
        write_csv(
            metadata / "unannotated_frames.csv",
            unannotated_frames,
            ["sequence", "frame_id"],
        )
        logging.info("Exporting YOLO and full-continuity MOT")
        export_yolo(annotations, images, sequences, splits, config, classes, output / "detection")
        export_mot(annotations, images, sequences, output / "tracking")
        generate_statistics(annotations, sequences, splits, classes, metadata)
        validate_exports(output, annotations, images, sequences, splits, config, classes)
        write_json(metadata / "manifest.json", dict(dataset="UA-DETRAC", version=version,
            scope="smoke" if smoke else "full", split_method="sequence-level", classes=classes,
            preprocessing=config, splits=splits, detection_format="YOLO", tracking_format="MOT-style (UA classes)",
            visibility="-1 unknown; no inferred ratio", fps_source="configured UA-DETRAC default",
            visual_inspection="PENDING",
            ignored_regions_policy="preserved_for_evaluation_filtering",
            unannotated_frame_policy=(
                "exclude_from_detection_and_evaluation; keep_in_tracking_timeline"
            ),
            source_xml_sha256={s: sha256(xmls[s]) for s in selected},
            config_sha256={p.name: sha256(p) for p in metadata.glob("*.yaml")},
            split_sha256={p.name: sha256(p) for p in metadata.glob("*_sequences.txt")}))
        logging.info("Validation PASSED: %s", output)
    except Exception as exc:
        write_json(metadata / "validation_report.json", dict(status="FAILED", error=str(exc)))
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=ROOT / "data/raw")
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed/dataset_v1.1")
    parser.add_argument("--splits", type=Path, default=ROOT / "splits")
    parser.add_argument("--configs", type=Path, default=ROOT / "configs/data")
    parser.add_argument("--smoke", action="store_true", help="One full sequence per fixed split")
    parser.add_argument("--version", default="1.1")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="[DATA] %(message)s")
    prepare(args.raw, args.output, args.splits, args.configs, args.smoke, args.version)
