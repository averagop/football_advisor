from __future__ import annotations

import unittest
from datetime import datetime, timezone

from football_advisor.config import FeatureBuilderConfig
from football_advisor.feature_builder import (
    DuckDBFeatureBuilder,
    FeatureDataUnavailable,
)
from football_advisor.models import MatchRequest
from football_advisor.query_tools import QueryResult


class FakeQueryTool:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple[str, list | None]] = []

    def query_match_stats(self, sql: str, params: list | None = None) -> QueryResult:
        self.calls.append((sql, params))
        columns = list(self.rows[0].keys()) if self.rows else []
        return QueryResult(columns=columns, rows=self.rows, row_count=len(self.rows))

    @property
    def sql(self) -> str:
        return self.calls[-1][0] if self.calls else ""

    @property
    def params(self) -> list | None:
        return self.calls[-1][1] if self.calls else None


class FeatureBuilderTests(unittest.TestCase):
    def test_loads_latest_sporttery_detail_odds(self):
        class DetailQueryTool:
            def query_match_stats(self, sql, params=None):
                if "fact_sporttery_odds_detail" in sql:
                    rows = [
                        {"odds_type": "CORRECT_SCORE", "outcome_key": "1:0", "odds_value": 7.0},
                        {"odds_type": "TOTAL_GOALS", "outcome_key": "2", "odds_value": 3.2},
                        {"odds_type": "HALF_FULL", "outcome_key": "胜胜", "odds_value": 2.8},
                    ]
                else:
                    rows = [{
                        "match_id": "SYS_EPL_M001",
                        "home_team_name": "Mexico",
                        "away_team_name": "South Africa",
                        "latest_home_odds": 1.26,
                        "latest_draw_odds": 4.45,
                        "latest_away_odds": 9.0,
                    }]
                return QueryResult(columns=list(rows[0]), rows=rows, row_count=len(rows))

        features = DuckDBFeatureBuilder(query_tool=DetailQueryTool()).build(
            MatchRequest(query="Mexico vs South Africa", match_id="SYS_EPL_M001")
        )

        self.assertEqual(features.odds_correct_score, {"1:0": 7.0})
        self.assertEqual(features.odds_total_goals, {"2": 3.2})
        self.assertEqual(features.odds_half_full, {"胜胜": 2.8})

    def test_builds_features_from_llm_view_row(self):
        row = {
            "match_id": "M1",
            "home_team_name": "Arsenal",
            "away_team_name": "Chelsea",
            "home_elo": 1610,
            "away_elo": 1560,
            "home_attack_strength": 1.12,
            "away_attack_strength": 1.02,
            "home_defense_strength": 0.92,
            "away_defense_strength": 1.04,
            "home_recent_points_per_match": 2.1,
            "away_recent_points_per_match": 1.5,
            "home_home_points_per_match": 2.3,
            "away_away_points_per_match": 1.2,
            "home_injury_penalty": 0.08,
            "away_injury_penalty": 0.18,
            "latest_home_odds": 2.1,
            "latest_draw_odds": 3.4,
            "latest_away_odds": 3.6,
            "latest_rqspf_home_odds": 4.5,
            "latest_rqspf_draw_odds": 3.8,
            "latest_rqspf_away_odds": 1.62,
            "rqspf_handicap_line": -1.0,
            "totals_line": 2.75,
            "data_updated_at": datetime(2026, 5, 23, 8, 0, tzinfo=timezone.utc),
            "data_sources": "UNDERSTAT,PINNACLE",
            "anti_consensus_trap_flag": True,
            "derived_team_stats_quality_flag": False,
        }
        query_tool = FakeQueryTool([row])
        features = DuckDBFeatureBuilder(query_tool=query_tool).build(
            MatchRequest(query="Arsenal vs Chelsea", match_id="M1")
        )

        self.assertIn("core.view_llm_match_prediction_base", query_tool.calls[0][0])
        self.assertEqual(query_tool.calls[0][1], ["M1"])
        self.assertEqual(features.home.name, "Arsenal")
        self.assertEqual(features.away.name, "Chelsea")
        self.assertEqual(features.odds_1x2, {"home": 2.1, "draw": 3.4, "away": 3.6})
        self.assertEqual(
            features.odds_rqspf, {"home": 4.5, "draw": 3.8, "away": 1.62}
        )
        self.assertEqual(features.rqspf_handicap_line, -1.0)
        self.assertEqual(features.totals_line, 2.75)
        self.assertEqual(features.sources, ["UNDERSTAT", "PINNACLE"])
        self.assertEqual(features.context["feature_source"], "duckdb_view")
        self.assertTrue(features.context["anti_consensus_trap_flag"])
        self.assertFalse(features.context["derived_team_stats_quality_flag"])

    def test_rejects_missing_query_keys(self):
        with self.assertRaises(FeatureDataUnavailable):
            DuckDBFeatureBuilder(query_tool=FakeQueryTool([])).build(
                MatchRequest(query="unknown")
            )

    def test_builds_features_with_quality_flag_when_1x2_odds_are_incomplete(self):
        row = {
            "home_team_name": "A",
            "away_team_name": "B",
            "latest_home_odds": 2.0,
            "latest_draw_odds": None,
            "latest_away_odds": 3.0,
            "missing_1x2_odds_flag": True,
            "no_bet_data_quality_flag": True,
        }
        query_tool = FakeQueryTool([row])
        features = DuckDBFeatureBuilder(query_tool=query_tool).build(
            MatchRequest(query="A vs B", home_team="A", away_team="B")
        )

        self.assertEqual(query_tool.calls[0][1], ["A", "B"])
        self.assertEqual(features.home.name, "A")
        self.assertEqual(features.away.name, "B")
        self.assertEqual(features.odds_1x2, {"home": 2.0, "away": 3.0})
        self.assertTrue(features.context["missing_1x2_odds_flag"])
        self.assertTrue(features.context["no_bet_data_quality_flag"])

    def test_naive_duckdb_timestamp_is_treated_as_utc(self):
        row = {
            "match_id": "M_LOCAL_TIME",
            "home_team_name": "A",
            "away_team_name": "B",
            "latest_home_odds": 2.0,
            "latest_draw_odds": 3.0,
            "latest_away_odds": 3.2,
            "data_updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }
        features = DuckDBFeatureBuilder(query_tool=FakeQueryTool([row])).build(
            MatchRequest(query="A vs B", match_id="M_LOCAL_TIME")
        )

        age_seconds = (
            datetime.now(timezone.utc) - features.updated_at
        ).total_seconds()
        self.assertLess(abs(age_seconds), 120)

    def test_uses_custom_query_from_config(self):
        config = FeatureBuilderConfig(
            feature_view_or_query="SELECT * FROM my_custom_feature_view"
        )
        query_tool = FakeQueryTool([{"match_id": "M1"}])
        DuckDBFeatureBuilder(query_tool=query_tool, config=config).build(
            MatchRequest(query="test", match_id="M1")
        )

        self.assertIn("(SELECT * FROM my_custom_feature_view) AS base_query", query_tool.calls[0][0])
        self.assertEqual(query_tool.calls[0][1], ["M1"])

    def test_dynamically_loads_unknown_fields_into_context(self):
        row = {
            "match_id": "M1",
            "home_team_name": "A",
            "away_team_name": "B",
            "home_elo": 1500,
            "away_elo": 1500,
            "custom_dynamic_feature_1": 42.5,
            "custom_dynamic_feature_2": "high_intensity",
            "latest_home_odds": 2.0,
        }
        query_tool = FakeQueryTool([row])
        features = DuckDBFeatureBuilder(query_tool=query_tool).build(
            MatchRequest(query="test", match_id="M1")
        )

        # Base fields shouldn't be in context
        self.assertNotIn("home_team_name", features.context)
        self.assertNotIn("home_elo", features.context)
        self.assertNotIn("latest_home_odds", features.context)

        # Dynamic fields should be available
        self.assertEqual(features.context["custom_dynamic_feature_1"], 42.5)
        self.assertEqual(features.context["custom_dynamic_feature_2"], "high_intensity")
        self.assertEqual(features.context["match_id"], "M1")



if __name__ == "__main__":
    unittest.main()
