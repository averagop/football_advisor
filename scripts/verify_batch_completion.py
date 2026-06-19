from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EVIDENCE_DIR = Path("docs") / "verification" / "batches"
HIGH_RISK_PREFIXES = (
    "football_advisor/",
    "openwebui_tools/",
    "scripts/",
    ".github/",
    ".githooks/",
)
HIGH_RISK_EXACT_FILES = {
    "requirements.txt",
    "init_db.py",
}
HIGH_RISK_SCRIPT_KEYWORDS = (
    "api",
    "client",
    "config",
    "migration",
    "provider",
    "smoke_test_worldcup_readiness",
    "sporttery",
    "sync",
    "url_safety",
    "walkthrough_e2e",
)
CODE_ARTIFACT_PREFIXES = (
    "football_advisor/",
    "openwebui_tools/",
    "scripts/",
    "tests/",
    ".github/",
    ".githooks/",
)
CODE_ARTIFACT_EXACT_FILES = HIGH_RISK_EXACT_FILES
EXTERNAL_REVIEW_FAILURE_MARKERS = (
    "无法运行",
    "未运行",
    "没有运行",
    "未取得结论",
    "复审超时",
    "命令超时",
    "因文件数超过",
    "超过150限制",
    "review failed",
    "review error",
    "review timeout",
    "review timed out",
    "failed to run",
    "command timed out",
    "unknown error",
    "trpcclienterror",
)


@dataclass(frozen=True)
class BatchVerificationResult:
    passed: bool
    batch: int
    evidence_path: Path
    blocking_gaps: tuple[str, ...]


def verify_batch(
    repo_root: Path,
    batch: int,
    *,
    mode: str = "post-commit",
    git_status_text: str | None = None,
    current_commit: str | None = None,
    parent_commit: str | None = None,
    committed_changed_files: list[str] | None = None,
) -> BatchVerificationResult:
    repo_root = repo_root.resolve()
    evidence_path = repo_root / EVIDENCE_DIR / f"batch-{batch}-evidence.json"
    gaps: list[str] = []

    if not evidence_path.exists():
        return BatchVerificationResult(
            passed=False,
            batch=batch,
            evidence_path=evidence_path,
            blocking_gaps=(f"缺少证据文件: {evidence_path}",),
        )

    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return BatchVerificationResult(
            passed=False,
            batch=batch,
            evidence_path=evidence_path,
            blocking_gaps=(f"证据文件不是合法 JSON: {exc}",),
        )

    _check_required_fields(evidence, gaps)
    if evidence.get("batch") != batch:
        gaps.append(f"证据 batch={evidence.get('batch')} 与参数 batch={batch} 不一致")
    _check_commit_anchor(
        repo_root,
        evidence,
        gaps,
        mode=mode,
        current_commit=current_commit,
        parent_commit=parent_commit,
    )

    changed_files = _parse_string_list(evidence.get("changed_files"), "changed_files", gaps)
    effective_changed_files = changed_files
    if mode == "post-commit":
        committed_files = _post_commit_changed_files(
            repo_root,
            current_commit=current_commit,
            parent_commit=parent_commit,
            committed_changed_files=committed_changed_files,
        )
        if committed_files is not None:
            effective_changed_files = committed_files
            if sorted(changed_files) != sorted(effective_changed_files):
                gaps.append("evidence.changed_files 与提交实际改动不一致")

    required_artifacts = evidence.get("required_artifacts", [])
    if not isinstance(required_artifacts, list):
        required_artifacts = []
    artifact_required_files = _artifact_required_changed_files(effective_changed_files)
    if artifact_required_files and not required_artifacts:
        gaps.append("代码改动必须列出 required_artifacts")
    for relative_path in required_artifacts:
        if not (repo_root / str(relative_path)).exists():
            gaps.append(f"缺少必需产物: {relative_path}")

    review_file = evidence.get("review_file")
    if not review_file:
        gaps.append("缺少 review_file")
    elif not (repo_root / str(review_file)).exists():
        gaps.append(f"缺少人工复核文件: {review_file}")

    commands = evidence.get("commands", [])
    if not isinstance(commands, list):
        commands = []
    for command in commands:
        if not isinstance(command, dict):
            gaps.append(f"命令记录必须是对象: {command}")
            continue
        name = command.get("name", "<unknown>")
        if command.get("exit_code") != 0:
            gaps.append(f"命令未通过: {name}")
        if not command.get("command"):
            gaps.append(f"命令缺少 command 字段: {name}")
        if not command.get("summary"):
            gaps.append(f"命令缺少 summary 字段: {name}")

    coderabbit = evidence.get("coderabbit", {})
    if not isinstance(coderabbit, dict):
        coderabbit = {}
    external_review_required = evidence.get("external_review_required", True)
    if not isinstance(external_review_required, bool):
        gaps.append("external_review_required 必须是布尔值")
        external_review_required = True
    high_risk_files = _high_risk_changed_files(effective_changed_files)
    if high_risk_files and not external_review_required:
        gaps.append("高风险改动必须启用外部复审: " + ", ".join(high_risk_files))
    unhandled_issues = _parse_non_negative_int(
        coderabbit.get("unhandled_issues", 0),
        "coderabbit.unhandled_issues",
        gaps,
    )
    if external_review_required and (
        not coderabbit.get("ran")
        or coderabbit.get("exit_code") != 0
        or unhandled_issues != 0
    ):
        gaps.append("CodeRabbit 未成功执行")
    if external_review_required:
        _check_external_review_artifact(repo_root, coderabbit, gaps)
        _check_external_review_text(coderabbit, gaps)

    if not evidence.get("progress_updated"):
        gaps.append("docs/PROGRESS.md 未记录本批结果")
    if not evidence.get("independent_commit"):
        gaps.append("本批缺少独立提交")

    evidence_gaps = evidence.get("blocking_gaps", [])
    if evidence_gaps:
        gaps.extend(str(item) for item in evidence_gaps)

    if mode == "post-commit":
        status_text = git_status_text
        if status_text is None:
            status_text = _git_status(repo_root)
        status_lines = [line for line in status_text.strip().splitlines() if line]
        if status_lines:
            gaps.append("工作区存在未提交或未跟踪改动: " + "\n".join(status_lines))
    elif mode != "pre-commit":
        gaps.append(f"未知 mode: {mode}")

    return BatchVerificationResult(
        passed=not gaps,
        batch=batch,
        evidence_path=evidence_path,
        blocking_gaps=tuple(gaps),
    )


