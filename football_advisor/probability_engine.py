from __future__ import annotations

import math

from .models import MatchFeatures, ProbabilityResult


class ProbabilityEngine:
    """Explainable first-pass model: Poisson score grid plus Elo/form blending."""

    def __init__(
        self,
        max_goals: int = 7,
        elo_scale: float = 400.0,
        poisson_weight: float = 0.72,
        form_weight_multiplier: float = 0.018,
        form_weight_max: float = 0.035,
        model_version: str = "baseline-uncalibrated",
        calibrated: bool = False,
        enable_enhanced_boosts: bool = False,
    ) -> None:
        self.max_goals = max_goals
        self.elo_scale = elo_scale
        self.poisson_weight = max(0.0, min(1.0, poisson_weight))
        self.elo_weight = 1.0 - self.poisson_weight
        self.form_weight_multiplier = form_weight_multiplier
        self.form_weight_max = form_weight_max
        self.model_version = model_version
        self.calibrated = calibrated
        self.enable_enhanced_boosts = enable_enhanced_boosts

    def predict(self, features: MatchFeatures) -> ProbabilityResult:
        home_xg = self._expected_home_goals(features)
        away_xg = self._expected_away_goals(features)
        score_probs = self._score_grid(home_xg, away_xg)

        poisson_home = sum(p for (h, a), p in score_probs.items() if h > a)
        poisson_draw = sum(p for (h, a), p in score_probs.items() if h == a)
        poisson_away = sum(p for (h, a), p in score_probs.items() if h < a)

        elo_home, elo_draw, elo_away = self._elo_1x2(features)
        form_home, form_away = self._form_adjustment(features)

        home_win = (
            self.poisson_weight * poisson_home + self.elo_weight * elo_home + form_home
        )
        away_win = (
            self.poisson_weight * poisson_away + self.elo_weight * elo_away + form_away
        )
        draw = self.poisson_weight * poisson_draw + self.elo_weight * elo_draw
        home_win, draw, away_win = self._normalize(home_win, draw, away_win)

        over_2_5 = sum(p for (h, a), p in score_probs.items() if h + a > 2.5)
        under_2_5 = 1.0 - over_2_5
        likely_scores = sorted(
            ((f"{h}-{a}", p) for (h, a), p in score_probs.items()),
            key=lambda item: item[1],
            reverse=True,
        )[:5]
        total_goals_probabilities = self._total_goals_probabilities(score_probs)
        half_full_probabilities = self._half_full_probabilities(home_xg, away_xg)

        return ProbabilityResult(
            home_win=home_win,
            draw=draw,
            away_win=away_win,
            over_2_5=over_2_5,
            under_2_5=under_2_5,
            expected_home_goals=home_xg,
            expected_away_goals=away_xg,
            most_likely_scores=likely_scores,
            score_probabilities=score_probs,
            total_goals_probabilities=total_goals_probabilities,
            half_full_probabilities=half_full_probabilities,
        )

    @staticmethod
    def _total_goals_probabilities(
        score_probs: dict[tuple[int, int], float]
    ) -> dict[str, float]:
        probabilities = {str(goals): 0.0 for goals in range(7)}
        probabilities["7+"] = 0.0
        for (home_goals, away_goals), probability in score_probs.items():
            total = home_goals + away_goals
            key = str(total) if total <= 6 else "7+"
            probabilities[key] += probability
        return probabilities

    def _half_full_probabilities(
        self, home_xg: float, away_xg: float
    ) -> dict[str, float]:
        first_half = self._score_grid(home_xg * 0.45, away_xg * 0.45)
        second_half = self._score_grid(home_xg * 0.55, away_xg * 0.55)
        probabilities = {label: 0.0 for label in (
            "胜胜", "胜平", "胜负",
            "平胜", "平平", "平负",
            "负胜", "负平", "负负",
        )}
        labels = {"home": "胜", "draw": "平", "away": "负"}
        for (first_home, first_away), first_probability in first_half.items():
            half_label = labels[self._result_key(first_home, first_away)]
            for (second_home, second_away), second_probability in second_half.items():
                full_label = labels[
                    self._result_key(
                        first_home + second_home,
                        first_away + second_away,
                    )
                ]
                probabilities[half_label + full_label] += (
                    first_probability * second_probability
                )
        return probabilities

    @staticmethod
    def _result_key(home_goals: int, away_goals: int) -> str:
        if home_goals > away_goals:
            return "home"
        if home_goals < away_goals:
            return "away"
        return "draw"

    def _expected_home_goals(self, features: MatchFeatures) -> float:
        raw = (
            features.league_avg_home_goals
            * features.home.attack_strength
            * max(0.35, features.away.defense_strength)
            * (1.0 + features.home.home_advantage)
            - features.home.injury_penalty
        )
        if self.enable_enhanced_boosts:
            raw = self._apply_enhanced_boosts(raw, features, is_home=True)
        return min(4.5, max(0.15, raw))

    def _expected_away_goals(self, features: MatchFeatures) -> float:
        raw = (
            features.league_avg_away_goals
            * features.away.attack_strength
            * max(0.35, features.home.defense_strength)
            - features.away.injury_penalty
        )
        if self.enable_enhanced_boosts:
            raw = self._apply_enhanced_boosts(raw, features, is_home=False)
        return min(4.5, max(0.15, raw))

    @staticmethod
    def _apply_enhanced_boosts(xg: float, features: MatchFeatures, is_home: bool) -> float:
        """应用增强特征（阵型/H2H/疲劳/赔率趋势）调整预期进球。

        所有增强特征从 features.context 读取，不存在时不影响结果。
        """
        ctx = features.context

        # --- 阵型调整 ---
        attack_advantage = float(ctx.get("formation_attack_advantage", 0.0))
        if attack_advantage != 0.0 and "formation_home" in ctx:
            # attack_advantage > 0 表示主队进攻优势
            # 主队 xG 受益，客队 xG 受损
            if is_home:
                xg += attack_advantage * 0.25
            else:
                xg -= attack_advantage * 0.15

        # 中场人数差：中场占优的一方控球率更高，影响 xG
        midfield_diff = int(ctx.get("formation_midfield_diff", 0))
        if midfield_diff != 0:
            if is_home:
                xg += midfield_diff * 0.03
            else:
                xg -= midfield_diff * 0.03

        # --- H2H 心理优势 ---
        h2h_edge = float(ctx.get("h2h_psychological_edge", 0.0))
        if h2h_edge != 0.0:
            if is_home:
                xg += h2h_edge * 0.10
            else:
                xg -= h2h_edge * 0.10

        # --- 休息天数疲劳 ---
        fatigue = float(ctx.get(
            "rest_fatigue_penalty_home" if is_home else "rest_fatigue_penalty_away",
            0.0,
        ))
        xg -= fatigue

        return xg

    def _score_grid(
        self, home_xg: float, away_xg: float
    ) -> dict[tuple[int, int], float]:
        probs: dict[tuple[int, int], float] = {}
        for home_goals in range(self.max_goals + 1):
            hp = self._poisson(home_goals, home_xg)
            for away_goals in range(self.max_goals + 1):
                probs[(home_goals, away_goals)] = hp * self._poisson(
                    away_goals, away_xg
                )
        total = sum(probs.values())
        return {score: probability / total for score, probability in probs.items()}

    @staticmethod
    def _poisson(k: int, lam: float) -> float:
        return math.exp(-lam) * (lam**k) / math.factorial(k)

    def _elo_1x2(self, features: MatchFeatures) -> tuple[float, float, float]:
        home_elo_bonus = 65.0 if features.home.home_advantage > 0.0 else 0.0
        home_elo = features.home.elo + home_elo_bonus
        away_elo = features.away.elo
        home_no_draw = 1.0 / (1.0 + 10.0 ** ((away_elo - home_elo) / self.elo_scale))
        draw = max(0.18, min(0.31, 0.27 - abs(home_no_draw - 0.5) * 0.12))
        home = home_no_draw * (1.0 - draw)
        away = (1.0 - home_no_draw) * (1.0 - draw)
        return home, draw, away

    def _form_adjustment(self, features: MatchFeatures) -> tuple[float, float]:
        diff = (
            features.home.recent_points_per_match
            - features.away.recent_points_per_match
        )
        adjustment = max(
            -self.form_weight_max,
            min(self.form_weight_max, diff * self.form_weight_multiplier),
        )
        return adjustment, -adjustment

    @staticmethod
    def _normalize(*values: float) -> tuple[float, ...]:
        clipped = [max(0.001, value) for value in values]
        total = sum(clipped)
        return tuple(value / total for value in clipped)
