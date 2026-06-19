"""Sporttery 比赛迁移脚本：将 M_ST_ 前缀替换为标准 match_id。

使用方式:
    python -m football_advisor.reconcile_sporttery_matches <duckdb_path> [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta

import duckdb

from football_advisor.db_schema import apply_schema
from football_advisor.match_resolver import CanonicalMatchResolver, MatchResolutionError

logger = logging.getLogger(__name__)


def reconcile(connection: duckdb.DuckDBPyConnection, dry_run: bool = False) -> dict[str, int]:
    """迁移 Sporttery 比赛到标准 match_id。

    返回统计: {removed, resolved, remerged, errors}
    """
    resolver = CanonicalMatchResolver(connection)
    stats: dict[str, int] = {"removed": 0, "resolved": 0, "remerged": 0, "errors": 0}

    # 1. 读取 staging 中的 Sporttery 比赛
    staging_matches = connection.execute("""
        SELECT
            s.provider_match_id,
            s.provider_league_id,
            s.provider_home_team_id,
            s.provider_away_team_id,
            s.match_time
        FROM staging.stg_match_schedule s
        WHERE s.source_provider = 'SportteryOfficialWeb'
    """).fetchall()

    if not staging_matches:
        logger.info("staging 中无 Sporttery 比赛，无需迁移")
        return stats

    logger.info("发现 %d 条 Sporttery staging 比赛", len(staging_matches))

    # 2. 删除旧的 M_ST_ 前缀记录（必须在解析前删除，避免多命中）
    old_match_ids = connection.execute("""
        SELECT match_id FROM core.fact_match_schedule
        WHERE match_id LIKE 'M_ST_%'
    """).fetchall()
    old_match_ids = [row[0] for row in old_match_ids]

    if old_match_ids:
        logger.info("删除 %d 条 M_ST_ 前缀的 core 比赛记录", len(old_match_ids))
        if not dry_run:
            placeholders = ",".join("?" for _ in old_match_ids)
            connection.execute(
                f"DELETE FROM core.fact_odds_capital_flow WHERE match_id IN ({placeholders})",
                old_match_ids,
            )
            connection.execute(
                f"DELETE FROM core.fact_match_schedule WHERE match_id IN ({placeholders})",
                old_match_ids,
            )
            stats["removed"] = len(old_match_ids)
    else:
        logger.info("未发现 M_ST_ 前缀的 core 记录")

    # 3. 逐条解析并创建映射
    for provider_match_id, league_id, home_team, away_team, match_time in staging_matches:
        try:
            resolved = resolver.resolve(
                provider_name="SportteryOfficialWeb",
                provider_match_id=str(provider_match_id),
                provider_league_id=str(league_id),
                provider_home_team_id=str(home_team),
                provider_away_team_id=str(away_team),
                kickoff_time=match_time if isinstance(match_time, datetime) else datetime.fromisoformat(str(match_time)),
                persist_mapping=not dry_run,
            )
            logger.info(
                "已解析: %s -> %s (%s)",
                provider_match_id, resolved.system_match_id, resolved.resolution_method,
            )
            stats["resolved"] += 1
        except MatchResolutionError as exc:
            logger.warning("解析失败: %s — %s", provider_match_id, exc)
            stats["errors"] += 1

    # 4. 重新合并 schedule 和 odds
    if not dry_run and stats["resolved"] > 0:
        provider_match_ids = [str(m[0]) for m in staging_matches]
        placeholders = ",".join("?" for _ in provider_match_ids)

        # 重新合并 schedule
        connection.execute(
            f"""
            INSERT INTO core.fact_match_schedule (
                match_id, season, system_league_id, match_time,
                home_team_id, away_team_id, status, source_provider,
                updated_at, etl_insert_timestamp
            )
            SELECT
                m.system_match_id,
                strftime(s.match_time, '%Y'),
                COALESCE(lm.system_league_id, s.provider_league_id),
                s.match_time,
                COALESCE(hm.system_team_id, s.provider_home_team_id),
                COALESCE(am.system_team_id, s.provider_away_team_id),
                COALESCE(s.status, 'PRE-MATCH'),
                s.source_provider,
                CAST(CURRENT_TIMESTAMP AS TIMESTAMP),
                CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
            FROM staging.stg_match_schedule s
            INNER JOIN core.dim_provider_match_mapping m
                ON s.source_provider = m.provider_name
               AND s.provider_match_id = m.provider_match_id
            LEFT JOIN core.dim_provider_league_mapping lm
              ON s.source_provider = lm.provider_name
             AND s.provider_league_id = lm.provider_league_id
            LEFT JOIN core.dim_provider_team_mapping hm
              ON s.source_provider = hm.provider_name
             AND s.provider_home_team_id = hm.provider_team_id
            LEFT JOIN core.dim_provider_team_mapping am
              ON s.source_provider = am.provider_name
             AND s.provider_away_team_id = am.provider_team_id
            WHERE s.source_provider = 'SportteryOfficialWeb'
              AND s.provider_match_id IN ({placeholders})
            ON CONFLICT (match_id)
            DO UPDATE SET
                season = excluded.season,
                system_league_id = excluded.system_league_id,
                match_time = excluded.match_time,
                home_team_id = excluded.home_team_id,
                away_team_id = excluded.away_team_id,
                status = excluded.status,
                source_provider = excluded.source_provider,
                updated_at = excluded.updated_at,
                etl_insert_timestamp = excluded.etl_insert_timestamp
            """,
            provider_match_ids,
        )

        # 重新合并 odds
        connection.execute(
            f"""
            INSERT INTO core.fact_odds_capital_flow (
                match_id, snapshot_time, odds_type, bookmaker_name,
                handicap_line, home_odds, draw_odds, away_odds, source_provider
            )
            SELECT
                m.system_match_id,
                o.snapshot_time,
                o.odds_type,
                o.bookmaker_name,
                o.handicap_line,
                o.home_odds,
                o.draw_odds,
                o.away_odds,
                o.source_provider
            FROM staging.stg_odds o
            INNER JOIN core.dim_provider_match_mapping m
                ON o.source_provider = m.provider_name
               AND o.provider_match_id = m.provider_match_id
            WHERE o.source_provider = 'SportteryOfficialWeb'
              AND o.provider_match_id IN ({placeholders})
            ON CONFLICT (match_id, snapshot_time, odds_type, bookmaker_name)
            DO UPDATE SET
                handicap_line = excluded.handicap_line,
                home_odds = excluded.home_odds,
                draw_odds = excluded.draw_odds,
                away_odds = excluded.away_odds,
                source_provider = excluded.source_provider
            """,
            provider_match_ids,
        )

        # 重新合并 detail odds
        connection.execute(
            f"""
            INSERT INTO core.fact_sporttery_odds_detail (
                match_id, odds_type, outcome_key, odds_value,
                snapshot_time, source_provider
            )
            SELECT
                m.system_match_id,
                d.odds_type,
                d.score_key,
                d.odds_value,
                d.snapshot_time,
                d.source_provider
            FROM staging.stg_sporttery_odds_detail d
            INNER JOIN core.dim_provider_match_mapping m
                ON d.source_provider = m.provider_name
               AND d.provider_match_id = m.provider_match_id
            WHERE d.source_provider = 'SportteryOfficialWeb'
              AND d.provider_match_id IN ({placeholders})
            ON CONFLICT (match_id, odds_type, outcome_key, snapshot_time) DO NOTHING
            """,
            provider_match_ids,
        )

        # 统计
        remerged_schedule = connection.execute(
            f"""
            SELECT COUNT(DISTINCT m.system_match_id)
            FROM staging.stg_match_schedule s
            INNER JOIN core.dim_provider_match_mapping m
                ON s.source_provider = m.provider_name
               AND s.provider_match_id = m.provider_match_id
            WHERE s.provider_match_id IN ({placeholders})
            """,
            provider_match_ids,
        ).fetchone()[0]
        remerged_odds = connection.execute(
            f"""
            SELECT COUNT(*)
            FROM staging.stg_odds o
            INNER JOIN core.dim_provider_match_mapping m
                ON o.source_provider = m.provider_name
               AND o.provider_match_id = m.provider_match_id
            WHERE o.provider_match_id IN ({placeholders})
            """,
            provider_match_ids,
        ).fetchone()[0]
        stats["remerged"] = remerged_odds

        logger.info(
            "重新合并: %d 场比赛, %d 条赔率",
            remerged_schedule, remerged_odds,
        )

    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="迁移 Sporttery 比赛到标准 match_id")
    parser.add_argument("db_path", help="DuckDB 数据库路径")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不实际修改")
    args = parser.parse_args()

    connection = duckdb.connect(args.db_path)
    try:
        apply_schema(connection)
        stats = reconcile(connection, dry_run=args.dry_run)
        mode = "[DRY-RUN] " if args.dry_run else ""
        logger.info(
            "%s迁移完成: 删除=%d, 解析=%d, 重新合并=%d, 错误=%d",
            mode, stats["removed"], stats["resolved"], stats["remerged"], stats["errors"],
        )
    finally:
        connection.close()


if __name__ == "__main__":
    main()
