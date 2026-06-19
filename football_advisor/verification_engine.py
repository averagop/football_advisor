"""
预测日志 + 自动验证 + 投注账本

集成到 PredictionPipeline 主流程中：
1. 每次预测后自动保存预测快照到 fact_prediction_log
2. 每次数据同步时自动检测已完赛比赛，比对预测与实际结果
3. 投注账本独立管理，用户手动录入投注金额和选项
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import (
    MatchFeatures,
    PredictionBundle,
    ProbabilityResult,
    ValueAssessment,
)
from .time_utils import utc_now

logger = logging.getLogger(__name__)


class PredictionLoggingError(RuntimeError):
    """预测日志写入失败异常。"""


# ============================================================
# 预测日志
# ============================================================

@dataclass(frozen=True)
class PredictionLogEntry:
    prediction_id: str
    match_id: str
    home_team: str
    away_team: str
    prediction_time: datetime
    kickoff_time: datetime | None = None
    market_type: str = "SPF"
    predicted_outcome: str = ""
    predicted_probability: float = 0.0
    model_home_prob: float = 0.0
    model_draw_prob: float = 0.0
    model_away_prob: float = 0.0
    odds_at_prediction: float = 0.0
    expected_value: float = 0.0
    expected_home_goals: float = 0.0
    expected_away_goals: float = 0.0
    policy_recommendation: str = ""
    policy_reasons: str = ""
    confidence: float = 0.0
    risk_level: str = ""
    data_quality_flag: bool = False


class PredictionLogger:
    """预测日志管理器：每次预测后保存完整快照到 DuckDB。"""

    def __init__(self, database_path: str = "football_system.db") -> None:
        self._database_path = database_path

    def log_prediction(self, bundle: PredictionBundle) -> str:
        """保存预测快照，返回 prediction_id。

        从 PredictionBundle 中提取所有关键字段，写入 fact_prediction_log。
        安全注意：不记录投注金额（金额仅在 bet_ledger 中）。
        """
        import duckdb

        prediction_id = f"PRED_{uuid.uuid4().hex[:12]}"
        req = bundle.request
        probs = bundle.probabilities
        value = bundle.value_assessment
        policy = bundle.policy
        features = bundle.features
        home_team_id = str(features.context.get("home_team_id") or "").strip()
        away_team_id = str(features.context.get("away_team_id") or "").strip()
        if not home_team_id or not away_team_id:
            raise PredictionLoggingError(
                "Standard home_team_id and away_team_id are required for prediction logging."
            )

        # 最佳价值选项（投注建议方向，非模型预测结果）
        best_value = value.best_value
        predicted_outcome = best_value.outcome if best_value else ""
        predicted_probability = best_value.model_probability if best_value else 0.0
        odds_at_prediction = best_value.decimal_odds if best_value else 0.0
        expected_value = best_value.edge if best_value else 0.0

        # 模型最大概率结果（核验用，与投注建议分离）
        model_probs = [
            ("HOME", probs.home_win),
            ("DRAW", probs.draw),
            ("AWAY", probs.away_win),
        ]
        model_predicted_outcome = (
            max(model_probs, key=lambda x: x[1])[0] if probs else ""
        )
        model_predicted_probability = (
            max(model_probs, key=lambda x: x[1])[1] if probs else 0.0
        )

        # 特征快照 JSON（用于后续调试）
        feature_snapshot = self._serialize_features(features)

        try:
            with duckdb.connect(self._database_path) as conn:
                conn.execute(
                    """
                    INSERT INTO core.fact_prediction_log (
                        prediction_id, match_id, prediction_time,
                        home_team, away_team, home_team_id, away_team_id, kickoff_time,
                        market_type, predicted_outcome, predicted_probability,
                        model_predicted_outcome, model_predicted_probability,
                        value_candidate_outcome,
                        model_home_prob, model_draw_prob, model_away_prob,
                        odds_at_prediction, expected_value,
                        expected_home_goals, expected_away_goals,
                        policy_recommendation, policy_reasons,
                        confidence, risk_level, data_quality_flag,
                        feature_snapshot_json, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
                    """,
                    [
                        prediction_id,
                        req.match_id or f"MATCH_{prediction_id}",
                        utc_now(),
                        req.home_team or features.home.name,
                        req.away_team or features.away.name,
                        home_team_id,
                        away_team_id,
                        req.kickoff_time,
                        req.market,
                        predicted_outcome,
                        predicted_probability,
                        model_predicted_outcome,
                        model_predicted_probability,
                        predicted_outcome,  # value_candidate_outcome = best_value.outcome
                        probs.home_win,
                        probs.draw,
                        probs.away_win,
                        odds_at_prediction,
                        expected_value,
                        probs.expected_home_goals,
                        probs.expected_away_goals,
                        policy.recommendation.value,
                        json.dumps(policy.reasons, ensure_ascii=False),
                        policy.confidence,
                        policy.risk_level.value,
                        features.context.get("no_bet_data_quality_flag", False),
                        feature_snapshot,
                    ],
                )
            logger.info("Prediction logged: %s (%s vs %s)", prediction_id, req.home_team, req.away_team)
            return prediction_id
        except Exception as exc:
            logger.error("Failed to log prediction: %s", exc)
            raise PredictionLoggingError(
                f"Failed to log prediction to database: {exc}"
            ) from exc

    @staticmethod
    def _serialize_features(features: MatchFeatures) -> str:
        """将 MatchFeatures 序列化为 JSON（仅保留关键字段，不记录敏感信息）。"""
        try:
            return json.dumps(
                {
                    "home_name": features.home.name,
                    "away_name": features.away.name,
                    "home_elo": features.home.elo,
                    "away_elo": features.away.elo,
                    "home_attack": features.home.attack_strength,
                    "away_attack": features.away.attack_strength,
                    "home_defense": features.home.defense_strength,
                    "away_defense": features.away.defense_strength,
                    "home_recent_ppm": features.home.recent_points_per_match,
                    "away_recent_ppm": features.away.recent_points_per_match,
                    "odds_1x2": features.odds_1x2,
                    "sources": features.sources,
                    "context": {
                        k: v for k, v in features.context.items()
                        if not any(s in k.lower() for s in ("token", "key", "secret", "password"))
                    },
                },
                ensure_ascii=False,
            )
        except Exception:
            return "{}"


# ============================================================
# 自动验证
# ============================================================

@dataclass(frozen=True)
class VerificationResult:
    prediction_id: str
    match_id: str
    home_team: str
    away_team: str
    predicted_outcome: str
    actual_home_score: int
    actual_away_score: int
    actual_outcome: str
    is_correct: bool
    predicted_probability: float
    odds_at_prediction: float
    profit_loss_unit: float | None
    verified_at: datetime


@dataclass(frozen=True)
class PerformanceSummary:
    total_predictions: int
    verified_count: int
    correct_count: int
    accuracy: float
    total_bets: int
    total_staked: float
    total_returned: float
    total_profit: float
    roi: float
    win_rate: float
    avg_confidence: float
    avg_confidence_when_correct: float
    avg_confidence_when_wrong: float
    calibration_drift: float  # 正数 = 过度自信，负数 = 信心不足
    recent_accuracy: float  # 最近 10 场
    market_breakdown: dict[str, dict[str, float]]  # 按市场类型的分解


class PostMatchVerifier:
    """赛后自动验证器：检查已完赛的比赛，比对预测与实际结果。"""

    def __init__(self, database_path: str = "football_system.db") -> None:
        self._database_path = database_path

    def verify_pending_predictions(self) -> list[VerificationResult]:
        """查找所有 PENDING 预测，尝试用已完赛的 match_schedule 验证。

        调用时机：每次 sync_before_prediction 之后。
        返回：成功验证的结果列表。
        """
        import duckdb

        results: list[VerificationResult] = []
        try:
            with duckdb.connect(self._database_path) as conn:
                # 查找 PENDING 预测对应的已完赛比赛
                rows = conn.execute(
                    """
                    SELECT
                        p.prediction_id,
                        p.match_id,
                        p.home_team,
                        p.away_team,
                        p.model_predicted_outcome,
                        p.model_predicted_probability,
                        p.value_candidate_outcome,
                        p.odds_at_prediction,
                        p.policy_recommendation,
                        s.home_score,
                        s.away_score,
                        s.match_time
                    FROM core.fact_prediction_log p
                    LEFT JOIN core.fact_match_schedule s
                        ON p.match_id = s.match_id
                    WHERE p.status = 'PENDING'
                      AND s.status = 'FINISHED'
                      AND s.home_score IS NOT NULL
                      AND s.away_score IS NOT NULL
                    """
                ).fetchall()

                for row in rows:
                    result = self._verify_single(conn, row)
                    if result:
                        results.append(result)

                if results:
                    logger.info("Verified %d pending predictions", len(results))
            return results
        except Exception as exc:
            logger.error("Verification failed: %s", exc)
            return results

    def _verify_single(self, conn: Any, row: tuple) -> VerificationResult | None:
        """验证单条预测。"""
        (
            prediction_id, match_id, home_team, away_team,
            model_predicted_outcome, model_predicted_probability,
            value_candidate_outcome, odds_at_prediction,
            policy_recommendation,
            home_score, away_score, _match_time,
        ) = row

        actual_outcome = self._determine_outcome(home_score, away_score)
        # 使用模型预测结果（非投注建议）做正确性比对
        is_correct = self._outcomes_match(model_predicted_outcome, actual_outcome)
        # No Bet 推荐不计算投注盈亏
        profit_loss = (
            self._calc_profit_loss(
                self._outcomes_match(value_candidate_outcome, actual_outcome),
                odds_at_prediction,
            )
            if value_candidate_outcome and not self._is_no_bet(policy_recommendation)
            else None
        )

        now = utc_now()
        linked_bets: list[tuple] = []
        bets_detached = False
        ledger_transaction_started = False
        try:
            linked_bets = conn.execute(
                """
                SELECT bet_id, outcome_bet, result
                FROM core.fact_bet_ledger
                WHERE prediction_id = ?
                """,
                [prediction_id],
            ).fetchall()
            if linked_bets:
                # DuckDB 无法在同一事务内先更新子表外键再更新父表；先自动提交解绑，
                # 父表更新后再用独立事务恢复并结算账本。
                conn.execute(
                    "UPDATE core.fact_bet_ledger SET prediction_id = NULL "
                    "WHERE prediction_id = ?",
                    [prediction_id],
                )
                bets_detached = True
            conn.execute(
                """
                UPDATE core.fact_prediction_log
                SET status = 'VERIFIED',
                    verified_at = ?,
                    actual_home_score = ?,
                    actual_away_score = ?,
                    actual_outcome = ?,
                    is_verified = TRUE,
                    is_correct = ?,
                    profit_loss_unit = ?,
                    verified_by = 'auto',
                    updated_at = ?
                WHERE prediction_id = ?
                """,
                [
                    now, home_score, away_score, actual_outcome,
                    is_correct, profit_loss, now, prediction_id,
                ],
            )

            # 按每笔实际投注选项结算，不能复用模型预测是否命中。
            if linked_bets:
                conn.execute("BEGIN TRANSACTION")
                ledger_transaction_started = True
            for bet_id, outcome_bet, result in linked_bets:
                if str(result or "").upper() != "PENDING":
                    conn.execute(
                        "UPDATE core.fact_bet_ledger SET prediction_id = ? "
                        "WHERE bet_id = ?",
                        [prediction_id, bet_id],
                    )
                    continue
                bet_won = self._outcomes_match(outcome_bet, actual_outcome)
                conn.execute(
                    """
                    UPDATE core.fact_bet_ledger
                    SET prediction_id = ?,
                        result = ?,
                        actual_return = CASE WHEN ? THEN stake_amount * odds_at_bet ELSE 0.0 END,
                        net_profit = CASE WHEN ? THEN stake_amount * (odds_at_bet - 1.0) ELSE -stake_amount END,
                        verified_at = ?,
                        updated_at = ?
                    WHERE bet_id = ?
                    """,
                    [
                        prediction_id,
                        "WIN" if bet_won else "LOSS",
                        bet_won,
                        bet_won,
                        now,
                        now,
                        bet_id,
                    ],
                )
            if ledger_transaction_started:
                conn.execute("COMMIT")
                ledger_transaction_started = False
            bets_detached = False
        except Exception as exc:
            if ledger_transaction_started:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    logger.exception("Failed to roll back verification transaction")
            if bets_detached:
                try:
                    conn.executemany(
                        "UPDATE core.fact_bet_ledger SET prediction_id = ? "
                        "WHERE bet_id = ?",
                        [[prediction_id, bet_id] for bet_id, _outcome, _result in linked_bets],
                    )
                except Exception:
                    logger.exception("Failed to restore detached bet ledger links")
            logger.error("Failed to update verification for %s: %s", prediction_id, exc)
            return None

        logger.info(
            "Verified %s: %s vs %s (%d-%d), predicted=%s actual=%s correct=%s",
            prediction_id, home_team, away_team,
            home_score, away_score, model_predicted_outcome, actual_outcome, is_correct,
        )
        return VerificationResult(
            prediction_id=prediction_id,
            match_id=match_id,
            home_team=home_team or "",
            away_team=away_team or "",
            predicted_outcome=model_predicted_outcome or "",
            actual_home_score=home_score or 0,
            actual_away_score=away_score or 0,
            actual_outcome=actual_outcome,
            is_correct=is_correct,
            predicted_probability=model_predicted_probability or 0.0,
            odds_at_prediction=odds_at_prediction or 0.0,
            profit_loss_unit=profit_loss,
            verified_at=now,
        )

    @staticmethod
    def _determine_outcome(home_score: int, away_score: int) -> str:
        if home_score > away_score:
            return "home"
        if away_score > home_score:
            return "away"
        return "draw"

    @staticmethod
    def _outcomes_match(left: object, right: object) -> bool:
        return str(left or "").strip().upper() == str(right or "").strip().upper()

    @staticmethod
    def _is_no_bet(recommendation: object) -> bool:
        return str(recommendation or "").strip().lower() == "no_bet"

    @staticmethod
    def _calc_profit_loss(is_correct: bool, odds: float) -> float:
        if not is_correct:
            return -1.0
        return odds - 1.0

    def get_performance_summary(self) -> PerformanceSummary:
        """获取当前预测绩效汇总。"""
        import duckdb

        try:
            with duckdb.connect(self._database_path) as conn:
                # 整体指标
                row = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN is_verified THEN 1 ELSE 0 END) AS verified,
                        SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) AS correct,
                        ROUND(SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) * 1.0
                            / NULLIF(SUM(CASE WHEN is_verified THEN 1 ELSE 0 END), 0), 4) AS accuracy,
                        ROUND(AVG(CASE WHEN is_verified THEN predicted_probability END), 4) AS avg_conf,
                        ROUND(AVG(CASE WHEN is_verified AND is_correct THEN predicted_probability END), 4) AS avg_conf_win,
                        ROUND(AVG(CASE WHEN is_verified AND NOT is_correct THEN predicted_probability END), 4) AS avg_conf_loss
                    FROM core.fact_prediction_log
                    """
                ).fetchone()

                # 投注汇总
                bet_row = conn.execute(
                    "SELECT * FROM core.view_bet_performance"
                ).fetchone()

                # 最近 10 场准确率
                recent_row = conn.execute(
                    """
                    SELECT
                        SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) * 1.0
                        / NULLIF(COUNT(*), 0) AS recent_accuracy
                    FROM (
                        SELECT is_correct FROM core.fact_prediction_log
                        WHERE is_verified
                        ORDER BY verified_at DESC
                        LIMIT 10
                    )
                    """
                ).fetchone()

                # 按市场类型分解
                market_rows = conn.execute(
                    "SELECT * FROM core.view_prediction_quality"
                ).fetchall()

                market_breakdown = {}
                for mr in market_rows:
                    market_breakdown[mr[0]] = {
                        "total": mr[1],
                        "verified": mr[2],
                        "correct": mr[3],
                        "accuracy": mr[4] or 0.0,
                    }

                avg_conf = (row[4] or 0.0)
                avg_conf_win = (row[5] or avg_conf)
                avg_conf_loss = (row[6] or avg_conf)
                calibration_drift = avg_conf - (row[3] or 0.0)

                return PerformanceSummary(
                    total_predictions=row[0] or 0,
                    verified_count=row[1] or 0,
                    correct_count=row[2] or 0,
                    accuracy=row[3] or 0.0,
                    total_bets=int(bet_row[0]) if bet_row and bet_row[0] is not None else 0,
                    total_staked=float(bet_row[5]) if bet_row and bet_row[5] is not None else 0.0,
                    total_returned=float(bet_row[6]) if bet_row and bet_row[6] is not None else 0.0,
                    total_profit=float(bet_row[7]) if bet_row and bet_row[7] is not None else 0.0,
                    roi=float(bet_row[8]) if bet_row and bet_row[8] is not None else 0.0,
                    win_rate=float(bet_row[9]) if bet_row and bet_row[9] is not None else 0.0,
                    avg_confidence=avg_conf,
                    avg_confidence_when_correct=avg_conf_win,
                    avg_confidence_when_wrong=avg_conf_loss,
                    calibration_drift=calibration_drift,
                    recent_accuracy=float(recent_row[0]) if recent_row and recent_row[0] else 0.0,
                    market_breakdown=market_breakdown,
                )
        except Exception as exc:
            logger.error("Failed to get performance summary: %s", exc)
            return PerformanceSummary(
                total_predictions=0, verified_count=0, correct_count=0,
                accuracy=0.0, total_bets=0, total_staked=0.0, total_returned=0.0,
                total_profit=0.0, roi=0.0, win_rate=0.0,
                avg_confidence=0.0, avg_confidence_when_correct=0.0,
                avg_confidence_when_wrong=0.0, calibration_drift=0.0,
                recent_accuracy=0.0, market_breakdown={},
            )


