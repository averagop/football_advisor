from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from football_advisor.prediction_diff import PredictionDiffAnalyzer


class PredictionDiffAnalyzerTests(unittest.TestCase):
    def test_diff_update_failure_is_raised(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "diff.duckdb"
            with duckdb.connect(str(db_path)) as connection:
                connection.execute("CREATE SCHEMA core")
                connection.execute(
                    """
                    CREATE TABLE core.fact_prediction_log (
                        prediction_id VARCHAR PRIMARY KEY,
                        match_id VARCHAR NOT NULL,
                        prediction_time TIMESTAMP NOT NULL,
                        home_team VARCHAR,
                        away_team VARCHAR,
                        predicted_outcome VARCHAR,
                        predicted_probability DOUBLE,
                        model_home_prob DOUBLE,
                        model_draw_prob DOUBLE,
                        model_away_prob DOUBLE,
                        odds_at_prediction DOUBLE,
                        expected_value DOUBLE,
                        expected_home_goals DOUBLE,
                        expected_away_goals DOUBLE,
                        policy_recommendation VARCHAR,
                        confidence DOUBLE,
                        risk_level VARCHAR,
                        data_quality_flag BOOLEAN
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO core.fact_prediction_log (
                        prediction_id, match_id, prediction_time
                    ) VALUES ('PRED_CURRENT', 'MATCH_1', CURRENT_TIMESTAMP)
                    """
                )

            analyzer = PredictionDiffAnalyzer(database_path=str(db_path))
            with self.assertLogs("football_advisor.prediction_diff", level="ERROR"):
                with self.assertRaises(Exception):
                    analyzer.analyze_and_save("PRED_CURRENT", "MATCH_1")


if __name__ == "__main__":
    unittest.main()
