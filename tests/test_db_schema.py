from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from football_advisor.db_schema import (
    SCHEMA_SQL,
    _migrate_new_columns,
    apply_schema,
    initialize_database,
)


class FakeConnection:
    def __init__(self):
        self.executed = ""

    def execute(self, sql: str, params=None):
        self.executed = sql
        return self

    def fetchone(self):
        return (0, 0)


class FailingMigrationInspectionConnection:
    def execute(self, sql: str, params=None):
        raise RuntimeError("inspection unavailable")


class DbSchemaTests(unittest.TestCase):
    def test_migration_inspection_failure_raises_with_table_and_column(self):
        with self.assertRaisesRegex(
            RuntimeError,
            r"table=core\.fact_prediction_log, column=diff_summary",
        ):
            _migrate_new_columns(FailingMigrationInspectionConnection())

    def test_schema_defines_three_feature_views(self):
        self.assertIn("core.view_match_feature_base", SCHEMA_SQL)
        self.assertIn("core.view_market_feature_base", SCHEMA_SQL)
        self.assertIn("core.view_llm_match_prediction_base", SCHEMA_SQL)

    def test_schema_defines_staging_tables_and_elo_history(self):
        required_tables = [
            "staging.stg_match_schedule",
            "staging.stg_team_stats",
            "staging.stg_odds",
            "core.fact_team_elo_history",
        ]
        for table_name in required_tables:
            self.assertIn(table_name, SCHEMA_SQL)

    def test_schema_keeps_structured_tables_in_core(self):
        required_tables = [
            "core.dim_league_mapping",
            "core.dim_team_mapping",
            "core.dim_player_mapping",
            "core.dim_referee_profile",
            "core.fact_match_schedule",
            "core.fact_team_rolling_stats",
            "core.fact_odds_capital_flow",
            "core.fact_match_context_summary",
            "core.fact_news_signal_summary",
            "core.fact_match_events_unified",
        ]

        for table_name in required_tables:
            self.assertIn(table_name, SCHEMA_SQL)

    def test_schema_exposes_no_bet_and_freshness_fields(self):
        self.assertIn("critical_data_age_minutes", SCHEMA_SQL)
        self.assertIn("missing_1x2_odds_flag", SCHEMA_SQL)
        self.assertIn("no_bet_data_quality_flag", SCHEMA_SQL)
        self.assertIn("CSV_PRIOR_MATCHES_ONLY", SCHEMA_SQL)
        self.assertIn("SQLITE_PRIOR_MATCHES_ONLY", SCHEMA_SQL)
        self.assertIn("derived_team_stats_quality_flag", SCHEMA_SQL)
        self.assertIn("record_date <= s.match_time - INTERVAL 30 MINUTE", SCHEMA_SQL)

    def test_apply_schema_executes_single_schema_script(self):
        connection = FakeConnection()
        apply_schema(connection)

        self.assertEqual(connection.executed, SCHEMA_SQL)

    def test_initialize_database_creates_queryable_duckdb_schema(self):
        import duckdb

        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "schema_test.duckdb"

            initialize_database(str(database_path))

            with duckdb.connect(str(database_path), read_only=True) as connection:
                table_names = {row[0] for row in connection.execute("""
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema = 'core'
                        """).fetchall()}
                columns = {
                    row[0]
                    for row in connection.execute(
                        "SELECT * FROM core.view_llm_match_prediction_base LIMIT 0"
                    ).description
                }

        self.assertIn("fact_match_schedule", table_names)
        self.assertIn("view_llm_match_prediction_base", table_names)
        self.assertIn("critical_data_age_minutes", columns)
        self.assertIn("no_bet_data_quality_flag", columns)
        self.assertIn("home_team_id", columns)
        self.assertIn("away_team_id", columns)

    def test_apply_schema_upgrades_legacy_match_schedule_before_creating_views(self):
        import duckdb

        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "legacy_schema.duckdb"
            with duckdb.connect(str(database_path)) as connection:
                connection.execute("CREATE SCHEMA core")
                connection.execute(
                    """
                    CREATE TABLE core.fact_match_schedule (
                        match_id VARCHAR PRIMARY KEY,
                        season VARCHAR,
                        system_league_id VARCHAR NOT NULL,
                        match_time TIMESTAMP NOT NULL,
                        home_team_id VARCHAR NOT NULL,
                        away_team_id VARCHAR NOT NULL,
                        referee_id VARCHAR,
                        status VARCHAR,
                        weather_condition VARCHAR,
                        temperature DOUBLE,
                        rest_days_home INTEGER,
                        rest_days_away INTEGER,
                        home_score INTEGER,
                        away_score INTEGER,
                        source_provider VARCHAR,
                        updated_at TIMESTAMP,
                        etl_insert_timestamp TIMESTAMP,
                        _change_request_type VARCHAR
                    )
                    """
                )

                apply_schema(connection)

                columns = {
                    row[0]
                    for row in connection.execute(
                        "DESCRIBE core.fact_match_schedule"
                    ).fetchall()
                }
                connection.execute(
                    "SELECT * FROM core.view_llm_match_prediction_base LIMIT 0"
                )

        self.assertIn("is_neutral_venue", columns)

    def test_llm_prediction_view_returns_minimal_complete_match_sample(self):
        import duckdb

        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "sample_test.duckdb"

            with duckdb.connect(str(database_path)) as connection:
                apply_schema(connection)
                connection.execute("""
                    INSERT INTO core.dim_league_mapping (
                        system_league_id, league_standard_name, country
                    ) VALUES ('SYS_EPL', 'English Premier League', 'England');

                    INSERT INTO core.dim_team_mapping (
                        system_team_id, team_standard_name, elo_rating_base
                    ) VALUES
                        ('SYS_ARS', 'Arsenal', 1640.0),
                        ('SYS_CHE', 'Chelsea', 1580.0);

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
                        source_provider,
                        updated_at
                    ) VALUES (
                        'M_SAMPLE',
                        '2025-2026',
                        'SYS_EPL',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP) + INTERVAL 1 DAY,
                        'SYS_ARS',
                        'SYS_CHE',
                        'PRE-MATCH',
                        5,
                        4,
                        'TEST_FIXTURE',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
                    );

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
                        key_missing_weight
                    ) VALUES
                        (
                            'M_SAMPLE',
                            'SYS_ARS',
                            CAST(CURRENT_TIMESTAMP AS TIMESTAMP),
                            'TEST_STATS',
                            1.8,
                            0.9,
                            2.2,
                            1.15,
                            0.92,
                            0.7,
                            0.05
                        ),
                        (
                            'M_SAMPLE',
                            'SYS_CHE',
                            CAST(CURRENT_TIMESTAMP AS TIMESTAMP),
                            'TEST_STATS',
                            1.3,
                            1.2,
                            1.6,
                            1.03,
                            1.05,
                            0.6,
                            0.08
                        );

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
                    ) VALUES (
                        'M_SAMPLE',
                        3,
                        1,
                        2,
                        2.4,
                        1.2,
                        1,
                        2,
                        'TEST_CONTEXT',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
                    );

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
                    ) VALUES (
                        'M_SAMPLE',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP),
                        1440,
                        '1X2',
                        'TEST_BOOK',
                        2.05,
                        3.40,
                        3.80,
                        0.46,
                        0.28,
                        0.26,
                        'SportteryOfficialWeb'
                    );

                    INSERT INTO core.fact_odds_capital_flow (
                        match_id,
                        snapshot_time,
                        time_to_kickoff,
                        odds_type,
                        bookmaker_name,
                        handicap_line,
                        home_odds,
                        draw_odds,
                        away_odds,
                        source_provider
                    ) VALUES (
                        'M_SAMPLE',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP),
                        1440,
                        'SPORTTERY_RQSPF',
                        'SportteryOfficialWeb',
                        -1.0,
                        4.50,
                        3.80,
                        1.62,
                        'SportteryOfficialWeb'
                    );

                    INSERT INTO core.fact_news_signal_summary (
                        match_id,
                        news_sentiment_score,
                        news_risk_flag,
                        news_source_count,
                        news_last_updated_at,
                        source_provider,
                        updated_at
                    ) VALUES (
                        'M_SAMPLE',
                        0.1,
                        FALSE,
                        2,
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP),
                        'TEST_NEWS',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
                    );
                    """)

                row = connection.execute("""
                    SELECT
                        home_team_name,
                        away_team_name,
                        latest_home_odds,
                        rqspf_handicap_line,
                        latest_rqspf_home_odds,
                        latest_rqspf_draw_odds,
                        latest_rqspf_away_odds,
                        missing_team_stats_flag,
                        missing_1x2_odds_flag,
                        no_bet_data_quality_flag,
                        critical_data_age_minutes
                    FROM core.view_llm_match_prediction_base
                    WHERE match_id = 'M_SAMPLE'
                    """).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Arsenal")
        self.assertEqual(row[1], "Chelsea")
        self.assertEqual(row[2], 2.05)
        self.assertEqual(row[3], -1.0)
        self.assertEqual(row[4], 4.5)
        self.assertEqual(row[5], 3.8)
        self.assertEqual(row[6], 1.62)
        self.assertFalse(row[7])
        self.assertFalse(row[8])
        self.assertFalse(row[9])
        self.assertLessEqual(row[10], 5)

    def test_missing_optional_feature_component_does_not_force_epoch_data_timestamp(
        self,
    ):
        import duckdb

        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "timestamp_test.duckdb"

            with duckdb.connect(str(database_path)) as connection:
                apply_schema(connection)
                connection.execute("""
                    INSERT INTO core.dim_league_mapping (
                        system_league_id, league_standard_name, country
                    ) VALUES ('SYS_EPL', 'English Premier League', 'England');

                    INSERT INTO core.dim_team_mapping (
                        system_team_id, team_standard_name, elo_rating_base
                    ) VALUES
                        ('SYS_ARS', 'Arsenal', 1640.0),
                        ('SYS_CHE', 'Chelsea', 1580.0);

                    INSERT INTO core.fact_match_schedule (
                        match_id,
                        season,
                        system_league_id,
                        match_time,
                        home_team_id,
                        away_team_id,
                        status,
                        source_provider,
                        updated_at
                    ) VALUES (
                        'M_PARTIAL',
                        '2025-2026',
                        'SYS_EPL',
                        TIMESTAMP '2025-08-10 12:00:00',
                        'SYS_ARS',
                        'SYS_CHE',
                        'PRE-MATCH',
                        'TEST_FIXTURE',
                        TIMESTAMP '2025-08-10 10:00:00'
                    );

                    INSERT INTO core.fact_team_rolling_stats (
                        match_id,
                        team_id,
                        record_date,
                        source_provider,
                        recent_points_per_match,
                        attack_strength,
                        defense_strength
                    ) VALUES (
                        'M_PARTIAL',
                        'SYS_ARS',
                        TIMESTAMP '2025-08-10 09:00:00',
                        'TEST_STATS',
                        2.0,
                        1.1,
                        0.9
                    );

                    INSERT INTO core.fact_match_context_summary (
                        match_id,
                        h2h_home_wins,
                        h2h_draws,
                        h2h_away_wins,
                        source_provider,
                        updated_at
                    ) VALUES (
                        'M_PARTIAL',
                        1,
                        0,
                        0,
                        'TEST_CONTEXT',
                        TIMESTAMP '2025-08-10 09:30:00'
                    );

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
                    ) VALUES (
                        'M_PARTIAL',
                        TIMESTAMP '2025-08-10 09:45:00',
                        135,
                        '1X2',
                        'TEST_BOOK',
                        2.05,
                        3.40,
                        3.80,
                        0.46,
                        0.28,
                        0.26,
                        'TEST_ODDS'
                    );
                    """)

                row = connection.execute("""
                    SELECT
                        data_updated_at,
                        missing_team_stats_flag,
                        no_bet_data_quality_flag
                    FROM core.view_llm_match_prediction_base
                    WHERE match_id = 'M_PARTIAL'
                    """).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row[0].year, 2025)
        self.assertTrue(row[1])
        self.assertTrue(row[2])

    def test_market_freshness_uses_etl_time_when_snapshot_is_utc_naive(self):
        import duckdb

        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "market_freshness_test.duckdb"

            with duckdb.connect(str(database_path)) as connection:
                apply_schema(connection)
                connection.execute("""
                    INSERT INTO core.dim_league_mapping (
                        system_league_id, league_standard_name, country
                    ) VALUES ('INTL_FRIENDLY', 'International Friendly', NULL);

                    INSERT INTO core.dim_team_mapping (
                        system_team_id, team_standard_name, elo_rating_base
                    ) VALUES
                        ('WC_TEAM_GRE', 'Greece', 1500.0),
                        ('WC_TEAM_ITA', 'Italy', 1550.0);

                    INSERT INTO core.fact_match_schedule (
                        match_id,
                        season,
                        system_league_id,
                        match_time,
                        home_team_id,
                        away_team_id,
                        status,
                        source_provider,
                        updated_at
                    ) VALUES (
                        'M_FRESH_MARKET',
                        '2026',
                        'INTL_FRIENDLY',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP) + INTERVAL 1 DAY,
                        'WC_TEAM_GRE',
                        'WC_TEAM_ITA',
                        'PRE-MATCH',
                        'TEST_FIXTURE',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
                    );

                    INSERT INTO core.fact_team_rolling_stats (
                        match_id,
                        team_id,
                        record_date,
                        source_provider,
                        recent_points_per_match,
                        attack_strength,
                        defense_strength
                    ) VALUES
                        (
                            'M_FRESH_MARKET',
                            'WC_TEAM_GRE',
                            CAST(CURRENT_TIMESTAMP AS TIMESTAMP),
                            'TEST_STATS',
                            1.5,
                            1.0,
                            1.0
                        ),
                        (
                            'M_FRESH_MARKET',
                            'WC_TEAM_ITA',
                            CAST(CURRENT_TIMESTAMP AS TIMESTAMP),
                            'TEST_STATS',
                            1.7,
                            1.1,
                            0.9
                        );

                    INSERT INTO core.fact_match_context_summary (
                        match_id,
                        h2h_home_wins,
                        h2h_draws,
                        h2h_away_wins,
                        source_provider,
                        updated_at
                    ) VALUES (
                        'M_FRESH_MARKET',
                        0,
                        1,
                        2,
                        'TEST_CONTEXT',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
                    );

                    INSERT INTO core.fact_odds_capital_flow (
                        match_id,
                        snapshot_time,
                        odds_type,
                        bookmaker_name,
                        home_odds,
                        draw_odds,
                        away_odds,
                        source_provider,
                        etl_insert_timestamp
                    ) VALUES (
                        'M_FRESH_MARKET',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP) - INTERVAL 8 HOUR,
                        '1X2',
                        'SportteryOfficialWeb',
                        2.05,
                        3.10,
                        3.13,
                        'SportteryOfficialWeb',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
                    );

                    INSERT INTO core.fact_news_signal_summary (
                        match_id,
                        news_sentiment_score,
                        news_risk_flag,
                        news_source_count,
                        news_last_updated_at,
                        source_provider,
                        updated_at
                    ) VALUES (
                        'M_FRESH_MARKET',
                        0.0,
                        FALSE,
                        1,
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP),
                        'TEST_NEWS',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
                    );
                    """)

                row = connection.execute("""
                    SELECT critical_data_age_minutes, no_bet_data_quality_flag
                    FROM core.view_llm_match_prediction_base
                    WHERE match_id = 'M_FRESH_MARKET'
                    """).fetchone()

        self.assertIsNotNone(row)
        self.assertLessEqual(row[0], 5)
        self.assertFalse(row[1])

    def test_structured_freshness_uses_etl_time_for_prior_match_stats(self):
        import duckdb

        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "structured_freshness_test.duckdb"

            with duckdb.connect(str(database_path)) as connection:
                apply_schema(connection)
                connection.execute("""
                    INSERT INTO core.dim_league_mapping (
                        system_league_id, league_standard_name, country
                    ) VALUES ('INTL_FRIENDLY', 'International Friendly', NULL);

                    INSERT INTO core.dim_team_mapping (
                        system_team_id, team_standard_name, elo_rating_base
                    ) VALUES
                        ('WC_TEAM_GRE', 'Greece', 1500.0),
                        ('WC_TEAM_ITA', 'Italy', 1550.0);

                    INSERT INTO core.fact_match_schedule (
                        match_id,
                        season,
                        system_league_id,
                        match_time,
                        home_team_id,
                        away_team_id,
                        status,
                        source_provider,
                        updated_at,
                        etl_insert_timestamp
                    ) VALUES (
                        'M_FRESH_STRUCTURED',
                        '2026',
                        'INTL_FRIENDLY',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP) + INTERVAL 1 DAY,
                        'WC_TEAM_GRE',
                        'WC_TEAM_ITA',
                        'PRE-MATCH',
                        'TEST_FIXTURE',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP),
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
                    );

                    INSERT INTO core.fact_team_rolling_stats (
                        match_id,
                        team_id,
                        record_date,
                        source_provider,
                        recent_points_per_match,
                        attack_strength,
                        defense_strength,
                        etl_insert_timestamp
                    ) VALUES
                        (
                            'M_FRESH_STRUCTURED',
                            'WC_TEAM_GRE',
                            CAST(CURRENT_TIMESTAMP AS TIMESTAMP) - INTERVAL 10 DAY,
                            'TEST_STATS',
                            1.5,
                            1.0,
                            1.0,
                            CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
                        ),
                        (
                            'M_FRESH_STRUCTURED',
                            'WC_TEAM_ITA',
                            CAST(CURRENT_TIMESTAMP AS TIMESTAMP) - INTERVAL 10 DAY,
                            'TEST_STATS',
                            1.7,
                            1.1,
                            0.9,
                            CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
                        );

                    INSERT INTO core.fact_match_context_summary (
                        match_id,
                        home_recent_wins,
                        away_recent_wins,
                        source_provider,
                        updated_at
                    ) VALUES (
                        'M_FRESH_STRUCTURED',
                        1,
                        1,
                        'TEST_CONTEXT',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
                    );

                    INSERT INTO core.fact_odds_capital_flow (
                        match_id,
                        snapshot_time,
                        odds_type,
                        bookmaker_name,
                        home_odds,
                        draw_odds,
                        away_odds,
                        source_provider,
                        etl_insert_timestamp
                    ) VALUES (
                        'M_FRESH_STRUCTURED',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP) - INTERVAL 8 HOUR,
                        '1X2',
                        'SportteryOfficialWeb',
                        2.05,
                        3.10,
                        3.13,
                        'SportteryOfficialWeb',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
                    );

                    INSERT INTO core.fact_news_signal_summary (
                        match_id,
                        news_sentiment_score,
                        news_risk_flag,
                        news_source_count,
                        news_last_updated_at,
                        source_provider,
                        updated_at
                    ) VALUES (
                        'M_FRESH_STRUCTURED',
                        0.0,
                        FALSE,
                        1,
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP),
                        'TEST_NEWS',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
                    );
                    """)

                row = connection.execute("""
                    SELECT critical_data_age_minutes, no_bet_data_quality_flag
                    FROM core.view_llm_match_prediction_base
                    WHERE match_id = 'M_FRESH_STRUCTURED'
                    """).fetchone()

        self.assertIsNotNone(row)
        self.assertLessEqual(row[0], 5)
        self.assertFalse(row[1])


if __name__ == "__main__":
    unittest.main()
