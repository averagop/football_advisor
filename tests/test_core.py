from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from football_advisor.backtest_engine import (
    BacktestEngine,
    BacktestResult,
    HistoricalMatch,
    format_backtest_summary,
)
from football_advisor.calibration_engine import CalibrationEngine
from football_advisor.config import ProbabilityConfig
from football_advisor.models import (
    MatchFeatures,
    OddsValue,
    ProbabilityResult,
    TeamFeatures,
    ValueAssessment,
)
from football_advisor.no_bet_policy import NoBetPolicy
from football_advisor.odds_value_engine import OddsValueEngine
from football_advisor.probability_engine import ProbabilityEngine
from football_advisor.query_tools import UnsafeQueryError, validate_select_sql


def sample_features(**overrides):
    base = {
        "home": TeamFeatures(
            name="Home",
            elo=1550,
            attack_strength=1.08,
            defense_strength=0.94,
            recent_points_per_match=1.8,
            home_advantage=0.18,
        ),
        "away": TeamFeatures(
            name="Away",
            elo=1500,
            attack_strength=0.98,
            defense_strength=1.04,
            recent_points_per_match=1.3,
        ),
        "odds_1x2": {"home": 2.20, "draw": 3.30, "away": 3.40},
        "updated_at": datetime.now(timezone.utc),
    }

    base.update(overrides)
    return MatchFeatures(**base)


class ProbabilityEngineTests(unittest.TestCase):
    def test_probability_config_defaults_to_uncalibrated_baseline(self):
        config = ProbabilityConfig()

        self.assertEqual(config.poisson_weight, 0.72)
        self.assertEqual(config.model_version, "baseline-uncalibrated")
        self.assertFalse(config.calibrated)
        self.assertFalse(config.enable_enhanced_boosts)

    def test_neutral_venue_has_no_hidden_elo_home_advantage(self):
        features = MatchFeatures(
            home=TeamFeatures(name="Home", elo=1500, home_advantage=0.0),
            away=TeamFeatures(name="Away", elo=1500),
            league_avg_home_goals=1.3,
            league_avg_away_goals=1.3,
            updated_at=datetime.now(timezone.utc),
            context={"is_neutral_venue": True},
        )

        result = ProbabilityEngine().predict(features)

        self.assertAlmostEqual(result.home_win, result.away_win, places=6)

    def test_sporttery_probability_buckets_are_normalized(self):
        result = ProbabilityEngine().predict(sample_features())

        self.assertAlmostEqual(sum(result.total_goals_probabilities.values()), 1.0)
        self.assertAlmostEqual(sum(result.half_full_probabilities.values()), 1.0)
        self.assertEqual(
            set(result.half_full_probabilities),
            {"胜胜", "胜平", "胜负", "平胜", "平平", "平负", "负胜", "负平", "负负"},
        )

    def test_probabilities_are_normalized(self):
        result = ProbabilityEngine().predict(sample_features())

        self.assertAlmostEqual(
            result.home_win + result.draw + result.away_win, 1.0, places=6
        )
        self.assertAlmostEqual(result.over_2_5 + result.under_2_5, 1.0, places=6)
        self.assertGreater(result.expected_home_goals, 0)
        self.assertEqual(len(result.most_likely_scores), 5)

    def test_custom_weights_alter_probabilities(self) -> None:
        features = sample_features()
        default_engine = ProbabilityEngine()
        custom_engine = ProbabilityEngine(poisson_weight=0.9)

        p1 = default_engine.predict(features)
        p2 = custom_engine.predict(features)

        # They shouldn't be exactly the same if the weight changed
        self.assertNotEqual(p1.home_win, p2.home_win)
        self.assertAlmostEqual(p2.home_win + p2.draw + p2.away_win, 1.0, places=5)
        self.assertAlmostEqual(p2.over_2_5 + p2.under_2_5, 1.0, places=6)
        self.assertGreater(p2.expected_home_goals, 0)
        self.assertEqual(len(p2.most_likely_scores), 5)

    def test_odds_trend_does_not_affect_model_probability(self):
        """不变性测试：仅改变赔率趋势时，模型基础概率必须保持不变。"""
        features_without_trend = sample_features()
        features_with_trend = sample_features(context={
            "odds_trend_market_sentiment": 0.35,
            "odds_trend_home_direction": "down",
            "odds_trend_home_change_pct": -0.08,
        })

        engine = ProbabilityEngine()
        p_no_trend = engine.predict(features_without_trend)
        p_with_trend = engine.predict(features_with_trend)

        # 概率必须完全一致
        self.assertAlmostEqual(p_no_trend.home_win, p_with_trend.home_win, places=6)
        self.assertAlmostEqual(p_no_trend.draw, p_with_trend.draw, places=6)
        self.assertAlmostEqual(p_no_trend.away_win, p_with_trend.away_win, places=6)
        self.assertAlmostEqual(p_no_trend.expected_home_goals, p_with_trend.expected_home_goals, places=6)
        self.assertAlmostEqual(p_no_trend.expected_away_goals, p_with_trend.expected_away_goals, places=6)

    def test_enhanced_context_does_not_affect_probability_by_default(self):
        base = sample_features()
        enhanced = sample_features(
            context={
                "formation_home": "4-3-3",
                "formation_attack_advantage": 0.8,
                "formation_midfield_diff": 2,
                "h2h_psychological_edge": 0.5,
                "rest_fatigue_penalty_home": 0.2,
                "rest_fatigue_penalty_away": 0.1,
            }
        )

        base_result = ProbabilityEngine().predict(base)
        enhanced_result = ProbabilityEngine().predict(enhanced)

        self.assertAlmostEqual(
            base_result.expected_home_goals,
            enhanced_result.expected_home_goals,
        )
        self.assertAlmostEqual(
            base_result.expected_away_goals,
            enhanced_result.expected_away_goals,
        )


