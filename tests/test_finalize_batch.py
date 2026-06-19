from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.finalize_batch import _build_coderabbit_summary, finalize_batch


class FinalizeBatchTests(unittest.TestCase):
    def test_finalize_batch_generates_evidence_from_current_git_state(self):
        """自动 evidence 必须覆盖旧手写字段，并绑定当前 HEAD 和真实改动列表。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _init_git_repo(root)
            artifact = root / "docs" / "verification" / "artifact.txt"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("ok", encoding="utf-8")
            review = root / "docs" / "verification" / "batches" / "batch-2-review.md"
            review.parent.mkdir(parents=True, exist_ok=True)
            review.write_text("# review\n", encoding="utf-8")
            progress = root / "docs" / "PROGRESS.md"
            progress.write_text("## 批次 2\n", encoding="utf-8")
            coderabbit_artifact = (
                root / "docs" / "verification" / "coderabbit" / "batch-2.jsonl"
            )
            coderabbit_artifact.parent.mkdir(parents=True, exist_ok=True)
            coderabbit_artifact.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "finding", "fixed": 1}, ensure_ascii=False),
                        json.dumps(
                            {
                                "type": "complete",
                                "status": "review_completed",
                                "findings": 1,
                                "fixed": 1,
                            },
                            ensure_ascii=False,
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            manifest = root / "docs" / "verification" / "batches" / "batch-2-manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "review_file": "docs/verification/batches/batch-2-review.md",
                        "required_artifacts": ["docs/verification/artifact.txt"],
                        "coderabbit_artifact": "docs/verification/coderabbit/batch-2.jsonl",
                        "commands": [
                            {
                                "name": "smoke",
                                "command": [sys.executable, "-c", "print('ok')"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            stale_evidence = (
                root / "docs" / "verification" / "batches" / "batch-2-evidence.json"
            )
            stale_evidence.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "batch": 2,
                        "commit": "stale999",
                        "changed_files": ["handwritten.py"],
                        "required_artifacts": [],
                        "review_file": "missing.md",
                        "commands": [],
                        "coderabbit": {},
                        "progress_updated": False,
                        "independent_commit": False,
                        "blocking_gaps": ["manual"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            changed = root / "football_advisor" / "db_schema.py"
            changed.parent.mkdir(parents=True, exist_ok=True)
            changed.write_text("# changed\n", encoding="utf-8")

            result = finalize_batch(root, 2, manifest_path=manifest, mode="pre-commit")

            evidence = json.loads(stale_evidence.read_text(encoding="utf-8"))
            head = _git(root, "rev-parse", "HEAD")
            self.assertEqual(evidence["commit"], head)
            self.assertIn("football_advisor/db_schema.py", evidence["changed_files"])
            self.assertNotIn("handwritten.py", evidence["changed_files"])
            self.assertEqual(evidence["commands"][0]["exit_code"], 0)
            self.assertEqual(evidence["coderabbit"]["unhandled_issues"], 0)
            self.assertTrue(result.passed)

    def test_finalize_batch_fails_closed_without_head(self):
        """无 HEAD 的仓库不能生成可验收 evidence。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _git(root, "init")
            manifest = root / "docs" / "verification" / "batches" / "batch-2-manifest.json"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(
                json.dumps(
                    {
                        "review_file": "docs/verification/batches/batch-2-review.md",
                        "required_artifacts": [],
                        "commands": [
                            {
                                "name": "smoke",
                                "command": [sys.executable, "-c", "print('ok')"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = finalize_batch(root, 2, manifest_path=manifest, mode="pre-commit")

            self.assertFalse(result.passed)
            self.assertIn("无法读取当前 HEAD", "\n".join(result.blocking_gaps))

    def test_finalize_batch_records_new_evidence_file_as_changed(self):
        """首次生成 evidence 时，也必须把 evidence 文件自身列入 changed_files。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _init_git_repo(root)
            review = root / "docs" / "verification" / "batches" / "batch-3-review.md"
            review.parent.mkdir(parents=True, exist_ok=True)
            review.write_text("# review\n", encoding="utf-8")
            progress = root / "docs" / "PROGRESS.md"
            progress.parent.mkdir(parents=True, exist_ok=True)
            progress.write_text("## 批次 3\n", encoding="utf-8")
            manifest = root / "docs" / "verification" / "batches" / "batch-3-manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "review_file": "docs/verification/batches/batch-3-review.md",
                        "required_artifacts": [
                            "docs/verification/batches/batch-3-evidence.json"
                        ],
                        "commands": [
                            {
                                "name": "smoke",
                                "command": [sys.executable, "-c", "print('ok')"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            finalize_batch(root, 3, manifest_path=manifest, mode="pre-commit")

            evidence = json.loads(
                (root / "docs" / "verification" / "batches" / "batch-3-evidence.json")
                .read_text(encoding="utf-8")
            )
            self.assertIn(
                "docs/verification/batches/batch-3-evidence.json",
                evidence["changed_files"],
            )

    def test_coderabbit_summary_fails_when_artifact_contains_error(self):
        """CodeRabbit 产物中只要出现 error，就不能被 complete 行洗成成功。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact = root / "docs" / "verification" / "coderabbit" / "batch-4.jsonl"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "complete",
                                "status": "review_completed",
                                "findings": 0,
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "type": "error",
                                "errorType": "rate_limit",
                                "message": "Rate limit exceeded",
                            },
                            ensure_ascii=False,
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            summary = _build_coderabbit_summary(
                root,
                "docs/verification/coderabbit/batch-4.jsonl",
            )

        self.assertFalse(summary["ran"])
        self.assertEqual(summary["exit_code"], 1)
        self.assertIn("rate_limit", summary["summary"])

    def test_coderabbit_summary_fails_when_review_context_has_no_complete(self):
        """多段复审产物中任一 context 未完成，都不能算成功。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact = root / "docs" / "verification" / "coderabbit" / "batch-5.jsonl"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "review_context", "workingDirectory": "scripts"}, ensure_ascii=False),
                        json.dumps({"type": "complete", "status": "review_completed", "findings": 0}, ensure_ascii=False),
                        json.dumps({"type": "review_context", "workingDirectory": "tests"}, ensure_ascii=False),
                        json.dumps({"type": "status", "phase": "analyzing", "status": "reviewing"}, ensure_ascii=False),
                    ]
                ),
                encoding="utf-8",
            )

            summary = _build_coderabbit_summary(
                root,
                "docs/verification/coderabbit/batch-5.jsonl",
            )

        self.assertFalse(summary["ran"])
        self.assertEqual(summary["exit_code"], 1)
        self.assertIn("未完成", summary["summary"])

    def test_finalize_batch_script_runs_directly(self):
        """脚本必须支持 python scripts/finalize_batch.py 直接执行。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _git(root, "init")
            scripts_dir = root / "scripts"
            scripts_dir.mkdir()
            source = Path(__file__).resolve().parent.parent / "scripts" / "finalize_batch.py"
            verifier = (
                Path(__file__).resolve().parent.parent
                / "scripts"
                / "verify_batch_completion.py"
            )
            (scripts_dir / "finalize_batch.py").write_text(
                source.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (scripts_dir / "verify_batch_completion.py").write_text(
                verifier.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            manifest = root / "docs" / "verification" / "batches" / "batch-2-manifest.json"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(
                json.dumps(
                    {
                        "review_file": "docs/verification/batches/batch-2-review.md",
                        "required_artifacts": [],
                        "commands": [
                            {
                                "name": "smoke",
                                "command": [sys.executable, "-c", "print('ok')"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/finalize_batch.py",
                    "--batch",
                    "2",
                    "--mode",
                    "pre-commit",
                ],
                cwd=str(root),
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotIn("ModuleNotFoundError", completed.stderr)


def _init_git_repo(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.com")
    marker = root / "README.md"
    marker.write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "base")


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(root),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()
