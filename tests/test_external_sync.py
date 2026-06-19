from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from football_advisor.config import SyncConfig, AdvisorConfig, DuckDBConfig
from football_advisor.db_schema import apply_schema
from football_advisor.football_data_client import FootballDataClient
from football_advisor.models import MatchRequest
from football_advisor.sync import DataSyncCoordinator
from football_advisor.pre_match_collector import PreMatchResult
from football_advisor.post_match_collector import CollectResult


class ExternalSyncTests(unittest.TestCase):
    def test_post_match_refreshes_only_teams_affected_by_current_collection(self):
        config = AdvisorConfig(
            duckdb=DuckDBConfig(database_path=str(self.db_path)),
            sync=SyncConfig(api_football_token="token"),
        )
        coordinator = DataSyncCoordinator(config=config.sync, parent_config=config)

        with (
            patch("football_advisor.sync.PostMatchCollector") as collector_class,
            patch("football_advisor.sync.RollingStatsRefresher") as refresher_class,
        ):
            collector_class.return_value.collect_if_needed.return_value = CollectResult(
                status="success",
                matches_updated=1,
                affected_team_ids=("TEAM_A", "TEAM_B"),
            )
            refresher_class.return_value.refresh_teams.return_value = {
                "status": "success",
                "teams_refreshed": 2,
                "rows_written": 2,
            }

            coordinator.sync_post_match_results()

        refresher_class.return_value.refresh_teams.assert_called_once_with(
            ["TEAM_A", "TEAM_B"]
        )

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_sync.duckdb"
        self.connection = duckdb.connect(str(self.db_path))
        apply_schema(self.connection)
        
        # Setup basic dimension data for mapping
        self.connection.execute("""
            INSERT INTO core.dim_league_mapping (system_league_id, league_standard_name, country)
            VALUES ('SYS_EPL', 'English Premier League', 'England');
            
            INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name, elo_rating_base)
            VALUES 
                ('SYS_ARS', 'Arsenal', 1600.0),
                ('SYS_CHE', 'Chelsea', 1550.0);

            INSERT INTO core.dim_provider_league_mapping (
                provider_name, provider_league_id, system_league_id, provider_league_name
            ) VALUES ('football-data.org', 'SYS_EPL', 'SYS_EPL', 'English Premier League');

            INSERT INTO core.dim_provider_team_mapping (
                provider_name, provider_team_id, system_team_id, provider_team_name
            ) VALUES
                ('football-data.org', 'SYS_ARS', 'SYS_ARS', 'Arsenal'),
                ('football-data.org', 'SYS_CHE', 'SYS_CHE', 'Chelsea');
        """)
        
    def tearDown(self):
        self.connection.close()
        self.temp_dir.cleanup()

    def test_football_data_client_mock_inserts_and_merges(self):
        client = FootballDataClient(api_token="mock_token")
        
        # The mock returns a game for SYS_ARS vs SYS_CHE
        res = client.fetch_matches(self.connection, "2026-05-01", "2026-05-07")
        
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["staged_count"], 1)
        self.assertEqual(res["merged_count"], 1)
        self.assertEqual(res["skipped_unmapped_count"], 0)
        
        # Check staging
        stg_rows = self.connection.execute("SELECT * FROM staging.stg_match_schedule").fetchall()
        self.assertEqual(len(stg_rows), 1)
        self.assertEqual(stg_rows[0][0], "999991") # provider_match_id
        
        # Check core
        core_rows = self.connection.execute("SELECT * FROM core.fact_match_schedule").fetchall()
        self.assertEqual(len(core_rows), 1)
        self.assertEqual(core_rows[0][0], "M_FD_999991") # mapped system match_id
        self.assertEqual(core_rows[0][4], "SYS_ARS") # mapped home_team
        self.assertEqual(core_rows[0][5], "SYS_CHE") # mapped away_team
        self.assertEqual(core_rows[0][7], "PRE-MATCH") # status mapped

    def test_pre_match_collector_uses_pre_match_league_config(self):
        config = AdvisorConfig(
            duckdb=DuckDBConfig(database_path=str(self.db_path)),
            sync=SyncConfig(
                api_football_token="token",
                pre_match_collect_leagues=("WC_WORLD_CUP_2026",),
                post_match_collect_leagues=("TOP5_EPL",),
            ),
        )
        coordinator = DataSyncCoordinator(config=config.sync, parent_config=config)

        with patch("football_advisor.sync.PreMatchCollector") as collector_class:
            collector_class.return_value.collect_if_needed.return_value = PreMatchResult(
                status="skipped_already_collected"
            )
            coordinator.sync_pre_match_results()

        self.assertEqual(
            collector_class.call_args.kwargs["target_leagues"],
            ("WC_WORLD_CUP_2026",),
        )

    def test_football_data_client_uses_provider_team_mapping(self):
        self.connection.execute("""
            INSERT INTO core.dim_provider_league_mapping (
                provider_name, provider_league_id, system_league_id, provider_league_name
            ) VALUES ('football-data.org', 'FD_EPL', 'SYS_EPL', 'Premier League');

            INSERT INTO core.dim_provider_team_mapping (
                provider_name, provider_team_id, system_team_id, provider_team_name
            ) VALUES
                ('football-data.org', 'FD_ARS', 'SYS_ARS', 'Arsenal'),
                ('football-data.org', 'FD_CHE', 'SYS_CHE', 'Chelsea');
        """)

        class ProviderMappedFootballDataClient(FootballDataClient):
            def _mock_response(self, path):
                return {
                    "matches": [
                        {
                            "id": 999993,
                            "competition": {"id": "FD_EPL"},
                            "utcDate": "2026-05-01T12:00:00Z",
                            "status": "TIMED",
                            "homeTeam": {"id": "FD_ARS"},
                            "awayTeam": {"id": "FD_CHE"},
                            "score": {"fullTime": {"home": None, "away": None}},
                        }
                    ]
                }

        client = ProviderMappedFootballDataClient(api_token="mock_token")

        res = client.fetch_matches(self.connection, "2026-05-01", "2026-05-07")

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["merged_count"], 1)
        self.assertEqual(res["skipped_unmapped_count"], 0)

        core_rows = self.connection.execute(
            """
            SELECT match_id, system_league_id, home_team_id, away_team_id
            FROM core.fact_match_schedule
            WHERE match_id = 'M_FD_999993'
            """
        ).fetchall()
        self.assertEqual(core_rows, [("M_FD_999993", "SYS_EPL", "SYS_ARS", "SYS_CHE")])

    def test_sync_structured_data_skipped_when_no_token(self):
        config = AdvisorConfig(
            duckdb=DuckDBConfig(database_path=str(self.db_path)),
            sync=SyncConfig(football_data_api_token=None, sporttery_base_url=None)
        )
        coordinator = DataSyncCoordinator(config=config.sync, parent_config=config)
        
        request = MatchRequest(query="Arsenal vs Chelsea")
        result = coordinator.sync_structured_data(request)
        
        self.assertEqual(result.source, "multi_source")
        self.assertEqual(result.status, "no_source_coverage")
        self.assertEqual(result.details["success_count"], "0")
        
    def test_sync_structured_data_succeeds_with_mock_token(self):
        config = AdvisorConfig(
            duckdb=DuckDBConfig(database_path=str(self.db_path)),
            sync=SyncConfig(football_data_api_token="mock_token")
        )
        coordinator = DataSyncCoordinator(config=config.sync, parent_config=config)
        
        request = MatchRequest(query="Arsenal vs Chelsea")
        result = coordinator.sync_structured_data(request)
        
        self.assertEqual(result.source, "multi_source")
        self.assertEqual(result.status, "success")
        self.assertIn("source_count", result.details)
        
        # Verify db insertion
        core_rows = self.connection.execute("SELECT * FROM core.fact_match_schedule").fetchall()
        self.assertEqual(len(core_rows), 1)

    def test_football_data_client_reports_unmapped_rows_without_creating_teams(self):
        class UnmappedFootballDataClient(FootballDataClient):
            def _mock_response(self, path):
                return {
                    "matches": [
                        {
                            "id": 999992,
                            "competition": {"id": "SYS_EPL"},
                            "utcDate": "2026-05-01T12:00:00Z",
                            "status": "TIMED",
                            "homeTeam": {"id": "SYS_ARS"},
                            "awayTeam": {"id": "PROVIDER_UNKNOWN"},
                            "score": {"fullTime": {"home": None, "away": None}},
                        }
                    ]
                }

        client = UnmappedFootballDataClient(api_token="mock_token")

        res = client.fetch_matches(self.connection, "2026-05-01", "2026-05-07")

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["staged_count"], 1)
        self.assertEqual(res["merged_count"], 0)
        self.assertEqual(res["skipped_unmapped_count"], 1)

        teams = self.connection.execute(
            "SELECT system_team_id FROM core.dim_team_mapping WHERE system_team_id = 'PROVIDER_UNKNOWN'"
        ).fetchall()
        self.assertEqual(teams, [])

        core_rows = self.connection.execute("SELECT * FROM core.fact_match_schedule").fetchall()
        self.assertEqual(core_rows, [])

    def test_world_cup_fixture_merges_to_core_with_provider_mapping(self):
        self.connection.execute(
            """
            INSERT INTO core.dim_league_mapping (system_league_id, league_standard_name, country)
            VALUES ('WC_WORLD_CUP_2026', 'FIFA World Cup 2026', 'International');

            INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name, elo_rating_base)
            VALUES
                ('WC_TEAM_USA', 'USA', 1780.0),
                ('WC_TEAM_MEX', 'Mexico', 1720.0);

            INSERT INTO core.dim_provider_league_mapping (
                provider_name, provider_league_id, system_league_id, provider_league_name
            ) VALUES ('football-data.org', 'WC', 'WC_WORLD_CUP_2026', 'FIFA World Cup');

            INSERT INTO core.dim_provider_team_mapping (
                provider_name, provider_team_id, system_team_id, provider_team_name
            ) VALUES
                ('football-data.org', '772', 'WC_TEAM_USA', 'USA'),
                ('football-data.org', '769', 'WC_TEAM_MEX', 'Mexico');
            """
        )

        class WorldCupFootballDataClient(FootballDataClient):
            def _mock_response(self, path):
                return {
                    "matches": [
                        {
                            "id": 880001,
                            "competition": {"id": "WC"},
                            "utcDate": "2026-06-11T20:00:00Z",
                            "status": "TIMED",
                            "homeTeam": {"id": "772"},
                            "awayTeam": {"id": "769"},
                            "score": {"fullTime": {"home": None, "away": None}},
                        }
                    ]
                }

        client = WorldCupFootballDataClient(api_token="mock_token")

        res = client.fetch_matches(self.connection, "2026-06-11", "2026-06-11")

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["staged_count"], 1)
        self.assertEqual(res["merged_count"], 1)
        self.assertEqual(res["skipped_unmapped_count"], 0)
        self.assertEqual(
            self.connection.execute(
                """
                SELECT system_league_id, home_team_id, away_team_id, status
                FROM core.fact_match_schedule
                WHERE match_id = 'M_FD_880001'
                """
            ).fetchall(),
            [("WC_WORLD_CUP_2026", "WC_TEAM_USA", "WC_TEAM_MEX", "PRE-MATCH")],
        )

    def test_news_query_for_friendly_does_not_force_world_cup_context(self):
        coordinator = DataSyncCoordinator()
        request = MatchRequest(
            query="Greece vs Italy international friendly",
            home_team="Greece",
            away_team="Italy",
        )

        query = coordinator._build_news_search_query(request)

        self.assertNotIn("World Cup", query)
        self.assertIn("Greece vs Italy", query)
        self.assertIn("injury", query)

    def test_news_query_keeps_world_cup_context_when_requested(self):
        coordinator = DataSyncCoordinator()
        request = MatchRequest(
            query="Mexico vs South Africa World Cup",
            home_team="Mexico",
            away_team="South Africa",
        )

        query = coordinator._build_news_search_query(request)

        self.assertIn("World Cup", query)


if __name__ == '__main__':
    unittest.main()
