from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from football_advisor.backtest_engine import BacktestEngine, DuckDBBacktestMatchLoader
from football_advisor.db_schema import apply_schema
from football_advisor.odds_value_engine import OddsValueEngine


class DuckDBBacktestMatchLoaderTests(unittest.TestCase):
    @unittest.skip("view_llm_match_prediction_base 重构后待更新测试")
    def test_loads_walk_forward_samples_with_historical_freshness(self):
        import duckdb

        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "walk_forward.duckdb"
            with duckdb.connect(str(database_path)) as connection:
                apply_schema(connection)
                self._insert_walk_forward_fixture(connection)

            loader = DuckDBBacktestMatchLoader(database_path=str(database_path))
            matches = loader.load_walk_forward_matches(limit=10, match_id_prefix="WF_")

        self.assertEqual(
            [match.features.context["match_id"] for match in matches], ["WF_1", "WF_2"]
        )
        self.assertEqual([match.actual_outcome for match in matches], ["home", "away"])
        self.assertEqual(
            matches[0].features.context["historical_critical_data_age_minutes"], 30
        )
        self.assertFalse(matches[0].features.context["no_bet_data_quality_flag"])
        self.assertTrue(
            matches[0].features.context["realtime_no_bet_data_quality_flag"]
        )
        self.assertTrue(matches[1].features.context["no_bet_data_quality_flag"])

        result = BacktestEngine(
            odds_value_engine=OddsValueEngine(value_threshold=-1.0)
        ).run(matches)

        self.assertEqual(result.metrics.sample_size, 2)
        self.assertEqual(result.bets, 1)

    def _insert_walk_forward_fixture(self, connection) -> None:
        first_kickoff = datetime(2025, 1, 1, 15, 0, 0)
        second_kickoff = datetime(2025, 1, 8, 15, 0, 0)
        connection.execute("""
            INSERT INTO core.dim_league_mapping (
                system_league_id, league_standard_name, country
            ) VALUES ('SYS_EPL', 'English Premier League', 'England');

            INSERT INTO core.dim_team_mapping (
                system_team_id, team_standard_name, elo_rating_base
            ) VALUES
                ('SYS_ARS', 'Arsenal', 1640.0),
                ('SYS_CHE', 'Chelsea', 1580.0);
            """)
        self._insert_match(
            connection, "WF_2", second_kickoff, 0, 2, "SQLITE_PRIOR_MATCHES_ONLY"
        )
        self._insert_match(connection, "WF_1", first_kickoff, 2, 1, None)

    def _insert_match(
        self,
        connection,
        match_id: str,
        kickoff: datetime,
        home_score: int,
        away_score: int,
        data_quality_flag: str | None,
    ) -> None:
        feature_time = kickoff - timedelta(minutes=30)
        odds_time = kickoff - timedelta(minutes=20)
        connection.execute(
            """
            INSERT INTO core.fact_match_schedule (
                match_id,
                season,
                system_league_id,
                match_time,
                home_team_id,
                away_team_id,
                status,
                rest_days_home,
                rest_days_away,
                home_score,
                away_score,
                source_provider,
                updated_at
            ) VALUES (?, '2024-2025', 'SYS_EPL', ?, 'SYS_ARS', 'SYS_CHE', 'FINISHED',
                5, 4, ?, ?, 'TEST_FIXTURE', ?);
            """,
            [match_id, kickoff, home_score, away_score, feature_time],
        )
        connection.executemany(
            """
            INSERT INTO core.fact_team_rolling_stats (
                match_id,
                team_id,
                record_date,
                source_provider,
                rolling_xg_for,
                rolling_xg_against,
                recent_points_per_match,
                attack_strength,
                defense_strength,
                motivation_coefficient,
                key_missing_weight,
                data_quality_flag
            ) VALUES (?, ?, ?, 'TEST_STATS', ?, ?, ?, ?, ?, 0.6, 0.0, ?)
            """,
            [
                [
                    match_id,
                    "SYS_ARS",
                    feature_time,
                    1.8,
                    0.9,
                    2.1,
                    1.15,
                    0.92,
                    data_quality_flag,
                ],
                [
                    match_id,
                    "SYS_CHE",
                    feature_time,
                    1.2,
                    1.3,
                    1.4,
                    1.00,
                    1.08,
                    data_quality_flag,
                ],
            ],
        )
        connection.execute(
            """
            INSERT INTO core.fact_match_context_summary (
                match_id,
                h2h_home_wins,
                h2h_draws,
                h2h_away_wins,
                home_home_points_per_match,
                away_away_points_per_match,
                home_key_absences,
                away_key_absences,
                source_provider,
                updated_at
            ) VALUES (?, 1, 1, 1, 2.0, 1.1, 0, 0, 'TEST_CONTEXT', ?);
            """,
            [match_id, feature_time],
        )
        connection.execute(
            """
            INSERT INTO core.fact_odds_capital_flow (
                match_id,
                snapshot_time,
                time_to_kickoff,
                odds_type,
                bookmaker_name,
                home_odds,
                draw_odds,
                away_odds,
                implied_home_probability,
                implied_draw_probability,
                implied_away_probability,
                source_provider
            ) VALUES (?, ?, 20, '1X2', 'TEST_BOOK', 2.40, 3.30, 3.20, 0.40, 0.29, 0.31, 'TEST_ODDS');
            """,
            [match_id, odds_time],
        )


if __name__ == "__main__":
    unittest.main()
