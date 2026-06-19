"""预测触发式赛后数据采集模块。

每次预测前检查是否有遗漏的完赛数据，按需从 API-Football 拉取
TOP5 联赛 + 世界杯的昨日赛果，更新状态、比分和比赛统计。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .elo_updater import EloUpdater

# 北京时间偏移
_BEIJING_TZ = timezone(timedelta(hours=8))


def _beijing_today() -> date:
    """返回北京时间今天的日期。"""
    return datetime.now(_BEIJING_TZ).date()


def _beijing_yesterday() -> date:
    """返回北京时间昨天的日期。"""
    return _beijing_today() - timedelta(days=1)


def _as_date(d: date | None) -> date:
    """将 None 安全转为 date 类型。"""
    if d is None:
        return _beijing_today()
    return d


@dataclass(frozen=True)
class CollectResult:
    status: str  # "success" | "skipped_already_collected" | "skipped_disabled" | "error"
    collected_days: int = 0
    matches_updated: int = 0
    matches_inserted: int = 0
    stats_inserted: int = 0
    elo_updated: int = 0
    affected_team_ids: tuple[str, ...] = ()
    error: str | None = None


class PostMatchCollector:
    """预测触发式赛后数据采集器。

    每次采集只拉取 TOP5 联赛 + 世界杯的完赛数据，
    自动补齐从上次采集到昨天的所有遗漏天数。
    """

    def __init__(
        self,
        api_token: str,
        base_url: str = "https://v3.football.api-sports.io",
        duckdb_path: str = "football_system.db",
        enabled: bool = True,
        target_leagues: tuple[str, ...] = (
            "TOP5_EPL",
            "TOP5_LA_LIGA",
            "TOP5_SERIE_A",
            "TOP5_BUNDESLIGA",
            "TOP5_LIGUE_1",
            "WC_WORLD_CUP_2026",
        ),
    ) -> None:
        self._api_token = api_token
        self._base_url = base_url.rstrip("/")
        self._duckdb_path = duckdb_path
        self._enabled = enabled
        self._target_leagues = target_leagues

    def collect_if_needed(self) -> CollectResult:
        """检查并执行赛后采集。每天第一次预测触发一次。"""
        if not self._enabled:
            return CollectResult(status="skipped_disabled")

        import duckdb

        try:
            with duckdb.connect(self._duckdb_path) as conn:
                return self._collect(conn)
        except Exception as exc:
            return CollectResult(status="error", error=str(exc))

    # ---- 内部实现 ----

    def _collect(self, conn: Any) -> CollectResult:
        # 1. 检查今天是否已采集
        last_date = self._get_last_collected_date(conn)
        today = _beijing_today()

        if last_date is not None and last_date >= today:
            return CollectResult(status="skipped_already_collected")

        # 2. 确定采集范围：从上次采集的次日到昨天
        # 首次运行（last_date=None）从昨天开始，只采集一天，避免首次全量拉取超配额
        date_from = (
            last_date + timedelta(days=1)
            if last_date is not None
            else _beijing_yesterday()
        )
        date_to = _beijing_yesterday()

        if date_from > date_to:
            # 无遗漏天数（例如今天凌晨，昨天还没比赛结束）
            # 更新采集日期为昨天，避免每次预测都反复检查
            self._set_last_collected_date(conn, date_to)
            return CollectResult(status="skipped_already_collected")

        # 3. 加载 provider league mapping（API-Football league_id → system_league_id）
        league_map = self._load_league_map(conn)
        if not league_map:
            return CollectResult(
                status="error",
                error="No API-Football provider league mapping found for target leagues",
            )

        # 4. 逐日拉取赛果
        total_matches_updated = 0
        total_matches_inserted = 0
        total_stats_inserted = 0
        affected_team_ids: set[str] = set()
        current = date_from
        while current <= date_to:
            fixtures = self._fetch_fixtures(current)
            day_updated, day_inserted, day_stats, day_team_ids = self._process_fixtures(
                conn, fixtures, league_map, current
            )
            total_matches_updated += day_updated
            total_matches_inserted += day_inserted
            total_stats_inserted += day_stats
            affected_team_ids.update(day_team_ids)
            current += timedelta(days=1)

        # 5. 触发 Elo 更新
        elo_updated = EloUpdater().update_unprocessed_matches(conn)

        # 6. 更新采集状态
        self._set_last_collected_date(conn, date_to)

        return CollectResult(
            status="success",
            collected_days=(date_to - date_from).days + 1,
            matches_updated=total_matches_updated,
            matches_inserted=total_matches_inserted,
            stats_inserted=total_stats_inserted,
            elo_updated=elo_updated,
            affected_team_ids=tuple(sorted(affected_team_ids)),
        )

    # ---- 日期状态管理 ----

    def _get_last_collected_date(self, conn: Any) -> date | None:
        row = conn.execute(
            "SELECT last_collected_date FROM core.dim_collection_state "
            "WHERE collection_type = 'post_match_results'"
        ).fetchone()
        if row is None:
            return None
        raw = row[0]
        if isinstance(raw, date):
            return raw
        if isinstance(raw, datetime):
            return raw.date()
        return None

    def _set_last_collected_date(self, conn: Any, collected_date: date) -> None:
        conn.execute(
            "INSERT INTO core.dim_collection_state (collection_type, last_collected_date) "
            "VALUES ('post_match_results', ?) "
            "ON CONFLICT (collection_type) DO UPDATE SET last_collected_date = ?",
            [collected_date, collected_date],
        )

    # ---- Provider 映射 ----

    def _load_league_map(self, conn: Any) -> dict[int, str]:
        """返回 {provider_league_id_int: system_league_id}。"""
        placeholders = ", ".join(["?"] * len(self._target_leagues))
        rows = conn.execute(
            f"""
            SELECT CAST(m.provider_league_id AS INTEGER), m.system_league_id
            FROM core.dim_provider_league_mapping m
            WHERE m.provider_name = 'API-Football'
              AND m.system_league_id IN ({placeholders})
            """,
            list(self._target_leagues),
        ).fetchall()
        return {int(row[0]): str(row[1]) for row in rows}

    # ---- API 调用 ----

    def _fetch_fixtures(self, target_date: date) -> list[dict[str, Any]]:
        """调用 API-Football fixtures?date=X 获取某天的所有比赛。"""
        url = f"{self._base_url}/fixtures?date={target_date.isoformat()}"
        req = urllib.request.Request(
            url, headers={"x-apisports-key": self._api_token}
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            return []
        if not isinstance(data, dict) or "response" not in data:
            return []
        return data.get("response", []) or []

    # ---- 数据处理 ----

    def _process_fixtures(
        self,
        conn: Any,
        fixtures: list[dict[str, Any]],
        league_map: dict[int, str],
        fixture_date: date,
    ) -> tuple[int, int, int, set[str]]:
        """处理某天的 fixtures，写入 DuckDB。

        返回 (matches_updated, matches_inserted, stats_inserted, affected_team_ids)。
        """
        matches_updated = 0
        matches_inserted = 0
        stats_inserted = 0
        affected_team_ids: set[str] = set()

        for fixture in fixtures:
            # 过滤联赛
            league_id = fixture.get("league", {}).get("id")
            system_league_id = league_map.get(league_id) if isinstance(league_id, int) else None
            if not system_league_id:
                continue

            # 只处理完赛的比赛
            status_short = fixture.get("fixture", {}).get("status", {}).get("short", "")
            if status_short != "FT":
                continue

            match_id = str(fixture["fixture"]["id"])
            teams = fixture.get("teams", {})
            home_team_name = teams.get("home", {}).get("name", "")
            away_team_name = teams.get("away", {}).get("name", "")
            goals = fixture.get("goals", {})
            home_score = goals.get("home")
            away_score = goals.get("away")

            if home_score is None or away_score is None:
                continue

            # 查找 provider team mapping
            home_system_id = self._resolve_team(conn, teams.get("home", {}).get("id"))
            away_system_id = self._resolve_team(conn, teams.get("away", {}).get("id"))

            if not home_system_id or not away_system_id:
                continue
            affected_team_ids.update((home_system_id, away_system_id))

            # 比赛时间
            match_time_str = fixture.get("fixture", {}).get("date", "")
            match_time = self._parse_match_time(match_time_str) or datetime(
                fixture_date.year, fixture_date.month, fixture_date.day,
                tzinfo=timezone.utc,
            )

            # 检查是否已存在
            existing = conn.execute(
                "SELECT match_id, status FROM core.fact_match_schedule WHERE match_id = ?",
                [f"API_FOOTBALL_{match_id}"],
            ).fetchone()

            system_match_id = f"API_FOOTBALL_{match_id}"
            if existing:
                conn.execute(
                    """UPDATE core.fact_match_schedule
                       SET status = 'FINISHED', home_score = ?, away_score = ?,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE match_id = ?""",
                    [home_score, away_score, system_match_id],
                )
                matches_updated += 1
            else:
                conn.execute(
                    """INSERT INTO core.fact_match_schedule
                       (match_id, system_league_id, match_time,
                        home_team_id, away_team_id, status, home_score, away_score,
                        source_provider)
                       VALUES (?, ?, ?, ?, ?, 'FINISHED', ?, ?, 'API-Football')""",
                    [
                        system_match_id,
                        system_league_id,
                        match_time,
                        home_system_id,
                        away_system_id,
                        home_score,
                        away_score,
                    ],
                )
                matches_inserted += 1

            # 写入比赛统计
            stats_inserted += self._sink_fixture_stats(conn, fixture, system_match_id)

        return matches_updated, matches_inserted, stats_inserted, affected_team_ids

    def _resolve_team(self, conn: Any, provider_team_id: Any) -> str | None:
        """通过 provider_team_id 查找 system_team_id。"""
        if provider_team_id is None:
            return None
        row = conn.execute(
            """SELECT system_team_id
               FROM core.dim_provider_team_mapping
               WHERE provider_name = 'API-Football'
                 AND CAST(provider_team_id AS VARCHAR) = CAST(? AS VARCHAR)
               LIMIT 1""",
            [str(provider_team_id)],
        ).fetchone()
        return str(row[0]) if row else None

    def _sink_fixture_stats(
        self,
        conn: Any,
        fixture: dict[str, Any],
        system_match_id: str,
    ) -> int:
        """将比赛统计写入 fact_match_events_unified（简化版：只用射门/xG 数据）。"""
        fixture_id = fixture["fixture"]["id"]
        stats_payload = self._fetch_stats(fixture_id)
        if not stats_payload:
            return 0

        inserted = 0
        for team_stats in stats_payload:
            team_id_provider = team_stats.get("team", {}).get("id")
            team_system_id = self._resolve_team(conn, team_id_provider)
            if not team_system_id:
                continue

            for stat_entry in team_stats.get("statistics", []):
                stat_type = stat_entry.get("type", "")
                value = stat_entry.get("value")
                if value is None or stat_type not in self._SINK_STAT_TYPES:
                    continue
                event_id = f"STAT_{fixture_id}_{team_id_provider}_{stat_type}"
                try:
                    numeric_val = float(str(value).replace("%", ""))
                except (ValueError, TypeError):
                    continue

                inserted_row = conn.execute(
                    """INSERT OR IGNORE INTO core.fact_match_events_unified
                       (event_id, match_id, team_id, timestamp_ms, period,
                        event_type, event_value, source_provider)
                       VALUES (?, ?, ?, 0, 0, ?, ?, 'API-Football')
                       RETURNING event_id""",
                    [event_id, system_match_id, team_system_id, stat_type, numeric_val],
                ).fetchone()
                if inserted_row:
                    inserted += 1
        return inserted

    _SINK_STAT_TYPES: set[str] = {
        "Total Shots",
        "Shots on Goal",
        "Shots off Goal",
        "Blocked Shots",
        "Ball Possession",
        "Expected Goals",
        "Total passes",
        "Passes accurate",
        "Fouls",
        "Yellow Cards",
        "Red Cards",
        "Corner Kicks",
    }

    def _fetch_stats(self, fixture_id: Any) -> list[dict[str, Any]]:
        """获取某场比赛的统计数据。"""
        url = f"{self._base_url}/fixtures/statistics?fixture={fixture_id}"
        req = urllib.request.Request(
            url, headers={"x-apisports-key": self._api_token}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            return []
        if not isinstance(data, dict) or "response" not in data:
            return []
        return data.get("response", []) or []

    @staticmethod
    def _parse_match_time(value: str) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None
