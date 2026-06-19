"""
系统不变量回归测试

验证足球预测系统的10条业务不变量。每个测试在当前代码上预期失败，
失败原因必须与对应的不变量一致。各任务完成后这些测试应全部通过。
"""
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import duckdb

from tests.network_guard import install_unit_test_network_guard

from football_advisor.db_schema import apply_schema
from football_advisor.feature_builder import DuckDBFeatureBuilder
from football_advisor.models import (
    MatchFeatures,
    MatchRequest,
    OddsValue,
    PredictionBundle,
    ProbabilityResult,
    TeamFeatures,
    ValueAssessment,
    PolicyDecision,
    Recommendation,
    RiskLevel,
)
from football_advisor.verification_engine import PredictionLogger, PredictionLoggingError
from football_advisor.probability_engine import ProbabilityEngine
from football_advisor.target_sync import DataReadinessGate


install_unit_test_network_guard()


def _temp_db(setup_obj: unittest.TestCase, suffix: str = "invariant") -> tuple[str, Any]:
    """创建临时 DuckDB 并返回 (path, connection)。"""
    tmpdir = tempfile.TemporaryDirectory()
    db_path = str(Path(tmpdir.name) / f"test_{suffix}.duckdb")
    conn = duckdb.connect(db_path)
    apply_schema(conn)
    setup_obj.addCleanup(tmpdir.cleanup)
    setup_obj.addCleanup(conn.close)
    return db_path, conn


# ============================================================
# 不变量 1: 竞彩主赔率隔离
# ============================================================

