from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .probability_engine import ProbabilityEngine


@dataclass(frozen=True)
class CalibrationMetrics:
    brier_score: float
    log_loss: float
    accuracy: float
    sample_size: int
    no_bet_samples: int = 0
    no_bet_roi: float | None = None
    bucket_errors: dict[str, float] = field(default_factory=dict)
    market_sample_counts: dict[str, int] = field(default_factory=dict)


class CalibrationEngine:
    def evaluate_1x2(
        self,
        probability_rows: list[dict[str, float]],
        actual_outcomes: list[str],
    ) -> CalibrationMetrics:
        if len(probability_rows) != len(actual_outcomes):
            raise ValueError("Probability rows and outcomes must have the same length.")
        if not probability_rows:
            raise ValueError("At least one sample is required.")

        brier_total = 0.0
        log_total = 0.0
        hits = 0
        outcomes = ("home", "draw", "away")

        for probs, actual in zip(probability_rows, actual_outcomes, strict=True):
            if actual not in outcomes:
                raise ValueError(f"Unsupported outcome: {actual}")
            prediction = max(outcomes, key=lambda outcome: probs.get(outcome, 0.0))
            hits += int(prediction == actual)
            for outcome in outcomes:
                target = 1.0 if outcome == actual else 0.0
                probability = min(0.999999, max(0.000001, probs.get(outcome, 0.0)))
                brier_total += (probability - target) ** 2
            log_total += -math.log(min(0.999999, max(0.000001, probs.get(actual, 0.0))))

        n = len(actual_outcomes)
        return CalibrationMetrics(
            brier_score=brier_total / n,
            log_loss=log_total / n,
            accuracy=hits / n,
            sample_size=n,
        )

    def evaluate_with_buckets(
        self,
        probability_rows: list[dict[str, float]],
        actual_outcomes: list[str],
        num_buckets: int = 5,
    ) -> CalibrationMetrics:
        """评估并返回分桶校准误差。

        将预测概率分成 num_buckets 个等距区间，计算每个区间内
        预测均值与实际命中率的偏差。
        """
        base = self.evaluate_1x2(probability_rows, actual_outcomes)
        if base.sample_size < num_buckets * 2:
            return base

        bucket_errors: dict[str, float] = {}
        for outcome in ("home", "draw", "away"):
            paired = [
                (probs.get(outcome, 0.0), 1.0 if actual == outcome else 0.0)
                for probs, actual in zip(probability_rows, actual_outcomes, strict=True)
            ]
            paired.sort(key=lambda x: x[0])

            bucket_size = max(1, len(paired) // num_buckets)
            for b in range(num_buckets):
                start = b * bucket_size
                end = start + bucket_size if b < num_buckets - 1 else len(paired)
                segment = paired[start:end]
                if not segment:
                    continue
                avg_prob = sum(p for p, _ in segment) / len(segment)
                actual_rate = sum(a for _, a in segment) / len(segment)
                bucket_errors[
                    f"{outcome}_bucket_{b}_{avg_prob:.2f}"
                ] = round(avg_prob - actual_rate, 4)

        return CalibrationMetrics(
            brier_score=base.brier_score,
            log_loss=base.log_loss,
            accuracy=base.accuracy,
            sample_size=base.sample_size,
            bucket_errors=bucket_errors,
        )

    def walk_forward_calibrate(
        self,
        matches: list[Any],
        min_train_samples: int = 10,
    ) -> CalibrationMetrics:
        """按时间滚动训练和验证（walk-forward）。

        不得随机切分：按 kickoff_time 排序，逐场扩展训练集，
        每次预测下一场，累积评估。
        样本不足时返回标记"未校准"的结果。
        """
        ordered = sorted(matches, key=lambda m: m.kickoff_time)
        if len(ordered) < min_train_samples + 1:
            return CalibrationMetrics(
                brier_score=0.0,
                log_loss=0.0,
                accuracy=0.0,
                sample_size=len(ordered),
                no_bet_samples=0,
                bucket_errors={"__uncalibrated__": 0.0},
            )

        probability_rows: list[dict[str, float]] = []
        actuals: list[str] = []

        for i in range(min_train_samples, len(ordered)):
            train_set = ordered[:i]
            test_match = ordered[i]

            # 在训练集上做网格搜索选最优权重
            best_weight = self._find_best_weight(train_set)

            engine = ProbabilityEngine(poisson_weight=best_weight)
            probs = engine.predict(test_match.features)
            probability_rows.append(probs.outcome_probabilities())
            actuals.append(test_match.actual_outcome)

        return self.evaluate_with_buckets(probability_rows, actuals)

    def _find_best_weight(
        self,
        train_matches: list[Any],
        weight_start: float = 0.50,
        weight_end: float = 0.90,
        weight_step: float = 0.05,
    ) -> float:
        best_weight = (weight_start + weight_end) / 2
        best_brier = float("inf")

        current = weight_start
        while current <= weight_end + 1e-6:
            engine = ProbabilityEngine(poisson_weight=current)
            prob_rows = []
            actuals = []
            for match in train_matches:
                probs = engine.predict(match.features)
                prob_rows.append(probs.outcome_probabilities())
                actuals.append(match.actual_outcome)

            metrics = self.evaluate_1x2(prob_rows, actuals)
            if metrics.brier_score < best_brier:
                best_brier = metrics.brier_score
                best_weight = current

            current += weight_step

        return best_weight


@dataclass(frozen=True)
class GridSearchResult:
    best_poisson_weight: float
    best_metrics: CalibrationMetrics
    all_results: list[tuple[float, CalibrationMetrics]]


class GridSearchCalibrator:
    def __init__(self, calibration_engine: CalibrationEngine | None = None) -> None:
        self.calibration_engine = calibration_engine or CalibrationEngine()

    def optimize_poisson_weight(
        self,
        matches: list[Any],
        weight_start: float = 0.50,
        weight_end: float = 0.90,
        weight_step: float = 0.05,
    ) -> GridSearchResult:
        if not matches:
            raise ValueError("At least one sample is required for calibration.")
        if weight_step <= 0:
            raise ValueError("weight_step must be greater than 0.")
        if weight_start > weight_end:
            raise ValueError("weight_start must be less than or equal to weight_end.")

        results: list[tuple[float, CalibrationMetrics]] = []
        best_weight = weight_start
        best_brier = float("inf")
        best_metrics: CalibrationMetrics | None = None

        current_weight = weight_start
        while current_weight <= weight_end + 1e-6:
            engine = ProbabilityEngine(poisson_weight=current_weight)

            probability_rows = []
            actuals = []
            for match in matches:
                probs = engine.predict(match.features)
                probability_rows.append(probs.outcome_probabilities())
                actuals.append(match.actual_outcome)

            metrics = self.calibration_engine.evaluate_1x2(probability_rows, actuals)
            results.append((current_weight, metrics))

            if metrics.brier_score < best_brier:
                best_brier = metrics.brier_score
                best_weight = current_weight
                best_metrics = metrics

            current_weight += weight_step

        if best_metrics is None:
            raise RuntimeError("Grid search failed to produce any calibration metrics.")
        return GridSearchResult(
            best_poisson_weight=best_weight,
            best_metrics=best_metrics,
            all_results=results,
        )