class OddsValueEngineTests(unittest.TestCase):
    def test_sporttery_correct_score_total_goals_and_half_full_are_assessed(self):
        probabilities = ProbabilityEngine().predict(sample_features())
        engine = OddsValueEngine(value_threshold=-1.0)

        correct_score = engine.assess_correct_score(
            {"1:0": 7.0, "2:0": 9.0, "胜其它": 20.0},
            probabilities,
        )
        total_goals = engine.assess_total_goals(
            {str(goals): 8.0 for goals in range(7)} | {"7+": 15.0},
            probabilities,
        )
        half_full = engine.assess_half_full(
            {label: 9.0 for label in probabilities.half_full_probabilities},
            probabilities,
        )

        self.assertEqual([item.outcome for item in correct_score.values], ["1:0", "2:0", "胜其它"])
        self.assertAlmostEqual(sum(item.model_probability for item in correct_score.values), probabilities.home_win, delta=0.08)
        self.assertAlmostEqual(sum(item.model_probability for item in total_goals.values), 1.0)
        self.assertAlmostEqual(sum(item.model_probability for item in half_full.values), 1.0)

    def test_value_threshold_marks_best_candidate(self):
        probabilities = ProbabilityEngine().predict(sample_features())
        assessment = OddsValueEngine(value_threshold=0.01).assess_1x2(
            {"home": 2.60, "draw": 3.20, "away": 3.10},
            probabilities,
        )

        self.assertGreater(assessment.overround, 1.0)
        self.assertIsNotNone(assessment.best_value)
        self.assertTrue(any(item.value for item in assessment.values))

    def test_totals_value_uses_model_over_under_probabilities(self):
        probabilities = ProbabilityEngine().predict(sample_features())
        assessment = OddsValueEngine(value_threshold=-1.0).assess_totals(
            {"over": 2.05, "under": 1.85},
            probabilities,
        )

        self.assertEqual(
            [item.outcome for item in assessment.values], ["over", "under"]
        )
        self.assertAlmostEqual(
            assessment.values[0].model_probability,
            probabilities.over_2_5,
            places=6,
        )

    def test_half_goal_handicap_value_uses_1x2_probabilities(self):
        probabilities = ProbabilityEngine().predict(sample_features())
        assessment = OddsValueEngine(value_threshold=-1.0).assess_handicap(
            handicap_line=-0.5,
            odds={"home": 2.05, "away": 1.85},
            probabilities=probabilities,
        )

        self.assertEqual([item.outcome for item in assessment.values], ["home", "away"])
        self.assertAlmostEqual(
            assessment.values[0].model_probability,
            probabilities.home_win,
            places=6,
        )
        self.assertAlmostEqual(
            assessment.values[1].model_probability,
            probabilities.draw + probabilities.away_win,
            places=6,
        )

    def test_sporttery_rqspf_value_uses_three_way_score_grid(self):
        probabilities = ProbabilityResult(
            home_win=0.6,
            draw=0.25,
            away_win=0.15,
            over_2_5=0.5,
            under_2_5=0.5,
            expected_home_goals=1.8,
            expected_away_goals=0.9,
            most_likely_scores=[],
            score_probabilities={
                (2, 0): 0.20,
                (1, 0): 0.30,
                (1, 1): 0.25,
                (0, 1): 0.25,
            },
        )

        assessment = OddsValueEngine(value_threshold=-1.0).assess_handicap_1x2(
            handicap_line=-1,
            odds={"home": 4.50, "draw": 3.80, "away": 1.62},
            probabilities=probabilities,
        )

        self.assertEqual(
            [item.outcome for item in assessment.values],
            ["home", "draw", "away"],
        )
        self.assertAlmostEqual(assessment.values[0].model_probability, 0.20)
        self.assertAlmostEqual(assessment.values[1].model_probability, 0.30)
        self.assertAlmostEqual(assessment.values[2].model_probability, 0.50)


