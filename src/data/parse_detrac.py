"""
Script: parse_detrac.py
Chức năng chính:
Usage:
"""

import json
import math
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

"""
    Ý nghĩa các cột của một annotation sau khi parse:
    - sequence: được lấy từ sequence.name, là tên của sequence được gán nhán
    - frame_id: được lấy từ frame.num, là ID của frame được gán nhãn
    - track_id: được lấy từ target.id, là track ID của một đối tượng trong ảnh
    - class_name: được lấy từ target>attribute.vehicle_type là tên loại phương tiện
    - x | y | w | h: là size của một bbox theo dạng (x_min, y_min, width, height), được lấy lần lượt từ box.left, box.top, box.width, box.height
    - occlusion_ratio: chỉ lấy khi XML cung cấp; không suy ra từ region_overlap
    - occlusion_xml: giữ nguyên XML của node occlusion
    - occlusion_regions_json: region_overlap đã parse, gắn với frame và track
"""
ANNOTATION_COLUMNS = [
    "sequence", "frame_id", "track_id", "class_name", "x", "y", "w", "h",
    "occlusion", "occlusion_ratio", "truncation", "orientation", "occlusion_xml",
    "occlusion_region_count", "occlusion_regions_json", "attributes_json",
]


def parse_detrac(xml_path, num_frames=None):
    """Đọc XML thành bảng object, vùng ignore và metadata của sequence."""
    root = ET.parse(xml_path).getroot()
    sequence = root.attrib.get("name", Path(xml_path).stem)
    if sequence != Path(xml_path).stem:
        raise ValueError(f"Sequence/file mismatch: {xml_path}")
    rows, frames, regions = [], [], []
    weather = root.find("sequence_attribute")
    weather = weather.attrib.get("weather", "") if weather is not None else ""
    # Vùng ignore không gắn với một object cụ thể nên lưu riêng.
    for box in root.findall("./ignored_region/box"):
        regions.append(dict(sequence=sequence, **{
            k: float(box.attrib[src]) for k, src in [("x", "left"), ("y", "top"), ("w", "width"), ("h", "height")]}))
    for frame in root.findall("frame"):
        frame_id = int(frame.attrib["num"])
        frames.append(frame_id)
        for target in frame.findall("./target_list/target"):
            box = target.find("box")
            attr = target.find("attribute")
            if box is None or attr is None:
                raise ValueError(f"Missing box/attribute: {sequence}/{frame_id}")
            attrs = attr.attrib
            occ = target.find("occlusion")
            overlap_regions = []
            for overlap in target.findall("./occlusion/region_overlap"):
                overlap_attrs = dict(overlap.attrib)
                geometry = ("left", "top", "width", "height")
                if not all(key in overlap_attrs for key in geometry):
                    raise ValueError(
                        f"Incomplete region_overlap: {sequence}/{frame_id}/"
                        f"{target.attrib['id']}"
                    )
                coordinates = {
                    key: float(overlap_attrs[key]) for key in geometry
                }
                if not all(math.isfinite(value) for value in coordinates.values()):
                    raise ValueError(
                        f"Non-finite region_overlap: {sequence}/{frame_id}/"
                        f"{target.attrib['id']}"
                    )
                if coordinates["width"] <= 0 or coordinates["height"] <= 0:
                    raise ValueError(
                        f"Invalid region_overlap size: {sequence}/{frame_id}/"
                        f"{target.attrib['id']}"
                    )
                overlap_regions.append(
                    {
                        **coordinates,
                        "occlusion_id": overlap_attrs.get("occlusion_id", ""),
                        "occlusion_status": overlap_attrs.get(
                            "occlusion_status", ""
                        ),
                        "attributes_json": json.dumps(
                            overlap_attrs, sort_keys=True
                        ),
                    }
                )
            # Preserve both raw XML and structured regions; do not infer a ratio.
            rows.append(
                dict(
                    sequence=sequence,
                    frame_id=frame_id,
                    track_id=int(target.attrib["id"]),
                    class_name=attrs["vehicle_type"],
                    x=float(box.attrib["left"]),
                    y=float(box.attrib["top"]),
                    w=float(box.attrib["width"]),
                    h=float(box.attrib["height"]),
                    occlusion=(
                        float(attrs["occlusion_ratio"])
                        if "occlusion_ratio" in attrs else None
                    ),
                    occlusion_ratio=(
                        float(attrs["occlusion_ratio"])
                        if "occlusion_ratio" in attrs else None
                    ),
                    truncation=(
                        float(attrs["truncation_ratio"])
                        if "truncation_ratio" in attrs else None
                    ),
                    orientation=attrs.get("orientation", ""),
                    occlusion_xml=(
                        ET.tostring(occ, encoding="unicode") if occ is not None else ""
                    ),
                    occlusion_region_count=len(overlap_regions),
                    occlusion_regions_json=json.dumps(
                        overlap_regions, sort_keys=True
                    ),
                    attributes_json=json.dumps(attrs, sort_keys=True),
                )
            )
    # Frame IDs may be sparse, but must be positive and unique.
    if not frames or len(frames) != len(set(frames)) or min(frames) < 1:
        raise ValueError(f"Duplicate or invalid annotation frame IDs: {sequence}")
    timeline_length = num_frames if num_frames is not None else max(frames)
    if max(frames) > timeline_length:
        raise ValueError(f"Annotation frame exceeds image timeline: {sequence}")
    return pd.DataFrame(rows, columns=ANNOTATION_COLUMNS), regions, dict(
        sequence=sequence, num_frames=timeline_length, num_annotated_frames=len(frames),
        annotated_frame_ids=sorted(frames),
        unannotated_frames=timeline_length - len(frames),
        annotation_coverage=len(frames) / timeline_length,
        weather=weather, xml_path=str(xml_path))


