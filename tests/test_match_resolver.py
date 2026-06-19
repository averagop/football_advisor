from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import duckdb

from football_advisor.db_schema import apply_schema
from football_advisor.match_resolver import CanonicalMatchResolver, MatchResolutionError


class MatchResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_resolver.duckdb"
        self.connection = duckdb.connect(str(self.db_path))
        apply_schema(self.connection)

        self.connection.execute("""
            INSERT INTO core.dim_league_mapping (
                system_league_id, league_standard_name, country
            ) VALUES ('WC_WORLD_CUP_2026', 'FIFA World Cup 2026', 'World');
        """)
        self.connection.execute("""
            INSERT INTO core.dim_team_mapping (
                system_team_id, team_standard_name
            ) VALUES
                ('WC_TEAM_CAN', 'Canada'),
                ('WC_TEAM_BIH', 'Bosnia-Herzegovina'),
                ('WC_TEAM_USA', 'USA'),
                ('WC_TEAM_PAR', 'Paraguay');
        """)
        self.connection.execute("""
            INSERT INTO core.dim_provider_league_mapping (
                provider_name, provider_league_id, system_league_id, provider_league_name
            ) VALUES ('SportteryOfficialWeb', '世界杯', 'WC_WORLD_CUP_2026', '世界杯');
        """)
        self.connection.execute("""
            INSERT INTO core.dim_provider_team_mapping (
                provider_name, provider_team_id, system_team_id, provider_team_name
            ) VALUES
                ('SportteryOfficialWeb', '加拿大', 'WC_TEAM_CAN', '加拿大'),
                ('SportteryOfficialWeb', '波黑', 'WC_TEAM_BIH', '波黑'),
                ('SportteryOfficialWeb', '美国', 'WC_TEAM_USA', '美国'),
                ('SportteryOfficialWeb', '巴拉圭', 'WC_TEAM_PAR', '巴拉圭');
        """)
        self.connection.execute("""
            INSERT INTO core.fact_match_schedule (
                match_id, season, system_league_id, match_time,
                home_team_id, away_team_id, status, source_provider
            ) VALUES
                ('WC2026_M003', '2026', 'WC_WORLD_CUP_2026',
                 TIMESTAMP '2026-06-13 03:00:00',
                 'WC_TEAM_CAN', 'WC_TEAM_BIH', 'PRE-MATCH', 'worldcup_basic_importer'),
                ('WC2026_M004', '2026', 'WC_WORLD_CUP_2026',
                 TIMESTAMP '2026-06-14 03:00:00',
                 'WC_TEAM_USA', 'WC_TEAM_PAR', 'PRE-MATCH', 'worldcup_basic_importer');
        """)

        self.resolver = CanonicalMatchResolver(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.temp_dir.cleanup()

    def test_resolves_sporttery_match_to_existing_world_cup_match(self) -> None:
        resolved = self.resolver.resolve(
            provider_name="SportteryOfficialWeb",
            provider_match_id="2040164",
            provider_league_id="世界杯",
            provider_home_team_id="加拿大",
            provider_away_team_id="波黑",
            kickoff_time=datetime(2026, 6, 13, 3, 0),
        )
        self.assertEqual(resolved.system_match_id, "WC2026_M003")
        self.assertEqual(resolved.resolution_method, "mapped_teams_and_time")

    def test_rejects_unmapped_provider_team_instead_of_using_raw_name(self) -> None:
        with self.assertRaises(MatchResolutionError):
            self.resolver.resolve(
                provider_name="SportteryOfficialWeb",
                provider_match_id="unknown",
                provider_league_id="世界杯",
                provider_home_team_id="未映射球队",
                provider_away_team_id="波黑",
                kickoff_time=datetime(2026, 6, 13, 3, 0),
            )

    def test_mapping_is_persisted_and_reused(self) -> None:
        first = self.resolver.resolve(
            provider_name="SportteryOfficialWeb",
            provider_match_id="2040164",
            provider_league_id="世界杯",
            provider_home_team_id="加拿大",
            provider_away_team_id="波黑",
            kickoff_time=datetime(2026, 6, 13, 3, 0),
        )
        self.assertEqual(first.system_match_id, "WC2026_M003")

        second = self.resolver.resolve(
            provider_name="SportteryOfficialWeb",
            provider_match_id="2040164",
            provider_league_id="世界杯",
            provider_home_team_id="加拿大",
            provider_away_team_id="波黑",
            kickoff_time=datetime(2026, 6, 13, 3, 0),
        )
        self.assertEqual(second.system_match_id, "WC2026_M003")
        self.assertEqual(second.resolution_method, "existing_mapping")

    def test_zero_match_raises_error(self) -> None:
        with self.assertRaises(MatchResolutionError):
            self.resolver.resolve(
                provider_name="SportteryOfficialWeb",
                provider_match_id="9999999",
                provider_league_id="世界杯",
                provider_home_team_id="加拿大",
                provider_away_team_id="波黑",
                kickoff_time=datetime(2026, 6, 14, 12, 0),
            )

    def test_multi_match_raises_error(self) -> None:
        self.connection.execute("""
            INSERT INTO core.fact_match_schedule (
                match_id, season, system_league_id, match_time,
                home_team_id, away_team_id, status, source_provider
            ) VALUES
                ('WC2026_M003_DUP', '2026', 'WC_WORLD_CUP_2026',
                 TIMESTAMP '2026-06-13 03:00:00',
                 'WC_TEAM_CAN', 'WC_TEAM_BIH', 'PRE-MATCH', 'test');
        """)
        with self.assertRaises(MatchResolutionError):
            self.resolver.resolve(
                provider_name="SportteryOfficialWeb",
                provider_match_id="2040165",
                provider_league_id="世界杯",
                provider_home_team_id="加拿大",
                provider_away_team_id="波黑",
                kickoff_time=datetime(2026, 6, 13, 3, 0),
            )

    def test_usa_paraguay_resolves_to_wc2026_m004(self) -> None:
        resolved = self.resolver.resolve(
            provider_name="SportteryOfficialWeb",
            provider_match_id="2040165",
            provider_league_id="世界杯",
            provider_home_team_id="美国",
            provider_away_team_id="巴拉圭",
            kickoff_time=datetime(2026, 6, 14, 3, 0),
        )
        self.assertEqual(resolved.system_match_id, "WC2026_M004")

    def test_rejects_home_and_away_resolving_to_same_system_team(self) -> None:
        """主客队不能解析为同一标准球队。"""
        # 添加一条映射：另一个 provider_team_id 也指向同一系统球队
        self.connection.execute("""
            INSERT INTO core.dim_provider_team_mapping (
                provider_name, provider_team_id, system_team_id, provider_team_name
            ) VALUES ('SportteryOfficialWeb', '加拿大别名', 'WC_TEAM_CAN', '加拿大别名');
        """)
        with self.assertRaises(MatchResolutionError) as ctx:
            self.resolver.resolve(
                provider_name="SportteryOfficialWeb",
                provider_match_id="2040999",
                provider_league_id="世界杯",
                provider_home_team_id="加拿大",
                provider_away_team_id="加拿大别名",
                kickoff_time=datetime(2026, 6, 13, 3, 0),
            )
        self.assertIn("同一标准球队", str(ctx.exception))

    def test_quarantines_unmapped_team_record(self) -> None:
        """未映射球队记录进入隔离清单，不能静默丢弃。"""
        with self.assertRaises(MatchResolutionError):
            self.resolver.resolve(
                provider_name="SportteryOfficialWeb",
                provider_match_id="2040999",
                provider_league_id="世界杯",
                provider_home_team_id="未知球队",
                provider_away_team_id="波黑",
                kickoff_time=datetime(2026, 6, 13, 3, 0),
            )
        # 隔离清单中应有记录
        quarantined = self.connection.execute(
            """
            SELECT provider_name, provider_match_id, provider_home_team_id,
                   provider_away_team_id, error_reason, component
            FROM core.quarantine_unmapped_records
            WHERE provider_match_id = '2040999'
            """
        ).fetchall()
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0][2], "未知球队")
        self.assertIn("主队", quarantined[0][4])

    def test_quarantines_unmapped_league_record(self) -> None:
        """未映射联赛记录进入隔离清单。"""
        with self.assertRaises(MatchResolutionError):
            self.resolver.resolve(
                provider_name="SportteryOfficialWeb",
                provider_match_id="2040999",
                provider_league_id="未知联赛",
                provider_home_team_id="加拿大",
                provider_away_team_id="波黑",
                kickoff_time=datetime(2026, 6, 13, 3, 0),
            )
        quarantined = self.connection.execute(
            """
            SELECT provider_name, provider_league_id, error_reason, component
            FROM core.quarantine_unmapped_records
            WHERE provider_match_id = '2040999'
            """
        ).fetchall()
        self.assertEqual(len(quarantined), 1)
        self.assertIn("联赛", quarantined[0][2])

    def test_provider_id_not_used_as_system_id_directly(self) -> None:
        """provider ID 不能直接当作标准实体 ID 使用，必须通过映射表。"""
        # 场景：provider_team_id 恰好与某个 system_team_id 同名
        self.connection.execute("""
            INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name)
            VALUES ('加拿大', 'AmbiguousTeam');
        """)
        # 删除映射表中 '加拿大' → 'WC_TEAM_CAN' 的映射
        self.connection.execute("""
            DELETE FROM core.dim_provider_team_mapping
            WHERE provider_team_id = '加拿大';
        """)
        # 即使 dim_team_mapping 中存在 system_team_id='加拿大'，
        # resolver 也不能直接使用 provider_team_id 作为 system_team_id
        with self.assertRaises(MatchResolutionError):
            self.resolver.resolve(
                provider_name="SportteryOfficialWeb",
                provider_match_id="2040999",
                provider_league_id="世界杯",
                provider_home_team_id="加拿大",
                provider_away_team_id="波黑",
                kickoff_time=datetime(2026, 6, 13, 3, 0),
            )
        # 隔离清单中应有记录，不能静默丢弃
        quarantined = self.connection.execute(
            "SELECT COUNT(*) FROM core.quarantine_unmapped_records WHERE provider_match_id = '2040999'"
        ).fetchone()[0]
        self.assertGreaterEqual(quarantined, 1)


if __name__ == "__main__":
    unittest.main()