def _check_required_fields(evidence: dict[str, Any], gaps: list[str]) -> None:
    required = [
        "schema_version",
        "generated_by",
        "batch",
        "commit",
        "changed_files",
        "required_artifacts",
        "review_file",
        "commands",
        "coderabbit",
        "progress_updated",
        "independent_commit",
        "blocking_gaps",
    ]
    for field in required:
        if field not in evidence:
            gaps.append(f"缺少字段: {field}")
    if evidence.get("schema_version") != "1.0":
        gaps.append("schema_version 必须是 1.0")
    if evidence.get("generated_by") != "scripts/finalize_batch.py":
        gaps.append("generated_by 必须是 scripts/finalize_batch.py")
    batch = evidence.get("batch")
    if isinstance(batch, bool) or not isinstance(batch, int) or batch < 0:
        gaps.append("batch 必须是非负整数")
    commit = evidence.get("commit")
    if not isinstance(commit, str) or len(commit) < 7:
        gaps.append("commit 必须至少包含7个字符")
    if not isinstance(evidence.get("commands", []), list):
        gaps.append("commands 必须是数组")
    elif len(evidence.get("commands", [])) == 0:
        gaps.append("commands 至少需要记录一条命令")
    if not isinstance(evidence.get("required_artifacts", []), list):
        gaps.append("required_artifacts 必须是数组")
    if not isinstance(evidence.get("changed_files", []), list):
        gaps.append("changed_files 必须是数组")
    if not isinstance(evidence.get("blocking_gaps", []), list):
        gaps.append("blocking_gaps 必须是数组")
    if not isinstance(evidence.get("coderabbit", {}), dict):
        gaps.append("coderabbit 必须是对象")


def _check_commit_anchor(
    repo_root: Path,
    evidence: dict[str, Any],
    gaps: list[str],
    *,
    mode: str,
    current_commit: str | None,
    parent_commit: str | None,
) -> None:
    evidence_commit = evidence.get("commit")
    if not isinstance(evidence_commit, str) or len(evidence_commit) < 7:
        return
    head = current_commit
    if head is None:
        head = _git_head(repo_root)
    if not head:
        if (repo_root / ".git").exists():
            gaps.append("无法读取当前 HEAD，不能校验 evidence.commit")
        return
    if mode == "pre-commit":
        if not head.startswith(evidence_commit):
            gaps.append(f"证据 commit={evidence_commit} 与当前 HEAD={head} 不一致")
        return
    if mode == "post-commit":
        parent = parent_commit
        if parent is None:
            parent = _git_head_parent(repo_root)
        if not parent:
            if (repo_root / ".git").exists():
                gaps.append("root commit 不能作为普通批次验收提交")
            return
        if head.startswith(evidence_commit):
            gaps.append("post-commit 阶段 evidence.commit 仍等于当前 HEAD，缺少独立提交")
        if not parent.startswith(evidence_commit):
            gaps.append(
                f"证据 commit={evidence_commit} 与当前 HEAD 父提交={parent} 不一致"
            )
        return
    if not head.startswith(evidence_commit):
        gaps.append(f"证据 commit={evidence_commit} 与当前 HEAD={head} 不一致")


