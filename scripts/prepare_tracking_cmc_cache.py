"""Prepare independent test CMC caches after freezing the val-selected config."""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.tracking.common import (
    find_sequence_images,
    read_sequences,
    sha256_file,
    write_json,
)
from src.tracking.detections import detections_by_frame, load_cached_detections
from src.tracking.gmc import load_or_estimate_gmc


def prepare_sequence(job):
    """Use the same cache writer as tracking; isolate OpenCV RNG per process."""
    import cv2
    import numpy as np

    cv2.setNumThreads(1)
    sequence, frames, output = job
    source = ROOT / "outputs/detections_full_yolo26l_E17" / f"{sequence}.csv"
    dataset = ROOT / "outputs/ua_detrac/dataset_v1.1"
    begin = time.perf_counter()
    rows, _ = load_cached_detections(
        source, sequence, frames, output / "detections"
    )
    transforms, provenance = load_or_estimate_gmc(
        sequence,
        find_sequence_images(dataset, sequence),
        detections_by_frame(rows),
        frames,
        output / "gmc",
        sha256_file(source),
    )
    assert transforms.shape == (frames + 1, 2, 3)
    assert np.isfinite(transforms).all()
    return dict(
        sequence=sequence,
        frames=frames,
        seconds=time.perf_counter() - begin,
        identity_fallbacks=len(provenance["failures"]),
        provenance=provenance,
        opencv_threads=cv2.getNumThreads(),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "outputs/tracking/cmc_v3"
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("workers must be positive")
    selection_path = args.output / "val_selection.json"
    selected = json.loads(selection_path.read_text(encoding="utf-8"))
    if not selected["cmc_selected"]:
        raise ValueError(
            "CMC did not win on val; do not prepare a test experiment"
        )
    metadata = read_sequences(ROOT / "outputs/ua_detrac/dataset_v1.1")
    names = sorted(s for s, row in metadata.items() if row["split"] == "test")
    if len(names) != 40:
        raise ValueError("Require all 40 fixed test sequences")
    lock = dict(
        selection_sha256=sha256_file(selection_path),
        config=selected["config"],
        sequences=names,
        workers=args.workers,
        opencv_threads_per_worker=1,
        script_sha256=sha256_file(__file__),
        gmc_sha256=sha256_file(ROOT / "src/tracking/gmc.py"),
        test_metrics_used_for_selection=False,
    )
    path = args.output / "test_preparation_lock.json"
    if path.exists() and json.loads(path.read_text()) != lock:
        raise ValueError(
            "Preparation settings changed; use a new output directory"
        )
    write_json(path, lock)
    begin = time.perf_counter()
    jobs = [
        (s, int(metadata[s]["num_frames"]), args.output / "runs/cache")
        for s in names
    ]
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(prepare_sequence, job): job[0] for job in jobs}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(
                f"[CMC CACHE] {len(results)}/40 {result['sequence']}: "
                f"{result['seconds']:.1f}s, "
                f"fallbacks={result['identity_fallbacks']}",
                flush=True,
            )
            write_json(args.output / "test_preparation_progress.json", results)
    write_json(
        args.output / "test_preparation_complete.json",
        dict(
            status="complete",
            wall_seconds=time.perf_counter() - begin,
            sequence_count=len(results),
            frames=sum(r["frames"] for r in results),
            sequence_seconds_sum=sum(r["seconds"] for r in results),
            selection_sha256=lock["selection_sha256"],
            results=results,
        ),
    )


if __name__ == "__main__":
    main()
