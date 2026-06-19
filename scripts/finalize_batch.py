from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verify_batch_completion import (
    EVIDENCE_DIR,
    _high_risk_changed_files,
    verify_batch,
)


GENERATED_BY = "scripts/finalize_batch.py"


def finalize_batch(
    repo_root: Path,
    batch: int,
    *,
    manifest_path: Path | None = None,
    mode: str = "pre-commit",
) -> Any:
    repo_root = repo_root.resolve()
    manifest = _load_manifest(repo_root, batch, manifest_path)
    changed_files = _git_changed_files(repo_root)
    evidence_relative_path = (EVIDENCE_DIR / f"batch-{batch}-evidence.json").as_posix()
    if evidence_relative_path not in changed_files:
        changed_files.append(evidence_relative_path)
    current_head = _git_output(repo_root, ["rev-parse", "HEAD"])
    parent_head = _git_output(repo_root, ["rev-parse", "HEAD^"])
    external_review_required = bool(_high_risk_changed_files(changed_files))
    command_results = [_run_manifest_command(repo_root, item) for item in manifest["commands"]]
    coderabbit = _build_coderabbit_summary(repo_root, manifest.get("coderabbit_artifact"))
    progress_updated = _progress_mentions_batch(repo_root, batch)
    independent_commit = bool(current_head)
    # post-commit 模式：优先使用父提交，root commit 情况下使用当前 HEAD
    if mode == "post-commit":
        evidence_commit = parent_head or current_head
    else:
        evidence_commit = current_head

    evidence = {
        "schema_version": "1.0",
        "generated_by": GENERATED_BY,
        "batch": batch,
        "commit": evidence_commit or "NO_HEAD",
        "mode": mode,
        "changed_files": changed_files,
        "required_artifacts": manifest.get("required_artifacts", []),
        "review_file": manifest["review_file"],
        "commands": command_results,
        "coderabbit": coderabbit,
        "external_review_required": external_review_required,
        "progress_updated": progress_updated,
        "independent_commit": independent_commit,
        "blocking_gaps": [],
    }
    evidence_path = repo_root / evidence_relative_path
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return verify_batch(repo_root, batch, mode=mode)


def _load_manifest(repo_root: Path, batch: int, manifest_path: Path | None) -> dict[str, Any]:
    path = manifest_path or repo_root / EVIDENCE_DIR / f"batch-{batch}-manifest.json"
    if not path.is_absolute():
        path = repo_root / path
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("commands"), list) or not data["commands"]:
        raise ValueError("manifest.commands 至少需要一条命令")
    if not data.get("review_file"):
        raise ValueError("manifest.review_file 必填")
    data.setdefault("required_artifacts", [])
    return data


def _git_changed_files(repo_root: Path) -> list[str]:
    files: list[str] = []
    for args in (
        ["diff", "--name-only"],
        ["diff", "--cached", "--name-only"],
        ["ls-files", "--others", "--exclude-standard"],
    ):
        output = _git_output(repo_root, args) or ""
        for line in output.splitlines():
            normalized = line.strip().replace("\\", "/")
            if normalized and normalized not in files:
                files.append(normalized)
    return files


def _run_manifest_command(repo_root: Path, item: dict[str, Any]) -> dict[str, Any]:
    command = item.get("command")
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        raise ValueError("manifest command 必须是字符串数组")
    completed = subprocess.run(
        command,
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "name": str(item.get("name") or "command"),
        "command": " ".join(command),
        "exit_code": completed.returncode,
        "summary": _summarize_command_output(completed),
    }


def _summarize_command_output(completed: subprocess.CompletedProcess[str]) -> str:
    text = (completed.stdout + "\n" + completed.stderr).strip()
    if not text:
        return "无输出"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(lines[-5:])[:500]


def _build_coderabbit_summary(repo_root: Path, artifact: str | None) -> dict[str, Any]:
    if not artifact:
        return {"ran": False, "exit_code": 1, "unhandled_issues": 0}
    artifact_path = repo_root / artifact
    if not artifact_path.exists():
        return {
            "ran": False,
            "exit_code": 1,
            "unhandled_issues": 0,
            "artifact": artifact,
            "summary": "外部复审产物不存在",
        }
    findings = 0
    fixed = 0
    completed = False
    errors: list[str] = []
    context_count = 0
    completed_contexts = 0
    for line in artifact_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("type") == "review_context":
            context_count += 1
        if item.get("type") == "error":
            error_type = str(item.get("errorType") or "error")
            message = str(item.get("message") or "CodeRabbit error")
            errors.append(f"{error_type}: {message}")
        if item.get("type") == "finding":
            findings += 1
            fixed += int(item.get("fixed") or 0)
        if item.get("type") == "complete":
            completed = item.get("status") == "review_completed"
            if completed:
                completed_contexts += 1
            findings = int(item.get("findings", findings) or 0)
            fixed = int(item.get("fixed", fixed) or 0)
    unhandled = max(0, findings - fixed)
    if errors:
        return {
            "ran": False,
            "exit_code": 1,
            "unhandled_issues": unhandled,
            "summary": "CodeRabbit 原始产物包含错误：" + "；".join(errors),
            "artifact": artifact,
        }
    if context_count and completed_contexts < context_count:
        return {
            "ran": False,
            "exit_code": 1,
            "unhandled_issues": unhandled,
            "summary": f"CodeRabbit 原始产物未完成：contexts={context_count}, completed={completed_contexts}",
            "artifact": artifact,
        }
    return {
        "ran": completed,
        "exit_code": 0 if completed else 1,
        "unhandled_issues": unhandled,
        "summary": f"CodeRabbit 原始产物解析完成：findings={findings}, fixed={fixed}",
        "artifact": artifact,
    }


def _progress_mentions_batch(repo_root: Path, batch: int) -> bool:
    progress = repo_root / "docs" / "PROGRESS.md"
    if not progress.exists():
        return False
    text = progress.read_text(encoding="utf-8", errors="replace")
    return f"批次 {batch}" in text or f"批次{batch}" in text


def _git_output(repo_root: Path, args: list[str]) -> str | None:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="自动生成并校验批次 evidence")
    parser.add_argument("--batch", type=int, required=True)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--mode", choices=["pre-commit", "post-commit"], default="pre-commit")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)

    manifest = Path(args.manifest) if args.manifest else None
    result = finalize_batch(
        Path(args.repo_root),
        args.batch,
        manifest_path=manifest,
        mode=args.mode,
    )
    if result.passed:
        print(f"批次 {args.batch} 自动验收通过: {result.evidence_path}")
        return 0
    print(f"批次 {args.batch} 自动验收未通过: {result.evidence_path}")
    for gap in result.blocking_gaps:
        print(f"- {gap}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
