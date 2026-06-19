from __future__ import annotations

import unittest

from football_advisor.models import ReportNarrative
from football_advisor.report_generator import ReportGenerator


class FakeReportBuilder:
    def __init__(self) -> None:
        self.template_calls = 0
        self.narrative_valid = True

    def build_prompt(self, bundle):
        return "system", "user"

    def validate_narrative(self, bundle, narrative: ReportNarrative) -> tuple[bool, list[str]]:
        if not self.narrative_valid:
            return False, ["fake_validation_error"]
        return True, []

    def build_markdown(self, bundle, narrative: ReportNarrative | None = None) -> str:
        self.template_calls += 1
        if narrative is not None:
            return f"report_with_narrative: {narrative.key_factors}"
        return "template"

    def is_valid_generated_report(self, bundle, content: str) -> bool:
        return bool(content and content.strip())


class CountingRouter:
    def __init__(self, external=None, backup=None) -> None:
        self.external = external
        self.backup = backup
        self.external_calls = 0
        self.backup_calls = 0

    def generate_external(self, system_prompt: str, user_prompt: str) -> str:
        self.external_calls += 1
        if isinstance(self.external, Exception):
            raise self.external
        return self.external or ""

    def generate_backup(self, system_prompt: str, user_prompt: str) -> str:
        self.backup_calls += 1
        if isinstance(self.backup, Exception):
            raise self.backup
        return self.backup or ""


class ReportGeneratorTests(unittest.TestCase):
    def test_external_success_does_not_call_backup(self):
        """外部模型返回有效 JSON 时，不调用备用模型。"""
        router = CountingRouter(
            external='{"key_factors": "主队状态良好", "main_risks": "伤病风险", "reasoning_summary": "综合来看主队占优"}',
        )
        builder = FakeReportBuilder()

        result = ReportGenerator(router, builder).generate_report(object())

        self.assertEqual(result.provider, "external")
        self.assertTrue(result.generated)
        self.assertIn("report_with_narrative", result.content)
        self.assertEqual(router.external_calls, 1)
        self.assertEqual(router.backup_calls, 0)
        self.assertEqual(builder.template_calls, 1)  # build_markdown called once

    def test_external_failure_calls_backup_once(self):
        """外部模型异常时，调用 Qwen 备用一次。"""
        router = CountingRouter(
            external=RuntimeError("external failed"),
            backup='{"key_factors": "备用分析", "main_risks": "备用风险", "reasoning_summary": "备用总结"}',
        )
        builder = FakeReportBuilder()

        result = ReportGenerator(router, builder).generate_report(object())

        self.assertEqual(result.provider, "ollama_backup")
        self.assertTrue(result.generated)
        self.assertEqual(router.external_calls, 1)
        self.assertEqual(router.backup_calls, 1)
        self.assertEqual(builder.template_calls, 1)

    def test_both_models_failure_builds_template_once(self):
        """两个模型都失败时，回退到模板。"""
        router = CountingRouter(
            external=RuntimeError("external failed"),
            backup=RuntimeError("backup failed"),
        )
        builder = FakeReportBuilder()

        result = ReportGenerator(router, builder).generate_report(object())

        self.assertEqual(result.provider, "local_template")
        self.assertFalse(result.generated)
        self.assertEqual(router.external_calls, 1)
        self.assertEqual(router.backup_calls, 1)
        self.assertEqual(builder.template_calls, 1)

    def test_external_invalid_json_falls_back_to_qwen(self):
        """外部模型返回无效 JSON 时，回退到 Qwen。"""
        router = CountingRouter(
            external="这不是 JSON，是一段随便的文字",
            backup='{"key_factors": "备用分析", "main_risks": "风险", "reasoning_summary": "总结"}',
        )
        builder = FakeReportBuilder()

        result = ReportGenerator(router, builder).generate_report(object())

        self.assertEqual(result.provider, "ollama_backup")
        self.assertTrue(result.generated)
        self.assertEqual(router.external_calls, 1)
        self.assertEqual(router.backup_calls, 1)

    def test_external_json_with_markdown_wrapper_is_rejected(self):
        """严格契约拒绝 Markdown 包裹，进入备用模型。"""
        router = CountingRouter(
            external='```json\n{"key_factors": "分析", "main_risks": "风险", "reasoning_summary": "总结"}\n```',
            backup='{"key_factors": "备用", "main_risks": "风险", "reasoning_summary": "总结"}',
        )
        builder = FakeReportBuilder()

        result = ReportGenerator(router, builder).generate_report(object())

        self.assertEqual(result.provider, "ollama_backup")
        self.assertTrue(result.generated)

    def test_external_json_with_extra_text_is_rejected(self):
        """严格契约拒绝 JSON 前后额外文本。"""
        router = CountingRouter(
            external='好的，以下是我的分析：\n{"key_factors": "主队近期状态出色", "main_risks": "客队反击威胁", "reasoning_summary": "建议谨慎"}',
            backup='{"key_factors": "备用", "main_risks": "风险", "reasoning_summary": "总结"}',
        )
        builder = FakeReportBuilder()

        result = ReportGenerator(router, builder).generate_report(object())

        self.assertEqual(result.provider, "ollama_backup")
        self.assertTrue(result.generated)

    def test_external_json_with_extra_field_is_rejected(self):
        router = CountingRouter(
            external='{"key_factors": "分析", "main_risks": "风险", "reasoning_summary": "总结", "probability": 0.72}',
            backup='{"key_factors": "备用", "main_risks": "风险", "reasoning_summary": "总结"}',
        )

        result = ReportGenerator(router, FakeReportBuilder()).generate_report(object())

        self.assertEqual(result.provider, "ollama_backup")

    def test_narrative_validation_rejects_invalid_content(self):
        """叙述验证失败时，回退到下一级。"""
        router = CountingRouter(
            external='{"key_factors": "包含虚假数字 72%", "main_risks": "风险", "reasoning_summary": "总结"}',
            backup='{"key_factors": "备用分析", "main_risks": "备用风险", "reasoning_summary": "备用总结"}',
        )
        builder = FakeReportBuilder()
        builder.narrative_valid = False  # 模拟验证失败

        result = ReportGenerator(router, builder).generate_report(object())

        # 两个模型的叙述验证都失败，应回退到模板
        self.assertEqual(result.provider, "local_template")
        self.assertFalse(result.generated)

    def test_both_models_invalid_json_falls_back_to_template(self):
        """两个模型都返回无效 JSON 时，回退到模板。"""
        router = CountingRouter(
            external="无效输出",
            backup="也是无效输出",
        )
        builder = FakeReportBuilder()

        result = ReportGenerator(router, builder).generate_report(object())

        self.assertEqual(result.provider, "local_template")
        self.assertFalse(result.generated)
        self.assertEqual(result.content, "template")

    def test_empty_llm_output_falls_back(self):
        """LLM 返回空字符串时，跳过解析直接回退。"""
        router = CountingRouter(
            external="",
            backup='{"key_factors": "备用", "main_risks": "风险", "reasoning_summary": "总结"}',
        )
        builder = FakeReportBuilder()

        result = ReportGenerator(router, builder).generate_report(object())

        self.assertEqual(result.provider, "ollama_backup")
        self.assertTrue(result.generated)


if __name__ == "__main__":
    unittest.main()
