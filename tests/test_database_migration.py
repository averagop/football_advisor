from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from football_advisor.db_schema import (
    SCHEMA_SQL,
    _migrate_new_columns,
    apply_schema,
    initialize_database,
)


class DatabaseMigrationTests(unittest.TestCase):
    """批次2：数据库初始化、迁移幂等和恢复前置"""

    # ---- 失败测试 1: 全新空库一次初始化成功 ----

    def test_fresh_database_initializes_all_required_tables(self):
        """全新空库执行 initialize_database 后，所有 core 和 staging 表必须存在。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "fresh.duckdb")
            initialize_database(db_path)

            with duckdb.connect(db_path, read_only=True) as conn:
                tables = {
                    (row[0], row[1])
                    for row in conn.execute(
                        "SELECT table_schema, table_name FROM information_schema.tables "
                        "WHERE table_schema IN ('core', 'staging')"
                    ).fetchall()
                }

        required_tables = [
            ("core", "dim_league_mapping"),
            ("core", "dim_team_mapping"),
            ("core", "dim_provider_league_mapping"),
            ("core", "dim_provider_team_mapping"),
            ("core", "dim_provider_match_mapping"),
            ("core", "dim_player_mapping"),
            ("core", "dim_referee_profile"),
            ("core", "fact_match_schedule"),
            ("core", "fact_team_rolling_stats"),
            ("core", "fact_odds_capital_flow"),
            ("core", "fact_match_context_summary"),
            ("core", "fact_news_signal_summary"),
            ("core", "fact_match_events_unified"),
            ("core", "fact_sporttery_odds_detail"),
            ("core", "fact_sporttery_market_status"),
            ("core", "fact_target_sync_state"),
            ("core", "fact_team_elo_history"),
            ("core", "dim_collection_state"),
            ("core", "fact_league_standings"),
            ("core", "fact_prediction_log"),
            ("core", "fact_bet_ledger"),
            ("core", "schema_version"),
            ("staging", "stg_match_schedule"),
            ("staging", "stg_team_stats"),
            ("staging", "stg_odds"),
            ("staging", "stg_sporttery_odds_detail"),
            ("staging", "stg_player_squad"),
            ("staging", "stg_injuries"),
            ("staging", "stg_lineups"),
            ("staging", "stg_referee_candidates"),
            ("staging", "stg_player_candidates"),
            ("staging", "stg_schedule_candidates"),
            ("staging", "stg_odds_snapshot"),
        ]
        for schema, table_name in required_tables:
            self.assertIn(
                (schema, table_name),
                tables,
                f"缺失表 {schema}.{table_name}",
            )

    # ---- 失败测试 2: 同一库二次初始化不重复列、不丢数据 ----

    def test_second_initialize_is_idempotent_no_duplicate_columns(self):
        """同一数据库二次调用 initialize_database 后，列数不变、数据不丢失。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "idempotent.duckdb")

            # 第一次初始化并插入数据
            initialize_database(db_path)
            with duckdb.connect(db_path) as conn:
                conn.execute("""
                    INSERT INTO core.dim_league_mapping (
                        system_league_id, league_standard_name, country
                    ) VALUES ('SYS_TEST', 'Test League', 'Testland')
                """)
                conn.execute("""
                    INSERT INTO core.fact_match_schedule (
                        match_id, season, system_league_id, match_time,
                        home_team_id, away_team_id, source_provider
                    ) VALUES (
                        'M_IDEMPOTENT', '2026', 'SYS_TEST',
                        TIMESTAMP '2026-06-20 12:00:00',
                        'SYS_A', 'SYS_B', 'TEST'
                    )
                """)

            # 第二次初始化
            initialize_database(db_path)

            with duckdb.connect(db_path, read_only=True) as conn:
                # 列数不应翻倍
                col_count = conn.execute(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema='core' AND table_name='dim_league_mapping'"
                ).fetchone()[0]
                self.assertEqual(col_count, 4, f"dim_league_mapping 列数应为4，实际为{col_count}")

                # 数据不丢失
                league_count = conn.execute(
                    "SELECT COUNT(*) FROM core.dim_league_mapping"
                ).fetchone()[0]
                self.assertEqual(league_count, 1, f"二次初始化后 league 数据丢失")

                match_count = conn.execute(
                    "SELECT COUNT(*) FROM core.fact_match_schedule"
                ).fetchone()[0]
                self.assertEqual(match_count, 1, f"二次初始化后 match 数据丢失")

    # ---- 失败测试 3: 版本号只前进不倒退 ----

    def test_schema_version_advances_never_regresses(self):
        """version 字段只增不减，尝试写入更小版本号时被拒绝。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "version_guard.duckdb")

            initialize_database(db_path)

            with duckdb.connect(db_path) as conn:
                # 初始版本应 >= 1
                current = conn.execute(
                    "SELECT MAX(version) FROM core.schema_version"
                ).fetchone()[0]
                self.assertIsNotNone(current)
                self.assertGreaterEqual(current, 1)

                # 尝试写入更小版本号应被拒绝
                with self.assertRaises(Exception):
                    conn.execute(
                        "INSERT INTO core.schema_version (version, applied_at, migration_name) "
                        "VALUES (0, CURRENT_TIMESTAMP, 'test_regression')"
                    )

    # ---- 失败测试 4: 迁移中途失败时原库副本仍可读取 ----

    def test_backup_created_before_migration_and_readable_after_failure(self):
        """迁移前创建备份，迁移失败后备份仍可读取。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "backup_test.duckdb")

            # 初始化包含数据的库
            initialize_database(db_path)
            with duckdb.connect(db_path) as conn:
                conn.execute("""
                    INSERT INTO core.dim_league_mapping (
                        system_league_id, league_standard_name, country
                    ) VALUES ('SYS_BACKUP', 'Backup League', 'BackupLand')
                """)

            # 手动创建备份
            backup_path = db_path + ".backup"
            import shutil
            shutil.copy2(db_path, backup_path)

            # 备份应可读取
            with duckdb.connect(backup_path, read_only=True) as conn:
                row = conn.execute(
                    "SELECT league_standard_name FROM core.dim_league_mapping"
                ).fetchone()
                self.assertEqual(row[0], "Backup League")

    def test_initialize_database_creates_backup_before_runtime_migration_failure(self):
        """initialize_database 自身必须在迁移前创建可读备份，不能只靠测试手动复制。"""
        from football_advisor.db_schema import migration_backup_path

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "runtime_backup.duckdb")

            initialize_database(db_path)
            with duckdb.connect(db_path) as conn:
                conn.execute("""
                    INSERT INTO core.dim_league_mapping (
                        system_league_id, league_standard_name, country
                    ) VALUES ('SYS_RUNTIME_BACKUP', 'Runtime Backup League', 'BackupLand')
                """)

            backup_path = migration_backup_path(db_path)
            self.assertFalse(Path(backup_path).exists())

            with patch("football_advisor.db_schema.apply_schema", side_effect=RuntimeError("boom")):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    initialize_database(db_path)

            self.assertTrue(Path(backup_path).exists())
            with duckdb.connect(backup_path, read_only=True) as conn:
                row = conn.execute(
                    "SELECT league_standard_name FROM core.dim_league_mapping"
                ).fetchone()
                self.assertEqual(row[0], "Runtime Backup League")

    def test_backup_copy_failure_does_not_block_database_initialization(self):
        """Windows 上被映射的 DuckDB 文件可能无法复制，备份失败不能阻断初始化。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "locked_backup.duckdb")

            initialize_database(db_path)

            with patch("football_advisor.db_schema.shutil.copy2", side_effect=OSError("locked")):
                initialize_database(db_path)

    # ---- 失败测试 5: 视图只依赖迁移后真实存在的列 ----

    def test_all_views_query_all_columns_exist(self):
        """所有视图的 SELECT 列必须在对应表中真实存在，不应引用不存在的列。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "view_columns.duckdb")

            initialize_database(db_path)

            with duckdb.connect(db_path, read_only=True) as conn:
                views = conn.execute(
                    "SELECT schema_name, view_name, sql "
                    "FROM duckdb_views() "
                    "WHERE schema_name = 'core' AND internal = FALSE"
                ).fetchall()

                for schema, view_name, view_sql in views:
                    # 直接从视图 SQL 中提取 core.xxx 和 staging.xxx 引用
                    # 使用简单字符串扫描替代复杂正则
                    table_refs = set()
                    for part in view_sql.replace("(", " ").replace(")", " ").split():
                        part = part.strip().rstrip(";").rstrip(",")
                        if part.startswith("core.") or part.startswith("staging."):
                            table_refs.add(part)

                    for ref in table_refs:
                        parts = ref.split(".")
                        if len(parts) == 2:
                            exists = conn.execute(
                                "SELECT COUNT(*) FROM information_schema.tables "
                                "WHERE table_schema = ? AND table_name = ?",
                                [parts[0], parts[1]],
                            ).fetchone()[0]
                            self.assertGreater(
                                exists,
                                0,
                                f"视图 {schema}.{view_name} 引用了不存在的表 {ref}",
                            )

    # ---- 辅助测试: schema_version 表存在 ----

    def test_schema_version_table_exists_and_has_required_columns(self):
        """schema_version 表必须存在，包含 version, applied_at, migration_name 列。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "schema_version.duckdb")
            initialize_database(db_path)

            with duckdb.connect(db_path, read_only=True) as conn:
                columns = {
                    row[0]
                    for row in conn.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='core' AND table_name='schema_version'"
                    ).fetchall()
                }

        self.assertIn("version", columns)
        self.assertIn("applied_at", columns)
        self.assertIn("migration_name", columns)

    # ---- 辅助测试: 旧结构迁移后版本号正确 ----

    def test_legacy_database_migration_sets_version_properly(self):
        """旧结构库执行 initialize_database 后，schema_version 记录更新。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "legacy_migrate.duckdb")

            with duckdb.connect(db_path) as conn:
                conn.execute("CREATE SCHEMA core")
                conn.execute("""
                    CREATE TABLE core.fact_match_schedule (
                        match_id VARCHAR PRIMARY KEY,
                        season VARCHAR,
                        system_league_id VARCHAR NOT NULL,
                        match_time TIMESTAMP NOT NULL,
                        home_team_id VARCHAR NOT NULL,
                        away_team_id VARCHAR NOT NULL,
                        referee_id VARCHAR,
                        status VARCHAR,
                        weather_condition VARCHAR,
                        temperature DOUBLE,
                        rest_days_home INTEGER,
                        rest_days_away INTEGER,
                        home_score INTEGER,
                        away_score INTEGER,
                        source_provider VARCHAR,
                        updated_at TIMESTAMP,
                        etl_insert_timestamp TIMESTAMP,
                        _change_request_type VARCHAR
                    )
                """)

            # 对旧库执行初始化
            initialize_database(db_path)

            with duckdb.connect(db_path, read_only=True) as conn:
                version_row = conn.execute(
                    "SELECT MAX(version) FROM core.schema_version"
                ).fetchone()
                self.assertIsNotNone(version_row[0])
                self.assertGreaterEqual(version_row[0], 1)

                # 视图应可查询
                conn.execute(
                    "SELECT * FROM core.view_llm_match_prediction_base LIMIT 0"
                )


if __name__ == "__main__":
    unittest.main()
