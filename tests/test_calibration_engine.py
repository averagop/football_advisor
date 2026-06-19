from __future__ import annotations

import unittest
from dataclasses import dataclass

from football_advisor.calibration_engine import (
    CalibrationEngine,
    CalibrationMetrics,
    GridSearchCalibrator,
)
from football_advisor.models import MatchFeatures, TeamFeatures
from scripts.calibrate_probability_model import calibration_gate_approved


@dataclass
class MockMatch:
    features: MatchFeatures
    actual_outcome: str


def sample_match(home_points: float, away_points: float, outcome: str) -> MockMatch:
    features = MatchFeatures(
        home=TeamFeatures(
            name="Home",
            elo=1500,
            attack_strength=1.0,
            defense_strength=1.0,
            recent_points_per_match=home_points,
            home_advantage=0.0,
        ),
        away=TeamFeatures(
            name="Away",
            elo=1500,
            attack_strength=1.0,
            defense_strength=1.0,
            recent_points_per_match=away_points,
        ),
        odds_1x2={"home": 2.5, "draw": 3.0, "away": 2.5},
    )
    return MockMatch(features=features, actual_outcome=outcome)


class GridSearchCalibratorTests(unittest.TestCase):
    def test_release_gate_requires_sample_quality_and_baseline_improvement(self):
        baseline = CalibrationMetrics(0.24, 0.8, 0.5, 80)

        self.assertTrue(
            calibration_gate_approved(
                CalibrationMetrics(0.20, 0.7, 0.6, 50), baseline
            )
        )
        self.assertFalse(
            calibration_gate_approved(
                CalibrationMetrics(0.20, 0.7, 0.6, 49), baseline
            )
        )
        self.assertFalse(
            calibration_gate_approved(
                CalibrationMetrics(0.25, 0.7, 0.6, 80), baseline
            )
        )
        self.assertFalse(
            calibration_gate_approved(
                CalibrationMetrics(0.24, 0.7, 0.6, 80), baseline
            )
        )

    def test_grid_search_optimizes_poisson_weight(self):
        # We create a sample of matches that heavily favors home
        matches = [
            sample_match(3.0, 0.0, "home"),
            sample_match(3.0, 0.0, "home"),
            sample_match(0.0, 3.0, "away"),
            sample_match(1.0, 1.0, "draw"),
        ]

        calibrator = GridSearchCalibrator()
        result = calibrator.optimize_poisson_weight(
            matches,
            weight_start=0.50,
            weight_end=0.90,
            weight_step=0.10,
        )

        self.assertTrue(0.50 <= result.best_poisson_weight <= 0.90)
        self.assertGreater(len(result.all_results), 0)
        self.assertIsNotNone(result.best_metrics)

        # Verify that the best brier score is indeed the minimum in all_results
        min_brier = min(metrics.brier_score for _, metrics in result.all_results)
        self.assertAlmostEqual(result.best_metrics.brier_score, min_brier)

    def test_requires_at_least_one_sample(self):
        calibrator = GridSearchCalibrator()
        with self.assertRaises(ValueError):
            calibrator.optimize_poisson_weight([])

    def test_rejects_invalid_weight_range(self):
        calibrator = GridSearchCalibrator()
        matches = [sample_match(3.0, 0.0, "home")]

        with self.assertRaisesRegex(ValueError, "weight_start"):
            calibrator.optimize_poisson_weight(
                matches,
                weight_start=0.90,
                weight_end=0.50,
            )

    def test_rejects_non_positive_weight_step(self):
        calibrator = GridSearchCalibrator()
        matches = [sample_match(3.0, 0.0, "home")]

        with self.assertRaisesRegex(ValueError, "weight_step"):
            calibrator.optimize_poisson_weight(
                matches,
                weight_step=0.0,
            )


if __name__ == "__main__":
    unittest.main()