def inspect_raw(raw, expected_sequences):
    """Đếm file thật và báo lỗi theo sequence trước khi export."""
    raw = Path(raw)
    if not raw.is_dir():
        raise FileNotFoundError(f"Raw directory does not exist: {raw}")
    xml_candidates, image_files = {}, {}

    def walk_error(error):
        raise error

    # Quét cây thư mục một lần; nếu Drive lỗi đọc thì báo lỗi thật, không coi là thiếu ảnh.
    for directory, children, files in os.walk(raw, onerror=walk_error):
        folder = Path(directory)
        # Ảnh có thể nằm ngay trong sequence hoặc thư mục con như image/ hay img1/.
        sequence_folder = next((p for p in (folder, *folder.parents)
                                if p != raw and raw in p.parents and re.fullmatch(r'MVI_\d+', p.name)), None)
        for name in files:
            path = folder / name
            if re.fullmatch(r'MVI_\d+', path.stem) and path.suffix.lower() == '.xml':
                xml_candidates.setdefault(path.stem, []).append(path)
            if sequence_folder is not None and path.suffix.lower() in ('.jpg', '.jpeg'):
                image_files.setdefault(sequence_folder.name, []).append(path)
    xmls, images, rows = {}, {}, []
    expected = set(expected_sequences)
    for sequence in sorted(expected | set(xml_candidates) | set(image_files)):
        candidates = xml_candidates.get(sequence, [])
        paths = image_files.get(sequence, [])
        issues, annotation_frames, frame_paths = [], set(), {}
        # Mỗi sequence cần đúng một XML để tránh chọn nhầm annotation.
        if len(candidates) != 1:
            issues.append('missing_xml' if not candidates else 'duplicate_xml')
        else:
            try:
                for event, element in ET.iterparse(candidates[0], events=('end',)):
                    if element.tag == 'frame':
                        frame = int(element.attrib['num'])
                        if frame in annotation_frames:
                            issues.append('duplicate_annotation_frame')
                        annotation_frames.add(frame)
                    element.clear()
                if not annotation_frames or min(annotation_frames) < 1:
                    issues.append('missing_or_invalid_annotation_frames')
            except (ET.ParseError, KeyError, ValueError) as exc:
                issues.append(f'invalid_xml: {exc}')
        if not paths:
            issues.append('missing_images')
        # Số frame trong tên ảnh phải khớp với frame đã khai báo trong XML.
        for path in sorted(paths):
            match = re.fullmatch(r'(?:img)?(\d+)', path.stem, re.IGNORECASE)
            if not match or int(match[1]) <= 0:
                issues.append(f'invalid_image_name: {path.name}')
            elif int(match[1]) in frame_paths:
                issues.append(f'duplicate_image_frame: {int(match[1])}')
            else:
                frame_paths[int(match[1])] = path
        image_frame_ids = set(frame_paths)
        missing = sorted(annotation_frames - image_frame_ids)
        unannotated = sorted(image_frame_ids - annotation_frames)
        if missing:
            issues.append('annotation_frame_without_image')
        if image_frame_ids and image_frame_ids != set(range(1, max(image_frame_ids) + 1)):
            issues.append('missing_or_invalid_image_frames')
        timeline_length = max(image_frame_ids, default=0)
        coverage = len(annotation_frames & image_frame_ids) / timeline_length if timeline_length else 0.0
        # ready chỉ nghĩa là XML và danh sách frame khớp; chưa thay validation bbox/ảnh.
        row = dict(sequence=sequence, expected=sequence in expected, ready=not issues,
            xml_paths=[str(p) for p in candidates], image_directories=sorted({str(p.parent) for p in paths}),
            num_images=len(paths), num_annotation_frames=len(annotation_frames),
            num_unannotated_images=len(unannotated), annotation_coverage=coverage,
            missing_image_frames=missing, unannotated_image_frames=unannotated,
            extra_image_frames=unannotated, issues=sorted(set(issues)))
        rows.append(row)
        if not issues:
            xmls[sequence], images[sequence] = candidates[0], frame_paths
    # Tổng số thư mục, số sequence đủ frame và số sequence chưa đủ được tách riêng.
    report = dict(raw=str(raw), expected_sequences=len(expected),
        sequences_with_images=sum(r['num_images'] > 0 for r in rows),
        sequences_with_xml=sum(bool(r['xml_paths']) for r in rows),
        incomplete_sequences_with_images=[r['sequence'] for r in rows if r['num_images'] and not r['ready']],
        partial_annotation_sequences=[r['sequence'] for r in rows if r['ready'] and r['num_unannotated_images']],
        invalid_sequences=[r['sequence'] for r in rows if r['expected'] and not r['ready']],
        unannotated_image_frames=sum(r['num_unannotated_images'] for r in rows),
        ready_sequences=sum(r['ready'] and r['expected'] for r in rows),
        missing_images=[r['sequence'] for r in rows if r['expected'] and 'missing_images' in r['issues']],
        missing_xml=[r['sequence'] for r in rows if r['expected'] and 'missing_xml' in r['issues']],
        unexpected_sequences=[r['sequence'] for r in rows if not r['expected']], sequences=rows)
    return report, xmls, images
