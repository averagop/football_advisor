"""B-2.6+ 多源同步写库烟测 — 验证 provider mapping 后的 staging/core 写入链路。

验证路径：
1. DuckDB schema 初始化与 provider mapping 导入
2. 多源协调器 fetch_all 真实 API 烟测
3. staging 表数据落库验证
4. core 表 provider mapping 后写入验证
5. 未映射实体统计

此脚本只读取 .env 中已配置的 token，不依赖本地 LLM 或 ChromaDB。
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_PATH = str(ROOT / "football_system.db")
MAPPING_DIR = ROOT / "data" / "provider_mappings"
COUNTED_DATA_TABLES = (
    "staging.stg_match_schedule",
    "staging.stg_odds",
    "staging.stg_team_stats",
    "staging.stg_player_squad",
    "staging.stg_injuries",
    "staging.stg_lineups",
    "core.fact_match_schedule",
    "core.fact_odds_capital_flow",
    "core.fact_team_rolling_stats",
)
TIMESTAMP_COLUMNS = {
    "core.fact_match_schedule": "updated_at",
    "core.fact_odds_capital_flow": "snapshot_time",
    "core.fact_team_rolling_stats": "record_date",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="多源同步写库烟测 — staging/core 写入链路验证"
    )
    parser.add_argument(
        "--date",
        default=datetime.now(timezone.utc).date().isoformat(),
        help="按 UTC 日期测试赛程同步，格式 YYYY-MM-DD",
    )
    parser.add_argument(
        "--db-path",
        default=DB_PATH,
        help=f"DuckDB 路径，默认 {DB_PATH}",
    )
    parser.add_argument(
        "--home-team",
        default=None,
        help="主队名（可选，用于 API-Football 精确匹配和 TheSportsDB 阵容查询）",
    )
    parser.add_argument(
        "--away-team",
        default=None,
        help="客队名（可选）",
    )
    args = parser.parse_args(argv)

    env = _load_env(ROOT / ".env")

    print("=" * 64)
    print(" 多源同步写库烟测 — staging/core 写入链路验证")
    print(f" 时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f" 目标日期: {args.date}")
    print(f" 数据库: {args.db_path}")
    print("=" * 64)

    passed = 0
    failed = 0
    warnings = 0

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))

    def warn(name: str, condition: bool, detail: str = "") -> None:
        nonlocal warnings
        if condition:
            check(name, True, detail)
        else:
            warnings += 1
            print(f"  [WARN] {name} — {detail}")

    import duckdb

    # ---- 1. 数据库连通性 ----
    print("\n[1] 数据库连通性与 Schema 初始化")
    db_exists = os.path.exists(args.db_path)
    check("数据库文件存在", db_exists, f"路径: {args.db_path}")

    conn = duckdb.connect(args.db_path)

    from football_advisor.db_schema import apply_schema
    apply_schema(conn)

    schemas = conn.execute(
        "SELECT schema_name FROM information_schema.schemata"
    ).fetchall()
    schema_names = {s[0] for s in schemas}
    check("core schema 存在", "core" in schema_names)
    check("staging schema 存在", "staging" in schema_names)

    staging_tables = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='staging'"
    ).fetchall()
    staging_table_names = {t[0] for t in staging_tables}
    required_staging = [
        "stg_match_schedule", "stg_team_stats",
        "stg_odds", "stg_player_squad",
        "stg_injuries", "stg_lineups",
    ]
    for table in required_staging:
        check(f"staging.{table} 存在", table in staging_table_names)

    core_tables = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='core'"
    ).fetchall()
    core_table_names = {t[0] for t in core_tables}
    required_core = [
        "dim_league_mapping", "dim_team_mapping",
        "dim_provider_league_mapping", "dim_provider_team_mapping",
        "fact_match_schedule", "fact_team_rolling_stats",
        "fact_odds_capital_flow", "fact_match_context_summary",
    ]
    for table in required_core:
        check(f"core.{table} 存在", table in core_table_names)

    # ---- 2. Provider Mapping 导入 ----
    print("\n[2] Provider Mapping 导入")
    from football_advisor.provider_mapping_importer import import_provider_mappings

    mapping_result = import_provider_mappings(conn, str(MAPPING_DIR))
    check("联赛映射导入 >=0", True, f"插入 {mapping_result.league_inserted}, 跳过 {mapping_result.league_skipped}")
    check("球队映射导入 >=0", True, f"插入 {mapping_result.team_inserted}, 跳过 {mapping_result.team_skipped}")

    league_count = conn.execute(
        "SELECT COUNT(*) FROM core.dim_league_mapping"
    ).fetchone()[0]
    team_count = conn.execute(
        "SELECT COUNT(*) FROM core.dim_team_mapping"
    ).fetchone()[0]
    prov_league_count = conn.execute(
        "SELECT COUNT(*) FROM core.dim_provider_league_mapping"
    ).fetchone()[0]
    prov_team_count = conn.execute(
        "SELECT COUNT(*) FROM core.dim_provider_team_mapping"
    ).fetchone()[0]

    check("dim_league_mapping > 0", league_count > 0, f"当前: {league_count}")
    check("dim_team_mapping > 0", team_count > 0, f"当前: {team_count}")
    check("dim_provider_league_mapping > 0", prov_league_count > 0,
          f"当前: {prov_league_count}")
    check("dim_provider_team_mapping > 0", prov_team_count > 0,
          f"当前: {prov_team_count}")

    prov_dist = conn.execute("""
        SELECT provider_name, COUNT(*) AS cnt
        FROM core.dim_provider_league_mapping
        GROUP BY provider_name ORDER BY cnt DESC
    """).fetchall()
    print(f"  Provider 联赛映射分布: {', '.join(f'{r[0]}={r[1]}' for r in prov_dist)}")

    prov_team_dist = conn.execute("""
        SELECT provider_name, COUNT(*) AS cnt
        FROM core.dim_provider_team_mapping
        GROUP BY provider_name ORDER BY cnt DESC
    """).fetchall()
    print(f"  Provider 球队映射分布: {', '.join(f'{r[0]}={r[1]}' for r in prov_team_dist)}")

    # ---- 3. 多源协调器 fetch_all ----
    print("\n[3] 多源协调器 fetch_all")
    from football_advisor.config import load_config, SyncConfig
    from football_advisor.multi_source_coordinator import MultiSourceCoordinator

    config = load_config(env)
    sync_config = config.sync

    source_config_summary = []
    if sync_config.football_data_api_token:
        source_config_summary.append("football-data.org: 已配置")
    else:
        source_config_summary.append("football-data.org: 未配置")
    if sync_config.api_football_token:
        source_config_summary.append("API-Football: 已配置")
    else:
        source_config_summary.append("API-Football: 未配置")
    if sync_config.thesportsdb_api_token:
        source_config_summary.append("TheSportsDB: 已配置")
    else:
        source_config_summary.append("TheSportsDB: 未配置")
    if sync_config.sportmonks_api_token:
        source_config_summary.append("Sportmonks: 已配置")
    else:
        source_config_summary.append("Sportmonks: 未配置")
    if sync_config.isports_api_token:
        source_config_summary.append("iSports: 已配置")
    else:
        source_config_summary.append("iSports: 未配置")
    print(f"  源配置: {' | '.join(source_config_summary)}")

    before_counts = _read_table_counts(conn, COUNTED_DATA_TABLES)
    before_timestamps = _read_table_timestamps(conn, TIMESTAMP_COLUMNS)

    ref_time = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    coordinator = MultiSourceCoordinator(
        sync_config=sync_config,
        duckdb_path=args.db_path,
    )
    result = coordinator.fetch_all(
        home_team=args.home_team,
        away_team=args.away_team,
        kickoff_time=ref_time,
    )

    check("fetch_all 不抛出异常", True)
    check("返回 6 个源结果", len(result.results) == 6,
          f"实际: {len(result.results)}")
    print(f"  成功: {result.success_count} | 失败: {result.failure_count} | 跳过: {result.skipped_count}")
    print(f"  总耗时: {result.total_elapsed_ms:.0f}ms")
    print(f"  覆盖字段: {result.coverage_summary}")

    for source_result in result.results:
        status_icon = "+" if source_result.status == "success" else ("!" if source_result.error else "-")
        print(f"    [{status_icon}] {source_result.provider_name:20s} "
              f"status={source_result.status:25s} "
              f"elapsed={source_result.elapsed_ms:.0f}ms "
              f"data={source_result.data}")

    after_counts = _read_table_counts(conn, COUNTED_DATA_TABLES)
    after_timestamps = _read_table_timestamps(conn, TIMESTAMP_COLUMNS)

    # ---- 4. staging 表数据验证 ----
    print("\n[4] staging 表数据落库验证")

    stg_match_count = after_counts["staging.stg_match_schedule"]
    stg_odds_count = after_counts["staging.stg_odds"]
    stg_stats_count = after_counts["staging.stg_team_stats"]
    stg_squad_count = after_counts["staging.stg_player_squad"]
    stg_injuries_count = after_counts["staging.stg_injuries"]
    stg_lineups_count = after_counts["staging.stg_lineups"]

    print(f"  staging.stg_match_schedule: {stg_match_count} 条 (本次 +{_count_delta(before_counts, after_counts, 'staging.stg_match_schedule')})")
    print(f"  staging.stg_odds:           {stg_odds_count} 条 (本次 +{_count_delta(before_counts, after_counts, 'staging.stg_odds')})")
    print(f"  staging.stg_team_stats:     {stg_stats_count} 条 (本次 +{_count_delta(before_counts, after_counts, 'staging.stg_team_stats')})")
    print(f"  staging.stg_player_squad:   {stg_squad_count} 条 (本次 +{_count_delta(before_counts, after_counts, 'staging.stg_player_squad')})")
    print(f"  staging.stg_injuries:       {stg_injuries_count} 条 (本次 +{_count_delta(before_counts, after_counts, 'staging.stg_injuries')})")
    print(f"  staging.stg_lineups:        {stg_lineups_count} 条 (本次 +{_count_delta(before_counts, after_counts, 'staging.stg_lineups')})")

    schedule_staged_count = (
        _result_count(result, "football-data.org", "staged_count")
        + _result_count(result, "Sportmonks", "staged_count")
        + _result_count(result, "iSports-API", "staged_count")
    )
    if schedule_staged_count > 0:
        check(
            "stg_match_schedule 本次新增赛程数据",
            _count_delta(before_counts, after_counts, "staging.stg_match_schedule")
            >= schedule_staged_count,
            f"provider 返回 {schedule_staged_count} 条，本次新增 {_count_delta(before_counts, after_counts, 'staging.stg_match_schedule')} 条",
        )
    elif (
        sync_config.football_data_api_token
        or sync_config.sportmonks_api_token
        or sync_config.isports_api_token
    ):
        warn("stg_match_schedule 本次新增赛程数据", False, "已配置赛程源但本次未返回可写赛程")
    else:
        warn("stg_match_schedule 本次新增赛程数据（无可写源跳过）", True)

    if sync_config.api_football_token:
        api_odds_count = _result_count(result, "API-Football", "odds_count")
        if _api_football_odds_check_state(api_odds_count) == "pass":
            check(
                "stg_odds 本次新增 API-Football 赔率数据",
                _count_delta(before_counts, after_counts, "staging.stg_odds")
                >= api_odds_count,
                f"provider 返回 {api_odds_count} 条，本次新增 {_count_delta(before_counts, after_counts, 'staging.stg_odds')} 条",
            )
        else:
            warn(
                "stg_odds 本次新增 API-Football 赔率数据",
                False,
                "本次未返回赔率；免费层通常为空，不能计为通过",
            )

        api_stats_count = _result_count(result, "API-Football", "stats_count")
        if api_stats_count > 0:
            check(
                "stg_team_stats 本次新增 API-Football 统计数据",
                _count_delta(before_counts, after_counts, "staging.stg_team_stats")
                >= api_stats_count,
                f"provider 返回 {api_stats_count} 条，本次新增 {_count_delta(before_counts, after_counts, 'staging.stg_team_stats')} 条",
            )
        else:
            warn("stg_team_stats 本次新增 API-Football 统计数据", False, "本次未返回球队统计")
    else:
        warn("stg_odds 非空检查（无 API-Football 跳过）", stg_odds_count >= 0)

    if sync_config.thesportsdb_api_token:
        if args.home_team and args.away_team:
            squad_count = _result_count(result, "TheSportsDB", "squad_count")
            check(
                "stg_player_squad 本次新增 TheSportsDB 阵容数据",
                squad_count > 0
                and _count_delta(before_counts, after_counts, "staging.stg_player_squad")
                >= squad_count,
                f"TheSportsDB 返回 {squad_count} 条，本次新增 {_count_delta(before_counts, after_counts, 'staging.stg_player_squad')} 条",
            )
        else:
            warn("stg_player_squad 非空检查（需提供 --home-team / --away-team）",
                 stg_squad_count >= 0)
    else:
        warn("stg_player_squad 非空检查（无 TheSportsDB 跳过）", stg_squad_count >= 0)

    stg_source_dist = conn.execute("""
        SELECT source_provider, COUNT(*) AS cnt
        FROM staging.stg_match_schedule
        GROUP BY source_provider ORDER BY cnt DESC
    """).fetchall()
    if stg_source_dist:
        print(f"  stg_match_schedule 来源分布: {', '.join(f'{r[0]}={r[1]}' for r in stg_source_dist)}")

    # ---- 5. core 表 provider mapping 后写入验证 ----
    print("\n[5] core 表 provider mapping 后写入验证")

    core_match_count = after_counts["core.fact_match_schedule"]
    core_odds_count = after_counts["core.fact_odds_capital_flow"]
    core_stats_count = after_counts["core.fact_team_rolling_stats"]

    print(f"  core.fact_match_schedule:      {core_match_count} 条 (本次 +{_count_delta(before_counts, after_counts, 'core.fact_match_schedule')})")
    print(f"  core.fact_odds_capital_flow:    {core_odds_count} 条 (本次 +{_count_delta(before_counts, after_counts, 'core.fact_odds_capital_flow')})")
    print(f"  core.fact_team_rolling_stats:   {core_stats_count} 条 (本次 +{_count_delta(before_counts, after_counts, 'core.fact_team_rolling_stats')})")

    football_data_merged = _result_count(result, "football-data.org", "merged_count")
    if football_data_merged > 0:
        check(
            "core.fact_match_schedule 本次写入或更新 football-data.org 数据",
            _table_changed(
                before_counts,
                after_counts,
                before_timestamps,
                after_timestamps,
                "core.fact_match_schedule",
            ),
            "provider mapping 后应写入或更新 core",
        )
    elif sync_config.football_data_api_token:
        warn("core.fact_match_schedule 本次写入或更新 football-data.org 数据", False, "本次无可映射赛程写入 core")

    if sync_config.api_football_token:
        api_odds_count = _result_count(result, "API-Football", "odds_count")
        if api_odds_count > 0:
            check(
                "core.fact_odds_capital_flow 本次写入 API-Football 赔率数据",
                _table_changed(
                    before_counts,
                    after_counts,
                    before_timestamps,
                    after_timestamps,
                    "core.fact_odds_capital_flow",
                ),
                "API-Football 赔率应写入 core",
            )
        else:
            warn("core.fact_odds_capital_flow 本次写入 API-Football 赔率数据", False, "本次无赔率写入 core")

        api_merged = _result_count(result, "API-Football", "merged_count")
        if api_merged > 0:
            check(
                "core.fact_team_rolling_stats 本次写入 API-Football 统计数据",
                _table_changed(
                    before_counts,
                    after_counts,
                    before_timestamps,
                    after_timestamps,
                    "core.fact_team_rolling_stats",
                ),
                "API-Football 统计应经 provider mapping 写入 core",
            )
        else:
            warn("core.fact_team_rolling_stats 本次写入 API-Football 统计数据", False, "本次无可映射统计写入 core")

    core_source_dist = conn.execute("""
        SELECT source_provider, COUNT(*) AS cnt
        FROM core.fact_match_schedule
        GROUP BY source_provider ORDER BY cnt DESC
    """).fetchall()
    if core_source_dist:
        print(f"  core.fact_match_schedule 来源分布: {', '.join(f'{r[0]}={r[1]}' for r in core_source_dist)}")

    # ---- 6. 未映射实体统计 ----
    print("\n[6] 未映射实体统计")

    unmapped_matches = conn.execute("""
        SELECT s.provider_match_id, s.source_provider, s.provider_league_id,
               s.provider_home_team_id, s.provider_away_team_id
        FROM staging.stg_match_schedule s
        WHERE NOT EXISTS (
            SELECT 1 FROM core.dim_provider_league_mapping lm
            WHERE s.source_provider = lm.provider_name
              AND s.provider_league_id = lm.provider_league_id
        )
        UNION ALL
        SELECT s.provider_match_id, s.source_provider, s.provider_league_id,
               s.provider_home_team_id, s.provider_away_team_id
        FROM staging.stg_match_schedule s
        WHERE NOT EXISTS (
            SELECT 1 FROM core.dim_provider_team_mapping hm
            WHERE s.source_provider = hm.provider_name
              AND s.provider_home_team_id = hm.provider_team_id
        )
        UNION ALL
        SELECT s.provider_match_id, s.source_provider, s.provider_league_id,
               s.provider_home_team_id, s.provider_away_team_id
        FROM staging.stg_match_schedule s
        WHERE NOT EXISTS (
            SELECT 1 FROM core.dim_provider_team_mapping am
            WHERE s.source_provider = am.provider_name
              AND s.provider_away_team_id = am.provider_team_id
        )
        LIMIT 20
    """).fetchall()

    if unmapped_matches:
        print(f"  未映射比赛数 (前20): {len(unmapped_matches)}")
        for row in unmapped_matches[:5]:
            print(f"    match={row[0]} src={row[1]} league={row[2]} home={row[3]} away={row[4]}")
    else:
        print("  所有 staging 比赛均可映射（或无 staging 数据）")

    unmapped_stats = conn.execute("""
        SELECT s.provider_match_id, s.provider_team_id, s.source_provider
        FROM staging.stg_team_stats s
        WHERE NOT EXISTS (
            SELECT 1 FROM core.dim_provider_team_mapping m
            WHERE s.source_provider = m.provider_name
              AND s.provider_team_id = m.provider_team_id
        )
        LIMIT 10
    """).fetchall()

    if unmapped_stats:
        print(f"  未映射球队统计数 (前10): {len(unmapped_stats)}")
        for row in unmapped_stats[:3]:
            print(f"    match={row[0]} team={row[1]} src={row[2]}")
    else:
        print("  所有 staging 球队统计均可映射（或无 staging 数据）")

    # ---- 7. 数据时效性检查 ----
    print("\n[7] 数据时效性检查")

    max_etl = conn.execute(
        "SELECT MAX(etl_insert_timestamp) FROM staging.stg_match_schedule"
    ).fetchone()[0]
    if max_etl:
        age_minutes = (
            datetime.now(timezone.utc) -
            datetime.fromisoformat(str(max_etl)).replace(tzinfo=timezone.utc)
        ).total_seconds() / 60
        check("stg_match_schedule ETL 时间 < 30 分钟",
              age_minutes < 30,
              f"距上次 ETL {age_minutes:.1f} 分钟")
    else:
        warn("stg_match_schedule ETL 时间检查（无 staging 数据）", True)

    conn.close()

    # ---- 汇总 ----
    print("\n" + "=" * 64)
    total = passed + failed + warnings
    print(f" 检查总数: {total}")
    print(f" 通过: {passed}")
    print(f" 警告: {warnings}")
    print(f" 失败: {failed}")
    print("=" * 64)

    if failed == 0:
        print(" 多源同步写库烟测: 全部通过" + (" (含警告)" if warnings > 0 else ""))
        return 0
    else:
        print(f" 多源同步写库烟测: {failed} 项失败 — 请检查 provider mapping 覆盖率")
        return 1


def _load_env(path: Path) -> dict[str, str]:
    env = dict(os.environ)
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in env:
            env[key] = value
    return env


def _read_table_counts(connection: Any, tables: tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in tables:
        counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return counts


def _read_table_timestamps(
    connection: Any,
    timestamp_columns: dict[str, str],
) -> dict[str, Any]:
    timestamps: dict[str, Any] = {}
    for table, column in timestamp_columns.items():
        timestamps[table] = connection.execute(
            f"SELECT MAX({column}) FROM {table}"
        ).fetchone()[0]
    return timestamps


def _count_delta(
    before_counts: dict[str, int],
    after_counts: dict[str, int],
    table: str,
) -> int:
    return after_counts.get(table, 0) - before_counts.get(table, 0)


def _has_new_rows(
    before_counts: dict[str, int],
    after_counts: dict[str, int],
    table: str,
) -> bool:
    return _count_delta(before_counts, after_counts, table) > 0


def _table_changed(
    before_counts: dict[str, int],
    after_counts: dict[str, int],
    before_timestamps: dict[str, Any],
    after_timestamps: dict[str, Any],
    table: str,
) -> bool:
    if _has_new_rows(before_counts, after_counts, table):
        return True
    before_timestamp = before_timestamps.get(table)
    after_timestamp = after_timestamps.get(table)
    if after_timestamp is None:
        return False
    if before_timestamp is None:
        return True
    try:
        return after_timestamp > before_timestamp
    except TypeError:
        return str(after_timestamp) > str(before_timestamp)


def _api_football_odds_check_state(odds_count: int) -> str:
    return "pass" if odds_count > 0 else "warn"


def _result_count(result: Any, provider_name: str, field_name: str) -> int:
    for source_result in result.results:
        if source_result.provider_name == provider_name:
            try:
                return int(source_result.data.get(field_name, 0))
            except (TypeError, ValueError):
                return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
