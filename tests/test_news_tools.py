from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace

from football_advisor.chroma_collections import (
    create_bge_m3_embedding_function,
    get_or_create_news_collection,
)
from football_advisor.config import ChromaConfig
from football_advisor.news_tools import ChromaNewsSearchTool
from football_advisor.reranker import TwoStageReranker


class FakeCollection:
    def query(self, query_texts, n_results):
        return {
            "documents": [
                ["relevant team news", "unrelated transfer rumor", "missing distance"]
            ],
            "metadatas": [
                [{"team": "Arsenal"}, {"team": "Chelsea"}, {"team": "Spurs"}]
            ],
            "distances": [[0.42, 0.91]],
        }


class FakeClient:
    last_instance = None

    def __init__(self, path):
        FakeClient.last_instance = self
        self.path = path
        self.collection_calls = []

    def get_or_create_collection(self, name, **kwargs):
        self.collection_calls.append({"name": name, **kwargs})
        return FakeCollection()


class FakeCollectionWithBadDistance:
    def query(self, query_texts, n_results):
        return {
            "documents": [["good news", "bad distance news"]],
            "metadatas": [[{"team": "Arsenal"}, {"team": "Arsenal"}]],
            "distances": [[0.31, None]],
        }


class FakeClientWithBadDistance:
    def __init__(self, path):
        self.path = path

    def get_or_create_collection(self, name, **kwargs):
        return FakeCollectionWithBadDistance()


class FakeEmbeddingFunctions:
    class OllamaEmbeddingFunction:
        def __init__(self, url, model_name):
            self.url = url
            self.model_name = model_name


class FakeRealChroma:
    __version__ = "1.5.9"
    utils = SimpleNamespace(embedding_functions=FakeEmbeddingFunctions)


class ExistingCollection:
    def __init__(self, metadata):
        self.metadata = metadata


class ExistingCollectionClient:
    def __init__(self, metadata):
        self.collection = ExistingCollection(metadata)

    def get_or_create_collection(self, name, **kwargs):
        return self.collection


class FakeScorer:
    def __init__(self):
        self.documents = []

    def score_batch(self, query, documents):
        self.documents = list(documents)
        return [0.5] * len(documents)


class FailingReranker:
    def rerank(self, query, documents, top_k=None):
        raise RuntimeError("private host: http://internal-reranker:11434")


class NewsToolTests(unittest.TestCase):
    def test_reranker_without_compressor_scores_original_text(self):
        scorer = FakeScorer()
        reranker = TwoStageReranker(compressor_model=None, scorer=scorer)

        result = reranker.rerank(
            "Arsenal",
            [{"text": "original long-form news", "metadata": {}}],
        )

        self.assertIsNone(reranker.compressor)
        self.assertEqual(scorer.documents, ["original long-form news"])
        self.assertEqual(result[0]["compressed_text"], "original long-form news")

    def test_rerank_failure_preserves_documents_and_status(self):
        original = sys.modules.get("chromadb")
        sys.modules["chromadb"] = SimpleNamespace(PersistentClient=FakeClient)
        try:
            result = ChromaNewsSearchTool(
                max_distance=0.7,
                embedding_function=object(),
                reranker=FailingReranker(),
            ).search_team_news("Arsenal", limit=8)
        finally:
            if original is None:
                sys.modules.pop("chromadb", None)
            else:
                sys.modules["chromadb"] = original

        self.assertEqual(result.documents[0]["text"], "relevant team news")
        self.assertEqual(result.rerank_status, "failed")
        self.assertEqual(result.rerank_error, "News reranking failed.")
        self.assertNotIn("internal-reranker", repr(result))

    def test_filters_chromadb_results_by_max_distance(self):
        original = sys.modules.get("chromadb")
        sys.modules["chromadb"] = SimpleNamespace(PersistentClient=FakeClient)
        try:
            tool = ChromaNewsSearchTool(max_distance=0.7, embedding_function=object())
            result = tool.search_team_news("Arsenal", limit=8)
        finally:
            if original is None:
                sys.modules.pop("chromadb", None)
            else:
                sys.modules["chromadb"] = original

        self.assertIs(
            FakeClient.last_instance.collection_calls[0]["embedding_function"],
            tool.embedding_function,
        )
        self.assertEqual(
            FakeClient.last_instance.collection_calls[0]["metadata"][
                "embedding_model_version"
            ],
            "bge-m3",
        )
        self.assertEqual(len(result.documents), 1)
        doc = result.documents[0]
        self.assertEqual(doc["text"], "relevant team news")
        self.assertEqual(doc["metadata"], {"team": "Arsenal"})
        self.assertAlmostEqual(doc["distance"], 0.42, places=2)

    def test_default_embedding_function_uses_configured_ollama_model(self):
        config = ChromaConfig(
            embedding_model_name="bge-m3",
            ollama_base_url="http://localhost:11434/api/embeddings",
        )

        embedding = create_bge_m3_embedding_function(config, FakeRealChroma)

        self.assertEqual(embedding.url, "http://localhost:11434/api/embeddings")
        self.assertEqual(embedding.model_name, "bge-m3")

    def test_rejects_existing_collection_with_different_embedding_metadata(self):
        client = ExistingCollectionClient(
            {
                "hnsw:space": "cosine",
                "embedding_model_name": "legacy-default",
                "embedding_model_version": "legacy-default",
            }
        )

        with self.assertRaisesRegex(RuntimeError, "embedding metadata mismatch"):
            get_or_create_news_collection(
                client=client,
                collection_name="football_news",
                config=ChromaConfig(
                    embedding_model_name="bge-m3",
                    embedding_model_version="bge-m3",
                ),
                chromadb_module=FakeRealChroma,
                embedding_function=object(),
            )

    def test_samples_raw_chromadb_distances_for_threshold_calibration(self):
        original = sys.modules.get("chromadb")
        sys.modules["chromadb"] = SimpleNamespace(PersistentClient=FakeClient)
        try:
            distances = ChromaNewsSearchTool().sample_distances(["Arsenal"], limit=8)
        finally:
            if original is None:
                sys.modules.pop("chromadb", None)
            else:
                sys.modules["chromadb"] = original

        self.assertEqual(distances, [0.42, 0.91])

    def test_search_skips_non_numeric_distances(self):
        original = sys.modules.get("chromadb")
        sys.modules["chromadb"] = SimpleNamespace(
            PersistentClient=FakeClientWithBadDistance
        )
        try:
            result = ChromaNewsSearchTool(max_distance=0.7).search_team_news(
                "Arsenal", limit=8
            )
        finally:
            if original is None:
                sys.modules.pop("chromadb", None)
            else:
                sys.modules["chromadb"] = original

        self.assertEqual(len(result.documents), 1)
        doc = result.documents[0]
        self.assertEqual(doc["text"], "good news")
        self.assertEqual(doc["metadata"], {"team": "Arsenal"})
        self.assertAlmostEqual(doc["distance"], 0.31, places=2)


if __name__ == "__main__":
    unittest.main()
