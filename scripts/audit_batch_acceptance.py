from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


Runner = Callable[[list[str], Path], "CommandResult"]


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class AuditResult:
    passed: bool
    batch: int
    blocking_gaps: tuple[str, ...]
    checks: tuple[dict[str, object], ...]


def audit_batch_acceptance(
    repo_root: Path,
    batch: int,
    *,
    runner: Runner | None = None,
) -> AuditResult:
    repo_root = repo_root.resolve()
    run = runner or _run_subprocess
    checks: list[dict[str, object]] = []
    gaps: list[str] = []

    status = run(["git", "status", "--short", "--branch"], repo_root)
    checks.append(_check_record("git status --short --branch", status))
    if status.returncode != 0:
        gaps.append("无法读取工作区状态")
    else:
        dirty_lines = [line for line in status.stdout.splitlines() if line and not line.startswith("##")]
        if dirty_lines:
            gaps.append("工作区不干净: " + "\n".join(dirty_lines))

    rev_count = run(["git", "rev-list", "--count", "HEAD"], repo_root)
    checks.append(_check_record("git rev-list --count HEAD", rev_count))
    if rev_count.returncode != 0:
        gaps.append("无法读取提交数量")
    else:
        try:
            if int(rev_count.stdout.strip()) <= 1:
                gaps.append("普通批次不能以 root commit 作为验收提交")
        except ValueError:
            gaps.append("提交数量不是合法整数")

    verifier_command = [
        sys.executable,
        "scripts\\verify_batch_completion.py",
        "--batch",
        str(batch),
        "--mode",
        "post-commit",
    ]
    verifier = run(verifier_command, repo_root)
    checks.append(_check_record("verify_batch_completion.py post-commit", verifier))
    if verifier.returncode != 0:
        gaps.append("post-commit 门禁未通过: " + _summarize(verifier))

    return AuditResult(
        passed=not gaps,
        batch=batch,
        blocking_gaps=tuple(gaps),
        checks=tuple(checks),
    )


def _check_record(name: str, result: CommandResult) -> dict[str, object]:
    return {
        "name": name,
        "exit_code": result.returncode,
        "summary": _summarize(result),
    }


def _summarize(result: CommandResult) -> str:
    text = (result.stdout + "\n" + result.stderr).strip()
    if not text:
        return "无输出"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(lines[-6:])[:700]


def _run_subprocess(command: list[str], cwd: Path) -> CommandResult:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读审计批次交付是否达到接收标准")
    parser.add_argument("--batch", type=int, required=True, help="批次编号")
    parser.add_argument("--repo-root", default=".", help="仓库根目录")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)

    result = audit_batch_acceptance(Path(args.repo_root), args.batch)
    if args.json:
        print(
            json.dumps(
                {
                    "passed": result.passed,
                    "batch": result.batch,
                    "blocking_gaps": list(result.blocking_gaps),
                    "checks": list(result.checks),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif result.passed:
        print(f"批次{args.batch}达到验收接收标准")
    else:
        print(f"批次{args.batch}未达到验收接收标准")
        for gap in result.blocking_gaps:
            print(f"- {gap}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
