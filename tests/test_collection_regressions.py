from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import duckdb

from football_advisor.odds_movement import OddsMovementEngine
from football_advisor.post_match_collector import PostMatchCollector
from football_advisor.pre_match_collector import PreMatchCollector


class _QueryResult:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _JsonResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


class CollectionRegressionTests(unittest.TestCase):
    def test_fixture_stats_count_only_new_rows(self):
        class EventConnection:
            def __init__(self):
                self.event_ids = set()

            def execute(self, sql, params):
                event_id = params[0]
                if event_id in self.event_ids:
                    return _QueryResult()
                self.event_ids.add(event_id)
                return _QueryResult((event_id,))

        class Collector(PostMatchCollector):
            def _fetch_stats(self, fixture_id):
                return [
                    {
                        "team": {"id": 10},
                        "statistics": [{"type": "Total Shots", "value": 8}],
                    }
                ]

            def _resolve_team(self, conn, provider_team_id):
                return "TEAM_10"

        collector = Collector(api_token="token")
        connection = EventConnection()
        fixture = {"fixture": {"id": 99}}

        self.assertEqual(collector._sink_fixture_stats(connection, fixture, "MATCH_99"), 1)
        self.assertEqual(collector._sink_fixture_stats(connection, fixture, "MATCH_99"), 0)

    def test_recent_form_and_h2h_do_not_count_existing_matches(self):
        fixture = {
            "fixture": {
                "id": 99,
                "date": "2026-06-01T12:00:00+00:00",
                "status": {"short": "FT"},
            },
            "league": {"id": 1},
            "teams": {"home": {"id": 10}, "away": {"id": 20}},
            "goals": {"home": 1, "away": 0},
        }

        class ExistingMatchConnection:
            def execute(self, sql, params=None):
                if "dim_provider_team_mapping" in sql:
                    system_id = params[0]
                    return _QueryResult(("10" if system_id == "HOME" else "20",))
                if "fact_match_schedule" in sql and "SELECT match_id" in sql:
                    return _QueryResult(("API_FOOTBALL_99",))
                raise AssertionError(sql)

        collector = PreMatchCollector(api_token="token")
        response = _JsonResponse({"response": [fixture]})
        with patch("football_advisor.pre_match_collector.urllib.request.urlopen", return_value=response):
            self.assertEqual(collector._collect_recent_form(ExistingMatchConnection(), "HOME"), 0)
            self.assertEqual(collector._collect_h2h(ExistingMatchConnection(), "HOME", "AWAY"), 0)

    def test_injuries_are_collected_once_per_team_and_match(self):
        fixtures = [
            {
                "fixture": {"id": fixture_id, "status": {"short": "NS"}},
                "league": {"id": 1, "season": 2026},
                "teams": {"home": {"id": 10}, "away": {"id": 20}},
            }
            for fixture_id in (101, 102)
        ]

        class Collector(PreMatchCollector):
            def __init__(self):
                super().__init__(api_token="token", lookahead_days=0)
                self.injury_calls = []

            def _get_last_collected_date(self, conn):
                return None

            def _load_league_map(self, conn):
                return {1: "LEAGUE"}

            def _fetch_fixtures(self, target_date):
                return fixtures

            def _upsert_schedule(self, *args):
                return True

            def _snapshot_odds(self, *args):
                return 0

            def _collect_lineups(self, *args):
                return 0

            def _resolve_team(self, conn, provider_team_id):
                return f"TEAM_{provider_team_id}"

            def _collect_recent_form(self, *args):
                return 0

            def _collect_h2h(self, *args):
                return 0

            def _collect_team_stats(self, *args):
                return 0

            def _collect_standings(self, *args):
                return 0

            def _collect_injuries(self, conn, team_id, provider_match_id):
                self.injury_calls.append((team_id, provider_match_id))
                return 0

            def _set_last_collected_date(self, *args):
                return None

        collector = Collector()
        collector._collect(object())

        self.assertEqual(
            collector.injury_calls,
            [
                ("TEAM_10", "101"),
                ("TEAM_20", "101"),
                ("TEAM_10", "102"),
                ("TEAM_20", "102"),
            ],
        )

    def test_sharp_move_uses_largest_single_interval_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "odds.duckdb"
            with duckdb.connect(str(db_path)) as connection:
                connection.execute("CREATE SCHEMA staging")
                connection.execute(
                    """
                    CREATE TABLE staging.stg_odds_snapshot (
                        snapshot_date DATE,
                        match_id VARCHAR,
                        market_type VARCHAR,
                        home_odds DOUBLE,
                        draw_odds DOUBLE,
                        away_odds DOUBLE
                    )
                    """
                )
                connection.executemany(
                    "INSERT INTO staging.stg_odds_snapshot VALUES (?, 'M1', '1X2', ?, ?, ?)",
                    [
                        [date(2026, 6, 1), 2.00, 3.00, 4.00],
                        [date(2026, 6, 2), 1.68, 3.00, 4.00],
                        [date(2026, 6, 3), 1.68, 3.00, 3.20],
                    ],
                )

            signal = OddsMovementEngine(str(db_path)).analyze_match("M1")

        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, "away")


if __name__ == "__main__":
    unittest.main()
