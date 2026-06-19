from __future__ import annotations

import logging
from dataclasses import dataclass
from math import isfinite
from typing import Any

from .chroma_collections import (
    DEFAULT_CHROMA_EMBEDDING_FUNCTION,
    get_or_create_news_collection,
)
from .config import ChromaConfig, load_config
from .reranker import TwoStageReranker


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewsSearchResult:
    documents: list[dict[str, Any]]
    status: str = "ok"
    rerank_status: str = "not_run"
    rerank_error: str | None = None


class ChromaNewsSearchTool:
    def __init__(
        self,
        collection_name: str | None = None,
        persist_directory: str | None = None,
        max_distance: float | None = None,
        config: ChromaConfig | None = None,
        embedding_function: Any = DEFAULT_CHROMA_EMBEDDING_FUNCTION,
        reranker: TwoStageReranker | None = None,
    ) -> None:
        chroma_config = config or load_config().chroma
        self.config = chroma_config
        self.collection_name = collection_name or chroma_config.collection_name
        self.persist_directory = persist_directory or chroma_config.persist_directory
        self.max_distance = (
            max_distance if max_distance is not None else chroma_config.max_distance
        )
        self.embedding_function = embedding_function
        # 新闻重排默认只使用 bge-reranker-v2-m3，不调用 Qwen 压缩（Qwen 仅用于报告备用）
        self.reranker = reranker or TwoStageReranker(
            compressor_model=None,
            scorer_model="bge-reranker-v2-m3",
        )

    def search_match_news(
        self, match_id: str, query: str, limit: int = 8
    ) -> NewsSearchResult:
        """按标准比赛 ID 过滤检索新闻，未提供 match_id 时返回 empty_search。"""
        if not match_id:
            return NewsSearchResult(documents=[], status="empty_search")

        try:
            import chromadb  # type: ignore[import-not-found]
        except ModuleNotFoundError:
            return NewsSearchResult(documents=[], status="empty_search")

        client = chromadb.PersistentClient(path=self.persist_directory)
        collection = get_or_create_news_collection(
            client=client,
            collection_name=self.collection_name,
            config=self.config,
            chromadb_module=chromadb,
            embedding_function=self.embedding_function,
        )
        result = collection.query(
            query_texts=[query],
            n_results=limit,
            where={"match_id": match_id},
        )
        docs = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        documents = []
        for doc, metadata, distance in zip(docs, metadatas, distances, strict=False):
            distance_value = _coerce_distance(distance)
            if distance_value is None or distance_value > self.max_distance:
                continue
            documents.append(
                {
                    "text": doc,
                    "metadata": metadata or {},
                    "distance": distance_value,
                }
            )

        rerank_status = "not_run"
        rerank_error = None
        if documents and self.reranker:
            try:
                documents = self.reranker.rerank(query, documents, top_k=min(limit, 5))
                rerank_status = "ok"
            except Exception as exc:
                logger.warning("News reranking failed: %s", type(exc).__name__)
                rerank_status = "failed"
                rerank_error = "News reranking failed."

        if not documents:
            return NewsSearchResult(
                documents=[],
                status="empty_search",
                rerank_status=rerank_status,
                rerank_error=rerank_error,
            )
        return NewsSearchResult(
            documents=documents,
            status="ok",
            rerank_status=rerank_status,
            rerank_error=rerank_error,
        )

    def search_team_news(self, query: str, limit: int = 8) -> NewsSearchResult:
        try:
            import chromadb  # type: ignore[import-not-found]
        except ModuleNotFoundError:
            return NewsSearchResult(documents=[])

        client = chromadb.PersistentClient(path=self.persist_directory)
        collection = get_or_create_news_collection(
            client=client,
            collection_name=self.collection_name,
            config=self.config,
            chromadb_module=chromadb,
            embedding_function=self.embedding_function,
        )
        result = collection.query(query_texts=[query], n_results=limit)
        docs = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        documents = []
        for doc, metadata, distance in zip(docs, metadatas, distances, strict=False):
            distance_value = _coerce_distance(distance)
            if distance_value is None or distance_value > self.max_distance:
                continue
            documents.append(
                {
                    "text": doc,
                    "metadata": metadata or {},
                    "distance": distance_value,
                }
            )
        result_docs = documents

        # 使用本地重排器优化相关性顺序。
        rerank_status = "not_run"
        rerank_error = None
        if result_docs and self.reranker:
            try:
                result_docs = self.reranker.rerank(query, result_docs, top_k=min(limit, 5))
                rerank_status = "ok"
            except Exception as exc:
                logger.warning("News reranking failed: %s", type(exc).__name__)
                rerank_status = "failed"
                rerank_error = "News reranking failed."

        return NewsSearchResult(
            documents=result_docs,
            rerank_status=rerank_status,
            rerank_error=rerank_error,
        )

    def sample_distances(self, queries: list[str], limit: int = 8) -> list[float]:
        try:
            import chromadb  # type: ignore[import-not-found]
        except ModuleNotFoundError:
            return []

        client = chromadb.PersistentClient(path=self.persist_directory)
        collection = get_or_create_news_collection(
            client=client,
            collection_name=self.collection_name,
            config=self.config,
            chromadb_module=chromadb,
            embedding_function=self.embedding_function,
        )
        samples: list[float] = []
        for query in queries:
            result = collection.query(query_texts=[query], n_results=limit)
            distances = result.get("distances", [[]])[0]
            samples.extend(
                distance_value
                for distance in distances
                if (distance_value := _coerce_distance(distance)) is not None
            )
        return samples


def _coerce_distance(distance: Any) -> float | None:
    try:
        distance_value = float(distance)
    except (TypeError, ValueError):
        return None
    if not isfinite(distance_value):
        return None
    return distance_value
