"""
两级重排序器 (Two-Stage Reranker)

阶段 1 — 可选内容压缩:
  生产新闻链路默认跳过生成式压缩，直接使用原始文档文本。

阶段 2 — 交叉编码器打分:
  对 (query, 压缩文档) 逐对计算相关性分数。
  优先使用本地 bge-reranker-v2-m3 (sentence_transformers 交叉编码器)，
  不可用时回退到 qwen2.5:7b (Ollama prompt 打分)。

使用方式:
  reranker = TwoStageReranker(
      compressor_model=None,
      scorer_model="bge-reranker-v2-m3",  # 自动使用本地交叉编码器
  )
  results = reranker.rerank(query, documents)
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# bge-reranker-v2-m3 本地模型路径（OpenWebUI HuggingFace 缓存）
_BGE_RERANKER_V2_M3_LOCAL_PATH = os.environ.get(
    "BGE_RERANKER_PATH",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "..", "Ollama", "open-webui", "cache", "embedding", "models",
        "models--BAAI--bge-reranker-v2-m3"
    ),
)

# ============================================================
# Stage 1: 文档压缩器
# ============================================================

_COMPRESS_SYSTEM_PROMPT = """你是一个信息提取助手。给定一个查询和一篇文档，请提取文档中与查询最相关的核心内容。

规则：
- 只提取直接相关的句子，忽略背景介绍、广告、无关段落
- 保留关键事实：球员名、球队名、比分、伤病、战术、数据
- 压缩结果控制在 200 字以内
- 如果文档完全不相关，返回 "无相关内容"

严格按以下 JSON 格式输出，不要输出任何其他内容：
{"compressed": "提取的核心内容"}"""


class OllamaCompressor:
    """用 Ollama LLM 对单篇文档做语义压缩。"""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b",
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def compress(self, query: str, document_text: str) -> str:
        """压缩一篇文档，返回精炼后的文本。"""
        if not document_text.strip():
            return ""

        import requests

        truncated = document_text[:2000]  # 防止输入过长
        user_prompt = f"查询：{query}\n\n文档：{truncated}\n\n请提取与查询最相关的核心内容。"

        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": _COMPRESS_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "options": {"temperature": 0.0, "num_predict": 300},
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            content = response.json().get("message", {}).get("content", "")

            # 解析 JSON
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                parsed = json.loads(content[start:end])
                compressed = parsed.get("compressed", "")
                if compressed and compressed != "无相关内容":
                    return compressed
            return ""
        except Exception as exc:
            logger.warning("compressor failed for doc: %s", exc)
            return document_text[:200]  # fallback: 截断前 200 字

    def compress_batch(self, query: str, documents: list[dict[str, Any]]) -> list[str]:
        """批量压缩，保留顺序。"""
        results = []
        for doc in documents:
            text = doc.get("text", "")
            compressed = self.compress(query, text)
            results.append(compressed or text[:200])
        return results


# ============================================================
# Stage 2: 交叉编码器打分
# ============================================================

_SCORER_SYSTEM_PROMPT = """你是一个文本相关性评分器。给定一个查询和一段文档摘要，请评估文档与查询的相关性。

评分标准：
- 10: 完全匹配，直接回答查询（包含阵容、伤病、战术、预测等关键信息）
- 7-9: 高度相关，包含大部分关键信息
- 4-6: 部分相关
- 1-3: 微弱相关
- 0: 完全不相关