# ============================================================
# 投注账本
# ============================================================

@dataclass(frozen=True)
class BetRecord:
    bet_id: str
    prediction_id: str
    match_id: str
    home_team: str
    away_team: str
    stake_amount: float
    odds_at_bet: float
    outcome_bet: str
    market_type: str
    result: str
    net_profit: float
    bet_time: datetime


class BetLedger:
    """投注账本管理器。

    安全注意：
    - 投注金额仅在运行时通过此方法录入，不写入代码或配置文件
    - 所有数据存储在本地 DuckDB，不联网
    - 查询时注意脱敏输出
    """

    def __init__(self, database_path: str = "football_system.db") -> None:
        self._database_path = database_path

    def record_bet(
        self,
        prediction_id: str,
        match_id: str,
        home_team: str,
        away_team: str,
        stake_amount: float,
        outcome_bet: str,
        odds_at_bet: float,
        market_type: str = "SPF",
        notes: str = "",
        predicted_prob: float | None = None,
        expected_value: float | None = None,
    ) -> str:
        """记录一笔投注。返回 bet_id。

        Args:
            prediction_id: 关联的预测日志 ID
            match_id: 比赛 ID
            home_team: 主队名
            away_team: 客队名
            stake_amount: 投注金额（本币）
            outcome_bet: 投注选项（home/draw/away）
            odds_at_bet: 投注时的赔率
            market_type: 玩法（SPF/RQSPF/CRS/TTG/HAFU）
            notes: 备注
            predicted_prob: 预测概率
            expected_value: 期望价值
        """
        import duckdb

        bet_id = f"BET_{uuid.uuid4().hex[:12]}"
        try:
            with duckdb.connect(self._database_path) as conn:
                conn.execute(
                    """
                    INSERT INTO core.fact_bet_ledger (
                        bet_id, prediction_id, match_id,
                        home_team, away_team, bet_time,
                        stake_amount, market_type, outcome_bet,
                        odds_at_bet, predicted_prob, expected_value,
                        result, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
                    """,
                    [
                        bet_id, prediction_id, match_id,
                        home_team, away_team, utc_now(),
                        stake_amount, market_type, outcome_bet,
                        odds_at_bet, predicted_prob, expected_value,
                        notes,
                    ],
                )
            logger.info("Bet recorded: %s on %s vs %s (%.2f @ %.2f)",
                         bet_id, home_team, away_team, stake_amount, odds_at_bet)
            return bet_id
        except Exception as exc:
            logger.error("Failed to record bet: %s", exc)
            raise

    def get_open_bets(self) -> list[BetRecord]:
        """获取所有未结算的投注。"""
        return self._query_bets("WHERE result = 'PENDING'")

    def get_settled_bets(self, limit: int = 50) -> list[BetRecord]:
        """获取已结算的投注。"""
        return self._query_bets("WHERE result != 'PENDING' ORDER BY verified_at DESC LIMIT ?", [limit])

    def get_all_bets(self) -> list[BetRecord]:
        """获取所有投注记录。"""
        return self._query_bets("")

    def _query_bets(self, where_clause: str, params: list | None = None) -> list[BetRecord]:
        import duckdb

        try:
            with duckdb.connect(self._database_path) as conn:
                rows = conn.execute(
                    f"""
                    SELECT bet_id, prediction_id, match_id, home_team, away_team,
                           stake_amount, odds_at_bet, outcome_bet, market_type,
                           result, net_profit, bet_time
                    FROM core.fact_bet_ledger
                    {where_clause}
                    """,
                    params or [],
                ).fetchall()
                return [
                    BetRecord(
                        bet_id=r[0], prediction_id=r[1] or "", match_id=r[2],
                        home_team=r[3] or "", away_team=r[4] or "",
                        stake_amount=r[5], odds_at_bet=r[6], outcome_bet=r[7] or "",
                        market_type=r[8] or "SPF", result=r[9] or "PENDING",
                        net_profit=r[10] or 0.0,
                        bet_time=r[11] if isinstance(r[11], datetime) else utc_now(),
                    )
                    for r in rows
                ]
        except Exception as exc:
            logger.error("Failed to query bet ledger: %s", exc)
            return []

    def get_performance_text(self) -> str:
        """生成投注绩效文本摘要（用于 LLM 报告）。"""
        import duckdb

        try:
            with duckdb.connect(self._database_path) as conn:
                row = conn.execute("SELECT * FROM core.view_bet_performance").fetchone()
                if not row or row[0] == 0:
                    return "暂无投注记录。"

                return (
                    f"投注总数: {row[0]}, 胜: {row[1]}, 负: {row[2]}, 待定: {row[3]}\n"
                    f"胜率: {row[4] or 0:.1%}, 结算胜率: {row[13] or 0:.1%}\n"
                    f"总投注: {row[5] or 0:.2f}, 总返还: {row[6] or 0:.2f}, "
                    f"净利润: {row[7] or 0:.2f}, ROI: {row[8] or 0:.1%}"
                )
        except Exception:
            return "投注绩效数据不可用。"
