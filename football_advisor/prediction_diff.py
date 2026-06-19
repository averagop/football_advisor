"""
预测差异分析器 (PredictionDiffAnalyzer)

每次预测后自动比对同一 match_id 的上一次预测，分析差异原因。

分析流程：
  1. 规则层：提取关键指标增量（概率偏移、赔率变化、特征变化）
  2. LLM 层：本地 qwen2.5:7b 生成一句话自然语言解释
  3. 写入 fact_prediction_log.diff_summary，注入 LLM 报告上下文

确保每次 predict() 都自动运行——已集成在 pipeline.py 中。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================
# 规则层：结构化差异提取
# ============================================================

@dataclass(frozen=True)
class PredictionSnapshot:
    """从 fact_prediction_log 提取的核心字段，用于比对。"""
    prediction_id: str
    prediction_time: str = ""
    home_team: str = ""
    away_team: str = ""
    predicted_outcome: str = ""
    model_predicted_outcome: str = ""
    predicted_probability: float = 0.0
    model_home_prob: float = 0.0
    model_draw_prob: float = 0.0
    model_away_prob: float = 0.0
    odds_at_prediction: float = 0.0
    expected_value: float = 0.0
    expected_home_goals: float = 0.0
    expected_away_goals: float = 0.0
    policy_recommendation: str = ""
    confidence: float = 0.0
    risk_level: str = ""
    data_quality_flag: bool = False


@dataclass(frozen=True)
class StructuredDiff:
    """规则层提取的结构化差异。"""
    has_previous: bool = False
    previous_prediction_id: str = ""
    time_delta_minutes: float = 0.0

    # 概率偏移
    home_prob_delta: float = 0.0
    draw_prob_delta: float = 0.0
    away_prob_delta: float = 0.0
    max_prob_shift: float = 0.0  # 最大概率变化绝对值

    # 赔率变化
    odds_delta: float = 0.0
    odds_change_pct: float = 0.0

    # 预期进球变化
    expected_home_goals_delta: float = 0.0
    expected_away_goals_delta: float = 0.0

    # 决策变化
    outcome_changed: bool = False
    recommendation_changed: bool = False
    confidence_delta: float = 0.0

    # 数据质量变化
    data_quality_improved: bool = False

    # 所有变化摘要
    significant_changes: list[str] = field(default_factory=list)


class RuleBasedDiffExtractor:
    """纯规则提取：比对两次预测的结构化差异。"""

    @staticmethod
    def extract(prev: PredictionSnapshot, curr: PredictionSnapshot) -> StructuredDiff:
        """提取结构化差异。"""
        changes: list[str] = []

        # 时间差
        try:
            prev_time = prev.prediction_time
            curr_time = curr.prediction_time
            if prev_time and curr_time:
                from datetime import datetime
                try:
                    pt = datetime.fromisoformat(str(prev_time).replace("Z", "+00:00"))
                    ct = datetime.fromisoformat(str(curr_time).replace("Z", "+00:00"))
                    time_delta = abs((ct - pt).total_seconds() / 60.0)
                except (ValueError, TypeError):
                    time_delta = 0.0
            else:
                time_delta = 0.0
        except Exception:
            time_delta = 0.0

        # 概率偏移
        home_delta = curr.model_home_prob - prev.model_home_prob
        draw_delta = curr.model_draw_prob - prev.model_draw_prob
        away_delta = curr.model_away_prob - prev.model_away_prob
        max_shift = max(abs(home_delta), abs(draw_delta), abs(away_delta))

        if max_shift > 0.03:
            direction = ""
            if abs(home_delta) >= max_shift:
                direction = "主胜" if home_delta > 0 else "主胜下降"
                changes.append(f"主胜概率 {home_delta:+.1%}")
            if abs(draw_delta) >= max_shift:
                direction = "平局" if draw_delta > 0 else "平局下降"
                changes.append(f"平局概率 {draw_delta:+.1%}")
            if abs(away_delta) >= max_shift:
                direction = "客胜" if away_delta > 0 else "客胜下降"
                changes.append(f"客胜概率 {away_delta:+.1%}")

        # 赔率变化
        odds_delta = curr.odds_at_prediction - prev.odds_at_prediction
        odds_pct = odds_delta / prev.odds_at_prediction if prev.odds_at_prediction > 0 else 0.0
        if abs(odds_pct) > 0.03:
            changes.append(f"赔率 {odds_delta:+.2f} ({odds_pct:+.1%})")

        # 预期进球变化
        home_goals_delta = curr.expected_home_goals - prev.expected_home_goals
        away_goals_delta = curr.expected_away_goals - prev.expected_away_goals
        if abs(home_goals_delta) > 0.15 or abs(away_goals_delta) > 0.15:
            changes.append(
                f"预期进球: 主 {home_goals_delta:+.2f}, 客 {away_goals_delta:+.2f}"
            )

        # 决策变化
        outcome_changed = curr.model_predicted_outcome != prev.model_predicted_outcome
        recommendation_changed = curr.policy_recommendation != prev.policy_recommendation
        confidence_delta = curr.confidence - prev.confidence

        if outcome_changed:
            changes.append(f"预测结果从\"{prev.model_predicted_outcome}\"变为\"{curr.model_predicted_outcome}\"")
        if recommendation_changed:
            changes.append(f"策略从\"{prev.policy_recommendation}\"变为\"{curr.policy_recommendation}\"")
        if abs(confidence_delta) > 0.05:
            changes.append(f"置信度 {confidence_delta:+.1%}")

        # 数据质量改善
        quality_improved = curr.data_quality_flag is False and prev.data_quality_flag is True

        return StructuredDiff(
            has_previous=True,
            previous_prediction_id=prev.prediction_id,
            time_delta_minutes=round(time_delta, 1),
            home_prob_delta=round(home_delta, 4),
            draw_prob_delta=round(draw_delta, 4),
            away_prob_delta=round(away_delta, 4),
            max_prob_shift=round(max_shift, 4),
            odds_delta=round(odds_delta, 4),
            odds_change_pct=round(odds_pct, 4),
            expected_home_goals_delta=round(home_goals_delta, 4),
            expected_away_goals_delta=round(away_goals_delta, 4),
            outcome_changed=outcome_changed,
            recommendation_changed=recommendation_changed,
            confidence_delta=round(confidence_delta, 4),
            data_quality_improved=quality_improved,
            significant_changes=changes,
        )


# ============================================================
# LLM 层：本地 qwen2.5:7b 生成解释
# ============================================================

_DIFF_EXPLAIN_PROMPT = """你是一个足球预测系统诊断助手。系统对同一场比赛在不同时间做了两次预测，你需要用一句话解释为什么预测发生了变化。

