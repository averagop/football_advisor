from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from football_advisor.pdf_text_extractor import (
    PDFTextExtractionError,
    extract_pdf_text,
    summarize_pdf_text,
)


class PDFTextExtractorTests(unittest.TestCase):
    def test_extracts_page_text_with_metadata(self):
        fake_reader = SimpleNamespace(
            metadata={
                "/Title": "世界杯名单",
                "/Author": "FIFA",
            },
            pages=[
                SimpleNamespace(extract_text=lambda: "第一页 裁判 名单"),
                SimpleNamespace(extract_text=lambda: "第二页 球员 阵容"),
            ],
        )
        fake_pypdf = SimpleNamespace(PdfReader=lambda path: fake_reader)

        with patch.dict(sys.modules, {"pypdf": fake_pypdf}):
            result = extract_pdf_text(Path("sample.pdf"))

        self.assertEqual(result.page_count, 2)
        self.assertEqual(result.character_count, len(result.text))
        self.assertIn("第一页 裁判 名单", result.text)
        self.assertEqual(result.metadata["title"], "世界杯名单")
        self.assertEqual(result.metadata["author"], "FIFA")

    def test_raises_clear_error_when_pypdf_is_missing(self):
        with patch.dict(sys.modules, {"pypdf": None}):
            with self.assertRaises(PDFTextExtractionError) as context:
                extract_pdf_text(Path("sample.pdf"))

        self.assertIn("pypdf", str(context.exception))

    def test_summarizes_world_cup_pdf_text(self):
        summary = summarize_pdf_text(
            "2026 FIFA World Cup referees referee officials "
            "players squad roster match schedule"
        )

        self.assertIn("裁判", summary.detected_topics)
        self.assertIn("参赛名单", summary.detected_topics)
        self.assertIn("赛程", summary.detected_topics)


if __name__ == "__main__":
    unittest.main()
