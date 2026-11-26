"""Small shared helpers for reproducible tracking runs."""

import hashlib
import json
import platform
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = ROOT / "UCMC/UCMCTrack"
CLASS_NAMES = {0: "car", 1: "bus", 2: "van", 3: "others"}
BASELINE = dict(
    conf=0.1,
    high_score=0.6,
    a1=10.0,
    a2=10.0,
    wx=0.1,
    wy=0.1,
    vmax=0.5,
    max_age=30,
    fps=25,
    hp=False,
    interpolation=False,
    cmc=False,
    preview_frames=0,
)


def environment_versions():
    """Record numerical dependencies so a changed environment invalidates reuse."""
    versions = dict(
        python=platform.python_version(), platform=platform.platform()
    )
    for package in (
        "numpy",
        "scipy",
        "pandas",
        "filterpy",
        "lap",
        "opencv-python",
        "opencv-python-headless",
    ):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            pass
    return versions


def sha256_file(path):
    """Hash file contents in bounded memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(value):
    """Hash JSON-compatible settings deterministically."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True).encode()
    ).hexdigest()


def write_json(path, value):
    """Replace a JSON report atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def read_sequences(dataset):
    """Read the prepared dataset's timeline, without inferring it from detections."""
    table = pd.read_csv(Path(dataset) / "metadata/sequences.csv")
    if table.sequence.duplicated().any():
        raise ValueError("Duplicate sequence metadata")
    return table.set_index("sequence").to_dict("index")


def resolve_config(config=None):
    """Apply the fixed baseline and validate operational options."""
    config = dict(config or {})
    if set(config) - set(BASELINE):
        raise ValueError(f"Unknown options: {set(config) - set(BASELINE)}")
    result = {**BASELINE, **config}
    import math

    for key in ("conf", "high_score", "a1", "a2", "wx", "wy", "vmax"):
        if not math.isfinite(result[key]):
            raise ValueError(f"Nonfinite {key}")
    if not 0.05 <= result["conf"] <= result["high_score"] <= 1:
        raise ValueError("Require 0.05 <= conf <= high_score <= 1")
    if result["fps"] != 25:
        raise ValueError("UA-DETRAC runs require 25 FPS")
    for key in ("a1", "a2", "wx", "wy", "vmax"):
        if result[key] <= 0:
            raise ValueError(f"{key} must be positive")
    for key in ("max_age", "preview_frames"):
        if not isinstance(result[key], int) or result[key] < (
            key == "max_age"
        ):
            raise ValueError(f"Invalid {key}")
    for key in ("hp", "interpolation", "cmc"):
        if not isinstance(result[key], bool):
            raise ValueError(f"{key} must be boolean")
    return result


def find_sequence_images(dataset, sequence, split=""):
    """Return the original frame numbers from the prepared tracking images."""
    folder = Path(dataset) / "tracking" / sequence / "img1"
    if not folder.is_dir():
        raise FileNotFoundError(folder)
    return {int(p.stem): p for p in folder.glob("*.jpg")}


def find_detection_csv(root, sequence):
    """Resolve the explicit detector handoff convention."""
    path = Path(root) / f"{sequence}.csv"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path
