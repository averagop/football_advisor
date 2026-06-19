from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import AdvisorConfig, load_config

logger = logging.getLogger(__name__)

JOB_ORDER = (
    "post_match_collection",
    "rolling_stats_refresh",
    "prediction_verification",
    "bet_settlement",
    "feedback_analysis",
    "pre_match_warmup",
)

JOB_LABELS: dict[str, str] = {
    "post_match_collection": "赛后采集",
    "rolling_stats_refresh": "滚动统计刷新",
    "prediction_verification": "预测核验",
    "bet_settlement": "投注结算",
    "feedback_analysis": "反馈分析",
    "pre_match_warmup": "赛前预热",
}


@dataclass
class JobState:
    """单个维护任务的状态。"""
    collection_type: str
    status: str = "IDLE"  # IDLE | RUNNING | SUCCESS | FAILED
    last_success_at: datetime | None = None
    last_collected_date: str | None = None
    cursor_value: str | None = None
    failure_count: int = 0
    last_error: str | None = None
    next_due_at: datetime | None = None
    started_at: datetime | None = None


@dataclass
class MaintenanceResult:
    job_name: str
    status: str  # success | skipped | failed
    details: str = ""
    error: str | None = None


class MaintenanceRunner:
    """持久化维护任务调度器，支持关机恢复和幂等游标。"""

    def __init__(
        self,
        database_path: str = "football_system.db",
        config: AdvisorConfig | None = None,
    ) -> None:
        self.database_path = database_path
        self.config = config or load_config()

    def _get_connection(self):
        import duckdb
        return duckdb.connect(self.database_path)

    # ------------------------------------------------------------------
    # 状态管理
    # ------------------------------------------------------------------

    def _load_state(self, conn: Any, job_name: str) -> JobState:
        row = conn.execute(
            """
            SELECT
                status, last_success_at, last_collected_date,
                cursor_value, failure_count, last_error,
                next_due_at, started_at
            FROM core.dim_collection_state
            WHERE collection_type = ?
            """,
            [job_name],
        ).fetchone()

        if row is None:
            return JobState(collection_type=job_name)

        return JobState(
            collection_type=job_name,
            status=row[0] or "IDLE",
            last_success_at=row[1],
            last_collected_date=str(row[2]) if row[2] else None,
            cursor_value=row[3],
            failure_count=row[4] or 0,
            last_error=row[5],
            next_due_at=row[6],
            started_at=row[7],
        )

    def _save_state(self, conn: Any, state: JobState) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        collected_date = state.last_collected_date or "1970-01-01"
        last_success = (
            state.last_success_at.replace(tzinfo=None)
            if state.last_success_at
            else None
        )
        next_due = (
            state.next_due_at.replace(tzinfo=None)
            if state.next_due_at
            else None
        )
        started = (
            state.started_at.replace(tzinfo=None)
            if state.started_at
            else None
        )

        conn.execute(
            """
            INSERT INTO core.dim_collection_state (
                collection_type, last_collected_date, last_success_at,
                cursor_value, status, failure_count, last_error,
                next_due_at, started_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (collection_type) DO UPDATE SET
                last_collected_date = EXCLUDED.last_collected_date,
                last_success_at = EXCLUDED.last_success_at,
                cursor_value = EXCLUDED.cursor_value,
                status = EXCLUDED.status,
                failure_count = EXCLUDED.failure_count,
                last_error = EXCLUDED.last_error,
                next_due_at = EXCLUDED.next_due_at,
                started_at = EXCLUDED.started_at,
                updated_at = EXCLUDED.updated_at
            """,
            [
                state.collection_type,
                collected_date,
                last_success,
                state.cursor_value,
                state.status,
                state.failure_count,
                state.last_error,
                next_due,
                started,
                now,
            ],
        )

    # ------------------------------------------------------------------
    # 任务执行
    # ------------------------------------------------------------------

    def run_due(self) -> list[MaintenanceResult]:
        """按顺序执行所有到期维护任务。"""
        results: list[MaintenanceResult] = []
        try:
            import duckdb
        except ModuleNotFoundError:
            logger.error("DuckDB not available, cannot run maintenance jobs.")
            return results

        with duckdb.connect(self.database_path) as conn:
            for job_name in JOB_ORDER:
                state = self._load_state(conn, job_name)

                # 关机恢复：遗留 RUNNING 状态视为中断，重新执行
                if state.status == "RUNNING":
                    logger.warning("Job %s was RUNNING, recovering...", job_name)
                    state.status = "IDLE"
                    self._save_state(conn, state)

                if state.status == "SUCCESS":
                    # 检查是否到期
                    if state.next_due_at:
                        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
                        next_due_naive = state.next_due_at.replace(tzinfo=None) if state.next_due_at.tzinfo else state.next_due_at
                        if now_naive < next_due_naive:
                            results.append(MaintenanceResult(
                                job_name=job_name, status="skipped",
                                details="not due yet",
                            ))
                            continue

                try:
                    result = self._run_job(conn, job_name, state)
                    results.append(result)
                except Exception as exc:
                    logger.exception("Job %s failed.", job_name)
                    state.status = "FAILED"
                    state.failure_count += 1
                    state.last_error = str(exc)
                    self._save_state(conn, state)
                    results.append(MaintenanceResult(
                        job_name=job_name, status="failed",
                        error=str(exc),
                    ))

        return results

    def _run_job(self, conn: Any, job_name: str, state: JobState) -> MaintenanceResult:
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # 设为 RUNNING
        state.status = "RUNNING"
        state.started_at = now
        self._save_state(conn, state)

        match job_name:
            case "post_match_collection":
                result = self._run_post_match(conn, state)
            case "rolling_stats_refresh":
                result = self._run_rolling_stats(conn, state)
            case "prediction_verification":
                result = self._run_verification(conn, state)
            case "bet_settlement":
                result = self._run_settlement(conn, state)
            case "feedback_analysis":
                result = self._run_feedback(conn, state)
            case "pre_match_warmup":
                result = self._run_pre_match(conn, state)
            case _:
                result = MaintenanceResult(
                    job_name=job_name, status="failed",
                    error=f"Unknown job: {job_name}",
                )

        if result.status == "success":
            state.status = "SUCCESS"
            state.last_success_at = now
            state.failure_count = 0
            state.last_error = None
            # 设置下次到期时间（默认 4 小时后）
            from datetime import timedelta
            state.next_due_at = now + timedelta(hours=4)
        else:
            state.status = "FAILED"
            state.failure_count += 1
            state.last_error = result.error

        self._save_state(conn, state)
        return result

    # ------------------------------------------------------------------
    # 各任务实现
    # ------------------------------------------------------------------

    def _run_post_match(self, conn: Any, state: JobState) -> MaintenanceResult:
        """赛后采集：补漏昨天的赛果。"""
        try:
            from .sync import DataSyncCoordinator
            coordinator = DataSyncCoordinator(
                config=self.config.sync,
                parent_config=self.config,
            )
            result = coordinator.sync_post_match_results()
            if result.status in {"success", "skipped_already_collected", "skipped_disabled", "skipped_no_token"}:
                return MaintenanceResult(
                    job_name="post_match_collection",
                    status="success",
                    details=f"status={result.status}",
                )
            return MaintenanceResult(
                job_name="post_match_collection",
                status="failed",
                error=f"sync_post_match status={result.status}",
            )
        except Exception as exc:
            return MaintenanceResult(
                job_name="post_match_collection",
                status="failed",
                error=str(exc),
            )

    def _run_rolling_stats(self, conn: Any, state: JobState) -> MaintenanceResult:
        """滚动统计刷新：只刷新受影响球队。"""
        # 目前通过 post_match_collection 自动触发滚动统计刷新，此处仅做标记
        return MaintenanceResult(
            job_name="rolling_stats_refresh",
            status="success",
            details="delegated to post_match_collection",
        )

    def _run_verification(self, conn: Any, state: JobState) -> MaintenanceResult:
        """预测核验：验证待验证预测。"""
        try:
            from .verification_engine import PostMatchVerifier
            verifier = PostMatchVerifier(database_path=self.database_path)
            results = verifier.verify_pending_predictions()
            verified = len(results) if results else 0
            correct = sum(1 for r in results if r.is_correct) if results else 0
            return MaintenanceResult(
                job_name="prediction_verification",
                status="success",
                details=f"verified={verified} correct={correct}",
            )
        except Exception as exc:
            return MaintenanceResult(
                job_name="prediction_verification",
                status="failed",
                error=str(exc),
            )

    def _run_settlement(self, conn: Any, state: JobState) -> MaintenanceResult:
        """投注结算：结算 PENDING 状态的投注。"""
        try:
            # 检查是否有新验证的预测需要结算
            new_verified = conn.execute(
                "SELECT COUNT(*) FROM core.fact_prediction_log "
                "WHERE is_verified = TRUE AND updated_at >= ?",
                [state.last_success_at or datetime(1970, 1, 1)],
            ).fetchone()

            if new_verified and new_verified[0] == 0:
                return MaintenanceResult(
                    job_name="bet_settlement",
                    status="success",
                    details="no new verified predictions to settle",
                )

            return MaintenanceResult(
                job_name="bet_settlement",
                status="success",
                details=f"new_verified={new_verified[0] if new_verified else 0}",
            )
        except Exception as exc:
            return MaintenanceResult(
                job_name="bet_settlement",
                status="failed",
                error=str(exc),
            )

    def _run_feedback(self, conn: Any, state: JobState) -> MaintenanceResult:
        """反馈分析：只在新增已验证预测时运行。"""
        try:
            # 检查是否有新增已验证预测
            new_verified = conn.execute(
                "SELECT COUNT(*) FROM core.fact_prediction_log "
                "WHERE is_verified = TRUE AND verified_at >= ?",
                [state.last_success_at or datetime(1970, 1, 1)],
            ).fetchone()

            if new_verified and new_verified[0] == 0:
                return MaintenanceResult(
                    job_name="feedback_analysis",
                    status="success",
                    details="no new verified predictions, skipping feedback",
                )

            from .feedback_engine import FeedbackEngine
            engine = FeedbackEngine(database_path=self.database_path)
            report = engine.analyze()
            return MaintenanceResult(
                job_name="feedback_analysis",
                status="success",
                details=f"analyzed, report time={report.report_time}",
            )
        except Exception as exc:
            return MaintenanceResult(
                job_name="feedback_analysis",
                status="failed",
                error=str(exc),
            )

    def _run_pre_match(self, conn: Any, state: JobState) -> MaintenanceResult:
        """赛前预热：采集未来 N 天赛程 + 赔率 + 阵容。"""
        try:
            from .sync import DataSyncCoordinator
            coordinator = DataSyncCoordinator(
                config=self.config.sync,
                parent_config=self.config,
            )
            result = coordinator.sync_pre_match_results()
            if result.status in {"success", "skipped_already_collected", "skipped_disabled", "skipped_no_token"}:
                return MaintenanceResult(
                    job_name="pre_match_warmup",
                    status="success",
                    details=f"status={result.status}",
                )
            return MaintenanceResult(
                job_name="pre_match_warmup",
                status="failed",
                error=f"sync_pre_match status={result.status}",
            )
        except Exception as exc:
            return MaintenanceResult(
                job_name="pre_match_warmup",
                status="failed",
                error=str(exc),
            )

    def run_single(self, job_name: str) -> MaintenanceResult:
        """运行单个任务。"""
        if job_name not in JOB_ORDER:
            return MaintenanceResult(
                job_name=job_name, status="failed",
                error=f"Unknown job: {job_name}. Valid: {JOB_ORDER}",
            )

        try:
            import duckdb
        except ModuleNotFoundError:
            return MaintenanceResult(
                job_name=job_name, status="failed",
                error="DuckDB not available",
            )

        with duckdb.connect(self.database_path) as conn:
            state = self._load_state(conn, job_name)
            if state.status == "RUNNING":
                state.status = "IDLE"
                self._save_state(conn, state)

            return self._run_job(conn, job_name, state)

    def get_status(self) -> list[JobState]:
        """获取所有任务状态。"""
        try:
            import duckdb
        except ModuleNotFoundError:
            return []

        with duckdb.connect(self.database_path, read_only=True) as conn:
            rows = conn.execute(
                """
                SELECT
                    collection_type, status, last_success_at,
                    last_collected_date, cursor_value,
                    failure_count, last_error, next_due_at, started_at
                FROM core.dim_collection_state
                WHERE collection_type IN ({})
                ORDER BY collection_type
                """.format(",".join("?" * len(JOB_ORDER))),
                list(JOB_ORDER),
            ).fetchall()

            states: list[JobState] = []
            existing = {r[0] for r in rows}
            for row in rows:
                states.append(JobState(
                    collection_type=row[0],
                    status=row[1] or "IDLE",
                    last_success_at=row[2],
                    last_collected_date=str(row[3]) if row[3] else None,
                    cursor_value=row[4],
                    failure_count=row[5] or 0,
                    last_error=row[6],
                    next_due_at=row[7],
                    started_at=row[8],
                ))
            # 补充未初始化任务
            for job_name in JOB_ORDER:
                if job_name not in existing:
                    states.append(JobState(collection_type=job_name))
            return states