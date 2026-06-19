from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.enforce_batch_gate import _git_output, resolve_batch_number, run_batch_gate


class EnforceBatchGateTests(unittest.TestCase):
    def test_resolves_explicit_batch_first(self):
        batch = resolve_batch_number(
            explicit_batch=3,
            env={},
            branch_name="codex/batch-2",
            commit_message="批次1",
        )

        self.assertEqual(batch, 3)

    def test_resolves_batch_from_environment(self):
        batch = resolve_batch_number(
            explicit_batch=None,
            env={"FOOTBALL_ADVISOR_BATCH": "4"},
            branch_name="codex/no-batch",
            commit_message="",
        )

        self.assertEqual(batch, 4)

    def test_resolves_batch_from_branch_or_commit_message(self):
        self.assertEqual(
            resolve_batch_number(
                explicit_batch=None,
                env={},
                branch_name="codex/batch-12-provider-fix",
                commit_message="",
            ),
            12,
        )
        self.assertEqual(
            resolve_batch_number(
                explicit_batch=None,
                env={},
                branch_name="codex/no-batch",
                commit_message="批次7：补齐证据",
            ),
            7,
        )

    def test_pre_commit_does_not_use_commit_message(self):
        with self.assertRaisesRegex(ValueError, "无法确定批次编号"):
            resolve_batch_number(
                explicit_batch=None,
                env={},
                branch_name="codex/no-batch",
                commit_message="批次7：上一条提交不能作为预提交批次",
                allow_commit_message=False,
            )

    def test_post_commit_can_use_commit_message(self):
        batch = resolve_batch_number(
            explicit_batch=None,
            env={},
            branch_name="codex/no-batch",
            commit_message="批次8：提交后验收",
            allow_commit_message=True,
        )

        self.assertEqual(batch, 8)

    def test_missing_batch_number_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "无法确定批次编号"):
            resolve_batch_number(
                explicit_batch=None,
                env={},
                branch_name="codex/no-batch",
                commit_message="no batch",
            )

    def test_gate_invokes_verifier_with_resolved_batch_and_mode(self):
        calls: list[list[str]] = []

        def fake_runner(command: list[str], cwd: Path) -> int:
            calls.append(command)
            return 0

        with tempfile.TemporaryDirectory() as temp_dir:
            exit_code = run_batch_gate(
                repo_root=Path(temp_dir),
                mode="pre-commit",
                explicit_batch=None,
                env={"FOOTBALL_ADVISOR_BATCH": "5"},
                branch_name="codex/no-batch",
                commit_message="",
                runner=fake_runner,
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            calls,
            [
                [
                    sys.executable,
                    str(Path("scripts") / "verify_batch_completion.py"),
                    "--batch",
                    "5",
                    "--mode",
                    "pre-commit",
                ]
            ],
        )

    def test_git_output_uses_utf8_with_replacement(self):
        completed = Mock(returncode=0, stdout="批次0\n")

        with patch("scripts.enforce_batch_gate.subprocess.run", return_value=completed) as run:
            output = _git_output(Path("."), ["log", "-1", "--pretty=%B"])

        self.assertEqual(output, "批次0")
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run.call_args.kwargs["errors"], "replace")


if __name__ == "__main__":
    unittest.main()
