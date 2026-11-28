"""Package validated tracking outputs, reports, calibration and a code snapshot."""

import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.tracking.common import sha256_file, write_json


def files(directory):
    return [p for p in Path(directory).rglob("*") if p.is_file()]


def archive(path, sources, metadata=None):
    """Use workspace-relative paths and verify every archived member's CRC."""
    sources = sorted(set(sources))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for source in sources:
            source.resolve().relative_to(ROOT.resolve())
            zipped.write(source, source.relative_to(ROOT).as_posix())
        if metadata:
            zipped.writestr("SOURCE_SNAPSHOT.json", json.dumps(metadata, indent=2))
    with zipfile.ZipFile(path) as zipped:
        if zipped.testzip() is not None:
            raise AssertionError(f"Archive integrity failed: {path}")
        members = len(zipped.infolist())
    return dict(
        file_name=path.name,
        bytes=path.stat().st_size,
        sha256=sha256_file(path),
        members=members,
        crc_verified=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "outputs/tracking/delivery_v3"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    test = ROOT / "outputs/tracking/test_final_v3"
    if json.loads((test / "completion.json").read_text())["status"] != "complete":
        raise ValueError("Complete the test run and evaluation before packaging")
    if not all(json.loads((test / "diagnostic_agreement.json").read_text()).values()):
        raise ValueError("Test diagnostics do not agree with evaluation")
    if not json.loads((output / "bundle_verification.json").read_text())["all_match"]:
        raise ValueError("Verify the exported evaluator inputs before packaging")
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    upstream = ROOT / "UCMC/UCMCTrack"
    upstream_revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=upstream, text=True
    ).strip()
    reports = list((ROOT / "docs").glob("TRACKING*.md")) + [
        ROOT / "docs/UCMC_LOCAL_PATCHES.md"
    ]
    reports += files(ROOT / "docs/assets/tracking_reports")
    val = ROOT / "outputs/tracking/refinement_v3"
    analysis = reports + [p for p in val.iterdir() if p.is_file()]
    analysis += files(val / "configs") + files(val / "report_figures")
    for run in (val / "runs").iterdir():
        if not (run / "run_manifest.json").exists():
            continue
        analysis += [run / "run_manifest.json", run / "trial.json"]
        analysis += files(run / "evaluation") + files(run / "sequence_reports")
        if run.name in ("control", "shared_vote_boundary_10"):
            analysis += files(run / "tracks") + files(run / "mot")
    error_details = files(test / "error_audit") + files(test / "detection_audit")
    analysis += files(test / "report_figures") + files(test / "difficulty")
    analysis += [p for p in test.iterdir() if p.is_file()]
    analysis += files(output / "bundle_verification")
    analysis += [
        output / name
        for name in (
            "bundle_verification.json",
            "validation_checks.json",
            "notebook_preservation.json",
            "report_asset_manifest.json",
            "test_report_asset_manifest.json",
        )
    ]
    analysis += [
        ROOT / "outputs/tracking/LOCAL_EXECUTION_REPORT.md",
        ROOT / "outputs/tracking/tuning_h_approved_v1/LOCAL_TUNING_REPORT.md",
    ]
    calibration = files(ROOT / "outputs/tracking/test_calibration_draft")
    calibration += [
        ROOT / "outputs/tracking/calibration/registry_approved_h_v1.json",
        ROOT / "outputs/tracking/calibration/approval_h_v1.json",
    ]
    # Stage/commit the requested code first; archive only tracked code, never raw data.
    code = []
    for repository, prefix in [(ROOT, ROOT), (upstream, upstream)]:
        records = (
            subprocess.check_output(
                ["git", "ls-files", "--stage", "-z"], cwd=repository
            )
            .decode()
            .split("\0")
        )
        for record in records:
            if record:
                mode, relative = record.split(" ", 1)[0], record.split("\t", 1)[1]
                # Keep source, omitting upstream demonstration data unrelated to UA.
                if repository == upstream and (
                    relative.startswith("det_results/") or relative == "demo/demo.gif"
                ):
                    continue
                if mode != "160000":
                    code.append(prefix / relative)
    snapshot = dict(
        source_commit=revision,
        upstream_commit=upstream_revision,
        upstream_compatibility_patch="patches/ucmc_compat.patch",
        clone_contains_compatibility_patch=True,
        raw_dataset_included=False,
        notebook_outputs_in_code=False,
        omitted_upstream_demo_data=["det_results/", "demo/demo.gif"],
        val_selection_sha256=sha256_file(val / "best_config.json"),
        test_selection_lock_sha256=sha256_file(test / "selection_lock.json"),
        evaluator_protocol_version=2,
        test_camera_review_complete=False,
    )
    groups = [
        ("trackeval_val_test", files(output / "trackeval_bundle")),
        ("analysis", analysis),
        ("test_error_details", error_details),
        ("camera_proposals", calibration),
        ("code", code),
    ]
    backup = ROOT / "outputs/tracking/validation_v2/notebook_executed_v2.ipynb"
    if backup.exists():
        groups.append(
            ("executed_notebook_v2", [backup, output / "notebook_preservation.json"])
        )
    records = []
    for role, members in groups:
        path = output / f"ua_detrac_ucmc_v3_{role}_{revision[:7]}.zip"
        record = archive(path, members, snapshot if role == "code" else None)
        records.append(dict(role=role, **record))
        print(json.dumps(records[-1]))
    write_json(output / "archives.json", dict(source_commit=revision, archives=records))
    (output / "SHA256SUMS.txt").write_text(
        "".join(f"{r['sha256']}  {r['file_name']}\n" for r in records), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
