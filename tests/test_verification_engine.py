from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from football_advisor.db_schema import apply_schema
from football_advisor.models import (
    MatchFeatures,
    MatchRequest,
    PolicyDecision,
    PredictionBundle,
    ProbabilityResult,
    Recommendation,
    RiskLevel,
    TeamFeatures,
    ValueAssessment,
)
from football_advisor.verification_engine import (
    PostMatchVerifier,
    PredictionLogger,
    PredictionLoggingError,
)


def _bundle(context: dict[str, object]) -> PredictionBundle:
    return PredictionBundle(
        request=MatchRequest(
            query="Qatar vs Switzerland",
            match_id="WC2026_M008",
            home_team="Qatar",
            away_team="Switzerland",
        ),
        features=MatchFeatures(
            home=TeamFeatures(name="Qatar"),
            away=TeamFeatures(name="Switzerland"),
            context=context,
        ),
        probabilities=ProbabilityResult(
            home_win=0.25,
            draw=0.30,
            away_win=0.45,
            over_2_5=0.48,
            under_2_5=0.52,
            expected_home_goals=1.0,
            expected_away_goals=1.4,
            most_likely_scores=[("0-1", 0.12)],
        ),
        value_assessment=ValueAssessment(
            values=[],
            best_value=None,
            overround=0.0,
            threshold=0.05,
        ),
        policy=PolicyDecision(
            recommendation=Recommendation.NO_BET,
            risk_level=RiskLevel.HIGH,
            confidence=0.0,
            reasons=["test"],
        ),
        news=[],
    )


class PredictionLoggerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "verification.duckdb"
        with duckdb.connect(str(self.db_path)) as connection:
            apply_schema(connection)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_logs_standard_team_ids_from_feature_context(self):
        logger = PredictionLogger(str(self.db_path))

        prediction_id = logger.log_prediction(
            _bundle({"home_team_id": "TEAM_QAT", "away_team_id": "TEAM_SUI"})
        )

        with duckdb.connect(str(self.db_path), read_only=True) as connection:
            row = connection.execute(
                """
                SELECT home_team_id, away_team_id
                FROM core.fact_prediction_log
                WHERE prediction_id = ?
                """,
                [prediction_id],
            ).fetchone()
        self.assertEqual(row, ("TEAM_QAT", "TEAM_SUI"))

    def test_missing_standard_team_ids_is_rejected(self):
        logger = PredictionLogger(str(self.db_path))

        with self.assertRaises(PredictionLoggingError):
            logger.log_prediction(_bundle({}))


class PostMatchVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "verification.duckdb"
        with duckdb.connect(str(self.db_path)) as connection:
            apply_schema(connection)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_model_outcome_comparison_is_case_insensitive(self):
        self._insert_finished_prediction(
            model_outcome="HOME",
            value_outcome="away",
            policy_recommendation="no_bet",
            home_score=2,
            away_score=1,
        )

        results = PostMatchVerifier(str(self.db_path)).verify_pending_predictions()

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].is_correct)

    def test_no_bet_prediction_has_null_profit_loss(self):
        self._insert_finished_prediction(
            model_outcome="HOME",
            value_outcome="home",
            policy_recommendation="no_bet",
            home_score=2,
            away_score=1,
        )

        results = PostMatchVerifier(str(self.db_path)).verify_pending_predictions()

        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0].profit_loss_unit)
        with duckdb.connect(str(self.db_path), read_only=True) as connection:
            stored_profit_loss = connection.execute(
                "SELECT profit_loss_unit FROM core.fact_prediction_log "
                "WHERE prediction_id = 'PRED_TEST'"
            ).fetchone()[0]
        self.assertIsNone(stored_profit_loss)

    def test_prediction_profit_loss_uses_value_candidate_outcome(self):
        self._insert_finished_prediction(
            model_outcome="HOME",
            value_outcome="AWAY",
            policy_recommendation="bet",
            home_score=0,
            away_score=1,
            odds_at_prediction=3.0,
        )

        results = PostMatchVerifier(str(self.db_path)).verify_pending_predictions()

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].is_correct)
        self.assertEqual(results[0].profit_loss_unit, 2.0)

    def test_bet_ledger_settles_each_recorded_outcome(self):
        self._insert_finished_prediction(
            model_outcome="HOME",
            value_outcome="AWAY",
            policy_recommendation="bet",
            home_score=0,
            away_score=1,
        )
        with duckdb.connect(str(self.db_path)) as connection:
            connection.executemany(
                """
                INSERT INTO core.fact_bet_ledger (
                    bet_id, prediction_id, match_id, bet_time,
                    stake_amount, outcome_bet, odds_at_bet
                ) VALUES (?, 'PRED_TEST', 'MATCH_TEST', ?, ?, ?, ?)
                """,
                [
                    ["BET_AWAY", datetime.now(timezone.utc), 10.0, "AWAY", 3.0],
                    ["BET_HOME", datetime.now(timezone.utc), 5.0, "home", 2.0],
                ],
            )

        PostMatchVerifier(str(self.db_path)).verify_pending_predictions()

        with duckdb.connect(str(self.db_path), read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT bet_id, result, actual_return, net_profit
                FROM core.fact_bet_ledger
                ORDER BY bet_id
                """
            ).fetchall()
        self.assertEqual(
            rows,
            [
                ("BET_AWAY", "WIN", 30.0, 20.0),
                ("BET_HOME", "LOSS", 0.0, -5.0),
            ],
        )

    def _insert_finished_prediction(
        self,
        *,
        model_outcome: str,
        value_outcome: str,
        policy_recommendation: str,
        home_score: int,
        away_score: int,
        odds_at_prediction: float = 3.0,
    ) -> None:
        now = datetime.now(timezone.utc)
        with duckdb.connect(str(self.db_path)) as connection:
            connection.execute(
                """
                INSERT INTO core.fact_match_schedule (
                    match_id, system_league_id, match_time,
                    home_team_id, away_team_id, status,
                    home_score, away_score
                ) VALUES (
                    'MATCH_TEST', 'LEAGUE_TEST', ?,
                    'TEAM_HOME', 'TEAM_AWAY', 'FINISHED', ?, ?
                )
                """,
                [now, home_score, away_score],
            )
            connection.execute(
                """
                INSERT INTO core.fact_prediction_log (
                    prediction_id, match_id, prediction_time,
                    home_team, away_team,
                    model_predicted_outcome, model_predicted_probability,
                    value_candidate_outcome, odds_at_prediction,
                    policy_recommendation, status
                ) VALUES (
                    'PRED_TEST', 'MATCH_TEST', ?,
                    'Home', 'Away', ?, 0.6, ?, ?, ?, 'PENDING'
                )
                """,
                [
                    now,
                    model_outcome,
                    value_outcome,
                    odds_at_prediction,
                    policy_recommendation,
                ],
            )


if __name__ == "__main__":
    unittest.main()