class NoBetPolicyTests(unittest.TestCase):
    def test_naive_timestamp_is_interpreted_as_utc(self):
        naive_utc_now = datetime.now(timezone.utc).replace(tzinfo=None)

        self.assertLess(abs(NoBetPolicy._age_minutes(naive_utc_now)), 1.0)

    def test_uncalibrated_model_is_forced_to_no_bet(self):
        features = sample_features()
        probabilities = ProbabilityEngine().predict(features)
        assessment = OddsValueEngine(value_threshold=-1.0).assess_1x2(
            features.odds_1x2, probabilities
        )

        decision = NoBetPolicy(calibrated=False).decide(features, assessment)

        self.assertEqual(decision.recommendation.value, "no_bet")
        self.assertEqual(decision.risk_level.value, "high")
        self.assertIn("model_uncalibrated", decision.reasons)

    def test_secondary_market_value_can_drive_bet_decision(self):
        features = sample_features()
        primary = ValueAssessment(values=[], best_value=None, overround=1.05, threshold=0.03)
        secondary_value = OddsValue(
            outcome="home",
            decimal_odds=2.0,
            implied_probability=0.5,
            model_probability=0.62,
            edge=0.12,
            value=True,
        )
        secondary = ValueAssessment(
            values=[secondary_value],
            best_value=secondary_value,
            overround=1.04,
            threshold=0.03,
        )

        decision = NoBetPolicy(calibrated=True).decide(
            features,
            primary,
            {"让球胜平负": secondary},
        )

        self.assertEqual(decision.recommendation.value, "bet")
        self.assertTrue(any("让球胜平负" in reason for reason in decision.reasons))

    def test_stale_data_blocks_bet(self):
        features = sample_features(
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=45)
        )
        probabilities = ProbabilityEngine().predict(features)
        assessment = OddsValueEngine(value_threshold=0.01).assess_1x2(
            features.odds_1x2, probabilities
        )
        decision = NoBetPolicy(max_data_age_minutes=30).decide(features, assessment)

        self.assertEqual(decision.recommendation.value, "no_bet")
        self.assertEqual(decision.risk_level.value, "high")

    def test_future_data_timestamp_blocks_bet(self):
        features = sample_features(
            updated_at=datetime.now(timezone.utc) + timedelta(minutes=5)
        )
        probabilities = ProbabilityEngine().predict(features)
        assessment = OddsValueEngine(value_threshold=-1.0).assess_1x2(
            features.odds_1x2, probabilities
        )
        decision = NoBetPolicy(max_data_age_minutes=30).decide(features, assessment)

        self.assertEqual(decision.recommendation.value, "no_bet")
        self.assertEqual(decision.risk_level.value, "high")
        self.assertTrue(any("future" in reason.lower() for reason in decision.reasons))

    def test_naive_local_timestamp_is_rejected_when_it_is_future_in_utc(self):
        features = sample_features(updated_at=datetime.now().replace(tzinfo=None))
        probabilities = ProbabilityEngine().predict(features)
        assessment = OddsValueEngine(value_threshold=-1.0).assess_1x2(
            features.odds_1x2, probabilities
        )
        decision = NoBetPolicy(max_data_age_minutes=30).decide(features, assessment)

        self.assertEqual(decision.recommendation.value, "no_bet")
        self.assertTrue(any("future" in reason.lower() for reason in decision.reasons))

    def test_sync_quality_flag_blocks_bet(self):
        features = sample_features(context={"sync_data_quality_flag": True})
        probabilities = ProbabilityEngine().predict(features)
        assessment = OddsValueEngine(value_threshold=-1.0).assess_1x2(
            features.odds_1x2, probabilities
        )
        decision = NoBetPolicy(max_data_age_minutes=30).decide(features, assessment)

        self.assertEqual(decision.recommendation.value, "no_bet")
        self.assertEqual(decision.risk_level.value, "high")
        self.assertTrue(any("sync" in reason.lower() for reason in decision.reasons))

    def test_missing_news_signal_blocks_bet(self):
        features = sample_features(context={"missing_news_signal_flag": True})
        probabilities = ProbabilityEngine().predict(features)
        assessment = OddsValueEngine(value_threshold=-1.0).assess_1x2(
            features.odds_1x2, probabilities
        )
        decision = NoBetPolicy(max_data_age_minutes=30).decide(features, assessment)

        self.assertEqual(decision.recommendation.value, "no_bet")
        self.assertEqual(decision.risk_level.value, "high")
        self.assertTrue(any("news" in reason.lower() for reason in decision.reasons))

    def test_duckdb_data_quality_flag_blocks_bet(self):
        features = sample_features(context={"no_bet_data_quality_flag": True})
        probabilities = ProbabilityEngine().predict(features)
        assessment = OddsValueEngine(value_threshold=0.01).assess_1x2(
            {"home": 2.60, "draw": 3.20, "away": 3.10},
            probabilities,
        )
        decision = NoBetPolicy(max_data_age_minutes=30).decide(features, assessment)

        self.assertEqual(decision.recommendation.value, "no_bet")
        self.assertEqual(decision.risk_level.value, "high")

    def test_platt_confidence_bounds(self):
        policy = NoBetPolicy(
            platt_a=7.0, platt_b=-0.5, confidence_floor=0.35, confidence_cap=0.90
        )

        # Test extreme low edge
        low_confidence = policy._platt_confidence(-0.5)
        self.assertEqual(low_confidence, 0.35)

        # Test extreme high edge
        high_confidence = policy._platt_confidence(1.0)
        self.assertEqual(high_confidence, 0.90)

        # Test mid edge
        mid_confidence = policy._platt_confidence(0.1)
        self.assertGreater(mid_confidence, 0.35)
        self.assertLess(mid_confidence, 0.90)

    def test_gap_reasons_are_collected_per_component(self):
        """组件级缺口检查：每个缺失组件给出明确原因。"""
        features = sample_features(context={
            "missing_canonical_match_mapping": True,
            "missing_sporttery_markets": ["CRS", "TTG"],
            "missing_news_signal_flag": True,
        })
        probabilities = ProbabilityEngine().predict(features)
        assessment = OddsValueEngine(value_threshold=0.01).assess_1x2(
            features.odds_1x2, probabilities,
        )

        with self.subTest("canonical_mapping"):
            decision = NoBetPolicy().decide(features, assessment)
            reasons_text = " ".join(decision.reasons)
            self.assertIn("canonical", reasons_text.lower())
            self.assertEqual(decision.recommendation.value, "no_bet")
            self.assertEqual(decision.risk_level.value, "high")

        with self.subTest("missing_markets"):
            features_mk = sample_features(context={
                "missing_sporttery_markets": ["CRS", "TTG"],
            })
            probs = ProbabilityEngine().predict(features_mk)
            assessment_mk = OddsValueEngine(value_threshold=-1.0).assess_1x2(
                features_mk.odds_1x2, probs,
            )
            decision_mk = NoBetPolicy().decide(features_mk, assessment_mk)
            reasons_text = " ".join(decision_mk.reasons)
            self.assertIn("Sporttery", reasons_text)
            self.assertIn("CRS", reasons_text)
            self.assertIn("TTG", reasons_text)

        with self.subTest("stale_components"):
            features_stale = sample_features(context={
                "stale_components": [
                    {"name": "sporttery_odds", "age_minutes": 45},
                    {"name": "team_stats", "age_minutes": 42},
                ],
            })
            probs_stale = ProbabilityEngine().predict(features_stale)
            assessment_stale = OddsValueEngine(value_threshold=-1.0).assess_1x2(
                features_stale.odds_1x2, probs_stale,
            )
            decision_stale = NoBetPolicy().decide(features_stale, assessment_stale)
            reasons_text = " ".join(decision_stale.reasons)
            self.assertIn("Stale", reasons_text)
            self.assertIn("sporttery_odds", reasons_text)
            self.assertIn("45min", reasons_text)

        with self.subTest("missing_optional_no_block"):
            """可选组件缺失不应阻塞预测。"""
            features_opt = sample_features(context={
                "missing_optional": ["weather", "referee"],
            })
            probs_opt = ProbabilityEngine().predict(features_opt)
            assessment_opt = OddsValueEngine(value_threshold=-1.0).assess_1x2(
                features_opt.odds_1x2, probs_opt,
            )
            decision_opt = NoBetPolicy().decide(features_opt, assessment_opt)
            reasons_text = " ".join(decision_opt.reasons)
            self.assertIn("weather", reasons_text)
            self.assertIn("referee", reasons_text)
            self.assertIn("Not blocking", reasons_text)
            # 可选组件缺失不应升级风险为 HIGH
            self.assertNotEqual(decision_opt.risk_level.value, "high")


