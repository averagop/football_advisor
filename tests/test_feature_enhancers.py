from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from football_advisor.db_schema import apply_schema
from football_advisor.feature_enhancers import (
    FormationEnhancer,
    LineupConfirmationEnhancer,
    OddsTrendEnhancer,
    StandingsEnhancer,
)
from football_advisor.models import MatchFeatures, TeamFeatures


class FeatureEnhancerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temp_dir.name) / "enhancers.duckdb"
        with duckdb.connect(str(self.db_path)) as connection:
            apply_schema(connection)
            connection.execute(
                "INSERT INTO core.dim_league_mapping VALUES "
                "('WC', 'World Cup', 'World', CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "INSERT INTO core.dim_team_mapping "
                "(system_team_id, team_standard_name) VALUES "
                "('HOME', 'Home Team'), ('AWAY', 'Away Team')"
            )
            connection.execute(
                """
                INSERT INTO core.fact_match_schedule
                    (match_id, season, system_league_id, match_time,
                     home_team_id, away_team_id)
                VALUES ('MATCH_1', '2026', 'WC', CURRENT_TIMESTAMP,
                        'HOME', 'AWAY')
                """
            )

        self.features = MatchFeatures(
            home=TeamFeatures(name="Home Team", attack_strength=1.2),
            away=TeamFeatures(name="Away Team", defense_strength=0.9),
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_standings_use_matching_league_and_season_facts(self) -> None:
        with duckdb.connect(str(self.db_path)) as connection:
            connection.executemany(
                """
                INSERT INTO core.fact_league_standings
                    (standing_id, system_league_id, system_team_id, season,
                     rank_position, goals_diff, points)
                VALUES (?, 'WC', ?, ?, ?, ?, ?)
                """,
                [
                    ["HOME_2025", "HOME", 2025, 9, -3, 4],
                    ["AWAY_2025", "AWAY", 2025, 1, 10, 12],
                    ["HOME_2026", "HOME", 2026, 2, 5, 7],
                    ["AWAY_2026", "AWAY", 2026, 5, -2, 3],
                ],
            )

        result = StandingsEnhancer(str(self.db_path)).enhance(
            self.features, "MATCH_1"
        )

        self.assertEqual(result.context["standings_home_rank"], 2)
        self.assertEqual(result.context["standings_away_rank"], 5)
        self.assertEqual(result.context["standings_home_points"], 7)
        self.assertEqual(result.context["standings_away_points"], 3)
        self.assertEqual(
            result.context["standings_source_provider"],
            "core.fact_league_standings",
        )

    def test_valid_fresh_lineups_and_injuries_enter_context_only(self) -> None:
        self._insert_provider_mappings()
        now = datetime.now()
        with duckdb.connect(str(self.db_path)) as connection:
            connection.executemany(
                """
                INSERT INTO core.dim_player_mapping
                    (system_player_id, player_standard_name, team_id)
                VALUES (?, ?, ?)
                """,
                [
                    ["P_HOME_1", "Home Starter", "HOME"],
                    ["P_AWAY_1", "Away Starter", "AWAY"],
                    ["P_HOME_2", "Home Injured", "HOME"],
                ],
            )
            connection.executemany(
                """
                INSERT INTO staging.stg_lineups
                    (provider_match_id, player_name, team_name, role,
                     formation, source_provider, etl_insert_timestamp)
                VALUES ('9001', ?, ?, 'starter', ?, 'API-Football', ?)
                """,
                [
                    ["Home Starter", "Home Provider", "4-3-3", now],
                    ["Away Starter", "Away Provider", "5-4-1", now],
                ],
            )
            connection.execute(
                """
                INSERT INTO staging.stg_injuries
                    (provider_match_id, player_name, team_name, injury_type,
                     reason, source_provider, etl_insert_timestamp)
                VALUES ('9001', 'Home Injured', 'Home Provider', 'Missing',
                        'Knee', 'API-Football', ?)
                """,
                [now],
            )

        result = LineupConfirmationEnhancer(str(self.db_path)).enhance(
            self.features, "MATCH_1"
        )
        formation_result = FormationEnhancer(str(self.db_path)).enhance(
            self.features, "MATCH_1"
        )

        self.assertEqual(result.home, self.features.home)
        self.assertEqual(result.away, self.features.away)
        self.assertEqual(result.context["lineup_home_confirmed_count"], 1)
        self.assertEqual(result.context["lineup_away_confirmed_count"], 1)
        self.assertEqual(result.context["injury_home_confirmed_count"], 1)
        self.assertEqual(result.context["injury_away_confirmed_count"], 0)
        self.assertEqual(
            result.context["lineup_injury_source_provider"], "API-Football"
        )
        self.assertIsNotNone(result.context["lineup_injury_updated_at"])
        self.assertEqual(formation_result.context["formation_home"], "4-3-3")
        self.assertEqual(formation_result.context["formation_away"], "5-4-1")

    def test_stale_or_unmapped_players_do_not_enter_context(self) -> None:
        self._insert_provider_mappings()
        stale = datetime.now() - timedelta(minutes=31)
        with duckdb.connect(str(self.db_path)) as connection:
            connection.execute(
                """
                INSERT INTO core.dim_player_mapping
                    (system_player_id, player_standard_name, team_id)
                VALUES ('P_HOME_1', 'Home Starter', 'HOME')
                """
            )
            connection.executemany(
                """
                INSERT INTO staging.stg_lineups
                    (provider_match_id, player_name, team_name, role,
                     formation, source_provider, etl_insert_timestamp)
                VALUES ('9001', ?, 'Home Provider', 'starter', '4-3-3',
                        'API-Football', ?)
                """,
                [
                    ["Home Starter", stale],
                    ["Unmapped Player", datetime.now()],
                ],
            )

        lineup_result = LineupConfirmationEnhancer(str(self.db_path)).enhance(
            self.features, "MATCH_1"
        )
        formation_result = FormationEnhancer(str(self.db_path)).enhance(
            self.features, "MATCH_1"
        )

        self.assertNotIn("lineup_home_confirmed_count", lineup_result.context)
        self.assertNotIn("formation_home", formation_result.context)

    def _insert_provider_mappings(self) -> None:
        with duckdb.connect(str(self.db_path)) as connection:
            connection.executemany(
                """
                INSERT INTO core.dim_provider_team_mapping
                    (provider_name, provider_team_id, system_team_id,
                     provider_team_name)
                VALUES ('API-Football', ?, ?, ?)
                """,
                [
                    ["10", "HOME", "Home Provider"],
                    ["20", "AWAY", "Away Provider"],
                ],
            )
            connection.execute(
                """
                INSERT INTO core.dim_provider_match_mapping
                    (provider_name, provider_match_id, system_match_id,
                     resolution_method, resolution_confidence, verified_at)
                VALUES ('API-Football', '9001', 'MATCH_1', 'test', 1.0,
                        CURRENT_TIMESTAMP)
                """
            )

    # ---- OddsTrendEnhancer 测试 ----

    def test_odds_trend_insufficient_snapshots_sets_status(self):
        """无竞彩官方 SPF 快照时返回 insufficient_comparable_snapshots 状态。"""
        with duckdb.connect(str(self.db_path)) as connection:
            connection.execute(
                """
                INSERT INTO core.fact_odds_capital_flow
                    (match_id, snapshot_time, odds_type, bookmaker_name,
                     source_provider, home_odds, draw_odds, away_odds,
                     sharp_money_ratio, etl_insert_timestamp)
                VALUES ('MATCH_1', CURRENT_TIMESTAMP, '1X2', 'SportteryOfficialWeb',
                        'SportteryOfficialWeb', 2.0, 3.0, 4.0, 0.5, CURRENT_TIMESTAMP)
                """
            )

        result = OddsTrendEnhancer(str(self.db_path)).enhance(
            self.features, "MATCH_1"
        )
        self.assertEqual(
            result.context.get("odds_trend_status"),
            "insufficient_comparable_snapshots",
        )

    def test_odds_trend_requires_sporttery_provider(self):
        """第三方赔率不得用于竞彩趋势，应标记 insufficient。"""
        with duckdb.connect(str(self.db_path)) as connection:
            connection.executemany(
                """
                INSERT INTO core.fact_odds_capital_flow
                    (match_id, snapshot_time, odds_type, bookmaker_name,
                     source_provider, home_odds, draw_odds, away_odds,
                     sharp_money_ratio, etl_insert_timestamp)
                VALUES (?, ?, '1X2', 'bet365',
                        'ThirdParty', ?, ?, ?, 0.3, CURRENT_TIMESTAMP)
                """,
                [
                    ["MATCH_1", "2026-06-01 10:00:00", 2.1, 3.2, 3.8],
                    ["MATCH_1", "2026-06-01 11:00:00", 2.0, 3.3, 3.9],
                ],
            )

        result = OddsTrendEnhancer(str(self.db_path)).enhance(
            self.features, "MATCH_1"
        )
        self.assertEqual(
            result.context.get("odds_trend_status"),
            "insufficient_comparable_snapshots",
        )

    def test_odds_trend_requires_1x2_odds_type(self):
        """非 1X2 玩法（如 RQSPF）不得混入竞彩趋势。"""
        with duckdb.connect(str(self.db_path)) as connection:
            connection.executemany(
                """
                INSERT INTO core.fact_odds_capital_flow
                    (match_id, snapshot_time, odds_type, bookmaker_name,
                     source_provider, home_odds, draw_odds, away_odds,
                     sharp_money_ratio, etl_insert_timestamp)
                VALUES (?, ?, 'SPORTTERY_RQSPF', 'SportteryOfficialWeb',
                        'SportteryOfficialWeb', ?, ?, ?, 0.5, CURRENT_TIMESTAMP)
                """,
                [
                    ["MATCH_1", "2026-06-01 10:00:00", 1.8, 3.0, 4.5],
                    ["MATCH_1", "2026-06-01 11:00:00", 1.7, 3.1, 4.6],
                ],
            )

        result = OddsTrendEnhancer(str(self.db_path)).enhance(
            self.features, "MATCH_1"
        )
        self.assertEqual(
            result.context.get("odds_trend_status"),
            "insufficient_comparable_snapshots",
        )

    def test_odds_trend_with_valid_sporttery_spf_data(self):
        """竞彩官方 SPF 赔率有 >=2 快照时正常返回趋势。"""
        with duckdb.connect(str(self.db_path)) as connection:
            connection.execute(
                """
                INSERT INTO core.fact_odds_capital_flow
                    (match_id, snapshot_time, odds_type, bookmaker_name,
                     source_provider, home_odds, draw_odds, away_odds,
                     sharp_money_ratio, etl_insert_timestamp)
                VALUES ('MATCH_1', '2026-06-01 10:00:00'::TIMESTAMP, '1X2',
                        'SportteryOfficialWeb', 'SportteryOfficialWeb',
                        2.5, 3.0, 2.8, 0.5, CURRENT_TIMESTAMP)
                """
            )
            connection.execute(
                """
                INSERT INTO core.fact_odds_capital_flow
                    (match_id, snapshot_time, odds_type, bookmaker_name,
                     source_provider, home_odds, draw_odds, away_odds,
                     sharp_money_ratio, etl_insert_timestamp)
                VALUES ('MATCH_1', '2026-06-01 11:00:00'::TIMESTAMP, '1X2',
                        'SportteryOfficialWeb', 'SportteryOfficialWeb',
                        2.2, 3.1, 3.0, 0.5, CURRENT_TIMESTAMP)
                """
            )
            # 验证插入成功
            cnt = connection.execute(
                "SELECT COUNT(*) FROM core.fact_odds_capital_flow WHERE match_id = 'MATCH_1'"
            ).fetchone()
            self.assertEqual(cnt[0], 2, f"Expected 2 rows inserted, got {cnt}")

        result = OddsTrendEnhancer(str(self.db_path)).enhance(
            self.features, "MATCH_1"
        )
        self.assertEqual(result.context.get("odds_trend_status"), "success",
                         f"Context: {result.context}")
        self.assertIn("odds_trend_home_direction", result.context)
        self.assertIn("odds_trend_home_change_pct", result.context)
        self.assertEqual(result.context.get("odds_trend_snapshot_count"), 2)


if __name__ == "__main__":
    unittest.main()
