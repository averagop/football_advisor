from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


EVIDENCE_DIR = Path("docs") / "verification" / "batches"

Runner = Callable[[list[str], Path], "CommandResult"]


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class CloseBatchResult:
    passed: bool
    batch: int
    blocking_gaps: tuple[str, ...]
    commands: tuple[tuple[str, ...], ...]


def close_batch(
    repo_root: Path,
    batch: int,
    *,
    message: str,
    runner: Runner | None = None,
) -> CloseBatchResult:
    repo_root = repo_root.resolve()
    run = runner or _run_subprocess
    commands: list[tuple[str, ...]] = []
    gaps: list[str] = []

    manifest_path = EVIDENCE_DIR / f"batch-{batch}-manifest.json"
    review_path = EVIDENCE_DIR / f"batch-{batch}-review.md"
    evidence_path = EVIDENCE_DIR / f"batch-{batch}-evidence.json"

    manifest = _load_manifest(repo_root / manifest_path, gaps)
    if not (repo_root / review_path).exists():
        gaps.append(f"缺少批次复核文件: {review_path.as_posix()}")
    if gaps:
        return _result(batch, gaps, commands)

    allowed_dirty = _allowed_dirty_paths(batch, manifest)
    status = _run(run, repo_root, ["git", "status", "--short"], commands)
    if status.returncode != 0:
        gaps.append("无法读取 git 工作区状态")
        return _result(batch, gaps, commands)
    unexplained = _unexplained_dirty_paths(status.stdout, allowed_dirty)
    if unexplained:
        gaps.append("工作区存在未纳入本批 manifest 的改动: " + ", ".join(unexplained))
        return _result(batch, gaps, commands)

    for command, label in (
        (
            [sys.executable, "scripts/finalize_batch.py", "--batch", str(batch), "--mode", "pre-commit"],
            "finalize_batch.py pre-commit",
        ),
        (
            [
                sys.executable,
                "scripts/verify_batch_completion.py",
                "--batch",
                str(batch),
                "--mode",
                "pre-commit",
            ],
            "verify_batch_completion.py pre-commit",
        ),
    ):
        completed = _run(run, repo_root, command, commands)
        if completed.returncode != 0:
            gaps.append(f"{label} 未通过")
            return _result(batch, gaps, commands)

    stage_paths = _stage_paths(batch, manifest)
    completed = _run(run, repo_root, ["git", "add", "--", *stage_paths], commands)
    if completed.returncode != 0:
        gaps.append("git add 未通过")
        return _result(batch, gaps, commands)

    commit_message = _message_with_batch(message, batch)
    completed = _run(run, repo_root, ["git", "commit", "-m", commit_message], commands)
    if completed.returncode != 0:
        gaps.append("git commit 未通过")
        return _result(batch, gaps, commands)

    completed = _run(
        run,
        repo_root,
        [
            sys.executable,
            "scripts/verify_batch_completion.py",
            "--batch",
            str(batch),
            "--mode",
            "post-commit",
        ],
        commands,
    )
    if completed.returncode != 0:
        gaps.append(f"批次{batch}未通过 post-commit 门禁")
        return _result(batch, gaps, commands)

    final_status = _run(run, repo_root, ["git", "status", "--short"], commands)
    if final_status.returncode != 0:
        gaps.append("无法读取最终 git 工作区状态")
        return _result(batch, gaps, commands)
    final_dirty = _dirty_paths(final_status.stdout)
    if final_dirty:
        gaps.append("批次提交后工作区仍不干净: " + ", ".join(final_dirty))

    return _result(batch, gaps, commands)


def _load_manifest(path: Path, gaps: list[str]) -> dict[str, object]:
    if not path.exists():
        gaps.append(f"缺少批次 manifest: {path.as_posix()}")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        gaps.append(f"批次 manifest 不是合法 JSON: {exc}")
        return {}
    if not isinstance(data, dict):
        gaps.append("批次 manifest 必须是 JSON 对象")
        return {}
    return data


def _allowed_dirty_paths(batch: int, manifest: dict[str, object]) -> set[str]:
    paths = set(_manifest_paths(manifest))
    paths.update(
        {
            "docs/PROGRESS.md",
            (EVIDENCE_DIR / f"batch-{batch}-evidence.json").as_posix(),
            (EVIDENCE_DIR / f"batch-{batch}-manifest.json").as_posix(),
            (EVIDENCE_DIR / f"batch-{batch}-review.md").as_posix(),
        }
    )
    return paths


def _stage_paths(batch: int, manifest: dict[str, object]) -> list[str]:
    paths = sorted(_allowed_dirty_paths(batch, manifest))
    return paths


def _manifest_paths(manifest: dict[str, object]) -> set[str]:
    paths: set[str] = set()
    review_file = manifest.get("review_file")
    if isinstance(review_file, str) and review_file:
        paths.add(_normalize_path(review_file))
    coderabbit_artifact = manifest.get("coderabbit_artifact")
    if isinstance(coderabbit_artifact, str) and coderabbit_artifact:
        paths.add(_normalize_path(coderabbit_artifact))
    required_artifacts = manifest.get("required_artifacts", [])
    if isinstance(required_artifacts, list):
        for item in required_artifacts:
            if isinstance(item, str) and item:
                paths.add(_normalize_path(item))
    return paths


def _unexplained_dirty_paths(status_text: str, allowed_dirty: set[str]) -> list[str]:
    return [path for path in _dirty_paths(status_text) if path not in allowed_dirty]


def _dirty_paths(status_text: str) -> list[str]:
    paths: list[str] = []
    for line in status_text.splitlines():
        if not line.strip():
            continue
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        normalized = _normalize_path(path.strip())
        if normalized:
            paths.append(normalized)
    return paths


def _normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/").lstrip("./")


def _message_with_batch(message: str, batch: int) -> str:
    marker = f"批次{batch}"
    if marker in message:
        return message
    return f"{message}（{marker}）"


def _run(
    runner: Runner,
    repo_root: Path,
    command: list[str],
    commands: list[tuple[str, ...]],
) -> CommandResult:
    commands.append(tuple(command))
    return runner(command, repo_root)


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


def _result(
    batch: int,
    gaps: list[str],
    commands: list[tuple[str, ...]],
) -> CloseBatchResult:
    return CloseBatchResult(
        passed=not gaps,
        batch=batch,
        blocking_gaps=tuple(gaps),
        commands=tuple(commands),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="自动关闭单个修复批次")
    parser.add_argument("--batch", type=int, required=True, help="批次编号")
    parser.add_argument("--message", required=True, help="提交信息")
    parser.add_argument("--repo-root", default=".", help="仓库根目录")
    args = parser.parse_args(argv)

    result = close_batch(Path(args.repo_root), args.batch, message=args.message)
    if result.passed:
        print(f"批次{args.batch}关闭完成")
        return 0
    print(f"批次{args.batch}未通过")
    for gap in result.blocking_gaps:
        print(f"- {gap}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
