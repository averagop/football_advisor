from __future__ import annotations

import concurrent.futures
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import AdvisorConfig
from .models import DataReadinessStatus, MatchRequest
from .sync import DataSyncCoordinator, SyncResult
from .time_utils import utc_now, as_utc as _as_utc_standard, FutureTimestampError

logger = logging.getLogger(__name__)


def _run_sync_with_timeout(sync_fn, timeout: float = 120.0) -> SyncResult:
    """在线程池中执行同步函数，带超时保护。"""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(sync_fn)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError(f"Sync timed out after {timeout}s")

SPORTTERY_MARKETS = ("SPF", "RQSPF", "CRS", "TTG", "HAFU")
CACHE_TTL_MINUTES = 30

# 组件名称常量
COMP_SCHEDULE = "schedule"
COMP_SPORTTERY_ODDS = "sporttery_odds"
COMP_TEAM_STATS = "team_stats"
COMP_LINEUP_INJURY = "lineup_injury"
COMP_NEWS = "news"
COMP_CAPITAL_FLOW = "capital_flow"
COMP_WEATHER = "weather"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class TargetSyncResult:
    """目标比赛同步结果，区分关键和可选数据。"""
    match_id: str
    critical_results: tuple[SyncResult, ...]
    optional_results: tuple[SyncResult, ...]
    completed_at: datetime

    @property
    def critical_ok(self) -> bool:
        return all(
            result.status in {"success", "fresh_cache"}
            for result in self.critical_results
        )


