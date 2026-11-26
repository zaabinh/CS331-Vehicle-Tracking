# Local compatibility patches

The project evaluator uses bundled TrackEval HOTA, CLEAR and Identity, with
their original aggregation implementations. Removed NumPy aliases are changed
from `np.float` to `float` in HOTA and `np.int` to `int` in Identity. No metric
formulas or aggregation functions are changed. The UA-DETRAC thresholds and
masks live in the adapter.

`metrics/clear.py` also resets `prev_timestep_tracker_id` in both empty-frame
branches. Without this state reset, the three-frame regression
GT=[1,1,1], tracker=[1,empty,1] returns Frag=0 rather than 1: early `continue`
retains the previous frame's match. The reset makes those branches agree with
the regular per-frame update and removes stale immediate-frame matching
priority. `prev_tracker_id` is retained for identity switches across gaps.
This correctness patch can affect Frag and CLEAR matching; it is explicitly
recorded in the project protocol and its cache fingerprint. A regression test
covers a fully empty prediction frame. HOTA and Identity logic are unchanged.

The corresponding [TrackEval reference source](https://github.com/JonathonLuiten/TrackEval/blob/master/trackeval/metrics/clear.py)
was inspected on 2026-11-23; it has the same early-return branches. This is a
documented local patch rather than an assertion of exact unmodified CLEAR
behavior. No previously published benchmark scores should be compared without
disclosing this patch.

Metric modules load through an isolated namespace to avoid unrelated dataset
adapters and optional dependencies. The tracker and Kalman source are unchanged.
