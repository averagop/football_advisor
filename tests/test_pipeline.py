from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from football_advisor.csv_importer import import_csv_directory
from football_advisor.config import AdvisorConfig
from football_advisor.db_schema import apply_schema
from football_advisor.feature_builder import DuckDBFeatureBuilder
from football_advisor.models import MatchFeatures, MatchRequest, TeamFeatures
from football_advisor.pipeline import PredictionPipeline
from football_advisor.query_tools import DuckDBStatsQueryTool
from football_advisor.sync import SyncResult
from football_advisor.target_sync import DataReadinessGate, TargetSyncResult
from football_advisor.verification_engine import PredictionLoggingError


class FakeSync:
    def sync_before_prediction(self, request: MatchRequest):
        return []


class FakeNewsTool:
    def search_team_news(self, query: str):
        return SimpleNamespace(documents=[])

    def search_match_news(self, match_id: str, query: str, limit: int = 8):
        return SimpleNamespace(documents=[])


class FakeLLMRouter:
    def generate_external(self, system_prompt: str, user_prompt: str) -> str:
        return ""

    def generate_backup(self, system_prompt: str, user_prompt: str) -> str:
        return ""


class BadExternalLLMRouter:
    def generate_external(self, system_prompt: str, user_prompt: str) -> str:
        return "# Bad report\n- home win probability: 99%\n- 建议投注: bet"

    def generate_backup(self, system_prompt: str, user_prompt: str) -> str:
        return ""


class FakePredictionLogger:
    def log_prediction(self, bundle) -> str:
        return "PRED_TEST"


class IncompleteOddsFeatureBuilder:
    def build(self, request: MatchRequest, sync_results):
        return MatchFeatures(
            home=TeamFeatures(name="Arsenal", elo=1620, attack_strength=1.12),
            away=TeamFeatures(name="Chelsea", elo=1570, attack_strength=1.01),
            odds_1x2={"home": 2.15, "away": 3.60},
            updated_at=datetime.now(timezone.utc),
            sources=["TEST_DUCKDB"],
            context={
                "feature_source": "duckdb_view",
                "match_id": "MISSING_ODDS",
                "home_team_id": "TEAM_ARS",
                "away_team_id": "TEAM_CHE",
                "missing_1x2_odds_flag": True,
            },
        )


