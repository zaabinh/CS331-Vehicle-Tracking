# CS331 Vehicle Tracking

UA-DETRAC preparation for Detection (YOLO) and Tracking (MOT-style).

Start with [Colab pipeline notebook](notebooks/data/00b_run_pipeline.ipynb), using the raw paths from [download notebook](notebooks/data/00_data_preparation.ipynb).

See [data implementation guide](docs/DATA_IMPLEMENTATION_GUIDE.md) for fixed splits, validation, packaging, Colab setup, and reproducible small-data experiments.

For practical handoff instructions, see the [team usage guide](docs/TEAM_USAGE_GUIDE.md), including separate workflows and data contracts for Detection and Tracking members.

For camera calibration, UCMCTrack smoke runs, class-aware evaluation and bounded
validation tuning, see the [tracking usage guide](docs/TRACKING_USAGE_GUIDE.md)
and [tracking notebooks](notebooks/tracking).

Initialize the pinned UCMCTrack dependency and its documented evaluation fixes:

```bash
git submodule update --init --recursive
python scripts/setup_ucmc.py
pip install -r requirements-tracking.txt
```

Compatibility changes are stored in `patches/ucmc_compat.patch`; see
[patch details](docs/UCMC_LOCAL_PATCHES.md). Applying the patch intentionally
leaves the submodule working tree modified relative to the upstream commit.

Approved homography tuning completed 15 configurations on 12 validation
sequences: HOTA 75.864, IDF1 84.631, MOTA 72.667. These are development-validation
scores, not the original UA-DETRAC PR-MOTA benchmark. Tracking outputs and the
approved camera registry are delivered separately in the
[project Drive folder](https://drive.google.com/drive/folders/1RmtLDxNiZFKA7VztYEd8oYvOqE8yzRQK).