@dataclass(frozen=True)
class DataReadinessGate:
    """按组件独立判断数据新鲜度，不再使用 LEAST() 混合时间戳。"""

    match_id: str
    schedule_updated_at: datetime | None = None
    sporttery_odds_updated_at: datetime | None = None
    team_stats_updated_at: datetime | None = None
    lineup_injury_updated_at: datetime | None = None
    news_updated_at: datetime | None = None
    # 可选组件
    capital_flow_updated_at: datetime | None = None
    weather_updated_at: datetime | None = None

    max_age_minutes: int = 30

    @classmethod
    def load(
        cls,
        connection: Any,
        match_id: str,
        max_age_minutes: int,
    ) -> DataReadinessGate:
        schedule = connection.execute(
            """
            SELECT updated_at, home_team_id, away_team_id,
                   weather_condition, temperature
            FROM core.fact_match_schedule
            WHERE match_id = ?
            """,
            [match_id],
        ).fetchone()
        schedule_updated_at = schedule[0] if schedule else None
        home_team_id = schedule[1] if schedule else None
        away_team_id = schedule[2] if schedule else None

        sporttery_row = connection.execute(
            """
            SELECT CASE WHEN COUNT(*) = 5 THEN MIN(latest_snapshot) ELSE NULL END
            FROM (
                SELECT market_type, MAX(snapshot_time) AS latest_snapshot
                FROM core.fact_sporttery_market_status
                WHERE match_id = ?
                  AND source_provider = 'SportteryOfficialWeb'
                  AND market_type IN ('SPF', 'RQSPF', 'CRS', 'TTG', 'HAFU')
                GROUP BY market_type
            ) latest_markets
            """,
            [match_id],
        ).fetchone()

        team_stats_updated_at = None
        if home_team_id and away_team_id:
            team_stats_row = connection.execute(
                """
                SELECT CASE WHEN COUNT(*) = 2 THEN MIN(latest_update) ELSE NULL END
                FROM (
                    SELECT team_id, MAX(etl_insert_timestamp) AS latest_update
                    FROM core.fact_team_rolling_stats
                    WHERE match_id = ?
                      AND team_id IN (?, ?)
                    GROUP BY team_id
                ) latest_team_stats
                """,
                [match_id, home_team_id, away_team_id],
            ).fetchone()
            team_stats_updated_at = team_stats_row[0] if team_stats_row else None

        lineup_injury_row = connection.execute(
            """
            SELECT MAX(COALESCE(source_updated_at, last_success_at))
            FROM core.fact_target_sync_state
            WHERE match_id = ?
              AND component = 'lineup_injury'
              AND status = 'success'
              AND (row_count > 0 OR empty_confirmed = TRUE)
            """,
            [match_id],
        ).fetchone()
        news_row = connection.execute(
            """
            SELECT news_last_updated_at
            FROM core.fact_news_signal_summary
            WHERE match_id = ?
              AND news_last_updated_at IS NOT NULL
            """,
            [match_id],
        ).fetchone()
        capital_row = connection.execute(
            """
            SELECT MAX(etl_insert_timestamp)
            FROM core.fact_odds_capital_flow
            WHERE match_id = ?
              AND (matched_volume IS NOT NULL OR sharp_money_ratio IS NOT NULL)
            """,
            [match_id],
        ).fetchone()
        weather_updated_at = (
            schedule_updated_at
            if schedule and (schedule[3] is not None or schedule[4] is not None)
            else None
        )

        return cls(
            match_id=match_id,
            schedule_updated_at=schedule_updated_at,
            sporttery_odds_updated_at=sporttery_row[0] if sporttery_row else None,
            team_stats_updated_at=team_stats_updated_at,
            lineup_injury_updated_at=lineup_injury_row[0] if lineup_injury_row else None,
            news_updated_at=news_row[0] if news_row else None,
            capital_flow_updated_at=capital_row[0] if capital_row else None,
            weather_updated_at=weather_updated_at,
            max_age_minutes=max_age_minutes,
        )

    def check(self) -> DataReadinessStatus:
        """返回每个组件的状态和总体就绪判断。"""
        now = utc_now()
        components: dict[str, dict[str, Any]] = {}

        for name, ts in [
            ("schedule", self.schedule_updated_at),
            ("sporttery_odds", self.sporttery_odds_updated_at),
            ("team_stats", self.team_stats_updated_at),
            ("lineup_injury", self.lineup_injury_updated_at),
            ("news", self.news_updated_at),
        ]:
            if ts is None:
                components[name] = {"status": "missing", "age_minutes": None}
            else:
                ts = _as_utc(ts)
                if ts > now:
                    components[name] = {
                        "status": "invalid_future",
                        "age_minutes": None,
                        "updated_at": ts.isoformat(),
                    }
                else:
                    age = (now - ts).total_seconds() / 60
                    components[name] = {
                        "status": "fresh" if age <= self.max_age_minutes else "stale",
                        "age_minutes": round(age, 1),
                        "updated_at": ts.isoformat(),
                    }

        for name, ts in [
            ("capital_flow", self.capital_flow_updated_at),
            ("weather", self.weather_updated_at),
        ]:
            if ts is None:
                components[name] = {"status": "missing_optional", "age_minutes": None}
            else:
                ts = _as_utc(ts)
                age = (now - ts).total_seconds() / 60
                components[name] = {
                    "status": "fresh_optional" if age <= self.max_age_minutes else "stale_optional",
                    "age_minutes": round(age, 1),
                }

        critical_ok = all(
            components[name]["status"] == "fresh"
            for name in ["schedule", "sporttery_odds", "team_stats", "lineup_injury", "news"]
        )

        missing_critical = [
            name
            for name in ["schedule", "sporttery_odds", "team_stats", "lineup_injury", "news"]
            if components[name]["status"] in ("missing", "invalid_future")
        ]
        stale_critical = [
            name
            for name in ["schedule", "sporttery_odds", "team_stats", "lineup_injury", "news"]
            if components[name]["status"] == "stale"
        ]

        return DataReadinessStatus(
            ready=critical_ok,
            components=components,
            missing_critical=missing_critical,
            stale_critical=stale_critical,
        )


