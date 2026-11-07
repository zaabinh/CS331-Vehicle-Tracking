import logging
import math
import configparser
from PIL import Image
from .split import validate_splits
from .utils import clip_xywh, write_json

LOGGER = logging.getLogger(__name__)


def validate_canonical(annotations, images, sequences, classes):
    """Kiểm tra ảnh, bbox, class và track trước khi dùng dataset."""
    errors, checked, out_of_frame = [], 0, 0
    total_sequences = len(sequences)
    total_images = sum(len(sequence_images) for sequence_images in images.values())
    LOGGER.info(
        "Validating %s sequences, %s images, and %s annotations",
        total_sequences,
        total_images,
        len(annotations),
    )
    for index, (sequence, meta) in enumerate(sequences.items(), start=1):
        sequence_images = images[sequence]
        LOGGER.info(
            "Validating images %s (%s/%s; %s files)",
            sequence,
            index,
            total_sequences,
            len(sequence_images),
        )
        if set(sequence_images) != set(range(1, meta["num_frames"] + 1)):
            errors.append(f"{sequence}: missing/extra image frames")
        for frame, path in sequence_images.items():
            checked += 1
            try:
                with Image.open(path) as image:
                    if image.size != (meta["width"], meta["height"]):
                        errors.append(f"Resolution mismatch: {path}")
                    image.load()
            except Exception as exc:
                errors.append(f"Unreadable {path}: {exc}")
    # Một track không thể có hai bbox trong cùng frame của một sequence.
    duplicates = int(annotations.duplicated(["sequence", "frame_id", "track_id"]).sum())
    if duplicates:
        errors.append(f"Duplicate frame/track pairs: {duplicates}")
    annotation_groups = list(annotations.groupby("sequence"))
    for index, (sequence, rows) in enumerate(annotation_groups, start=1):
        LOGGER.info(
            "Validating annotations %s (%s/%s; %s objects)",
            sequence,
            index,
            len(annotation_groups),
            len(rows),
        )
        m = sequences[sequence]
        intersects_image = ((rows.x < m["width"]) & (rows.y < m["height"])
                            & (rows.x + rows.w > 0) & (rows.y + rows.h > 0))
        valid = ((rows.w > 0) & (rows.h > 0) & intersects_image
                 & (rows.frame_id > 0) & (rows.frame_id <= m["num_frames"])
                 & (rows.track_id > 0) & rows.class_id.isin(classes))
        valid &= rows[["x", "y", "w", "h"]].apply(lambda col: col.map(math.isfinite)).all(axis=1)
        if not valid.all():
            errors.append(f"Invalid annotations {sequence}: {rows.loc[~valid].head(5).to_dict('records')}")
        out_of_frame += int((~((rows.x >= 0) & (rows.y >= 0)
                              & (rows.x + rows.w <= m["width"])
                              & (rows.y + rows.h <= m["height"]))).sum())
    LOGGER.info(
        "Canonical validation scan finished: %s images checked, %s errors",
        checked,
        len(errors),
    )
    return dict(images_checked=checked, duplicate_tracks=duplicates,
                out_of_frame_boxes=out_of_frame, errors=errors)


def validate_exports(output, annotations, images, sequences, splits, config, classes):
    """Đối chiếu file export với canonical và ghi validation report."""
    report = validate_canonical(annotations, images, sequences, classes)
    errors = report["errors"]
    try:
        validate_splits(splits)
    except ValueError as exc:
        errors.append(str(exc))
    # Đối chiếu export ngược về canonical để bắt lỗi lệch tọa độ/class.
    counts = annotations.groupby(["sequence", "frame_id"]).size().to_dict()
    # Work sequence-by-sequence to avoid allocating a DataFrame for every frame at once.
    cached_sequence, frame_rows = None, {}
    for part, names in splits.items():
        paths = (output / "detection" / f"{part}.txt").read_text().splitlines()
        expected = [
            f"./images/{sequence}/img{frame:05d}.jpg"
            for sequence in names
            for frame in sequences[sequence]["annotated_frame_ids"]
            if (frame - 1) % config[f"{part}_stride"] == 0
        ]
        if paths != expected:
            errors.append(f"Detection frame manifest mismatch: {part}")
        for relative in paths:
            image = output / "detection" / relative
            label = output / "detection/labels" / image.parent.name / (image.stem + ".txt")
            if not image.is_file() or not label.is_file():
                errors.append(f"Missing image/label {relative}")
                continue
            rows = label.read_text().splitlines()
            if cached_sequence != image.parent.name:
                cached_sequence = image.parent.name
                frame_rows = {int(f): g for f, g in annotations[annotations.sequence == cached_sequence].groupby('frame_id')}
            if len(rows) != counts.get((image.parent.name, int(image.stem[3:])), 0):
                errors.append(f"Label count mismatch {relative}")
            for row in rows:
                v = list(map(float, row.split()))
                if (len(v) != 5 or v[0] not in classes or
                    not all(math.isfinite(n) and 0 <= n <= 1 for n in v[1:]) or min(v[3:]) <= 0):
                    errors.append(f"Invalid YOLO: {relative}: {row}")
            source_rows = frame_rows.get(int(image.stem[3:]))
            if source_rows is not None:
                m = sequences[image.parent.name]
                for row, r in zip(rows, source_rows.itertuples()):
                    cls, xc, yc, w, h = map(float, row.split())
                    decoded = [(xc - w/2)*m['width'], (yc-h/2)*m['height'], w*m['width'], h*m['height']]
                    expected_box = clip_xywh(
                        r.x, r.y, r.w, r.h, m["width"], m["height"])
                    if cls != r.class_id or any(
                            abs(v - ref) > 1e-4
                            for v, ref in zip(decoded, expected_box)):
                        errors.append(f"YOLO/canonical mismatch: {relative}/{r.track_id}")
    for sequence, meta in sequences.items():
        folder = output / "tracking" / sequence
        ini = configparser.ConfigParser()
        ini.read(folder / 'seqinfo.ini')
        expected_info = dict(name=sequence, imdir='img1', framerate=str(meta['fps']),
            seqlength=str(meta['num_frames']), imwidth=str(meta['width']), imheight=str(meta['height']), imext='.jpg')
        if not ini.has_section('Sequence') or dict(ini['Sequence']) != expected_info:
            errors.append(f"Invalid seqinfo: {sequence}")
        if set(p.name for p in (folder / "img1").glob("*.jpg")) != {f"{f:06d}.jpg" for f in range(1, meta["num_frames"] + 1)}:
            errors.append(f"Tracking frame mismatch: {sequence}")
        actual = (folder / "gt/gt.txt").read_text().splitlines()
        source = annotations[annotations.sequence == sequence]
        if len(actual) != len(source):
            errors.append(f"Tracking annotation count mismatch: {sequence}")
        for line, r in zip(actual, source.itertuples()):
            if list(map(float, line.split(","))) != [r.frame_id, r.track_id, r.x, r.y, r.w, r.h, 1, r.class_id, -1]:
                errors.append(f"Tracking row mismatch: {sequence}/{r.frame_id}")
    report["status"] = "FAILED" if errors else "PASSED"
    write_json(output / "metadata/validation_report.json", report)
    if errors:
        raise ValueError(f"Validation FAILED: {errors[:10]}")
    return report
