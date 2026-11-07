import pandas as pd


def build_canonical(tables, classes, output):
    """Ghép annotation, gán class ID và lưu bảng chuẩn."""
    output.mkdir(parents=True, exist_ok=True)
    annotations = pd.concat(tables, ignore_index=True)
    # Chuẩn hóa tên class sang ID duy nhất trước khi export.
    mapping = {name: int(i) for i, name in classes.items()}
    unknown = set(annotations.class_name) - set(mapping)
    if unknown:
        raise ValueError(f"Unknown vehicle types: {unknown}")
    annotations["class_id"] = annotations.class_name.map(mapping).astype(int)
    annotations.sort_values(["sequence", "frame_id", "track_id"], inplace=True)
    annotations.to_csv(output / "annotations.csv", index=False)
    return annotations
