from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class PDFTextExtractionError(RuntimeError):
    pass


@dataclass(frozen=True)
class PDFTextExtractionResult:
    path: str
    page_count: int
    character_count: int
    text: str
    pages: list[str]
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PDFTextSummary:
    page_count: int
    character_count: int
    detected_topics: list[str]
    preview: str


def extract_pdf_text(path: str | Path) -> PDFTextExtractionResult:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise PDFTextExtractionError(
            "缺少 pypdf 依赖；请先安装项目依赖或运行 `pip install pypdf`。"
        ) from exc

    pdf_path = Path(path)
    try:
        reader = PdfReader(str(pdf_path))
    except Exception as exc:
        raise PDFTextExtractionError(f"PDF 读取失败: {pdf_path}") from exc

    pages: list[str] = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        pages.append(page_text.strip())

    text = "\n\n".join(page for page in pages if page)
    return PDFTextExtractionResult(
        path=str(pdf_path),
        page_count=len(reader.pages),
        character_count=len(text),
        text=text,
        pages=pages,
        metadata=_normalize_metadata(getattr(reader, "metadata", None)),
    )


def summarize_pdf_text(text: str, page_count: int = 0) -> PDFTextSummary:
    normalized = text.lower()
    topics: list[str] = []
    topic_keywords = [
        ("裁判", ("referee", "officials", "裁判")),
        ("参赛名单", ("squad", "roster", "players", "player list", "球员", "名单", "陣容")),
        ("赛程", ("schedule", "fixture", "match", "赛程", "賽程")),
        ("规则/赛事说明", ("regulation", "format", "qualification", "规则", "賽制", "赛制")),
    ]
    for topic, keywords in topic_keywords:
        if any(keyword in normalized for keyword in keywords):
            topics.append(topic)
    preview = " ".join(text.split())[:500]
    return PDFTextSummary(
        page_count=page_count,
        character_count=len(text),
        detected_topics=topics,
        preview=preview,
    )


def _normalize_metadata(metadata: Any) -> dict[str, str]:
    if not metadata:
        return {}
    mapping = {
        "/Title": "title",
        "/Author": "author",
        "/Subject": "subject",
        "/Creator": "creator",
        "/Producer": "producer",
        "/CreationDate": "creation_date",
        "/ModDate": "modified_date",
    }
    normalized: dict[str, str] = {}
    for raw_key, clean_key in mapping.items():
        value = metadata.get(raw_key) if hasattr(metadata, "get") else None
        if value:
            normalized[clean_key] = str(value)
    return normalized
