from __future__ import annotations

from .models import OddsValue, ProbabilityResult, ValueAssessment


class OddsValueEngine:
    def __init__(self, value_threshold: float = 0.05) -> None:
        self.value_threshold = value_threshold

    def assess_1x2(
        self,
        odds: dict[str, float],
        probabilities: ProbabilityResult,
    ) -> ValueAssessment:
        required = {"home", "draw", "away"}
        missing = required.difference(odds)
        if missing:
            raise ValueError(f"Missing 1x2 odds: {', '.join(sorted(missing))}")

        raw_implied = {outcome: 1.0 / float(odds[outcome]) for outcome in required}
        overround = sum(raw_implied.values())
        if overround <= 0:
            raise ValueError("Invalid odds overround")

        normalized_implied = {
            outcome: implied / overround for outcome, implied in raw_implied.items()
        }
        model_probs = probabilities.outcome_probabilities()

        values = [
            OddsValue(
                outcome=outcome,
                decimal_odds=float(odds[outcome]),
                implied_probability=normalized_implied[outcome],
                model_probability=model_probs[outcome],
                edge=model_probs[outcome] - normalized_implied[outcome],
                value=(model_probs[outcome] - normalized_implied[outcome])
                >= self.value_threshold,
            )
            for outcome in ("home", "draw", "away")
        ]
        best_value = max(values, key=lambda value: value.edge)
        if not best_value.value:
            best_value = None

        return ValueAssessment(
            values=values,
            best_value=best_value,
            overround=overround,
            threshold=self.value_threshold,
        )

    def assess_totals(
        self,
        odds: dict[str, float],
        probabilities: ProbabilityResult,
    ) -> ValueAssessment:
        return self._assess_two_way(
            odds=odds,
            model_probs={
                "over": probabilities.over_2_5,
                "under": probabilities.under_2_5,
            },
            required=("over", "under"),
            market_name="totals",
        )

    def assess_handicap(
        self,
        handicap_line: float,
        odds: dict[str, float],
        probabilities: ProbabilityResult,
    ) -> ValueAssessment:
        if handicap_line == -0.5:
            model_probs = {
                "home": probabilities.home_win,
                "away": probabilities.draw + probabilities.away_win,
            }
        elif handicap_line == 0.5:
            model_probs = {
                "home": probabilities.home_win + probabilities.draw,
                "away": probabilities.away_win,
            }
        else:
            raise ValueError(
                "Only +/-0.5 handicap lines are supported by the current value engine."
            )
        return self._assess_two_way(
            odds=odds,
            model_probs=model_probs,
            required=("home", "away"),
            market_name="handicap",
        )

    def assess_handicap_1x2(
        self,
        handicap_line: float,
        odds: dict[str, float],
        probabilities: ProbabilityResult,
    ) -> ValueAssessment:
        required = ("home", "draw", "away")
        missing = set(required).difference(odds)
        if missing:
            raise ValueError(
                f"Missing handicap 1x2 odds: {', '.join(sorted(missing))}"
            )
        if not probabilities.score_probabilities:
            raise ValueError("Score probabilities are required for handicap 1x2.")

        model_probs = dict.fromkeys(required, 0.0)
        for (home_goals, away_goals), probability in probabilities.score_probabilities.items():
            adjusted_home_goals = home_goals + handicap_line
            if adjusted_home_goals > away_goals:
                model_probs["home"] += probability
            elif adjusted_home_goals == away_goals:
                model_probs["draw"] += probability
            else:
                model_probs["away"] += probability

        return self._assess_three_way(
            odds=odds,
            model_probs=model_probs,
            required=required,
            market_name="handicap 1x2",
        )

    def assess_correct_score(
        self,
        odds: dict[str, float],
        probabilities: ProbabilityResult,
    ) -> ValueAssessment:
        if not probabilities.score_probabilities:
            raise ValueError("Score probabilities are required for correct score.")
        exact_scores = {key for key in odds if ":" in key}
        model_probs: dict[str, float] = {}
        for outcome in odds:
            if outcome in {"胜其它", "平其它", "负其它"}:
                target = outcome[0]
                model_probs[outcome] = sum(
                    probability
                    for (home_goals, away_goals), probability in probabilities.score_probabilities.items()
                    if self._score_result_label(home_goals, away_goals) == target
                    and f"{home_goals}:{away_goals}" not in exact_scores
                )
                continue
            try:
                home_goals, away_goals = (int(value) for value in outcome.split(":", 1))
            except (ValueError, TypeError):
                model_probs[outcome] = 0.0
                continue
            model_probs[outcome] = probabilities.score_probabilities.get(
                (home_goals, away_goals), 0.0
            )
        return self._assess_multiway(odds, model_probs, "correct score")

    def assess_total_goals(
        self,
        odds: dict[str, float],
        probabilities: ProbabilityResult,
    ) -> ValueAssessment:
        return self._assess_multiway(
            odds,
            probabilities.total_goals_probabilities,
            "total goals",
        )

    def assess_half_full(
        self,
        odds: dict[str, float],
        probabilities: ProbabilityResult,
    ) -> ValueAssessment:
        return self._assess_multiway(
            odds,
            probabilities.half_full_probabilities,
            "half full",
        )

    def _assess_multiway(
        self,
        odds: dict[str, float],
        model_probs: dict[str, float],
        market_name: str,
    ) -> ValueAssessment:
        if not odds:
            raise ValueError(f"Missing {market_name} odds")
        missing = set(odds).difference(model_probs)
        if missing:
            raise ValueError(
                f"Missing {market_name} probabilities: {', '.join(sorted(missing))}"
            )
        outcomes = tuple(odds)
        raw_implied = {outcome: 1.0 / float(odds[outcome]) for outcome in outcomes}
        overround = sum(raw_implied.values())
        if overround <= 0:
            raise ValueError(f"Invalid {market_name} overround")
        normalized_implied = {
            outcome: implied / overround for outcome, implied in raw_implied.items()
        }
        values = [
            OddsValue(
                outcome=outcome,
                decimal_odds=float(odds[outcome]),
                implied_probability=normalized_implied[outcome],
                model_probability=model_probs[outcome],
                edge=model_probs[outcome] - normalized_implied[outcome],
                value=(model_probs[outcome] - normalized_implied[outcome])
                >= self.value_threshold,
            )
            for outcome in outcomes
        ]
        best_value = max(values, key=lambda value: value.edge)
        if not best_value.value:
            best_value = None
        return ValueAssessment(
            values=values,
            best_value=best_value,
            overround=overround,
            threshold=self.value_threshold,
        )

    @staticmethod
    def _score_result_label(home_goals: int, away_goals: int) -> str:
        if home_goals > away_goals:
            return "胜"
        if home_goals < away_goals:
            return "负"
        return "平"

    def _assess_two_way(
        self,
        odds: dict[str, float],
        model_probs: dict[str, float],
        required: tuple[str, str],
        market_name: str,
    ) -> ValueAssessment:
        missing = set(required).difference(odds)
        if missing:
            raise ValueError(
                f"Missing {market_name} odds: {', '.join(sorted(missing))}"
            )

        raw_implied = {outcome: 1.0 / float(odds[outcome]) for outcome in required}
        overround = sum(raw_implied.values())
        if overround <= 0:
            raise ValueError(f"Invalid {market_name} overround")

        normalized_implied = {
            outcome: implied / overround for outcome, implied in raw_implied.items()
        }
        values = [
            OddsValue(
                outcome=outcome,
                decimal_odds=float(odds[outcome]),
                implied_probability=normalized_implied[outcome],
                model_probability=model_probs[outcome],
                edge=model_probs[outcome] - normalized_implied[outcome],
                value=(model_probs[outcome] - normalized_implied[outcome])
                >= self.value_threshold,
            )
            for outcome in required
        ]
        best_value = max(values, key=lambda value: value.edge)
        if not best_value.value:
            best_value = None
        return ValueAssessment(
            values=values,
            best_value=best_value,
            overround=overround,
            threshold=self.value_threshold,
        )

    def _assess_three_way(
        self,
        odds: dict[str, float],
        model_probs: dict[str, float],
        required: tuple[str, str, str],
        market_name: str,
    ) -> ValueAssessment:
        raw_implied = {outcome: 1.0 / float(odds[outcome]) for outcome in required}
        overround = sum(raw_implied.values())
        if overround <= 0:
            raise ValueError(f"Invalid {market_name} overround")

        normalized_implied = {
            outcome: implied / overround for outcome, implied in raw_implied.items()
        }
        values = [
            OddsValue(
                outcome=outcome,
                decimal_odds=float(odds[outcome]),
                implied_probability=normalized_implied[outcome],
                model_probability=model_probs[outcome],
                edge=model_probs[outcome] - normalized_implied[outcome],
                value=(model_probs[outcome] - normalized_implied[outcome])
                >= self.value_threshold,
            )
            for outcome in required
        ]
        best_value = max(values, key=lambda value: value.edge)
        if not best_value.value:
            best_value = None
        return ValueAssessment(
            values=values,
            best_value=best_value,
            overround=overround,
            threshold=self.value_threshold,
        )
