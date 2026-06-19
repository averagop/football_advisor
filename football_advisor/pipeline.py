from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import Any

from .config import AdvisorConfig, load_config
from .db_schema import initialize_database
from .feature_builder import DuckDBFeatureBuilder
from .feature_enhancers import FeatureEnhancerPipeline
from .llm_router import LLMRouter
from .models import (
    DataReadinessStatus,
    MatchFeatures,
    MatchRequest,
    PredictionBundle,
    ProbabilityResult,
    ValueAssessment,
)
from .news_tools import ChromaNewsSearchTool
from .no_bet_policy import NoBetPolicy
from .odds_value_engine import OddsValueEngine
from .prediction_diff import PredictionDiffAnalyzer
from .probability_engine import ProbabilityEngine
from .query_tools import DuckDBStatsQueryTool
from .report import ReportBuilder
from .report_generator import ReportGenerator
from .sync import DataSyncCoordinator
from .target_sync import DataReadinessGate, TargetSyncResult
from .verification_engine import PredictionLogger

logger = logging.getLogger(__name__)


class PredictionPipeline:
    def __init__(
        self,
        sync: DataSyncCoordinator | None = None,
        news_tool: ChromaNewsSearchTool | None = None,
        probability_engine: ProbabilityEngine | None = None,
        value_engine: OddsValueEngine | None = None,
        policy: NoBetPolicy | None = None,
        report_builder: ReportBuilder | None = None,
        llm_router: LLMRouter | None = None,
        feature_builder: DuckDBFeatureBuilder | None = None,
        prediction_logger: PredictionLogger | None = None,
        diff_analyzer: PredictionDiffAnalyzer | None = None,
        enhancer_pipeline: FeatureEnhancerPipeline | None = None,
        report_generator: ReportGenerator | None = None,
        config: AdvisorConfig | None = None,
    ) -> None:
        self.config = config or load_config()
        db_path = self.config.duckdb.database_path
        initialize_database(db_path)
        self.sync = sync or DataSyncCoordinator(
            config=self.config.sync,
            parent_config=self.config,
        )
        self.news_tool = news_tool or ChromaNewsSearchTool(config=self.config.chroma)
        probability_config = self.config.probability
        self.probability_engine = probability_engine or ProbabilityEngine(
            poisson_weight=probability_config.poisson_weight,
            model_version=probability_config.model_version,
            calibrated=probability_config.calibrated,
            enable_enhanced_boosts=probability_config.enable_enhanced_boosts,
        )
        self.value_engine = value_engine or OddsValueEngine()
        self.policy = policy or NoBetPolicy(
            calibrated=probability_config.calibrated,
            model_version=probability_config.model_version,
        )
        self.report_builder = report_builder or ReportBuilder()
        self.llm_router = llm_router or LLMRouter(config=self.config.llm)
        self.feature_builder = feature_builder or DuckDBFeatureBuilder(
            query_tool=DuckDBStatsQueryTool(config=self.config.duckdb),
            config=self.config.feature_builder,
        )
        # 赛后验证、反馈和投注账本只由后台维护任务构造。
        self.prediction_logger = prediction_logger or PredictionLogger(database_path=db_path)
        self.diff_analyzer = diff_analyzer or PredictionDiffAnalyzer(database_path=db_path)
        self.enhancer_pipeline = enhancer_pipeline or FeatureEnhancerPipeline(database_path=db_path)
        self.report_generator = report_generator or ReportGenerator(
            llm_router=self.llm_router,
            report_builder=self.report_builder,
        )

    def predict(self, request: MatchRequest) -> str:
        bundle = self.build_prediction_bundle(request)
        # 差异分析和反馈上下文由 predict() 注入
        return self.generate_report(bundle)

    def build_prediction_bundle(self, request: MatchRequest) -> PredictionBundle:
        parsed_request = self._parse_match_request(request)

        # === 集成点 1: 自动验证已移至后台维护任务（MaintenanceRunner） ===

        # === 集成点 2: 历史反馈已移至后台维护任务（MaintenanceRunner） ===

        sync_outcome = self.sync.sync_before_prediction(parsed_request)
        target_sync_result = (
            sync_outcome if isinstance(sync_outcome, TargetSyncResult) else None
        )
        sync_results = (
            list(target_sync_result.critical_results)
            + list(target_sync_result.optional_results)
            if target_sync_result is not None
            else list(sync_outcome or [])
        )
        match_id = (
            parsed_request.match_id
            or (target_sync_result.match_id if target_sync_result else "")
            or self._resolve_match_id_from_sync(sync_results)
        )
        readiness = (
            self._load_data_readiness(match_id)
            if target_sync_result is not None and match_id
            else None
        )
        try:
            news_result = self.news_tool.search_match_news(match_id, parsed_request.query)
            news = news_result.documents
        except Exception:
            logger.exception("ChromaDB news search failed, proceeding without news.")
            news = []
        features = self._build_features(parsed_request, sync_results)
        features = self._attach_runtime_quality_context(
            features,
            sync_results,
            news,
            readiness,
        )

        # === 集成点 3.5: 增强特征（阵型、H2H、休息天数、赔率趋势） ===
        enhancer_match_id = match_id or f"MATCH_{parsed_request.home_team}_{parsed_request.away_team}"
        try:
            features = self.enhancer_pipeline.enhance(features, enhancer_match_id)
        except Exception:
            logger.exception("Feature enhancement failed, continuing with base features.")

        probabilities = self.probability_engine.predict(features)
        value_assessment = self._assess_value(features, probabilities)
        secondary_value_assessments = self._assess_secondary_values(
            features, probabilities
        )
        policy = self.policy.decide(
            features,
            value_assessment,
            secondary_value_assessments,
        )
        return PredictionBundle(
            request=parsed_request,
            features=features,
            probabilities=probabilities,
            value_assessment=value_assessment,
            policy=policy,
            news=news,
            secondary_value_assessments=secondary_value_assessments,
            readiness=readiness,
        )

    def generate_report(self, bundle: PredictionBundle) -> str:
        """基于 PredictionBundle 生成报告，包含日志、差异分析、LLM 回退。"""
        match_id = bundle.request.match_id or ""
        feedback_report = None

        # === 集成点 4: 保存预测日志 ===
        prediction_id = self.prediction_logger.log_prediction(bundle)

        # === 集成点 4.5: 差异分析（与上次同场比赛预测对比） ===
        diff_result: dict[str, Any] = {}
        try:
            diff_result = self.diff_analyzer.analyze_and_save(prediction_id, match_id)
        except Exception:
            logger.exception("Diff analysis failed, continuing.")

        system_prompt, user_prompt = self.report_builder.build_prompt(bundle)

        # === 集成点 5: 注入历史反馈到 LLM 提示词 ===
        if feedback_report is not None and feedback_report.context_text:
            system_prompt = self._inject_feedback_to_prompt(
                system_prompt, feedback_report.context_text, prediction_id
            )

        # === 集成点 5.5: 注入差异分析上下文到 LLM 提示词  ===
        if diff_result.get("context_text"):
            system_prompt = self._inject_diff_to_prompt(system_prompt, diff_result)

        report_result = self.report_generator.generate_report(
            bundle,
            extra_system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        # 如果回退到模板，更新 bundle 的 generated_by 以正确显示备用引擎信息
        if not report_result.generated:
            bundle = PredictionBundle(
                request=bundle.request,
                features=bundle.features,
                probabilities=bundle.probabilities,
                value_assessment=bundle.value_assessment,
                policy=bundle.policy,
                news=bundle.news,
                secondary_value_assessments=bundle.secondary_value_assessments,
                readiness=bundle.readiness,
                generated_by=report_result.provider,
            )
            return self.report_builder.build_markdown(bundle)
        return report_result.content

    def _parse_match_request(self, request: MatchRequest) -> MatchRequest:
        if request.home_team and request.away_team:
            return request
        match = re.search(r"(.+?)\s+(?:vs|VS|v|对| versus )\s+(.+)", request.query)
        if not match:
            return request
        return MatchRequest(
            query=request.query,
            mode=request.mode,
            match_id=request.match_id,
            home_team=match.group(1).strip(),
            away_team=match.group(2).strip(),
            kickoff_time=request.kickoff_time,
            market=request.market,
        )

    def _build_features(
        self, request: MatchRequest, sync_results: list
    ) -> MatchFeatures:
        return self.feature_builder.build(request, sync_results)

    def _assess_value(
        self,
        features: MatchFeatures,
        probabilities: ProbabilityResult,
    ) -> ValueAssessment:
        try:
            return self.value_engine.assess_1x2(features.odds_1x2, probabilities)
        except ValueError:
            return ValueAssessment(
                values=[],
                best_value=None,
                overround=0.0,
                threshold=self.value_engine.value_threshold,
            )

    def _assess_secondary_values(
        self,
        features: MatchFeatures,
        probabilities: ProbabilityResult,
    ) -> dict[str, ValueAssessment]:
        assessments: dict[str, ValueAssessment] = {}
        if features.odds_rqspf and features.rqspf_handicap_line is not None:
            try:
                assessments["让球胜平负"] = self.value_engine.assess_handicap_1x2(
                    features.rqspf_handicap_line,
                    features.odds_rqspf,
                    probabilities,
                )
            except ValueError:
                pass
        for market_name, odds, assessor in (
            ("比分", features.odds_correct_score, self.value_engine.assess_correct_score),
            ("总进球", features.odds_total_goals, self.value_engine.assess_total_goals),
            ("半全场", features.odds_half_full, self.value_engine.assess_half_full),
        ):
            if not odds:
                continue
            try:
                assessments[market_name] = assessor(odds, probabilities)
            except ValueError:
                pass
        return assessments

    def _attach_runtime_quality_context(
        self,
        features: MatchFeatures,
        sync_results: list[Any],
        news: list[dict[str, Any]],
        readiness: DataReadinessStatus | None = None,
    ) -> MatchFeatures:
        context = dict(features.context)
        if readiness is not None:
            context["readiness"] = readiness.to_dict()
            context["readiness_missing_critical"] = list(
                readiness.missing_critical
            )
            context["no_bet_data_quality_flag"] = bool(
                context.get("no_bet_data_quality_flag") or not readiness.ready
            )
            context["stale_components"] = [
                {
                    "name": name,
                    "age_minutes": readiness.components[name].get("age_minutes"),
                }
                for name in readiness.stale_critical
            ]
        sync_statuses = [
            f"{getattr(result, 'source', 'unknown')}:{getattr(result, 'status', 'unknown')}"
            for result in sync_results
        ]
        if sync_statuses:
            context["sync_statuses"] = sync_statuses
            context["missing_exchange_flow_flag"] = any(
                getattr(result, "source", "") == "exchange_provider"
                and getattr(result, "status", "") == "skipped_no_provider_configured"
                for result in sync_results
            )
            context["sync_data_quality_flag"] = any(
                not self._is_acceptable_sync_status(
                    getattr(result, "source", "unknown"),
                    getattr(result, "status", "unknown")
                )
                for result in sync_results
            )

        # 同步后宽表新鲜度复查：同步成功但宽表数据仍超时
        critical_data_age = context.get("critical_data_age_minutes")
        if critical_data_age is not None:
            try:
                critical_data_age = float(critical_data_age)
            except (TypeError, ValueError):
                critical_data_age = None
        if critical_data_age is not None and critical_data_age > 30:
            sync_all_ok = not context.get("sync_data_quality_flag")
            if sync_all_ok:
                context["stale_data_after_sync_flag"] = True
                context["stale_data_detail"] = (
                    f"同步已完成但关键数据年龄为 {critical_data_age:.0f} 分钟（超过 30 分钟上限），"
                    "可能是上游数据源未及时更新。"
                )
            else:
                context["stale_data_detail"] = (
                    f"关键数据年龄 {critical_data_age:.0f} 分钟超过 30 分钟上限，"
                    "且同步质量未通过。"
                )

        news_count = int(context.get("news_source_count") or 0)
        context["missing_news_signal_flag"] = bool(
            context.get("missing_news_signal_flag")
            or "news" in context.get("readiness_missing_critical", [])
            or (not news and news_count <= 0)
        )
        return replace(features, context=context)

    def _load_data_readiness(self, match_id: str) -> DataReadinessStatus:
        try:
            import duckdb

            with duckdb.connect(self.config.duckdb.database_path, read_only=True) as conn:
                return DataReadinessGate.load(
                    conn,
                    match_id,
                    self.policy.max_data_age_minutes,
                ).check()
        except Exception:
            logger.exception("Failed to load component readiness for match %s", match_id)
            return DataReadinessStatus(
                ready=False,
                missing_critical=["readiness_database"],
            )

    @staticmethod
    def _resolve_match_id_from_sync(sync_results: list) -> str:
        """从同步结果中提取 match_id，无标准 ID 时返回空字符串（触发 empty_search）。"""
        for result in sync_results:
            mid = getattr(result, "details", {}).get("match_id", "")
            if mid:
                return mid
        return ""

    @staticmethod
    def _is_acceptable_sync_status(source: str, status: str) -> bool:
        if source == "exchange_provider" and status == "skipped_no_provider_configured":
            return True
        # 零结果状态不得被视为成功
        if status in {"empty_search", "empty_fetch", "empty_ingestion"}:
            return False
        return status in {
            "ok",
            "success",
            "updated",
            "fresh_cache",
            "cached",
            "no_action_needed",
        }

    def _attach_feedback_context(
        self,
        features: MatchFeatures,
        feedback_report: Any,
    ) -> MatchFeatures:
        """将反馈引擎的调整提示注入 MatchFeatures.context。"""
        context = dict(features.context)
        if hasattr(feedback_report, "adjustment_hints"):
            context.update(feedback_report.adjustment_hints)
        return replace(features, context=context)

    @staticmethod
    def _inject_feedback_to_prompt(
        system_prompt: str,
        feedback_text: str,
        prediction_id: str,
    ) -> str:
        """将历史绩效反馈注入 LLM system prompt。"""
        separator = "\n\n---\n\n"
        parts = [system_prompt]

        if feedback_text and "暂无" not in feedback_text:
            parts.append(feedback_text)
            parts.append(
                "请基于以上历史绩效数据，在报告中适当调整预测的置信度表述和风险提示。"
            )

        if prediction_id:
            parts.append(
                f"本预测 ID: {prediction_id}"
            )

        return separator.join(parts)

    @staticmethod
    def _inject_diff_to_prompt(
        system_prompt: str,
        diff_result: dict[str, Any],
    ) -> str:
        """将差异分析上下文注入 LLM system prompt。"""
        context_text = diff_result.get("context_text", "")
        if not context_text:
            return system_prompt

        version = diff_result.get("version", 1)
        version_note = f"(本场比赛第 {version} 次预测)" if version > 1 else ""

        return f"{system_prompt}\n\n---\n\n{context_text}\n\n{version_note}"
