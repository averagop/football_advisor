"""统一赛前预测可用性门禁。

保留世界杯本地资料导入能力，同时支持通过联赛上下文和 match_id 检查其他比赛。
脚本不会打印任何密钥。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_DB_PATH = ROOT / "football_system.db"
DEFAULT_ENV_PATH = ROOT / ".env"
MAPPING_DIR = ROOT / "data" / "provider_mappings"

CORE_STRUCTURED_TOKENS = (
    ("football-data.org", "FOOTBALL_DATA_API_TOKEN"),
    ("API-Football", "API_FOOTBALL_TOKEN"),
    ("TheSportsDB", "THESPORTSDB_API_TOKEN"),
)
OPTIONAL_STRUCTURED_TOKENS = (
    ("Sportmonks", "SPORTMONKS_API_TOKEN"),
    ("iSports-API", "ISPORTS_API_TOKEN"),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="统一赛前预测可用性门禁")
    parser.add_argument("--home-team", required=True, help="主队标准名称，例如 USA")
    parser.add_argument("--away-team", required=True, help="客队标准名称，例如 Mexico")
    parser.add_argument("--date", required=True, help="检查日期，格式 YYYY-MM-DD")
    parser.add_argument("--match-id", help="可选：指定标准 match_id 跳过模糊查找")
    parser.add_argument(
        "--league",
        "--competition-context",
        dest="competition_context",
        default="World Cup",
        help="联赛或赛事上下文，默认 World Cup",
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help=f"DuckDB 路径，默认 {DEFAULT_DB_PATH}",
    )
    parser.add_argument(
        "--env-path",
        default=str(DEFAULT_ENV_PATH),
        help=f".env 路径，默认 {DEFAULT_ENV_PATH}",
    )
    parser.add_argument(
        "--schedule-csv",
        help="可选：本地世界杯赛程 CSV，只导入 confirmed_group_fixture",
    )
    parser.add_argument(
        "--team-stats-csv",
        help="可选：本地世界杯球队最近 5 场统计 CSV，用作静态基础快照",
    )
    args = parser.parse_args(argv)

    check_date = _parse_date(args.date)
    env = _load_env(Path(args.env_path))
    env["FOOTBALL_DUCKDB_PATH"] = str(args.db_path)

    print("=" * 72)
    print("统一赛前预测可用性门禁")
    print(f"检查对象: {args.home_team} vs {args.away_team}")
    print(f"联赛上下文: {args.competition_context or '未指定'}")
    print(f"检查日期: {check_date.date().isoformat()}")
    print(f"数据库: {args.db_path}")
    print("=" * 72)

    failures: list[str] = []
    warnings: list[str] = []
    target_match_time = check_date

    import duckdb

    from football_advisor.config import SearchConfig, load_config
    from football_advisor.db_schema import apply_schema
    from football_advisor.multi_source_coordinator import MultiSourceCoordinator
    from football_advisor.news_ingestion import NewsIngester
    from football_advisor.provider_mapping_importer import import_provider_mappings
    from football_advisor.search_router import SearchRouter
    from football_advisor.text_fetcher import TextFetcher
    from football_advisor.worldcup_basic_importer import import_worldcup_basic_data

    config = load_config(env)

    with duckdb.connect(str(args.db_path)) as conn:
        apply_schema(conn)
        _seed_world_cup_dimensions(conn)
        if args.schedule_csv or args.team_stats_csv:
            import_result = import_worldcup_basic_data(
                conn,
                schedule_csv_path=args.schedule_csv,
                team_stats_csv_path=args.team_stats_csv,
            )
            print(
                "本地世界杯基础资料导入: "
                f"球队 {import_result.inserted_counts.get('teams', 0)}, "
                f"赛程 {import_result.inserted_counts.get('fixtures', 0)}, "
                f"静态近期状态 {import_result.inserted_counts.get('rolling_stats', 0)}, "
                f"跳过淘汰赛占位 {import_result.skipped_counts.get('bracket_slots', 0)}"
            )
        mapping_result = import_provider_mappings(conn, MAPPING_DIR)
        print(
            "provider mapping 导入: "
            f"联赛写入/更新 {mapping_result.league_inserted}, "
            f"球队写入/更新 {mapping_result.team_inserted}, "
            f"跳过 {mapping_result.league_skipped + mapping_result.team_skipped}"
        )

        home_team_id = _find_team_id(conn, args.home_team)
        away_team_id = _find_team_id(conn, args.away_team)
        _record_check(
            failures,
            "主队实体存在",
            home_team_id is not None,
            f"未找到 {args.home_team}",
        )
        _record_check(
            failures,
            "客队实体存在",
            away_team_id is not None,
            f"未找到 {args.away_team}",
        )

        if home_team_id and away_team_id:
            missing_mapping_teams = _teams_missing_provider_mapping(
                conn, [home_team_id, away_team_id]
            )
            _record_check(
                failures,
                "目标球队 SportteryOfficialWeb mapping 存在",
                not missing_mapping_teams,
                ", ".join(missing_mapping_teams),
            )
            _print_provider_mapping_summary(conn, [home_team_id, away_team_id])

        pdf_player_conflicts = _find_pdf_player_team_conflicts(conn)
        _record_check(
            failures,
            "PDF 球员跨队污染检查",
            not pdf_player_conflicts,
            "; ".join(pdf_player_conflicts),
        )

        match_id = args.match_id or _find_match_id(
            conn, home_team_id, away_team_id, check_date
        )
        if match_id:
            target_match_time = _find_match_time(conn, match_id) or check_date
            print(f"目标比赛开球时间: {target_match_time.isoformat()}")
        if home_team_id and away_team_id:
            _record_check(
                failures,
                "目标比赛 match_id 存在",
                match_id is not None,
                "core.fact_match_schedule 未命中目标主客队和检查日期附近 7 天内的比赛",
            )

    missing_core_tokens = [
        provider for provider, key in CORE_STRUCTURED_TOKENS if not env.get(key)
    ]
    missing_optional_tokens = [
        provider for provider, key in OPTIONAL_STRUCTURED_TOKENS if not env.get(key)
    ]
    _record_check(
        failures,
        "结构化源 token 配置完整",
        not missing_core_tokens,
        ", ".join(missing_core_tokens),
    )
    if missing_optional_tokens:
        warnings.append("可选结构化源 token 未配置: " + ", ".join(missing_optional_tokens))
        print("[WARN] 可选结构化源 token 未配置: " + ", ".join(missing_optional_tokens))

    search_configured = bool(
        config.search.searxng_base_url
        or config.search.serper_api_key
    )
    external_llm_configured = bool(
        config.llm.external_base_url and config.llm.external_api_key
    )
    _record_check(
        failures,
        "搜索 provider 配置可用",
        search_configured,
        "配置 FOOTBALL_SEARXNG_BASE_URL 或 FOOTBALL_SERPER_API_KEY；兼容 FOOTBALL_GOOGLE_SEARCH_API_KEY 作为 Serper key",
    )
    _record_check(
        failures,
        "外部 LLM 配置完整",
        external_llm_configured,
        "配置 FOOTBALL_EXTERNAL_LLM_BASE_URL 与 FOOTBALL_EXTERNAL_LLM_API_KEY；不能把 Codex 会话模型作为 OpenWebUI 运行时 LLM",
    )

    structured_can_run = any(env.get(key) for _, key in CORE_STRUCTURED_TOKENS + OPTIONAL_STRUCTURED_TOKENS)
    if structured_can_run:
        print("\n[结构化同步预检]")
        result = MultiSourceCoordinator(
            sync_config=config.sync,
            duckdb_path=str(args.db_path),
        ).fetch_all(args.home_team, args.away_team, target_match_time)
        print(
            f"结构化同步结果: 成功 {result.success_count}, "
            f"失败 {result.failure_count}, 跳过 {result.skipped_count}, "
            f"耗时 {result.total_elapsed_ms:.0f}ms"
        )
        for source_result in sorted(result.results, key=lambda item: item.provider_name):
            print(
                f"- {source_result.provider_name}: "
                f"status={source_result.status}, "
                f"error={_safe_error(source_result.error)}"
            )
        _record_check(
            failures,
            "至少一个结构化源返回可用数据",
            _has_structured_payload(result.results),
            "已配置 token，但结构化源没有返回可合并赛程、赔率、统计、阵容、历史数据或近期状态",
        )
    else:
        print("\n[结构化同步预检] 跳过: 未配置任何结构化数据源 token")

    if search_configured:
        print("\n[搜索与新闻链路预检]")
        query = _build_match_news_query(
            args.home_team, args.away_team, args.competition_context
        )
        preview = SearchRouter(config=config.search).preview(query, limit=5)
        print(f"搜索查询: {query}")
        print(f"搜索结果: provider={preview.provider}, status={preview.status}, count={len(preview.results)}")
        _record_check(
            failures,
            "搜索预览返回可用结果",
            preview.status == "ok" and bool(preview.results),
            ",".join(preview.errors) or preview.status,
        )

        if preview.status == "ok" and preview.results:
            documents = TextFetcher().fetch_many([item.url for item in preview.results])
            valid_documents = [doc for doc in documents if doc.status == "ok" and doc.text]
            if (
                not valid_documents
                and preview.provider == "searxng"
                and config.search.serper_api_key
            ):
                print("SearXNG 正文无有效文本，改用 Serper 搜索备用")
                preview = SearchRouter(
                    config=SearchConfig(
                        serper_api_key=config.search.serper_api_key,
                        timeout_seconds=config.search.timeout_seconds,
                    )
                ).preview(query, limit=5)
                print(
                    f"Serper 搜索结果: provider={preview.provider}, "
                    f"status={preview.status}, count={len(preview.results)}"
                )
                documents = (
                    TextFetcher().fetch_many([item.url for item in preview.results])
                    if preview.status == "ok"
                    else []
                )
                valid_documents = [
                    doc for doc in documents if doc.status == "ok" and doc.text
                ]
            print(f"正文抓取: 有效 {len(valid_documents)} / 总计 {len(documents)}")
            _record_check(
                failures,
                "正文抓取返回有效文档",
                bool(valid_documents),
                "搜索有结果，但正文抓取无有效文本",
            )
            if valid_documents and match_id:
                ingest_result = NewsIngester(config=config, database_path=str(args.db_path)).ingest_news(
                    match_id,
                    valid_documents,
                )
                print(
                    "新闻入库: "
                    f"duckdb={ingest_result.get('duckdb_status')}, "
                    f"chromadb={ingest_result.get('chromadb_status')}, "
                    f"source_count={ingest_result.get('source_count', 0)}"
                )
                _record_check(
                    failures,
                    "新闻摘要写入 DuckDB",
                    ingest_result.get("duckdb_status") == "success",
                    str(ingest_result.get("duckdb_status", "unknown")),
                )
            elif valid_documents and not match_id:
                warnings.append("未找到目标比赛 match_id，已完成搜索和正文抓取，但跳过新闻摘要写入")
                print("[WARN] 未找到目标比赛 match_id，跳过新闻摘要写入")
    else:
        print("\n[搜索与新闻链路预检] 跳过: 搜索 provider 未配置")

    print("\n" + "=" * 72)
    print(f"失败: {len(failures)}")
    print(f"警告: {len(warnings)}")
    if failures:
        print("统一赛前预测可用性门禁: 未通过")
        return 1
    print("统一赛前预测可用性门禁: 通过")
    return 0


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


def _parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise SystemExit("--date 必须使用 YYYY-MM-DD 格式") from exc


def _seed_world_cup_dimensions(connection: Any) -> None:
    from scripts.seed_worldcup_data import (
        WORLD_CUP_FIXTURES,
        WORLD_CUP_LEAGUES,
        WORLD_CUP_TEAMS,
    )

    for league_id, name, country in WORLD_CUP_LEAGUES:
        connection.execute(
            """
            INSERT INTO core.dim_league_mapping (
                system_league_id, league_standard_name, country, updated_at
            ) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (system_league_id) DO UPDATE SET
                league_standard_name = excluded.league_standard_name,
                country = excluded.country,
                updated_at = excluded.updated_at
            """,
            [league_id, name, country],
        )

    for team_id, name, country, elo in WORLD_CUP_TEAMS:
        connection.execute(
            """
            INSERT INTO core.dim_team_mapping (
                system_team_id, team_standard_name, country, elo_rating_base, updated_at
            ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (system_team_id) DO UPDATE SET
                team_standard_name = excluded.team_standard_name,
                country = excluded.country,
                elo_rating_base = excluded.elo_rating_base,
                updated_at = excluded.updated_at
            """,
            [team_id, name, country, elo],
        )

    for (
        match_id,
        season,
        league_id,
        match_time,
        home_team_id,
        away_team_id,
        source_provider,
    ) in WORLD_CUP_FIXTURES:
        connection.execute(
            """
            INSERT INTO core.fact_match_schedule (
                match_id,
                season,
                system_league_id,
                match_time,
                home_team_id,
                away_team_id,
                status,
                source_provider,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'PRE-MATCH', ?, CURRENT_TIMESTAMP)
            ON CONFLICT (match_id) DO UPDATE SET
                season = excluded.season,
                system_league_id = excluded.system_league_id,
                match_time = excluded.match_time,
                home_team_id = excluded.home_team_id,
                away_team_id = excluded.away_team_id,
                status = excluded.status,
                source_provider = excluded.source_provider,
                updated_at = excluded.updated_at
            """,
            [
                match_id,
                season,
                league_id,
                match_time,
                home_team_id,
                away_team_id,
                source_provider,
            ],
        )


def _find_team_id(connection: Any, team_name: str) -> str | None:
    row = connection.execute(
        """
        SELECT system_team_id
        FROM core.dim_team_mapping
        WHERE lower(team_standard_name) = lower(?)
        LIMIT 1
        """,
        [team_name.strip()],
    ).fetchone()
    return str(row[0]) if row else None


def _teams_missing_provider_mapping(connection: Any, team_ids: list[str]) -> list[str]:
    rows = connection.execute(
        """
        SELECT t.team_standard_name
        FROM core.dim_team_mapping t
        WHERE t.system_team_id IN ({placeholders})
          AND NOT EXISTS (
              SELECT 1
              FROM core.dim_provider_team_mapping m
              WHERE m.system_team_id = t.system_team_id
                AND m.provider_name = 'SportteryOfficialWeb'
          )
        ORDER BY t.team_standard_name
        """.format(placeholders=", ".join(["?"] * len(team_ids))),
        team_ids,
    ).fetchall()
    return [str(row[0]) for row in rows]


def _print_provider_mapping_summary(connection: Any, team_ids: list[str]) -> None:
    rows = connection.execute(
        """
        SELECT t.team_standard_name, m.provider_name, COUNT(*) AS mapping_count
        FROM core.dim_team_mapping t
        JOIN core.dim_provider_team_mapping m ON t.system_team_id = m.system_team_id
        WHERE t.system_team_id IN ({placeholders})
        GROUP BY t.team_standard_name, m.provider_name
        ORDER BY t.team_standard_name, m.provider_name
        """.format(placeholders=", ".join(["?"] * len(team_ids))),
        team_ids,
    ).fetchall()
    if not rows:
        print("目标球队 provider mapping: 无")
        return
    print("目标球队 provider mapping:")
    for team_name, provider_name, mapping_count in rows:
        print(f"- {team_name}: {provider_name}={mapping_count}")


def _find_pdf_player_team_conflicts(connection: Any, limit: int = 5) -> list[str]:
    rows = connection.execute(
        """
        SELECT player_standard_name, string_agg(DISTINCT team_id, ', ' ORDER BY team_id) AS team_ids
        FROM core.dim_player_mapping
        WHERE system_player_id LIKE 'PDF_%'
          AND player_standard_name IS NOT NULL
          AND team_id IS NOT NULL
        GROUP BY lower(player_standard_name), player_standard_name
        HAVING COUNT(DISTINCT team_id) > 1
        ORDER BY player_standard_name
        LIMIT ?
        """,
        [limit],
    ).fetchall()
    return [f"{row[0]} -> {row[1]}" for row in rows]


def _has_structured_payload(results: list[Any]) -> bool:
    count_fields = (
        "merged_count",
        "staged_count",
        "odds_count",
        "stats_count",
        "squad_count",
        "history_count",
        "event_count",
        "lineup_count",
        "injury_count",
        "friendly_count",
        "mapped_team_stats_count",
    )
    for result in results:
        if getattr(result, "error", None):
            continue
        if str(getattr(result, "status", "")).startswith("skipped"):
            continue
        data = getattr(result, "data", {}) or {}
        for field in count_fields:
            value = data.get(field)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)) and value > 0:
                return True
    return False


def _find_match_id(
    connection: Any,
    home_team_id: str | None,
    away_team_id: str | None,
    check_date: datetime,
) -> str | None:
    if not home_team_id or not away_team_id:
        return None
    row = connection.execute(
        """
        SELECT match_id
        FROM core.fact_match_schedule
        WHERE home_team_id = ?
          AND away_team_id = ?
          AND abs(date_diff('day', match_time, ?)) <= 7
        ORDER BY abs(date_diff('minute', match_time, ?)) ASC
        LIMIT 1
        """,
        [home_team_id, away_team_id, check_date, check_date],
    ).fetchone()
    return str(row[0]) if row else None


def _find_match_time(connection: Any, match_id: str) -> datetime | None:
    row = connection.execute(
        """
        SELECT match_time
        FROM core.fact_match_schedule
        WHERE match_id = ?
        LIMIT 1
        """,
        [match_id],
    ).fetchone()
    if not row:
        return None
    return row[0]


def _build_match_news_query(
    home_team: str, away_team: str, competition_context: str
) -> str:
    context = competition_context.strip()
    competition = f"{context} " if context else ""
    return (
        f"{home_team} vs {away_team} {competition}squad injury suspension lineup "
        "press conference training tactical news"
    )


def _record_check(
    failures: list[str],
    name: str,
    condition: bool,
    detail: str = "",
) -> None:
    if condition:
        print(f"[PASS] {name}")
        return
    message = name if not detail else f"{name}: {detail}"
    failures.append(message)
    print(f"[FAIL] {message}")


def _safe_error(error: str | None) -> str:
    if not error:
        return ""
    scrubbed = re.sub(r"(?i)(token|key|authorization|api[-_ ]?key)=?[^,\s]+", r"\1=***", error)
    return scrubbed[:200]


if __name__ == "__main__":
    raise SystemExit(main())