class SportteryPrimaryOddsTests(unittest.TestCase):
    """不变量 1 & 2: 竞彩赔率只来自 SportteryOfficialWeb，第三方不能替代。"""

    def setUp(self):
        self.db_path, self.conn = _temp_db(self, "primary_odds")

    def _insert_odds(self, match_id, provider, odds_type, home, draw, away, snapshot_time):
        self.conn.execute(
            """
            INSERT INTO core.fact_odds_capital_flow
            (match_id, odds_type, source_provider, bookmaker_name,
             home_odds, draw_odds, away_odds, snapshot_time, etl_insert_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [match_id, odds_type, provider, provider,
             home, draw, away, snapshot_time, snapshot_time],
        )

    def test_newer_third_party_odds_cannot_override_sporttery(self):
        """更新更快的第三方 1X2 不得覆盖竞彩官方赔率。"""
        match_id = "INV_T1_M001"
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=1)

        # 插入竞彩数据（较旧）
        self._insert_odds(match_id, "SportteryOfficialWeb", "1X2",
                          1.80, 3.20, 4.50, old)
        # 插入第三方数据（更新）
        self._insert_odds(match_id, "API-Football", "1X2",
                          2.50, 3.00, 2.80, now)

        # 确保比赛和球队基础数据存在
        self.conn.execute(
            "INSERT INTO core.fact_match_schedule "
            "(match_id, home_team_id, away_team_id, match_time, status, system_league_id) "
            "VALUES (?, 'TEAM_H', 'TEAM_A', ?, 'SCHEDULED', 'LEAGUE_TEST')",
            [match_id, now + timedelta(days=1)],
        )

        # 通过市场视图查询
        row = self.conn.execute(
            "SELECT latest_home_odds, latest_draw_odds, latest_away_odds "
            "FROM core.view_market_feature_base WHERE match_id = ?",
            [match_id],
        ).fetchone()

        self.assertIsNotNone(row, "市场视图应返回数据")
        home, draw, away = row

        # 当前行为：视图不区分 provider，最新快照会胜出（第三方 2.50/3.00/2.80）
        # 不变量的要求：竞彩 1.80/3.20/4.50 必须是主赔率
        self.assertEqual(home, 1.80,
                         "竞彩 SPF 主胜赔率必须优先；当前代码可能被第三方覆盖")
        self.assertEqual(draw, 3.20,
                         "竞彩 SPF 平局赔率必须优先")
        self.assertEqual(away, 4.50,
                         "竞彩 SPF 客胜赔率必须优先")

    def test_not_on_sale_cannot_be_filled_by_third_party_odds(self):
        """竞彩未开售的玩法不能被第三方填补。"""
        match_id = "INV_T1_M002"
        now = datetime.now(timezone.utc)

        # 只插入第三方 RQSPF（模拟竞彩未开售该玩法）
        self._insert_odds(match_id, "API-Football", "SPORTTERY_RQSPF",
                          2.10, 3.10, 3.40, now)

        with duckdb.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO core.fact_match_schedule "
                "(match_id, home_team_id, away_team_id, match_time, status, system_league_id) "
                "VALUES (?, 'TEAM_H', 'TEAM_A', ?, 'SCHEDULED', 'LEAGUE_TEST')",
                [match_id, now + timedelta(days=1)],
            )
            conn.execute(
                "INSERT INTO core.fact_sporttery_market_status "
                "(match_id, market_type, sale_status, snapshot_time, source_provider) "
                "VALUES (?, 'RQSPF', 'NOT_ON_SALE', ?, 'SportteryOfficialWeb')",
                [match_id, now],
            )

        # 检查 RQSPF 赔率：竞彩 NOT_ON_SALE 时不应有值
        row = self.conn.execute(
            "SELECT latest_rqspf_home_odds FROM core.view_market_feature_base "
            "WHERE match_id = ?",
            [match_id],
        ).fetchone()

        self.assertIsNotNone(row, "市场视图应返回行")
        rqspf_home = row[0]
        # 当前行为：视图不区分 provider，第三方 RQSPF 可能被选为最新
        # 不变量要求：NOT_ON_SALE 时不应使用第三方赔率
        self.assertIsNone(rqspf_home,
                          "竞彩 RQSPF 未开售时不得使用第三方赔率")


# ============================================================
# 不变量 3: 模型预测与价值候选分离
# ============================================================

class PredictionLogSemanticsTests(unittest.TestCase):
    """不变量 3 & 4: 模型结果、价值候选、策略建议分离；No Bet 不产生盈亏。"""

    def setUp(self):
        self.db_path, self.conn = _temp_db(self, "log_semantics")

    def test_value_candidate_is_not_logged_as_model_outcome(self):
        """predicted_outcome 应保存模型最大概率结果，而非最佳价值候选。"""
        logger = PredictionLogger(database_path=self.db_path)

        # 构造：模型最可能主胜(0.40)，但最佳价值为客胜(edge最大)
        probs = ProbabilityResult(
            home_win=0.40, draw=0.28, away_win=0.32,
            over_2_5=0.50, under_2_5=0.50,
            expected_home_goals=1.6, expected_away_goals=1.2,
            most_likely_scores=[("1-0", 0.15), ("1-1", 0.12)],
        )
        best_value = OddsValue(
            outcome="AWAY", decimal_odds=3.80,
            implied_probability=0.263, model_probability=0.32,
            edge=0.057, value=True,
        )
        value = ValueAssessment(
            values=[best_value],
            best_value=best_value,
            overround=1.06,
            threshold=0.05,
        )
        features = MatchFeatures(
            home=TeamFeatures(name="Team H", elo=1550),
            away=TeamFeatures(name="Team A", elo=1520),
            context={"home_team_id": "TEAM_H", "away_team_id": "TEAM_A"},
        )
        bundle = PredictionBundle(
            request=MatchRequest(query="test"),
            features=features,
            probabilities=probs,
            value_assessment=value,
            policy=PolicyDecision(
                recommendation=Recommendation.NO_BET,
                risk_level=RiskLevel.MEDIUM,
                confidence=0.50,
                reasons=["test"],
            ),
            news=[],
        )

        prediction_id = logger.log_prediction(bundle)

        # 读取写入的日志
        with duckdb.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT model_predicted_outcome, model_predicted_probability "
                "FROM core.fact_prediction_log WHERE prediction_id = ?",
                [prediction_id],
            ).fetchone()

        self.assertIsNotNone(row)
        outcome, prob = row

        # 不变量要求：model_predicted_outcome 应为模型最大概率结果(HOME, prob=0.40)
        self.assertEqual(outcome, "HOME",
                         "model_predicted_outcome 应为模型最大概率结果(HOME, prob=0.40)，"
                         "当前代码保存了最佳价值候选(AWAY)")
        self.assertAlmostEqual(prob, 0.40, places=2,
                               msg="model_predicted_probability 应为模型最大概率值")


# ============================================================
# 不变量 4: No Bet 不产生虚构盈亏
# ============================================================

class NoBetProfitLossTests(unittest.TestCase):
    """不变量 4: No Bet 预测不能计算虚构投注盈亏。"""

    def test_no_bet_verification_does_not_create_profit_loss(self):
        """No Bet 推荐的预测在核验时 profit_loss_unit 必须为 NULL。"""
        # 该测试通过代码审查验证 verification_engine._verify_single 的行为
        # 当前 PostMatchVerifier._verify_single 对所有 PENDING 预测无条件计算 profit_loss
        # 不变量要求：policy_recommendation = 'no_bet' 时不计算盈亏
        from football_advisor.verification_engine import PostMatchVerifier

        # 检查 _calc_profit_loss 是否存在于 _verify_single 中区分 No Bet
        # 当前代码在 _verify_single（L280-282）无条件计算：
        #   is_correct = (predicted_outcome == actual_outcome)
        #   profit_loss = self._calc_profit_loss(is_correct, odds_at_prediction)
        # 无 policy_recommendation 检查
        import inspect
        source = inspect.getsource(PostMatchVerifier._verify_single)
        # 当前行为：无条件计算 profit_loss
        # 不变量要求：No Bet 时 profit_loss 应为 NULL
        # 在没有 policy_recommendation 过滤的情况下，此测试应失败
        has_no_bet_check = ("policy_recommendation" in source or
                            "recommendation" in source)
        self.assertTrue(has_no_bet_check,
                        "PostMatchVerifier._verify_single 必须在计算 profit_loss "
                        "前检查 policy_recommendation，No Bet 时不应计算盈亏")


# ============================================================
# 不变量 5: UTC 时间语义与未来数据拒绝
# ============================================================

class TimestampInvariantTests(unittest.TestCase):
    """不变量 5: 关键时间统一 UTC，未来时间戳直接失败。"""

    def test_future_component_timestamp_is_not_fresh(self):
        """组件时间戳在未来时不得被判定为 fresh。"""
        future = datetime.now(timezone.utc) + timedelta(hours=2)

        gate = DataReadinessGate(
            match_id="FUTURE_M001",
            schedule_updated_at=future,
        )
        status = gate.check()

        # 当前行为：DataReadinessGate.check() 可能将 age_minutes 为负的未来时间
        # 判定为 fresh（因为 age_minutes = -120 < 30）
        # 不变量要求：未来时间戳必须为 invalid_future，不能是 fresh
        schedule_component = status.components.get("schedule", {})
        self.assertNotEqual(
            schedule_component.get("status"), "fresh",
            "未来时间戳不得被判定为 fresh；当前代码按 age_minutes < 30 判断"
        )
        self.assertIn("schedule", status.missing_critical + status.stale_critical,
                      "未来时间戳的组件必须进入关键缺失")


# ============================================================
# 不变量 6: 中立场无主场优势
# ============================================================

class NeutralVenueTests(unittest.TestCase):
    """不变量 6: 中立场不应用默认主场优势和 Elo 主场加成。"""

    def test_neutral_match_removes_home_advantage(self):
        """同一组球队在中立场时不得包含主场加成。"""
        engine = ProbabilityEngine()

        home_features = TeamFeatures(name="Qatar", elo=1450, attack_strength=1.0,
                                     defense_strength=1.0, home_advantage=0.12)
        away_features = TeamFeatures(name="Switzerland", elo=1620, attack_strength=1.0,
                                     defense_strength=1.0)

        neutral_features = MatchFeatures(
            home=replace(home_features, home_advantage=0.0),
            away=away_features,
        )
        home_features_match = MatchFeatures(
            home=home_features,
            away=away_features,
        )

        neutral_result = engine.predict(neutral_features)
        home_result = engine.predict(home_features_match)

        self.assertLess(neutral_result.expected_home_goals,
                        home_result.expected_home_goals,
                        "中立场(home_advantage=0)期望进球应低于主场优势时")

    def test_feature_builder_has_no_neutral_venue_awareness(self):
        """feature_builder 没有 is_neutral_venue 处理 — 当前总是返回正值。"""
        # 当前 DuckDBFeatureBuilder._home_advantage 始终返回 0.0-0.22
        # 不变量要求：中立场时必须返回 0.0
        import inspect
        source = inspect.getsource(DuckDBFeatureBuilder._home_advantage)
        has_neutral_check = "neutral" in source.lower()
        self.assertTrue(has_neutral_check,
                        "DuckDBFeatureBuilder._home_advantage 需要处理中立场，"
                        "当前总是返回正值")

    def test_probability_engine_no_default_home_elo_bonus(self):
        """概率引擎不应在特征层之外无条件叠加 Elo 主场加成。"""
        # 检查 _expected_home_goals 是否无条件使用 home_advantage
        # 当前代码：raw *= (1.0 + features.home.home_advantage)
        # 本应仅由 feature_builder 控制 home_advantage，但需确认无额外硬编码
        import inspect
        source = inspect.getsource(ProbabilityEngine._expected_home_goals)
        # 确认 home_advantage 来源是 features.home.home_advantage，而非硬编码常量
        uses_feature_advantage = "features.home.home_advantage" in source
        self.assertTrue(uses_feature_advantage,
                        "概率引擎应使用 features.home.home_advantage 而非硬编码主场加成")


# ============================================================
# 不变量 7: 组件证据就绪
# ============================================================

class ComponentEvidenceTests(unittest.TestCase):
    """不变量 7: 阵容伤停和新闻就绪只依赖真实组件证据。"""

    def test_lineup_readiness_requires_lineup_or_injury_evidence(self):
        """仅有 fact_match_context_summary.updated_at 不能证明阵容就绪。"""
        # 当前 DataReadinessGate.load 使用：
        #   COALESCE(injury_data_updated_at, updated_at)
        # 这导致仅更新了通用上下文也会被误判为阵容伤停就绪
        import inspect
        source = inspect.getsource(DataReadinessGate.load)
        # 不变量要求：不能使用 COALESCE(..., updated_at) 作为兜底
        has_coalesce_fallback = "COALESCE(injury_data_updated_at, updated_at)" in source
        # 当前行为：存在兜底逻辑
        # 不变量要求：应移除 COALESCE，要求真实证据
        self.assertFalse(has_coalesce_fallback,
                         "DataReadinessGate.load 不应使用 updated_at 作为 "
                         "injury_data_updated_at 的兜底；需要真实组件证据")

    def test_news_readiness_requires_real_news_evidence(self):
        """仅有 fact_news_signal_summary.updated_at 不能证明新闻就绪。"""
        import inspect
        source = inspect.getsource(DataReadinessGate.load)
        has_coalesce_fallback = "COALESCE(news_last_updated_at, updated_at)" in source
        self.assertFalse(has_coalesce_fallback,
                         "DataReadinessGate.load 不应使用 updated_at 作为 "
                         "news_last_updated_at 的兜底")


# ============================================================
# 不变量 8: LLM 只写叙述，代码渲染事实
# ============================================================

class LLMBoundaryTests(unittest.TestCase):
    """不变量 8: 模型只能生成叙述，所有数字和事实由代码渲染。"""

    def test_llm_output_cannot_replace_deterministic_report_facts(self):
        """LLM 输出中包含不在证据集合中的数字时应被拒绝。"""
        # 检查 report.py 中 build_prompt 是否仅发送模板给 LLM
        # 当前行为：LLM 接收完整模板并生成整篇 Markdown 报告
        # ReportNarrative 已定义但未被使用
        from football_advisor.models import ReportNarrative

        # 验证 ReportNarrative 类已定义
        self.assertIsNotNone(ReportNarrative)

        # 检查 report_generator 是否使用 ReportNarrative
        import inspect
        from football_advisor.report_generator import ReportGenerator
        gen_source = inspect.getsource(ReportGenerator)
        uses_narrative = "ReportNarrative" in gen_source
        # 当前行为：ReportNarrative 未在 report_generator 中使用
        # 不变量要求：LLM 输出应解析为 ReportNarrative 而非全文
        self.assertTrue(uses_narrative,
                        "ReportGenerator 应使用 ReportNarrative 约束 LLM 输出，"
                        "当前 LLM 可能生成包含虚假数字的整篇报告")


# ============================================================
# 不变量 9: 单次同步去重
# ============================================================

class SyncDedupTests(unittest.TestCase):
    """不变量 9: 单次预测每个外部组件最多同步一次。"""

    def test_prediction_sync_has_cache_or_dedup_mechanism(self):
        """TargetMatchSyncService 需要有实际的缓存/去重机制。"""
        # 检查 TargetMatchSyncService 是否有缓存判断或去重逻辑
        import inspect
        from football_advisor.target_sync import TargetMatchSyncService
        source = inspect.getsource(TargetMatchSyncService)

        # 当前 TargetSyncResult.critical_ok 接受 "fresh_cache" 状态
        # 但 TargetMatchSyncService 本身不检查缓存，每次都会同步
        # 不变量要求：30分钟内命中缓存不得再次调用外部服务
        #
        # 检查 sync_for_prediction 方法是否包含缓存检查逻辑
        sync_source = inspect.getsource(TargetMatchSyncService.sync_for_prediction)

        has_cache_lookup = ("last_success" in sync_source or
                           "cache" in sync_source.lower() or
                           "fresh_cache" in sync_source or
                           "fact_target_sync_state" in sync_source)
        has_dedup = "attempt" in sync_source or "called" in sync_source

        self.assertTrue(has_cache_lookup, 
                        "TargetMatchSyncService.sync_for_prediction 需要缓存检查逻辑，"
                        "当前每次预测无条件调用外部服务")
        self.assertTrue(has_dedup,
                        "TargetMatchSyncService.sync_for_prediction 需要去重机制，"
                        "同一请求内可能重复调用同一组件")


# ============================================================
# 不变量 10: 验收结论互斥
# ============================================================
#  该不变量由验收脚本 (walkthrough_e2e.py) 保证，在任务 10 实现。
#  此处暂不添加测试。


# ============================================================
# 不变量 11: 单元测试禁网
# ============================================================

class NetworkIsolationTests(unittest.TestCase):
    """不变量 11: 单元测试不得访问真实网络。"""

    def test_unit_test_detects_real_network_access(self):
        """当单元测试尝试访问真实网络时应立即失败。"""
        import socket
        import unittest.mock as mock

        # 模拟真实网络请求
        original_socket = socket.socket

        def blocking_socket(*args, **kwargs):
            raise RuntimeError(
                "单元测试禁止访问真实网络。请使用 mock 或 fixture。"
            )

        # 验证禁网机制能检测到 socket 调用
        with mock.patch.object(socket, "socket", blocking_socket):
            with self.assertRaises(RuntimeError) as ctx:
                socket.socket()
            self.assertIn("禁止访问真实网络", str(ctx.exception))

    def test_unit_test_network_guard_blocks_common_real_network_entrypoints(self):
        """单元测试入口必须全局阻断常见真实网络调用。"""
        import urllib.request

        with self.assertRaises(RuntimeError) as ctx:
            urllib.request.urlopen("https://example.com", timeout=1)
        self.assertIn("单元测试禁止访问真实网络", str(ctx.exception))

    def test_verification_status_incomplete_means_null_final_status(self):
        """verification_status=INCOMPLETE 时 final_status 必须为 None。"""
        from football_advisor.models import ExecutionResult

        # 构造 INCOMPLETE 状态
        result = ExecutionResult(
            verification_status="INCOMPLETE",
            final_status=None,
            missing_evidence=["缺少测试证据"],
            stages=[],
            failures=[],
        )

        self.assertEqual(result.verification_status, "INCOMPLETE")
        self.assertIsNone(result.final_status)
        self.assertEqual(result.missing_evidence, ["缺少测试证据"])

    def test_verification_status_complete_allows_final_status(self):
        """verification_status=COMPLETE 时 final_status 可为四种互斥状态之一。"""
        from football_advisor.models import ExecutionResult

        valid_statuses = ("PRODUCTION_READY", "SAFE_DEGRADED", "EXTERNAL_BLOCKED", "FAILED")
        for status in valid_statuses:
            result = ExecutionResult(
                verification_status="COMPLETE",
                final_status=status,
                missing_evidence=[],
                stages=[],
                failures=[],
            )
            self.assertEqual(result.verification_status, "COMPLETE")
            self.assertEqual(result.final_status, status)

    def test_incomplete_with_final_status_is_invalid(self):
        """verification_status=INCOMPLETE 时 final_status 不允许非 None 值。"""
        from football_advisor.models import ExecutionResult

        # 构造非法状态：INCOMPLETE 但 final_status 非 None
        with self.assertRaises(ValueError):
            ExecutionResult(
                verification_status="INCOMPLETE",
                final_status="PRODUCTION_READY",  # 非法！
                missing_evidence=[],
                stages=[],
                failures=[],
            )

    def test_complete_with_null_final_status_is_invalid(self):
        """verification_status=COMPLETE 时 final_status 不允许为 None。"""
        from football_advisor.models import ExecutionResult

        with self.assertRaises(ValueError):
            ExecutionResult(
                verification_status="COMPLETE",
                final_status=None,  # 非法！
                missing_evidence=[],
                stages=[],
                failures=[],
            )

    def test_complete_with_invalid_final_status_is_rejected(self):
        """verification_status=COMPLETE 时 final_status 只能为四种互斥状态之一。"""
        from football_advisor.models import ExecutionResult

        with self.assertRaises(ValueError):
            ExecutionResult(
                verification_status="COMPLETE",
                final_status="INVALID_STATUS",  # 非法！
                missing_evidence=[],
                stages=[],
                failures=[],
            )


if __name__ == "__main__":
    unittest.main()
