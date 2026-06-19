from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from scripts.audit_batch_acceptance import audit_batch_acceptance


class AuditBatchAcceptanceTests(unittest.TestCase):
    def test_rejects_dirty_worktree_before_trusting_reports(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = FakeRunner(
                outputs={
                    ("git", "status", "--short", "--branch"): (0, "## branch\n M docs/PROGRESS.md\n", ""),
                    ("git", "rev-list", "--count", "HEAD"): (0, "2\n", ""),
                    _verify_command(3): (0, "批次 3 验收门禁通过\n", ""),
                }
            )

            result = audit_batch_acceptance(Path(temp_dir), 3, runner=runner)

            self.assertFalse(result.passed)
            self.assertIn("工作区不干净", "\n".join(result.blocking_gaps))

    def test_rejects_root_commit_even_if_verifier_output_is_positive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = FakeRunner(
                outputs={
                    ("git", "status", "--short", "--branch"): (0, "## branch\n", ""),
                    ("git", "rev-list", "--count", "HEAD"): (0, "1\n", ""),
                    _verify_command(3): (0, "批次 3 验收门禁通过\n", ""),
                }
            )

            result = audit_batch_acceptance(Path(temp_dir), 3, runner=runner)

            self.assertFalse(result.passed)
            self.assertIn("root commit", "\n".join(result.blocking_gaps))

    def test_rejects_failed_post_commit_gate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = FakeRunner(
                outputs={
                    ("git", "status", "--short", "--branch"): (0, "## branch\n", ""),
                    ("git", "rev-list", "--count", "HEAD"): (0, "2\n", ""),
                    _verify_command(3): (1, "批次 3 验收门禁未通过\n- evidence.changed_files 与提交实际改动不一致\n", ""),
                }
            )

            result = audit_batch_acceptance(Path(temp_dir), 3, runner=runner)

            self.assertFalse(result.passed)
            self.assertIn("post-commit 门禁未通过", "\n".join(result.blocking_gaps))

    def test_passes_only_when_machine_acceptance_checks_pass(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = FakeRunner(
                outputs={
                    ("git", "status", "--short", "--branch"): (0, "## branch\n", ""),
                    ("git", "rev-list", "--count", "HEAD"): (0, "2\n", ""),
                    _verify_command(3): (0, "批次 3 验收门禁通过\n", ""),
                }
            )

            result = audit_batch_acceptance(Path(temp_dir), 3, runner=runner)

            self.assertTrue(result.passed)
            self.assertNotIn(("git", "add"), [command[:2] for command in runner.commands])
            self.assertNotIn(("git", "commit"), [command[:2] for command in runner.commands])
            self.assertEqual(
                runner.commands,
                [
                    ("git", "status", "--short", "--branch"),
                    ("git", "rev-list", "--count", "HEAD"),
                    _verify_command(3),
                ],
            )


def _verify_command(batch: int) -> tuple[str, ...]:
    return (
        sys.executable,
        "scripts\\verify_batch_completion.py",
        "--batch",
        str(batch),
        "--mode",
        "post-commit",
    )


class FakeRunner:
    def __init__(self, *, outputs):
        self.outputs = outputs
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: list[str], cwd: Path):
        normalized = tuple(command)
        self.commands.append(normalized)
        return CommandResult(*self.outputs.get(normalized, (0, "", "")))


class CommandResult:
    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


if __name__ == "__main__":
    unittest.main()
