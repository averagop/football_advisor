from __future__ import annotations

import unittest
from datetime import datetime, timezone

from football_advisor.cross_validator import CrossValidator, CrossValidationResult
from football_advisor.multi_source_coordinator import MultiSourceResult, SourceResult


class CrossValidatorTests(unittest.TestCase):
    def setUp(self):
        self.validator = CrossValidator()

    def _make_result(self, results: list[SourceResult]) -> MultiSourceResult:
        return MultiSourceResult(
            results=results,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            total_elapsed_ms=50.0,
        )

    def test_score_consensus_when_all_agree(self):
        results = [
            SourceResult(provider_name="A", status="success", data={"home_score": 2, "away_score": 1}),
            SourceResult(provider_name="B", status="success", data={"home_score": 2, "away_score": 1}),
            SourceResult(provider_name="C", status="success", data={"home_score": 2, "away_score": 1}),
        ]
        msr = self._make_result(results)
        r = self.validator.validate(msr)
        self.assertEqual(r.score_consensus, "consensus:2-1")
        self.assertFalse(r.score_flagged)

    def test_score_disagreement_flagged(self):
        results = [
            SourceResult(provider_name="A", status="success", data={"home_score": 2, "away_score": 1}),
            SourceResult(provider_name="B", status="success", data={"home_score": 1, "away_score": 1}),
            SourceResult(provider_name="C", status="success", data={"home_score": 3, "away_score": 0}),
        ]
        msr = self._make_result(results)
        r = self.validator.validate(msr)
        self.assertEqual(r.score_consensus, "score_disagreement")
        self.assertTrue(r.score_flagged)

    def test_no_score_data_returns_no_flag(self):
        results = [
            SourceResult(provider_name="A", status="success", data={}),
            SourceResult(provider_name="B", status="success", data={}),
        ]
        msr = self._make_result(results)
        r = self.validator.validate(msr)
        self.assertEqual(r.score_consensus, "no_score_data")
        self.assertFalse(r.score_flagged)

    def test_odds_deviation_detected(self):
        results = [
            SourceResult(provider_name="A", status="success", data={"home_odds": 2.0, "draw_odds": 3.5, "away_odds": 4.0}),
            SourceResult(provider_name="B", status="success", data={"home_odds": 2.0, "draw_odds": 3.5, "away_odds": 4.0}),
            SourceResult(provider_name="C", status="success", data={"home_odds": 3.0, "draw_odds": 5.0, "away_odds": 2.0}),
        ]
        msr = self._make_result(results)
        r = self.validator.validate(msr)
        self.assertIn("C", r.odds_deviation_sources)

    def test_single_source_no_deviation(self):
        results = [
            SourceResult(provider_name="A", status="success", data={"home_odds": 2.0, "draw_odds": 3.5, "away_odds": 4.0}),
        ]
        msr = self._make_result(results)
        r = self.validator.validate(msr)
        self.assertEqual(len(r.odds_deviation_sources), 0)

    def test_lineup_uncertain_from_single_source(self):
        results = [
            SourceResult(provider_name="A", status="success", data={"lineup_players": ["P1", "P2", "P3"]}),
            SourceResult(provider_name="B", status="success", data={"lineup_players": ["P1", "P4"]}),
        ]
        msr = self._make_result(results)
        r = self.validator.validate(msr)
        self.assertGreater(r.lineup_uncertain_count, 0)

    def test_injury_requires_two_sources(self):
        results = [
            SourceResult(provider_name="A", status="success", data={"injured_players": ["P1", "P2"]}),
            SourceResult(provider_name="B", status="success", data={"injured_players": ["P1"]}),
        ]
        msr = self._make_result(results)
        r = self.validator.validate(msr)
        self.assertEqual(r.injury_unconfirmed_count, 1)

    def test_coverage_flag_multi_source_rich(self):
        results = [
            SourceResult(provider_name="A", status="success", data={"covered_fields": ["odds", "stats", "schedule"]}),
            SourceResult(provider_name="B", status="success", data={"covered_fields": ["odds", "stats", "squad"]}),
            SourceResult(provider_name="C", status="success", data={"covered_fields": ["odds", "schedule", "injury"]}),
        ]
        msr = self._make_result(results)
        r = self.validator.validate(msr)
        self.assertEqual(r.multi_source_coverage_flag, "multi_source_rich")

    def test_coverage_flag_single_source(self):
        results = [
            SourceResult(provider_name="A", status="success", data={"covered_fields": ["odds"]}),
            SourceResult(provider_name="B", status="skipped_no_token", data={"covered_fields": []}),
        ]
        msr = self._make_result(results)
        r = self.validator.validate(msr)
        self.assertEqual(r.multi_source_coverage_flag, "single_source")

    def test_validation_details_include_source_count(self):
        results = [
            SourceResult(provider_name="A", status="success", data={}),
            SourceResult(provider_name="B", status="skipped_no_token", data={}),
        ]
        msr = self._make_result(results)
        r = self.validator.validate(msr)
        self.assertEqual(r.validation_details["source_count"], 2)
        self.assertEqual(r.validation_details["success_count"], 1)
        self.assertEqual(r.validation_details["skipped_count"], 1)

    def test_invalid_numeric_range_is_flagged(self):
        results = [
            SourceResult(
                provider_name="A",
                status="success",
                data={"home_odds": 1.0, "draw_odds": 3.5, "away_odds": 4.0},
            ),
        ]
        msr = self._make_result(results)
        r = self.validator.validate(msr)
        self.assertIn("A:home_odds", r.invalid_range_sources)
        self.assertTrue(r.cross_check_failed)

    def test_duplicate_provider_record_is_flagged(self):
        results = [
            SourceResult(
                provider_name="A",
                status="success",
                data={"provider_match_id": "M1", "covered_fields": ["odds"]},
            ),
            SourceResult(
                provider_name="A",
                status="success",
                data={"provider_match_id": "M1", "covered_fields": ["odds"]},
            ),
        ]
        msr = self._make_result(results)
        r = self.validator.validate(msr)
        self.assertIn("A:M1", r.duplicate_record_sources)
        self.assertTrue(r.cross_check_failed)

    def test_odds_history_three_sigma_anomaly_is_flagged(self):
        results = [
            SourceResult(
                provider_name="A",
                status="success",
                data={
                    "home_odds_history": [2.00, 2.01, 1.99, 2.02, 2.00, 2.45],
                },
            ),
        ]
        msr = self._make_result(results)
        r = self.validator.validate(msr)
        self.assertIn("A:home_odds_history", r.anomaly_sources)
        self.assertTrue(r.cross_check_failed)


if __name__ == "__main__":
    unittest.main()
