from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.verify_batch_completion import verify_batch


class VerifyBatchCompletionTests(unittest.TestCase):
    def test_missing_evidence_file_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = verify_batch(Path(temp_dir), 3, mode="pre-commit")

        self.assertFalse(result.passed)
        self.assertIn("缺少证据文件", result.blocking_gaps[0])

    def test_blocking_gaps_fail_even_when_commands_pass(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(root, batch=1, blocking_gaps=["untracked temp.py"])

            result = verify_batch(root, 1, mode="pre-commit")

        self.assertFalse(result.passed)
        self.assertIn("untracked temp.py", result.blocking_gaps)

    def test_coderabbit_must_run_successfully(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(root, batch=2, coderabbit={"ran": False})

            result = verify_batch(root, 2, mode="pre-commit")

        self.assertFalse(result.passed)
        self.assertIn("CodeRabbit 未成功执行", result.blocking_gaps)

    def test_coderabbit_is_optional_when_external_review_is_not_required(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(
                root,
                batch=11,
                coderabbit={"ran": True, "exit_code": 124, "unhandled_issues": 0},
                overrides={"external_review_required": False},
            )

            result = verify_batch(root, 11, mode="pre-commit")

        self.assertTrue(result.passed)
        self.assertEqual(result.blocking_gaps, ())

    def test_high_risk_changed_files_require_external_review(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(
                root,
                batch=14,
                coderabbit={"ran": True, "exit_code": 124, "unhandled_issues": 0},
                overrides={
                    "external_review_required": False,
                    "changed_files": ["football_advisor/probability_engine.py"],
                },
            )

            result = verify_batch(root, 14, mode="pre-commit")

        self.assertFalse(result.passed)
        self.assertIn(
            "高风险改动必须启用外部复审",
            "\n".join(result.blocking_gaps),
        )

    def test_gate_script_changes_require_external_review(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(
                root,
                batch=28,
                coderabbit={"ran": True, "exit_code": 124, "unhandled_issues": 0},
                overrides={
                    "external_review_required": False,
                    "changed_files": ["scripts/verify_batch_completion.py"],
                },
            )

            result = verify_batch(root, 28, mode="pre-commit")

        self.assertFalse(result.passed)
        self.assertIn(
            "高风险改动必须启用外部复审",
            "\n".join(result.blocking_gaps),
        )

    def test_missing_changed_files_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(root, batch=15, overrides={"changed_files": None})

            result = verify_batch(root, 15, mode="pre-commit")

        self.assertFalse(result.passed)
        self.assertIn("changed_files 必须是数组", result.blocking_gaps)

    def test_coderabbit_still_blocks_when_external_review_is_required(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(
                root,
                batch=12,
                coderabbit={"ran": True, "exit_code": 124, "unhandled_issues": 0},
                overrides={"external_review_required": True},
            )

            result = verify_batch(root, 12, mode="pre-commit")

        self.assertFalse(result.passed)
        self.assertIn("CodeRabbit 未成功执行", result.blocking_gaps)

    def test_external_review_rejects_contradictory_coderabbit_note(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(
                root,
                batch=16,
                coderabbit={
                    "ran": True,
                    "exit_code": 0,
                    "unhandled_issues": 0,
                    "note": "CodeRabbit CLI 因文件数超过150限制无法运行，已进行手动代码审查",
                    "artifact": "docs/verification/coderabbit/batch-16.txt",
                },
                overrides={"external_review_required": True},
            )

            result = verify_batch(root, 16, mode="pre-commit")

        self.assertFalse(result.passed)
        self.assertIn("CodeRabbit 结果说明与成功状态矛盾", result.blocking_gaps)

    def test_external_review_does_not_reject_incidental_error_words(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(
                root,
                batch=20,
                coderabbit={
                    "ran": True,
                    "exit_code": 0,
                    "unhandled_issues": 0,
                    "summary": "reviewed timeout and error handling tests",
                    "artifact": "docs/verification/coderabbit/batch-20.txt",
                },
                overrides={"external_review_required": True},
            )

            result = verify_batch(root, 20, mode="pre-commit")

        self.assertTrue(result.passed)
        self.assertEqual(result.blocking_gaps, ())

    def test_git_head_failure_in_real_repo_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".git").mkdir()
            _write_valid_evidence(root, batch=21)

            result = verify_batch(root, 21, mode="pre-commit")

        self.assertFalse(result.passed)
        self.assertIn("无法读取当前 HEAD，不能校验 evidence.commit", result.blocking_gaps)

    def test_external_review_requires_raw_artifact_when_required(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(
                root,
                batch=17,
                coderabbit={"ran": True, "exit_code": 0, "unhandled_issues": 0},
                overrides={"external_review_required": True},
            )

            result = verify_batch(root, 17, mode="pre-commit")

        self.assertFalse(result.passed)
        self.assertIn("外部复审缺少原始产物路径: coderabbit.artifact", result.blocking_gaps)

    def test_pre_commit_evidence_commit_must_match_current_head_when_known(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(root, batch=18, overrides={"commit": "old1234"})

            result = verify_batch(
                root,
                18,
                mode="pre-commit",
                current_commit="new5678",
            )

        self.assertFalse(result.passed)
        self.assertIn(
            "证据 commit=old1234 与当前 HEAD=new5678 不一致",
            result.blocking_gaps,
        )

    def test_post_commit_evidence_commit_must_match_parent_commit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(root, batch=22, overrides={"commit": "base1234"})

            result = verify_batch(
                root,
                22,
                mode="post-commit",
                git_status_text="",
                current_commit="new5678",
                parent_commit="base1234",
            )

        self.assertTrue(result.passed)
        self.assertEqual(result.blocking_gaps, ())

    def test_post_commit_rejects_stale_evidence_commit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(root, batch=23, overrides={"commit": "old1234"})

            result = verify_batch(
                root,
                23,
                mode="post-commit",
                git_status_text="",
                current_commit="new5678",
                parent_commit="base1234",
            )

        self.assertFalse(result.passed)
        self.assertIn(
            "证据 commit=old1234 与当前 HEAD 父提交=base1234 不一致",
            result.blocking_gaps,
        )

    def test_code_changes_require_required_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(
                root,
                batch=19,
                overrides={
                    "changed_files": ["football_advisor/models.py"],
                    "required_artifacts": [],
                },
            )

            result = verify_batch(root, 19, mode="pre-commit")

        self.assertFalse(result.passed)
        self.assertIn("代码改动必须列出 required_artifacts", result.blocking_gaps)

    def test_external_review_required_must_be_boolean(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(
                root,
                batch=13,
                overrides={"external_review_required": "false"},
            )

            result = verify_batch(root, 13, mode="pre-commit")

        self.assertFalse(result.passed)
        self.assertIn("external_review_required 必须是布尔值", result.blocking_gaps)

    def test_post_commit_mode_requires_clean_git_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(root, batch=4)

            result = verify_batch(
                root,
                4,
                mode="post-commit",
                git_status_text="?? temp.py\n",
            )

        self.assertFalse(result.passed)
        self.assertIn("工作区存在未提交或未跟踪改动", result.blocking_gaps[0])

    def test_post_commit_mode_rejects_dirty_evidence_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(root, batch=25)

            result = verify_batch(
                root,
                25,
                mode="post-commit",
                git_status_text=" M docs/verification/batches/batch-25-evidence.json\n",
            )

        self.assertFalse(result.passed)
        self.assertIn("工作区存在未提交或未跟踪改动", result.blocking_gaps[0])

    def test_post_commit_rejects_root_commit_without_explicit_init_batch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".git").mkdir()
            _write_valid_evidence(root, batch=26, overrides={"commit": "head1234"})

            result = verify_batch(
                root,
                26,
                mode="post-commit",
                git_status_text="",
                current_commit="head1234",
                parent_commit=None,
            )

        self.assertFalse(result.passed)
        self.assertIn("root commit 不能作为普通批次验收提交", result.blocking_gaps)

    def test_post_commit_uses_committed_diff_for_high_risk_detection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(
                root,
                batch=27,
                coderabbit={"ran": True, "exit_code": 124, "unhandled_issues": 0},
                overrides={
                    "commit": "base1234",
                    "changed_files": [],
                    "external_review_required": False,
                },
            )

            result = verify_batch(
                root,
                27,
                mode="post-commit",
                git_status_text="",
                current_commit="new5678",
                parent_commit="base1234",
                committed_changed_files=["football_advisor/db_schema.py"],
            )

        self.assertFalse(result.passed)
        self.assertIn(
            "高风险改动必须启用外部复审",
            "\n".join(result.blocking_gaps),
        )

    def test_malformed_collection_fields_report_gaps_without_crashing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(
                root,
                batch=6,
                overrides={
                    "required_artifacts": "docs/verification/artifact.txt",
                    "commands": {"name": "unit", "exit_code": 0},
                },
            )

            result = verify_batch(root, 6, mode="pre-commit")

        self.assertFalse(result.passed)
        self.assertIn("required_artifacts 必须是数组", result.blocking_gaps)
        self.assertIn("commands 必须是数组", result.blocking_gaps)

    def test_invalid_schema_metadata_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(
                root,
                batch=7,
                overrides={"schema_version": "0.9", "commit": "abc", "commands": []},
            )

            result = verify_batch(root, 7, mode="pre-commit")

        self.assertFalse(result.passed)
        self.assertIn("schema_version 必须是 1.0", result.blocking_gaps)
        self.assertIn("commit 必须至少包含7个字符", result.blocking_gaps)
        self.assertIn("commands 至少需要记录一条命令", result.blocking_gaps)

    def test_evidence_must_be_generated_by_finalize_script(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(root, batch=24, overrides={"generated_by": "manual"})

            result = verify_batch(root, 24, mode="pre-commit")

        self.assertFalse(result.passed)
        self.assertIn(
            "generated_by 必须是 scripts/finalize_batch.py",
            result.blocking_gaps,
        )

    def test_boolean_batch_is_not_accepted_as_integer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(root, batch=9, overrides={"batch": True})

            result = verify_batch(root, 9, mode="pre-commit")

        self.assertFalse(result.passed)
        self.assertIn("batch 必须是非负整数", result.blocking_gaps)

    def test_result_blocking_gaps_are_immutable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = verify_batch(Path(temp_dir), 10, mode="pre-commit")

        self.assertIsInstance(result.blocking_gaps, tuple)

    def test_malformed_coderabbit_unhandled_count_reports_gap_without_crashing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(
                root,
                batch=8,
                coderabbit={"ran": True, "exit_code": 0, "unhandled_issues": "many"},
            )

            result = verify_batch(root, 8, mode="pre-commit")

        self.assertFalse(result.passed)
        self.assertIn("coderabbit.unhandled_issues 必须是整数", result.blocking_gaps)

    def test_valid_pre_commit_evidence_passes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_evidence(root, batch=5)

            result = verify_batch(root, 5, mode="pre-commit")

        self.assertTrue(result.passed)
        self.assertEqual(result.blocking_gaps, ())


def _write_valid_evidence(
    root: Path,
    *,
    batch: int,
    blocking_gaps: list[str] | None = None,
    coderabbit: dict | None = None,
    overrides: dict | None = None,
) -> None:
    artifact = root / "docs" / "verification" / "artifact.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("ok", encoding="utf-8")
    coderabbit_artifact = (
        root / "docs" / "verification" / "coderabbit" / f"batch-{batch}.txt"
    )
    coderabbit_artifact.parent.mkdir(parents=True, exist_ok=True)
    coderabbit_artifact.write_text("CodeRabbit review passed", encoding="utf-8")
    review = root / "docs" / "verification" / "batches" / f"batch-{batch}-review.md"
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text("# review\n", encoding="utf-8")
    coderabbit_result = coderabbit or {
        "ran": True,
        "exit_code": 0,
        "unhandled_issues": 0,
        "artifact": str(coderabbit_artifact.relative_to(root)),
    }
    evidence = {
        "schema_version": "1.0",
        "generated_by": "scripts/finalize_batch.py",
        "batch": batch,
        "commit": "abc1234",
        "mode": "pre-commit",
        "changed_files": ["docs/verification/artifact.txt"],
        "required_artifacts": [str(artifact.relative_to(root))],
        "review_file": str(review.relative_to(root)),
        "commands": [
            {
                "name": "unit",
                "command": "python -m unittest",
                "exit_code": 0,
                "summary": "1/1 ok",
            }
        ],
        "coderabbit": coderabbit_result,
        "progress_updated": True,
        "independent_commit": True,
        "blocking_gaps": blocking_gaps or [],
    }
    if overrides:
        evidence.update(overrides)
    evidence_path = (
        root / "docs" / "verification" / "batches" / f"batch-{batch}-evidence.json"
    )
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
