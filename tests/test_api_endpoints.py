import unittest
import tempfile
import dataclasses
import os
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from football_advisor.api import create_app
from football_advisor.db_schema import initialize_database
from football_advisor.config import AdvisorConfig
from football_advisor.feature_builder import FeatureDataUnavailable
from football_advisor.pipeline import PredictionPipeline
from football_advisor.sync import DataSyncCoordinator
from football_advisor.backtest_engine import BacktestEngine, DuckDBBacktestMatchLoader
from datetime import datetime, timezone
from football_advisor.models import MatchFeatures, TeamFeatures, ProbabilityResult, ValueAssessment, PolicyDecision, Recommendation, RiskLevel
from football_advisor.llm_router import LLMResult

class TestAPIEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import uuid
        import os
        cls.db_path = f"test_api_endpoints_{uuid.uuid4().hex}.db"
        if os.path.exists(cls.db_path):
            os.remove(cls.db_path)

        initialize_database(cls.db_path)

        base_config = AdvisorConfig()
        cls.config = dataclasses.replace(
            base_config,
            duckdb=dataclasses.replace(base_config.duckdb, database_path=cls.db_path)
        )

        # Create mock dependencies
        cls.sync_mock = MagicMock()
        cls.sync_mock.sync_before_prediction.return_value = []

        cls.news_tool_mock = MagicMock()
        cls.news_tool_mock.search_match_news.return_value.documents = []

        cls.prob_engine_mock = MagicMock()
        cls.prob_engine_mock.predict.return_value = ProbabilityResult(
            home_win=0.5, draw=0.3, away_win=0.2, over_2_5=0.5, under_2_5=0.5,
            expected_home_goals=1.5, expected_away_goals=1.0, most_likely_scores=[]
        )

        cls.val_engine_mock = MagicMock()
        cls.val_engine_mock.assess_1x2.return_value = ValueAssessment(
            values=[], best_value=None, overround=0.0, threshold=0.05
        )

        cls.policy_mock = MagicMock()
        cls.policy_mock.decide.return_value = PolicyDecision(
            recommendation=Recommendation.NO_BET, risk_level=RiskLevel.HIGH, confidence=0.5, reasons=["Mock"]
        )

        cls.report_builder_mock = MagicMock()
        cls.report_builder_mock.build_prompt.return_value = ("sys", "user")
        cls.report_builder_mock.is_valid_generated_report.return_value = True
        cls.report_builder_mock.validate_narrative.return_value = (True, [])
        cls.report_builder_mock.build_markdown.return_value = "# Mock LLM Report"

        cls.llm_router_mock = MagicMock()
        cls.llm_router_mock.generate.return_value.used_backup = False
        cls.llm_router_mock.generate.return_value.content = "# Mock LLM Report"
        cls.llm_router_mock.generate.return_value.provider = "external"
        cls.llm_router_mock.generate_external.return_value = LLMResult(
            content='{"key_factors": "Test","main_risks": "Risk","reasoning_summary": "Summary"}',
            provider="external",
            used_backup=False,
        )
        cls.llm_router_mock.generate_backup.return_value = LLMResult(
            content="# Mock Qwen Report", provider="qwen2.5:7b", used_backup=True
        )
        cls.llm_router_mock._chat_completion.return_value = "# Mock Qwen Report"
        cls.llm_router_mock.backup_base_url = "http://localhost:11434"
        cls.llm_router_mock.backup_model = "qwen2.5:7b"

        cls.feature_builder_mock = MagicMock()
        cls.feature_builder_mock.build.return_value = MatchFeatures(
            home=TeamFeatures(name="Arsenal"),
            away=TeamFeatures(name="Chelsea"),
            odds_1x2={},
            updated_at=datetime.now(timezone.utc),
            sources=[],
            context={
                "home_team_id": "TEAM_ARSENAL",
                "away_team_id": "TEAM_CHELSEA",
            }
        )

        pipeline = PredictionPipeline(
            sync=cls.sync_mock,
            news_tool=cls.news_tool_mock,
            probability_engine=cls.prob_engine_mock,
            value_engine=cls.val_engine_mock,
            policy=cls.policy_mock,
            report_builder=cls.report_builder_mock,
            llm_router=cls.llm_router_mock,
            feature_builder=cls.feature_builder_mock,
            config=cls.config
        )
        cls.pipeline = pipeline

        from football_advisor.backtest_engine import BacktestResult, CalibrationMetrics
        backtest_engine = MagicMock()
        backtest_engine.run.return_value = BacktestResult(
            metrics=CalibrationMetrics(0.1, 0.5, 0.7, 100),
            bets=10,
            roi=0.15,
            max_drawdown=0.05,
            average_odds=2.0,
            equity_curve=[1.0, 1.15],
            no_bet_samples=0,
            no_bet_reasons={}
        )

        backtest_loader = MagicMock()
        backtest_loader.load_walk_forward_matches.return_value = [{"match_id": "M1"}]

        # Create app with injected mocks
        cls.app = create_app(
            pipeline=pipeline,
            backtest_loader=backtest_loader,
            backtest_engine=backtest_engine
        )
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        import os
        try:
            os.remove(cls.db_path)
        except OSError:
            pass

    def test_predict_endpoint(self):
        response = self.client.post("/predict", json={"query": "Arsenal vs Chelsea", "mode": "standard"})
        self.assertEqual(response.status_code, 200)

        content = response.json()
        self.assertEqual(content, {"report": "# Mock LLM Report"})

        # Verify that the stage-level mocks were called in pipeline
        self.sync_mock.sync_before_prediction.assert_called_once()
        self.prob_engine_mock.predict.assert_called_once()
        self.llm_router_mock.generate_external.assert_called_once()
        self.llm_router_mock.generate_backup.assert_not_called()

    def test_predict_returns_503_when_feature_data_is_unavailable(self):
        unavailable_pipeline = MagicMock()
        unavailable_pipeline.predict.side_effect = FeatureDataUnavailable(
            "duckdb://internal-host/private/path"
        )
        unavailable_pipeline.config = self.config
        client = TestClient(
            create_app(pipeline=unavailable_pipeline),
            raise_server_exceptions=False,
        )

        response = client.post(
            "/predict",
            json={"query": "Arsenal vs Chelsea", "match_id": "MISSING_FEATURES"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "match_id": "MISSING_FEATURES",
                "status": "data_unavailable",
                "reason": "Required prediction features are unavailable.",
            },
        )
        self.assertNotIn("probabilities", response.json())
        self.assertNotIn("internal-host", response.text)

    def test_fetch_preview_route_is_not_registered_when_disabled(self):
        with patch.dict(
            os.environ,
            {"FOOTBALL_ENABLE_FETCH_PREVIEW": "false"},
            clear=False,
        ):
            client = TestClient(create_app(pipeline=self.pipeline))

        response = client.post(
            "/search/fetch-preview",
            json={"urls": ["https://example.com/news"]},
        )

        self.assertEqual(response.status_code, 404)

    def test_fetch_preview_requires_valid_admin_token(self):
        text_fetcher = MagicMock()
        text_fetcher.fetch_many.return_value = []
        with patch.dict(
            os.environ,
            {
                "FOOTBALL_ENABLE_FETCH_PREVIEW": "true",
                "FOOTBALL_FETCH_PREVIEW_ADMIN_TOKEN": "test-admin-token",
            },
            clear=False,
        ):
            client = TestClient(
                create_app(pipeline=self.pipeline, text_fetcher=text_fetcher)
            )

            missing = client.post(
                "/search/fetch-preview",
                json={"urls": ["https://example.com/news"]},
            )
            invalid = client.post(
                "/search/fetch-preview",
                headers={"X-Football-Admin-Token": "wrong-token"},
                json={"urls": ["https://example.com/news"]},
            )
            valid = client.post(
                "/search/fetch-preview",
                headers={"X-Football-Admin-Token": "test-admin-token"},
                json={"urls": ["https://example.com/news"]},
            )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(invalid.status_code, 403)
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(valid.json(), {"documents": []})
        text_fetcher.fetch_many.assert_called_once_with(
            ["https://example.com/news"]
        )

    def test_fetch_preview_rejects_missing_server_token(self):
        with patch.dict(
            os.environ,
            {
                "FOOTBALL_ENABLE_FETCH_PREVIEW": "true",
                "FOOTBALL_FETCH_PREVIEW_ADMIN_TOKEN": "",
            },
            clear=False,
        ):
            client = TestClient(create_app(pipeline=self.pipeline))
            response = client.post(
                "/search/fetch-preview",
                headers={"X-Football-Admin-Token": "test-admin-token"},
                json={"urls": ["https://example.com/news"]},
            )

        self.assertEqual(response.status_code, 503)

    def test_backtest_endpoint(self):
        response = self.client.post("/backtest", json={"limit": 10, "match_id_prefix": "SQLITE_MATCH_"})
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["roi"], 0.15)
        self.assertEqual(data["bets"], 10)

if __name__ == "__main__":
    unittest.main()
