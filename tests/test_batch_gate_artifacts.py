from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class BatchGateArtifactsTests(unittest.TestCase):
    def test_git_hooks_call_enforce_batch_gate(self):
        pre_commit = REPO_ROOT / ".githooks" / "pre-commit"
        pre_push = REPO_ROOT / ".githooks" / "pre-push"

        self.assertTrue(pre_commit.exists())
        self.assertTrue(pre_push.exists())
        self.assertIn("scripts/git-hooks/pre-commit.ps1", pre_commit.read_text())
        self.assertIn("scripts/git-hooks/pre-push.ps1", pre_push.read_text())
        self.assertIn('"$@"', pre_push.read_text())

    def test_hook_scripts_use_pre_and_post_commit_modes(self):
        pre_commit = REPO_ROOT / "scripts" / "git-hooks" / "pre-commit.ps1"
        pre_push = REPO_ROOT / "scripts" / "git-hooks" / "pre-push.ps1"

        self.assertTrue(pre_commit.exists())
        self.assertTrue(pre_push.exists())
        self.assertIn("--mode pre-commit", pre_commit.read_text(encoding="utf-8"))
        self.assertIn("--mode post-commit", pre_push.read_text(encoding="utf-8"))

    def test_ci_workflow_runs_batch_gate_and_tests(self):
        workflow = REPO_ROOT / ".github" / "workflows" / "batch-gate.yml"

        self.assertTrue(workflow.exists())
        content = workflow.read_text(encoding="utf-8")
        self.assertIn("fetch-depth: 0", content)
        self.assertIn("scripts\\enforce_batch_gate.py --mode post-commit", content)
        self.assertIn("-m unittest discover -s tests -v", content)

    def test_install_script_configures_hooks_path(self):
        installer = REPO_ROOT / "scripts" / "install_git_hooks.ps1"

        self.assertTrue(installer.exists())
        content = installer.read_text(encoding="utf-8")
        self.assertIn("core.hooksPath", content)
        self.assertIn(".githooks", content)
        self.assertIn("LASTEXITCODE", content)


if __name__ == "__main__":
    unittest.main()