class PipelineTests(unittest.TestCase):
    def test_prediction_logging_failure_stops_before_report_generation(self):
        class FailingLogger:
            def log_prediction(self, bundle):
                raise PredictionLoggingError("log failed")

        class CountingReportGenerator:
            def __init__(self):
                self.calls = 0

            def generate_report(self, *args, **kwargs):
                self.calls += 1
                raise AssertionError("report generation must not run")

        report_generator = CountingReportGenerator()
        pipeline = PredictionPipeline(
            sync=object(),
            news_tool=object(),
            feature_builder=object(),
            prediction_logger=FailingLogger(),
            diff_analyzer=object(),
            enhancer_pipeline=object(),
            report_generator=report_generator,
        )
        bundle = SimpleNamespace(request=SimpleNamespace(match_id="LOG_FAIL"))

        with self.assertRaises(PredictionLoggingError):
            pipeline.generate_report(bundle)

        self.assertEqual(report_generator.calls, 0)
    def test_structured_readiness_blocks_prediction_and_is_preserved_in_bundle(self):
        now = datetime.now(timezone.utc)

        class StructuredSync:
            def sync_before_prediction(self, request: MatchRequest):
                return TargetSyncResult(
                    match_id="READY_MISSING_ODDS",
                    critical_results=(
                        SyncResult(source="sporttery", updated_at=now, status="success"),
                        SyncResult(source="multi_source", updated_at=now, status="success"),
                        SyncResult(source="news_provider", updated_at=now, status="success"),
                    ),
                    optional_results=(),
                    completed_at=now,
                )

        class ReadyFeatureBuilder:
            def build(self, request: MatchRequest, sync_results):
                return MatchFeatures(
                    home=TeamFeatures(name="Qatar", elo=1500, attack_strength=1.0),
                    away=TeamFeatures(name="Switzerland", elo=1600, attack_strength=1.0),
                    odds_1x2={"home": 3.2, "draw": 3.0, "away": 2.2},
                    updated_at=now,
                    sources=["TEST_DUCKDB"],
                    context={
                        "feature_source": "duckdb_view",
                        "home_team_id": "TEAM_QAT",
                        "away_team_id": "TEAM_SUI",
                        "news_source_count": 1,
                    },
                )

        class ReadyNewsTool:
            def search_match_news(self, match_id: str, query: str):
                return SimpleNamespace(documents=[{"text": "confirmed"}])

        readiness_gate = DataReadinessGate(
            match_id="READY_MISSING_ODDS",
            schedule_updated_at=now,
            sporttery_odds_updated_at=None,
            team_stats_updated_at=now,
            lineup_injury_updated_at=now,
            news_updated_at=now,
        )
        pipeline = PredictionPipeline(
            sync=StructuredSync(),
            news_tool=ReadyNewsTool(),
            feature_builder=ReadyFeatureBuilder(),
            enhancer_pipeline=object(),
        )
        pipeline.enhancer_pipeline = SimpleNamespace(enhance=lambda features, match_id: features)

        with patch(
            "football_advisor.pipeline.DataReadinessGate.load",
            return_value=readiness_gate,
        ):
            bundle = pipeline.build_prediction_bundle(
                MatchRequest(
                    query="Qatar vs Switzerland",
                    match_id="READY_MISSING_ODDS",
                )
            )

        self.assertFalse(bundle.readiness.ready)
        self.assertIn("sporttery_odds", bundle.readiness.missing_critical)
        self.assertEqual(bundle.policy.recommendation.value, "no_bet")
        report = pipeline.report_builder.build_markdown(bundle)
        self.assertIn("缺失=sporttery_odds", report)
    def test_initializes_configured_database_before_building_dependencies(self):
        config = replace(
            AdvisorConfig(),
            duckdb=replace(
                AdvisorConfig().duckdb,
                database_path="startup_migration.duckdb",
            ),
        )

        with patch(
            "football_advisor.pipeline.initialize_database",
            create=True,
        ) as initialize_database:
            pipeline = PredictionPipeline(
                config=config,
                sync=object(),
                news_tool=object(),
                feature_builder=object(),
                prediction_logger=object(),
                diff_analyzer=object(),
                enhancer_pipeline=object(),
                report_generator=object(),
            )

        initialize_database.assert_called_once_with("startup_migration.duckdb")
        self.assertFalse(hasattr(pipeline, "verifier"))
        self.assertFalse(hasattr(pipeline, "bet_ledger"))
        self.assertFalse(hasattr(pipeline, "feedback_engine"))

    def test_default_sync_coordinator_receives_pipeline_parent_config(self):
        config = replace(
            AdvisorConfig(),
            duckdb=replace(
                AdvisorConfig().duckdb,
                database_path="custom_pipeline_config.duckdb",
            ),
        )

        pipeline = PredictionPipeline(config=config)

        self.assertEqual(
            pipeline.sync.parent_config.duckdb.database_path,
            "custom_pipeline_config.duckdb",
        )

    def test_external_llm_output_that_changes_supplied_facts_falls_back_to_template(
        self,
    ):
        class FreshCompleteFeatureBuilder:
            def build(self, request: MatchRequest, sync_results):
                return MatchFeatures(
                    home=TeamFeatures(name="Arsenal", elo=1620, attack_strength=1.12),
                    away=TeamFeatures(name="Chelsea", elo=1570, attack_strength=1.01),
                    odds_1x2={"home": 2.60, "draw": 3.25, "away": 3.10},
                    updated_at=datetime.now(timezone.utc),
                    sources=["TEST_DUCKDB"],
                    context={
                        "feature_source": "duckdb_view",
                        "match_id": "SAFE_LLM",
                        "home_team_id": "TEAM_ARS",
                        "away_team_id": "TEAM_CHE",
                        "news_source_count": 2,
                    },
                )

        pipeline = PredictionPipeline(
            sync=FakeSync(),
            news_tool=FakeNewsTool(),
            feature_builder=FreshCompleteFeatureBuilder(),
            llm_router=BadExternalLLMRouter(),
        )

        report = pipeline.predict(
            MatchRequest(query="Arsenal vs Chelsea", match_id="SAFE_LLM")
        )

        self.assertNotIn("99%", report)
        self.assertIn("TEST_DUCKDB", report)
        self.assertIn("备用生成引擎: 是", report)

    def test_sync_skip_status_is_carried_into_no_bet_policy(self):
        class SkippedSync:
            def sync_before_prediction(self, request: MatchRequest):
                return [
                    SimpleNamespace(
                        source="structured_provider_placeholder",
                        status="skipped_no_provider_configured",
                    ),
                ]

        class FreshCompleteFeatureBuilder:
            def build(self, request: MatchRequest, sync_results):
                return MatchFeatures(
                    home=TeamFeatures(name="Arsenal", elo=1620, attack_strength=1.12),
                    away=TeamFeatures(name="Chelsea", elo=1570, attack_strength=1.01),
                    odds_1x2={"home": 2.60, "draw": 3.25, "away": 3.10},
                    updated_at=datetime.now(timezone.utc),
                    sources=["TEST_DUCKDB"],
                    context={
                        "feature_source": "duckdb_view",
                        "match_id": "SYNC_SKIP",
                        "home_team_id": "TEAM_ARS",
                        "away_team_id": "TEAM_CHE",
                        "news_source_count": 2,
                    },
                )

        pipeline = PredictionPipeline(
            sync=SkippedSync(),
            news_tool=FakeNewsTool(),
            feature_builder=FreshCompleteFeatureBuilder(),
            llm_router=FakeLLMRouter(),
        )

        report = pipeline.predict(
            MatchRequest(query="Arsenal vs Chelsea", match_id="SYNC_SKIP")
        )

        self.assertIn(
            "Sync result did not satisfy pre-prediction requirements.", report
        )
        self.assertIn(
            "structured_provider_placeholder:skipped_no_provider_configured", report
        )

    def test_missing_exchange_provider_is_reported_without_blocking_prediction(self):
        class MissingExchangeSync:
            def sync_before_prediction(self, request: MatchRequest):
                return [
                    SimpleNamespace(source="multi_source", status="success"),
                    SimpleNamespace(
                        source="exchange_provider",
                        status="skipped_no_provider_configured",
                    ),
                    SimpleNamespace(source="news_provider", status="success"),
                ]

        class FreshCompleteFeatureBuilder:
            def build(self, request: MatchRequest, sync_results):
                return MatchFeatures(
                    home=TeamFeatures(name="Arsenal", elo=1620, attack_strength=1.12),
                    away=TeamFeatures(name="Chelsea", elo=1570, attack_strength=1.01),
                    odds_1x2={"home": 3.00, "draw": 3.25, "away": 3.10},
                    updated_at=datetime.now(timezone.utc),
                    sources=["TEST_DUCKDB"],
                    context={
                        "feature_source": "duckdb_view",
                        "match_id": "MISSING_EXCHANGE",
                        "home_team_id": "TEAM_ARS",
                        "away_team_id": "TEAM_CHE",
                        "news_source_count": 2,
                    },
                )

        pipeline = PredictionPipeline(
            sync=MissingExchangeSync(),
            news_tool=FakeNewsTool(),
            feature_builder=FreshCompleteFeatureBuilder(),
            llm_router=FakeLLMRouter(),
        )

        report = pipeline.predict(
            MatchRequest(query="Arsenal vs Chelsea", match_id="MISSING_EXCHANGE")
        )

        self.assertIn("投注建议: 不建议投注", report)
        self.assertIn("model_uncalibrated", report)
        self.assertIn("exchange_provider:skipped_no_provider_configured", report)
        self.assertIn("资金流数据未接入", report)
        self.assertNotIn(
            "Sync result did not satisfy pre-prediction requirements.", report
        )

    def test_missing_market_stats_sync_status_is_carried_into_no_bet_policy(self):
        class MissingMarketStatsSync:
            def sync_before_prediction(self, request: MatchRequest):
                return [
                    SimpleNamespace(
                        source="API-Football",
                        status="fixture_matched_no_market_stats",
                        details={"missing_market_stats_count": "1"},
                    )
                ]

        class FreshCompleteFeatureBuilder:
            def build(self, request: MatchRequest, sync_results):
                return MatchFeatures(
                    home=TeamFeatures(name="Arsenal", elo=1620, attack_strength=1.12),
                    away=TeamFeatures(name="Chelsea", elo=1570, attack_strength=1.01),
                    odds_1x2={"home": 2.60, "draw": 3.25, "away": 3.10},
                    updated_at=datetime.now(timezone.utc),
                    sources=["TEST_DUCKDB"],
                    context={
                        "feature_source": "duckdb_view",
                        "match_id": "MISSING_MARKET_STATS",
                        "home_team_id": "TEAM_ARS",
                        "away_team_id": "TEAM_CHE",
                        "news_source_count": 2,
                    },
                )

        pipeline = PredictionPipeline(
            sync=MissingMarketStatsSync(),
            news_tool=FakeNewsTool(),
            feature_builder=FreshCompleteFeatureBuilder(),
            llm_router=FakeLLMRouter(),
        )

        report = pipeline.predict(
            MatchRequest(query="Arsenal vs Chelsea", match_id="MISSING_MARKET_STATS")
        )

        self.assertIn(
            "Sync result did not satisfy pre-prediction requirements.", report
        )
        self.assertIn("API-Football:fixture_matched_no_market_stats", report)

    def test_sync_success_but_stale_data_blocked_and_reported(self):
        """同步成功但 critical_data_age > 30 分钟时，报告触发 No Bet 并标注数据陈旧原因。"""
        from datetime import timedelta

        class SyncSuccessButStaleSync:
            def sync_before_prediction(self, request: MatchRequest):
                return [
                    SimpleNamespace(source="multi_source", status="success"),
                    SimpleNamespace(source="news_provider", status="success"),
                ]

        stale_updated_at = datetime.now(timezone.utc) - timedelta(minutes=45)

        class StaleFeatureBuilder:
            def build(self, request: MatchRequest, sync_results):
                return MatchFeatures(
                    home=TeamFeatures(name="Greece", elo=1620, attack_strength=1.08),
                    away=TeamFeatures(name="Italy", elo=1700, attack_strength=1.15),
                    odds_1x2={"home": 3.50, "draw": 3.20, "away": 2.10},
                    updated_at=stale_updated_at,
                    sources=["TEST_DUCKDB"],
                    context={
                        "feature_source": "duckdb_view",
                        "match_id": "STALE_FRESH",
                        "home_team_id": "TEAM_GRE",
                        "away_team_id": "TEAM_ITA",
                        "news_source_count": 3,
                        "critical_data_age_minutes": 45,
                    },
                )

        pipeline = PredictionPipeline(
            sync=SyncSuccessButStaleSync(),
            news_tool=FakeNewsTool(),
            feature_builder=StaleFeatureBuilder(),
            llm_router=FakeLLMRouter(),
        )

        report = pipeline.predict(
            MatchRequest(query="Greece vs Italy", match_id="STALE_FRESH")
        )

        self.assertIn("不建议投注", report)
        self.assertIn("同步已完成但关键数据年龄为 45 分钟", report)
        self.assertIn("超过 30 分钟上限", report)
        self.assertIn("Data is stale", report)

    def test_sync_success_and_fresh_data_not_blocked_by_age(self):
        """同步成功且宽表新鲜时，不因数据年龄触发 No Bet。"""
        class SyncSuccessFreshSync:
            def sync_before_prediction(self, request: MatchRequest):
                return [
                    SimpleNamespace(source="multi_source", status="success"),
                    SimpleNamespace(source="news_provider", status="success"),
                ]

        class FreshFeatureBuilder:
            def build(self, request: MatchRequest, sync_results):
                return MatchFeatures(
                    home=TeamFeatures(name="Greece", elo=1620, attack_strength=1.08),
                    away=TeamFeatures(name="Italy", elo=1700, attack_strength=1.15),
                    odds_1x2={"home": 3.50, "draw": 3.20, "away": 2.10},
                    updated_at=datetime.now(timezone.utc),
                    sources=["TEST_DUCKDB"],
                    context={
                        "feature_source": "duckdb_view",
                        "match_id": "FRESH_OK",
                        "home_team_id": "TEAM_GRE",
                        "away_team_id": "TEAM_ITA",
                        "news_source_count": 3,
                        "critical_data_age_minutes": 5,
                    },
                )

        pipeline = PredictionPipeline(
            sync=SyncSuccessFreshSync(),
            news_tool=FakeNewsTool(),
            feature_builder=FreshFeatureBuilder(),
            llm_router=FakeLLMRouter(),
        )

        report = pipeline.predict(
            MatchRequest(query="Greece vs Italy", match_id="FRESH_OK")
        )

        self.assertNotIn("超过 30 分钟上限", report)
        self.assertNotIn("Data is stale", report)

    def test_world_cup_prediction_blocks_when_sync_or_news_is_not_ready(self):
        class WorldCupReadinessSync:
            def sync_before_prediction(self, request: MatchRequest):
                return [
                    SimpleNamespace(source="multi_source", status="all_sources_failed"),
                    SimpleNamespace(
                        source="news_provider",
                        status="skipped_no_provider_configured",
                    ),
                ]

        class WorldCupFeatureBuilder:
            def build(self, request: MatchRequest, sync_results):
                return MatchFeatures(
                    home=TeamFeatures(
                        name="USA",
                        elo=1780,
                        attack_strength=1.08,
                        defense_strength=0.98,
                    ),
                    away=TeamFeatures(
                        name="Mexico",
                        elo=1720,
                        attack_strength=1.02,
                        defense_strength=1.01,
                    ),
                    odds_1x2={"home": 2.40, "draw": 3.20, "away": 3.05},
                    updated_at=datetime.now(timezone.utc),
                    sources=["TEST_WC_VIEW"],
                    context={
                        "feature_source": "duckdb_view",
                        "match_id": "WC_USA_MEX",
                        "home_team_id": "TEAM_USA",
                        "away_team_id": "TEAM_MEX",
                        "news_source_count": 0,
                    },
                )

        pipeline = PredictionPipeline(
            sync=WorldCupReadinessSync(),
            news_tool=FakeNewsTool(),
            feature_builder=WorldCupFeatureBuilder(),
            llm_router=FakeLLMRouter(),
        )

        report = pipeline.predict(
            MatchRequest(query="USA vs Mexico", match_id="WC_USA_MEX")
        )

        self.assertIn(
            "Sync result did not satisfy pre-prediction requirements.", report
        )
        self.assertIn("News signal is missing or stale; prediction blocked.", report)
        self.assertIn("multi_source:all_sources_failed", report)
        self.assertIn("news_provider:skipped_no_provider_configured", report)

    def test_incomplete_duckdb_odds_returns_no_bet_report_without_placeholder_fallback(
        self,
    ):
        pipeline = PredictionPipeline(
            sync=FakeSync(),
            news_tool=FakeNewsTool(),
            feature_builder=IncompleteOddsFeatureBuilder(),
            llm_router=FakeLLMRouter(),
        )

        report = pipeline.predict(
            MatchRequest(query="Arsenal vs Chelsea", match_id="MISSING_ODDS")
        )

        self.assertIn("投注建议: 不建议投注", report)
        self.assertIn("Missing 1x2 odds", report)
        self.assertIn("数据来源: TEST_DUCKDB", report)
        self.assertNotIn("placeholder_fallback", report)

    def test_feature_data_unavailable_stops_before_probability_generation(self):
        from unittest.mock import MagicMock

        class FailingFeatureBuilder:
            def build(self, request: MatchRequest, sync_results):
                from football_advisor.feature_builder import FeatureDataUnavailable

                raise FeatureDataUnavailable("Database down.")

        probability_engine = MagicMock()
        pipeline = PredictionPipeline(
            sync=FakeSync(),
            news_tool=FakeNewsTool(),
            feature_builder=FailingFeatureBuilder(),
            probability_engine=probability_engine,
            llm_router=FakeLLMRouter(),
        )

        from football_advisor.feature_builder import FeatureDataUnavailable

        with self.assertRaises(FeatureDataUnavailable):
            pipeline.build_prediction_bundle(
                MatchRequest(query="Arsenal vs Chelsea", match_id="MISSING_FEATURES")
            )

        probability_engine.predict.assert_not_called()

    def test_csv_duckdb_odds_pipeline_returns_structured_no_bet_report(self):
        import duckdb

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database_path = root / "pipeline.duckdb"
            csv_dir = root / "csv"
            csv_dir.mkdir()
            self._write_minimal_csv_fixture(csv_dir)

            with duckdb.connect(str(database_path)) as connection:
                apply_schema(connection)
                import_csv_directory(connection, csv_dir)
                connection.execute("""
                    INSERT INTO core.fact_match_context_summary (
                        match_id,
                        h2h_home_wins,
                        h2h_draws,
                        h2h_away_wins,
                        home_home_points_per_match,
                        away_away_points_per_match,
                        home_key_absences,
                        away_key_absences,
                        source_provider,
                        updated_at
                    ) VALUES (
                        'GAME_2',
                        1,
                        0,
                        0,
                        2.1,
                        1.1,
                        0,
                        1,
                        'TEST_CONTEXT',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
                    );

                    INSERT INTO core.fact_odds_capital_flow (
                        match_id,
                        snapshot_time,
                        time_to_kickoff,
                        odds_type,
                        bookmaker_name,
                        home_odds,
                        draw_odds,
                        away_odds,
                        implied_home_probability,
                        implied_draw_probability,
                        implied_away_probability,
                        source_provider
                    ) VALUES (
                        'GAME_2',
                        CAST(CURRENT_TIMESTAMP AS TIMESTAMP),
                        1440,
                        '1X2',
                        'TEST_BOOK',
                        2.60,
                        3.25,
                        3.10,
                        0.38,
                        0.30,
                        0.32,
                        'SportteryOfficialWeb'
                    );
                    """)

            pipeline = PredictionPipeline(
                sync=FakeSync(),
                news_tool=FakeNewsTool(),
                feature_builder=DuckDBFeatureBuilder(
                    DuckDBStatsQueryTool(database_path=str(database_path))
                ),
                llm_router=FakeLLMRouter(),
            )

            report = pipeline.predict(
                MatchRequest(query="Arsenal vs Chelsea", match_id="GAME_2")
            )

        self.assertIn("投注建议: 不建议投注", report)
        self.assertIn("DuckDB data quality flag requires No Bet.", report)
        self.assertIn("odds 2.60", report)
        self.assertIn("CSV_GAMES", report)
        self.assertIn("TEST_BOOK", report)
        self.assertIn("No Bet 数据质量标记=True", report)
        self.assertNotIn("placeholder_fallback", report)

    def _write_minimal_csv_fixture(self, csv_dir: Path) -> None:
        first_match_date = (
            (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
        )
        second_match_date = (
            (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()
        )
        (csv_dir / "competitions.csv").write_text(
            "\n".join(
                [
                    "competition_id,name,country_name",
                    "L1,Premier League,England",
                ]
            ),
            encoding="utf-8",
        )
        (csv_dir / "clubs.csv").write_text(
            "\n".join(
                [
                    "club_id,name,domestic_competition_id,stadium_name",
                    "10,Arsenal,L1,Emirates Stadium",
                    "20,Chelsea,L1,Stamford Bridge",
                ]
            ),
            encoding="utf-8",
        )
        (csv_dir / "games.csv").write_text(
            "\n".join(
                [
                    "game_id,competition_id,season,date,home_club_id,away_club_id,home_club_goals,away_club_goals,stadium,referee,home_club_name,away_club_name",
                    f"1,L1,2025,{first_match_date},10,20,2,1,Emirates Stadium,Ref One,Arsenal,Chelsea",
                    f"2,L1,2025,{second_match_date},10,20,,,"
                    "Emirates Stadium,Ref Two,Arsenal,Chelsea",
                ]
            ),
            encoding="utf-8",
        )
        (csv_dir / "club_games.csv").write_text(
            "\n".join(
                [
                    "game_id,club_id,own_goals,opponent_id,opponent_goals,hosting,is_win",
                    "1,10,2,20,1,Home,1",
                    "1,20,1,10,2,Away,0",
                    "2,10,,20,,Home,",
                    "2,20,,10,,Away,",
                ]
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