只输出一个 0-10 的整数分数，不要输出任何其他内容。"""


class OllamaScorer:
    """用 Ollama LLM 对 (query, doc) 做逐对相关性打分。

    当 bge-reranker-v2-m3 在 Ollama 可用时，替换 model 参数即可，
    代码会自动使用 cross-encoder 调用格式。
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b",
        timeout: int = 15,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._is_cross_encoder = "reranker" in model.lower()

    def score(self, query: str, document: str) -> float:
        """对单篇文档打分，返回 0-1 的归一化分数。"""
        if not document.strip():
            return 0.0

        import requests

        if self._is_cross_encoder:
            return self._score_cross_encoder(query, document)

        return self._score_llm(query, document)

    def _score_llm(self, query: str, document: str) -> float:
        """LLM prompt 方式打分。"""
        import requests

        truncated = document[:600]
        user_prompt = f"查询：{query}\n\n文档：{truncated}"

        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": _SCORER_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "options": {"temperature": 0.0, "num_predict": 5},
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            content = response.json().get("message", {}).get("content", "0")
            score = float(content.strip()) / 10.0
            return max(0.0, min(1.0, score))
        except Exception as exc:
            logger.warning("scorer failed: %s", exc)
            return 0.0

    def _score_cross_encoder(self, query: str, document: str) -> float:
        """bge-reranker-v2-m3 交叉编码器格式 (Ollama generate)。"""
        import requests

        # cross-encoder 输入格式: query [SEP] passage
        prompt = f"{query} [SEP] {document[:400]}"

        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": 1, "temperature": 0.0},
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            raw = response.json().get("response", "0")
            # cross-encoder 输出可能是分数文本或 logit
            try:
                score = float(raw.strip())
                return max(0.0, min(1.0, score))
            except ValueError:
                return 0.5  # 无法解析，返回中性分
        except Exception as exc:
            logger.warning("cross-encoder failed: %s", exc)
            return 0.0

    def score_batch(self, query: str, documents: list[str]) -> list[float]:
        """逐对打分。"""
        return [self.score(query, doc) for doc in documents]


# ============================================================
# Stage 2 替代: 本地交叉编码器
# ============================================================

class LocalCrossEncoder:
    """使用 sentence_transformers 加载本地 bge-reranker-v2-m3 交叉编码器。

    从 OpenWebUI 的 HuggingFace 缓存目录直接加载模型，不需要 Ollama。
    模型在首次使用时懒加载，避免不必要的内存占用。
    """

    def __init__(
        self,
        model_path: str | None = None,
        device: str = "cpu",
    ) -> None:
        self._model_path = model_path or _BGE_RERANKER_V2_M3_LOCAL_PATH
        self._device = device
        self._model: Any = None
        self._available: bool | None = None  # None = 未检测

    @property
    def available(self) -> bool:
        """检测本地模型是否可用（文件存在 + 能加载）。"""
        if self._available is not None:
            return self._available
        self._available = self._check_files()
        return self._available

    def _check_files(self) -> bool:
        """检查模型文件是否存在。"""
        path = Path(self._model_path)
        if not path.exists():
            return False
        # 查找 snapshots 目录中的模型文件
        snapshots_dir = path / "snapshots"
        if not snapshots_dir.exists():
            return False
        for snapshot in sorted(snapshots_dir.iterdir(), reverse=True):
            if (snapshot / "config.json").exists() and (snapshot / "model.safetensors").exists():
                return True
        return False

    def _ensure_loaded(self) -> Any:
        """懒加载模型。"""
        if self._model is not None:
            return self._model

        from sentence_transformers import CrossEncoder  # type: ignore[import-not-found]

        path = Path(self._model_path)
        snapshots_dir = path / "snapshots"
        # 找到最新的可用 snapshot
        snapshot_path = None
        for snapshot in sorted(snapshots_dir.iterdir(), reverse=True):
            if (snapshot / "config.json").exists() and (snapshot / "model.safetensors").exists():
                snapshot_path = str(snapshot)
                break

        if not snapshot_path:
            raise FileNotFoundError(f"No valid snapshot found in {snapshots_dir}")

        logger.info("Loading bge-reranker-v2-m3 from %s on %s", snapshot_path, self._device)
        self._model = CrossEncoder(snapshot_path, device=self._device)
        logger.info("bge-reranker-v2-m3 loaded successfully")
        return self._model

    def score(self, query: str, document: str) -> float:
        """对单篇文档打分，返回 0-1 归一化分数。"""
        if not document.strip():
            return 0.0
        try:
            model = self._ensure_loaded()
            # CrossEncoder.predict 返回 numpy ndarray，用 .item() 取标量
            pair = [(query, document[:400])]
            raw = model.predict(pair)
            import math
            import numpy as np
            if isinstance(raw, np.ndarray):
                raw = raw.item(0)
            else:
                raw = float(raw)
            score = 1.0 / (1.0 + math.exp(-raw))  # sigmoid
            return score
        except Exception as exc:
            logger.warning("LocalCrossEncoder.score failed: %s", exc)
            return 0.0

    def score_batch(self, query: str, documents: list[str]) -> list[float]:
        """批量打分。"""
        if not documents:
            return []
        try:
            model = self._ensure_loaded()
            pairs = [(query, doc[:400]) for doc in documents]
            raw_scores = model.predict(pairs)
            import math
            import numpy as np
            if isinstance(raw_scores, np.ndarray):
                raw_scores = raw_scores.tolist()
            return [1.0 / (1.0 + math.exp(-float(s))) for s in raw_scores]
        except Exception as exc:
            logger.warning("LocalCrossEncoder.score_batch failed: %s", exc)
            return [0.0] * len(documents)


