"""
反馈引擎：基于历史预测绩效指导新预测

集成到 PredictionPipeline 主流程中：
1. 每次预测前加载历史绩效数据
2. 计算校准漂移、近期趋势、市场偏差等信号
3. 生成反馈建议，注入到 LLM 报告上下文中
4. 自动调整 NoBet 策略阈值

联动关系：
  预测日志 (fact_prediction_log) → 绩效分析 → 反馈建议 → 新预测 → 投注账本 (fact_bet_ledger)
       ↑                                                                    |
       └────────────────── 自动验证 ← 赛后结果 ←──────────────────────────────┘
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .verification_engine import PerformanceSummary, PostMatchVerifier

logger = logging.getLogger(__name__)


# ============================================================
# 反馈信号
# ============================================================

@dataclass(frozen=True)
class FeedbackSignal:
    """单次预测前的反馈信号。"""

    # 整体绩效
    total_predictions: int
    verified_count: int
    overall_accuracy: float  # 总体准确率
    recent_accuracy: float  # 最近 10 场准确率

    # 校准质量
    calibration_drift: float  # 正值 = 过度自信，负值 = 信心不足
    avg_confidence_when_correct: float
    avg_confidence_when_wrong: float

    # 投注绩效
    total_bets: int
    total_profit: float
    roi: float
    win_rate: float

    # 趋势
    streak: int  # 连赢/连输（正=连赢，负=连输）
    recent_accuracy_trend: float  # 最近 10 场 vs 之前 10 场的变化

    # 市场偏差
    market_accuracy: dict[str, float]  # 各市场类型准确率
    best_market: str  # 最佳表现市场
    worst_market: str  # 最差表现市场

    # 建议
    confidence_adjustment: float  # 建议置信度调整（-0.1 ~ +0.1）
    value_threshold_adjustment: float  # 建议价值阈值调整
    no_bet_tightening: str  # "tighten" / "normal" / "loosen"
    recommendations: list[str]  # 自然语言建议列表


@dataclass(frozen=True)
class FeedbackReport:
    """反馈报告：用于注入 LLM 上下文。"""
    signal: FeedbackSignal
    context_text: str  # 可注入 LLM system prompt 的文本
    adjustment_hints: dict[str, Any]  # 可注入 MatchFeatures.context 的字典


# ============================================================
# 反馈引擎
# ============================================================

class FeedbackEngine:
    """历史绩效反馈引擎。

    在每次预测前调用，加载历史数据，生成反馈信号和建议。
    反馈信号用于：
    1. 调整预测报告的置信度表述
    2. 调整 NoBet 策略阈值
    3. 给 LLM 提供历史绩效上下文
    """

    def __init__(self, database_path: str = "football_system.db") -> None:
        self._database_path = database_path
        self._verifier = PostMatchVerifier(database_path)

    def analyze(self) -> FeedbackReport:
        """分析历史绩效，生成反馈报告。

        调用时机：predict() 开始前。
        即使没有历史数据，也返回空的 FeedbackReport（不影响正常流程）。
        """
        summary = self._verifier.get_performance_summary()

        if summary.verified_count == 0:
            return FeedbackReport(
                signal=FeedbackSignal(
                    total_predictions=0, verified_count=0,
                    overall_accuracy=0.0, recent_accuracy=0.0,
                    calibration_drift=0.0, avg_confidence_when_correct=0.0,
                    avg_confidence_when_wrong=0.0,
                    total_bets=0, total_profit=0.0, roi=0.0, win_rate=0.0,
                    streak=0, recent_accuracy_trend=0.0,
                    market_accuracy={}, best_market="", worst_market="",
                    confidence_adjustment=0.0, value_threshold_adjustment=0.0,
                    no_bet_tightening="normal", recommendations=[],
                ),
                context_text="暂无历史预测数据，无法提供绩效反馈。",
                adjustment_hints={},
            )

        # 计算连赢/连输
        streak = self._compute_streak()

        # 计算近期趋势
        recent_accuracy_trend = self._compute_recent_trend()

        # 市场分析
        market_accuracy = {
            k: v["accuracy"]
            for k, v in summary.market_breakdown.items()
            if v["verified"] >= 3  # 至少 3 场才有意义
        }
        best_market = max(market_accuracy, key=market_accuracy.get) if market_accuracy else ""
        worst_market = min(market_accuracy, key=market_accuracy.get) if market_accuracy else ""

        # 生成建议
        (
            confidence_adjustment,
            value_threshold_adjustment,
            no_bet_tightening,
            recommendations,
        ) = self._generate_recommendations(summary, streak, recent_accuracy_trend, market_accuracy)

        signal = FeedbackSignal(
            total_predictions=summary.total_predictions,
            verified_count=summary.verified_count,
            overall_accuracy=summary.accuracy,
            recent_accuracy=summary.recent_accuracy,
            calibration_drift=summary.calibration_drift,
            avg_confidence_when_correct=summary.avg_confidence_when_correct,
            avg_confidence_when_wrong=summary.avg_confidence_when_wrong,
            total_bets=summary.total_bets,
            total_profit=summary.total_profit,
            roi=summary.roi,
            win_rate=summary.win_rate,
            streak=streak,
            recent_accuracy_trend=recent_accuracy_trend,
            market_accuracy=market_accuracy,
            best_market=best_market,
            worst_market=worst_market,
            confidence_adjustment=confidence_adjustment,
            value_threshold_adjustment=value_threshold_adjustment,
            no_bet_tightening=no_bet_tightening,
            recommendations=recommendations,
        )

        context_text = self._build_context_text(signal, summary)
        adjustment_hints = self._build_adjustment_hints(signal)

        return FeedbackReport(
            signal=signal,
            context_text=context_text,
            adjustment_hints=adjustment_hints,
        )

    def _compute_streak(self) -> int:
        """计算最近连赢/连输。正数=连赢，负数=连输，0=无历史或无连胜。"""
        import duckdb

        try:
            with duckdb.connect(self._database_path) as conn:
                rows = conn.execute(
                    """
                    SELECT is_correct FROM core.fact_prediction_log
                    WHERE is_verified
                    ORDER BY verified_at DESC
                    LIMIT 20
                    """
                ).fetchall()
                if not rows:
                    return 0

                streak = 0
                first_result = rows[0][0]
                for row in rows:
                    if row[0] == first_result:
                        streak += 1 if first_result else -1
                    else:
                        break
                return streak
        except Exception:
            return 0

    def _compute_recent_trend(self) -> float:
        """计算最近 10 场 vs 之前 10 场的准确率变化。"""
        import duckdb

        try:
            with duckdb.connect(self._database_path) as conn:
                # 最近 10 场
                recent = conn.execute(
                    """
                    SELECT SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0)
                    FROM (
                        SELECT is_correct FROM core.fact_prediction_log
                        WHERE is_verified ORDER BY verified_at DESC LIMIT 10
                    )
                    """
                ).fetchone()

                # 之前 10 场
                older = conn.execute(
                    """
                    SELECT SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0)
                    FROM (
                        SELECT is_correct FROM core.fact_prediction_log
                        WHERE is_verified ORDER BY verified_at DESC LIMIT 10 OFFSET 10
                    )
                    """
                ).fetchone()

                recent_acc = float(recent[0]) if recent and recent[0] else 0.0
                older_acc = float(older[0]) if older and older[0] else 0.0
                return recent_acc - older_acc
        except Exception:
            return 0.0

    def _generate_recommendations(
        self,
        summary: PerformanceSummary,
        streak: int,
        recent_trend: float,
        market_accuracy: dict[str, float],
    ) -> tuple[float, float, str, list[str]]:
        """生成调整建议。"""
        recommendations: list[str] = []
        confidence_adjustment = 0.0
        value_threshold_adjustment = 0.0
        no_bet_tightening = "normal"

        # 1. 校准漂移分析
        if summary.calibration_drift > 0.10:
            recommendations.append(
                f"模型存在过度自信（校准漂移 {summary.calibration_drift:+.1%}），"
                f"预测正确时平均置信度 {summary.avg_confidence_when_correct:.1%}，"
                f"预测错误时平均置信度 {summary.avg_confidence_when_wrong:.1%}。"
                "建议在报告中降低确定性表述，增加风险提示。"
            )
            confidence_adjustment = -0.05
        elif summary.calibration_drift < -0.05:
            recommendations.append(
                f"模型偏保守（校准漂移 {summary.calibration_drift:+.1%}），"
                "可适当提高置信度表述。"
            )
            confidence_adjustment = +0.03

        # 2. 近期趋势分析
        if recent_trend > 0.10:
            recommendations.append(
                f"近期准确率呈上升趋势（+{recent_trend:.1%}），"
                "模型可能正在适应当前比赛节奏。"
            )
        elif recent_trend < -0.10:
            recommendations.append(
                f"近期准确率下降（{recent_trend:.1%}），"
                "建议收紧 NoBet 策略，提高数据质量门槛。"
            )
            no_bet_tightening = "tighten"

        # 3. 连赢/连输分析
        if streak >= 3:
            recommendations.append(
                f"当前 {streak} 连赢，注意不要过度乐观，保持纪律。"
            )
        elif streak <= -3:
            recommendations.append(
                f"当前 {abs(streak)} 连输，检查策略是否需要调整，"
                "但不要因短期波动而大幅改变策略。"
            )
            no_bet_tightening = "tighten"
            value_threshold_adjustment = +0.02  # 提高价值门槛

        # 4. 市场偏差分析
        if len(market_accuracy) >= 2:
            best = max(market_accuracy, key=market_accuracy.get)
            worst = min(market_accuracy, key=market_accuracy.get)
            if market_accuracy[best] - market_accuracy[worst] > 0.15:
                recommendations.append(
                    f"市场偏差显著：{best} 准确率 {market_accuracy[best]:.1%}，"
                    f"{worst} 准确率 {market_accuracy[worst]:.1%}。"
                    f"建议优先使用 {best} 玩法，在 {worst} 玩法上增加 NoBet 频率。"
                )

        # 5. ROI 分析
        if summary.roi > 0.15:
            recommendations.append(
                f"当前 ROI {summary.roi:.1%}，策略表现优异。"
            )
        elif summary.roi < -0.10 and summary.total_bets >= 10:
            recommendations.append(
                f"当前 ROI {summary.roi:.1%}，持续亏损。"
                "建议重新评估策略参数，或暂停投注等待策略调整。"
            )
            no_bet_tightening = "tighten"

        # 6. 样本量提醒
        if summary.verified_count < 20:
            recommendations.append(
                f"已验证预测仅 {summary.verified_count} 场，"
                "统计显著性不足，反馈建议仅供参考。"
            )

        return confidence_adjustment, value_threshold_adjustment, no_bet_tightening, recommendations

    def _build_context_text(self, signal: FeedbackSignal, summary: PerformanceSummary) -> str:
        """构建可注入 LLM 的上下文文本。"""
        parts = ["## 历史预测绩效反馈", ""]

        if summary.verified_count == 0:
            parts.append("暂无已验证的历史预测。")
            return "\n".join(parts)

        # 核心指标
        parts.append("### 核心指标")
        parts.append(f"- 总预测: {summary.total_predictions} 场")
        parts.append(f"- 已验证: {summary.verified_count} 场")
        parts.append(f"- 整体准确率: {summary.accuracy:.1%}")
        parts.append(f"- 最近 10 场准确率: {summary.recent_accuracy:.1%}")
        parts.append(f"- 校准漂移: {summary.calibration_drift:+.1%}（正=过度自信）")
        parts.append("")

        if summary.total_bets > 0:
            parts.append("### 投注绩效")
            parts.append(f"- 投注数: {summary.total_bets}")
            parts.append(f"- 胜率: {summary.win_rate:.1%}")
            if summary.roi is not None:
                parts.append(f"- ROI: {summary.roi:+.1%}")
            if summary.total_profit is not None:
                parts.append(f"- 累计盈亏: {summary.total_profit:+.2f} 单位")
            parts.append("")

        if signal.market_accuracy:
            parts.append("### 各玩法准确率")
            for market, acc in sorted(signal.market_accuracy.items()):
                badge = "✓" if acc >= 0.5 else "✗"
                parts.append(f"- {badge} {market}: {acc:.1%}")
            parts.append("")

        if signal.recommendations:
            parts.append("### 策略建议")
            for i, rec in enumerate(signal.recommendations, 1):
                parts.append(f"{i}. {rec}")
            parts.append("")

        return "\n".join(parts)

    def _build_adjustment_hints(self, signal: FeedbackSignal) -> dict[str, Any]:
        """构建可注入 MatchFeatures.context 的调整提示。"""
        hints: dict[str, Any] = {
            "feedback_confidence_adjustment": signal.confidence_adjustment,
            "feedback_value_threshold_adjustment": signal.value_threshold_adjustment,
            "feedback_no_bet_tightening": signal.no_bet_tightening,
            "feedback_recent_accuracy": signal.recent_accuracy,
            "feedback_streak": signal.streak,
            "feedback_calibration_drift": signal.calibration_drift,
        }

        if signal.best_market:
            hints["feedback_best_market"] = signal.best_market
        if signal.worst_market:
            hints["feedback_worst_market"] = signal.worst_market

        return hints

    def get_feedback_for_llm(self) -> str:
        """获取反馈文本，可直接注入 LLM system prompt。"""
        report = self.analyze()
        return report.context_text

    def get_feedback_for_features(self) -> dict[str, Any]:
        """获取反馈提示，可注入 MatchFeatures.context。"""
        report = self.analyze()
        return report.adjustment_hints