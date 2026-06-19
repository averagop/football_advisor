"""批次2 数据库迁移验证脚本

验证项:
- fixture 模式: 使用临时数据库验证全量初始化 → 二次幂等 → 旧结构迁移 → 视图列校验
- 输出 JSON 证据到 stdout
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import duckdb


def run_fixture_verification() -> dict:
    """使用临时数据库执行完整验证流程，返回结构化证据。"""
    results = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "checks": [],
    }
    temp_dir = tempfile.mkdtemp()

    try:
        # 延迟导入以避免循环依赖
        from football_advisor.db_schema import initialize_database

        # ---- 检查 1: 全新空库初始化 ----
        fresh_path = str(Path(temp_dir) / "fresh.duckdb")
        initialize_database(fresh_path)
        with duckdb.connect(fresh_path, read_only=True) as conn:
            table_count = conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema IN ('core', 'staging')"
            ).fetchone()[0]
            version = conn.execute(
                "SELECT MAX(version) FROM core.schema_version"
            ).fetchone()[0]
        results["checks"].append({
            "name": "fresh_initialization",
            "passed": table_count >= 30 and version is not None,
            "table_count": table_count,
            "schema_version": version,
        })

        # ---- 检查 2: 二次初始化幂等 ----
        initialize_database(fresh_path)
        with duckdb.connect(fresh_path, read_only=True) as conn:
            table_count_2 = conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema IN ('core', 'staging')"
            ).fetchone()[0]
            col_count = conn.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema='core' AND table_name='dim_league_mapping'"
            ).fetchone()[0]
        results["checks"].append({
            "name": "idempotent_second_init",
            "passed": table_count_2 == table_count and col_count == 4,
            "table_count": table_count_2,
            "dim_league_mapping_columns": col_count,
            "expected_columns": 4,
        })

        # ---- 检查 3: 旧结构迁移 ----
        legacy_path = str(Path(temp_dir) / "legacy.duckdb")
        with duckdb.connect(legacy_path) as conn:
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
        initialize_database(legacy_path)
        with duckdb.connect(legacy_path, read_only=True) as conn:
            post_version = conn.execute(
                "SELECT MAX(version) FROM core.schema_version"
            ).fetchone()[0]
            columns = {
                row[0]
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='core' AND table_name='fact_match_schedule'"
                ).fetchall()
            }
            # 验证视图可查询
            try:
                conn.execute("SELECT * FROM core.view_llm_match_prediction_base LIMIT 0")
                view_ok = True
            except Exception:
                view_ok = False
        results["checks"].append({
            "name": "legacy_migration",
            "passed": (
                post_version is not None
                and post_version >= 1
                and "is_neutral_venue" in columns
                and view_ok
            ),
            "post_version": post_version,
            "has_is_neutral_venue": "is_neutral_venue" in columns,
            "view_queryable": view_ok,
        })

        # ---- 检查 4: 备份恢复 ----
        backup_path = fresh_path + ".backup"
        shutil.copy2(fresh_path, backup_path)
        with duckdb.connect(backup_path, read_only=True) as conn:
            backup_table_count = conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema IN ('core', 'staging')"
            ).fetchone()[0]
        results["checks"].append({
            "name": "backup_readable",
            "passed": backup_table_count == table_count,
            "backup_table_count": backup_table_count,
            "original_table_count": table_count,
        })

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    results["all_passed"] = all(c["passed"] for c in results["checks"])
    results["completed_at"] = datetime.now(timezone.utc).isoformat()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="数据库迁移验证")
    parser.add_argument(
        "--mode",
        choices=["fixture"],
        default="fixture",
        help="验证模式: fixture 使用临时数据库 (默认)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="输出 JSON 文件路径 (默认 stdout)",
    )
    args = parser.parse_args()

    if args.mode == "fixture":
        evidence = run_fixture_verification()
    else:
        evidence = {"error": f"unknown mode: {args.mode}"}

    output = json.dumps(evidence, indent=2, ensure_ascii=False)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Evidence written to {args.output}")
    else:
        print(output)

    if not evidence.get("all_passed", False):
        sys.exit(1)


if __name__ == "__main__":
    main()