规则：
- 只输出一句话，不超过 80 字
- 用中文
- 聚焦最重要的变化原因（概率偏移最大或结果改变的维度）
- 不要重复输入数据，不要说"根据数据显示"
- 如果变化很小，就说"预测基本稳定，无明显变化" """


class DiffExplainer:
    """使用本地 Ollama (qwen2.5:7b) 将结构化差异转化为自然语言解释。"""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b",
        timeout: int = 20,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def explain(self, diff: StructuredDiff, prev: PredictionSnapshot, curr: PredictionSnapshot) -> str:
        """生成一句话解释。"""
        if not diff.significant_changes:
            return "预测基本稳定，关键指标无明显变化。"

        # 构建结构化输入
        input_text = self._build_input(diff, prev, curr)

        import requests

        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": _DIFF_EXPLAIN_PROMPT},
                        {"role": "user", "content": input_text},
                    ],
                    "options": {"temperature": 0.1, "num_predict": 120},
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            content = response.json().get("message", {}).get("content", "")
            explanation = content.strip().strip("\"'")
            if len(explanation) > 150:
                explanation = explanation[:147] + "..."
            return explanation if explanation else self._fallback_explanation(diff)
        except Exception as exc:
            logger.warning("DiffExplainer failed: %s, using fallback", exc)
            return self._fallback_explanation(diff)

    @staticmethod
    def _build_input(diff: StructuredDiff, prev: PredictionSnapshot, curr: PredictionSnapshot) -> str:
        """构建 LLM 输入。"""
        lines = []
        lines.append(f"比赛: {curr.home_team} vs {curr.away_team}")
        lines.append(f"当前预测: {curr.model_predicted_outcome} (概率 {curr.predicted_probability:.1%})")
        lines.append(f"上次预测: {prev.model_predicted_outcome} (概率 {prev.predicted_probability:.1%})")
        lines.append("")

        if diff.significant_changes:
            lines.append("关键变化:")
            for change in diff.significant_changes:
                lines.append(f"- {change}")

        if diff.data_quality_improved:
            lines.append("- 数据质量从不足改善为充足")

        if diff.outcome_changed:
            lines.append("- 预测结果发生了改变")

        return "\n".join(lines)

    @staticmethod
    def _fallback_explanation(diff: StructuredDiff) -> str:
        """LLM 不可用时的规则回退。"""
        if not diff.significant_changes:
            return "预测基本稳定，关键指标无明显变化。"
        return "；".join(diff.significant_changes[:3])


# ============================================================
# 主分析器
# ============================================================

class PredictionDiffAnalyzer:
    """预测差异分析器：主入口。

    集成在 pipeline.py 中，每次 predict() 自动调用。
    """

    def __init__(
        self,
        database_path: str = "football_system.db",
        explainer: DiffExplainer | None = None,
    ) -> None:
        self._database_path = database_path
        self._extractor = RuleBasedDiffExtractor()
        self._explainer = explainer or DiffExplainer()

    def analyze_and_save(self, prediction_id: str, match_id: str) -> dict[str, Any]:
        """分析当前预测与上次的差异，写入 diff_summary，返回结果字典。

        调用时机：prediction_logger.log_prediction() 之后立即调用。
        即使失败也不影响主流程。
        """
        result: dict[str, Any] = {
            "diff_summary": "",
            "version": 1,
            "has_previous": False,
            "context_text": "",
        }

        if not prediction_id or not match_id:
            return result

        import duckdb

        try:
            with duckdb.connect(self._database_path) as conn:
                # 获取当前预测
                curr_row = conn.execute(
                    """SELECT prediction_id, prediction_time, home_team, away_team,
                       COALESCE(model_predicted_outcome, predicted_outcome),
                       predicted_probability,
                       model_home_prob, model_draw_prob, model_away_prob,
                       odds_at_prediction, expected_value,
                       expected_home_goals, expected_away_goals,
                       policy_recommendation, confidence, risk_level,
                       data_quality_flag
                    FROM core.fact_prediction_log
                    WHERE prediction_id = ?""",
                    [prediction_id],
                ).fetchone()

                if not curr_row:
                    return result

                curr = PredictionSnapshot(
                    prediction_id=str(curr_row[0]),
                    prediction_time=str(curr_row[1]),
                    home_team=str(curr_row[2] or ""),
                    away_team=str(curr_row[3] or ""),
                    predicted_outcome=str(curr_row[4] or ""),
                    model_predicted_outcome=str(curr_row[4] or ""),
                    predicted_probability=float(curr_row[5] or 0),
                    model_home_prob=float(curr_row[6] or 0),
                    model_draw_prob=float(curr_row[7] or 0),
                    model_away_prob=float(curr_row[8] or 0),
                    odds_at_prediction=float(curr_row[9] or 0),
                    expected_value=float(curr_row[10] or 0),
                    expected_home_goals=float(curr_row[11] or 0),
                    expected_away_goals=float(curr_row[12] or 0),
                    policy_recommendation=str(curr_row[13] or ""),
                    confidence=float(curr_row[14] or 0),
                    risk_level=str(curr_row[15] or ""),
                    data_quality_flag=bool(curr_row[16]),
                )

                # 查找同 match_id 的上一次预测
                try:
                    prev_row = conn.execute(
                        """SELECT prediction_id, prediction_time, home_team, away_team,
                           COALESCE(model_predicted_outcome, predicted_outcome),
                           predicted_probability,
                           model_home_prob, model_draw_prob, model_away_prob,
                           odds_at_prediction, expected_value,
                           expected_home_goals, expected_away_goals,
                           policy_recommendation, confidence, risk_level,
                           data_quality_flag, COALESCE(version, 1)
                        FROM core.fact_prediction_log
                        WHERE match_id = ? AND prediction_id != ?
                        ORDER BY prediction_time DESC
                        LIMIT 1""",
                        [match_id, prediction_id],
                    ).fetchone()
                except Exception:
                    # version 列可能不存在（旧表结构）
                    prev_row = conn.execute(
                        """SELECT prediction_id, prediction_time, home_team, away_team,
                           COALESCE(model_predicted_outcome, predicted_outcome),
                           predicted_probability,
                           model_home_prob, model_draw_prob, model_away_prob,
                           odds_at_prediction, expected_value,
                           expected_home_goals, expected_away_goals,
                           policy_recommendation, confidence, risk_level,
                           data_quality_flag, 1
                        FROM core.fact_prediction_log
                        WHERE match_id = ? AND prediction_id != ?
                        ORDER BY prediction_time DESC
                        LIMIT 1""",
                        [match_id, prediction_id],
                    ).fetchone()

                version = 1
                if prev_row:
                    prev = PredictionSnapshot(
                        prediction_id=str(prev_row[0]),
                        prediction_time=str(prev_row[1]),
                        home_team=str(prev_row[2] or ""),
                        away_team=str(prev_row[3] or ""),
                        predicted_outcome=str(prev_row[4] or ""),
                        model_predicted_outcome=str(prev_row[4] or ""),
                        predicted_probability=float(prev_row[5] or 0),
                        model_home_prob=float(prev_row[6] or 0),
                        model_draw_prob=float(prev_row[7] or 0),
                        model_away_prob=float(prev_row[8] or 0),
                        odds_at_prediction=float(prev_row[9] or 0),
                        expected_value=float(prev_row[10] or 0),
                        expected_home_goals=float(prev_row[11] or 0),
                        expected_away_goals=float(prev_row[12] or 0),
                        policy_recommendation=str(prev_row[13] or ""),
                        confidence=float(prev_row[14] or 0),
                        risk_level=str(prev_row[15] or ""),
                        data_quality_flag=bool(prev_row[16]),
                    )
                    version = int(prev_row[17] or 0) + 1

                    # 规则层：提取结构化差异
                    diff = self._extractor.extract(prev, curr)

                    # LLM 层：生成自然语言解释
                    explanation = self._explainer.explain(diff, prev, curr)

                    conn.execute(
                        "UPDATE core.fact_prediction_log SET diff_summary = ? WHERE prediction_id = ?",
                        [explanation, prediction_id],
                    )

                    result["diff_summary"] = explanation
                    result["has_previous"] = True
                    result["context_text"] = self._build_context_text(diff, explanation)

                    logger.info("Diff analysis for %s: %s", prediction_id, explanation)
                else:
                    # 首次预测，无差异
                    conn.execute(
                        "UPDATE core.fact_prediction_log SET diff_summary = '首次预测，无历史对比' WHERE prediction_id = ?",
                        [prediction_id],
                    )
                    result["diff_summary"] = "首次预测，无历史对比"

                # 更新 version
                conn.execute(
                    "UPDATE core.fact_prediction_log SET version = ? WHERE prediction_id = ?",
                    [version, prediction_id],
                )
                result["version"] = version

                return result
        except Exception:
            logger.exception("Diff analysis failed for prediction_id=%s", prediction_id)
            raise

    @staticmethod
    def _build_context_text(diff: StructuredDiff, explanation: str) -> str:
        """构建可注入 LLM 报告的上下文文本。"""
        lines = []
        lines.append("## 预测变化分析")
        lines.append("")

        if diff.significant_changes:
            lines.append("### 与上次预测的差异")
            for change in diff.significant_changes[:5]:
                lines.append(f"- {change}")
            lines.append("")

        lines.append(f"### 变化原因")
        lines.append(f"> {explanation}")
        lines.append("")

        if diff.outcome_changed:
            lines.append("**注意**：本次预测结果与上次不同，请仔细核对最新数据。")

        return "\n".join(lines)
