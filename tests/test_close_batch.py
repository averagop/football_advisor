from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.close_batch import close_batch


class CloseBatchTests(unittest.TestCase):
    def test_rejects_dirty_files_not_declared_by_batch_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_batch_manifest(root, 101, required_artifacts=["docs/PROGRESS.md"])
            (root / "docs" / "PROGRESS.md").write_text("## 批次101\n", encoding="utf-8")
            runner = FakeRunner(
                git_outputs={
                    ("status", "--short"): " M docs/PROGRESS.md\n?? temp.py\n",
                }
            )

            result = close_batch(root, 101, message="chore: close batch 101", runner=runner)

            self.assertFalse(result.passed)
            self.assertIn("temp.py", "\n".join(result.blocking_gaps))
            self.assertEqual(runner.commands, [("git", "status", "--short")])

    def test_stops_when_finalize_pre_commit_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_batch_manifest(root, 101)
            runner = FakeRunner(
                git_outputs={("status", "--short"): ""},
                command_exit_codes={
                    ("python", "scripts/finalize_batch.py", "--batch", "101", "--mode", "pre-commit"): 1,
                },
            )

            result = close_batch(root, 101, message="chore: close batch 101", runner=runner)

            self.assertFalse(result.passed)
            self.assertIn("finalize_batch.py", "\n".join(result.blocking_gaps))
            self.assertNotIn(("git", "commit", "-m", "chore: close batch 101（批次101）"), runner.commands)

    def test_stops_when_post_commit_gate_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_batch_manifest(root, 101)
            runner = FakeRunner(
                git_outputs={("status", "--short"): ""},
                command_exit_codes={
                    ("python", "scripts/verify_batch_completion.py", "--batch", "101", "--mode", "post-commit"): 1,
                },
            )

            result = close_batch(root, 101, message="chore: close batch 101", runner=runner)

            self.assertFalse(result.passed)
            self.assertIn("批次101未通过 post-commit 门禁", "\n".join(result.blocking_gaps))

    def test_rejects_dirty_worktree_after_post_commit_gate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_batch_manifest(root, 101)
            runner = FakeRunner(
                git_outputs={
                    ("status", "--short"): [
                        "",
                        "?? docs/verification/batches/batch-101-evidence.json\n",
                    ],
                }
            )

            result = close_batch(root, 101, message="chore: close batch 101", runner=runner)

            self.assertFalse(result.passed)
            self.assertIn("batch-101-evidence.json", "\n".join(result.blocking_gaps))

    def test_missing_manifest_fails_before_git_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runner = FakeRunner()

            result = close_batch(root, 101, message="chore: close batch 101", runner=runner)

            self.assertFalse(result.passed)
            self.assertIn("缺少批次 manifest", "\n".join(result.blocking_gaps))
            self.assertEqual(runner.commands, [])

    def test_runs_close_sequence_and_requires_clean_final_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_batch_manifest(
                root,
                101,
                required_artifacts=[
                    "scripts/close_batch.py",
                    "tests/test_close_batch.py",
                    "docs/PROGRESS.md",
                ],
            )
            runner = FakeRunner(
                git_outputs={
                    ("status", "--short"): [
                        " M scripts/close_batch.py\n M tests/test_close_batch.py\n M docs/PROGRESS.md\n",
                        "",
                    ],
                }
            )

            result = close_batch(root, 101, message="chore: close batch 101", runner=runner)

            self.assertTrue(result.passed)
            self.assertEqual(
                runner.commands,
                [
                    ("git", "status", "--short"),
                    ("python", "scripts/finalize_batch.py", "--batch", "101", "--mode", "pre-commit"),
                    ("python", "scripts/verify_batch_completion.py", "--batch", "101", "--mode", "pre-commit"),
                    (
                        "git",
                        "add",
                        "--",
                        "docs/PROGRESS.md",
                        "docs/verification/batches/batch-101-evidence.json",
                        "docs/verification/batches/batch-101-manifest.json",
                        "docs/verification/batches/batch-101-review.md",
                        "scripts/close_batch.py",
                        "tests/test_close_batch.py",
                    ),
                    ("git", "commit", "-m", "chore: close batch 101（批次101）"),
                    ("python", "scripts/verify_batch_completion.py", "--batch", "101", "--mode", "post-commit"),
                    ("git", "status", "--short"),
                ],
            )

    def test_stages_coderabbit_artifact_declared_by_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_batch_manifest(root, 101, coderabbit_artifact="docs/verification/coderabbit/batch-101-review.jsonl")
            runner = FakeRunner(
                git_outputs={
                    ("status", "--short"): [
                        "?? docs/verification/coderabbit/batch-101-review.jsonl\n",
                        "",
                    ],
                }
            )

            result = close_batch(root, 101, message="chore: close batch 101", runner=runner)

            self.assertTrue(result.passed)
            add_command = next(command for command in runner.commands if command[:3] == ("git", "add", "--"))
            self.assertIn("docs/verification/coderabbit/batch-101-review.jsonl", add_command)


class FakeRunner:
    def __init__(self, *, git_outputs=None, command_exit_codes=None):
        self.git_outputs = git_outputs or {}
        self.command_exit_codes = command_exit_codes or {}
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: list[str], cwd: Path):
        normalized = tuple("python" if part.endswith("python.exe") or part == "python" else part for part in command)
        self.commands.append(normalized)
        if normalized[:2] == ("git", "status"):
            output = self.git_outputs.get(normalized[1:], "")
            if isinstance(output, list):
                output = output.pop(0) if output else ""
            return CommandResult(0, output, "")
        return CommandResult(self.command_exit_codes.get(normalized, 0), "", "")


class CommandResult:
    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _write_batch_manifest(
    root: Path,
    batch: int,
    *,
    required_artifacts: list[str] | None = None,
    coderabbit_artifact: str | None = None,
) -> None:
    batch_dir = root / "docs" / "verification" / "batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    review = batch_dir / f"batch-{batch}-review.md"
    review.write_text("# 批次复核\n", encoding="utf-8")
    manifest = batch_dir / f"batch-{batch}-manifest.json"
    payload = {
        "review_file": f"docs/verification/batches/batch-{batch}-review.md",
        "required_artifacts": required_artifacts or [],
        "commands": [
            {
                "name": "smoke",
                "command": ["python", "-c", "print('ok')"],
            }
        ],
    }
    if coderabbit_artifact:
        payload["coderabbit_artifact"] = coderabbit_artifact
    manifest.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
