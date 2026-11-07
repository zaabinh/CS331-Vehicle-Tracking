import pandas as pd


def generate_statistics(annotations, sequences, splits, classes, output):
    """Tạo thống kê object, class và track cho từng split."""
    rows = []
    for part, names in splits.items():
        a = annotations[annotations.sequence.isin(names)]
        frames = sum(sequences[s]["num_frames"] for s in names)
        annotated_frames = sum(sequences[s]["num_annotated_frames"] for s in names)
        # Bổ sung frame không có object bằng 0 khi tính objects/frame.
        counts = a.groupby(["sequence", "frame_id"]).size().tolist()
        counts += [0] * (frames - len(counts))
        lengths = a.groupby(["sequence", "track_id"]).size()
        rows.append(dict(split=part, num_sequences=len(names), num_frames=frames,
            num_annotated_frames=annotated_frames,
            num_unannotated_frames=frames - annotated_frames,
            annotation_coverage=annotated_frames / frames if frames else 0,
            num_objects=len(a), num_tracks=len(lengths),
            **{f"{name}_count": int((a.class_name == name).sum()) for name in classes.values()},
            mean_objects_per_frame=len(a) / frames if frames else 0,
            median_objects_per_frame=pd.Series(counts, dtype=float).median(),
            mean_bbox_width=a.w.mean(), mean_bbox_height=a.h.mean(), mean_bbox_area=(a.w * a.h).mean(),
            median_track_length=lengths.median(),
            occlusion_count=int((a.occlusion_ratio > 0).sum()),
            occlusion_unknown=int(a.occlusion_ratio.isna().sum()),
            occlusion_region_count=int(a.occlusion_region_count.sum()),
            truncation_count=int((a.truncation > 0).sum()), truncation_unknown=int(a.truncation.isna().sum())))
    pd.DataFrame(rows).to_csv(output / "dataset_stats.csv", index=False)
    annotations.groupby("class_name").size().to_csv(output / "class_distribution.csv", header=["count"])
    annotations.groupby(["sequence", "track_id"]).size().to_csv(output / "track_lengths.csv", header=["length"])
    annotations.groupby(["sequence", "frame_id"]).size().to_csv(output / "objects_per_frame.csv", header=["count"])
