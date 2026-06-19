from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

from .calibration_engine import CalibrationEngine, CalibrationMetrics
from .feature_builder import DuckDBFeatureBuilder
from .models import MatchFeatures, MatchRequest
from .no_bet_policy import NoBetPolicy
from .odds_value_engine import OddsValueEngine
from .probability_engine import ProbabilityEngine
from .query_tools import DuckDBStatsQueryTool


@dataclass(frozen=True)
class RecordedBet:
    stake_amount: float
    odds_at_bet: float
    result: str
    net_profit: float


@dataclass(frozen=True)
class HistoricalMatch:
    kickoff_time: datetime
    features: MatchFeatures
    actual_outcome: str
    closing_odds_1x2: dict[str, float] | None = None
    recorded_bets: tuple[RecordedBet, ...] = ()


@dataclass(frozen=True)
class BacktestResult:
    metrics: CalibrationMetrics
    bets: int
    roi: float
    max_drawdown: float
    average_odds: float
    staged_win_rate: float = 0.0
    equity_curve: list[float] = field(default_factory=list)
    no_bet_samples: int = 0
    no_bet_reasons: dict[str, int] = field(default_factory=dict)
    feature_version: str = ""
    parameter_version: str = ""
    data_sources: list[str] = field(default_factory=list)


def format_backtest_summary(result: BacktestResult) -> str:
    reason_lines = [
        f"- {reason}: {count}"
        for reason, count in sorted(result.no_bet_reasons.items())
    ]
    if not reason_lines:
        reason_lines = ["- none: 0"]

    lines = [
        "# Walk-forward 回测摘要",
        "",
        f"样本数: {result.metrics.sample_size}",
        f"投注数: {result.bets}",
        f"No Bet: {result.no_bet_samples}",
        f"ROI: {result.roi:.2%}",
        f"投注命中率: {result.staged_win_rate:.2%}",
        f"最大回撤: {result.max_drawdown:.2f}",
        f"平均赔率: {result.average_odds:.2f}",
        f"Brier Score: {result.metrics.brier_score:.6f}",
        f"LogLoss: {result.metrics.log_loss:.6f}",
        f"命中率: {result.metrics.accuracy:.2%}",
    ]

    if result.feature_version:
        lines.append(f"特征版本: {result.feature_version}")
    if result.parameter_version:
        lines.append(f"参数版本: {result.parameter_version}")
    if result.data_sources:
        lines.append(f"数据来源: {', '.join(result.data_sources)}")

    if result.metrics.no_bet_roi is not None:
        lines.append(f"No Bet 后 ROI: {result.metrics.no_bet_roi:.2%}")

    if result.metrics.market_sample_counts:
        lines.append("")
        lines.append("各玩法样本数:")
        for market, count in sorted(result.metrics.market_sample_counts.items()):
            lines.append(f"  - {market}: {count}")

    if result.metrics.bucket_errors:
        if "__uncalibrated__" in result.metrics.bucket_errors:
            lines.append("")
            lines.append("状态: 样本不足，未校准")
        else:
            lines.append("")
            lines.append("分桶校准误差 (预测均值 - 实际命中率):")
            for bucket, error in sorted(result.metrics.bucket_errors.items()):
                lines.append(f"  - {bucket}: {error:+.4f}")

    lines.append("")
    lines.append("No Bet 原因:")
    lines.extend(reason_lines)

    return "\n".join(lines)


