"""赛前数据预采集模块。

每次预测前，拉取未来 N 天 TOP5+WC 联赛中所有即将开赛的比赛，
采集赛程、阵容和赔率快照，为赔率走势分析提供数据基础。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

_BEIJING_TZ = timezone(timedelta(hours=8))


def _beijing_today() -> date:
    """返回北京时间今天的日期。"""
    return datetime.now(_BEIJING_TZ).date()


@dataclass(frozen=True)
class PreMatchResult:
    status: str
    days_collected: int = 0
    matches_inserted: int = 0
    odds_snapshots: int = 0
    lineups_collected: int = 0
    recent_form_matches: int = 0
    h2h_matches: int = 0
    team_stats_collected: int = 0
    standings_updates: int = 0
    injuries_collected: int = 0
    error: str | None = None


class PreMatchCollector:
    """赛前数据预采集器。

    每次预测触发一次，拉取未来 N 天的赛程 + 赔率快照，
    写入 fact_match_schedule (PRE-MATCH) 和 staging.stg_odds_snapshot。
    """

    def __init__(
        self,
        api_token: str,
        base_url: str = "https://v3.football.api-sports.io",
        duckdb_path: str = "football_system.db",
        enabled: bool = True,
        lookahead_days: int = 7,
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
        self._lookahead_days = lookahead_days
        self._target_leagues = target_leagues

    def collect_if_needed(self) -> PreMatchResult:
        """拉取未来 N 天的赛前数据。每天第一次预测触发一次。"""
        if not self._enabled:
            return PreMatchResult(status="skipped_disabled")

        import duckdb

        try:
            with duckdb.connect(self._duckdb_path) as conn:
                return self._collect(conn)
        except Exception as exc:
            return PreMatchResult(status="error", error=str(exc))

    # ---- 内部实现 ----

    def _collect(self, conn: Any) -> PreMatchResult:
        today = _beijing_today()

        # 检查今天是否已采集
        last_date = self._get_last_collected_date(conn)
        if last_date is not None and last_date >= today:
            return PreMatchResult(status="skipped_already_collected")

        # 加载联赛映射
        league_map = self._load_league_map(conn)
        if not league_map:
            return PreMatchResult(
                status="error",
                error="No API-Football provider league mapping for target leagues",
            )

        # 逐日拉取赛程 + 8 维度数据
        total_matches = 0
        total_odds = 0
        total_lineups = 0
        total_recent = 0
        total_h2h = 0
        total_team_stats = 0
        total_standings = 0
        total_injuries = 0

        # 去重缓存：同一支球队在一次采集运行中只拉一次近期战绩/队统/伤停
        seen_recent_form: set[str] = set()
        seen_h2h_pairs: set[tuple[str, str]] = set()
        seen_team_stats: set[str] = set()
        seen_standings: set[str] = set()
        seen_injuries: set[tuple[str, str]] = set()

        for offset in range(self._lookahead_days + 1):
            search_date = today + timedelta(days=offset)
            fixtures = self._fetch_fixtures(search_date)

            for fix in fixtures:
                if not self._is_target_league(fix, league_map):
                    continue
                status_short = fix.get("fixture", {}).get("status", {}).get("short", "")
                if status_short in ("FT", "AET", "PEN"):
                    continue

                # 1. 写入赛程
                if self._upsert_schedule(conn, fix, league_map):
                    total_matches += 1

                # 2. 赔率快照
                odds_count = self._snapshot_odds(conn, fix, search_date)
                total_odds += odds_count

                # 3. 阵容（距开赛 3 天内）
                if offset <= 3:
                    lineup_count = self._collect_lineups(conn, fix)
                    total_lineups += lineup_count

                # 提取本场比赛的两支球队
                home_prov = fix.get("teams", {}).get("home", {}).get("id")
                away_prov = fix.get("teams", {}).get("away", {}).get("id")
                home_sys = self._resolve_team(conn, home_prov)
                away_sys = self._resolve_team(conn, away_prov)
                if home_sys and away_sys:
                    # 4. 球队近期战绩（每队每赛季仅拉一次）
                    for tid in (home_sys, away_sys):
                        if tid not in seen_recent_form:
                            seen_recent_form.add(tid)
                            total_recent += self._collect_recent_form(conn, tid)
                    # 5. H2H 对战历史（每对对仅拉一次）
                    pair = tuple(sorted([home_sys, away_sys]))
                    if pair not in seen_h2h_pairs:
                        seen_h2h_pairs.add(pair)
                        total_h2h += self._collect_h2h(conn, home_sys, away_sys)
                    # 6. 球队赛季统计（每队每赛季仅拉一次）
                    league_id = fix.get("league", {}).get("id")
                    season = fix.get("league", {}).get("season", 2026)
                    for tid in (home_sys, away_sys):
                        stat_key = f"{tid}_{season}"
                        if stat_key not in seen_team_stats:
                            seen_team_stats.add(stat_key)
                            total_team_stats += self._collect_team_stats(
                                conn, tid, league_id, season
                            )
                    # 7. 联赛积分榜（每个联赛仅拉一次）
                    league_sys_id = league_map.get(league_id) if isinstance(league_id, int) else None
                    if league_sys_id and league_sys_id not in seen_standings:
                        seen_standings.add(league_sys_id)
                        total_standings += self._collect_standings(
                            conn, league_id, league_sys_id, season
                        )
                    # 8. 伤停名单（每队距开赛 3 天内才拉）
                    if offset <= 3:
                        provider_match_id = str(fix.get("fixture", {}).get("id") or "")
                        for tid in (home_sys, away_sys):
                            injury_key = (tid, provider_match_id)
                            if provider_match_id and injury_key not in seen_injuries:
                                seen_injuries.add(injury_key)
                                total_injuries += self._collect_injuries(
                                    conn, tid, provider_match_id
                                )

        # 更新采集状态
        self._set_last_collected_date(conn, today)

        return PreMatchResult(
            status="success",
            days_collected=self._lookahead_days + 1,
            matches_inserted=total_matches,
            odds_snapshots=total_odds,
            lineups_collected=total_lineups,
            recent_form_matches=total_recent,
            h2h_matches=total_h2h,
            team_stats_collected=total_team_stats,
            standings_updates=total_standings,
            injuries_collected=total_injuries,
        )

    # ---- 赛程写入 ----

    def _upsert_schedule(
        self,
        conn: Any,
        fixture: dict[str, Any],
        league_map: dict[int, str],
    ) -> bool:
        """将赛前比赛写入 fact_match_schedule (status=PRE-MATCH)。"""
        fixture_data = fixture.get("fixture", {})
        match_id_raw = fixture_data.get("id")
        if not match_id_raw:
            return False

        system_match_id = f"API_FOOTBALL_{match_id_raw}"
        league_id = fixture.get("league", {}).get("id")
        system_league_id = league_map.get(league_id) if isinstance(league_id, int) else None
        if not system_league_id:
            return False

        home_system_id = self._resolve_team(conn, fixture.get("teams", {}).get("home", {}).get("id"))
        away_system_id = self._resolve_team(conn, fixture.get("teams", {}).get("away", {}).get("id"))
        if not home_system_id or not away_system_id:
            return False

        match_time_str = fixture_data.get("date", "")
        match_time = self._parse_match_time(match_time_str)

        existing = conn.execute(
            "SELECT match_id FROM core.fact_match_schedule WHERE match_id = ?",
            [system_match_id],
        ).fetchone()

        if existing:
            return False  # 已存在，不重复写入

        conn.execute(
            """INSERT INTO core.fact_match_schedule
               (match_id, system_league_id, match_time,
                home_team_id, away_team_id, status,
                source_provider)
               VALUES (?, ?, ?, ?, ?, 'PRE-MATCH', 'API-Football')""",
            [
                system_match_id,
                system_league_id,
                match_time,
                home_system_id,
                away_system_id,
            ],
        )
        return True

    # ---- 赔率快照 ----

    def _snapshot_odds(
        self,
        conn: Any,
        fixture: dict[str, Any],
        snapshot_date: date,
    ) -> int:
        """拉取赔率并写入 odds_snapshot 表。"""
        fixture_id = fixture.get("fixture", {}).get("id")
        if not fixture_id:
            return 0

        odds_payload = self._fetch_odds(fixture_id)
        if not odds_payload:
            return 0

        # 解析赔率：取第一个博彩公司的 1X2 赔率作为参考
        count = 0
        for item in odds_payload:
            for bookmaker in item.get("bookmakers", []):
                for bet in bookmaker.get("bets", []):
                    if str(bet.get("name", "")).casefold() != "match winner":
                        continue
                    prices: dict[str, float] = {}
                    for value in bet.get("values", []):
                        odd_str = str(value.get("value", "")).casefold()
                        odd_num = value.get("odd")
                        if odd_str in ("home", "draw", "away") and odd_num is not None:
                            try:
                                prices[odd_str] = float(odd_num)
                            except (ValueError, TypeError):
                                pass
                    if len(prices) < 3:
                        continue

                    system_match_id = f"API_FOOTBALL_{fixture_id}"
                    snapshot_id = f"ODDSNAP_{fixture_id}_{snapshot_date.isoformat()}"
                    conn.execute(
                        """INSERT OR IGNORE INTO staging.stg_odds_snapshot
                           (snapshot_id, match_id, provider_name, snapshot_date,
                            market_type, home_odds, draw_odds, away_odds)
                           VALUES (?, ?, 'API-Football', ?, '1X2', ?, ?, ?)""",
                        [
                            snapshot_id,
                            system_match_id,
                            snapshot_date,
                            round(prices.get("home", 0.0), 4),
                            round(prices.get("draw", 0.0), 4),
                            round(prices.get("away", 0.0), 4),
                        ],
                    )
                    count += 1
                    break  # 只取第一个博彩公司
                if count > 0:
                    break
            if count > 0:
                break
        return count

    # ---- 阵容采集 ----

    def _collect_lineups(self, conn: Any, fixture: dict[str, Any]) -> int:
        """拉取阵容（首发+替补），写入 staging.stg_lineups。"""
        fixture_id = fixture.get("fixture", {}).get("id")
        if not fixture_id:
            return 0

        url = f"{self._base_url}/fixtures/lineups?fixture={fixture_id}"
        req = urllib.request.Request(url, headers={"x-apisports-key": self._api_token})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            return 0

        response_data = data.get("response", []) if isinstance(data, dict) else []
        if not response_data:
            return 0

        count = 0
        for team_lineup in response_data:
            team_name = team_lineup.get("team", {}).get("name", "")
            formation = team_lineup.get("formation", "")
            for player in team_lineup.get("startXI", []):
                player_name = player.get("player", {}).get("name", "")
                if not player_name:
                    continue
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO staging.stg_lineups
                           (provider_match_id, player_name, team_name,
                            role, formation, source_provider)
                           VALUES (?, ?, ?, 'starter', ?, 'API-Football')""",
                        [str(fixture_id), player_name, team_name, formation],
                    )
                    count += 1
                except Exception:
                    pass
            for player in team_lineup.get("substitutes", []):
                player_name = player.get("player", {}).get("name", "")
                if not player_name:
                    continue
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO staging.stg_lineups
                           (provider_match_id, player_name, team_name,
                            role, formation, source_provider)
                           VALUES (?, ?, ?, 'substitute', ?, 'API-Football')""",
                        [str(fixture_id), player_name, team_name, formation],
                    )
                    count += 1
                except Exception:
                    pass
        return count

    # ---- 工具方法 ----

    def _fetch_fixtures(self, target_date: date) -> list[dict[str, Any]]:
        url = f"{self._base_url}/fixtures?date={target_date.isoformat()}"
        req = urllib.request.Request(url, headers={"x-apisports-key": self._api_token})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            return []
        if not isinstance(data, dict) or "response" not in data:
            return []
        return data.get("response", []) or []

    def _fetch_odds(self, fixture_id: Any) -> list[dict[str, Any]]:
        url = f"{self._base_url}/odds?fixture={fixture_id}"
        req = urllib.request.Request(url, headers={"x-apisports-key": self._api_token})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            return []
        if not isinstance(data, dict) or "response" not in data:
            return []
        return data.get("response", []) or []

    def _is_target_league(
        self, fixture: dict[str, Any], league_map: dict[int, str]
    ) -> bool:
        league_id = fixture.get("league", {}).get("id")
        return league_id in league_map if isinstance(league_id, int) else False

    def _load_league_map(self, conn: Any) -> dict[int, str]:
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

    def _resolve_team(self, conn: Any, provider_team_id: Any) -> str | None:
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

    def _get_last_collected_date(self, conn: Any) -> date | None:
        row = conn.execute(
            "SELECT last_collected_date FROM core.dim_collection_state "
            "WHERE collection_type = 'pre_match_results'"
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
            "VALUES ('pre_match_results', ?) "
            "ON CONFLICT (collection_type) DO UPDATE SET last_collected_date = ?",
            [collected_date, collected_date],
        )

    @staticmethod
    def _parse_match_time(value: str) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None

    # ---- 维度 4: 球队近期战绩 ----

    def _collect_recent_form(self, conn: Any, system_team_id: str) -> int:
        """拉取球队最近 10 场比赛，写入 fact_match_schedule。"""
        provider_team_id = self._get_provider_team_id(conn, system_team_id)
        if not provider_team_id:
            return 0

        url = f"{self._base_url}/fixtures?team={provider_team_id}&season={self._resolve_season(2026, None)}"
        # 注意: 免费层不支持 &last= 参数，也不能跳过 season 参数
        req = urllib.request.Request(url, headers={"x-apisports-key": self._api_token})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            return 0

        response_data = data.get("response", []) if isinstance(data, dict) else []
        count = 0
        for fix in response_data:
            match_id_raw = fix.get("fixture", {}).get("id")
            status_short = fix.get("fixture", {}).get("status", {}).get("short", "")
            if not match_id_raw or status_short not in ("FT", "AET", "PEN"):
                continue

            system_match_id = f"API_FOOTBALL_{match_id_raw}"
            existing = conn.execute(
                "SELECT match_id FROM core.fact_match_schedule WHERE match_id = ?",
                [system_match_id],
            ).fetchone()
            if existing:
                continue

            goals = fix.get("goals", {})
            home_score = goals.get("home")
            away_score = goals.get("away")
            if home_score is None or away_score is None:
                continue

            home_sys = self._resolve_team(conn, fix.get("teams", {}).get("home", {}).get("id"))
            away_sys = self._resolve_team(conn, fix.get("teams", {}).get("away", {}).get("id"))
            if not home_sys or not away_sys:
                continue

            match_time_str = fix.get("fixture", {}).get("date", "")
            match_time = self._parse_match_time(match_time_str)
            league_id = fix.get("league", {}).get("id")
            system_league_id = f"API_FB_LEAGUE_{league_id}" if league_id else None

            conn.execute(
                """INSERT INTO core.fact_match_schedule
                   (match_id, system_league_id, match_time,
                    home_team_id, away_team_id, status,
                    home_score, away_score, source_provider)
                   VALUES (?, ?, ?, ?, ?, 'FINISHED', ?, ?, 'API-Football-detail')""",
                [
                    system_match_id, system_league_id, match_time,
                    home_sys, away_sys, home_score, away_score,
                ],
            )
            count += 1
        return count

    # ---- 维度 5: H2H 对战历史 ----

    def _collect_h2h(self, conn: Any, home_sys: str, away_sys: str) -> int:
        """拉取两队历史交锋记录，写入 fact_match_schedule。"""
        home_prov = self._get_provider_team_id(conn, home_sys)
        away_prov = self._get_provider_team_id(conn, away_sys)
        if not home_prov or not away_prov:
            return 0

        url = f"{self._base_url}/fixtures/headtohead?h2h={home_prov}-{away_prov}"
        req = urllib.request.Request(url, headers={"x-apisports-key": self._api_token})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            return 0

        response_data = data.get("response", []) if isinstance(data, dict) else []
        count = 0
        for fix in response_data:
            match_id_raw = fix.get("fixture", {}).get("id")
            if not match_id_raw:
                continue

            system_match_id = f"API_FOOTBALL_{match_id_raw}"
            existing = conn.execute(
                "SELECT match_id FROM core.fact_match_schedule WHERE match_id = ?",
                [system_match_id],
            ).fetchone()
            if existing:
                continue

            goals = fix.get("goals", {})
            home_score = goals.get("home")
            away_score = goals.get("away")
            if home_score is None or away_score is None:
                continue

            fix_home_sys = self._resolve_team(conn, fix.get("teams", {}).get("home", {}).get("id"))
            fix_away_sys = self._resolve_team(conn, fix.get("teams", {}).get("away", {}).get("id"))
            if not fix_home_sys or not fix_away_sys:
                continue

            match_time_str = fix.get("fixture", {}).get("date", "")
            match_time = self._parse_match_time(match_time_str)

            conn.execute(
                """INSERT INTO core.fact_match_schedule
                   (match_id, system_league_id, match_time,
                    home_team_id, away_team_id, status,
                    home_score, away_score, source_provider)
                   VALUES (?, 'API_FB_LEAGUE_H2H', ?, ?, ?, 'FINISHED', ?, ?, 'API-Football-h2h')""",
                [
                    system_match_id, match_time,
                    fix_home_sys, fix_away_sys, home_score, away_score,
                ],
            )
            count += 1
        return count

    # ---- 维度 6: 球队赛季统计 ----

    def _collect_team_stats(
        self, conn: Any, system_team_id: str, league_id: Any, season: Any
    ) -> int:
        """拉取球队赛季统计数据，计算 attack/defense strength 写入 fact_team_rolling_stats。"""
        provider_team_id = self._get_provider_team_id(conn, system_team_id)
        if not provider_team_id or not league_id:
            return 0

        # 免费层最多支持到 2024 赛季，尝试请求赛季和回退
        actual_season = self._resolve_season(season, league_id)
        url = (
            f"{self._base_url}/teams/statistics"
            f"?team={provider_team_id}&league={league_id}&season={actual_season}"
        )
        req = urllib.request.Request(url, headers={"x-apisports-key": self._api_token})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            return 0

        response_data = data.get("response", {}) if isinstance(data, dict) else {}
        fixtures_data = response_data.get("fixtures", {})
        goals_data = response_data.get("goals", {})
        if not fixtures_data or not goals_data:
            return 0

        played = int(fixtures_data.get("played", {}).get("total", 0) or 0)
        wins = int(fixtures_data.get("wins", {}).get("total", 0) or 0)
        draws = int(fixtures_data.get("draws", {}).get("total", 0) or 0)
        loses = int(fixtures_data.get("loses", {}).get("total", 0) or 0)

        goals_for_total = int(goals_data.get("for", {}).get("total", {}).get("total", 0) or 0)
        goals_against_total = int(goals_data.get("against", {}).get("total", {}).get("total", 0) or 0)

        if played == 0:
            return 0

        avg_goals_for = goals_for_total / played
        avg_goals_against = goals_against_total / played
        points = wins * 3 + draws
        ppm = points / played
        attack = max(0.5, min(2.0, avg_goals_for / 1.4))
        defense = max(0.5, min(2.0, avg_goals_against / 1.1))

        stat_match_id = f"TEAM_STATS_{system_team_id}_{season}"
        now = datetime.now(timezone.utc)

        conn.execute(
            """INSERT INTO core.fact_team_rolling_stats
               (match_id, team_id, record_date, source_provider,
                rolling_goals_for, rolling_goals_against,
                recent_points_per_match, attack_strength, defense_strength,
                motivation_coefficient, key_missing_weight, data_quality_flag)
               VALUES (?, ?, ?, 'API_FOOTBALL_TEAM_STATS',
                ?, ?, ?, ?, ?, 0.5, 0.0, 'API_FOOTBALL_TEAM_STATS')
               ON CONFLICT (match_id, team_id, record_date, source_provider)
               DO UPDATE SET
                rolling_goals_for = excluded.rolling_goals_for,
                rolling_goals_against = excluded.rolling_goals_against,
                recent_points_per_match = excluded.recent_points_per_match,
                attack_strength = excluded.attack_strength,
                defense_strength = excluded.defense_strength,
                data_quality_flag = excluded.data_quality_flag""",
            [
                stat_match_id, system_team_id, now,
                round(avg_goals_for, 4), round(avg_goals_against, 4),
                round(ppm, 4), round(attack, 4), round(defense, 4),
            ],
        )
        return 1

    # ---- 维度 7: 联赛积分榜 ----

    def _collect_standings(
        self, conn: Any, provider_league_id: int, system_league_id: str, season: Any
    ) -> int:
        """拉取联赛积分榜，写入 core.fact_league_standings。"""
        actual_season = self._resolve_season(season, provider_league_id)
        url = f"{self._base_url}/standings?league={provider_league_id}&season={actual_season}"
        req = urllib.request.Request(url, headers={"x-apisports-key": self._api_token})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            return 0

        response_data = data.get("response", []) if isinstance(data, dict) else []
        count = 0
        for league_block in response_data:
            for standing in league_block.get("league", {}).get("standings", []):
                for team_entry in standing:
                    team_info = team_entry.get("team", {})
                    provider_team_id = team_info.get("id")
                    if not provider_team_id:
                        continue
                    system_team_id = self._resolve_team(conn, provider_team_id)
                    if not system_team_id:
                        continue

                    standing_id = f"STAND_{system_league_id}_{system_team_id}_{season}"
                    rank = team_entry.get("rank", 0)
                    all_stats = team_entry.get("all", {})
                    home_stats = team_entry.get("home", {})
                    away_stats = team_entry.get("away", {})
                    goals_diff = team_entry.get("goalsDiff", 0)

                    conn.execute(
                        """INSERT INTO core.fact_league_standings
                           (standing_id, system_league_id, system_team_id, season,
                            rank_position, played, wins, draws, loses,
                            goals_for, goals_against, goals_diff, points,
                            form, description,
                            home_played, home_wins, home_draws, home_loses,
                            home_goals_for, home_goals_against,
                            away_played, away_wins, away_draws, away_loses,
                            away_goals_for, away_goals_against)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                   ?, ?, ?, ?, ?, ?,
                                   ?, ?, ?, ?, ?, ?)
                           ON CONFLICT (system_league_id, system_team_id, season)
                           DO UPDATE SET
                            rank_position = excluded.rank_position,
                            played = excluded.played, wins = excluded.wins,
                            draws = excluded.draws, loses = excluded.loses,
                            goals_for = excluded.goals_for, goals_against = excluded.goals_against,
                            goals_diff = excluded.goals_diff, points = excluded.points,
                            form = excluded.form, description = excluded.description,
                            home_played = excluded.home_played, home_wins = excluded.home_wins,
                            home_draws = excluded.home_draws, home_loses = excluded.home_loses,
                            home_goals_for = excluded.home_goals_for, home_goals_against = excluded.home_goals_against,
                            away_played = excluded.away_played, away_wins = excluded.away_wins,
                            away_draws = excluded.away_draws, away_loses = excluded.away_loses,
                            away_goals_for = excluded.away_goals_for, away_goals_against = excluded.away_goals_against,
                            updated_at = CURRENT_TIMESTAMP""",
                        [
                            standing_id, system_league_id, system_team_id, int(season),
                            rank,
                            all_stats.get("played", 0), all_stats.get("win", 0),
                            all_stats.get("draw", 0), all_stats.get("lose", 0),
                            all_stats.get("goals", {}).get("for", 0),
                            all_stats.get("goals", {}).get("against", 0),
                            goals_diff, team_entry.get("points", 0),
                            team_entry.get("form", ""), team_entry.get("description", ""),
                            home_stats.get("played", 0), home_stats.get("win", 0),
                            home_stats.get("draw", 0), home_stats.get("lose", 0),
                            home_stats.get("goals", {}).get("for", 0),
                            home_stats.get("goals", {}).get("against", 0),
                            away_stats.get("played", 0), away_stats.get("win", 0),
                            away_stats.get("draw", 0), away_stats.get("lose", 0),
                            away_stats.get("goals", {}).get("for", 0),
                            away_stats.get("goals", {}).get("against", 0),
                        ],
                    )
                    count += 1
        return count

    # ---- 维度 8: 伤停名单 ----

    def _collect_injuries(
        self, conn: Any, system_team_id: str, provider_match_id: str
    ) -> int:
        """拉取球队当前伤停名单，写入 staging.stg_injuries。"""
        provider_team_id = self._get_provider_team_id(conn, system_team_id)
        if not provider_team_id:
            return 0

        url = f"{self._base_url}/injuries?fixture={provider_match_id}"
        req = urllib.request.Request(url, headers={"x-apisports-key": self._api_token})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            return 0

        players = data.get("response", []) if isinstance(data, dict) else []
        count = 0
        for injury in players:
            player_info = injury.get("player", {})
            player_name = player_info.get("name", "")
            team_info = injury.get("team", {})
            if str(team_info.get("id") or "") != provider_team_id:
                continue
            team_name = team_info.get("name", "")
            injury_type = injury.get("player", {}).get("type", "")
            reason = injury.get("player", {}).get("reason", "")

            if not player_name:
                continue

            try:
                conn.execute(
                    """INSERT OR IGNORE INTO staging.stg_injuries
                       (provider_match_id, player_name, team_name,
                        injury_type, reason, source_provider)
                       VALUES (?, ?, ?, ?, ?, 'API-Football')""",
                    [
                        provider_match_id,
                        player_name, team_name,
                        injury_type, reason,
                    ],
                )
                count += 1
            except Exception:
                pass
        return count

    # ---- 通用辅助 ----

    def _get_provider_team_id(self, conn: Any, system_team_id: str) -> str | None:
        """从 system_team_id 反查 API-Football 的 provider_team_id。"""
        row = conn.execute(
            """SELECT provider_team_id
               FROM core.dim_provider_team_mapping
               WHERE provider_name = 'API-Football'
                 AND system_team_id = ?
               LIMIT 1""",
            [system_team_id],
        ).fetchone()
        return str(row[0]) if row else None

    @staticmethod
    def _resolve_season(season: Any, league_id: Any) -> int:
        """免费层最多支持到 2024 赛季，超过 2024 自动回退。"""
        try:
            s = int(season)
        except (ValueError, TypeError):
            s = 2024
        return min(s, 2024)
