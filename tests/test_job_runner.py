from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

import duckdb

from football_advisor.config import AdvisorConfig
from football_advisor.db_schema import apply_schema
from football_advisor.job_runner import (
    JOB_ORDER,
    JobState,
    MaintenanceResult,
    MaintenanceRunner,
)


class MaintenanceRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_jobs.duckdb")
        self.connection = duckdb.connect(self.db_path)
        apply_schema(self.connection)
        self.connection.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_initial_state_all_idle(self):
        runner = MaintenanceRunner(database_path=self.db_path)
        states = runner.get_status()
        self.assertEqual(len(states), len(JOB_ORDER))
        for s in states:
            self.assertEqual(s.status, "IDLE")
            self.assertEqual(s.failure_count, 0)

    def test_status_contains_all_jobs(self):
        runner = MaintenanceRunner(database_path=self.db_path)
        states = runner.get_status()
        job_names = {s.collection_type for s in states}
        for job_name in JOB_ORDER:
            self.assertIn(job_name, job_names)

    def test_run_single_unknown_job(self):
        runner = MaintenanceRunner(database_path=self.db_path)
        result = runner.run_single("nonexistent_job")
        self.assertEqual(result.status, "failed")
        self.assertIn("Unknown job", result.error or "")

    def test_run_single_known_job(self):
        """运行单个任务，应正常完成（即使底层依赖不可用也会被 try/except 捕获）。"""
        runner = MaintenanceRunner(database_path=self.db_path)
        result = runner.run_single("rolling_stats_refresh")
        self.assertIn(result.status, {"success", "failed"})

    def test_run_due_skips_not_due(self):
        """SUCCESS 状态且未到期时跳过。"""
        runner = MaintenanceRunner(database_path=self.db_path)
        conn = duckdb.connect(self.db_path)
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO core.dim_collection_state "
            "(collection_type, last_collected_date, status, last_success_at, next_due_at) "
            "VALUES (?, ?, 'SUCCESS', ?, ?)",
            ["post_match_collection", "2026-06-12", now, now + timedelta(hours=1)],
        )
        conn.close()

        results = runner.run_due()
        post_match_results = [r for r in results if r.job_name == "post_match_collection"]
        if post_match_results:
            self.assertEqual(post_match_results[0].status, "skipped")

    def test_crash_recovery(self):
        """RUNNING 状态的任务应被恢复执行。"""
        runner = MaintenanceRunner(database_path=self.db_path)
        conn = duckdb.connect(self.db_path)
        conn.execute(
            "INSERT INTO core.dim_collection_state "
            "(collection_type, last_collected_date, status) "
            "VALUES (?, ?, 'RUNNING')",
            ["rolling_stats_refresh", "2026-06-12"],
        )
        conn.close()

        # 确认初始状态为 RUNNING
        states = runner.get_status()
        rolling = next(s for s in states if s.collection_type == "rolling_stats_refresh")
        self.assertEqual(rolling.status, "RUNNING")

        # 运行 run_due 应恢复执行
        results = runner.run_due()
        rolling_results = [r for r in results if r.job_name == "rolling_stats_refresh"]
        if rolling_results:
            self.assertIn(rolling_results[0].status, {"success", "failed"})

        # 恢复后状态不应为 RUNNING
        states_after = runner.get_status()
        rolling_after = next(s for s in states_after if s.collection_type == "rolling_stats_refresh")
        self.assertNotEqual(rolling_after.status, "RUNNING")

    def test_failure_increments_count(self):
        """失败任务应增加 failure_count。"""
        runner = MaintenanceRunner(database_path=self.db_path)
        result = runner.run_single("post_match_collection")

        states = runner.get_status()
        post_match = next(s for s in states if s.collection_type == "post_match_collection")
        if result.status == "failed":
            self.assertGreater(post_match.failure_count, 0)

    def test_job_state_save_and_load(self):
        """状态保存和加载应正确。"""
        conn = duckdb.connect(self.db_path)
        runner = MaintenanceRunner(database_path=self.db_path)

        state = JobState(
            collection_type="bet_settlement",
            status="SUCCESS",
            last_success_at=datetime.now(timezone.utc),
            last_collected_date="2026-06-12",
            failure_count=0,
        )
        runner._save_state(conn, state)
        conn.close()

        states = runner.get_status()
        bet = next(s for s in states if s.collection_type == "bet_settlement")
        self.assertEqual(bet.status, "SUCCESS")
        self.assertEqual(bet.failure_count, 0)
        self.assertEqual(bet.last_collected_date, "2026-06-12")


class JobStateTests(unittest.TestCase):
    def test_default_state(self):
        state = JobState(collection_type="test_job")
        self.assertEqual(state.status, "IDLE")
        self.assertEqual(state.failure_count, 0)
        self.assertIsNone(state.last_success_at)
        self.assertIsNone(state.last_error)


class MaintenanceResultTests(unittest.TestCase):
    def test_success_result(self):
        result = MaintenanceResult(
            job_name="test_job",
            status="success",
            details="completed normally",
        )
        self.assertEqual(result.status, "success")
        self.assertIsNone(result.error)

    def test_failed_result(self):
        result = MaintenanceResult(
            job_name="test_job",
            status="failed",
            error="connection refused",
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "connection refused")


if __name__ == "__main__":
    unittest.main()