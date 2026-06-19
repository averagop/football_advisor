"""赛后滚动统计刷新模块。

在赛后采集完成后，重新计算受影响球队的最近 N 场滚动统计，
确保预测引擎能引用最新数据。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RollingStatsRefresher:
    """赛后滚动统计刷新器。

    只刷新从 API-Football 同步的球队统计（source_provider='API-Football'），
    不触碰 SQLite 历史数据。
    """

    def __init__(
        self,
        window_size: int = 5,
        duckdb_path: str = "football_system.db",
    ) -> None:
        self._window_size = window_size
        self._duckdb_path = duckdb_path

    def refresh_teams(self, team_ids: list[str]) -> dict[str, Any]:
        """为指定的球队列表刷新滚动统计。

        Args:
            team_ids: 需要刷新的 system_team_id 列表。

        Returns:
            {"status": "success", "teams_refreshed": N, "rows_written": M}
        """
        if not team_ids:
            return {"status": "success", "teams_refreshed": 0, "rows_written": 0}

        import duckdb

        try:
            with duckdb.connect(self._duckdb_path) as conn:
                return self._refresh(conn, team_ids)
        except (duckdb.Error, OSError) as exc:
            logger.exception("Rolling stats refresh failed.")
            return {"status": "error", "error": str(exc)}

    def _refresh(
        self, conn: Any, team_ids: list[str]
    ) -> dict[str, Any]:
        """执行刷新：对每个球队取最近 N 场完赛数据，计算聚合指标。"""
        placeholders = ", ".join(["?"] * len(team_ids))
        finished = conn.execute(
            f"""
            SELECT s.match_id, s.match_time, s.home_team_id, s.away_team_id,
                   s.home_score, s.away_score
            FROM core.fact_match_schedule s
            WHERE s.status = 'FINISHED'
              AND s.home_score IS NOT NULL AND s.away_score IS NOT NULL
              AND (s.home_team_id IN ({placeholders}) OR s.away_team_id IN ({placeholders}))
            ORDER BY s.match_time DESC
            """,
            team_ids + team_ids,
        ).fetchall()

        if not finished:
            return {"status": "success", "teams_refreshed": 0, "rows_written": 0}

        # 按球队分组，取最近 N 场
        team_matches: dict[str, list[tuple[Any, ...]]] = {tid: [] for tid in team_ids}
        for row in finished:
            home_tid, away_tid = str(row[2]), str(row[3])
            for tid in (home_tid, away_tid):
                if tid in team_matches and len(team_matches[tid]) < self._window_size:
                    team_matches[tid].append(row)

        # 写入刷新后的滚动统计
        rows_written = 0
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        for tid, matches in team_matches.items():
            if not matches:
                continue

            # 计算聚合指标
            goals_for = 0.0
            goals_against = 0.0
            points = 0.0
            for row in matches:
                home_tid, away_tid = str(row[2]), str(row[3])
                home_score = row[4] or 0
                away_score = row[5] or 0
                if tid == home_tid:
                    goals_for += home_score
                    goals_against += away_score
                    if home_score > away_score:
                        points += 3
                    elif home_score == away_score:
                        points += 1
                else:
                    goals_for += away_score
                    goals_against += home_score
                    if away_score > home_score:
                        points += 3
                    elif home_score == away_score:
                        points += 1

            n = len(matches)
            avg_goals_for = goals_for / n
            avg_goals_against = goals_against / n
            ppm = points / n
            attack = max(0.5, min(2.0, avg_goals_for / 1.4))
            defense = max(0.5, min(2.0, avg_goals_against / 1.1))

            # 以最后一场比赛的 match_id 写入最新滚动统计
            latest_match = matches[0]
            match_id = str(latest_match[0])
            match_time = latest_match[1]

            conn.execute(
                """INSERT INTO core.fact_team_rolling_stats
                   (match_id, team_id, record_date, source_provider,
                    rolling_goals_for, rolling_goals_against,
                    recent_points_per_match, attack_strength, defense_strength,
                    motivation_coefficient, key_missing_weight, data_quality_flag)
                   VALUES (?, ?, ?, 'API_FOOTBALL_POST_MATCH',
                    ?, ?, ?, ?, ?, 0.5, 0.0, 'API_FOOTBALL_POST_MATCH')
                   ON CONFLICT (match_id, team_id, record_date, source_provider)
                   DO UPDATE SET
                    rolling_goals_for = excluded.rolling_goals_for,
                    rolling_goals_against = excluded.rolling_goals_against,
                    recent_points_per_match = excluded.recent_points_per_match,
                    attack_strength = excluded.attack_strength,
                    defense_strength = excluded.defense_strength,
                    data_quality_flag = excluded.data_quality_flag""",
                [
                    match_id, tid, match_time,
                    round(avg_goals_for, 4), round(avg_goals_against, 4),
                    round(ppm, 4), round(attack, 4), round(defense, 4),
                ],
            )
            rows_written += 1

        return {
            "status": "success",
            "teams_refreshed": sum(1 for m in team_matches.values() if m),
            "rows_written": rows_written,
        }