class TargetMatchSyncService:
    """目标比赛关键数据同步服务，替代全局同步。"""

    def __init__(
        self,
        parent_config: AdvisorConfig,
        sync_coordinator: DataSyncCoordinator | None = None,
        connection: Any = None,
    ) -> None:
        self.parent_config = parent_config
        self._sync = sync_coordinator or DataSyncCoordinator(
            config=parent_config.sync,
            parent_config=parent_config,
        )
        self._resolver_cache: dict[str, str] = {}
        self._external_conn: Any = connection  # 外部传入的连接，用于缓存查询
        # 单次请求内已尝试的组件，防止重复同步
        self._attempted: dict[tuple[str, str, str], SyncResult] = {}

    def sync_for_prediction(self, request: MatchRequest) -> TargetSyncResult:
        """按固定顺序同步目标比赛关键数据，带 30 分钟缓存和去重。

        顺序：
        1. 标准比赛解析
        2. 竞彩五玩法（缓存检查 + 去重）
        3. 目标比赛结构化数据（缓存检查 + 去重）
        4. 目标比赛新闻（缓存检查 + 去重）
        5. 资金流（可选，缓存检查）
        """
        match_id = request.match_id or ""
        self._attempted.clear()
        critical: list[SyncResult] = []
        optional: list[SyncResult] = []

        # Step 1: 标准比赛解析
        if not match_id:
            match_id = self._sync._find_match_id(request) or ""
            if not match_id:
                return TargetSyncResult(
                    match_id="",
                    critical_results=(
                        SyncResult(
                            source="match_resolver",
                            updated_at=datetime.now(timezone.utc),
                            status="failed_no_match_id",
                            details={"reason": "Cannot resolve match_id"},
                        ),
                    ),
                    optional_results=(),
                    completed_at=datetime.now(timezone.utc),
                )

        # Step 2: 竞彩五玩法同步（带缓存）
        sporttery_result = self._sync_with_cache(
            match_id=match_id,
            component=COMP_SPORTTERY_ODDS,
            provider="SportteryOfficialWeb",
            sync_fn=lambda: self._sync_sporttery(match_id, request),
        )
        critical.append(sporttery_result)

        # Step 3: 目标比赛结构化数据（带缓存 + 排除 Sporttery 去重）
        exclude = {"SportteryOfficialWeb"}

        structured_result = self._sync_with_cache(
            match_id=match_id,
            component=COMP_TEAM_STATS,
            provider="multi_source",
            sync_fn=lambda: self._sync.sync_structured_data(
                request, exclude_providers=exclude
            ),
        )
        critical.append(structured_result)

        lineup_injury_result = self._record_lineup_injury_evidence(
            match_id,
            structured_result,
        )
        critical.append(lineup_injury_result)

        # Step 4: 目标比赛新闻（带缓存）
        news_result = self._sync_with_cache(
            match_id=match_id,
            component=COMP_NEWS,
            provider="news_provider",
            sync_fn=lambda: self._sync.sync_news_data(request),
        )
        critical.append(news_result)

        # Step 5: 资金流（可选，带缓存）
        capital_result = self._sync_with_cache(
            match_id=match_id,
            component=COMP_CAPITAL_FLOW,
            provider="exchange_provider",
            sync_fn=lambda: self._sync.sync_capital_flow_data(request),
        )
        optional.append(capital_result)

        return TargetSyncResult(
            match_id=match_id,
            critical_results=tuple(critical),
            optional_results=tuple(optional),
            completed_at=datetime.now(timezone.utc),
        )

    def _sync_with_cache(
        self,
        match_id: str,
        component: str,
        provider: str,
        sync_fn,
    ) -> SyncResult:
        """带缓存检查的同步包装器。

        逻辑：
        1. 检查缓存：若命中 fresh → 返回 fresh_cache
        2. 检查失败缓存：若 recent_failure 且未过 TTL → 返回 recent_failure
        3. 否则执行同步 → 写入缓存 → 返回结果
        """
        attempt_key = (match_id, component, provider)
        attempted_result = self._attempted.get(attempt_key)
        if attempted_result is not None:
            return attempted_result

        try:
            cache_status = self._check_component_cache(match_id, component, provider)
        except Exception as exc:
            logger.info(
                "Sync cache read failed for %s/%s/%s",
                match_id,
                component,
                provider,
            )
            result = SyncResult(
                source=component,
                updated_at=utc_now(),
                status="failed_cache_read",
                details={
                    "match_id": match_id,
                    "component": component,
                    "provider": provider,
                    "error_type": type(exc).__name__,
                },
            )
            self._attempted[attempt_key] = result
            return result

        if cache_status == "fresh":
            logger.info(
                "Cache hit for %s/%s/%s", match_id, component, provider
            )
            result = SyncResult(
                source=component,
                updated_at=utc_now(),
                status="fresh_cache",
                details={
                    "match_id": match_id,
                    "component": component,
                    "provider": provider,
                    "reason": "data_fresh_within_ttl",
                },
            )
            self._attempted[attempt_key] = result
            return result

        if cache_status == "recent_failure":
            logger.info(
                "Recent failure cache for %s/%s/%s, suppressing retry",
                match_id, component, provider,
            )
            result = SyncResult(
                source=component,
                updated_at=utc_now(),
                status="recent_failure",
                details={
                    "match_id": match_id,
                    "component": component,
                    "provider": provider,
                    "reason": "recent_failure_within_ttl",
                },
            )
            self._attempted[attempt_key] = result
            return result

        # 执行实际同步（带超时保护）
        try:
            upstream_result = _run_sync_with_timeout(sync_fn, timeout=120.0)
        except Exception as exc:
            logger.warning(
                "Sync timeout/error for %s/%s/%s: %s",
                match_id, component, provider, exc,
            )
            result = SyncResult(
                source=component,
                updated_at=utc_now(),
                status="failed_timeout",
                details={
                    "match_id": match_id,
                    "component": component,
                    "provider": provider,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:200],
                },
            )
            self._attempted[attempt_key] = result
            return result
        result = SyncResult(
            source=component,
            updated_at=upstream_result.updated_at,
            status=upstream_result.status,
            details=upstream_result.details,
        )

        # 写入缓存
        details = result.details if isinstance(result.details, dict) else {}
        row_count = self._result_row_count(details)
        empty_confirmed = result.status in {"empty_search", "empty_ingestion"}
        source_updated_at = result.updated_at if row_count > 0 or empty_confirmed else None
        if result.status in {"success", "fresh_cache"} and row_count == 0:
            try:
                row_count, source_updated_at = self._target_component_evidence(
                    match_id,
                    component,
                )
            except Exception as exc:
                result = SyncResult(
                    source=component,
                    updated_at=utc_now(),
                    status="failed_evidence_read",
                    details={
                        "match_id": match_id,
                        "component": component,
                        "provider": provider,
                        "error_type": type(exc).__name__,
                    },
                )
                self._attempted[attempt_key] = result
                return result
        cache_status = result.status
        error_code = str(details.get("error", ""))
        if result.status in {"success", "fresh_cache"} and row_count == 0 and not empty_confirmed:
            cache_status = "failed_no_rows"
            error_code = "zero_rows_without_confirmation"

        try:
            self._write_sync_state(
                match_id=match_id,
                component=component,
                provider=provider,
                status=cache_status,
                row_count=row_count,
                empty_confirmed=empty_confirmed,
                error_code=error_code,
                source_updated_at=source_updated_at,
            )
        except Exception as exc:
            logger.info(
                "Sync cache write failed for %s/%s/%s",
                match_id,
                component,
                provider,
            )
            result = SyncResult(
                source=component,
                updated_at=utc_now(),
                status="failed_cache_write",
                details={
                    "match_id": match_id,
                    "component": component,
                    "provider": provider,
                    "sync_status": result.status,
                    "error_type": type(exc).__name__,
                },
            )

        # 标记已尝试
        self._attempted[attempt_key] = result
        return result

    @staticmethod
    def _result_row_count(details: dict[str, Any]) -> int:
        value = details.get("row_count")
        if value is None:
            value = details.get("ingested_chunks", 0)
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def _target_component_evidence(
        self,
        match_id: str,
        component: str,
    ) -> tuple[int, datetime | None]:
        if component != COMP_TEAM_STATS:
            return 0, None
        with self._cache_connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*), MAX(etl_insert_timestamp)
                FROM core.fact_team_rolling_stats
                WHERE match_id = ?
                """,
                [match_id],
            ).fetchone()
        if row is None:
            return 0, None
        return int(row[0] or 0), row[1]

    def _record_lineup_injury_evidence(
        self,
        match_id: str,
        structured_result: SyncResult,
    ) -> SyncResult:
        provider = "structured_sources"
        try:
            cache_status = self._check_component_cache(
                match_id,
                COMP_LINEUP_INJURY,
                provider,
            )
        except Exception as exc:
            return SyncResult(
                source=COMP_LINEUP_INJURY,
                updated_at=utc_now(),
                status="failed_cache_read",
                details={"match_id": match_id, "error_type": type(exc).__name__},
            )
        if cache_status == "fresh":
            return SyncResult(
                source=COMP_LINEUP_INJURY,
                updated_at=utc_now(),
                status="fresh_cache",
                details={"match_id": match_id, "reason": "data_fresh_within_ttl"},
            )

        with self._cache_connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*), MAX(etl_insert_timestamp)
                FROM (
                    SELECT lineups.etl_insert_timestamp
                    FROM staging.stg_lineups lineups
                    INNER JOIN core.dim_provider_match_mapping mapping
                      ON mapping.provider_name = lineups.source_provider
                     AND mapping.provider_match_id = lineups.provider_match_id
                    WHERE mapping.system_match_id = ?
                    UNION ALL
                    SELECT injuries.etl_insert_timestamp
                    FROM staging.stg_injuries injuries
                    INNER JOIN core.dim_provider_match_mapping mapping
                      ON mapping.provider_name = injuries.source_provider
                     AND mapping.provider_match_id = injuries.provider_match_id
                    WHERE mapping.system_match_id = ?
                    UNION ALL
                    SELECT context.injury_data_updated_at AS etl_insert_timestamp
                    FROM core.fact_match_context_summary context
                    WHERE context.match_id = ?
                      AND context.source_provider IS NOT NULL
                      AND context.injury_data_updated_at IS NOT NULL
                      AND (
                          context.home_key_absences IS NOT NULL
                          OR context.away_key_absences IS NOT NULL
                          OR context.home_lineup_confirmed IS NOT NULL
                          OR context.away_lineup_confirmed IS NOT NULL
                      )
                ) evidence
                """,
                [match_id, match_id, match_id],
            ).fetchone()

        row_count = int(row[0] or 0) if row else 0
        source_updated_at = row[1] if row else None
        details = (
            structured_result.details
            if isinstance(structured_result.details, dict)
            else {}
        )
        explicit_empty = (
            structured_result.status in {"success", "fresh_cache"}
            and details.get("lineup_injury_empty_confirmed") is True
            and isinstance(details.get("lineup_injury_source_updated_at"), datetime)
        )
        if row_count > 0:
            status = "success"
            empty_confirmed = False
            error_code = ""
        elif explicit_empty:
            status = "success"
            empty_confirmed = True
            source_updated_at = details["lineup_injury_source_updated_at"]
            error_code = ""
        else:
            status = "failed_no_evidence"
            empty_confirmed = False
            source_updated_at = None
            error_code = "lineup_injury_evidence_missing"

        try:
            self._write_sync_state(
                match_id=match_id,
                component=COMP_LINEUP_INJURY,
                provider=provider,
                status=status,
                row_count=row_count,
                empty_confirmed=empty_confirmed,
                error_code=error_code,
                source_updated_at=source_updated_at,
            )
        except Exception as exc:
            logger.info("Lineup/injury evidence cache write failed for %s", match_id)
            return SyncResult(
                source=COMP_LINEUP_INJURY,
                updated_at=utc_now(),
                status="failed_cache_write",
                details={"match_id": match_id, "error_type": type(exc).__name__},
            )

        return SyncResult(
            source=COMP_LINEUP_INJURY,
            updated_at=_as_utc(source_updated_at) if source_updated_at else utc_now(),
            status=status,
            details={
                "match_id": match_id,
                "row_count": row_count,
                "empty_confirmed": empty_confirmed,
            },
        )

    @contextmanager
    def _cache_connection(self):
        """获取 DuckDB 连接。优先使用外部传入的连接。"""
        if self._external_conn is not None:
            yield self._external_conn
            return
        import duckdb

        connection = duckdb.connect(self.parent_config.duckdb.database_path)
        try:
            yield connection
        finally:
            connection.close()

    def _check_component_cache(
        self,
        match_id: str,
        component: str,
        provider: str,
    ) -> str:
        """检查组件缓存状态。

        Returns:
            "fresh" - 缓存新鲜，可直接使用
            "recent_failure" - 最近失败，不应重试
            "stale" - 缓存过期，需要重新同步
            "missing" - 无缓存记录
        """
        with self._cache_connection() as conn:
            row = conn.execute(
                """
                SELECT status, last_success_at, last_attempt_at, row_count,
                       empty_confirmed, source_updated_at
                FROM core.fact_target_sync_state
                WHERE match_id = ? AND component = ? AND provider = ?
                """,
                [match_id, component, provider],
            ).fetchone()

        if row is None:
            return "missing"

        (
            status,
            last_success_at,
            last_attempt_at,
            row_count,
            empty_confirmed,
            source_updated_at,
        ) = row
        now = utc_now()

        # 成功缓存：检查是否在 TTL 内
        if status == "success":
            if int(row_count or 0) <= 0 and not bool(empty_confirmed):
                return "stale"
            freshness_timestamp = source_updated_at or last_success_at
            if freshness_timestamp is not None:
                last_success = _as_utc(freshness_timestamp)
                age = (now - last_success).total_seconds() / 60
                if age <= CACHE_TTL_MINUTES:
                    return "fresh"
            return "stale"

        if empty_confirmed and last_attempt_at is not None:
            last_attempt = _as_utc(last_attempt_at)
            age = (now - last_attempt).total_seconds() / 60
            if age <= CACHE_TTL_MINUTES:
                return "recent_failure"

        # 失败缓存：检查是否在 TTL 内，抑制重试风暴
        if status.startswith("failed") or status == "recent_failure":
            if last_attempt_at is not None:
                last_attempt = _as_utc(last_attempt_at)
                age = (now - last_attempt).total_seconds() / 60
                if age <= CACHE_TTL_MINUTES:
                    return "recent_failure"
            return "stale"

        return "stale"

    def _write_sync_state(
        self,
        match_id: str,
        component: str,
        provider: str,
        status: str,
        row_count: int = 0,
        empty_confirmed: bool = False,
        error_code: str = "",
        source_updated_at: datetime | None = None,
    ) -> None:
        """写入组件同步状态到 fact_target_sync_state。"""
        now = utc_now()
        with self._cache_connection() as conn:
            existing = conn.execute(
                """
                SELECT 1
                FROM core.fact_target_sync_state
                WHERE match_id = ? AND component = ? AND provider = ?
                """,
                [match_id, component, provider],
            ).fetchone()

            is_success = status in {"success", "fresh_cache"}
            evidence_updated_at = source_updated_at if is_success else None
            if is_success and evidence_updated_at is None:
                evidence_updated_at = now

            if existing:
                conn.execute(
                    """
                    UPDATE core.fact_target_sync_state
                    SET last_attempt_at = ?,
                        source_updated_at = ?,
                        status = ?,
                        row_count = ?,
                        empty_confirmed = ?,
                        error_code = ?
                    WHERE match_id = ? AND component = ? AND provider = ?
                    """,
                    [
                        now,
                        evidence_updated_at,
                        status,
                        row_count,
                        empty_confirmed,
                        error_code if not is_success else "",
                        match_id,
                        component,
                        provider,
                    ],
                )
                if is_success:
                    conn.execute(
                        """
                        UPDATE core.fact_target_sync_state
                        SET last_success_at = ?
                        WHERE match_id = ? AND component = ? AND provider = ?
                        """,
                        [now, match_id, component, provider],
                    )
            else:
                conn.execute(
                    """
                    INSERT INTO core.fact_target_sync_state
                        (match_id, component, provider, last_attempt_at,
                         last_success_at, source_updated_at, status,
                         row_count, empty_confirmed, error_code)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        match_id,
                        component,
                        provider,
                        now,
                        now if is_success else None,
                        evidence_updated_at,
                        status,
                        row_count,
                        empty_confirmed,
                        error_code if not is_success else "",
                    ],
                )
    def _sync_sporttery(self, match_id: str, request: MatchRequest) -> SyncResult:
        """同步竞彩五玩法数据。"""
        try:
            from .sporttery_client import SportteryClient
        except ImportError:
            return SyncResult(
                source="sporttery",
                updated_at=datetime.now(timezone.utc),
                status="skipped_no_client",
                details={"reason": "SportteryClient not available"},
            )

        try:
            import duckdb
        except ModuleNotFoundError:
            return SyncResult(
                source="sporttery",
                updated_at=datetime.now(timezone.utc),
                status="skipped_no_duckdb",
            )

        try:
            client = SportteryClient(
                base_url=self.parent_config.sync.sporttery_base_url or "https://www.sporttery.cn",
            )
            with duckdb.connect(self.parent_config.duckdb.database_path) as conn:
                result = client.fetch_football_odds(conn)
                status = result.get("status", "unknown")
                details = {
                    "match_id": match_id,
                    "sporttery_status": status,
                    "matches_processed": result.get(
                        "matches_processed",
                        result.get("match_count", 0),
                    ),
                }
                if status != "success":
                    return SyncResult(
                        source="sporttery",
                        updated_at=datetime.now(timezone.utc),
                        status=status if status == "empty_after_retries" else "failed",
                        details=details,
                    )
                if result.get("match_count") == 0:
                    return self._sporttery_result(
                        "failed_target_not_in_feed",
                        details,
                    )
                return self._validate_target_sporttery(conn, match_id, details)
        except Exception as exc:
            logger.exception("Sporttery sync failed for match %s", match_id)
            return SyncResult(
                source="sporttery",
                updated_at=datetime.now(timezone.utc),
                status="failed",
                details={"match_id": match_id, "error": str(exc)},
            )

    def _validate_target_sporttery(
        self,
        connection: Any,
        match_id: str,
        details: dict[str, Any],
    ) -> SyncResult:
        mapping = connection.execute(
            """
            SELECT provider_match_id
            FROM core.dim_provider_match_mapping
            WHERE provider_name = 'SportteryOfficialWeb'
              AND system_match_id = ?
            """,
            [match_id],
        ).fetchone()
        if mapping is None:
            return self._sporttery_result(
                "failed_target_mapping_missing",
                details,
            )

        rows = connection.execute(
            """
            SELECT market_type, sale_status, snapshot_time
            FROM core.fact_sporttery_market_status
            WHERE match_id = ?
              AND source_provider = 'SportteryOfficialWeb'
            ORDER BY snapshot_time DESC
            """,
            [match_id],
        ).fetchall()
        latest: dict[str, tuple[str, datetime]] = {}
        for market_type, sale_status, snapshot_time in rows:
            if market_type in SPORTTERY_MARKETS and market_type not in latest:
                latest[market_type] = (sale_status, snapshot_time)

        missing_markets = sorted(set(SPORTTERY_MARKETS) - set(latest))
        details["provider_match_id"] = str(mapping[0])
        details["market_statuses"] = {
            market: latest[market][0]
            for market in SPORTTERY_MARKETS
            if market in latest
        }
        details["row_count"] = len(latest)
        if missing_markets:
            details["missing_market_statuses"] = missing_markets
            return self._sporttery_result(
                "failed_target_market_status_missing",
                details,
            )

        invalid_markets = sorted(
            market
            for market, (sale_status, _) in latest.items()
            if sale_status not in {"OPEN", "NOT_ON_SALE"}
        )
        if invalid_markets:
            details["invalid_market_statuses"] = invalid_markets
            return self._sporttery_result(
                "failed_target_market_status_invalid",
                details,
            )

        missing_open_market_odds = sorted(
            market
            for market, (sale_status, snapshot_time) in latest.items()
            if sale_status == "OPEN"
            and not self._has_target_market_odds(
                connection,
                match_id,
                market,
                snapshot_time,
            )
        )
        if missing_open_market_odds:
            details["missing_open_market_odds"] = missing_open_market_odds
            return self._sporttery_result(
                "failed_target_open_market_missing_odds",
                details,
            )

        return self._sporttery_result("success", details)

    @staticmethod
    def _has_target_market_odds(
        connection: Any,
        match_id: str,
        market_type: str,
        snapshot_time: datetime,
    ) -> bool:
        if market_type in {"SPF", "RQSPF"}:
            odds_type = "1X2" if market_type == "SPF" else "SPORTTERY_RQSPF"
            row = connection.execute(
                """
                SELECT 1
                FROM core.fact_odds_capital_flow
                WHERE match_id = ?
                  AND snapshot_time = ?
                  AND odds_type = ?
                  AND source_provider = 'SportteryOfficialWeb'
                  AND home_odds IS NOT NULL
                  AND draw_odds IS NOT NULL
                  AND away_odds IS NOT NULL
                LIMIT 1
                """,
                [match_id, snapshot_time, odds_type],
            ).fetchone()
            return row is not None

        odds_type = {
            "CRS": "CORRECT_SCORE",
            "TTG": "TOTAL_GOALS",
            "HAFU": "HALF_FULL",
        }[market_type]
        row = connection.execute(
            """
            SELECT 1
            FROM core.fact_sporttery_odds_detail
            WHERE match_id = ?
              AND snapshot_time = ?
              AND odds_type = ?
              AND source_provider = 'SportteryOfficialWeb'
            LIMIT 1
            """,
            [match_id, snapshot_time, odds_type],
        ).fetchone()
        return row is not None

    @staticmethod
    def _sporttery_result(status: str, details: dict[str, Any]) -> SyncResult:
        return SyncResult(
            source="sporttery",
            updated_at=datetime.now(timezone.utc),
            status=status,
            details=details,
        )
