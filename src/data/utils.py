import csv
import hashlib
import json
import shutil
from pathlib import Path

import yaml


def read_yaml(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_csv(path, rows, columns):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def copy_image(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def xywh_to_yolo(x, y, w, h, width, height):
    return ((x + w / 2) / width, (y + h / 2) / height, w / width, h / height)


def clip_xywh(x, y, w, h, width, height):
    """Clip an XYWH box to the image while keeping canonical coordinates intact."""
    left = max(0.0, x)
    top = max(0.0, y)
    right = min(float(width), x + w)
    bottom = min(float(height), y + h)
    if right <= left or bottom <= top:
        raise ValueError("Bounding box does not intersect the image")
    return left, top, right - left, bottom - top
