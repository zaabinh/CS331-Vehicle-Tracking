"""Optional correction for upstream unmatched confirmed-track expiry."""


def update_tracker(tracker, detections, frame_id, fix_coasting=False):
    """Preserve upstream matching; expire unmatched tracks even without low scores."""
    tracker.update(detections, frame_id)
    if not fix_coasting:
        return
    # Upstream misses this transition when the low-score association has no pairs.
    for track in tracker.trackers:
        if track.detidx < 0 and track.status.name == "Confirmed":
            track.status = type(track.status).Coasted
    tracker.trackers = [
        track
        for track in tracker.trackers
        if not (track.status.name == "Coasted" and track.death_count >= tracker.max_age)
    ]
    tracker.update_status(detections)
