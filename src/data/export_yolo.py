import yaml

from .utils import clip_xywh, copy_image, xywh_to_yolo


def export_yolo(annotations, images, sequences, splits, config, classes, output):
    """Chép ảnh đã sampling và xuất nhãn YOLO đã chuẩn hóa."""
    output.mkdir(parents=True, exist_ok=True)
    for part, names in splits.items():
        lines = []
        for sequence in names:
            meta = sequences[sequence]
            # Gom object theo frame để mỗi ảnh chỉ cần một lần tra annotation.
            grouped = {int(f): g for f, g in annotations[annotations.sequence == sequence].groupby("frame_id")}
            annotated_frames = meta["annotated_frame_ids"]
            stride = config[f"{part}_stride"]
            sampled_frames = set(range(1, meta["num_frames"] + 1, stride))
            for frame in annotated_frames:
                if frame not in sampled_frames:
                    continue
                relative = f"{sequence}/img{frame:05d}"
                copy_image(images[sequence][frame], output / "images" / f"{relative}.jpg")
                label = output / "labels" / f"{relative}.txt"
                label.parent.mkdir(parents=True, exist_ok=True)
                rows = grouped.get(frame)
                # Clip truncation boxes only for YOLO's normalized labels.
                text = []
                if rows is not None:
                    for row in rows.itertuples():
                        box = clip_xywh(
                            row.x, row.y, row.w, row.h,
                            meta["width"], meta["height"],
                        )
                        values = xywh_to_yolo(
                            *box, meta["width"], meta["height"]
                        )
                        text.append(
                            f"{row.class_id} "
                            + " ".join(f"{value:.9f}" for value in values)
                        )
                label.write_text("\n".join(text) + ("\n" if text else ""))
                lines.append(f"./images/{relative}.jpg")
        (output / f"{part}.txt").write_text("\n".join(lines) + "\n")
    # Danh sách dùng đường dẫn tương đối; rebase_dataset.py cập nhật path sau khi giải nén.
    (output / "data.yaml").write_text(yaml.safe_dump(dict(path=str(output.resolve()),
        train="train.txt", val="val.txt", test="test.txt", names=classes), sort_keys=False))
