"""The selected artifact must be usable without overwriting historical results."""

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.revalidate_tracking import finalize


class ValidationDeliveryTest(unittest.TestCase):
    def test_deliver_winner_and_baseline_without_overwriting_v1(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, output = root / "old", root / "new"
            rows = []
            for config_id, role, score, conf in [
                ("base", "baseline", 50, 0.1),
                ("best", "selected", 80, 0.05),
            ]:
                run = source / "runs" / config_id
                (run / "tracks").mkdir(parents=True)
                (run / "tracks/S.csv").write_text(config_id)
                (run / "run_manifest.json").write_text("{}")
                (run / "evaluation").mkdir()
                historical = run / "evaluation/protocol.json"
                historical.write_text('{"version": 1}')
                evaluation = output / "reevaluation" / config_id
                evaluation.mkdir(parents=True)
                (evaluation / "protocol.json").write_text('{"version": 2}')
                rows.append(
                    dict(
                        config_id=config_id,
                        role=role,
                        HOTA=score,
                        IDF1=score,
                        IDS=1,
                        runtime_seconds=1,
                        config_json=json.dumps({"conf": conf}),
                        source_run=str(run),
                        evaluation_dir=str(evaluation),
                    )
                )
            pd.DataFrame(rows).to_csv(output / "leaderboard.csv", index=False)
            # Verification is the same configuration, not a third tuning trial.
            pd.DataFrame(
                [
                    dict(
                        role="reproduce_v2_winner",
                        HOTA=80,
                        IDF1=80,
                        IDS=1,
                        runtime_seconds=2,
                        config_json=json.dumps({"conf": 0.05}),
                        run_dir=rows[1]["source_run"],
                    )
                ]
            ).to_csv(output / "followups.csv", index=False)
            finalize(source, output)
            selection = json.loads((output / "best_config.json").read_text())
            self.assertEqual(selection["config_id"], "best")
            self.assertEqual(len(pd.read_csv(output / "all_trials.csv")), 2)
            for name in ("base", "best"):
                self.assertEqual(
                    (output / "runs" / name / "tracks/S.csv").read_text(), name
                )
                self.assertEqual(
                    json.loads(
                        (
                            output / "runs" / name / "evaluation/protocol.json"
                        ).read_text()
                    )["version"],
                    2,
                )
                self.assertEqual(
                    json.loads(
                        (
                            source / "runs" / name / "evaluation/protocol.json"
                        ).read_text()
                    )["version"],
                    1,
                )

            # Equal hyperparameters with a different lifecycle are a distinct model.
            fixed = output / "focused_runs/fixed"
            (fixed / "tracks").mkdir(parents=True)
            (fixed / "tracks/S.csv").write_text("fixed")
            (fixed / "run_manifest.json").write_text("{}")
            (fixed / "evaluation").mkdir()
            (fixed / "evaluation/protocol.json").write_text('{"version": 2}')
            pd.DataFrame(
                [
                    dict(
                        role="fixed",
                        algorithm_variant="coasting_fix",
                        HOTA=81,
                        IDF1=81,
                        IDS=1,
                        runtime_seconds=1,
                        config_json=json.dumps({"conf": 0.05}),
                        run_dir=str(fixed),
                    )
                ]
            ).to_csv(output / "focused_followups.csv", index=False)
            finalize(source, output)
            selection = json.loads((output / "best_config.json").read_text())
            self.assertTrue(selection["fix_coasting"])
            self.assertEqual(selection["config_id"], "fixed")
            self.assertEqual(len(pd.read_csv(output / "all_trials.csv")), 3)
