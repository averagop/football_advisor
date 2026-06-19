from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Mapping

from .models import (
    MatchFeatures,
    PolicyDecision,
    Recommendation,
    RiskLevel,
    ValueAssessment,
)
from .time_utils import as_utc


class NoBetPolicy:
    def __init__(
        self,
        max_data_age_minutes: int = 30,
        platt_a: float = 7.0,
        platt_b: float = -0.5,
        confidence_floor: float = 0.35,
        confidence_cap: float = 0.90,
        market_deviation_threshold: float = 0.15,
        calibrated: bool = True,
        model_version: str = "unspecified",
    ) -> None:
        self.max_data_age_minutes = max_data_age_minutes
        self.platt_a = platt_a
        self.platt_b = platt_b
        self.confidence_floor = confidence_floor
        self.confidence_cap = confidence_cap
        self.market_deviation_threshold = market_deviation_threshold
        self.calibrated = calibrated
        self.model_version = model_version

    def decide(
        self,
        features: MatchFeatures,
        value_assessment: ValueAssessment,
        secondary_value_assessments: Mapping[str, ValueAssessment] | None = None,
    ) -> PolicyDecision:
        reasons: list[str] = []
        risk = RiskLevel.LOW
        best_market = "胜平负"
        best_value = value_assessment.best_value
        for market, assessment in (secondary_value_assessments or {}).items():
            candidate = assessment.best_value
            if candidate is not None and (
                best_value is None or candidate.edge > best_value.edge
            ):
                best_market = market
                best_value = candidate

        if not self.calibrated:
            reasons.append("model_uncalibrated")
            risk = RiskLevel.HIGH

        data_age_minutes = self._age_minutes(features.updated_at)
        if data_age_minutes < -0.01:
            reasons.append(
                f"Data timestamp is in the future by {abs(data_age_minutes):.1f} minutes."
            )
            risk = RiskLevel.HIGH
        if data_age_minutes > self.max_data_age_minutes:
            reasons.append(
                f"Data is stale: {data_age_minutes:.1f} minutes old, limit is {self.max_data_age_minutes}."
            )
            risk = RiskLevel.HIGH

        missing_odds = {"home", "draw", "away"}.difference(features.odds_1x2)
        if missing_odds:
            reasons.append(f"Missing 1x2 odds: {', '.join(sorted(missing_odds))}.")
            risk = RiskLevel.HIGH

        if not best_value:
            reasons.append("No outcome clears the value threshold.")
            risk = max(risk, RiskLevel.MEDIUM, key=self._risk_rank)

        if features.home.injury_penalty >= 0.35 or features.away.injury_penalty >= 0.35:
            reasons.append("Important injury uncertainty detected.")
            risk = max(risk, RiskLevel.MEDIUM, key=self._risk_rank)

        if features.context.get("no_bet_data_quality_flag"):
            reasons.append("DuckDB data quality flag requires No Bet.")
            risk = RiskLevel.HIGH

        if features.context.get("sync_data_quality_flag"):
            reasons.append("Sync result did not satisfy pre-prediction requirements.")
            risk = RiskLevel.HIGH

        if features.context.get("missing_news_signal_flag"):
            reasons.append("News signal is missing or stale; prediction blocked.")
            risk = RiskLevel.HIGH

        if features.context.get("feature_source") == "placeholder_fallback":
            reasons.append(
                "Feature source is placeholder fallback; prediction blocked."
            )
            risk = RiskLevel.HIGH

        if features.context.get("anti_consensus_trap_flag"):
            reasons.append("Market anti-consensus trap flag detected.")
            risk = max(risk, RiskLevel.MEDIUM, key=self._risk_rank)

        if features.context.get(
            "high_variance_referee_warning"
        ) or features.context.get("news_risk_flag"):
            reasons.append("High-variance external risk flag detected.")
            risk = max(risk, RiskLevel.MEDIUM, key=self._risk_rank)

        # 组件级缺口检查：每个缺失的组件给出明确原因
        gap_reasons, gap_risk = self._collect_gap_reasons(features)
        reasons.extend(gap_reasons)
        risk = max(risk, gap_risk, key=self._risk_rank)

        # 市场偏离风险：模型概率与归一化赔率隐含概率偏差过大
        market_deviation = self._market_deviation(value_assessment)
        if market_deviation > self.market_deviation_threshold:
            reasons.append(
                f"Market deviation {market_deviation:.1%} exceeds threshold "
                f"{self.market_deviation_threshold:.0%}; model probability "
                f"significantly diverges from market consensus."
            )
            risk = max(risk, RiskLevel.MEDIUM, key=self._risk_rank)

        if risk is RiskLevel.HIGH or best_value is None:
            return PolicyDecision(
                recommendation=Recommendation.NO_BET,
                risk_level=risk,
                confidence=0.0,
                reasons=reasons or ["No bet policy blocked recommendation."],
            )

        confidence = self._platt_confidence(best_value.edge)
        reasons.append(
            f"Best edge is {best_value.edge:.3f} on {best_market}:{best_value.outcome}."
        )
        return PolicyDecision(
            recommendation=Recommendation.BET,
            risk_level=risk,
            confidence=confidence,
            reasons=reasons,
        )

    @staticmethod
    def _age_minutes(updated_at: datetime) -> float:
        return (
            datetime.now(timezone.utc) - as_utc(updated_at)
        ).total_seconds() / 60.0

    def _platt_confidence(self, edge: float) -> float:
        """Map edge to confidence via Platt Scaling sigmoid: 1/(1+exp(-(A*x+B)))."""
        raw = 1.0 / (1.0 + math.exp(-(self.platt_a * edge + self.platt_b)))
        return min(self.confidence_cap, max(self.confidence_floor, raw))

    @staticmethod
    def _risk_rank(level: RiskLevel) -> int:
        return {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3}[level]

    @staticmethod
    def _market_deviation(assessment: ValueAssessment) -> float:
        """计算模型概率与归一化赔率隐含概率的最大偏差。

        返回 0.0 到 1.0 之间的值，表示 max(abs(model - implied))。
        当 best_value 为 None 时返回 0.0。
        """
        if not assessment.values:
            return 0.0
        deviations = [
            abs(item.model_probability - item.implied_probability)
            for item in assessment.values
        ]
        return max(deviations) if deviations else 0.0

    def _collect_gap_reasons(
        self, features: MatchFeatures
    ) -> tuple[list[str], RiskLevel]:
        """按组件独立检查数据缺口，每个缺口给出明确原因。

        返回 (原因列表, 最高风险等级)。
        """
        reasons: list[str] = []
        risk = RiskLevel.LOW

        ctx = features.context

        # 标准比赛映射缺失
        if ctx.get("missing_canonical_match_mapping"):
            reasons.append("Missing canonical match mapping; Sporttery data not bound.")
            risk = RiskLevel.HIGH

        # 官方已开售玩法缺失
        missing_markets = ctx.get("missing_sporttery_markets", [])
        if missing_markets:
            market_names = ", ".join(missing_markets)
            reasons.append(f"Missing Sporttery markets: {market_names}.")
            risk = max(risk, RiskLevel.HIGH, key=self._risk_rank)

        # 主客队统计缺失
        missing_stats = ctx.get("missing_team_stats", [])
        if missing_stats:
            stats_names = ", ".join(missing_stats)
            reasons.append(f"Missing team stats: {stats_names}.")
            risk = max(risk, RiskLevel.HIGH, key=self._risk_rank)

        # 比赛上下文缺失
        if ctx.get("missing_match_context_flag"):
            reasons.append("Missing match context (H2H, form, venue).")
            risk = max(risk, RiskLevel.HIGH, key=self._risk_rank)

        # 目标比赛新闻缺失
        if ctx.get("missing_news_signal_flag"):
            reasons.append("Missing target match news signal.")
            risk = max(risk, RiskLevel.HIGH, key=self._risk_rank)

        # 组件过期检查
        stale_components = ctx.get("stale_components", [])
        if stale_components:
            stale_names = ", ".join(
                f"{c['name']}({c['age_minutes']}min)" for c in stale_components
            )
            reasons.append(f"Stale components exceeding 30min: {stale_names}.")
            risk = max(risk, RiskLevel.HIGH, key=self._risk_rank)

        # 可选组件缺失：天气、裁判 — 不阻塞预测，仅记录
        missing_optional = ctx.get("missing_optional", [])
        if missing_optional:
            optional_names = ", ".join(missing_optional)
            reasons.append(
                f"Optional component(s) missing: {optional_names}. "
                f"Not blocking prediction."
            )

        return reasons, risk
