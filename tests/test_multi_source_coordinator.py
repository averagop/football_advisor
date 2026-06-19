from __future__ import annotations

import unittest
from datetime import datetime, timezone
import tempfile
from pathlib import Path
from unittest.mock import patch

from football_advisor.config import SyncConfig
from football_advisor.multi_source_coordinator import (
    MultiSourceCoordinator,
    MultiSourceResult,
    SourceResult,
)


class MultiSourceCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.sync_config = SyncConfig()

    def test_excluding_sporttery_does_not_trigger_third_party_odds_gap_fill(self):
        coordinator = MultiSourceCoordinator(
            sync_config=self.sync_config,
            duckdb_path=":memory:",
        )
        attempted_gaps: list[str] = []

        with patch.object(
            coordinator,
            "_try_fill_gap",
            side_effect=lambda gap, *_: attempted_gaps.append(gap),
        ):
            coordinator._fill_critical_gaps(
                [],
                "Germany",
                "Curacao",
                None,
                excluded_providers={"SportteryOfficialWeb"},
            )

        self.assertNotIn("SPF赔率缺失", attempted_gaps)
        self.assertNotIn("RQSPF赔率缺失", attempted_gaps)

    def test_fetch_all_without_any_tokens_still_runs_public_sporttery_source(self):
        class FakeSportteryClient:
            def __init__(self, base_url=""):
                self.base_url = base_url

            def fetch_football_odds(self, connection, target_date=None):
                return {
                    "status": "success",
                    "match_count": 1,
                    "odds_count": 2,
                    "merged_match_count": 1,
                    "merged_odds_count": 2,
                    "skipped_unmapped_count": 0,
                }

        coordinator = MultiSourceCoordinator(
            sync_config=self.sync_config,
            duckdb_path=":memory:",
        )
        with patch("football_advisor.sporttery_client.SportteryClient", FakeSportteryClient):
            result = coordinator.fetch_all("Arsenal", "Chelsea")
        self.assertIsInstance(result, MultiSourceResult)
        self.assertEqual(len(result.results), 8)
        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.skipped_count, 7)
        self.assertFalse(result.all_failed)
        self.assertNotIn(
            "OddsMatrix",
            {source_result.provider_name for source_result in result.results},
        )
        self.assertIn(
            "SportteryOfficialWeb",
            {source_result.provider_name for source_result in result.results},
        )
        self.assertIn(
            "TheOddsAPI",
            {source_result.provider_name for source_result in result.results},
        )
        self.assertIn(
            "RapidAPI-OddsFeed",
            {source_result.provider_name for source_result in result.results},
        )

    def test_third_party_odds_sources_run_when_configured(self):
        calls = []

        class FakeThirdPartyOddsClient:
            def __init__(self, **kwargs):
                calls.append(("init", kwargs))

            def fetch_the_odds_api_odds(self, connection, home_team, away_team, kickoff_time):
                calls.append(("the_odds", home_team, away_team, kickoff_time))
                return {
                    "status": "success",
                    "event_count": 1,
                    "odds_count": 2,
                    "matched_event_count": 1,
                    "capital_flow_fields": [],
                }

            def fetch_rapidapi_odds_feed(self, connection, home_team, away_team, kickoff_time):
                calls.append(("rapid", home_team, away_team, kickoff_time))
                return {
                    "status": "success",
                    "event_count": 1,
                    "odds_count": 1,
                    "matched_event_count": 1,
                    "capital_flow_fields": ["matched_volume"],
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = MultiSourceCoordinator(
                sync_config=SyncConfig(
                    the_odds_api_token="the-odds-secret",
                    the_odds_api_base_url="https://the-odds.example/v4",
                    rapidapi_token="rapid-secret",
                    odds_feed_rapid_host="odds-feed.example.p.rapidapi.com",
                    odds_feed_rapid_base_url="https://odds-feed.example",
                ),
                duckdb_path=str(Path(temp_dir) / "test.duckdb"),
            )

            with patch(
                "football_advisor.third_party_odds_client.ThirdPartyOddsClient",
                FakeThirdPartyOddsClient,
            ):
                result = coordinator.fetch_all(
                    "Greece",
                    "Italy",
                    datetime(2026, 6, 8, 3, 0, tzinfo=timezone.utc),
                )

        the_odds = next(item for item in result.results if item.provider_name == "TheOddsAPI")
        rapid = next(item for item in result.results if item.provider_name == "RapidAPI-OddsFeed")
        self.assertEqual(the_odds.status, "success")
        self.assertIn("odds_cross_check", the_odds.data["covered_fields"])
        self.assertEqual(the_odds.data["capital_flow_fields"], [])
        self.assertEqual(rapid.status, "success")
        self.assertIn("capital_flow", rapid.data["covered_fields"])
        self.assertEqual(rapid.data["capital_flow_fields"], ["matched_volume"])
        self.assertIn(("the_odds", "Greece", "Italy", datetime(2026, 6, 8, 3, 0, tzinfo=timezone.utc)), calls)

    def test_multi_source_result_properties(self):
        results = [
            SourceResult(provider_name="A", status="success", data={"covered_fields": ["odds", "stats"]}),
            SourceResult(provider_name="B", status="success", data={"covered_fields": ["odds", "schedule"]}),
            SourceResult(provider_name="C", status="skipped_no_token", data={"covered_fields": []}),
            SourceResult(provider_name="D", status="failed", error="timeout"),
        ]
        msr = MultiSourceResult(
            results=results,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            total_elapsed_ms=100.0,
        )
        self.assertEqual(msr.success_count, 2)
        self.assertEqual(msr.failure_count, 1)
        self.assertEqual(msr.skipped_count, 1)
        self.assertFalse(msr.all_failed)
        self.assertEqual(msr.coverage_summary, {"odds": 2, "stats": 1, "schedule": 1})

    def test_all_failed_when_no_success(self):
        results = [
            SourceResult(provider_name="A", status="failed", error="err1"),
            SourceResult(provider_name="B", status="failed", error="err2"),
        ]
        msr = MultiSourceResult(
            results=results,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            total_elapsed_ms=50.0,
        )
        self.assertTrue(msr.all_failed)
        self.assertEqual(msr.success_count, 0)
        self.assertEqual(msr.failure_count, 2)

    def test_api_football_uses_recent_friendlies_when_target_payload_is_empty(self):
        calls = {}

        class EmptyTargetApiFootballClient:
            def __init__(self, api_token=None, base_url=""):
                pass

            def fetch_match_data(self, connection, ref_time, home_team, away_team):
                calls["target"] = (ref_time, home_team, away_team)
                return {
                    "status": "match_not_found",
                    "odds_count": 0,
                    "stats_count": 0,
                    "merged_count": 0,
                }

            def fetch_recent_friendlies(
                self,
                connection,
                target_match_time,
                home_team,
                away_team,
                date_from,
                date_to,
            ):
                calls["recent"] = (
                    target_match_time,
                    home_team,
                    away_team,
                    date_from,
                    date_to,
                )
                return {
                    "status": "success",
                    "friendly_count": 1,
                    "mapped_team_stats_count": 1,
                    "skipped_unmapped_count": 1,
                }

            def fetch_team_last_matches(
                self, connection, target_match_id,
                provider_team_id, system_team_id, last_n=10,
            ):
                return {
                    "status": "success",
                    "match_count": 0,
                    "mapped_stats_count": 0,
                }

            PROVIDER_NAME = "API-Football"

            def _find_target_match_id(self, connection, target_match_time, home_team, away_team):
                return "M_TEST"

        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = MultiSourceCoordinator(
                sync_config=SyncConfig(api_football_token="real-token"),
                duckdb_path=str(Path(temp_dir) / "test.duckdb"),
            )

            with patch(
                "football_advisor.api_football_client.ApiFootballClient",
                EmptyTargetApiFootballClient,
            ):
                result = coordinator.fetch_all(
                    "Mexico",
                    "South Africa",
                    datetime(2026, 6, 11, 19, 0, tzinfo=timezone.utc),
                )

        api_result = next(
            item for item in result.results if item.provider_name == "API-Football"
        )
        self.assertEqual(api_result.status, "success")
        self.assertEqual(api_result.data["target_status"], "match_not_found")
        self.assertEqual(api_result.data["friendly_count"], 1)
        self.assertEqual(api_result.data["mapped_team_stats_count"], 1)
        self.assertIn("recent_form", api_result.data["covered_fields"])
        self.assertIn("recent", calls)

    def test_sporttery_source_runs_when_base_url_is_configured(self):
        calls = {}

        class FakeSportteryClient:
            def __init__(self, base_url=""):
                calls["base_url"] = base_url

            def fetch_football_odds(self, connection, target_date=None):
                calls["target_date"] = target_date
                return {
                    "status": "success",
                    "match_count": 1,
                    "odds_count": 2,
                    "merged_match_count": 1,
                    "merged_odds_count": 2,
                    "skipped_unmapped_count": 0,
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = MultiSourceCoordinator(
                sync_config=SyncConfig(
                    sporttery_base_url="https://sporttery.example",
                ),
                duckdb_path=str(Path(temp_dir) / "test.duckdb"),
            )

            with patch("football_advisor.sporttery_client.SportteryClient", FakeSportteryClient):
                result = coordinator.fetch_all(
                    "Arsenal",
                    "Chelsea",
                    datetime(2026, 6, 8, 19, 30, tzinfo=timezone.utc),
                )

        sporttery_result = next(
            item for item in result.results if item.provider_name == "SportteryOfficialWeb"
        )
        self.assertEqual(sporttery_result.status, "success")
        self.assertEqual(sporttery_result.data["odds_count"], 2)
        self.assertIn("sporttery_official_odds", sporttery_result.data["covered_fields"])
        self.assertEqual(calls["base_url"], "https://sporttery.example")


if __name__ == "__main__":
    unittest.main()
