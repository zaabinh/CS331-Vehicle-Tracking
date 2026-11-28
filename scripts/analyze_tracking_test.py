"""Build test error statistics and visual cases from the frozen saved run."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image

from src.analysis.tracking_cases import diagnose, draw_frame, window_scores
from src.tracking.common import CLASS_NAMES, write_json
from src.tracking.evaluation import GT_COLUMNS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "outputs/ua_detrac/dataset_v1.1"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "outputs/tracking/test_final_v3"
    )
    args = parser.parse_args()
    run = args.output / "run"
    summary = json.loads((run / "evaluation/summary.json").read_text())
    sequence = pd.read_csv(run / "evaluation/per_sequence.csv").set_index("sequence")
    classes = pd.read_csv(run / "evaluation/per_class.csv")
    metadata = pd.read_csv(args.dataset / "metadata/sequences.csv").set_index(
        "sequence"
    )
    manifest = json.loads((run / "run_manifest.json").read_text())
    lock = json.loads((args.output / "selection_lock.json").read_text())
    sequence["calibration_status"] = [
        manifest["provenance"]["cameras"][name]["status"] for name in sequence.index
    ]
    for system, groups in lock["difficulty"].items():
        membership = {name: level for level, names in groups.items() for name in names}
        sequence[system + "_difficulty"] = sequence.index.map(membership)
    sequence = sequence.join(
        metadata[["sence_weather", "camera_state", "annotation_coverage"]]
    )
    sequence.to_csv(args.output / "sequence_analysis.csv")
    directory = args.output / "error_audit"
    figures = args.output / "report_figures"
    figures.mkdir(exist_ok=True)
    windows, checks, border, misses = [], {}, [], []
    for name in summary["sequences"]:
        frames = pd.read_csv(directory / f"{name}_frames.csv").set_index("frame_id")
        actual = frames[["FP", "FN", "IDS", "Frag"]].sum()
        checks[name] = bool(
            all(int(actual[k]) == int(sequence.loc[name, k]) for k in actual.index)
        )
        if not checks[name]:
            raise AssertionError(f"Event counts differ for {name}")
        candidates = window_scores(frames)
        candidates["sequence"] = name
        windows.append(candidates)
        fn = pd.read_csv(directory / f"{name}_fn_candidates.csv")
        fn["sequence"] = name
        misses.append(fn)
        events = pd.read_csv(directory / f"{name}_events.csv")
        pred = pd.read_csv(run / "tracks" / f"{name}.csv")
        selected = pred.loc[events.loc[events.kind.eq("FP"), "pred_index"].astype(int)]
        at_edge = (
            selected.x.le(2)
            | selected.y.le(2)
            | (selected.x + selected.w).ge(metadata.loc[name, "width"] - 2)
            | (selected.y + selected.h).ge(metadata.loc[name, "height"] - 2)
        )
        border.append(
            dict(sequence=name, FP=len(selected), FP_near_border=int(at_edge.sum()))
        )
    all_windows = pd.concat(windows, ignore_index=True)
    all_windows.to_csv(args.output / "window_scores.csv", index=False)
    chosen = {
        "best": all_windows.sort_values(
            ["error_rate", "GT"], ascending=[True, False]
        ).iloc[0],
        "worst": all_windows.sort_values(
            ["error_rate", "GT"], ascending=[False, False]
        ).iloc[0],
    }
    pd.DataFrame(chosen).T.to_csv(
        args.output / "selected_cases.csv", index_label="case"
    )
    write_json(args.output / "diagnostic_agreement.json", checks)
    pd.DataFrame(border).to_csv(args.output / "fp_border.csv", index=False)
    fn = pd.concat(misses, ignore_index=True)
    if len(fn) != summary["FN"]:
        raise AssertionError("Attributed FN do not sum to the frozen run's FN")
    fn["height_bin"] = pd.cut(
        fn.h, [0, 16, 32, 64, np.inf], labels=["0-16", "16-32", "32-64", "64+"]
    )
    fn.groupby(["class_id", "height_bin"], observed=True).size().rename("FN").to_csv(
        args.output / "fn_class_size.csv"
    )
    grouped = fn.category.value_counts().rename_axis("category").to_frame("FN")
    grouped["percentage"] = 100 * grouped.FN / summary["FN"]
    grouped.to_csv(args.output / "fn_category_summary.csv")
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.barh(grouped.index[::-1], grouped.FN.iloc[::-1], color="#168c87")
    ax.set_xlabel("FN observations (hierarchical detector-candidate diagnostics)")
    fig.tight_layout()
    fig.savefig(figures / "fn_categories.png", dpi=150)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(10, 10))
    ordered = sequence.sort_values("HOTA")
    ax.barh(ordered.index, ordered.HOTA, color="#168c87")
    ax.set(xlim=(0, 100), xlabel="HOTA (0-100), each sequence")
    fig.tight_layout()
    fig.savefig(figures / "sequence_hota.png", dpi=150)
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    axes[0].bar(classes.class_name, classes.HOTA, color="#168c87")
    axes[0].set(ylim=(0, 100), ylabel="HOTA (0-100)")
    x = np.arange(len(classes))
    axes[1].bar(x - 0.18, classes.FP, width=0.36, label="FP", color="#d85858")
    axes[1].bar(x + 0.18, classes.FN, width=0.36, label="FN", color="#e9a64a")
    axes[1].set(xticks=x, xticklabels=classes.class_name, ylabel="Observations")
    axes[1].legend()
    fig.tight_layout()
    fig.savefig(figures / "class_errors.png", dpi=150)
    plt.close(fig)
    difficulty = pd.read_csv(args.output / "difficulty_summary.csv")
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    for ax, system in zip(axes, ["tracking", "detection"]):
        table = difficulty[difficulty.system.eq(system)]
        ax.bar(table.difficulty, table.HOTA, color="#168c87")
        ax.set(
            title=f"{system.title()} toolkit membership",
            ylim=(0, 100),
            ylabel="HOTA (0-100)",
        )
    fig.tight_layout()
    fig.savefig(figures / "difficulty_hota.png", dpi=150)
    plt.close(fig)
    names = {str(case.sequence) for case in chosen.values()}
    names.add(str(sequence.IDS.idxmax()))
    canonical = pd.concat(
        [
            c[c.sequence.isin(names)]
            for c in pd.read_csv(
                args.dataset / "canonical/annotations.csv",
                usecols=GT_COLUMNS,
                chunksize=100_000,
            )
        ]
    )
    annotated = pd.read_csv(args.dataset / "metadata/annotated_frames.csv")
    ignored = pd.read_csv(args.dataset / "canonical/ignored_regions.csv")
    analyses = {}
    for name in names:
        info = metadata.loc[name]
        analyses[name] = diagnose(
            canonical[canonical.sequence.eq(name)],
            pd.read_csv(run / "tracks" / f"{name}.csv"),
            annotated.loc[annotated.sequence.eq(name), "frame_id"],
            int(info.num_frames),
            ignored.loc[ignored.sequence.eq(name), ["x", "y", "w", "h"]].to_numpy(),
            int(info.width),
            int(info.height),
            protocol_version=2,
        )
    for label, case in chosen.items():
        name, start, end = str(case.sequence), int(case.start), int(case.end)
        result = analyses[name]
        local = result["frames"].loc[start:end]
        peak = int((local.FP + local.FN + local.IDS).idxmax())
        if peak in (start, end):
            peak = (start + end) // 2
        pictures = [
            draw_frame(
                args.dataset / "tracking" / name / "img1" / f"{f:06d}.jpg",
                result,
                f,
                f"Test {name} frame {f}: {label} window",
            )
            for f in [start, peak, end]
        ]
        canvas = Image.new("RGB", (pictures[0].width, sum(p.height for p in pictures)))
        top = 0
        for picture in pictures:
            canvas.paste(picture, (0, top))
            top += picture.height
        canvas.save(figures / f"{label}_window.png")
        clip = []
        for frame in range(start, end + 1, 5):
            picture = draw_frame(
                args.dataset / "tracking" / name / "img1" / f"{frame:06d}.jpg",
                result,
                frame,
                f"Test {name} frame {frame}",
            )
            clip.append(
                picture.resize((800, round(picture.height * 800 / picture.width)))
            )
        clip[0].save(
            figures / f"{label}_window.gif",
            save_all=True,
            append_images=clip[1:],
            duration=200,
            loop=0,
        )
    name = str(sequence.IDS.idxmax())
    events = analyses[name]["events"]
    switch = events[events.kind.eq("IDS")].iloc[0]
    write_json(
        args.output / "identity_example.json",
        dict(
            sequence=name,
            frame_id=int(switch.frame_id),
            class_id=int(switch.class_id),
            class_name=CLASS_NAMES[int(switch.class_id)],
            gt_id=int(switch.gt_id),
            previous_pred_id=int(switch.previous_pred_id),
            pred_id=int(switch.pred_id),
            previous_match_frame=int(switch.previous_match_frame),
            iou=float(switch.iou),
        ),
    )
    frame = int(switch.frame_id)
    draw_frame(
        args.dataset / "tracking" / name / "img1" / f"{frame:06d}.jpg",
        analyses[name],
        frame,
        f"Test {name}: identity switch",
    ).save(figures / "identity_switch.png")
    print(grouped.to_string())
    print(pd.DataFrame(chosen).T.to_string())
    print("Worst sequences:", sequence.sort_values("HOTA").head(5).to_string())


if __name__ == "__main__":
    main()
