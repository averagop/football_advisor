from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from football_advisor.db_schema import apply_schema
from football_advisor.provider_mapping_importer import import_provider_mappings
from football_advisor.sporttery_client import SportteryClient

SPORTTERY_SEED_LEAGUES = (
    ("INTL_FRIENDLY", "International Friendly", "International"),
)

SPORTTERY_SEED_TEAMS = (
    ("WC_TEAM_CRO", "Croatia", "Croatia", 1755.0),
    ("WC_TEAM_SLO", "Slovenia", "Slovenia", 1640.0),
    ("WC_TEAM_MAR", "Morocco", "Morocco", 1725.0),
    ("WC_TEAM_NOR", "Norway", "Norway", 1690.0),
    ("WC_TEAM_GRE", "Greece", "Greece", 1650.0),
    ("WC_TEAM_ITA", "Italy", "Italy", 1785.0),
    ("WC_TEAM_COL", "Colombia", "Colombia", 1740.0),
    ("WC_TEAM_JOR", "Jordan", "Jordan", 1580.0),
)


def load_env_file(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env_value(values: dict[str, str], key: str, default: str | None = None) -> str | None:
    value = values.get(key) or os.environ.get(key) or default
    if value is None:
        return None
    value = value.strip()
    return value or None


def run_sync(
    *,
    env_path: Path,
    target_date: date | None = None,
) -> dict[str, Any]:
    env_values = load_env_file(env_path)
    base_url = _env_value(
        env_values,
        "FOOTBALL_SPORTTERY_BASE_URL",
        "https://www.sporttery.cn",
    )

    db_path = _env_value(env_values, "FOOTBALL_DUCKDB_PATH", "football_system.db")
    assert db_path is not None
    client = SportteryClient(base_url=base_url)
    with duckdb.connect(db_path) as connection:
        apply_schema(connection)
        ensure_sporttery_seed_entities(connection)
        mapping_result = import_provider_mappings(
            connection, ROOT / "data" / "provider_mappings"
        )
        result = client.fetch_football_odds(connection, target_date=target_date)

    return {
        "provider": SportteryClient.PROVIDER_NAME,
        "status": result.get("status", "unknown"),
        "match_count": int(result.get("match_count", 0)),
        "odds_count": int(result.get("odds_count", 0)),
        "merged_match_count": int(result.get("merged_match_count", 0)),
        "merged_odds_count": int(result.get("merged_odds_count", 0)),
        "skipped_unmapped_count": int(result.get("skipped_unmapped_count", 0)),
        "mapping": mapping_result.as_details(),
    }


def ensure_sporttery_seed_entities(connection: Any) -> None:
    for league_id, name, country in SPORTTERY_SEED_LEAGUES:
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
    for team_id, name, country, elo in SPORTTERY_SEED_TEAMS:
        connection.execute(
            """
            INSERT INTO core.dim_team_mapping (
                system_team_id, team_standard_name, country, elo_rating_base, updated_at
            ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (system_team_id) DO UPDATE SET
                team_standard_name = excluded.team_standard_name,
                country = excluded.country,
                updated_at = excluded.updated_at
            """,
            [team_id, name, country, elo],
        )


def format_result(result: dict[str, Any]) -> str:
    lines = [
        "# 中国竞彩网官方竞彩足球同步结果",
        "",
        f"- provider: {result['provider']}",
        f"- status: {result['status']}",
        f"- match_count: {result['match_count']}",
        f"- odds_count: {result['odds_count']}",
        f"- merged_match_count: {result['merged_match_count']}",
        f"- merged_odds_count: {result['merged_odds_count']}",
        f"- skipped_unmapped_count: {result['skipped_unmapped_count']}",
    ]
    if result["status"] == "skipped_no_base_url":
        lines.extend(
            [
                "",
                "未配置 FOOTBALL_SPORTTERY_BASE_URL，未发起官方数据请求。",
            ]
        )
    elif result["skipped_unmapped_count"]:
        lines.extend(
            [
                "",
                "存在未映射赛事：数据已进入 staging，但未全部合并到 core。",
                "请补齐 SportteryOfficialWeb 的联赛和球队 provider mapping。",
            ]
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同步中国竞彩网官方竞彩足球固定奖金")
    parser.add_argument(
        "--env-path",
        type=Path,
        default=ROOT / ".env",
        help="本地 .env 路径",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="可选目标日期，格式 YYYY-MM-DD；不传则同步接口返回的全部可解析赛事",
    )
    args = parser.parse_args(argv)

    target_date = date.fromisoformat(args.date) if args.date else None
    result = run_sync(env_path=args.env_path, target_date=target_date)
    print(format_result(result))
    return 0 if result["status"] in {"success", "skipped_no_base_url"} else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
