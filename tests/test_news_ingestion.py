from __future__ import annotations

import datetime
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import duckdb

from football_advisor.config import AdvisorConfig
from football_advisor.db_schema import apply_schema
from football_advisor.llm_router import LLMResult, LLMRouter
from football_advisor.models import MatchRequest
from football_advisor.news_ingestion import NewsIngester
from football_advisor.search_router import (
    SearchPreviewResult,
    SearchResultItem,
    SearchRouter,
)
from football_advisor.sync import DataSyncCoordinator
from football_advisor.text_fetcher import FetchedTextDocument


class NoExternalLLMRouter:
    external_base_url = None
    external_api_key = None

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        raise AssertionError(
            "local fallback LLM must not be used for news signal extraction"
        )


# Fake ChromaDB Cls for testing
class FakeCollection:
    def __init__(self) -> None:
        self.deleted_match_ids: list[str] = []
        self.added_documents: list[str] = []
        self.added_ids: list[str] = []
        self.added_metadatas: list[dict] = []

    def delete(self, where: dict) -> None:
        self.deleted_match_ids.append(where.get("match_id"))

    def add(self, documents: list[str], ids: list[str], metadatas: list[dict]) -> None:
        self.added_documents.extend(documents)
        self.added_ids.extend(ids)
        self.added_metadatas.extend(metadatas)


class FakeClient:
    last_instance = None

    def __init__(self, path: str) -> None:
        FakeClient.last_instance = self
        self.path = path
        self.collection = FakeCollection()
        self.collection_calls: list[dict] = []

    def get_or_create_collection(self, name: str, **kwargs) -> FakeCollection:
        self.collection_calls.append({"name": name, **kwargs})
        return self.collection


class NewsIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.duckdb"
        with duckdb.connect(str(self.db_path)) as conn:
            apply_schema(conn)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_chunk_text_splits_by_size_and_punctuation(self) -> None:
        ingester = NewsIngester(
            chunk_size=50, chunk_overlap=10, database_path=str(self.db_path)
        )
        # 英文文本切分
        text = "This is a long sentence. It will be split near the dot if possible. Otherwise it cuts off."
        chunks = ingester._chunk_text(text)
        self.assertTrue(len(chunks) >= 2)
        # 第一块以句号结尾加空格
        self.assertEqual(chunks[0], "This is a long sentence.")

        # 换行符文本切分，总长度需要大于 chunk_size = 50，以测试除点号外的其他分割符
        nl_text = "This is paragraph one.\nThis is paragraph two which is very long and will trigger splitting."
        nl_chunks = ingester._chunk_text(nl_text)
        self.assertTrue(len(nl_chunks) >= 2)
        self.assertEqual(nl_chunks[0], "This is paragraph one.")

    def test_rejects_invalid_chunk_overlap(self) -> None:
        with self.assertRaises(ValueError):
            NewsIngester(
                chunk_size=50, chunk_overlap=50, database_path=str(self.db_path)
            )

        with self.assertRaises(ValueError):
            NewsIngester(
                chunk_size=50, chunk_overlap=-1, database_path=str(self.db_path)
            )

    def test_analyze_by_rules_detects_injury_and_sentiment(self) -> None:
        ingester = NewsIngester(database_path=str(self.db_path))
        # 负面情绪与风险词
        sentiment, risk = ingester._analyze_by_rules(
            "主力前锋伤停，缺阵本次比赛。球队遭遇三连败，士气低迷。"
        )
        self.assertTrue(risk)
        self.assertLess(sentiment, 0.0)

        # 正面情绪
        sentiment_pos, risk_pos = ingester._analyze_by_rules(
            "主力伤愈复出，球队取得三连胜，士气大振，充满信心。"
        )
        self.assertFalse(risk_pos)
        self.assertGreater(sentiment_pos, 0.0)

    def test_analyze_news_signals_uses_rules_without_external_llm(self) -> None:
        ingester = NewsIngester(
            database_path=str(self.db_path), llm_router=NoExternalLLMRouter()
        )
        doc = FetchedTextDocument(
            url="https://test.com/news1",
            title="Injury report",
            text="主力前锋缺席且伤停",
            content_hash="abc",
            status="ok",
        )
        analysis = ingester._analyze_news_signals([doc])
        self.assertTrue(analysis.risk)
        self.assertLess(analysis.sentiment, 0.0)
        self.assertEqual(analysis.generated_by, "rules")
        self.assertTrue(analysis.requires_cross_check)

    def test_news_signal_external_failure_never_calls_backup_model(self) -> None:
        mock_router = MagicMock(spec=LLMRouter)
        mock_router.external_base_url = "https://llm.example.test/v1"
        mock_router.external_api_key = "test-key"
        mock_router.generate_external.side_effect = RuntimeError("external failed")
        doc = FetchedTextDocument(
            url="https://test.com/news1",
            title="Injury report",
            text="主力前锋缺席且伤停",
            content_hash="abc",
            status="ok",
        )

        analysis = NewsIngester(
            database_path=str(self.db_path), llm_router=mock_router
        )._analyze_news_signals([doc])

        self.assertEqual(analysis.generated_by, "rules")
        mock_router.generate_external.assert_called_once()
        mock_router.generate_backup.assert_not_called()

    def test_analyze_news_signals_uses_external_llm_and_parses_json(self) -> None:
        mock_router = MagicMock(spec=LLMRouter)
        mock_router.external_base_url = "https://llm.example.test/v1"
        mock_router.external_api_key = "test-key"
        # Mock LLM 成功返回 JSON 格式
        mock_router.generate_external.return_value = (
            '{"sentiment_score": 0.65, "risk_flag": false, "reason": "Good condition"}'
        )

        ingester = NewsIngester(database_path=str(self.db_path), llm_router=mock_router)
        doc = FetchedTextDocument(
            url="https://test.com/news1",
            title="Good news",
            text="Team in high morale and fit.",
            content_hash="abc",
            status="ok",
        )
        analysis = ingester._analyze_news_signals([doc])
        self.assertEqual(analysis.sentiment, 0.65)
        self.assertFalse(analysis.risk)
        self.assertEqual(analysis.generated_by, "external_llm")
        self.assertTrue(analysis.requires_cross_check)
        mock_router.generate_external.assert_called_once()
        mock_router.generate_backup.assert_not_called()

    def test_analyze_news_signals_falls_back_to_rules_on_llm_failure(self) -> None:
        mock_router = MagicMock(spec=LLMRouter)
        mock_router.external_base_url = "https://llm.example.test/v1"
        mock_router.external_api_key = "test-key"
        # Mock LLM 返回非 JSON / 垃圾文本
        mock_router.generate_external.return_value = "This is not JSON text"

        ingester = NewsIngester(database_path=str(self.db_path), llm_router=mock_router)
        doc = FetchedTextDocument(
            url="https://test.com/news1",
            title="Injury report",
            text="主力前锋缺席且伤停",
            content_hash="abc",
            status="ok",
        )
        # 降级至规则匹配
        analysis = ingester._analyze_news_signals([doc])
        self.assertTrue(analysis.risk)
        self.assertLess(analysis.sentiment, 0.0)
        self.assertEqual(analysis.generated_by, "rules")
        self.assertTrue(analysis.requires_cross_check)

    def test_write_to_duckdb_inserts_and_updates_correctly(self) -> None:
        ingester = NewsIngester(database_path=str(self.db_path))
        match_id = "M_TEST_INGEST"
        last_updated = datetime.datetime.now(datetime.timezone.utc)

        # 写入
        ingester._write_to_duckdb(
            match_id=match_id,
            sentiment=0.3,
            risk=False,
            source_count=3,
            last_updated_at=last_updated,
            provider="test.com",
        )

        # 查询校验
        with duckdb.connect(str(self.db_path)) as conn:
            row = conn.execute(
                """
                SELECT
                    news_sentiment_score,
                    news_risk_flag,
                    news_source_count,
                    generated_by,
                    source_text_hash,
                    confidence,
                    requires_cross_check
                FROM core.fact_news_signal_summary
                WHERE match_id = ?
                """,
                [match_id],
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertAlmostEqual(row[0], 0.3)
            self.assertFalse(row[1])
            self.assertEqual(row[2], 3)
            self.assertEqual(row[3], "rules")
            self.assertRegex(row[4], r"^[0-9a-f]{64}$")
            self.assertGreaterEqual(row[5], 0.0)
            self.assertTrue(row[6])

        # 更新
        ingester._write_to_duckdb(
            match_id=match_id,
            sentiment=-0.5,
            risk=True,
            source_count=1,
            last_updated_at=last_updated,
            provider="test.com,example.org",
        )

        with duckdb.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT news_sentiment_score, news_risk_flag, news_source_count, source_provider FROM core.fact_news_signal_summary WHERE match_id = ?",
                [match_id],
            ).fetchone()
            self.assertEqual(row[2], 1)
            self.assertTrue(row[1])
            self.assertAlmostEqual(row[0], -0.5)
            self.assertEqual(row[3], "test.com,example.org")

    def test_store_in_chromadb_calls_delete_and_add(self) -> None:
        original = sys.modules.get("chromadb")
        sys.modules["chromadb"] = SimpleNamespace(PersistentClient=FakeClient)
        # 重置 news_ingestion 中的 chromadb 模块绑定，因为可能之前由于 ModuleNotFoundError 是 None
        from football_advisor import news_ingestion

        news_ingestion.chromadb = sys.modules["chromadb"]

        try:
            ingester = NewsIngester(
                collection_name="test_news",
                persist_directory="test_persist",
                database_path=str(self.db_path),
                embedding_function=object(),
            )
            doc = FetchedTextDocument(
                url="https://test.com/news1",
                title="Good news",
                text="This is text to be chunked. Second sentence is here.",
                content_hash="abc123hash",
                status="ok",
            )

            # 由于 chunk_size 默认 600，此文本会切成 1 个 chunk
            chunks_added = ingester._store_in_chromadb("M_CHROMA_TEST", [doc])
            self.assertEqual(chunks_added, 1)
            self.assertEqual(
                FakeClient.last_instance.collection_calls[0]["name"], "test_news"
            )
            self.assertIs(
                FakeClient.last_instance.collection_calls[0]["embedding_function"],
                ingester.embedding_function,
            )
            self.assertEqual(
                FakeClient.last_instance.collection_calls[0]["metadata"][
                    "embedding_model_name"
                ],
                "bge-m3",
            )
            self.assertEqual(
                FakeClient.last_instance.collection.added_metadatas[0][
                    "embedding_model_version"
                ],
                "bge-m3",
            )
        finally:
            if original is None:
                sys.modules.pop("chromadb", None)
                news_ingestion.chromadb = None
            else:
                sys.modules["chromadb"] = original
                news_ingestion.chromadb = original

    def test_ingest_news_handles_no_chromadb_gracefully(self) -> None:
        # Mock chromadb 为 None 模拟缺失
        from football_advisor import news_ingestion

        original_chromadb = news_ingestion.chromadb
        news_ingestion.chromadb = None

        try:
            ingester = NewsIngester(database_path=str(self.db_path))
            doc = FetchedTextDocument(
                url="https://test.com/news1",
                title="Rule based title",
                text="仅包含伤停和停赛",
                content_hash="abc",
                status="ok",
            )
            res = ingester.ingest_news("M_TEST_MOCK_MISSING", [doc])
            # chromadb 被跳过，但 DuckDB 依然成功
            self.assertEqual(res["chromadb_status"], "skipped_no_chromadb")
            self.assertEqual(res["duckdb_status"], "success")
            self.assertTrue(res["risk"])
        finally:
            news_ingestion.chromadb = original_chromadb

    def test_ingest_world_cup_news_writes_cross_checked_summary(self) -> None:
        from football_advisor import news_ingestion

        original_chromadb = news_ingestion.chromadb
        news_ingestion.chromadb = None
        try:
            ingester = NewsIngester(
                database_path=str(self.db_path),
                llm_router=NoExternalLLMRouter(),
            )
            documents = [
                FetchedTextDocument(
                    url="https://example.test/usa-squad",
                    title="USA World Cup squad confirmed",
                    text="最终大名单已经确认，球队士气提升，核心球员参加训练。",
                    content_hash="a" * 64,
                    status="ok",
                ),
                FetchedTextDocument(
                    url="https://example.org/mexico-injury",
                    title="Mexico injury and press conference update",
                    text="赛前发布会提到一名主力缺阵，球队需要调整战术。",
                    content_hash="b" * 64,
                    status="ok",
                ),
            ]

            result = ingester.ingest_news("M_WC_NEWS", documents)

            self.assertEqual(result["duckdb_status"], "success")
            self.assertEqual(result["source_count"], 2)
            self.assertTrue(result["requires_cross_check"])
            with duckdb.connect(str(self.db_path)) as conn:
                row = conn.execute(
                    """
                    SELECT news_source_count, source_text_hash, requires_cross_check
                    FROM core.fact_news_signal_summary
                    WHERE match_id = 'M_WC_NEWS'
                    """
                ).fetchone()
            self.assertEqual(row[0], 2)
            self.assertRegex(row[1], r"^[0-9a-f]{64}$")
            self.assertTrue(row[2])
        finally:
            news_ingestion.chromadb = original_chromadb

    def test_sync_news_data_fails_when_duckdb_write_fails(self) -> None:
        mock_search = MagicMock(spec=SearchRouter)
        mock_search.preview.return_value = SearchPreviewResult(
            query="Arsenal vs Chelsea news",
            provider="mock_search",
            status="ok",
            results=[
                SearchResultItem(
                    title="Team News",
                    url="https://mock.com/news1",
                    snippet="snippet",
                    provider="mock",
                )
            ],
        )
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_many.return_value = [
            FetchedTextDocument(
                url="https://mock.com/news1",
                title="Team News",
                text="Arsenal is in high morale.",
                content_hash="mock123hash",
                status="ok",
            )
        ]
        mock_ingester = MagicMock()
        mock_ingester.ingest_news.return_value = {
            "match_id": "M_SYNC_ERROR",
            "ingested_chunks": 1,
            "chromadb_status": "success",
            "duckdb_status": "error:disk full",
            "sentiment": 0.1,
            "risk": False,
            "source_count": 1,
        }

        coordinator = DataSyncCoordinator(
            search_router=mock_search,
            text_fetcher=mock_fetcher,
            news_ingester=mock_ingester,
        )
        result = coordinator.sync_news_data(
            MatchRequest(query="Arsenal vs Chelsea", match_id="M_SYNC_ERROR")
        )

        self.assertEqual(result.status, "failed_news_ingestion")
        self.assertEqual(result.details["duckdb_status"], "error:disk full")

    def test_find_match_id_without_kickoff_rejects_ambiguous_future_matches(
        self,
    ) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO core.dim_league_mapping (system_league_id, league_standard_name, country) VALUES ('L_EPL', 'Premier League', 'England')"
            )
            conn.execute(
                "INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name, country) VALUES ('T_ARS', 'Arsenal', 'England')"
            )
            conn.execute(
                "INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name, country) VALUES ('T_CHE', 'Chelsea', 'England')"
            )
            conn.execute("""
                INSERT INTO core.fact_match_schedule (
                    match_id, season, system_league_id, match_time, status, home_team_id, away_team_id, source_provider, updated_at
                ) VALUES
                    ('M_AMBIG_1', '2025', 'L_EPL', TIMESTAMP '2026-08-10 12:00:00', 'PRE-MATCH', 'T_ARS', 'T_CHE', 'TEST', CURRENT_TIMESTAMP),
                    ('M_AMBIG_2', '2025', 'L_EPL', TIMESTAMP '2026-09-10 12:00:00', 'PRE-MATCH', 'T_ARS', 'T_CHE', 'TEST', CURRENT_TIMESTAMP)
                """)

        from football_advisor.config import DuckDBConfig

        config = AdvisorConfig(duckdb=DuckDBConfig(database_path=str(self.db_path)))
        coordinator = DataSyncCoordinator(parent_config=config)

        resolved = coordinator._find_match_id(
            MatchRequest(
                query="Arsenal vs Chelsea", home_team="Arsenal", away_team="Chelsea"
            )
        )

        self.assertIsNone(resolved)

    def test_sync_news_data_flow(self) -> None:
        from football_advisor import news_ingestion

        original_chromadb = news_ingestion.chromadb
        news_ingestion.chromadb = None
        # 1. 初始化 Mock 数据到 DuckDB 中
        try:
            with duckdb.connect(str(self.db_path)) as conn:
                conn.execute(
                    "INSERT INTO core.dim_league_mapping (system_league_id, league_standard_name, country) VALUES ('L_EPL', 'Premier League', 'England')"
                )
                conn.execute(
                    "INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name, country) VALUES ('T_ARS', 'Arsenal', 'England')"
                )
                conn.execute(
                    "INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name, country) VALUES ('T_CHE', 'Chelsea', 'England')"
                )
                conn.execute("""
                    INSERT INTO core.fact_match_schedule (
                        match_id, season, system_league_id, match_time, status, home_team_id, away_team_id, source_provider, updated_at
                    ) VALUES ('M_SYNC_1', '2025', 'L_EPL', TIMESTAMP '2026-08-10 12:00:00', 'PRE-MATCH', 'T_ARS', 'T_CHE', 'TEST', CURRENT_TIMESTAMP)
                    """)

            # 2. Mock 搜索和抓取
            mock_search = MagicMock(spec=SearchRouter)
            mock_search.preview.return_value = SearchPreviewResult(
                query="Arsenal vs Chelsea news",
                provider="mock_search",
                status="ok",
                results=[
                    SearchResultItem(
                        title="Arsenal Team News",
                        url="https://mock.com/news1",
                        snippet="Arsenal is in high morale. No injury problems.",
                        provider="mock",
                    )
                ],
            )

            mock_fetcher = MagicMock()
            mock_fetcher.fetch_many.return_value = [
                FetchedTextDocument(
                    url="https://mock.com/news1",
                    title="Arsenal Team News",
                    text="球队取得连胜，士气和信心提升，主力球员状态大好。",
                    content_hash="mock123hash",
                    status="ok",
                )
            ]

            # 3. 构造 DataSyncCoordinator 并同步
            # 由于 AdvisorConfig 和 DuckDBConfig 是 frozen 的，需要在构造时传入配置
            from football_advisor.config import DuckDBConfig

            config = AdvisorConfig(duckdb=DuckDBConfig(database_path=str(self.db_path)))

            coordinator = DataSyncCoordinator(
                search_router=mock_search,
                text_fetcher=mock_fetcher,
                parent_config=config,
            )

            request = MatchRequest(
                query="Arsenal vs Chelsea", home_team="Arsenal", away_team="Chelsea"
            )

            result = coordinator.sync_news_data(request)

            # 4. 校验同步结果
            self.assertEqual(result.status, "success")
            self.assertEqual(result.details["match_id"], "M_SYNC_1")
            self.assertEqual(result.details["source_count"], "1")
            self.assertFalse(result.details["risk"] == "True")

            # 5. 校验 DuckDB 的摘要沉淀
            with duckdb.connect(str(self.db_path)) as conn:
                row = conn.execute(
                    "SELECT news_sentiment_score, news_risk_flag, news_source_count, source_provider FROM core.fact_news_signal_summary WHERE match_id = 'M_SYNC_1'"
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertGreater(row[0], 0.0)  # sentiment > 0 (high morale)
                self.assertFalse(row[1])  # risk is false
                self.assertEqual(row[2], 1)  # source count = 1
                self.assertEqual(row[3], "mock.com")
        finally:
            news_ingestion.chromadb = original_chromadb

    def test_sync_news_data_uses_world_cup_readiness_search_terms(self) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO core.dim_league_mapping (system_league_id, league_standard_name, country) VALUES ('WC_WORLD_CUP_2026', 'FIFA World Cup 2026', 'International')"
            )
            conn.execute(
                "INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name, country) VALUES ('WC_TEAM_USA', 'USA', 'United States')"
            )
            conn.execute(
                "INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name, country) VALUES ('WC_TEAM_MEX', 'Mexico', 'Mexico')"
            )
            conn.execute(
                """
                INSERT INTO core.fact_match_schedule (
                    match_id, season, system_league_id, match_time, status,
                    home_team_id, away_team_id, source_provider, updated_at
                ) VALUES (
                    'M_WC_QUERY', '2026', 'WC_WORLD_CUP_2026',
                    TIMESTAMP '2026-06-11 20:00:00', 'PRE-MATCH',
                    'WC_TEAM_USA', 'WC_TEAM_MEX', 'TEST', CURRENT_TIMESTAMP
                )
                """
            )

        mock_search = MagicMock(spec=SearchRouter)
        mock_search.preview.return_value = SearchPreviewResult(
            query="",
            provider="none",
            status="skipped_no_provider_configured",
        )
        from football_advisor.config import DuckDBConfig

        config = AdvisorConfig(duckdb=DuckDBConfig(database_path=str(self.db_path)))
        coordinator = DataSyncCoordinator(search_router=mock_search, parent_config=config)

        coordinator.sync_news_data(
            MatchRequest(
                query="USA vs Mexico",
                home_team="USA",
                away_team="Mexico",
                match_id="M_WC_QUERY",
            )
        )

        search_query = mock_search.preview.call_args.args[0]
        self.assertIn("USA", search_query)
        self.assertIn("Mexico", search_query)
        self.assertIn("World Cup", search_query)
        self.assertIn("squad", search_query)
        self.assertIn("injury", search_query)
        self.assertIn("press conference", search_query)


if __name__ == "__main__":
    unittest.main()