# ============================================================
# 两级合并
# ============================================================

class TwoStageReranker:
    """两级重排：Stage 1 压缩 → Stage 2 打分 → 排序返回。

    scorer 优先使用 LocalCrossEncoder (bge-reranker-v2-m3)，
    不可用时回退到 OllamaScorer (qwen2.5:7b prompt 打分)。
    """

    _shared_cross_encoder: LocalCrossEncoder | None = None

    @classmethod
    def _get_or_create_cross_encoder(cls) -> LocalCrossEncoder | None:
        """获取共享的交叉编码器实例（懒加载，避免重复加载模型）。"""
        if cls._shared_cross_encoder is None:
            ce = LocalCrossEncoder()
            if ce.available:
                cls._shared_cross_encoder = ce
            else:
                return None
        return cls._shared_cross_encoder

    def __init__(
        self,
        compressor_model: str | None = None,
        scorer_model: str = "qwen2.5:7b",
        base_url: str = "http://localhost:11434",
        timeout: int = 30,
        scorer: Any = None,
    ) -> None:
        self.compressor = (
            OllamaCompressor(
                base_url=base_url,
                model=compressor_model,
                timeout=timeout,
            )
            if compressor_model
            else None
        )

        if scorer is not None:
            self.scorer = scorer
        elif "reranker" in scorer_model.lower() or "bge" in scorer_model.lower():
            # 尝试使用本地 bge-reranker 交叉编码器
            local = self._get_or_create_cross_encoder()
            if local is not None:
                logger.info("TwoStageReranker: using LocalCrossEncoder (bge-reranker-v2-m3) for scoring")
                self.scorer = local
            else:
                logger.warning("bge-reranker-v2-m3 not found locally, falling back to OllamaScorer")
                self.scorer = OllamaScorer(base_url=base_url, model=scorer_model, timeout=timeout)
        else:
            self.scorer = OllamaScorer(base_url=base_url, model=scorer_model, timeout=timeout)

    def rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        top_k: int | None = None,
    ) -> list[dict[str, Any]]:
        """两级重排主入口。

        Args:
            query: 搜索查询。
            documents: ChromaDB 检索结果，每个 doc 需含 'text' 键。
            top_k: 返回 top-K。None 则返回全部排序结果。

        Returns:
            按相关性分数降序排列的文档列表，额外字段: compressed_text, rerank_score。
        """
        if not documents:
            return []

        # Stage 1: 压缩
        if self.compressor is not None:
            logger.info("TwoStageReranker: compressing %d documents", len(documents))
            try:
                compressed_texts = self.compressor.compress_batch(query, documents)
            except Exception as exc:
                logger.warning("Stage 1 compression failed: %s", type(exc).__name__)
                compressed_texts = [d.get("text", "") for d in documents]
        else:
            compressed_texts = [d.get("text", "") for d in documents]

        # Stage 2: 打分
        logger.info("TwoStageReranker: scoring %d compressed documents", len(compressed_texts))
        try:
            scores = self.scorer.score_batch(query, compressed_texts)
        except Exception as exc:
            logger.warning("Stage 2 scoring failed: %s", exc)
            scores = [0.0] * len(documents)

        # 合并结果
        for i, doc in enumerate(documents):
            doc["compressed_text"] = compressed_texts[i] if i < len(compressed_texts) else ""
            doc["rerank_score"] = scores[i] if i < len(scores) else 0.0

        sorted_docs = sorted(documents, key=lambda d: d.get("rerank_score", 0.0), reverse=True)

        if top_k is not None and top_k < len(sorted_docs):
            return sorted_docs[:top_k]
        return sorted_docs


# ============================================================
# 兼容旧接口
# ============================================================

class OllamaReranker(TwoStageReranker):
    """向后兼容：单级 LLM 打分（等同于 TwoStageReranker 但只做 Stage 2）。
    
    推荐使用 TwoStageReranker 以获得更好的精度。
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b",
        timeout: int = 30,
    ) -> None:
        super().__init__(compressor_model=model, scorer_model=model, base_url=base_url, timeout=timeout)
