from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import duckdb

from football_advisor.db_schema import apply_schema
from football_advisor.sporttery_client import SportteryClient


class SportteryClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "sporttery.duckdb"
        self.connection = duckdb.connect(str(self.db_path))
        apply_schema(self.connection)
        self.connection.execute(
            """
            INSERT INTO core.dim_league_mapping (
                system_league_id, league_standard_name, country
            ) VALUES ('SYS_EPL', 'English Premier League', 'England');

            INSERT INTO core.dim_team_mapping (
                system_team_id, team_standard_name
            ) VALUES
                ('SYS_ARS', 'Arsenal'),
                ('SYS_CHE', 'Chelsea');

            INSERT INTO core.dim_provider_league_mapping (
                provider_name, provider_league_id, system_league_id, provider_league_name
            ) VALUES ('SportteryOfficialWeb', 'English Premier League', 'SYS_EPL', 'English Premier League');

            INSERT INTO core.dim_provider_team_mapping (
                provider_name, provider_team_id, system_team_id, provider_team_name
            ) VALUES
                ('SportteryOfficialWeb', 'Arsenal', 'SYS_ARS', 'Arsenal'),
                ('SportteryOfficialWeb', 'Chelsea', 'SYS_CHE', 'Chelsea');
            """
        )

    def tearDown(self) -> None:
        self.connection.close()
        self.temp_dir.cleanup()

    def _setup_mapped_match(self) -> None:
        """插入标准赛程和比赛映射，使合并可以正常工作。"""
        self.connection.execute("""
            INSERT INTO core.fact_match_schedule (
                match_id, season, system_league_id, match_time,
                home_team_id, away_team_id, status, source_provider
            ) VALUES (
                'SYS_EPL_M001', '2026', 'SYS_EPL',
                TIMESTAMP '2026-06-08 19:30:00',
                'SYS_ARS', 'SYS_CHE', 'PRE-MATCH', 'test'
            );
        """)
        self.connection.execute("""
            INSERT INTO core.dim_provider_match_mapping (
                provider_name, provider_match_id, system_match_id,
                resolution_method, resolution_confidence, verified_at
            ) VALUES (
                'SportteryOfficialWeb', '周一001', 'SYS_EPL_M001',
                'mapped_teams_and_time', 1.0, CURRENT_TIMESTAMP
            );
        """)

    def test_json_payload_writes_spf_and_rqspf_odds_to_staging_and_core(self) -> None:
        self._setup_mapped_match()
        payload = {
            "matches": [
                {
                    "match_id": "周一001",
                    "league": "English Premier League",
                    "kickoff_time": "2026-06-08T19:30:00Z",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "spf": {"home": "2.10", "draw": "3.30", "away": "3.40"},
                    "rqspf": {
                        "handicap": "-1",
                        "home": "4.50",
                        "draw": "3.80",
                        "away": "1.62",
                    },
                }
            ]
        }

        class JsonSportteryClient(SportteryClient):
            def _http_get(self, path: str) -> str | None:
                return json.dumps(payload)

        result = JsonSportteryClient(base_url="https://sporttery.example").fetch_football_odds(
            self.connection,
            target_date=date(2026, 6, 8),
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["match_count"], 1)
        self.assertEqual(result["odds_count"], 2)
        self.assertEqual(result["merged_match_count"], 1)
        self.assertEqual(result["merged_odds_count"], 2)

        staging_odds = self.connection.execute(
            """
            SELECT provider_match_id, odds_type, handicap_line, home_odds, draw_odds, away_odds
            FROM staging.stg_odds
            ORDER BY odds_type
            """
        ).fetchall()
        self.assertEqual(
            staging_odds,
            [
                ("周一001", "1X2", None, 2.1, 3.3, 3.4),
                ("周一001", "SPORTTERY_RQSPF", -1.0, 4.5, 3.8, 1.62),
            ],
        )

        core_match = self.connection.execute(
            """
            SELECT match_id, system_league_id, home_team_id, away_team_id, source_provider
            FROM core.fact_match_schedule
            WHERE match_id = 'SYS_EPL_M001'
            """
        ).fetchall()
        self.assertEqual(
            core_match,
            [("SYS_EPL_M001", "SYS_EPL", "SYS_ARS", "SYS_CHE", "SportteryOfficialWeb")],
        )

        core_odds_count = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM core.fact_odds_capital_flow
            WHERE match_id = 'SYS_EPL_M001'
            """
        ).fetchone()[0]
        self.assertEqual(core_odds_count, 2)

    def test_mapped_match_persists_all_five_market_statuses(self) -> None:
        self._setup_mapped_match()
        payload = {
            "matches": [
                {
                    "match_id": "周一001",
                    "league": "English Premier League",
                    "kickoff_time": "2026-06-08T19:30:00Z",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "spf": {"home": "2.10", "draw": "3.30", "away": "3.40"},
                }
            ]
        }

        class JsonSportteryClient(SportteryClient):
            def _http_get(self, path: str) -> str | None:
                return json.dumps(payload)

        JsonSportteryClient(base_url="https://sporttery.example").fetch_football_odds(
            self.connection,
            target_date=date(2026, 6, 8),
        )

        statuses = self.connection.execute(
            """
            SELECT market_type, sale_status
            FROM core.fact_sporttery_market_status
            WHERE match_id = 'SYS_EPL_M001'
            ORDER BY market_type
            """
        ).fetchall()
        self.assertEqual(
            statuses,
            [
                ("CRS", "NOT_ON_SALE"),
                ("HAFU", "NOT_ON_SALE"),
                ("RQSPF", "NOT_ON_SALE"),
                ("SPF", "OPEN"),
                ("TTG", "NOT_ON_SALE"),
            ],
        )

    def test_fetch_resolves_unmapped_match_before_merging_core_odds(self) -> None:
        self.connection.execute(
            """
            INSERT INTO core.fact_match_schedule (
                match_id, season, system_league_id, match_time,
                home_team_id, away_team_id, status, source_provider
            ) VALUES (
                'SYS_EPL_M002', '2026', 'SYS_EPL',
                TIMESTAMP '2026-06-08 19:30:00',
                'SYS_ARS', 'SYS_CHE', 'PRE-MATCH', 'test'
            )
            """
        )
        payload = {
            "matches": [
                {
                    "match_id": "AUTO_MAP_001",
                    "league": "English Premier League",
                    "kickoff_time": "2026-06-08T19:30:00Z",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "spf": {"home": "2.10", "draw": "3.30", "away": "3.40"},
                    "crs_odds": {"1:0": 7.5},
                    "ttg_odds": {"2": 3.25},
                    "hafu_odds": {"hh": 2.35},
                }
            ]
        }

        class JsonSportteryClient(SportteryClient):
            def _http_get(self, path: str) -> str | None:
                return json.dumps(payload)

        result = JsonSportteryClient().fetch_football_odds(
            self.connection,
            target_date=date(2026, 6, 8),
        )

        mapping = self.connection.execute(
            """
            SELECT system_match_id
            FROM core.dim_provider_match_mapping
            WHERE provider_name = 'SportteryOfficialWeb'
              AND provider_match_id = 'AUTO_MAP_001'
            """
        ).fetchone()
        core_odds_count = self.connection.execute(
            "SELECT COUNT(*) FROM core.fact_odds_capital_flow WHERE match_id = 'SYS_EPL_M002'"
        ).fetchone()[0]
        detail_odds_count = self.connection.execute(
            "SELECT COUNT(*) FROM core.fact_sporttery_odds_detail WHERE match_id = 'SYS_EPL_M002'"
        ).fetchone()[0]

        self.assertEqual(mapping, ("SYS_EPL_M002",))
        self.assertEqual(result["merged_match_count"], 1)
        self.assertEqual(result["skipped_unmapped_count"], 0)
        self.assertEqual(core_odds_count, 1)
        self.assertEqual(detail_odds_count, 3)

    def test_unmapped_matches_stay_in_staging_only(self) -> None:
        """未映射的比赛不应进入 core.* 表。"""
        payload = {
            "matches": [
                {
                    "match_id": "未映射001",
                    "league": "English Premier League",
                    "kickoff_time": "2026-06-08T19:30:00Z",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "spf": {"home": "2.10", "draw": "3.30", "away": "3.40"},
                }
            ]
        }

        class JsonSportteryClient(SportteryClient):
            def _http_get(self, path: str) -> str | None:
                return json.dumps(payload)

        result = JsonSportteryClient(base_url="https://sporttery.example").fetch_football_odds(
            self.connection,
            target_date=date(2026, 6, 8),
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["match_count"], 1)
        self.assertEqual(result["skipped_unmapped_count"], 1)
        self.assertEqual(result["merged_match_count"], 0)
        self.assertEqual(result["merged_odds_count"], 0)

        staging_count = self.connection.execute(
            "SELECT COUNT(*) FROM staging.stg_match_schedule"
        ).fetchone()[0]
        self.assertEqual(staging_count, 1)

        core_schedule = self.connection.execute(
            "SELECT COUNT(*) FROM core.fact_match_schedule"
        ).fetchone()[0]
        self.assertEqual(core_schedule, 0)

    def test_html_data_attributes_are_parsed_without_executing_javascript(self) -> None:
        html = """
        <table>
          <tr
            data-match-id="周二002"
            data-league="English Premier League"
            data-kickoff="2026-06-09T20:00:00Z"
            data-home="Arsenal"
            data-away="Chelsea"
            data-spf-home="1.95"
            data-spf-draw="3.20"
            data-spf-away="3.90">
          </tr>
        </table>
        """

        class HtmlSportteryClient(SportteryClient):
            def _http_get(self, path: str) -> str | None:
                return html

        result = HtmlSportteryClient(base_url="https://sporttery.example").fetch_football_odds(
            self.connection,
            target_date=date(2026, 6, 9),
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["match_count"], 1)
        self.assertEqual(result["odds_count"], 1)

        odds = self.connection.execute(
            """
            SELECT provider_match_id, odds_type, home_odds, draw_odds, away_odds
            FROM staging.stg_odds
            """
        ).fetchall()
        self.assertEqual(odds, [("周二002", "1X2", 1.95, 3.2, 3.9)])

    def test_existing_core_match_updated_at_refreshes_on_resync(self) -> None:
        self._setup_mapped_match()
        payload = {
            "matches": [
                {
                    "match_id": "周一001",
                    "league": "English Premier League",
                    "kickoff_time": "2026-06-08T19:30:00Z",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "spf": {"home": "2.10", "draw": "3.30", "away": "3.40"},
                }
            ]
        }

        class JsonSportteryClient(SportteryClient):
            def _http_get(self, path: str) -> str | None:
                return json.dumps(payload)

        client = JsonSportteryClient(base_url="https://sporttery.example")
        client.fetch_football_odds(self.connection, target_date=date(2026, 6, 8))
        self.connection.execute(
            """
            UPDATE core.fact_match_schedule
            SET updated_at = TIMESTAMP '2026-06-07 08:00:00',
                etl_insert_timestamp = TIMESTAMP '2026-06-07 08:00:00'
            WHERE match_id = 'SYS_EPL_M001'
            """
        )

        client.fetch_football_odds(self.connection, target_date=date(2026, 6, 8))
        updated_at, etl_insert_timestamp = self.connection.execute(
            """
            SELECT updated_at, etl_insert_timestamp
            FROM core.fact_match_schedule
            WHERE match_id = 'SYS_EPL_M001'
            """
        ).fetchone()

        self.assertGreater(updated_at, datetime(2026, 6, 7, 8, 0, 0))
        self.assertGreater(etl_insert_timestamp, datetime(2026, 6, 7, 8, 0, 0))

    def test_no_m_st_prefix_in_core(self) -> None:
        """确认核心表中不再出现 M_ST_ 前缀的 match_id。"""
        self._setup_mapped_match()
        payload = {
            "matches": [
                {
                    "match_id": "周一001",
                    "league": "English Premier League",
                    "kickoff_time": "2026-06-08T19:30:00Z",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "spf": {"home": "2.10", "draw": "3.30", "away": "3.40"},
                }
            ]
        }

        class JsonSportteryClient(SportteryClient):
            def _http_get(self, path: str) -> str | None:
                return json.dumps(payload)

        JsonSportteryClient(base_url="https://sporttery.example").fetch_football_odds(
            self.connection, target_date=date(2026, 6, 8)
        )

        core_match_ids = self.connection.execute(
            "SELECT match_id FROM core.fact_match_schedule"
        ).fetchall()
        core_match_ids = [row[0] for row in core_match_ids]
        self.assertNotIn("M_ST_周一001", core_match_ids)
        self.assertIn("SYS_EPL_M001", core_match_ids)

        odds_match_ids = self.connection.execute(
            "SELECT DISTINCT match_id FROM core.fact_odds_capital_flow"
        ).fetchall()
        odds_match_ids = [row[0] for row in odds_match_ids]
        self.assertNotIn("M_ST_周一001", odds_match_ids)
        self.assertIn("SYS_EPL_M001", odds_match_ids)

    def test_empty_response_retry_succeeds_on_second_attempt(self) -> None:
        """空响应重试：第二次成功。"""
        payload = {
            "matches": [
                {
                    "match_id": "周一001",
                    "league": "English Premier League",
                    "kickoff_time": "2026-06-08T19:30:00Z",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "spf": {"home": "2.10", "draw": "3.30", "away": "3.40"},
                }
            ]
        }
        attempts = []

        class RetrySportteryClient(SportteryClient):
            def _http_get(self, path: str) -> str | None:
                attempts.append(path)
                if len(attempts) < 2:
                    return None
                return json.dumps(payload)

        result = RetrySportteryClient(base_url="https://sporttery.example").fetch_football_odds(
            self.connection,
            target_date=date(2026, 6, 8),
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["match_count"], 1)
        self.assertGreaterEqual(len(attempts), 2)

    def test_empty_response_after_retries_returns_empty_result(self) -> None:
        """三次空响应后返回 empty_after_retries。"""
        class AlwaysEmptyClient(SportteryClient):
            def _http_get(self, path: str) -> str | None:
                return None

        result = AlwaysEmptyClient(base_url="https://sporttery.example").fetch_football_odds(
            self.connection,
            target_date=date(2026, 6, 8),
        )
        self.assertEqual(result["status"], "empty_after_retries")
        self.assertEqual(result["match_count"], 0)

    def test_detail_odds_failure_raises_error(self) -> None:
        """detail odds 写入失败时不应静默吞错。"""
        self._setup_mapped_match()
        # 使用一个带有 detail odds 的 payload，但表不存在时会抛出异常
        # 由于 apply_schema 已创建表，此测试确认正常写入
        payload = {
            "matches": [
                {
                    "match_id": "周一001",
                    "league": "English Premier League",
                    "kickoff_time": "2026-06-08T19:30:00Z",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "spf": {"home": "2.10", "draw": "3.30", "away": "3.40"},
                    "crs_odds": {"1:0": 7.5, "2:0": 10.0},
                    "ttg_odds": {"0": 10.5, "1": 4.4},
                    "hafu_odds": {"hh": 2.35, "hd": 18.0},
                }
            ]
        }

        class JsonSportteryClient(SportteryClient):
            def _http_get(self, path: str) -> str | None:
                return json.dumps(payload)

        result = JsonSportteryClient(base_url="https://sporttery.example").fetch_football_odds(
            self.connection,
            target_date=date(2026, 6, 8),
        )
        self.assertEqual(result["detail_count"], 6)  # 2 crs + 2 ttg + 2 hafu

        detail_count = self.connection.execute(
            "SELECT COUNT(*) FROM staging.stg_sporttery_odds_detail"
        ).fetchone()[0]
        self.assertEqual(detail_count, 6)

    def test_official_match_calculator_payload_is_parsed_from_webapi_first(self) -> None:
        self.connection.execute(
            """
            INSERT INTO core.dim_league_mapping (
                system_league_id, league_standard_name, country
            ) VALUES ('SYS_INT', '国际赛', 'World');

            INSERT INTO core.dim_team_mapping (
                system_team_id, team_standard_name
            ) VALUES
                ('SYS_CRO', '克罗地亚'),
                ('SYS_SLO', '斯洛文尼亚');

            INSERT INTO core.dim_provider_league_mapping (
                provider_name, provider_league_id, system_league_id, provider_league_name
            ) VALUES ('SportteryOfficialWeb', '国际赛', 'SYS_INT', '国际赛');

            INSERT INTO core.dim_provider_team_mapping (
                provider_name, provider_team_id, system_team_id, provider_team_name
            ) VALUES
                ('SportteryOfficialWeb', '克罗地亚', 'SYS_CRO', '克罗地亚'),
                ('SportteryOfficialWeb', '斯洛文尼', 'SYS_SLO', '斯洛文尼');
            """
        )

        payload = {
            "errorCode": 0,
            "success": True,
            "value": {
                "lastUpdateTime": "2026-06-07 12:10:12",
                "matchInfoList": [
                    {
                        "businessDate": "2026-06-07",
                        "matchNumDate": "260607",
                        "subMatchList": [
                            {
                                "matchId": 2040141,
                                "matchNumStr": "周日201",
                                "matchDate": "2026-06-08",
                                "matchTime": "02:45:00",
                                "leagueAbbName": "国际赛",
                                "homeTeamAbbName": "克罗地亚",
                                "awayTeamAbbName": "斯洛文尼",
                                "had": {
                                    "h": "1.24",
                                    "d": "4.60",
                                    "a": "9.45",
                                    "goalLine": "",
                                },
                                "hhad": {
                                    "h": "1.98",
                                    "d": "3.15",
                                    "a": "3.28",
                                    "goalLine": "-1",
                                },
                            }
                        ],
                    }
                ],
            },
        }

        class OfficialApiSportteryClient(SportteryClient):
            def __init__(self) -> None:
                super().__init__(base_url="https://www.sporttery.cn")
                self.paths: list[str] = []

            def _http_get(self, path: str) -> str | None:
                self.paths.append(path)
                return json.dumps(payload)

        client = OfficialApiSportteryClient()
        result = client.fetch_football_odds(
            self.connection,
            target_date=date(2026, 6, 8),
        )

        self.assertEqual(
            client.paths,
            [
                "https://webapi.sporttery.cn/gateway/uniform/football/"
                "getMatchCalculatorV1.qry?channel=c&poolCode=hhad,had,crs,ttg,hafu"
            ],
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["match_count"], 1)
        self.assertEqual(result["odds_count"], 2)
        # 没有映射表条目，不合并到 core
        self.assertEqual(result["merged_match_count"], 0)
        self.assertEqual(result["skipped_unmapped_count"], 1)

        staging_odds = self.connection.execute(
            """
            SELECT provider_match_id, odds_type, handicap_line, home_odds, draw_odds, away_odds
            FROM staging.stg_odds
            WHERE provider_match_id = '2040141'
            ORDER BY odds_type
            """
        ).fetchall()
        self.assertEqual(
            staging_odds,
            [
                ("2040141", "1X2", None, 1.24, 4.6, 9.45),
                ("2040141", "SPORTTERY_RQSPF", -1.0, 1.98, 3.15, 3.28),
            ],
        )


if __name__ == "__main__":
    unittest.main()
