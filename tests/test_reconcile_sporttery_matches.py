from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import duckdb

from football_advisor.db_schema import apply_schema
from football_advisor.provider_mapping_importer import import_provider_mappings
from football_advisor.reconcile_sporttery_matches import reconcile


class ReconcileSportteryMatchesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_reconcile.duckdb"
        self.connection = duckdb.connect(str(self.db_path))
        apply_schema(self.connection)

        # 插入标准赛程和映射
        self.connection.execute("""
            INSERT INTO core.dim_league_mapping (
                system_league_id, league_standard_name, country
            ) VALUES ('WC_WORLD_CUP_2026', 'FIFA World Cup 2026', 'World');

            INSERT INTO core.dim_team_mapping (
                system_team_id, team_standard_name
            ) VALUES
                ('WC_TEAM_CAN', 'Canada'),
                ('WC_TEAM_BIH', 'Bosnia-Herzegovina');

            INSERT INTO core.dim_provider_league_mapping (
                provider_name, provider_league_id, system_league_id, provider_league_name
            ) VALUES ('SportteryOfficialWeb', '世界杯', 'WC_WORLD_CUP_2026', '世界杯');

            INSERT INTO core.dim_provider_team_mapping (
                provider_name, provider_team_id, system_team_id, provider_team_name
            ) VALUES
                ('SportteryOfficialWeb', '加拿大', 'WC_TEAM_CAN', '加拿大'),
                ('SportteryOfficialWeb', '波黑', 'WC_TEAM_BIH', '波黑');

            INSERT INTO core.fact_match_schedule (
                match_id, season, system_league_id, match_time,
                home_team_id, away_team_id, status, source_provider
            ) VALUES (
                'WC2026_M003', '2026', 'WC_WORLD_CUP_2026',
                TIMESTAMP '2026-06-13 03:00:00',
                'WC_TEAM_CAN', 'WC_TEAM_BIH', 'PRE-MATCH', 'worldcup_basic_importer'
            );
        """)

    def tearDown(self) -> None:
        self.connection.close()
        self.temp_dir.cleanup()

    def test_reconcile_migrates_old_m_st_records(self) -> None:
        # 旧的 M_ST_ 记录
        self.connection.execute("""
            INSERT INTO core.fact_match_schedule (
                match_id, season, system_league_id, match_time,
                home_team_id, away_team_id, status, source_provider
            ) VALUES (
                'M_ST_2040164', '2026', 'WC_WORLD_CUP_2026',
                TIMESTAMP '2026-06-13 03:00:00',
                'WC_TEAM_CAN', 'WC_TEAM_BIH', 'PRE-MATCH', 'SportteryOfficialWeb'
            );
        """)
        self.connection.execute("""
            INSERT INTO core.fact_odds_capital_flow (
                match_id, snapshot_time, odds_type, bookmaker_name,
                home_odds, draw_odds, away_odds, source_provider
            ) VALUES (
                'M_ST_2040164', TIMESTAMP '2026-06-12 12:00:00', '1X2', '竞彩',
                2.0, 3.0, 4.0, 'SportteryOfficialWeb'
            );
        """)

        # staging 数据
        self.connection.execute("""
            INSERT INTO staging.stg_match_schedule (
                provider_match_id, provider_league_id, provider_home_team_id,
                provider_away_team_id, match_time, source_provider
            ) VALUES (
                '2040164', '世界杯', '加拿大', '波黑',
                TIMESTAMP '2026-06-13 03:00:00', 'SportteryOfficialWeb'
            );
        """)
        self.connection.execute("""
            INSERT INTO staging.stg_odds (
                provider_match_id, odds_type, bookmaker_name,
                home_odds, draw_odds, away_odds, snapshot_time, source_provider
            ) VALUES (
                '2040164', '1X2', '竞彩', 2.0, 3.0, 4.0,
                TIMESTAMP '2026-06-12 12:00:00', 'SportteryOfficialWeb'
            );
        """)

        # 执行迁移
        stats = reconcile(self.connection, dry_run=False)

        self.assertGreaterEqual(stats["removed"], 1)
        self.assertGreaterEqual(stats["resolved"], 1)

        # 验证 M_ST_ 记录已删除
        m_st_count = self.connection.execute(
            "SELECT COUNT(*) FROM core.fact_match_schedule WHERE match_id = 'M_ST_2040164'"
        ).fetchone()[0]
        self.assertEqual(m_st_count, 0)

        m_st_odds = self.connection.execute(
            "SELECT COUNT(*) FROM core.fact_odds_capital_flow WHERE match_id = 'M_ST_2040164'"
        ).fetchone()[0]
        self.assertEqual(m_st_odds, 0)

        # 验证正确记录已创建
        core_match = self.connection.execute(
            "SELECT match_id FROM core.fact_match_schedule WHERE match_id = 'WC2026_M003'"
        ).fetchone()
        self.assertIsNotNone(core_match)

        core_odds = self.connection.execute(
            "SELECT COUNT(*) FROM core.fact_odds_capital_flow WHERE match_id = 'WC2026_M003'"
        ).fetchone()[0]
        self.assertGreaterEqual(core_odds, 1)

        # 验证映射表
        mapping = self.connection.execute(
            "SELECT system_match_id FROM core.dim_provider_match_mapping WHERE provider_match_id = '2040164'"
        ).fetchone()
        self.assertEqual(mapping[0], "WC2026_M003")

    def test_dry_run_does_not_modify(self) -> None:
        self.connection.execute("""
            INSERT INTO core.fact_match_schedule (
                match_id, season, system_league_id, match_time,
                home_team_id, away_team_id, status, source_provider
            ) VALUES (
                'M_ST_2040164', '2026', 'WC_WORLD_CUP_2026',
                TIMESTAMP '2026-06-13 03:00:00',
                'WC_TEAM_CAN', 'WC_TEAM_BIH', 'PRE-MATCH', 'SportteryOfficialWeb'
            );
        """)
        self.connection.execute("""
            INSERT INTO staging.stg_match_schedule (
                provider_match_id, provider_league_id, provider_home_team_id,
                provider_away_team_id, match_time, source_provider
            ) VALUES (
                '2040164', '世界杯', '加拿大', '波黑',
                TIMESTAMP '2026-06-13 03:00:00', 'SportteryOfficialWeb'
            );
        """)

        stats = reconcile(self.connection, dry_run=True)

        # dry-run 不应删除
        m_st_count = self.connection.execute(
            "SELECT COUNT(*) FROM core.fact_match_schedule WHERE match_id = 'M_ST_2040164'"
        ).fetchone()[0]
        self.assertEqual(m_st_count, 1)

        mapping_count = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM core.dim_provider_match_mapping
            WHERE provider_name = 'SportteryOfficialWeb'
              AND provider_match_id = '2040164'
            """
        ).fetchone()[0]
        self.assertEqual(mapping_count, 0)

        self.assertGreaterEqual(stats["resolved"], 1)

    def test_official_world_cup_ids_resolve_four_target_matches(self) -> None:
        self.connection.execute(
            """
            INSERT INTO core.dim_team_mapping (
                system_team_id, team_standard_name
            ) VALUES
                ('WC_TEAM_QAT', 'Qatar'),
                ('WC_TEAM_SUI', 'Switzerland'),
                ('WC_TEAM_BRA', 'Brazil'),
                ('WC_TEAM_MAR', 'Morocco'),
                ('WC_TEAM_HAI', 'Haiti'),
                ('WC_TEAM_SCO', 'Scotland'),
                ('WC_TEAM_AUS', 'Australia'),
                ('WC_TEAM_TUR', 'Turkiye');

            INSERT INTO core.fact_match_schedule (
                match_id, season, system_league_id, match_time,
                home_team_id, away_team_id, status, source_provider
            ) VALUES
                ('WC2026_M008', '2026', 'WC_WORLD_CUP_2026',
                 TIMESTAMP '2026-06-13 19:00:00', 'WC_TEAM_QAT', 'WC_TEAM_SUI',
                 'PRE-MATCH', 'worldcup_basic_importer'),
                ('WC2026_M007', '2026', 'WC_WORLD_CUP_2026',
                 TIMESTAMP '2026-06-13 22:00:00', 'WC_TEAM_BRA', 'WC_TEAM_MAR',
                 'PRE-MATCH', 'worldcup_basic_importer'),
                ('WC2026_M005', '2026', 'WC_WORLD_CUP_2026',
                 TIMESTAMP '2026-06-14 01:00:00', 'WC_TEAM_HAI', 'WC_TEAM_SCO',
                 'PRE-MATCH', 'worldcup_basic_importer'),
                ('WC2026_M006', '2026', 'WC_WORLD_CUP_2026',
                 TIMESTAMP '2026-06-14 04:00:00', 'WC_TEAM_AUS', 'WC_TEAM_TUR',
                 'PRE-MATCH', 'worldcup_basic_importer');

            INSERT INTO staging.stg_match_schedule (
                provider_match_id, provider_league_id,
                provider_home_team_id, provider_away_team_id,
                match_time, source_provider
            ) VALUES
                ('2040166', '世界杯', '428', '403',
                 TIMESTAMP '2026-06-14 03:00:00', 'SportteryOfficialWeb'),
                ('2040167', '世界杯', '417', '424',
                 TIMESTAMP '2026-06-14 06:00:00', 'SportteryOfficialWeb'),
                ('2040168', '世界杯', '915', '398',
                 TIMESTAMP '2026-06-14 09:00:00', 'SportteryOfficialWeb'),
                ('2040169', '世界杯', '421', '404',
                 TIMESTAMP '2026-06-14 12:00:00', 'SportteryOfficialWeb');
            """
        )
        mapping_dir = Path(__file__).resolve().parents[1] / "data" / "provider_mappings"
        import_provider_mappings(self.connection, mapping_dir)

        stats = reconcile(self.connection, dry_run=False)

        self.assertEqual(stats["errors"], 0)
        mappings = self.connection.execute(
            """
            SELECT provider_match_id, system_match_id
            FROM core.dim_provider_match_mapping
            WHERE provider_name = 'SportteryOfficialWeb'
              AND provider_match_id IN ('2040166', '2040167', '2040168', '2040169')
            ORDER BY provider_match_id
            """
        ).fetchall()
        self.assertEqual(
            mappings,
            [
                ("2040166", "WC2026_M008"),
                ("2040167", "WC2026_M007"),
                ("2040168", "WC2026_M005"),
                ("2040169", "WC2026_M006"),
            ],
        )

        schedule_teams = self.connection.execute(
            """
            SELECT match_id, home_team_id, away_team_id
            FROM core.fact_match_schedule
            WHERE match_id IN ('WC2026_M005', 'WC2026_M006', 'WC2026_M007', 'WC2026_M008')
            ORDER BY match_id
            """
        ).fetchall()
        self.assertEqual(
            schedule_teams,
            [
                ("WC2026_M005", "WC_TEAM_HAI", "WC_TEAM_SCO"),
                ("WC2026_M006", "WC_TEAM_AUS", "WC_TEAM_TUR"),
                ("WC2026_M007", "WC_TEAM_BRA", "WC_TEAM_MAR"),
                ("WC2026_M008", "WC_TEAM_QAT", "WC_TEAM_SUI"),
            ],
        )

    def test_reconcile_quarantines_unmapped_records(self) -> None:
        """映射缺失时，未映射记录进入隔离清单，不能静默丢弃。"""
        self.connection.execute("""
            INSERT INTO staging.stg_match_schedule (
                provider_match_id, provider_league_id, provider_home_team_id,
                provider_away_team_id, match_time, source_provider
            ) VALUES (
                '9999999', '世界杯', '未知主队', '波黑',
                TIMESTAMP '2026-06-13 03:00:00', 'SportteryOfficialWeb'
            );
        """)

        stats = reconcile(self.connection, dry_run=False)

        self.assertGreaterEqual(stats["errors"], 1)
        # 隔离清单中应有记录
        quarantined = self.connection.execute(
            """
            SELECT provider_match_id, provider_home_team_id, error_reason
            FROM core.quarantine_unmapped_records
            WHERE provider_match_id = '9999999'
            """
        ).fetchall()
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0][1], "未知主队")

    def test_re_resolve_after_mapping_fix(self) -> None:
        """映射补齐后，隔离记录可重新解析并合并到 core。"""
        # 1. 先插入一条无法解析的 staging 记录
        self.connection.execute("""
            INSERT INTO staging.stg_match_schedule (
                provider_match_id, provider_league_id, provider_home_team_id,
                provider_away_team_id, match_time, source_provider
            ) VALUES (
                '8888888', '世界杯', '未知B队', '波黑',
                TIMESTAMP '2026-06-13 03:00:00', 'SportteryOfficialWeb'
            );
        """)

        # 2. 执行 reconcile，因映射缺失而失败，进入隔离
        stats = reconcile(self.connection, dry_run=False)
        self.assertGreaterEqual(stats["errors"], 1)

        # 3. 模拟补齐映射：添加 '未知B队' → 'WC_TEAM_CAN' 映射
        self.connection.execute("""
            INSERT INTO core.dim_provider_team_mapping (
                provider_name, provider_team_id, system_team_id, provider_team_name
            ) VALUES ('SportteryOfficialWeb', '未知B队', 'WC_TEAM_CAN', '未知B队');
        """)

        # 4. 重新执行 reconcile，应能解析
        stats2 = reconcile(self.connection, dry_run=False)
        self.assertEqual(stats2["errors"], 0)
        self.assertGreaterEqual(stats2["resolved"], 1)

        # 5. 验证映射表已更新
        mapping = self.connection.execute(
            "SELECT system_match_id FROM core.dim_provider_match_mapping WHERE provider_match_id = '8888888'"
        ).fetchone()
        self.assertEqual(mapping[0], "WC2026_M003")


if __name__ == "__main__":
    unittest.main()