class QuerySafetyTests(unittest.TestCase):
    def test_select_gets_limit(self):
        self.assertEqual(
            validate_select_sql("SELECT * FROM matches", 10),
            "SELECT * FROM matches LIMIT 10",
        )

    def test_rejects_multiple_statements(self):
        with self.assertRaises(UnsafeQueryError):
            validate_select_sql("SELECT * FROM matches; DROP TABLE matches")

    def test_rejects_mutating_statement(self):
        with self.assertRaises(UnsafeQueryError):
            validate_select_sql("DELETE FROM matches")


class CalibrationTests(unittest.TestCase):
    def test_metrics_are_computed(self):
        metrics = CalibrationEngine().evaluate_1x2(
            [
                {"home": 0.6, "draw": 0.25, "away": 0.15},
                {"home": 0.2, "draw": 0.25, "away": 0.55},
            ],
            ["home", "away"],
        )

        self.assertEqual(metrics.sample_size, 2)
        self.assertEqual(metrics.accuracy, 1.0)
        self.assertGreater(metrics.log_loss, 0.0)


class BacktestTests(unittest.TestCase):
    def test_future_leakage_is_rejected(self):
        kickoff = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        match = HistoricalMatch(
            kickoff_time=kickoff,
            features=sample_features(updated_at=kickoff + timedelta(minutes=1)),
            actual_outcome="home",
        )

        with self.assertRaises(ValueError):
            BacktestEngine().run([match])

    def test_duckdb_data_quality_no_bet_flag_skips_backtest_bet(self):
        kickoff = datetime.now(timezone.utc) + timedelta(days=1)
        match = HistoricalMatch(
            kickoff_time=kickoff,
            features=sample_features(
                updated_at=kickoff - timedelta(minutes=20),
                context={"no_bet_data_quality_flag": True},
            ),
            actual_outcome="home",
            closing_odds_1x2={"home": 2.60, "draw": 3.20, "away": 3.10},
        )

        result = BacktestEngine(
            odds_value_engine=OddsValueEngine(value_threshold=0.01)
        ).run([match])

        self.assertEqual(result.bets, 0)
        self.assertEqual(result.roi, 0.0)

    def test_backtest_summarizes_no_bet_counts_by_reason(self):
        kickoff = datetime.now(timezone.utc) + timedelta(days=1)
        matches = [
            HistoricalMatch(
                kickoff_time=kickoff,
                features=sample_features(
                    updated_at=kickoff - timedelta(minutes=20),
                    context={"no_bet_data_quality_flag": True},
                ),
                actual_outcome="home",
                closing_odds_1x2={"home": 2.60, "draw": 3.20, "away": 3.10},
            ),
            HistoricalMatch(
                kickoff_time=kickoff + timedelta(days=1),
                features=sample_features(updated_at=kickoff - timedelta(minutes=10)),
                actual_outcome="draw",
                closing_odds_1x2=None,
            ),
            HistoricalMatch(
                kickoff_time=kickoff + timedelta(days=2),
                features=sample_features(updated_at=kickoff - timedelta(minutes=5)),
                actual_outcome="away",
                closing_odds_1x2={"home": 2.20, "draw": 3.30, "away": 3.40},
            ),
        ]

        result = BacktestEngine(
            odds_value_engine=OddsValueEngine(value_threshold=1.0)
        ).run(matches)

        self.assertEqual(result.bets, 0)
        self.assertEqual(result.no_bet_samples, 3)
        self.assertEqual(
            result.no_bet_reasons,
            {
                "data_quality": 1,
                "missing_closing_odds": 1,
                "no_value": 1,
            },
        )

    def test_formats_minimal_walk_forward_backtest_summary(self):
        result = BacktestResult(
            metrics=CalibrationEngine().evaluate_1x2(
                [{"home": 0.7, "draw": 0.2, "away": 0.1}],
                ["home"],
            ),
            bets=2,
            roi=-0.125,
            max_drawdown=1.5,
            average_odds=2.35,
            staged_win_rate=0.5,
            equity_curve=[-1.0, 0.5],
            no_bet_samples=8,
            no_bet_reasons={"data_quality": 5, "no_value": 3},
        )

        summary = format_backtest_summary(result)

        self.assertIn("样本数: 1", summary)
        self.assertIn("投注数: 2", summary)
        self.assertIn("No Bet: 8", summary)
        self.assertIn("ROI: -12.50%", summary)
        self.assertIn("最大回撤: 1.50", summary)
        self.assertIn("投注命中率: 50.00%", summary)
        self.assertIn("Brier Score:", summary)
        self.assertIn("LogLoss:", summary)
        self.assertIn("命中率:", summary)
        self.assertIn("- data_quality: 5", summary)
        self.assertIn("- no_value: 3", summary)

    @unittest.skip("BacktestEngine 重构后待更新测试")
    def test_staged_win_rate_and_equity_curve(self):
        kickoff = datetime.now(timezone.utc) + timedelta(days=1)
        matches = [
            HistoricalMatch(
                kickoff_time=kickoff,
                features=sample_features(updated_at=kickoff - timedelta(minutes=20)),
                actual_outcome="home",
                closing_odds_1x2={"home": 2.00, "draw": 3.00, "away": 3.00},
            ),
            HistoricalMatch(
                kickoff_time=kickoff + timedelta(days=1),
                features=sample_features(updated_at=kickoff - timedelta(minutes=20)),
                actual_outcome="draw",
                closing_odds_1x2={"home": 2.00, "draw": 3.00, "away": 3.00},
            ),
        ]

        # Suppose OddsValueEngine says 'home' for both
        class FakeOddsValueEngine:
            def assess_1x2(self, odds, probs):
                from football_advisor.models import OddsValue, ValueAssessment

                return ValueAssessment(
                    values=[],
                    best_value=OddsValue(
                        outcome="home",
                        model_probability=0.6,
                        decimal_odds=2.00,
                        edge=0.2,
                        implied_probability=0.5,
                        value=0.1,
                    ),
                    overround=1.05,
                    threshold=0.01,
                )

        engine = BacktestEngine(odds_value_engine=FakeOddsValueEngine())
        result = engine.run(matches)

        # 1st bet: home win, odds 2.00 -> profit 1.0
        # 2nd bet: draw (bet home), odds 2.00 -> profit -1.0
        self.assertEqual(result.bets, 2)
        self.assertEqual(result.staged_win_rate, 0.5)
        self.assertEqual(result.equity_curve, [1.0, 0.0])
        self.assertEqual(result.max_drawdown, 1.0)


if __name__ == "__main__":
    unittest.main()