class DuckDBBacktestMatchLoader:
    def __init__(
        self,
        database_path: str = "football_system.db",
        query_tool: DuckDBStatsQueryTool | None = None,
        feature_builder: DuckDBFeatureBuilder | None = None,
        max_historical_data_age_minutes: int = 30,
    ) -> None:
        self.query_tool = query_tool or DuckDBStatsQueryTool(
            database_path=database_path
        )
        self.feature_builder = feature_builder or DuckDBFeatureBuilder(self.query_tool)
        self.max_historical_data_age_minutes = max_historical_data_age_minutes

    def load_walk_forward_matches(
        self,
        limit: int = 50,
        match_id_prefix: str = "SQLITE_MATCH_",
    ) -> list[HistoricalMatch]:
        safe_limit = max(1, int(limit))
        prefix_literal = self._sql_literal(f"{match_id_prefix}%")
        result = self.query_tool.query_match_stats(f"""
            SELECT
                v.match_id,
                v.match_time,
                s.home_score,
                s.away_score
            FROM core.view_llm_match_prediction_base v
            JOIN core.fact_match_schedule s ON v.match_id = s.match_id
            WHERE s.home_score IS NOT NULL
              AND s.away_score IS NOT NULL
              AND v.latest_home_odds IS NOT NULL
              AND v.latest_draw_odds IS NOT NULL
              AND v.latest_away_odds IS NOT NULL
              AND v.match_id LIKE {prefix_literal}
            ORDER BY v.match_time ASC
            LIMIT {safe_limit}
            """)

        matches: list[HistoricalMatch] = []
        for row in result.rows:
            match_id = str(row["match_id"])
            kickoff_time = self._datetime(row["match_time"])
            features = self.feature_builder.build(
                request=MatchRequest(query=match_id, match_id=match_id),
                sync_results=[],
            )
            features = self._with_historical_data_quality(features, kickoff_time)
            matches.append(
                HistoricalMatch(
                    kickoff_time=kickoff_time,
                    features=features,
                    actual_outcome=self._actual_outcome(row),
                    closing_odds_1x2=features.odds_1x2,
                    recorded_bets=self._load_recorded_bets(match_id),
                )
            )
        return matches

    def _load_recorded_bets(self, match_id: str) -> tuple[RecordedBet, ...]:
        match_literal = self._sql_literal(match_id)
        result = self.query_tool.query_match_stats(f"""
            SELECT stake_amount, odds_at_bet, result, net_profit
            FROM core.fact_bet_ledger
            WHERE match_id = {match_literal}
              AND result IN ('WIN', 'LOSS')
            ORDER BY bet_time ASC
            """)
        return tuple(
            RecordedBet(
                stake_amount=float(row["stake_amount"]),
                odds_at_bet=float(row["odds_at_bet"]),
                result=str(row["result"]),
                net_profit=float(row["net_profit"]),
            )
            for row in result.rows
        )

    def _with_historical_data_quality(
        self,
        features: MatchFeatures,
        kickoff_time: datetime,
    ) -> MatchFeatures:
        context = dict(features.context)
        context["realtime_no_bet_data_quality_flag"] = bool(
            context.get("no_bet_data_quality_flag")
        )

        data_age_minutes = int(
            (
                self._local_naive(kickoff_time)
                - self._local_naive(features.updated_at)
            ).total_seconds()
            // 60
        )
        context["historical_critical_data_age_minutes"] = data_age_minutes
        context["no_bet_data_quality_flag"] = (
            bool(context.get("missing_team_stats_flag"))
            or bool(context.get("derived_team_stats_quality_flag"))
            or bool(context.get("missing_1x2_odds_flag"))
            or data_age_minutes < 0
            or data_age_minutes > self.max_historical_data_age_minutes
        )
        context["backtest_data_quality_tier"] = (
            "basic_only" if context.get("derived_team_stats_quality_flag") else "full"
        )
        return replace(features, context=context)

    @staticmethod
    def _actual_outcome(row: dict[str, object]) -> str:
        home_score = int(row["home_score"])
        away_score = int(row["away_score"])
        if home_score > away_score:
            return "home"
        if away_score > home_score:
            return "away"
        return "draw"

    @staticmethod
    def _datetime(value: object) -> datetime:
        if isinstance(value, datetime):
            return value
        raise ValueError("DuckDB backtest row is missing a valid match_time.")

    @staticmethod
    def _naive(value: datetime) -> datetime:
        return value.replace(tzinfo=None)

    @staticmethod
    def _local_naive(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone().replace(tzinfo=None)

    @staticmethod
    def _sql_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"


class BacktestEngine:
    def __init__(
        self,
        probability_engine: ProbabilityEngine | None = None,
        odds_value_engine: OddsValueEngine | None = None,
        no_bet_policy: NoBetPolicy | None = None,
    ) -> None:
        self.probability_engine = probability_engine or ProbabilityEngine()
        self.odds_value_engine = odds_value_engine or OddsValueEngine()
        self.calibration_engine = CalibrationEngine()
        self.no_bet_policy = no_bet_policy or NoBetPolicy()

    def run(self, matches: list[HistoricalMatch]) -> BacktestResult:
        ordered = sorted(matches, key=lambda match: match.kickoff_time)
        probability_rows: list[dict[str, float]] = []
        actuals: list[str] = []
        returns: list[float] = []
        stakes: list[float] = []
        odds_used: list[float] = []
        wins: int = 0
        no_bet_reasons: dict[str, int] = {}

        for match in ordered:
            self._assert_no_future_leakage(match)
            probabilities = self.probability_engine.predict(match.features)
            probability_rows.append(probabilities.outcome_probabilities())
            actuals.append(match.actual_outcome)

            for bet in match.recorded_bets:
                result = bet.result.strip().upper()
                if result not in {"WIN", "LOSS"} or bet.stake_amount <= 0:
                    continue
                returns.append(bet.net_profit)
                stakes.append(bet.stake_amount)
                odds_used.append(bet.odds_at_bet)
                wins += int(result == "WIN")

            if self._has_no_bet_data_quality_flag(match):
                self._count_no_bet(no_bet_reasons, "data_quality")
                continue
            if not match.closing_odds_1x2:
                self._count_no_bet(no_bet_reasons, "missing_closing_odds")
                continue
            try:
                assessment = self.odds_value_engine.assess_1x2(
                    match.closing_odds_1x2,
                    probabilities,
                )
            except ValueError:
                self._count_no_bet(no_bet_reasons, "invalid_closing_odds")
                continue
            if assessment.best_value:
                continue
            else:
                self._count_no_bet(no_bet_reasons, "no_value")

        metrics = self.calibration_engine.evaluate_1x2(probability_rows, actuals)
        roi = sum(returns) / sum(stakes) if stakes else 0.0
        average_odds = sum(odds_used) / len(odds_used) if odds_used else 0.0
        equity_curve = self._build_equity_curve(returns)
        return BacktestResult(
            metrics=metrics,
            bets=len(returns),
            roi=roi,
            max_drawdown=self._max_drawdown_from_curve(equity_curve),
            average_odds=average_odds,
            staged_win_rate=wins / len(returns) if returns else 0.0,
            equity_curve=equity_curve,
            no_bet_samples=sum(no_bet_reasons.values()),
            no_bet_reasons=no_bet_reasons,
            feature_version=f"poisson={self.probability_engine.poisson_weight:.2f},"
                            f"elo={self.probability_engine.elo_weight:.2f}",
            parameter_version=f"platt_a={self.no_bet_policy.platt_a},"
                              f"deviation={self.no_bet_policy.market_deviation_threshold}",
        )

    @staticmethod
    def _assert_no_future_leakage(match: HistoricalMatch) -> None:
        feature_time = DuckDBBacktestMatchLoader._local_naive(match.features.updated_at)
        kickoff_time = DuckDBBacktestMatchLoader._local_naive(match.kickoff_time)
        if feature_time > kickoff_time:
            raise ValueError(
                "Feature timestamp is after kickoff; possible future data leakage."
            )

    @staticmethod
    def _has_no_bet_data_quality_flag(match: HistoricalMatch) -> bool:
        return bool(match.features.context.get("no_bet_data_quality_flag"))

    @staticmethod
    def _count_no_bet(reasons: dict[str, int], reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    @staticmethod
    def _build_equity_curve(returns: list[float]) -> list[float]:
        curve: list[float] = []
        equity = 0.0
        for value in returns:
            equity += value
            curve.append(equity)
        return curve

    @staticmethod
    def _max_drawdown_from_curve(equity_curve: list[float]) -> float:
        peak = 0.0
        max_dd = 0.0
        for equity in equity_curve:
            peak = max(peak, equity)
            max_dd = min(max_dd, equity - peak)
        return abs(max_dd)
