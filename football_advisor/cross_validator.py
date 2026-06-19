from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, pstdev
from typing import Any

from .multi_source_coordinator import MultiSourceResult, SourceResult


@dataclass(frozen=True)
class CrossValidationResult:
    score_consensus: str
    score_flagged: bool
    odds_deviation_sources: list[str] = field(default_factory=list)
    invalid_range_sources: list[str] = field(default_factory=list)
    duplicate_record_sources: list[str] = field(default_factory=list)
    anomaly_sources: list[str] = field(default_factory=list)
    lineup_uncertain_count: int = 0
    injury_unconfirmed_count: int = 0
    multi_source_coverage_flag: str = "single_source"
    cross_check_failed: bool = False
    validation_details: dict[str, Any] = field(default_factory=dict)

    @property
    def has_critical_issues(self) -> bool:
        return (
            self.score_flagged
            or bool(self.invalid_range_sources)
            or bool(self.duplicate_record_sources)
            or bool(self.anomaly_sources)
            or self.cross_check_failed
        )


class CrossValidator:
    """5 项交叉验证规则引擎。

    所有数据源结果并行输入，无先后依赖。
    """

    SCORE_CHECK_THRESHOLD = 0.8
    ODDS_DEVIATION_THRESHOLD = 0.05
    INJURY_MIN_SOURCES = 2
    MAX_ODDS = 1000.0
    MIN_ODDS = 1.01

    def validate(self, multi_result: MultiSourceResult) -> CrossValidationResult:
        score_consensus, score_flagged = self._check_score_consensus(multi_result)
        odds_deviation_sources = self._check_odds_deviation(multi_result)
        invalid_range_sources = self._check_value_ranges(multi_result)
        duplicate_record_sources = self._check_duplicate_records(multi_result)
        anomaly_sources = self._check_anomalies(multi_result)
        lineup_uncertain_count, injury_unconfirmed_count = self._check_lineup_injury(multi_result)
        coverage_flag = self._compute_coverage_flag(multi_result)

        cross_check_failed = False
        if score_flagged:
            cross_check_failed = True
        if multi_result.all_failed:
            cross_check_failed = True
        if invalid_range_sources or duplicate_record_sources or anomaly_sources:
            cross_check_failed = True

        return CrossValidationResult(
            score_consensus=score_consensus,
            score_flagged=score_flagged,
            odds_deviation_sources=odds_deviation_sources,
            invalid_range_sources=invalid_range_sources,
            duplicate_record_sources=duplicate_record_sources,
            anomaly_sources=anomaly_sources,
            lineup_uncertain_count=lineup_uncertain_count,
            injury_unconfirmed_count=injury_unconfirmed_count,
            multi_source_coverage_flag=coverage_flag,
            cross_check_failed=cross_check_failed,
            validation_details={
                "source_count": len(multi_result.results),
                "success_count": multi_result.success_count,
                "failure_count": multi_result.failure_count,
                "skipped_count": multi_result.skipped_count,
                "coverage": multi_result.coverage_summary,
                "invalid_range_sources": invalid_range_sources,
                "duplicate_record_sources": duplicate_record_sources,
                "anomaly_sources": anomaly_sources,
                "total_elapsed_ms": multi_result.total_elapsed_ms,
            },
        )

    def _check_score_consensus(
        self,
        multi_result: MultiSourceResult,
    ) -> tuple[str, bool]:
        scores: dict[str, int] = {}
        for r in multi_result.results:
            home = r.data.get("home_score")
            away = r.data.get("away_score")
            if home is not None and away is not None:
                key = f"{home}-{away}"
                scores[key] = scores.get(key, 0) + 1

        if not scores:
            return "no_score_data", False

        max_count = max(scores.values())
        total = sum(scores.values())
        consensus_ratio = max_count / total if total > 0 else 0

        if consensus_ratio >= self.SCORE_CHECK_THRESHOLD:
            best_score = max(scores, key=lambda k: scores[k])
            return f"consensus:{best_score}", False
        elif total >= 2:
            return "score_disagreement", True
        else:
            return "single_source_score", False

    def _check_odds_deviation(
        self,
        multi_result: MultiSourceResult,
    ) -> list[str]:
        all_odds: list[tuple[str, float, float, float]] = []
        for r in multi_result.results:
            home = r.data.get("home_odds")
            draw = r.data.get("draw_odds")
            away = r.data.get("away_odds")
            if home is None or draw is None or away is None:
                continue
            if not self._valid_odds(home) or not self._valid_odds(draw) or not self._valid_odds(away):
                continue
            all_odds.append((r.provider_name, home, draw, away))

        if len(all_odds) < 2:
            return []

        avg_home = sum(o[1] for o in all_odds) / len(all_odds)
        avg_draw = sum(o[2] for o in all_odds) / len(all_odds)
        avg_away = sum(o[3] for o in all_odds) / len(all_odds)

        deviated: list[str] = []
        for provider, home, draw, away in all_odds:
            home_dev = abs(home - avg_home) / avg_home if avg_home > 0 else 0
            draw_dev = abs(draw - avg_draw) / avg_draw if avg_draw > 0 else 0
            away_dev = abs(away - avg_away) / avg_away if avg_away > 0 else 0
            max_dev = max(home_dev, draw_dev, away_dev)
            if max_dev > self.ODDS_DEVIATION_THRESHOLD:
                deviated.append(provider)

        return deviated

    def _check_lineup_injury(
        self,
        multi_result: MultiSourceResult,
    ) -> tuple[int, int]:
        lineup_sources: dict[str, set[str]] = {}
        injury_sources: dict[str, set[str]] = {}

        for r in multi_result.results:
            players = r.data.get("lineup_players", [])
            if players:
                lineup_sources[r.provider_name] = set(players)

            injured = r.data.get("injured_players", [])
            if injured:
                injury_sources[r.provider_name] = set(injured)

        all_players: set[str] = set()
        for players in lineup_sources.values():
            all_players |= players

        player_source_count: dict[str, int] = {}
        for player in all_players:
            count = sum(1 for src_players in lineup_sources.values() if player in src_players)
            player_source_count[player] = count

        lineup_uncertain = sum(1 for c in player_source_count.values() if c == 1)
        all_injuries: set[str] = set()
        for injured in injury_sources.values():
            all_injuries |= injured

        injury_source_count: dict[str, int] = {}
        for injury in all_injuries:
            count = sum(1 for src_injured in injury_sources.values() if injury in src_injured)
            injury_source_count[injury] = count

        injury_unconfirmed = sum(1 for c in injury_source_count.values() if c < self.INJURY_MIN_SOURCES)
        return lineup_uncertain, injury_unconfirmed

    def _compute_coverage_flag(self, multi_result: MultiSourceResult) -> str:
        coverage = multi_result.coverage_summary
        total_fields = len(coverage)
        if total_fields == 0:
            return "no_coverage"

        multi_source_fields = sum(1 for c in coverage.values() if c >= 2)
        single_source_fields = sum(1 for c in coverage.values() if c == 1)

        if multi_source_fields >= 3:
            return "multi_source_rich"
        elif multi_source_fields >= 1:
            return "multi_source_partial"
        elif single_source_fields > 0:
            return "single_source"
        else:
            return "no_coverage"

    def _check_value_ranges(
        self,
        multi_result: MultiSourceResult,
    ) -> list[str]:
        invalid: list[str] = []
        odds_fields = {"home_odds", "draw_odds", "away_odds"}
        probability_fields = {
            "home_probability",
            "draw_probability",
            "away_probability",
            "implied_home_probability",
            "implied_draw_probability",
            "implied_away_probability",
        }
        non_negative_fields = {
            "home_score",
            "away_score",
            "odds_count",
            "stats_count",
            "merged_count",
            "staged_count",
            "squad_count",
            "history_count",
            "bookmaker_count",
        }

        for result in multi_result.results:
            for field, value in result.data.items():
                if not isinstance(value, (int, float)):
                    continue
                if field in odds_fields and not self._valid_odds(float(value)):
                    invalid.append(f"{result.provider_name}:{field}")
                elif field in probability_fields and not 0.0 <= float(value) <= 1.0:
                    invalid.append(f"{result.provider_name}:{field}")
                elif field in non_negative_fields and float(value) < 0.0:
                    invalid.append(f"{result.provider_name}:{field}")
        return invalid

    @staticmethod
    def _check_duplicate_records(
        multi_result: MultiSourceResult,
    ) -> list[str]:
        seen: set[tuple[str, str]] = set()
        duplicates: list[str] = []
        for result in multi_result.results:
            record_id = result.data.get("provider_match_id") or result.data.get("match_id")
            if not record_id:
                continue
            key = (result.provider_name, str(record_id))
            if key in seen:
                duplicates.append(f"{result.provider_name}:{record_id}")
            seen.add(key)
        return duplicates

    @staticmethod
    def _check_anomalies(
        multi_result: MultiSourceResult,
    ) -> list[str]:
        anomalies: list[str] = []
        history_fields = {
            "home_odds_history",
            "draw_odds_history",
            "away_odds_history",
            "xg_history",
            "shots_history",
        }
        for result in multi_result.results:
            for field in history_fields:
                values = result.data.get(field)
                if not isinstance(values, list) or len(values) < 4:
                    continue
                numeric_values = [
                    float(value)
                    for value in values
                    if isinstance(value, (int, float))
                ]
                if len(numeric_values) != len(values) or len(numeric_values) < 4:
                    continue
                baseline = numeric_values[:-1]
                latest = numeric_values[-1]
                sigma = pstdev(baseline)
                if sigma == 0.0:
                    if latest != baseline[-1]:
                        anomalies.append(f"{result.provider_name}:{field}")
                    continue
                if abs(latest - mean(baseline)) >= 3 * sigma:
                    anomalies.append(f"{result.provider_name}:{field}")
        return anomalies

    @staticmethod
    def _valid_odds(value: float) -> bool:
        return CrossValidator.MIN_ODDS <= value <= CrossValidator.MAX_ODDS
