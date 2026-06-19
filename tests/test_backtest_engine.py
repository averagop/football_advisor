from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from football_advisor import backtest_engine
from football_advisor.backtest_engine import BacktestEngine, HistoricalMatch
from football_advisor.models import (
    MatchFeatures,
    OddsValue,
    ProbabilityResult,
    TeamFeatures,
    ValueAssessment,
)


class _FixedProbabilityEngine:
    poisson_weight = 0.72
    elo_weight = 0.28

    def predict(self, features):
        return ProbabilityResult(
            home_win=0.6,
            draw=0.25,
            away_win=0.15,
            over_2_5=0.5,
            under_2_5=0.5,
            expected_home_goals=1.5,
            expected_away_goals=0.8,
            most_likely_scores=[],
        )


class _FixedValueEngine:
    def assess_1x2(self, odds, probabilities):
        candidate = OddsValue(
            outcome="home",
            decimal_odds=2.0,
            implied_probability=0.5,
            model_probability=0.6,
            edge=0.1,
            value=True,
        )
        return ValueAssessment(
            values=[candidate],
            best_value=candidate,
            overround=1.0,
            threshold=0.05,
        )


class BacktestBettingSemanticsTests(unittest.TestCase):
    def test_simulated_value_candidate_is_not_real_betting_performance(self):
        kickoff = datetime.now(timezone.utc) + timedelta(days=1)
        match = HistoricalMatch(
            kickoff_time=kickoff,
            features=MatchFeatures(
                home=TeamFeatures(name="Home"),
                away=TeamFeatures(name="Away"),
                odds_1x2={"home": 2.0, "draw": 3.0, "away": 4.0},
                updated_at=kickoff - timedelta(minutes=20),
            ),
            actual_outcome="home",
            closing_odds_1x2={"home": 2.0, "draw": 3.0, "away": 4.0},
        )

        result = BacktestEngine(
            probability_engine=_FixedProbabilityEngine(),
            odds_value_engine=_FixedValueEngine(),
        ).run([match])

        self.assertEqual(result.bets, 0)
        self.assertEqual(result.roi, 0.0)
        self.assertEqual(result.staged_win_rate, 0.0)
        self.assertEqual(result.equity_curve, [])

    def test_only_recorded_bet_contributes_to_betting_performance(self):
        self.assertTrue(hasattr(backtest_engine, "RecordedBet"))
        recorded_bet_type = getattr(backtest_engine, "RecordedBet", None)
        if recorded_bet_type is None:
            return

        kickoff = datetime.now(timezone.utc) + timedelta(days=1)
        match = HistoricalMatch(
            kickoff_time=kickoff,
            features=MatchFeatures(
                home=TeamFeatures(name="Home"),
                away=TeamFeatures(name="Away"),
                odds_1x2={"home": 2.0, "draw": 3.0, "away": 4.0},
                updated_at=kickoff - timedelta(minutes=20),
            ),
            actual_outcome="away",
            closing_odds_1x2={"home": 2.0, "draw": 3.0, "away": 4.0},
            recorded_bets=(
                recorded_bet_type(
                    stake_amount=10.0,
                    odds_at_bet=3.0,
                    result="WIN",
                    net_profit=20.0,
                ),
            ),
        )

        result = BacktestEngine(
            probability_engine=_FixedProbabilityEngine(),
            odds_value_engine=_FixedValueEngine(),
        ).run([match])

        self.assertEqual(result.bets, 1)
        self.assertEqual(result.roi, 2.0)
        self.assertEqual(result.average_odds, 3.0)
        self.assertEqual(result.staged_win_rate, 1.0)
        self.assertEqual(result.equity_curve, [20.0])


if __name__ == "__main__":
    unittest.main()
