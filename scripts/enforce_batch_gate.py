from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping


VERIFY_SCRIPT = Path("scripts") / "verify_batch_completion.py"
BATCH_PATTERNS = (
    re.compile(r"(?:^|[-_/])batch[-_/]?(\d+)(?:$|[-_/])", re.IGNORECASE),
    re.compile(r"批次\s*(\d+)"),
)

Runner = Callable[[list[str], Path], int]


def resolve_batch_number(
    *,
    explicit_batch: int | None,
    env: Mapping[str, str],
    branch_name: str,
    commit_message: str,
    allow_commit_message: bool = True,
) -> int:
    if explicit_batch is not None:
        return _validate_batch(explicit_batch)

    env_batch = env.get("FOOTBALL_ADVISOR_BATCH")
    if env_batch:
        try:
            return _validate_batch(int(env_batch))
        except ValueError as exc:
            raise ValueError("FOOTBALL_ADVISOR_BATCH 必须是非负整数") from exc

    texts = [branch_name]
    if allow_commit_message:
        texts.append(commit_message)

    for text in texts:
        batch = _extract_batch(text)
        if batch is not None:
            return batch

    raise ValueError(
        "无法确定批次编号；请设置 FOOTBALL_ADVISOR_BATCH，或使用包含 batch-N 的分支名"
    )


def run_batch_gate(
    *,
    repo_root: Path,
    mode: str,
    explicit_batch: int | None,
    env: Mapping[str, str],
    branch_name: str,
    commit_message: str,
    runner: Runner | None = None,
) -> int:
    batch = resolve_batch_number(
        explicit_batch=explicit_batch,
        env=env,
        branch_name=branch_name,
        commit_message=commit_message,
        allow_commit_message=mode != "pre-commit",
    )
    if runner is None:
        _require_file(Path(sys.executable), "Python解释器")
        _require_file(repo_root / VERIFY_SCRIPT, "批次验收脚本")

    command = [
        sys.executable,
        str(VERIFY_SCRIPT),
        "--batch",
        str(batch),
        "--mode",
        mode,
    ]
    return (runner or _run_subprocess)(command, repo_root)


def _validate_batch(batch: int) -> int:
    if isinstance(batch, bool) or batch < 0:
        raise ValueError("批次编号必须是非负整数")
    return batch


def _require_file(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label}不存在: {path}")


def _extract_batch(text: str) -> int | None:
    for pattern in BATCH_PATTERNS:
        match = pattern.search(text or "")
        if match:
            return _validate_batch(int(match.group(1)))
    return None


def _run_subprocess(command: list[str], cwd: Path) -> int:
    completed = subprocess.run(command, cwd=str(cwd), check=False)
    return completed.returncode


def _git_output(repo_root: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="强制执行批次验收门禁")
    parser.add_argument("--batch", type=int, default=None, help="批次编号")
    parser.add_argument(
        "--mode",
        choices=["pre-commit", "post-commit"],
        required=True,
        help="验收器模式",
    )
    parser.add_argument("--repo-root", default=".", help="仓库根目录")
    parser.add_argument(
        "--commit-message",
        default="",
        help="可选提交信息，仅用于 post-commit 解析 批次N/batch-N",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    branch_name = _git_output(repo_root, ["branch", "--show-current"])
    commit_message = ""
    if args.mode != "pre-commit":
        commit_message = args.commit_message or _git_output(repo_root, ["log", "-1", "--pretty=%B"])

    try:
        return run_batch_gate(
            repo_root=repo_root,
            mode=args.mode,
            explicit_batch=args.batch,
            env=os.environ,
            branch_name=branch_name,
            commit_message=commit_message,
        )
    except ValueError as exc:
        print(f"批次门禁未执行: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
