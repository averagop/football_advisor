r"""PDF → staging → DuckDB 管线执行脚本。

用法:
  .\.runtime\python\python.exe scripts\run_pdf_staging.py "C:/Users/admin/Desktop/*.pdf"
  .\.runtime\python\python.exe scripts\run_pdf_staging.py "C:/Users/admin/Desktop/referee.pdf" "C:/Users/admin/Desktop/squad.pdf"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_DB_PATH = ROOT / "football_system.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PDF → staging → DuckDB 管线")
    parser.add_argument("paths", nargs="+", help="PDF 文件路径，支持通配符")
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help=f"DuckDB 路径，默认 {DEFAULT_DB_PATH}",
    )
    parser.add_argument(
        "--skip-core",
        action="store_true",
        help="只运行到 staging，不执行实体映射和交叉校验写 core",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只解析不写库",
    )
    args = parser.parse_args(argv)

    import duckdb

    from football_advisor.db_schema import apply_schema
    from football_advisor.pdf_staging_pipeline import (
        run_cross_validation,
        run_entity_mapping,
        run_pdf_staging_pipeline,
    )

    pdf_paths = _expand_paths(args.paths)
    if not pdf_paths:
        print("未找到任何 PDF 文件")
        return 1

    print("=" * 72)
    print("PDF → staging → DuckDB 管线")
    print(f"PDF 数量: {len(pdf_paths)}")
    for p in pdf_paths:
        print(f"  - {p}")
    print(f"数据库: {args.db_path}")
    print("=" * 72)

    if args.dry_run:
        from football_advisor.pdf_text_extractor import extract_pdf_text, summarize_pdf_text

        for pdf_path in pdf_paths:
            result = extract_pdf_text(pdf_path)
            summary = summarize_pdf_text(result.text, result.page_count)
            print(f"\n文件: {Path(pdf_path).name}")
            print(f"  页数: {result.page_count}")
            print(f"  字符数: {result.character_count}")
            print(f"  主题: {', '.join(summary.detected_topics) or '未识别'}")
        print("\n[Dry Run] 未写库")
        return 0

    with duckdb.connect(str(args.db_path)) as conn:
        apply_schema(conn)

        # 阶段 1: PDF → staging
        print("\n[阶段 1] PDF 文本抽取 → staging 候选")
        staging_result = run_pdf_staging_pipeline(pdf_paths, conn)
        print(f"  裁判候选: {len(staging_result.referee_candidates)}")
        print(f"  球员候选: {len(staging_result.player_candidates)}")
        print(f"  赛程候选: {len(staging_result.schedule_candidates)}")
        if staging_result.warnings:
            for w in staging_result.warnings:
                print(f"  [WARN] {w}")

        if args.skip_core:
            print("\n[跳过] 实体映射和交叉校验（--skip-core）")
            return 0

        # 阶段 2: 实体映射
        print("\n[阶段 2] 实体映射")
        mapping_result = run_entity_mapping(conn, staging_result)
        print(f"  裁判: 映射 {mapping_result.referees_mapped} / 未映射 {mapping_result.referees_unmapped}")
        print(f"  球员: 映射 {mapping_result.players_mapped} / 未映射 {mapping_result.players_unmapped}")
        print(f"  赛程: 映射 {mapping_result.schedules_mapped} / 未映射 {mapping_result.schedules_unmapped}")

        # 阶段 3: 交叉校验
        print("\n[阶段 3] 交叉校验 → core / No Bet 证据")
        validation_result = run_cross_validation(conn, staging_result)
        print(f"  裁判: 通过 {validation_result.referees_validated} / 拒绝 {validation_result.referees_rejected}")
        print(f"  球员: 通过 {validation_result.players_validated} / 拒绝 {validation_result.players_rejected}")
        print(f"  赛程: 通过 {validation_result.schedules_validated} / 拒绝 {validation_result.schedules_rejected}")
        print(f"  写入 core: {validation_result.core_writes}")
        print(f"  No Bet 证据: {validation_result.no_bet_evidence}")

    print("\n" + "=" * 72)
    print("PDF → staging → DuckDB 管线完成")
    return 0


def _expand_paths(paths: list[str]) -> list[Path]:
    expanded: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if any(char in raw_path for char in "*?"):
            matches = sorted(path.parent.glob(path.name))
            expanded.extend(match for match in matches if match.is_file())
        else:
            if path.is_file():
                expanded.append(path)
    return expanded


if __name__ == "__main__":
    raise SystemExit(main())