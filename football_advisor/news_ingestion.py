from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

try:
    import chromadb  # type: ignore[import-not-found]
except ModuleNotFoundError:
    chromadb = None

from .chroma_collections import (
    DEFAULT_CHROMA_EMBEDDING_FUNCTION,
    get_or_create_news_collection,
)
from .config import AdvisorConfig, load_config
from .llm_router import LLMRouter
from .text_fetcher import FetchedTextDocument

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewsSignalAnalysis:
    sentiment: float
    risk: bool
    generated_by: str
    source_text_hash: str
    confidence: float
    requires_cross_check: bool


class NewsIngester:
    def __init__(
        self,
        collection_name: str | None = None,
        persist_directory: str | None = None,
        database_path: str | None = None,
        llm_router: LLMRouter | None = None,
        chunk_size: int = 600,
        chunk_overlap: int = 60,
        config: AdvisorConfig | None = None,
        embedding_function: Any = DEFAULT_CHROMA_EMBEDDING_FUNCTION,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than 0.")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError(
                "chunk_overlap must be non-negative and less than chunk_size."
            )

        self.config = config or load_config()
        self.collection_name = collection_name or self.config.chroma.collection_name
        self.persist_directory = (
            persist_directory or self.config.chroma.persist_directory
        )
        self.database_path = database_path or self.config.duckdb.database_path
        self.llm_router = llm_router or LLMRouter(config=self.config.llm)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.embedding_function = embedding_function

    def ingest_news(
        self,
        match_id: str,
        documents: list[FetchedTextDocument],
        home_team_id: str = "",
        away_team_id: str = "",
        kickoff_date: str = "",
        published_at: str = "",
    ) -> dict[str, Any]:
        """Ingests fetched text documents for a match into ChromaDB and summarizes signals into DuckDB."""
        valid_docs = [doc for doc in documents if doc.status == "ok" and doc.text]
        if not valid_docs:
            return {
                "match_id": match_id,
                "ingested_chunks": 0,
                "status": "empty_ingestion",
            }

        # 1. 存入 ChromaDB
        chromadb_meta = {
            "home_team_id": home_team_id or "",
            "away_team_id": away_team_id or "",
            "kickoff_date": kickoff_date or "",
            "published_at": published_at or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        chunks_count = 0
        chromadb_status = "skipped_no_chromadb"
        if chromadb is not None:
            try:
                chunks_count = self._store_in_chromadb(match_id, valid_docs, chromadb_meta)
                chromadb_status = "success"
            except Exception as exc:
                chromadb_status = f"error:{exc}"

        # 2. 提取新闻信号（情绪、风险）
        analysis = self._analyze_news_signals(valid_docs)

        # 3. 写入 DuckDB
        source_count = len(valid_docs)
        # 获取所有来源的 domain
        domains = []
        for doc in valid_docs:
            parsed = urlparse(doc.url)
            domain = parsed.netloc or "unknown"
            if domain not in domains:
                domains.append(domain)
        provider = ",".join(domains)
        last_updated_at = datetime.datetime.now(datetime.timezone.utc)

        try:
            self._write_to_duckdb(
                match_id=match_id,
                sentiment=analysis.sentiment,
                risk=analysis.risk,
                source_count=source_count,
                last_updated_at=last_updated_at,
                provider=provider,
                generated_by=analysis.generated_by,
                source_text_hash=analysis.source_text_hash,
                confidence=analysis.confidence,
                requires_cross_check=analysis.requires_cross_check,
            )
            duckdb_status = "success"
        except Exception as exc:
            duckdb_status = f"error:{exc}"

        return {
            "match_id": match_id,
            "ingested_chunks": chunks_count,
            "chromadb_status": chromadb_status,
            "duckdb_status": duckdb_status,
            "sentiment": analysis.sentiment,
            "risk": analysis.risk,
            "source_count": source_count,
            "generated_by": analysis.generated_by,
            "source_text_hash": analysis.source_text_hash,
            "confidence": analysis.confidence,
            "requires_cross_check": analysis.requires_cross_check,
        }

    def _chunk_text(self, text: str) -> list[str]:
        if not text:
            return []
        chunks = []
        start = 0
        text_len = len(text)
        while start < text_len:
            end = min(start + self.chunk_size, text_len)
            # 寻找在分割符边界，以保持句子完整
            if end < text_len:
                found_idx = -1
                search_start = start + self.chunk_overlap
                for sep in (". ", "? ", "! ", "\n", "。"):
                    idx = text.rfind(sep, search_start, end)
                    if idx != -1:
                        found_idx = max(found_idx, idx + len(sep))
                if found_idx != -1 and found_idx - self.chunk_overlap > start:
                    end = found_idx

            chunks.append(text[start:end].strip())
            start = end - self.chunk_overlap
            if start >= text_len - self.chunk_overlap:
                break
        return [c for c in chunks if c]

    def _store_in_chromadb(
        self,
        match_id: str,
        documents: list[FetchedTextDocument],
        extra_metadata: dict[str, str] | None = None,
    ) -> int:
        if chromadb is None:
            return 0

        extra = extra_metadata or {}
        client = chromadb.PersistentClient(path=self.persist_directory)
        collection = get_or_create_news_collection(
            client=client,
            collection_name=self.collection_name,
            config=self.config.chroma,
            chromadb_module=chromadb,
            embedding_function=self.embedding_function,
        )

        # 幂等性：先清理该比赛对应的数据
        collection.delete(where={"match_id": match_id})

        chunks_to_add = []
        ids = []
        metadatas = []
        chunk_counter = 0

        for doc in documents:
            chunks = self._chunk_text(doc.text)
            parsed_url = urlparse(doc.url)
            domain = parsed_url.netloc or "unknown"

            for i, chunk in enumerate(chunks):
                chunk_id = f"{match_id}_{doc.content_hash[:10]}_{chunk_counter}"
                chunks_to_add.append(chunk)
                ids.append(chunk_id)
                metadatas.append(
                    {
                        "match_id": match_id,
                        "home_team_id": extra.get("home_team_id", ""),
                        "away_team_id": extra.get("away_team_id", ""),
                        "kickoff_date": extra.get("kickoff_date", ""),
                        "published_at": extra.get("published_at", ""),
                        "source": domain,
                        "source_url": doc.url,
                        "title": doc.title,
                        "chunk_index": i,
                        "content_hash": doc.content_hash,
                        "embedding_model_name": self.config.chroma.embedding_model_name,
                        "embedding_model_version": self.config.chroma.embedding_model_version,
                    }
                )
                chunk_counter += 1

        if chunks_to_add:
            collection.add(documents=chunks_to_add, ids=ids, metadatas=metadatas)
        return len(chunks_to_add)

    def _analyze_news_signals(
        self, documents: list[FetchedTextDocument]
    ) -> NewsSignalAnalysis:
        """Analyzes sentiment and risk indicators from news documents."""
        # 拼接全部新闻文本（主要用标题和正文的前 500 字符，以防超出大模型上限）
        text_samples = []
        for doc in documents:
            snippet = doc.text[:500]
            text_samples.append(f"Title: {doc.title}\nContent: {snippet}...")
        combined_text = "\n\n---\n\n".join(text_samples)
        source_text_hash = hashlib.sha256(combined_text.encode("utf-8")).hexdigest()

        if self._can_use_external_llm():
            system_prompt = (
                "You are an expert football analyst. Analyze the provided match news articles and extract "
                "the overall team sentiment score (ranging from -1.0 for highly negative/crises to 1.0 for highly positive/confident) "
                "and whether there is any significant risk flag (true/false) such as key player injuries, coach sackings, internal conflicts, or bad weather. "
                "Your output MUST be a valid JSON object with keys: 'sentiment_score' (float), 'risk_flag' (bool), 'confidence' (float), and 'reason' (string)."
            )
            user_prompt = f"Analyze these match articles:\n\n{combined_text}"

            try:
                external_content = self.llm_router.generate_external(
                    system_prompt, user_prompt
                )
                if external_content and external_content != user_prompt:
                    match = re.search(r"\{.*\}", external_content, re.DOTALL)
                    if match:
                        data = json.loads(match.group(0))
                        sentiment = max(
                            -1.0, min(1.0, float(data.get("sentiment_score", 0.0)))
                        )
                        risk = _parse_bool(data.get("risk_flag", False))
                        confidence = max(
                            0.0, min(1.0, float(data.get("confidence", 0.65)))
                        )
                        return NewsSignalAnalysis(
                            sentiment=sentiment,
                            risk=risk,
                            generated_by="external_llm",
                            source_text_hash=source_text_hash,
                            confidence=confidence,
                            requires_cross_check=True,
                        )
            except Exception as exc:
                logger.warning(
                    "External news signal extraction failed; using rules: %s",
                    type(exc).__name__,
                )

        sentiment, risk = self._analyze_by_rules(combined_text)
        return NewsSignalAnalysis(
            sentiment=sentiment,
            risk=risk,
            generated_by="rules",
            source_text_hash=source_text_hash,
            confidence=0.45,
            requires_cross_check=True,
        )

    def _can_use_external_llm(self) -> bool:
        return bool(
            getattr(self.llm_router, "external_base_url", None)
            and getattr(self.llm_router, "external_api_key", None)
        )

    def _analyze_by_rules(self, text: str) -> tuple[float, bool]:
        risk_words = {
            "伤停",
            "缺阵",
            "受伤",
            "停赛",
            "冲突",
            "矛盾",
            "罢工",
            "欠薪",
            "内讧",
            "红牌",
            "暴雨",
            "危机",
            "换帅",
            "解雇",
            "缺席",
        }
        has_risk = any(word in text for word in risk_words)

        pos_words = {
            "连胜",
            "士气",
            "复出",
            "复归",
            "回归",
            "胜利",
            "信心",
            "主力伤愈",
            "利好",
            "优势",
            "状态大好",
            "强劲",
        }
        neg_words = {
            "伤病",
            "低迷",
            "连败",
            "停赛",
            "惨败",
            "不和",
            "不利",
            "劣势",
            "堪忧",
            "折损",
            "缺席",
            "缺阵",
        }

        pos_count = sum(text.count(word) for word in pos_words)
        neg_count = sum(text.count(word) for word in neg_words)

        total = pos_count + neg_count
        if total == 0:
            sentiment = 0.0
        else:
            sentiment = (pos_count - neg_count) / total
            sentiment = max(-0.8, min(0.8, sentiment))

        return sentiment, has_risk

    def _write_to_duckdb(
        self,
        match_id: str,
        sentiment: float,
        risk: bool,
        source_count: int,
        last_updated_at: datetime.datetime,
        provider: str,
        generated_by: str = "rules",
        source_text_hash: str | None = None,
        confidence: float = 0.45,
        requires_cross_check: bool = True,
    ) -> None:
        try:
            import duckdb  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "duckdb is not installed. Install project requirements first."
            ) from exc

        with duckdb.connect(self.database_path) as conn:
            normalized_hash = (
                source_text_hash
                or hashlib.sha256(
                    f"{match_id}:{provider}:{sentiment}:{risk}:{source_count}".encode(
                        "utf-8"
                    )
                ).hexdigest()
            )
            # 事务性写入新闻摘要：先删除后插入
            conn.execute("BEGIN TRANSACTION")
            try:
                conn.execute(
                    "DELETE FROM core.fact_news_signal_summary WHERE match_id = ?",
                    [match_id],
                )
                conn.execute(
                    """
                    INSERT INTO core.fact_news_signal_summary (
                        match_id,
                        news_sentiment_score,
                        news_risk_flag,
                        news_source_count,
                        news_last_updated_at,
                        source_provider,
                        generated_by,
                        source_text_hash,
                        confidence,
                        requires_cross_check
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        match_id,
                        sentiment,
                        risk,
                        source_count,
                        last_updated_at,
                        provider,
                        generated_by,
                        normalized_hash,
                        confidence,
                        requires_cross_check,
                    ],
                )
                conn.execute("COMMIT")
            except Exception as exc:
                conn.execute("ROLLBACK")
                raise exc


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)
