from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.verify_delivery_readiness import (
    REQUIRED_PATHS,
    build_execution_result,
    collect_import_checks,
    collect_path_checks,
    parse_env_file,
    summarize_env_checks,
)


class DeliveryReadinessTests(unittest.TestCase):
    def test_path_checks_fail_when_required_artifacts_are_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checks = collect_path_checks(root)

        missing = [check for check in checks if not check.passed]
        self.assertEqual(len(missing), len(REQUIRED_PATHS))
        self.assertIn("start_fastapi.bat", {check.name for check in missing})

    def test_path_checks_pass_for_minimal_delivery_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative_path in REQUIRED_PATHS:
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("placeholder", encoding="utf-8")

            checks = collect_path_checks(root)

        self.assertTrue(all(check.passed for check in checks))

    def test_env_summary_redacts_values_and_marks_optional_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "FOOTBALL_DUCKDB_PATH=football_system.db",
                        "FOOTBALL_SERPER_API_KEY=sk-test-secret",
                        "# FOOTBALL_EXTERNAL_LLM_API_KEY=<YOUR_API_KEY>",
                    ]
                ),
                encoding="utf-8",
            )

            values = parse_env_file(env_path)
            checks = summarize_env_checks(values)

        rendered = "\n".join(check.detail for check in checks)
        self.assertNotIn("sk-test-secret", rendered)
        self.assertIn("FOOTBALL_SERPER_API_KEY", {check.name for check in checks})
        self.assertTrue(
            next(check for check in checks if check.name == "FOOTBALL_DUCKDB_PATH").passed
        )

    def test_import_checks_can_load_project_modules_from_repo_root(self):
        checks = collect_import_checks(Path.cwd())
        by_name = {check.name: check for check in checks}

        self.assertTrue(by_name["football_advisor.api"].passed)
        self.assertTrue(by_name["openwebui_tools.football_advisor_tools"].passed)

    def test_execution_result_records_missing_required_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            checks = collect_path_checks(Path(tmp))

        result = build_execution_result(checks)

        self.assertEqual(result.verification_status, "INCOMPLETE")
        self.assertIsNone(result.final_status)
        self.assertTrue(result.missing_evidence)
        self.assertIn("start_fastapi.bat", result.missing_evidence[0])

    def test_execution_result_completes_when_required_checks_pass(self):
        checks = [
            *collect_path_checks(Path.cwd()),
            *summarize_env_checks({"FOOTBALL_DUCKDB_PATH": "football_system.db"}),
        ]

        result = build_execution_result(checks)

        self.assertEqual(result.verification_status, "COMPLETE")
        self.assertEqual(result.final_status, "SAFE_DEGRADED")
        self.assertEqual(result.missing_evidence, [])


if __name__ == "__main__":
    unittest.main()