def _parse_non_negative_int(value: Any, field_name: str, gaps: list[str]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        gaps.append(f"{field_name} 必须是整数")
        return None
    if value < 0:
        gaps.append(f"{field_name} 必须是非负整数")
        return None
    return value


def _parse_string_list(value: Any, field_name: str, gaps: list[str]) -> list[str]:
    if not isinstance(value, list):
        gaps.append(f"{field_name} 必须是数组")
        return []
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            gaps.append(f"{field_name} 必须只包含非空字符串")
            continue
        items.append(item.replace("\\", "/"))
    return items


def _check_external_review_artifact(
    repo_root: Path,
    coderabbit: dict[str, Any],
    gaps: list[str],
) -> None:
    artifact = coderabbit.get("artifact")
    if not isinstance(artifact, str) or not artifact:
        gaps.append("外部复审缺少原始产物路径: coderabbit.artifact")
        return
    if not (repo_root / artifact).exists():
        gaps.append(f"外部复审原始产物不存在: {artifact}")


def _check_external_review_text(
    coderabbit: dict[str, Any],
    gaps: list[str],
) -> None:
    text_parts: list[str] = []
    for field in ("summary", "note", "raw_summary"):
        value = coderabbit.get(field)
        if isinstance(value, str):
            text_parts.append(value)
    text = "\n".join(text_parts).lower()
    if not text:
        return
    if any(marker in text for marker in EXTERNAL_REVIEW_FAILURE_MARKERS):
        gaps.append("CodeRabbit 结果说明与成功状态矛盾")


def _high_risk_changed_files(changed_files: list[str]) -> list[str]:
    high_risk: list[str] = []
    for path in changed_files:
        normalized = path.lstrip("./")
        if normalized in HIGH_RISK_EXACT_FILES:
            high_risk.append(path)
            continue
        if normalized.startswith(HIGH_RISK_PREFIXES):
            high_risk.append(path)
            continue
        if normalized.startswith("scripts/") and any(
            keyword in normalized.lower() for keyword in HIGH_RISK_SCRIPT_KEYWORDS
        ):
            high_risk.append(path)
    return high_risk


def _artifact_required_changed_files(changed_files: list[str]) -> list[str]:
    files: list[str] = []
    for path in changed_files:
        normalized = path.lstrip("./")
        if normalized in CODE_ARTIFACT_EXACT_FILES:
            files.append(path)
            continue
        if normalized.startswith(CODE_ARTIFACT_PREFIXES):
            files.append(path)
    return files


def _post_commit_changed_files(
    repo_root: Path,
    *,
    current_commit: str | None,
    parent_commit: str | None,
    committed_changed_files: list[str] | None,
) -> list[str] | None:
    if committed_changed_files is not None:
        return [path.replace("\\", "/") for path in committed_changed_files]
    if not (repo_root / ".git").exists():
        return None
    head = current_commit or _git_head(repo_root)
    parent = parent_commit or _git_head_parent(repo_root)
    if not head or not parent:
        return []
    completed = subprocess.run(
        ["git", "diff", "--name-only", parent, head],
        cwd=str(repo_root),
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        return None
    files: list[str] = []
    for line in completed.stdout.splitlines():
        normalized = line.strip().replace("\\", "/")
        if normalized and normalized not in files:
            files.append(normalized)
    return files


def _git_status(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "status", "--short"],
        cwd=str(repo_root),
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        return completed.stderr.strip() or completed.stdout.strip()
    return completed.stdout


def _git_head(repo_root: Path) -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_root),
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _git_head_parent(repo_root: Path) -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD^"],
        cwd=str(repo_root),
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="验证单个修复批次是否具备机器证据")
    parser.add_argument("--batch", type=int, required=True, help="批次编号")
    parser.add_argument(
        "--mode",
        choices=["pre-commit", "post-commit"],
        default="post-commit",
        help="pre-commit 只校验证据；post-commit 还要求 git status clean",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="仓库根目录，默认当前目录",
    )
    args = parser.parse_args(argv)

    result = verify_batch(Path(args.repo_root), args.batch, mode=args.mode)
    if result.passed:
        print(f"批次 {args.batch} 验收门禁通过: {result.evidence_path}")
        return 0

    print(f"批次 {args.batch} 验收门禁未通过: {result.evidence_path}")
    for gap in result.blocking_gaps:
        print(f"- {gap}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
