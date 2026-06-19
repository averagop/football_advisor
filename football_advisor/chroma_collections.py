from __future__ import annotations

import logging
from typing import Any

from .config import ChromaConfig

logger = logging.getLogger(__name__)

DEFAULT_CHROMA_EMBEDDING_FUNCTION = object()


def chroma_collection_metadata(config: ChromaConfig) -> dict[str, str]:
    return {
        "hnsw:space": "cosine",
        "embedding_model_name": config.embedding_model_name,
        "embedding_model_version": config.embedding_model_version,
    }


def get_or_create_news_collection(
    client: Any,
    collection_name: str,
    config: ChromaConfig,
    chromadb_module: Any,
    embedding_function: Any = DEFAULT_CHROMA_EMBEDDING_FUNCTION,
) -> Any:
    kwargs: dict[str, Any] = {"metadata": chroma_collection_metadata(config)}
    resolved_embedding_function = embedding_function
    if resolved_embedding_function is DEFAULT_CHROMA_EMBEDDING_FUNCTION:
        resolved_embedding_function = create_bge_m3_embedding_function(
            config, chromadb_module
        )
    if resolved_embedding_function is not None:
        kwargs["embedding_function"] = resolved_embedding_function
    try:
        collection = client.get_or_create_collection(collection_name, **kwargs)
        _validate_collection_metadata(collection, chroma_collection_metadata(config))
        return collection
    except ValueError as exc:
        if "embedding function" in str(exc).lower():
            logger.warning(
                "ChromaDB collection '%s' embedding function conflict, recreating.",
                collection_name,
            )
            try:
                client.delete_collection(collection_name)
            except Exception:
                pass
            collection = client.create_collection(collection_name, **kwargs)
            return collection
        raise


def create_bge_m3_embedding_function(config: ChromaConfig, chromadb_module: Any) -> Any:
    if not _looks_like_real_chromadb(chromadb_module):
        return None
    try:
        embedding_functions = chromadb_module.utils.embedding_functions
        return embedding_functions.OllamaEmbeddingFunction(
            url=config.ollama_base_url,
            model_name=config.embedding_model_name,
        )
    except Exception as exc:
        raise RuntimeError(
            f"ChromaDB Ollama embedding function is unavailable for {config.embedding_model_name}."
        ) from exc


def _looks_like_real_chromadb(chromadb_module: Any) -> bool:
    return bool(
        getattr(chromadb_module, "__version__", None)
        and getattr(chromadb_module, "utils", None)
    )


def _validate_collection_metadata(collection: Any, expected: dict[str, str]) -> None:
    if not hasattr(collection, "metadata"):
        return
    metadata = collection.metadata or {}

    mismatches = {
        key: metadata.get(key)
        for key, expected_value in expected.items()
        if metadata.get(key) != expected_value
    }
    if mismatches:
        raise RuntimeError(
            "ChromaDB collection embedding metadata mismatch. "
            "Rebuild or migrate the collection before mixing vector spaces."
        )
