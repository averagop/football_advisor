"""维护任务命令行入口。

用法:
    python -m football_advisor.jobs status
    python -m football_advisor.jobs run-due
    python -m football_advisor.jobs run post-match
    python -m football_advisor.jobs run pre-match --days 2

所有命令支持 --db-path 参数。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from .config import load_config
from .job_runner import JOB_LABELS, JOB_ORDER, MaintenanceResult, MaintenanceRunner


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="足球预测系统维护任务命令行",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="可用任务: " + ", ".join(JOB_ORDER),
    )
    parser.add_argument(
        "--db-path",
        default="football_system.db",
        help="DuckDB 数据库路径（默认: football_system.db）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="查看所有任务状态")

    sub.add_parser("run-due", help="运行所有到期维护任务")

    run_parser = sub.add_parser("run", help="运行单个任务")
    run_parser.add_argument("job_name", choices=JOB_ORDER, help="任务名称")
    run_parser.add_argument("--days", type=int, default=None, help="天数参数（预留）")

    return parser


def _print_status(runner: MaintenanceRunner) -> None:
    states = runner.get_status()
    if not states:
        print("无法获取任务状态。")
        return

    print(f"{'任务':<30} {'状态':<12} {'失败次数':<8} {'最后成功':<20}")
    print("-" * 70)
    for s in states:
        label = JOB_LABELS.get(s.collection_type, s.collection_type)
        status_icon = {
            "IDLE": "○ 空闲",
            "RUNNING": "▶ 运行中",
            "SUCCESS": "✓ 成功",
            "FAILED": "✗ 失败",
        }.get(s.status, f"? {s.status}")

        last_success = (
            s.last_success_at.strftime("%Y-%m-%d %H:%M")
            if s.last_success_at
            else "从未"
        )
        print(f"{label:<30} {status_icon:<12} {s.failure_count:<8} {last_success:<20}")

        if s.last_error and s.status == "FAILED":
            error_short = s.last_error[:100]
            print(f"  └─ 错误: {error_short}")


def _print_result(result: MaintenanceResult) -> None:
    label = JOB_LABELS.get(result.job_name, result.job_name)
    icon = {"success": "✓", "skipped": "○", "failed": "✗"}.get(result.status, "?")
    print(f"{icon} {label}: {result.status}")
    if result.details:
        print(f"  详情: {result.details}")
    if result.error:
        print(f"  错误: {result.error}")


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv or sys.argv[1:])

    config = load_config()
    runner = MaintenanceRunner(
        database_path=args.db_path,
        config=config,
    )

    if args.command == "status":
        _print_status(runner)
    elif args.command == "run-due":
        print("执行到期维护任务...")
        results = runner.run_due()
        for r in results:
            _print_result(r)
        if not results:
            print("无到期任务。")
    elif args.command == "run":
        print(f"执行任务: {args.job_name}")
        result = runner.run_single(args.job_name)
        _print_result(result)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()