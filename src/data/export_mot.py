from .utils import copy_image


def export_mot(annotations, images, sequences, output):
    """Xuất GT MOT và toàn bộ frame liên tục cho tracking."""
    for sequence, meta in sequences.items():
        folder = output / sequence
        (folder / "gt").mkdir(parents=True)
        rows = annotations[annotations.sequence == sequence]
        with (folder / "gt/gt.txt").open("w") as stream:
            for r in rows.itertuples():
                # -1 nghĩa là nguồn không có visibility dạng số; XML gốc vẫn ở canonical.
                stream.write(f"{r.frame_id},{r.track_id},{r.x},{r.y},{r.w},{r.h},1,{r.class_id},-1\n")
        # MOT cần mọi frame liên tục, kể cả frame không có object.
        for frame, source in sorted(images[sequence].items()):
            copy_image(source, folder / "img1" / f"{frame:06d}.jpg")
        (folder / "seqinfo.ini").write_text(
            f"[Sequence]\nname={sequence}\nimDir=img1\nframeRate={meta['fps']}\n"
            f"seqLength={meta['num_frames']}\nimWidth={meta['width']}\nimHeight={meta['height']}\nimExt=.jpg\n")
