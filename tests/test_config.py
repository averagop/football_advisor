from __future__ import annotations

import unittest
from pathlib import Path

from football_advisor.config import load_config
from football_advisor.llm_router import LLMRouter
from football_advisor.pipeline import PredictionPipeline
from football_advisor.sync import DataSyncCoordinator


class ConfigTests(unittest.TestCase):
    def test_invalid_boolean_environment_value_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "FOOTBALL_POST_MATCH_COLLECT_ENABLED"):
            load_config({"FOOTBALL_POST_MATCH_COLLECT_ENABLED": "sometimes"})

    def test_load_config_applies_defaults_and_environment_overrides(self):
        config = load_config(
            {
                "FOOTBALL_DUCKDB_PATH": "data/advisor.duckdb",
                "FOOTBALL_CHROMA_PERSIST_DIRECTORY": "data/chroma",
                "FOOTBALL_CHROMA_COLLECTION": "football_advisor_news",
                "FOOTBALL_CHROMA_MAX_DISTANCE": "0.55",
                "FOOTBALL_CHROMA_EMBEDDING_MODEL": "bge-m3",
                "FOOTBALL_CHROMA_EMBEDDING_MODEL_VERSION": "bge-m3-2024-02",
                "FOOTBALL_EXTERNAL_LLM_BASE_URL": "https://llm.example/v1",
                "FOOTBALL_EXTERNAL_LLM_API_KEY": "secret",
                "FOOTBALL_EXTERNAL_LLM_MODEL": "custom-model",
                "FOOTBALL_LLM_TIMEOUT_SECONDS": "12",
                "FOOTBALL_LLM_RETRIES": "3",
                "FOOTBALL_SEARXNG_BASE_URL": "http://localhost:8080",
                "FOOTBALL_GOOGLE_SEARCH_API_KEY": "google-key",
                "FOOTBALL_SERPER_API_KEY": "serper-key",
                "FOOTBALL_STRUCTURED_SYNC_SOURCE": "football-data",
                "FOOTBALL_NEWS_SYNC_SOURCE": "searxng",
                "FOOTBALL_SPORTTERY_BASE_URL": "https://sporttery.example",
                "THESPORTSDB_BASE_URL": "https://sports.example/api",
                "SPORTMONKS_BASE_URL": "https://sportmonks.example/v3/football",
                "ISPORTS_BASE_URL": "https://isports.example",
                "THE_ODDS_API_TOKEN": "the-odds-secret",
                "THE_ODDS_API_BASE_URL": "https://the-odds.example/v4",
                "RAPIDAPI_TOKEN": "rapid-secret",
                "ODDS_FEED_RAPID_HOST": "odds-feed.example.p.rapidapi.com",
                "ODDS_FEED_RAPID_BASE_URL": "https://odds-feed.example",
                "FOOTBALL_PRE_MATCH_COLLECT_LEAGUES": "WC_WORLD_CUP_2026,TOP5_EPL",
            }
        )

        self.assertEqual(config.duckdb.database_path, "data/advisor.duckdb")
        self.assertEqual(config.chroma.persist_directory, "data/chroma")
        self.assertEqual(config.chroma.collection_name, "football_advisor_news")
        self.assertEqual(config.chroma.max_distance, 0.55)
        self.assertEqual(config.chroma.embedding_model_name, "bge-m3")
        self.assertEqual(config.chroma.embedding_model_version, "bge-m3-2024-02")
        self.assertEqual(config.llm.external_base_url, "https://llm.example/v1")
        self.assertEqual(config.llm.external_api_key, "secret")
        self.assertEqual(config.llm.external_model, "custom-model")
        self.assertEqual(config.llm.timeout_seconds, 12)
        self.assertEqual(config.llm.retries, 3)
        self.assertEqual(config.search.searxng_base_url, "http://localhost:8080")
        self.assertEqual(config.search.google_api_key, "google-key")
        self.assertEqual(config.search.serper_api_key, "serper-key")
        self.assertEqual(config.sync.structured_source, "football-data")
        self.assertEqual(config.sync.news_source, "searxng")
        self.assertEqual(config.sync.sporttery_base_url, "https://sporttery.example")
        self.assertEqual(config.sync.thesportsdb_base_url, "https://sports.example/api")
        self.assertEqual(
            config.sync.sportmonks_base_url,
            "https://sportmonks.example/v3/football",
        )
        self.assertEqual(config.sync.isports_base_url, "https://isports.example")
        self.assertEqual(config.sync.the_odds_api_token, "the-odds-secret")
        self.assertEqual(config.sync.the_odds_api_base_url, "https://the-odds.example/v4")
        self.assertEqual(config.sync.rapidapi_token, "rapid-secret")
        self.assertEqual(
            config.sync.odds_feed_rapid_host,
            "odds-feed.example.p.rapidapi.com",
        )
        self.assertEqual(
            config.sync.odds_feed_rapid_base_url,
            "https://odds-feed.example",
        )
        self.assertEqual(
            config.sync.pre_match_collect_leagues,
            ("WC_WORLD_CUP_2026", "TOP5_EPL"),
        )

    def test_chroma_embedding_defaults_to_bge_m3(self):
        config = load_config({})

        self.assertEqual(config.chroma.embedding_model_name, "bge-m3")
        self.assertEqual(config.chroma.embedding_model_version, "bge-m3")
        self.assertEqual(config.sync.sporttery_base_url, "https://www.sporttery.cn")

    def test_requirements_include_ollama_for_default_chroma_embedding(self):
        requirements = Path("requirements.txt").read_text(encoding="utf-8")

        self.assertIn("ollama", requirements)

    def test_default_components_can_be_built_from_config(self):
        config = load_config(
            {
                "FOOTBALL_DUCKDB_PATH": "configured.duckdb",
                "FOOTBALL_CHROMA_PERSIST_DIRECTORY": "configured_chroma",
                "FOOTBALL_CHROMA_COLLECTION": "configured_news",
                "FOOTBALL_CHROMA_MAX_DISTANCE": "0.66",
                "FOOTBALL_EXTERNAL_LLM_BASE_URL": "https://llm.example/v1",
                "FOOTBALL_EXTERNAL_LLM_API_KEY": "secret",
                "FOOTBALL_EXTERNAL_LLM_MODEL": "custom-model",
                "FOOTBALL_STRUCTURED_SYNC_SOURCE": "structured-provider",
                "FOOTBALL_NEWS_SYNC_SOURCE": "news-provider",
                "FOOTBALL_FEATURE_VIEW_OR_QUERY": "SELECT * FROM configured_features",
            }
        )

        pipeline = PredictionPipeline(config=config)
        router = LLMRouter(config=config.llm)
        sync = DataSyncCoordinator(config=config.sync)

        self.assertEqual(pipeline.news_tool.persist_directory, "configured_chroma")
        self.assertEqual(pipeline.news_tool.collection_name, "configured_news")
        self.assertEqual(pipeline.news_tool.max_distance, 0.66)
        self.assertEqual(
            pipeline.feature_builder.query_tool.database_path, "configured.duckdb"
        )
        self.assertEqual(
            pipeline.feature_builder.config.feature_view_or_query,
            "SELECT * FROM configured_features",
        )
        self.assertEqual(router.external_base_url, "https://llm.example/v1")
        self.assertEqual(router.external_api_key, "secret")
        self.assertEqual(router.external_model, "custom-model")
        self.assertEqual(sync.config.structured_source, "structured-provider")
        self.assertEqual(sync.config.news_source, "news-provider")

    def test_google_search_key_is_accepted_as_serper_compatibility_key(self):
        config = load_config({"FOOTBALL_GOOGLE_SEARCH_API_KEY": "serper-compat-key"})

        self.assertEqual(config.search.serper_api_key, "serper-compat-key")


if __name__ == "__main__":
    unittest.main()
