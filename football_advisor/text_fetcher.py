from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request
from urllib.request import urlopen as default_urlopen

from .url_safety import validate_url, build_safe_urlopen

UrlOpen = Callable[[Request, int], Any]


@dataclass(frozen=True)
class FetchedTextDocument:
    url: str
    title: str
    text: str
    content_hash: str
    status: str
    ingested: bool = False
    error: str | None = None


class TextFetcher:
    def __init__(
        self,
        urlopen: UrlOpen | None = None,
        timeout_seconds: int = 15,
        min_text_length: int = 150,
        require_title: bool = True,
    ) -> None:
        if urlopen is not None:
            self.urlopen = urlopen
            self._use_safe_opener = False
        else:
            self.urlopen = build_safe_urlopen()
            self._use_safe_opener = True
        self.timeout_seconds = timeout_seconds
        self.min_text_length = min_text_length
        self.require_title = require_title

    def fetch_many(self, urls: list[str]) -> list[FetchedTextDocument]:
        documents: list[FetchedTextDocument] = []
        seen_hashes: set[str] = set()
        for url in urls:
            document = self.fetch_one(url)
            if document.status != "ok":
                documents.append(document)
                continue
            if document.content_hash in seen_hashes:
                continue
            seen_hashes.add(document.content_hash)
            documents.append(document)
        return documents

    def fetch_one(self, url: str) -> FetchedTextDocument:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return _empty_document(url, "unsupported_url_scheme")

        # URL 安全验证
        safe, reason = validate_url(url)
        if not safe:
            return _empty_document(url, f"unsafe_url:{reason}")

        try:
            request = Request(url, headers={"User-Agent": "football-advisor/0.1"})
            with self.urlopen(request, timeout=self.timeout_seconds) as response:
                raw_html = response.read()
        except Exception as exc:  # pragma: no cover - defensive network boundary
            return _empty_document(url, "fetch_error", str(exc))

        title, text = _extract_text(raw_html.decode("utf-8", errors="replace"))
        if not text:
            return _empty_document(url, "empty_text")
        if self.require_title and not title:
            return _empty_document(url, "missing_title")
        if len(text) < self.min_text_length:
            return _empty_document(url, "text_too_short")

        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return FetchedTextDocument(
            url=url,
            title=title,
            text=text,
            content_hash=content_hash,
            status="ok",
        )


class _ReadableHtmlParser(HTMLParser):
    ignored_tags = {"script", "style", "noscript", "nav", "footer", "header", "aside"}
    block_tags = {"p", "br", "div", "article", "section", "h1", "h2", "h3", "li"}

    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._ignored_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        normalized = tag.lower()
        if normalized in self.ignored_tags:
            self._ignored_depth += 1
            return
        if normalized == "title":
            self._in_title = True
        if normalized in self.block_tags:
            self.text_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in self.ignored_tags and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if normalized == "title":
            self._in_title = False
        if normalized in self.block_tags:
            self.text_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        self.text_parts.append(data)


def _extract_text(raw_html: str) -> tuple[str, str]:
    parser = _ReadableHtmlParser()
    parser.feed(raw_html)
    title = _normalize_text(" ".join(parser.title_parts))
    text = _normalize_text(" ".join(parser.text_parts))
    return title, text


def _normalize_text(value: str) -> str:
    unescaped = html.unescape(value)
    return re.sub(r"\s+", " ", unescaped).strip()


def _empty_document(
    url: str,
    status: str,
    error: str | None = None,
) -> FetchedTextDocument:
    return FetchedTextDocument(
        url=url,
        title="",
        text="",
        content_hash="",
        status=status,
        error=error,
    )
