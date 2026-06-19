from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import duckdb

from football_advisor.db_schema import initialize_database
from football_advisor.third_party_odds_client import ThirdPartyOddsClient


class ThirdPartyOddsClientTests(unittest.TestCase):
    def test_the_odds_api_payload_writes_odds_without_capital_flow(self):
        payload = [
            {
                "id": "event-1",
                "commence_time": "2026-06-08T03:00:00Z",
                "home_team": "Greece",
                "away_team": "Italy",
                "bookmakers": [
                    {
                        "title": "Bookmaker A",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Greece", "price": 2.20},
                                    {"name": "Draw", "price": 3.10},
                                    {"name": "Italy", "price": 3.00},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]

        with tempfile.NamedTemporaryFile(suffix=".db") as temp:
            db_path = temp.name
        initialize_database(db_path)

        with duckdb.connect(db_path) as conn:
            self._seed_match(conn)
            client = ThirdPartyOddsClient(the_odds_api_token="token")
            result = client.process_the_odds_api_payload(
                conn,
                payload,
                "Greece",
                "Italy",
                datetime(2026, 6, 8, 3, 0, tzinfo=timezone.utc),
            )
            rows = conn.execute(
                """
                SELECT bookmaker_name, home_odds, draw_odds, away_odds, source_provider
                FROM core.fact_odds_capital_flow
                WHERE match_id = 'M_TEST'
                """
            ).fetchall()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["event_count"], 1)
        self.assertEqual(result["odds_count"], 1)
        self.assertEqual(result["merged_odds_count"], 1)
        self.assertEqual(result["capital_flow_fields"], [])
        self.assertEqual(rows, [("Bookmaker A", 2.20, 3.10, 3.00, "TheOddsAPI")])

    def test_rapidapi_payload_reports_capital_flow_fields_when_present(self):
        payload = {
            "events": [
                {
                    "id": "rapid-1",
                    "home_team": "Greece",
                    "away_team": "Italy",
                    "commence_time": "2026-06-08T03:00:00Z",
                    "bookmakers": [
                        {
                            "title": "Exchange A",
                            "markets": [
                                {
                                    "key": "h2h",
                                    "outcomes": [
                                        {"name": "Greece", "price": 2.24},
                                        {"name": "Draw", "price": 3.15},
                                        {"name": "Italy", "price": 2.96},
                                    ],
                                }
                            ],
                        }
                    ],
                    "totalMatched": 12345.6,
                }
            ]
        }

        with tempfile.NamedTemporaryFile(suffix=".db") as temp:
            db_path = temp.name
        initialize_database(db_path)

        with duckdb.connect(db_path) as conn:
            self._seed_match(conn)
            client = ThirdPartyOddsClient(rapidapi_token="token")
            result = client.process_rapidapi_payload(
                conn,
                payload,
                "Greece",
                "Italy",
                datetime(2026, 6, 8, 3, 0, tzinfo=timezone.utc),
            )
            rows = conn.execute(
                """
                SELECT bookmaker_name, matched_volume, source_provider
                FROM core.fact_odds_capital_flow
                WHERE match_id = 'M_TEST'
                """
            ).fetchall()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["merged_odds_count"], 1)
        self.assertEqual(result["capital_flow_fields"], ["totalMatched"])
        self.assertEqual(rows, [("Exchange A", 12345.6, "RapidAPI-OddsFeed")])

    @staticmethod
    def _seed_match(connection):
        match_time = datetime(2026, 6, 8, 3, 0, tzinfo=timezone.utc)
        connection.execute(
            """
            INSERT INTO core.dim_league_mapping (
                system_league_id, league_standard_name, country
            ) VALUES ('INTL_FRIENDLY', 'International Friendly', 'International')
            """
        )
        connection.execute(
            """
            INSERT INTO core.dim_team_mapping (
                system_team_id, team_standard_name, country
            ) VALUES ('WC_TEAM_GRE', 'Greece', 'Greece')
            """
        )
        connection.execute(
            """
            INSERT INTO core.dim_team_mapping (
                system_team_id, team_standard_name, country
            ) VALUES ('WC_TEAM_ITA', 'Italy', 'Italy')
            """
        )
        connection.execute(
            """
            INSERT INTO core.fact_match_schedule (
                match_id, season, system_league_id, match_time,
                home_team_id, away_team_id, status, source_provider, updated_at
            ) VALUES (
                'M_TEST', '2026', 'INTL_FRIENDLY', ?, 'WC_TEAM_GRE',
                'WC_TEAM_ITA', 'PRE-MATCH', 'TEST', ?
            )
            """,
            [match_time, match_time - timedelta(minutes=5)],
        )


if __name__ == "__main__":
    unittest.main()
