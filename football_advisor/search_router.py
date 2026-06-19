from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request
from urllib.request import urlopen as default_urlopen

from .config import SearchConfig, load_config

UrlOpen = Callable[[Request, int], Any]


@dataclass(frozen=True)
class SearchResultItem:
    title: str
    url: str
    snippet: str
    provider: str
    score: float | None = None


@dataclass(frozen=True)
class SearchPreviewResult:
    query: str
    provider: str
    status: str
    ingested: bool = False
    results: list[SearchResultItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class SearchRouter:
    def __init__(
        self,
        config: SearchConfig | None = None,
        urlopen: UrlOpen | None = None,
    ) -> None:
        self.config = config or load_config().search
        self.urlopen = urlopen or default_urlopen

    def preview(self, query: str, limit: int = 5) -> SearchPreviewResult:
        normalized_query = query.strip()
        if not normalized_query:
            return SearchPreviewResult(
                query=query,
                provider="none",
                status="empty_query",
            )

        capped_limit = max(1, min(limit, 20))
        errors: list[str] = []

        if self.config.searxng_base_url:
            try:
                return self._preview_searxng(normalized_query, capped_limit)
            except Exception as exc:  # pragma: no cover - defensive provider boundary
                errors.append(f"searxng:{exc}")

        if self.config.serper_api_key:
            try:
                return self._preview_serper(normalized_query, capped_limit, errors)
            except Exception as exc:  # pragma: no cover - defensive provider boundary
                errors.append(f"serper:{exc}")

        status = "skipped_no_provider_configured" if not errors else "provider_error"
        return SearchPreviewResult(
            query=normalized_query,
            provider="none",
            status=status,
            errors=errors,
        )

    def _preview_searxng(self, query: str, limit: int) -> SearchPreviewResult:
        base_url = self.config.searxng_base_url.rstrip("/")
        payload = self._get_json(
            f"{base_url}/search?"
            + urlencode(
                {
                    "q": query,
                    "format": "json",
                    "language": "auto",
                    "safesearch": 1,
                    "pageno": 1,
                    "categories": "general",
                }
            )
        )
        results = [
            SearchResultItem(
                title=str(item.get("title", "")).strip(),
                url=str(item.get("url", "")).strip(),
                snippet=str(item.get("content", "")).strip(),
                provider="searxng",
                score=_optional_float(item.get("score")),
            )
            for item in payload.get("results", [])[:limit]
            if item.get("title") and item.get("url")
        ]
        return SearchPreviewResult(
            query=query,
            provider="searxng",
            status="ok",
            results=results,
        )

    def _preview_serper(
        self,
        query: str,
        limit: int,
        errors: list[str],
    ) -> SearchPreviewResult:
        payload_bytes = json.dumps({"q": query, "num": min(limit, 10)}).encode("utf-8")
        request = Request(
            "https://google.serper.dev/search",
            data=payload_bytes,
            headers={
                "X-API-KEY": self.config.serper_api_key,
                "Content-Type": "application/json",
            },
        )
        with self.urlopen(request, timeout=self.config.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))

        results = [
            SearchResultItem(
                title=str(item.get("title", "")).strip(),
                url=str(item.get("link", "")).strip(),
                snippet=str(item.get("snippet", "")).strip(),
                provider="serper",
            )
            for item in payload.get("organic", [])[:limit]
            if item.get("title") and item.get("link")
        ]
        return SearchPreviewResult(
            query=query,
            provider="serper",
            status="ok",
            results=results,
            errors=errors,
        )

    def _get_json(self, url: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            },
        )
        with self.urlopen(request, timeout=self.config.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
