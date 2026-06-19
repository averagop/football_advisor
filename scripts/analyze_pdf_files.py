from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from football_advisor.pdf_text_extractor import extract_pdf_text, summarize_pdf_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PDF 文本解析与基础摘要")
    parser.add_argument("paths", nargs="+", help="PDF 文件路径")
    parser.add_argument("--preview-chars", type=int, default=500, help="预览字符数")
    args = parser.parse_args(argv)

    for raw_path in _expand_paths(args.paths):
        path = Path(raw_path)
        result = extract_pdf_text(path)
        summary = summarize_pdf_text(result.text, result.page_count)
        print("=" * 72)
        print(f"文件: {path.name}")
        print(f"页数: {result.page_count}")
        print(f"字符数: {result.character_count}")
        print(f"识别主题: {', '.join(summary.detected_topics) or '未识别'}")
        if result.metadata:
            print("元数据:")
            for key in sorted(result.metadata):
                print(f"- {key}: {result.metadata[key]}")
        print("预览:")
        print(summary.preview[: max(0, args.preview_chars)])
    return 0


def _expand_paths(paths: list[str]) -> list[Path]:
    expanded: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if any(char in raw_path for char in "*?"):
            matches = sorted(path.parent.glob(path.name))
            expanded.extend(match for match in matches if match.is_file())
        else:
            expanded.append(path)
    return expanded


if __name__ == "__main__":
    raise SystemExit(main())
