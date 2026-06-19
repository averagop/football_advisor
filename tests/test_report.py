import unittest
from dataclasses import replace
from datetime import datetime, timezone
from football_advisor.models import (
    MatchRequest,
    PredictionBundle,
    ProbabilityResult,
    ValueAssessment,
    OddsValue,
    PolicyDecision,
    Recommendation,
    RiskLevel,
    MatchFeatures,
    TeamFeatures,
    ReportNarrative,
)
from football_advisor.report import ReportBuilder

class ReportBuilderTests(unittest.TestCase):
    def setUp(self):
        self.builder = ReportBuilder()
        
        self.features = MatchFeatures(
            home=TeamFeatures(name="HOME", elo=1500.0, attack_strength=1.5, defense_strength=1.0, home_advantage=0.1),
            away=TeamFeatures(name="AWAY", elo=1450.0, attack_strength=1.2, defense_strength=1.2, home_advantage=0.0),
            context={
                "feature_source": "duckdb_view",
                "critical_data_age_minutes": 5,
                "league_standard_name": "International Friendly",
            },
            updated_at=datetime.now(timezone.utc),
            sources=["DuckDB"],
        )
        
        self.probs = ProbabilityResult(
            home_win=0.5,
            draw=0.3,
            away_win=0.2,
            expected_home_goals=1.5,
            expected_away_goals=0.8,
            most_likely_scores=[("1-0", 0.15), ("1-1", 0.12), ("2-0", 0.10)],
            over_2_5=0.45,
            under_2_5=0.55
        )
        
        self.values = ValueAssessment(
            values=[
                OddsValue(outcome="home", decimal_odds=2.5, implied_probability=0.4, model_probability=0.5, edge=0.1, value=True)
            ],
            best_value=OddsValue(outcome="home", decimal_odds=2.5, implied_probability=0.4, model_probability=0.5, edge=0.1, value=True),
            overround=1.05,
            threshold=0.05
        )
        
        self.policy = PolicyDecision(
            recommendation=Recommendation.BET,
            risk_level=RiskLevel.LOW,
            confidence=0.8,
            reasons=[]
        )
        
        self.bundle = PredictionBundle(
            request=MatchRequest(
                query="HOME vs AWAY",
                home_team="HOME",
                away_team="AWAY",
            ),
            features=self.features,
            probabilities=self.probs,
            value_assessment=self.values,
            policy=self.policy,
            news=[],
            generated_by="external"
        )

    def test_build_markdown_generates_correct_report(self):
        markdown = self.builder.build_markdown(self.bundle)
        self.assertIn("对阵: HOME vs AWAY", markdown)
        self.assertIn("赛事: International Friendly", markdown)
        self.assertIn("主胜 50.0%", markdown)
        self.assertIn("建议投注", markdown)
        self.assertIn("风险等级: low", markdown)

    def test_is_valid_generated_report_accepts_valid_content(self):
        valid_content = self.builder.build_markdown(self.bundle)
        self.assertTrue(self.builder.is_valid_generated_report(self.bundle, valid_content))

    def test_is_valid_generated_report_rejects_missing_content(self):
        invalid_content = "This report is completely missing the required numbers."
        self.assertFalse(self.builder.is_valid_generated_report(self.bundle, invalid_content))

    def test_is_valid_generated_report_rejects_changed_match_facts(self):
        invalid_content = (
            "# 报告\n"
            "对阵: 希腊 vs 意大利\n"
            "赛事: European Qualifier\n"
            "主胜 50.0% 平局 30.0% 客胜 20.0% 大2.5 45.0%\n"
            "建议投注\n"
        )

        self.assertFalse(
            self.builder.is_valid_generated_report(self.bundle, invalid_content)
        )

    def test_is_valid_generated_report_requires_policy_reasons(self):
        policy = PolicyDecision(
            recommendation=Recommendation.NO_BET,
            risk_level=RiskLevel.HIGH,
            confidence=0.0,
            reasons=["Data is stale: 45.0 minutes old, limit is 30."],
        )
        bundle = PredictionBundle(
            request=self.bundle.request,
            features=self.features,
            probabilities=self.probs,
            value_assessment=self.values,
            policy=policy,
            news=[],
            generated_by="external",
        )
        valid_content = self.builder.build_markdown(bundle)
        invalid_content = valid_content.replace(policy.reasons[0], "数据有轻微延迟。")

        self.assertTrue(self.builder.is_valid_generated_report(bundle, valid_content))
        self.assertFalse(
            self.builder.is_valid_generated_report(bundle, invalid_content)
        )

    def test_no_bet_report_does_not_label_best_value_as_candidate(self):
        policy = PolicyDecision(
            recommendation=Recommendation.NO_BET,
            risk_level=RiskLevel.HIGH,
            confidence=0.0,
            reasons=["DuckDB data quality flag requires No Bet."],
        )
        bundle = PredictionBundle(
            request=self.bundle.request,
            features=self.features,
            probabilities=self.probs,
            value_assessment=self.values,
            policy=policy,
            news=[],
            generated_by="validated_template",
        )

        markdown = self.builder.build_markdown(bundle)

        self.assertNotIn("最佳价值候选", markdown)
        self.assertIn("模型倾向观察", markdown)
        self.assertIn("不构成投注建议", markdown)

    def test_sanitize_removes_forbidden_terms(self):
        unsafe_content = "这场比赛稳胆，你必胜，可以说是稳赚稳赢。"
        sanitized = self.builder._sanitize(unsafe_content)
        self.assertNotIn("稳胆", sanitized)
        self.assertNotIn("必胜", sanitized)
        self.assertNotIn("稳赚", sanitized)
        self.assertNotIn("稳赢", sanitized)
        self.assertIn("确定性表述", sanitized)

    def test_build_markdown_includes_secondary_rqspf_value_assessment(self):
        rqspf = ValueAssessment(
            values=[
                OddsValue(
                    outcome="home",
                    decimal_odds=4.5,
                    implied_probability=0.18,
                    model_probability=0.20,
                    edge=0.02,
                    value=False,
                ),
                OddsValue(
                    outcome="draw",
                    decimal_odds=3.8,
                    implied_probability=0.21,
                    model_probability=0.30,
                    edge=0.09,
                    value=True,
                ),
                OddsValue(
                    outcome="away",
                    decimal_odds=1.62,
                    implied_probability=0.61,
                    model_probability=0.50,
                    edge=-0.11,
                    value=False,
                ),
            ],
            best_value=OddsValue(
                outcome="draw",
                decimal_odds=3.8,
                implied_probability=0.21,
                model_probability=0.30,
                edge=0.09,
                value=True,
            ),
            overround=1.1,
            threshold=0.05,
        )
        bundle = PredictionBundle(
            request=None,  # type: ignore
            features=self.features,
            probabilities=self.probs,
            value_assessment=self.values,
            secondary_value_assessments={"让球胜平负": rqspf},
            policy=self.policy,
            news=[],
            generated_by="external",
        )

        markdown = self.builder.build_markdown(bundle)

        self.assertIn("## 让球胜平负价值", markdown)
        self.assertIn("- draw: odds 3.80", markdown)
        self.assertIn("edge 9.0%", markdown)

    def test_build_markdown_injects_narrative(self):
        """build_markdown 接受 narrative 参数时，应将三段叙述注入报告。"""
        narrative = ReportNarrative(
            key_factors="主队近期状态火热，连胜三场。",
            main_risks="客队反击速度极快，可能造成威胁。",
            reasoning_summary="综合来看主队稍占优势，但需防范反击。",
        )
        markdown = self.builder.build_markdown(self.bundle, narrative)

        self.assertIn("### 比赛关键因素", markdown)
        self.assertIn("主队近期状态火热，连胜三场。", markdown)
        self.assertIn("### 主要风险", markdown)
        self.assertIn("客队反击速度极快，可能造成威胁。", markdown)
        self.assertIn("### 综合推理", markdown)
        self.assertIn("综合来看主队稍占优势，但需防范反击。", markdown)

    def test_narrative_context_uses_actual_enhancers_and_sporttery_fields(self):
        features = replace(
            self.features,
            odds_correct_score={"1-0": 7.0},
            odds_total_goals={"2": 3.2},
            odds_half_full={"胜胜": 4.5},
            context={
                **self.features.context,
                "formation_home": "4-3-3",
                "h2h_total_matches": 3,
                "rest_days_home": 5,
                "odds_trend_status": "success",
            },
        )
        bundle = replace(self.bundle, features=features)

        prompt = self.builder._narrative_context(bundle)

        self.assertIn("阵型分析可用", prompt)
        self.assertIn("历史交锋分析可用", prompt)
        self.assertIn("疲劳分析可用", prompt)
        self.assertIn("赔率趋势分析可用", prompt)
        self.assertIn("比分", prompt)
        self.assertIn("总进球", prompt)
        self.assertIn("半全场", prompt)

    def test_build_markdown_without_narrative_is_unchanged(self):
        """不传 narrative 时，build_markdown 行为与之前一致。"""
        markdown = self.builder.build_markdown(self.bundle)
        # 不应包含叙述章节
        self.assertNotIn("### 比赛关键因素", markdown)
        self.assertNotIn("### 主要风险", markdown)
        self.assertNotIn("### 综合推理", markdown)
        # 原有的确定性内容应存在
        self.assertIn("对阵: HOME vs AWAY", markdown)

    def test_validate_narrative_rejects_fake_percentage(self):
        """叙述中包含不在证据中的百分比时应被拒绝。"""
        narrative = ReportNarrative(
            key_factors="主队胜率高达 72.0%。",
            main_risks="风险存在。",
            reasoning_summary="总结。",
        )
        is_valid, errors = self.builder.validate_narrative(self.bundle, narrative)
        self.assertFalse(is_valid)
        self.assertTrue(any("unverified_percentage" in e for e in errors))

    def test_validate_narrative_rejects_fake_score(self):
        """叙述中包含不在模型预测中的比分时应被拒绝。"""
        narrative = ReportNarrative(
            key_factors="预计比分 3:0。",
            main_risks="风险。",
            reasoning_summary="总结。",
        )
        is_valid, errors = self.builder.validate_narrative(self.bundle, narrative)
        self.assertFalse(is_valid)
        self.assertTrue(any("unverified_score" in e for e in errors))

    def test_validate_narrative_rejects_no_bet_conflict(self):
        """No Bet 策略时叙述中包含投注建议应被拒绝。"""
        policy = PolicyDecision(
            recommendation=Recommendation.NO_BET,
            risk_level=RiskLevel.HIGH,
            confidence=0.0,
            reasons=["test"],
        )
        bundle = PredictionBundle(
            request=self.bundle.request,
            features=self.features,
            probabilities=self.probs,
            value_assessment=self.values,
            policy=policy,
            news=[],
            generated_by="external",
        )
        narrative = ReportNarrative(
            key_factors="分析。",
            main_risks="建议投注主胜。",
            reasoning_summary="总结。",
        )
        is_valid, errors = self.builder.validate_narrative(bundle, narrative)
        self.assertFalse(is_valid)
        self.assertTrue(any("conflicts_with_no_bet_policy" in e for e in errors))

    def test_validate_narrative_accepts_valid_content(self):
        """不包含虚假数字、真实球队名的叙述应通过验证。"""
        narrative = ReportNarrative(
            key_factors="主队近期状态良好，客队防守稳固。",
            main_risks="伤病可能影响主队发挥。",
            reasoning_summary="需要谨慎评估。",
        )
        is_valid, errors = self.builder.validate_narrative(self.bundle, narrative)
        self.assertTrue(is_valid, f"Expected valid, got errors: {errors}")

    def test_validate_narrative_rejects_forbidden_terms(self):
        """叙述中包含禁止用语应被拒绝。"""
        narrative = ReportNarrative(
            key_factors="这场比赛稳胆。",
            main_risks="风险。",
            reasoning_summary="必胜。",
        )
        is_valid, errors = self.builder.validate_narrative(self.bundle, narrative)
        self.assertFalse(is_valid)
        self.assertTrue(any("forbidden_terms" in e for e in errors))

if __name__ == '__main__':
    unittest.main()
