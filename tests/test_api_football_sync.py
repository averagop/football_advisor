from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import duckdb

from football_advisor.config import SyncConfig, AdvisorConfig, DuckDBConfig
from football_advisor.db_schema import apply_schema
from football_advisor.api_football_client import ApiFootballClient
from football_advisor.models import MatchRequest
from football_advisor.sync import DataSyncCoordinator


class ApiFootballSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_api_football.duckdb"
        self.connection = duckdb.connect(str(self.db_path))
        apply_schema(self.connection)
        
        self.connection.execute("""
            INSERT INTO core.dim_league_mapping (system_league_id, league_standard_name, country)
            VALUES ('SYS_EPL', 'English Premier League', 'England');
            
            INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name)
            VALUES 
                ('SYS_ARS', 'Arsenal'),
                ('SYS_CHE', 'Chelsea');

            INSERT INTO core.dim_provider_team_mapping (
                provider_name, provider_team_id, system_team_id, provider_team_name
            ) VALUES
                ('API-Football', 'SYS_ARS', 'SYS_ARS', 'Arsenal'),
                ('API-Football', 'SYS_CHE', 'SYS_CHE', 'Chelsea');
        """)
        
    def tearDown(self):
        self.connection.close()
        self.temp_dir.cleanup()

    def test_api_football_client_mock_inserts_and_merges_odds_and_stats(self):
        client = ApiFootballClient(api_token="mock_token")
        
        ref_time = datetime.now(timezone.utc)
        res = client.fetch_match_data(self.connection, ref_time, "Arsenal", "Chelsea")
        
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["odds_count"], 1)
        self.assertEqual(res["stats_count"], 2) # Home and Away
        self.assertEqual(res["staged_count"], 2)
        self.assertEqual(res["merged_count"], 2)
        self.assertEqual(res["skipped_unmapped_count"], 0)
        
        # Check staging odds
        stg_odds = self.connection.execute("SELECT provider_match_id, home_odds FROM staging.stg_odds").fetchall()
        self.assertEqual(len(stg_odds), 1)
        self.assertEqual(stg_odds[0][0], "888881")
        self.assertEqual(stg_odds[0][1], 2.1)
        
        # Check core odds
        core_odds = self.connection.execute("SELECT match_id, home_odds FROM core.fact_odds_capital_flow").fetchall()
        self.assertEqual(len(core_odds), 1)
        self.assertEqual(core_odds[0][0], "M_AF_888881")
        self.assertEqual(core_odds[0][1], 2.1)

        # Check staging stats
        stg_stats = self.connection.execute("SELECT provider_team_id, xg_for FROM staging.stg_team_stats ORDER BY provider_team_id").fetchall()
        self.assertEqual(len(stg_stats), 2)
        
        # Check core stats
        core_stats = self.connection.execute("SELECT team_id, rolling_xg_for FROM core.fact_team_rolling_stats ORDER BY team_id").fetchall()
        self.assertEqual(len(core_stats), 2)
        # Assuming ordering puts SYS_ARS before SYS_CHE
        self.assertEqual(core_stats[0][0], "SYS_ARS")
        self.assertEqual(core_stats[0][1], 10.5)
        self.assertEqual(core_stats[1][0], "SYS_CHE")
        self.assertEqual(core_stats[1][1], 7.8)

    def test_sync_routes_to_api_football_when_configured(self):
        config = AdvisorConfig(
            duckdb=DuckDBConfig(database_path=str(self.db_path)),
            sync=SyncConfig(
                football_data_api_token="mock_token_basic",
                api_football_token="mock_token" # This should take precedence
            )
        )
        coordinator = DataSyncCoordinator(config=config.sync, parent_config=config)
        
        request = MatchRequest(query="Arsenal vs Chelsea")
        result = coordinator.sync_structured_data(request)
        
        self.assertEqual(result.source, "multi_source")
        self.assertEqual(result.status, "success")
        self.assertIn("source_count", result.details)
        self.assertIn("coverage_flag", result.details)

    def test_real_api_football_fixture_without_embedded_odds_stats_is_not_success(self):
        class RealShapeApiFootballClient(ApiFootballClient):
            def _http_get(self, path):
                return {
                    "response": [
                        {
                            "fixture": {
                                "id": 888883,
                                "date": "2026-05-01T12:00:00Z",
                                "status": {"short": "NS"},
                            },
                            "league": {"id": 39, "name": "Premier League"},
                            "teams": {
                                "home": {"id": 42, "name": "Arsenal"},
                                "away": {"id": 49, "name": "Chelsea"},
                            },
                            "goals": {"home": None, "away": None},
                        }
                    ]
                }

        client = RealShapeApiFootballClient(api_token="real_token")

        res = client.fetch_match_data(
            self.connection,
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            "Arsenal",
            "Chelsea",
        )

        self.assertEqual(res["status"], "fixture_matched_no_market_stats")
        self.assertEqual(res["odds_count"], 0)
        self.assertEqual(res["stats_count"], 0)
        self.assertEqual(res["merged_count"], 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM core.fact_odds_capital_flow"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM core.fact_team_rolling_stats"
            ).fetchone()[0],
            0,
        )

    def test_real_api_football_fetches_independent_odds_and_stats_endpoints(self):
        class RealEndpointApiFootballClient(ApiFootballClient):
            def _http_get(self, path):
                if path.startswith("/fixtures?"):
                    return {
                        "response": [
                            {
                                "fixture": {
                                    "id": 888884,
                                    "date": "2026-05-01T12:00:00Z",
                                },
                                "teams": {
                                    "home": {"id": 42, "name": "Arsenal"},
                                    "away": {"id": 49, "name": "Chelsea"},
                                },
                            }
                        ]
                    }
                if path == "/odds?fixture=888884":
                    return {
                        "response": [
                            {
                                "bookmakers": [
                                    {
                                        "name": "API Book",
                                        "bets": [
                                            {
                                                "name": "Match Winner",
                                                "values": [
                                                    {"value": "Home", "odd": "2.10"},
                                                    {"value": "Draw", "odd": "3.40"},
                                                    {"value": "Away", "odd": "3.50"},
                                                ],
                                            }
                                        ],
                                    }
                                ]
                            }
                        ]
                    }
                if path == "/fixtures/statistics?fixture=888884":
                    return {
                        "response": [
                            {
                                "team": {"id": 42},
                                "statistics": [
                                    {"type": "Expected Goals", "value": "1.8"},
                                    {"type": "Shots on Goal", "value": 6},
                                ],
                            },
                            {
                                "team": {"id": 49},
                                "statistics": [
                                    {"type": "Expected Goals", "value": "1.1"},
                                    {"type": "Shots on Goal", "value": 3},
                                ],
                            },
                        ]
                    }
                return {"response": []}

        self.connection.execute("""
            INSERT INTO core.dim_provider_team_mapping (
                provider_name, provider_team_id, system_team_id, provider_team_name
            ) VALUES
                ('API-Football', '42', 'SYS_ARS', 'Arsenal'),
                ('API-Football', '49', 'SYS_CHE', 'Chelsea');
        """)
        client = RealEndpointApiFootballClient(api_token="real_token")

        res = client.fetch_match_data(
            self.connection,
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            "Arsenal",
            "Chelsea",
        )

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["odds_count"], 1)
        self.assertEqual(res["stats_count"], 2)
        self.assertEqual(res["missing_market_stats_count"], 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT bookmaker_name, home_odds, draw_odds, away_odds "
                "FROM core.fact_odds_capital_flow WHERE match_id = 'M_AF_888884'"
            ).fetchall(),
            [("API Book", 2.1, 3.4, 3.5)],
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT team_id, rolling_xg_for FROM core.fact_team_rolling_stats "
                "WHERE match_id = 'M_AF_888884' ORDER BY team_id"
            ).fetchall(),
            [("SYS_ARS", 1.8), ("SYS_CHE", 1.1)],
        )

    def test_sync_reports_api_football_failure_when_falling_back(self):
        class FailingApiFootballClient:
            def __init__(self, api_token=None, base_url=""):
                pass

            def fetch_match_data(self, connection, ref_time, home_team, away_team):
                raise RuntimeError("API-Football unavailable")

        config = AdvisorConfig(
            duckdb=DuckDBConfig(database_path=str(self.db_path)),
            sync=SyncConfig(api_football_token="broken_token", sporttery_base_url=None),
        )
        coordinator = DataSyncCoordinator(config=config.sync, parent_config=config)

        with patch(
            "football_advisor.api_football_client.ApiFootballClient",
            FailingApiFootballClient,
        ):
            result = coordinator.sync_structured_data(
                MatchRequest(query="Arsenal vs Chelsea")
            )

        self.assertEqual(result.status, "all_sources_failed")
        self.assertIn("failure_count", result.details)

    def test_api_football_client_reports_unmapped_stats_without_creating_teams(self):
        class UnmappedApiFootballClient(ApiFootballClient):
            def _mock_response(self, path):
                return {
                    "response": [
                        {
                            "fixture": {
                                "id": 888882,
                                "date": "2026-05-01T12:00:00Z",
                            },
                            "teams": {
                                "home": {"id": "SYS_ARS"},
                                "away": {"id": "PROVIDER_UNKNOWN"},
                            },
                            "odds": {
                                "1x2": {"home": 2.1, "draw": 3.4, "away": 3.5}
                            },
                            "stats": {
                                "home": {
                                    "goals_for": 12.0,
                                    "goals_against": 4.0,
                                    "xg_for": 10.5,
                                    "xg_against": 3.2,
                                },
                                "away": {
                                    "goals_for": 8.0,
                                    "goals_against": 9.0,
                                    "xg_for": 7.8,
                                    "xg_against": 8.5,
                                },
                            },
                        }
                    ]
                }

        client = UnmappedApiFootballClient(api_token="mock_token")

        ref_time = datetime.now(timezone.utc)
        res = client.fetch_match_data(self.connection, ref_time, "Arsenal", "Unknown")

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["staged_count"], 2)
        self.assertEqual(res["merged_count"], 1)
        self.assertEqual(res["skipped_unmapped_count"], 1)

        teams = self.connection.execute(
            "SELECT system_team_id FROM core.dim_team_mapping WHERE system_team_id = 'PROVIDER_UNKNOWN'"
        ).fetchall()
        self.assertEqual(teams, [])

        core_stats = self.connection.execute(
            "SELECT team_id FROM core.fact_team_rolling_stats ORDER BY team_id"
        ).fetchall()
        self.assertEqual(core_stats, [("SYS_ARS",)])

    def test_world_cup_real_endpoint_response_writes_market_stats_and_audit_rows(self):
        self.connection.execute("""
            INSERT INTO core.dim_league_mapping (system_league_id, league_standard_name)
            VALUES ('WC_WORLD_CUP_2026', 'FIFA World Cup 2026');

            INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name)
            VALUES
                ('WC_TEAM_USA', 'USA'),
                ('WC_TEAM_MEX', 'Mexico');

            INSERT INTO core.dim_provider_team_mapping (
                provider_name, provider_team_id, system_team_id, provider_team_name
            ) VALUES
                ('API-Football', '15', 'WC_TEAM_USA', 'USA'),
                ('API-Football', '14', 'WC_TEAM_MEX', 'Mexico');
        """)

        class WorldCupApiFootballClient(ApiFootballClient):
            def _http_get(self, path):
                if path.startswith("/fixtures?"):
                    return {
                        "response": [
                            {
                                "fixture": {"id": 990001, "date": "2026-06-11T20:00:00Z"},
                                "teams": {
                                    "home": {"id": 15, "name": "USA"},
                                    "away": {"id": 14, "name": "Mexico"},
                                },
                            }
                        ]
                    }
                if path == "/odds?fixture=990001":
                    return {
                        "response": [
                            {
                                "bookmakers": [
                                    {
                                        "name": "World Cup Book",
                                        "bets": [
                                            {
                                                "name": "Match Winner",
                                                "values": [
                                                    {"value": "Home", "odd": "2.20"},
                                                    {"value": "Draw", "odd": "3.05"},
                                                    {"value": "Away", "odd": "3.30"},
                                                ],
                                            }
                                        ],
                                    }
                                ]
                            }
                        ]
                    }
                if path == "/fixtures/statistics?fixture=990001":
                    return {
                        "response": [
                            {
                                "team": {"id": 15},
                                "statistics": [
                                    {"type": "Expected Goals", "value": "1.4"},
                                ],
                            },
                            {
                                "team": {"id": 14},
                                "statistics": [
                                    {"type": "Expected Goals", "value": "1.2"},
                                ],
                            },
                        ]
                    }
                if path == "/injuries?fixture=990001":
                    return {
                        "response": [
                            {
                                "player": {"name": "Example Player"},
                                "team": {"name": "USA"},
                                "type": "Missing Fixture",
                                "reason": "Muscle injury",
                            }
                        ]
                    }
                if path == "/fixtures/lineups?fixture=990001":
                    return {
                        "response": [
                            {
                                "team": {"name": "USA"},
                                "formation": "4-3-3",
                                "startXI": [{"player": {"name": "Starter One"}}],
                                "substitutes": [{"player": {"name": "Sub One"}}],
                            }
                        ]
                    }
                return {"response": []}

        client = WorldCupApiFootballClient(api_token="real_token")

        res = client.fetch_match_data(
            self.connection,
            datetime(2026, 6, 11, tzinfo=timezone.utc),
            "USA",
            "Mexico",
        )

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["odds_count"], 1)
        self.assertEqual(res["stats_count"], 2)
        self.assertEqual(res["merged_count"], 2)
        self.assertEqual(res["missing_market_stats_count"], 0)
        self.assertEqual(res["injury_count"], 1)
        self.assertEqual(res["lineup_count"], 2)
        self.assertEqual(
            self.connection.execute(
                "SELECT bookmaker_name, home_odds, draw_odds, away_odds "
                "FROM core.fact_odds_capital_flow WHERE match_id = 'M_AF_990001'"
            ).fetchall(),
            [("World Cup Book", 2.2, 3.05, 3.3)],
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT team_id, rolling_xg_for FROM core.fact_team_rolling_stats "
                "WHERE match_id = 'M_AF_990001' ORDER BY team_id"
            ).fetchall(),
            [("WC_TEAM_MEX", 1.2), ("WC_TEAM_USA", 1.4)],
        )

    def test_recent_friendlies_fill_target_match_recent_form_without_odds(self):
        self.connection.execute("""
            INSERT INTO core.dim_league_mapping (system_league_id, league_standard_name)
            VALUES ('WC_WORLD_CUP_2026', 'FIFA World Cup 2026');

            INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name)
            VALUES
                ('WC_TEAM_MEX', 'Mexico'),
                ('WC_TEAM_RSA', 'South Africa');

            INSERT INTO core.fact_match_schedule (
                match_id, season, system_league_id, match_time,
                home_team_id, away_team_id, status, source_provider
            ) VALUES (
                'WC2026_M001', 2026, 'WC_WORLD_CUP_2026',
                TIMESTAMP '2026-06-11 19:00:00',
                'WC_TEAM_MEX', 'WC_TEAM_RSA', 'PRE-MATCH', 'TEST'
            );

            INSERT INTO core.dim_provider_team_mapping (
                provider_name, provider_team_id, system_team_id, provider_team_name
            ) VALUES
                ('API-Football', '16', 'WC_TEAM_MEX', 'Mexico'),
                ('API-Football', '1111', 'WC_TEAM_RSA', 'South Africa');
        """)

        class RecentFriendlyApiFootballClient(ApiFootballClient):
            def _http_get(self, path):
                if path == "/fixtures?date=2026-06-05":
                    return {
                        "response": [
                            {
                                "fixture": {
                                    "id": 1528284,
                                    "date": "2026-06-05T02:00:00+00:00",
                                    "status": {"short": "FT"},
                                },
                                "league": {
                                    "id": 10,
                                    "name": "Friendlies",
                                    "country": "World",
                                },
                                "teams": {
                                    "home": {"id": 16, "name": "Mexico"},
                                    "away": {"id": 14, "name": "Serbia"},
                                },
                                "goals": {"home": 5, "away": 1},
                            }
                        ]
                    }
                if path == "/fixtures?date=2026-06-06":
                    return {
                        "response": [
                            {
                                "fixture": {
                                    "id": 1528285,
                                    "date": "2026-06-06T18:00:00+00:00",
                                    "status": {"short": "FT"},
                                },
                                "league": {
                                    "id": 10,
                                    "name": "Friendlies",
                                    "country": "World",
                                },
                                "teams": {
                                    "home": {"id": 2222, "name": "Botswana"},
                                    "away": {"id": 1111, "name": "South Africa"},
                                },
                                "goals": {"home": 0, "away": 0},
                            }
                        ]
                    }
                return {"response": []}

        client = RecentFriendlyApiFootballClient(api_token="real_token")

        res = client.fetch_recent_friendlies(
            self.connection,
            target_match_time=datetime(2026, 6, 11, 19, 0, tzinfo=timezone.utc),
            home_team="Mexico",
            away_team="South Africa",
            date_from=date(2026, 6, 5),
            date_to=date(2026, 6, 7),
        )

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["friendly_count"], 2)
        self.assertEqual(res["mapped_team_stats_count"], 2)
        self.assertEqual(res["skipped_unmapped_count"], 2)
        self.assertEqual(
            self.connection.execute(
                """
                SELECT match_id, team_id, rolling_goals_for, rolling_goals_against,
                       recent_points_per_match, data_quality_flag
                FROM core.fact_team_rolling_stats
                WHERE match_id = 'WC2026_M001'
                ORDER BY team_id
                """
            ).fetchall(),
            [
                (
                    "WC2026_M001",
                    "WC_TEAM_MEX",
                    5.0,
                    1.0,
                    3.0,
                    "API_FOOTBALL_RECENT_FRIENDLY",
                ),
                (
                    "WC2026_M001",
                    "WC_TEAM_RSA",
                    0.0,
                    0.0,
                    1.0,
                    "API_FOOTBALL_RECENT_FRIENDLY",
                )
            ],
        )
        self.assertEqual(
            self.connection.execute(
                """
                SELECT
                    home_recent_wins,
                    home_recent_draws,
                    home_recent_losses,
                    away_recent_wins,
                    away_recent_draws,
                    away_recent_losses,
                    home_home_points_per_match,
                    away_away_points_per_match,
                    source_provider
                FROM core.fact_match_context_summary
                WHERE match_id = 'WC2026_M001'
                """
            ).fetchall(),
            [(1, 0, 0, 0, 1, 0, 3.0, 1.0, "API-Football")],
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM core.fact_odds_capital_flow"
            ).fetchone()[0],
            0,
        )

if __name__ == '__main__':
    unittest.main()
