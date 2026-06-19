from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass(frozen=True)
class ResolvedMatch:
    system_match_id: str
    resolution_method: str  # "existing_mapping" | "mapped_teams_and_time"


class MatchResolutionError(RuntimeError):
    pass


class CanonicalMatchResolver:
    """将 provider 比赛解析为标准 match_id，确保不绕过映射表。"""

    TIME_WINDOW_HOURS = 12

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def resolve(
        self,
        provider_name: str,
        provider_match_id: str,
        provider_league_id: str,
        provider_home_team_id: str,
        provider_away_team_id: str,
        kickoff_time: datetime,
        persist_mapping: bool = True,
    ) -> ResolvedMatch:
        existing = self._connection.execute(
            """
            SELECT system_match_id
            FROM core.dim_provider_match_mapping
            WHERE provider_name = ? AND provider_match_id = ?
            """,
            [provider_name, provider_match_id],
        ).fetchone()
        if existing is not None:
            return ResolvedMatch(
                system_match_id=existing[0],
                resolution_method="existing_mapping",
            )

        league_row = self._connection.execute(
            """
            SELECT system_league_id
            FROM core.dim_provider_league_mapping
            WHERE provider_name = ? AND provider_league_id = ?
            """,
            [provider_name, provider_league_id],
        ).fetchone()
        if league_row is None:
            self._quarantine(
                provider_name=provider_name,
                provider_match_id=provider_match_id,
                provider_league_id=provider_league_id,
                provider_home_team_id=provider_home_team_id,
                provider_away_team_id=provider_away_team_id,
                match_time=kickoff_time,
                error_reason=f"未找到 provider 联赛映射: provider={provider_name}, "
                f"provider_league_id={provider_league_id}",
                component="league_mapping",
            )
            raise MatchResolutionError(
                f"未找到 provider 联赛映射: provider={provider_name}, "
                f"provider_league_id={provider_league_id}"
            )
        system_league_id = league_row[0]

        home_row = self._connection.execute(
            """
            SELECT system_team_id
            FROM core.dim_provider_team_mapping
            WHERE provider_name = ? AND provider_team_id = ?
            """,
            [provider_name, provider_home_team_id],
        ).fetchone()
        if home_row is None:
            self._quarantine(
                provider_name=provider_name,
                provider_match_id=provider_match_id,
                provider_league_id=provider_league_id,
                provider_home_team_id=provider_home_team_id,
                provider_away_team_id=provider_away_team_id,
                match_time=kickoff_time,
                error_reason=f"未找到 provider 主队映射: provider={provider_name}, "
                f"provider_home_team_id={provider_home_team_id}",
                component="team_mapping",
            )
            raise MatchResolutionError(
                f"未找到 provider 主队映射: provider={provider_name}, "
                f"provider_home_team_id={provider_home_team_id}"
            )
        system_home_team_id = home_row[0]

        away_row = self._connection.execute(
            """
            SELECT system_team_id
            FROM core.dim_provider_team_mapping
            WHERE provider_name = ? AND provider_team_id = ?
            """,
            [provider_name, provider_away_team_id],
        ).fetchone()
        if away_row is None:
            self._quarantine(
                provider_name=provider_name,
                provider_match_id=provider_match_id,
                provider_league_id=provider_league_id,
                provider_home_team_id=provider_home_team_id,
                provider_away_team_id=provider_away_team_id,
                match_time=kickoff_time,
                error_reason=f"未找到 provider 客队映射: provider={provider_name}, "
                f"provider_away_team_id={provider_away_team_id}",
                component="team_mapping",
            )
            raise MatchResolutionError(
                f"未找到 provider 客队映射: provider={provider_name}, "
                f"provider_away_team_id={provider_away_team_id}"
            )
        system_away_team_id = away_row[0]

        # 检查主客队是否解析为同一标准球队
        if system_home_team_id == system_away_team_id:
            self._quarantine(
                provider_name=provider_name,
                provider_match_id=provider_match_id,
                provider_league_id=provider_league_id,
                provider_home_team_id=provider_home_team_id,
                provider_away_team_id=provider_away_team_id,
                match_time=kickoff_time,
                error_reason=f"主客队解析为同一标准球队: system_team_id={system_home_team_id}, "
                f"home_provider_team_id={provider_home_team_id}, "
                f"away_provider_team_id={provider_away_team_id}",
                component="match_resolver",
            )
            raise MatchResolutionError(
                f"主客队解析为同一标准球队: system_team_id={system_home_team_id}, "
                f"home_provider_team_id={provider_home_team_id}, "
                f"away_provider_team_id={provider_away_team_id}"
            )

        window_start = kickoff_time - timedelta(hours=self.TIME_WINDOW_HOURS)
        window_end = kickoff_time + timedelta(hours=self.TIME_WINDOW_HOURS)

        matches = self._connection.execute(
            """
            SELECT match_id
            FROM core.fact_match_schedule
            WHERE system_league_id = ?
              AND home_team_id = ?
              AND away_team_id = ?
              AND match_time >= ?
              AND match_time <= ?
              AND match_id NOT LIKE 'M_ST_%'
            ORDER BY match_time ASC
            """,
            [
                system_league_id,
                system_home_team_id,
                system_away_team_id,
                window_start,
                window_end,
            ],
        ).fetchall()

        if len(matches) == 0:
            raise MatchResolutionError(
                f"未在标准赛程中找到匹配比赛: league={system_league_id}, "
                f"home={system_home_team_id}, away={system_away_team_id}, "
                f"kickoff={kickoff_time.isoformat()}"
            )
        if len(matches) > 1:
            match_ids = [m[0] for m in matches]
            raise MatchResolutionError(
                f"找到多个匹配比赛 ({len(matches)}): {match_ids}, "
                f"league={system_league_id}, home={system_home_team_id}, "
                f"away={system_away_team_id}"
            )

        system_match_id = matches[0][0]

        if persist_mapping:
            self._connection.execute(
                """
                INSERT INTO core.dim_provider_match_mapping (
                    provider_name, provider_match_id, system_match_id,
                    resolution_method, resolution_confidence, verified_at
                ) VALUES (?, ?, ?, 'mapped_teams_and_time', 1.0, ?)
                ON CONFLICT (provider_name, provider_match_id) DO NOTHING
                """,
                [
                    provider_name,
                    provider_match_id,
                    system_match_id,
                    datetime.now(timezone.utc),
                ],
            )

        return ResolvedMatch(
            system_match_id=system_match_id,
            resolution_method="mapped_teams_and_time",
        )

    def _quarantine(
        self,
        provider_name: str,
        provider_match_id: str,
        provider_league_id: str,
        provider_home_team_id: str,
        provider_away_team_id: str,
        match_time: datetime,
        error_reason: str,
        component: str,
    ) -> None:
        """将未映射记录写入隔离清单，不静默丢弃。"""
        self._connection.execute(
            """
            INSERT INTO core.quarantine_unmapped_records (
                provider_name, provider_match_id, provider_league_id,
                provider_home_team_id, provider_away_team_id,
                match_time, error_reason, component, quarantined_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            [
                provider_name,
                provider_match_id,
                provider_league_id,
                provider_home_team_id,
                provider_away_team_id,
                match_time,
                error_reason,
                component,
            ],
        )


def normalize_identifier(value: str) -> str:
    """标准化标识符：去除首尾空白、折叠内部空白、转大写、替换非字母数字字符为下划线。

    用于统一临时 match_id、team_id 等标识符，确保比较时格式一致。
    """
    import re

    if not value:
        return ""
    # 去除首尾空白
    value = value.strip()
    # 折叠内部空白
    value = re.sub(r"\s+", " ", value)
    # 转大写
    value = value.upper()
    # 替换非字母数字字符为下划线
    value = re.sub(r"[^A-Z0-9]", "_", value)
    # 折叠连续下划线
    value = re.sub(r"_+", "_", value)
    # 去除首尾下划线
    value = value.strip("_")
    return value
