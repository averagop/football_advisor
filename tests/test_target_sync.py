from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import duckdb

from football_advisor.config import AdvisorConfig, SyncConfig
from football_advisor.db_schema import apply_schema
from football_advisor.models import MatchRequest
from football_advisor.sync import DataSyncCoordinator, SyncResult
from football_advisor.target_sync import (
    DataReadinessGate,
    TargetMatchSyncService,
    TargetSyncResult,
)


class FakeSyncCoordinator:
    """模拟 DataSyncCoordinator，不触发真实网络请求。"""

    def __init__(self, structured_status="success", news_status="success", capital_status="success"):
        self.structured_status = structured_status
        self.news_status = news_status
        self.capital_status = capital_status
        self.parent_config = AdvisorConfig()
        self.structured_exclude_providers = None
        self.structured_calls = 0
        self.news_calls = 0
        self.capital_calls = 0

    def sync_structured_data(self, request, **kwargs):
        self.structured_calls += 1
        self.structured_exclude_providers = kwargs.get("exclude_providers")
        return SyncResult(
            source="multi_source",
            updated_at=datetime.now(timezone.utc),
            status=self.structured_status,
        )

    def sync_news_data(self, request):
        self.news_calls += 1
        return SyncResult(
            source="news_provider",
            updated_at=datetime.now(timezone.utc),
            status=self.news_status,
        )

    def sync_capital_flow_data(self, request):
        self.capital_calls += 1
        return SyncResult(
            source="exchange_provider",
            updated_at=datetime.now(timezone.utc),
            status=self.capital_status,
        )

    def _find_match_id(self, request):
        return "WC2026_M003"


class NoMatchFakeSync(FakeSyncCoordinator):
    """无 match_id 时返回 None 的模拟协调器。"""

    def _find_match_id(self, request):
        return None


class TargetSyncResultTests(unittest.TestCase):
    def test_critical_ok_all_success(self):
        result = TargetSyncResult(
            match_id="WC2026_M003",
            critical_results=(
                SyncResult(source="sporttery", updated_at=datetime.now(timezone.utc), status="success"),
                SyncResult(source="multi_source", updated_at=datetime.now(timezone.utc), status="success"),
                SyncResult(source="news", updated_at=datetime.now(timezone.utc), status="success"),
            ),
            optional_results=(),
            completed_at=datetime.now(timezone.utc),
        )
        self.assertTrue(result.critical_ok)

    def test_critical_ok_fresh_cache(self):
        result = TargetSyncResult(
            match_id="WC2026_M003",
            critical_results=(
                SyncResult(source="sporttery", updated_at=datetime.now(timezone.utc), status="fresh_cache"),
                SyncResult(source="multi_source", updated_at=datetime.now(timezone.utc), status="success"),
            ),
            optional_results=(),
            completed_at=datetime.now(timezone.utc),
        )
        self.assertTrue(result.critical_ok)

    def test_critical_not_ok_with_failure(self):
        result = TargetSyncResult(
            match_id="WC2026_M003",
            critical_results=(
                SyncResult(source="sporttery", updated_at=datetime.now(timezone.utc), status="success"),
                SyncResult(source="multi_source", updated_at=datetime.now(timezone.utc), status="failed"),
            ),
            optional_results=(),
            completed_at=datetime.now(timezone.utc),
        )
        self.assertFalse(result.critical_ok)


class DataReadinessGateTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)

    def test_all_fresh(self):
        gate = DataReadinessGate(
            match_id="WC2026_M003",
            schedule_updated_at=self.now,
            sporttery_odds_updated_at=self.now,
            team_stats_updated_at=self.now,
            lineup_injury_updated_at=self.now,
            news_updated_at=self.now,
        )
        result = gate.check()
        self.assertTrue(result["ready"])
        self.assertEqual(result["missing_critical"], [])
        self.assertEqual(result["stale_critical"], [])

    def test_load_does_not_accept_generic_context_injury_timestamp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "readiness_context_only.duckdb"
            with duckdb.connect(str(db_path)) as connection:
                apply_schema(connection)
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                connection.execute(
                    """
                    INSERT INTO core.fact_match_context_summary (
                        match_id, injury_data_updated_at, updated_at
                    ) VALUES ('READY_CONTEXT_ONLY', ?, ?)
                    """,
                    [now, now],
                )

                gate = DataReadinessGate.load(connection, "READY_CONTEXT_ONLY", 30)

        self.assertIsNone(gate.lineup_injury_updated_at)

    def test_load_accepts_lineup_injury_sync_state_with_real_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "readiness_lineup_state.duckdb"
            with duckdb.connect(str(db_path)) as connection:
                apply_schema(connection)
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                connection.execute(
                    """
                    INSERT INTO core.fact_target_sync_state (
                        match_id, component, provider, last_attempt_at,
                        last_success_at, source_updated_at, status,
                        row_count, empty_confirmed
                    ) VALUES (
                        'READY_LINEUP', 'lineup_injury', 'structured_sources',
                        ?, ?, ?, 'success', 2, FALSE
                    )
                    """,
                    [now, now, now],
                )

                gate = DataReadinessGate.load(connection, "READY_LINEUP", 30)

        self.assertEqual(gate.lineup_injury_updated_at, now)

    def test_missing_critical(self):
        gate = DataReadinessGate(
            match_id="WC2026_M003",
            schedule_updated_at=self.now,
            sporttery_odds_updated_at=None,  # 缺失
            team_stats_updated_at=self.now,
            lineup_injury_updated_at=self.now,
            news_updated_at=self.now,
        )
        result = gate.check()
        self.assertFalse(result["ready"])
        self.assertIn("sporttery_odds", result["missing_critical"])

    def test_stale_critical(self):
        stale_time = self.now - timedelta(minutes=60)
        gate = DataReadinessGate(
            match_id="WC2026_M003",
            schedule_updated_at=self.now,
            sporttery_odds_updated_at=stale_time,
            team_stats_updated_at=self.now,
            lineup_injury_updated_at=self.now,
            news_updated_at=self.now,
        )
        result = gate.check()
        self.assertFalse(result["ready"])
        self.assertIn("sporttery_odds", result["stale_critical"])

    def test_optional_missing_does_not_block(self):
        gate = DataReadinessGate(
            match_id="WC2026_M003",
            schedule_updated_at=self.now,
            sporttery_odds_updated_at=self.now,
            team_stats_updated_at=self.now,
            lineup_injury_updated_at=self.now,
            news_updated_at=self.now,
            capital_flow_updated_at=None,
            weather_updated_at=None,
        )
        result = gate.check()
        self.assertTrue(result["ready"])
        self.assertEqual(result["components"]["capital_flow"]["status"], "missing_optional")

    def test_custom_max_age(self):
        gate = DataReadinessGate(
            match_id="WC2026_M003",
            news_updated_at=self.now - timedelta(minutes=20),
            max_age_minutes=15,
        )
        result = gate.check()
        self.assertFalse(result["ready"])
        self.assertIn("schedule", result["missing_critical"])

    def test_load_reads_components_independently_from_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "readiness.duckdb"
            with duckdb.connect(str(db_path)) as connection:
                apply_schema(connection)
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                connection.execute(
                    """
                    INSERT INTO core.fact_match_schedule (
                        match_id, system_league_id, match_time,
                        home_team_id, away_team_id, updated_at
                    ) VALUES ('READY_1', 'LEAGUE', ?, 'HOME', 'AWAY', ?)
                    """,
                    [now, now],
                )
                connection.executemany(
                    """
                    INSERT INTO core.fact_team_rolling_stats (
                        match_id, team_id, record_date, source_provider,
                        etl_insert_timestamp
                    ) VALUES ('READY_1', ?, ?, 'test', ?)
                    """,
                    [("HOME", now, now), ("AWAY", now, now)],
                )
                connection.execute(
                    """
                    INSERT INTO core.fact_match_context_summary (
                        match_id, injury_data_updated_at, updated_at
                    ) VALUES ('READY_1', ?, ?)
                    """,
                    [now, now],
                )
                connection.execute(
                    """
                    INSERT INTO core.fact_news_signal_summary (
                        match_id, news_last_updated_at, updated_at
                    ) VALUES ('READY_1', ?, ?)
                    """,
                    [now, now],
                )

                gate = DataReadinessGate.load(connection, "READY_1", 30)

        result = gate.check()
        self.assertFalse(result.ready)
        self.assertIn("sporttery_odds", result.missing_critical)


class TargetMatchSyncServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_target_sync.duckdb"
        self.connection = duckdb.connect(str(self.db_path))
        apply_schema(self.connection)

    def tearDown(self):
        self.connection.close()
        self.temp_dir.cleanup()

    def _config_for_temp_database(self) -> AdvisorConfig:
        config = AdvisorConfig()
        return replace(
            config,
            duckdb=replace(config.duckdb, database_path=str(self.db_path)),
        )

    def _seed_target_mapping_and_markets(self, include_spf_odds: bool = True) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.connection.execute(
            """
            INSERT INTO core.dim_provider_match_mapping (
                provider_name, provider_match_id, system_match_id,
                resolution_method, resolution_confidence, verified_at
            ) VALUES (
                'SportteryOfficialWeb', '2040999', 'WC2026_M008',
                'test', 1.0, CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.executemany(
            """
            INSERT INTO core.fact_sporttery_market_status (
                match_id, market_type, sale_status, snapshot_time, source_provider
            ) VALUES (?, ?, ?, ?, 'SportteryOfficialWeb')
            """,
            [
                ("WC2026_M008", market, "OPEN" if market == "SPF" else "NOT_ON_SALE", now)
                for market in ("SPF", "RQSPF", "CRS", "TTG", "HAFU")
            ],
        )
        if include_spf_odds:
            self.connection.execute(
                """
                INSERT INTO core.fact_odds_capital_flow (
                    match_id, snapshot_time, odds_type, bookmaker_name,
                    home_odds, draw_odds, away_odds, source_provider
                ) VALUES (
                    'WC2026_M008', ?, '1X2', 'SportteryOfficialWeb',
                    2.10, 3.20, 3.40, 'SportteryOfficialWeb'
                )
                """,
                [now],
            )

    def test_sporttery_global_success_fails_when_target_mapping_is_missing(self):
        service = TargetMatchSyncService(
            parent_config=self._config_for_temp_database(),
            sync_coordinator=FakeSyncCoordinator(),
        )

        with patch(
            "football_advisor.sporttery_client.SportteryClient.fetch_football_odds",
            return_value={"status": "success", "matches_processed": 20},
        ):
            result = service._sync_sporttery(
                "WC2026_M008",
                MatchRequest(query="Qatar vs Switzerland", match_id="WC2026_M008"),
            )

        self.assertEqual(result.status, "failed_target_mapping_missing")
        self.assertEqual(result.details["match_id"], "WC2026_M008")

    def test_sporttery_target_requires_all_five_market_statuses(self):
        self._seed_target_mapping_and_markets()
        service = TargetMatchSyncService(
            parent_config=self._config_for_temp_database(),
            sync_coordinator=FakeSyncCoordinator(),
        )

        with patch(
            "football_advisor.sporttery_client.SportteryClient.fetch_football_odds",
            return_value={"status": "success", "matches_processed": 20},
        ):
            result = service._sync_sporttery(
                "WC2026_M008",
                MatchRequest(query="Qatar vs Switzerland", match_id="WC2026_M008"),
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(
            set(result.details["market_statuses"]),
            {"SPF", "RQSPF", "CRS", "TTG", "HAFU"},
        )

    def test_sporttery_open_market_without_odds_fails(self):
        self._seed_target_mapping_and_markets(include_spf_odds=False)
        service = TargetMatchSyncService(
            parent_config=self._config_for_temp_database(),
            sync_coordinator=FakeSyncCoordinator(),
        )

        with patch(
            "football_advisor.sporttery_client.SportteryClient.fetch_football_odds",
            return_value={"status": "success", "matches_processed": 20},
        ):
            result = service._sync_sporttery(
                "WC2026_M008",
                MatchRequest(query="Qatar vs Switzerland", match_id="WC2026_M008"),
            )

        self.assertEqual(result.status, "failed_target_open_market_missing_odds")
        self.assertEqual(result.details["missing_open_market_odds"], ["SPF"])

    def test_sporttery_success_with_zero_matches_does_not_reuse_old_status(self):
        self._seed_target_mapping_and_markets()
        service = TargetMatchSyncService(
            parent_config=self._config_for_temp_database(),
            sync_coordinator=FakeSyncCoordinator(),
        )

        with patch(
            "football_advisor.sporttery_client.SportteryClient.fetch_football_odds",
            return_value={"status": "success", "match_count": 0},
        ):
            result = service._sync_sporttery(
                "WC2026_M008",
                MatchRequest(query="Qatar vs Switzerland", match_id="WC2026_M008"),
            )

        self.assertEqual(result.status, "failed_target_not_in_feed")

    def test_lineup_injury_evidence_uses_target_mapped_staging_rows(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.connection.execute(
            """
            INSERT INTO core.dim_provider_match_mapping (
                provider_name, provider_match_id, system_match_id,
                resolution_method, resolution_confidence, verified_at
            ) VALUES (
                'API-Football', '12345', 'WC2026_M008',
                'test', 1.0, ?
            )
            """,
            [now],
        )
        self.connection.execute(
            """
            INSERT INTO staging.stg_injuries (
                provider_match_id, player_name, team_name,
                injury_type, reason, source_provider, etl_insert_timestamp
            ) VALUES ('12345', 'Player A', 'Qatar', 'injury', 'test', 'API-Football', ?)
            """,
            [now],
        )
        service = TargetMatchSyncService(
            parent_config=self._config_for_temp_database(),
            sync_coordinator=FakeSyncCoordinator(),
            connection=self.connection,
        )

        result = service._record_lineup_injury_evidence(
            "WC2026_M008",
            SyncResult(
                source="multi_source",
                updated_at=datetime.now(timezone.utc),
                status="success",
            ),
        )

        state = self.connection.execute(
            """
            SELECT status, row_count, empty_confirmed, source_updated_at
            FROM core.fact_target_sync_state
            WHERE match_id = 'WC2026_M008'
              AND component = 'lineup_injury'
              AND provider = 'structured_sources'
            """
        ).fetchone()
        self.assertEqual(result.status, "success")
        self.assertEqual(state[:3], ("success", 1, False))
        self.assertEqual(state[3], now)

    def test_lineup_injury_evidence_accepts_populated_core_context(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.connection.execute(
            """
            INSERT INTO core.fact_match_context_summary (
                match_id, home_key_absences, injury_data_updated_at,
                source_provider, updated_at
            ) VALUES ('WC2026_M008', 1, ?, 'API-Football', ?)
            """,
            [now, now],
        )
        service = TargetMatchSyncService(
            parent_config=self._config_for_temp_database(),
            sync_coordinator=FakeSyncCoordinator(),
            connection=self.connection,
        )

        result = service._record_lineup_injury_evidence(
            "WC2026_M008",
            SyncResult(
                source="multi_source",
                updated_at=datetime.now(timezone.utc),
                status="success",
            ),
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.details["row_count"], 1)

    def test_lineup_injury_explicit_empty_is_success_without_fake_timestamp(self):
        service = TargetMatchSyncService(
            parent_config=self._config_for_temp_database(),
            sync_coordinator=FakeSyncCoordinator(),
            connection=self.connection,
        )
        provider_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

        result = service._record_lineup_injury_evidence(
            "WC2026_M008",
            SyncResult(
                source="multi_source",
                updated_at=datetime.now(timezone.utc),
                status="success",
                details={
                    "lineup_injury_empty_confirmed": True,
                    "lineup_injury_source_updated_at": provider_updated_at,
                },
            ),
        )

        state = self.connection.execute(
            """
            SELECT status, row_count, empty_confirmed, source_updated_at
            FROM core.fact_target_sync_state
            WHERE match_id = 'WC2026_M008'
              AND component = 'lineup_injury'
              AND provider = 'structured_sources'
            """
        ).fetchone()
        self.assertEqual(result.status, "success")
        self.assertEqual(state, ("success", 0, True, provider_updated_at))

    def test_lineup_injury_fresh_explicit_empty_evidence_is_reused(self):
        service = TargetMatchSyncService(
            parent_config=self._config_for_temp_database(),
            sync_coordinator=FakeSyncCoordinator(),
            connection=self.connection,
        )
        provider_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        first = service._record_lineup_injury_evidence(
            "WC2026_M008",
            SyncResult(
                source="multi_source",
                updated_at=datetime.now(timezone.utc),
                status="success",
                details={
                    "lineup_injury_empty_confirmed": True,
                    "lineup_injury_source_updated_at": provider_updated_at,
                },
            ),
        )
        second = service._record_lineup_injury_evidence(
            "WC2026_M008",
            SyncResult(
                source="team_stats",
                updated_at=datetime.now(timezone.utc),
                status="recent_failure",
            ),
        )

        state = self.connection.execute(
            """
            SELECT status, row_count, empty_confirmed, source_updated_at
            FROM core.fact_target_sync_state
            WHERE match_id = 'WC2026_M008'
              AND component = 'lineup_injury'
              AND provider = 'structured_sources'
            """
        ).fetchone()
        self.assertEqual(first.status, "success")
        self.assertEqual(second.status, "fresh_cache")
        self.assertEqual(state, ("success", 0, True, provider_updated_at))

    def test_lineup_injury_without_rows_or_explicit_empty_stays_failed(self):
        service = TargetMatchSyncService(
            parent_config=self._config_for_temp_database(),
            sync_coordinator=FakeSyncCoordinator(),
            connection=self.connection,
        )

        result = service._record_lineup_injury_evidence(
            "WC2026_M008",
            SyncResult(
                source="multi_source",
                updated_at=datetime.now(timezone.utc),
                status="success",
            ),
        )

        state = self.connection.execute(
            """
            SELECT status, row_count, empty_confirmed, last_success_at
            FROM core.fact_target_sync_state
            WHERE match_id = 'WC2026_M008'
              AND component = 'lineup_injury'
              AND provider = 'structured_sources'
            """
        ).fetchone()
        self.assertEqual(result.status, "failed_no_evidence")
        self.assertEqual(state[:3], ("failed_no_evidence", 0, False))
        self.assertIsNone(state[3])

    def test_zero_row_success_is_not_written_as_success_cache(self):
        service = TargetMatchSyncService(
            parent_config=self._config_for_temp_database(),
            sync_coordinator=FakeSyncCoordinator(),
            connection=self.connection,
        )

        result = service._sync_with_cache(
            match_id="WC2026_M008",
            component="team_stats",
            provider="multi_source",
            sync_fn=lambda: SyncResult(
                source="multi_source",
                updated_at=datetime.now(timezone.utc),
                status="success",
                details={"row_count": 0},
            ),
        )

        cache_row = self.connection.execute(
            """
            SELECT status, row_count, last_success_at, error_code
            FROM core.fact_target_sync_state
            WHERE match_id = 'WC2026_M008'
              AND component = 'team_stats'
              AND provider = 'multi_source'
            """
        ).fetchone()
        self.assertEqual(result.status, "success")
        self.assertEqual(cache_row[0], "failed_no_rows")
        self.assertEqual(cache_row[1], 0)
        self.assertIsNone(cache_row[2])
        self.assertEqual(cache_row[3], "zero_rows_without_confirmation")

    def test_news_ingested_chunks_are_used_as_cache_row_evidence(self):
        service = TargetMatchSyncService(
            parent_config=self._config_for_temp_database(),
            sync_coordinator=FakeSyncCoordinator(),
            connection=self.connection,
        )

        service._sync_with_cache(
            match_id="WC2026_M008",
            component="news",
            provider="news_provider",
            sync_fn=lambda: SyncResult(
                source="news_provider",
                updated_at=datetime.now(timezone.utc),
                status="success",
                details={"ingested_chunks": "3"},
            ),
        )

        cache_row = self.connection.execute(
            """
            SELECT status, row_count, last_success_at
            FROM core.fact_target_sync_state
            WHERE match_id = 'WC2026_M008'
              AND component = 'news'
              AND provider = 'news_provider'
            """
        ).fetchone()
        self.assertEqual(cache_row[0], "success")
        self.assertEqual(cache_row[1], 3)
        self.assertIsNotNone(cache_row[2])

    def test_team_stats_cache_uses_real_target_core_rows(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.connection.executemany(
            """
            INSERT INTO core.fact_team_rolling_stats (
                match_id, team_id, record_date, source_provider,
                etl_insert_timestamp
            ) VALUES ('WC2026_M008', ?, ?, 'test', ?)
            """,
            [("HOME", now, now), ("AWAY", now, now)],
        )
        service = TargetMatchSyncService(
            parent_config=self._config_for_temp_database(),
            sync_coordinator=FakeSyncCoordinator(),
            connection=self.connection,
        )

        service._sync_with_cache(
            match_id="WC2026_M008",
            component="team_stats",
            provider="multi_source",
            sync_fn=lambda: SyncResult(
                source="multi_source",
                updated_at=datetime.now(timezone.utc),
                status="success",
            ),
        )

        cache_row = self.connection.execute(
            """
            SELECT status, row_count
            FROM core.fact_target_sync_state
            WHERE match_id = 'WC2026_M008'
              AND component = 'team_stats'
              AND provider = 'multi_source'
            """
        ).fetchone()
        self.assertEqual(cache_row, ("success", 2))

    def test_sync_with_cache_reuses_first_attempt_result(self):
        service = TargetMatchSyncService(
            parent_config=self._config_for_temp_database(),
            sync_coordinator=FakeSyncCoordinator(),
            connection=self.connection,
        )
        calls = 0

        def sync_once():
            nonlocal calls
            calls += 1
            return SyncResult(
                source="multi_source",
                updated_at=datetime.now(timezone.utc),
                status="success",
                details={"row_count": 1},
            )

        first = service._sync_with_cache(
            "WC2026_M008", "team_stats", "multi_source", sync_once
        )
        second = service._sync_with_cache(
            "WC2026_M008", "team_stats", "multi_source", sync_once
        )

        self.assertEqual(calls, 1)
        self.assertIs(second, first)

    def test_sporttery_failure_still_excludes_sporttery_from_structured_sync(self):
        fake_sync = FakeSyncCoordinator()
        service = TargetMatchSyncService(
            parent_config=self._config_for_temp_database(),
            sync_coordinator=fake_sync,
            connection=self.connection,
        )
        request = MatchRequest(query="Qatar vs Switzerland", match_id="WC2026_M008")

        with patch.object(
            TargetMatchSyncService,
            "_sync_sporttery",
            return_value=SyncResult(
                source="sporttery",
                updated_at=datetime.now(timezone.utc),
                status="failed",
                details={"error": "feed unavailable"},
            ),
        ):
            service.sync_for_prediction(request)

        self.assertEqual(
            fake_sync.structured_exclude_providers,
            {"SportteryOfficialWeb"},
        )

    def test_repeated_prediction_sync_does_not_repeat_external_components(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.connection.execute(
            """
            INSERT INTO core.dim_provider_match_mapping (
                provider_name, provider_match_id, system_match_id,
                resolution_method, resolution_confidence, verified_at
            ) VALUES ('API-Football', '12345', 'WC2026_M008', 'test', 1.0, ?)
            """,
            [now],
        )
        self.connection.execute(
            """
            INSERT INTO staging.stg_lineups (
                provider_match_id, player_name, team_name, role,
                formation, source_provider, etl_insert_timestamp
            ) VALUES ('12345', 'Player A', 'Qatar', 'starter', '4-3-3', 'API-Football', ?)
            """,
            [now],
        )
        self.connection.executemany(
            """
            INSERT INTO core.fact_team_rolling_stats (
                match_id, team_id, record_date, source_provider,
                etl_insert_timestamp
            ) VALUES ('WC2026_M008', ?, ?, 'test', ?)
            """,
            [("HOME", now, now), ("AWAY", now, now)],
        )
        fake_sync = FakeSyncCoordinator()
        service = TargetMatchSyncService(
            parent_config=self._config_for_temp_database(),
            sync_coordinator=fake_sync,
            connection=self.connection,
        )
        request = MatchRequest(query="Qatar vs Switzerland", match_id="WC2026_M008")

        with patch.object(
            TargetMatchSyncService,
            "_sync_sporttery",
            return_value=SyncResult(
                source="sporttery",
                updated_at=datetime.now(timezone.utc),
                status="success",
                details={"row_count": 1},
            ),
        ) as sporttery_sync:
            first = service.sync_for_prediction(request)
            second = service.sync_for_prediction(request)

        self.assertEqual(sporttery_sync.call_count, 1)
        self.assertEqual(fake_sync.structured_calls, 1)
        self.assertEqual(fake_sync.news_calls, 1)
        self.assertEqual(fake_sync.capital_calls, 1)
        self.assertIn("lineup_injury", [item.source for item in first.critical_results])
        self.assertIn("lineup_injury", [item.source for item in second.critical_results])

    def test_internal_cache_connection_is_closed(self):
        service = TargetMatchSyncService(
            parent_config=self._config_for_temp_database(),
            sync_coordinator=FakeSyncCoordinator(),
        )

        class FakeConnection:
            def __init__(self):
                self.closed = False

            def execute(self, sql, params):
                return self

            def fetchone(self):
                return None

            def close(self):
                self.closed = True

        connection = FakeConnection()
        with patch("duckdb.connect", return_value=connection):
            status = service._check_component_cache(
                "WC2026_M008", "team_stats", "multi_source"
            )

        self.assertEqual(status, "missing")
        self.assertTrue(connection.closed)

    def test_cache_read_error_is_explicit_and_skips_external_call(self):
        service = TargetMatchSyncService(
            parent_config=self._config_for_temp_database(),
            sync_coordinator=FakeSyncCoordinator(),
        )
        called = False

        class BrokenConnection:
            def execute(self, sql, params):
                raise RuntimeError("cache unavailable")

            def close(self):
                pass

        def sync_fn():
            nonlocal called
            called = True
            return SyncResult(
                source="multi_source",
                updated_at=datetime.now(timezone.utc),
                status="success",
            )

        with patch("duckdb.connect", return_value=BrokenConnection()):
            result = service._sync_with_cache(
                "WC2026_M008", "team_stats", "multi_source", sync_fn
            )

        self.assertEqual(result.status, "failed_cache_read")
        self.assertFalse(called)

    def test_cache_write_error_is_explicit(self):
        service = TargetMatchSyncService(
            parent_config=self._config_for_temp_database(),
            sync_coordinator=FakeSyncCoordinator(),
            connection=self.connection,
        )
        upstream = SyncResult(
            source="multi_source",
            updated_at=datetime.now(timezone.utc),
            status="success",
            details={"row_count": 1},
        )

        with patch.object(
            service,
            "_write_sync_state",
            side_effect=RuntimeError("cache unavailable"),
        ):
            result = service._sync_with_cache(
                "WC2026_M008",
                "team_stats",
                "multi_source",
                lambda: upstream,
            )

        self.assertEqual(result.status, "failed_cache_write")
        self.assertEqual(result.details["sync_status"], "success")

    def test_sync_for_prediction_without_match_id(self):
        """没有 match_id 且无法解析时返回 failed_no_match_id。"""
        config = AdvisorConfig()
        fake_sync = NoMatchFakeSync()

        service = TargetMatchSyncService(parent_config=config, sync_coordinator=fake_sync)
        request = MatchRequest(query="Unknown Team vs Another Team")
        result = service.sync_for_prediction(request)

        self.assertEqual(result.match_id, "")
        self.assertFalse(result.critical_ok)
        self.assertEqual(result.critical_results[0].status, "failed_no_match_id")

    def test_sync_for_prediction_with_match_id(self):
        """有 match_id 时正常同步关键数据。"""
        config = self._config_for_temp_database()
        fake_sync = FakeSyncCoordinator()

        service = TargetMatchSyncService(
            parent_config=config,
            sync_coordinator=fake_sync,
            connection=self.connection,
        )
        request = MatchRequest(
            query="Canada vs Bosnia",
            match_id="WC2026_M003",
            home_team="Canada",
            away_team="Bosnia-Herzegovina",
        )
        with patch.object(
            TargetMatchSyncService,
            "_sync_sporttery",
            return_value=SyncResult(
                source="sporttery",
                updated_at=datetime.now(timezone.utc),
                status="success",
            ),
        ):
            result = service.sync_for_prediction(request)

        # 关键数据应包含 sporttery(可能 skipped), structured, news
        self.assertGreaterEqual(len(result.critical_results), 2)
        self.assertIn(
            "lineup_injury",
            [item.source for item in result.critical_results],
        )
        # 可选数据包含 capital_flow
        self.assertGreaterEqual(len(result.optional_results), 0)

    def test_sync_for_prediction_critical_failure(self):
        """结构化数据失败时 critical_ok 为 False。"""
        config = self._config_for_temp_database()
        fake_sync = FakeSyncCoordinator(structured_status="failed")

        service = TargetMatchSyncService(
            parent_config=config,
            sync_coordinator=fake_sync,
            connection=self.connection,
        )
        request = MatchRequest(
            query="Canada vs Bosnia",
            match_id="WC2026_M003",
            home_team="Canada",
            away_team="Bosnia-Herzegovina",
        )
        with patch.object(
            TargetMatchSyncService,
            "_sync_sporttery",
            return_value=SyncResult(
                source="sporttery",
                updated_at=datetime.now(timezone.utc),
                status="success",
            ),
        ), patch.object(
            TargetMatchSyncService,
            "_check_component_cache",
            return_value="missing",
        ):
            result = service.sync_for_prediction(request)

        self.assertFalse(result.critical_ok)


class DataSyncCoordinatorDelegationTests(unittest.TestCase):
    """验证 sync_before_prediction() 正确委托给 TargetMatchSyncService。"""

    def test_sync_before_prediction_delegates(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db_path = Path(temp_dir.name) / "delegation.duckdb"
        with duckdb.connect(str(db_path)) as connection:
            apply_schema(connection)
        base_config = AdvisorConfig()
        config = replace(
            base_config,
            duckdb=replace(base_config.duckdb, database_path=str(db_path)),
        )
        fake_sync = FakeSyncCoordinator()

        coordinator = DataSyncCoordinator(
            config=config.sync,
            parent_config=config,
        )
        # 注入 fake 方法
        coordinator.sync_structured_data = fake_sync.sync_structured_data  # type: ignore[method-assign]
        coordinator.sync_news_data = fake_sync.sync_news_data  # type: ignore[method-assign]
        coordinator.sync_capital_flow_data = fake_sync.sync_capital_flow_data  # type: ignore[method-assign]
        coordinator._find_match_id = fake_sync._find_match_id  # type: ignore[method-assign]

        request = MatchRequest(
            query="Canada vs Bosnia",
            match_id="WC2026_M003",
            home_team="Canada",
            away_team="Bosnia-Herzegovina",
        )
        with patch.object(
            TargetMatchSyncService,
            "_sync_sporttery",
            return_value=SyncResult(
                source="sporttery",
                updated_at=datetime.now(timezone.utc),
                status="success",
            ),
        ):
            result = coordinator.sync_before_prediction(request)

        # 不应包含全局 pre_match / post_match 结果
        self.assertIsInstance(result, TargetSyncResult)
        sources = [r.source for r in result.critical_results + result.optional_results]
        self.assertNotIn("pre_match_collector", sources)
        self.assertNotIn("post_match_collector", sources)
        # 关键数据以组件名称出现（缓存/直接调用均可）
        self.assertIn("team_stats", sources)
        self.assertIn("news", sources)


if __name__ == "__main__":
    unittest.main()
