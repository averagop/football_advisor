"""
报告生成器 (ReportGenerator)

负责报告的生成与回退链：
  1. 外部模型生成 JSON 叙述 → 解析为 ReportNarrative → 代码渲染最终报告
  2. 外部模型失败/无效 → Qwen 备用 → 同样流程
  3. 两者都失败/无效 → 代码模板（无叙述）
  4. 所有叙述必须通过 validate_narrative 校验，防止模型编造数据
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from .llm_router import LLMRouter
from .models import PredictionBundle, ReportNarrative
from .report import ReportBuilder

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReportGenerationResult:
    content: str
    provider: str
    generated: bool
    errors: tuple[str, ...] = ()

    @property
    def used_backup(self) -> bool:
        return self.provider != "external"


class ReportGenerator:
    """报告生成器：外部 LLM → Qwen 备用 → 模板回退，所有输出经同一校验器验证。"""

    def __init__(
        self,
        llm_router: LLMRouter | None = None,
        report_builder: ReportBuilder | None = None,
    ) -> None:
        self.llm_router = llm_router or LLMRouter()
        self.report_builder = report_builder or ReportBuilder()

    def generate_report(
        self,
        bundle: PredictionBundle,
        extra_system_prompt: str = "",
        user_prompt: str = "",
    ) -> ReportGenerationResult:
        if not extra_system_prompt:
            extra_system_prompt, user_prompt = self.report_builder.build_prompt(bundle)
        system_prompt = extra_system_prompt
        errors: list[str] = []

        # 阶段 1: 尝试外部模型 → JSON 叙述 → 代码渲染
        external_content = ""
        try:
            external_result = self.llm_router.generate_external(
                system_prompt,
                user_prompt,
            )
            external_content = external_result.content if hasattr(external_result, "content") else str(external_result)
        except Exception as exc:
            errors.append(f"external_model_error: {_sanitize_error(str(exc))}")
            logger.warning("External model failed: %s", _sanitize_error(str(exc)))

        if external_content:
            result = self._try_narrative_pipeline(bundle, external_content, "external", errors)
            if result is not None:
                return result
            errors.append("external_model_invalid: 输出未通过校验")

        # 阶段 2: 尝试 Qwen 备用模型 → JSON 叙述 → 代码渲染
        qwen_content = ""
        try:
            qwen_result = self.llm_router.generate_backup(
                system_prompt,
                user_prompt,
            )
            qwen_content = qwen_result.content if hasattr(qwen_result, "content") else str(qwen_result)
        except Exception as exc:
            errors.append(f"qwen_backup_error: {_sanitize_error(str(exc))}")
            logger.warning("Qwen backup failed: %s", _sanitize_error(str(exc)))

        if qwen_content:
            result = self._try_narrative_pipeline(bundle, qwen_content, "ollama_backup", errors)
            if result is not None:
                return result
            errors.append("qwen_backup_invalid: 输出未通过校验")

        # 阶段 3: 回退到代码模板（无叙述）
        errors.append("all_models_failed_or_invalid: 回退到模板")
        template_content = self.report_builder.build_markdown(bundle)
        return ReportGenerationResult(
            content=template_content,
            provider="local_template",
            generated=False,
            errors=tuple(errors),
        )

    def _try_narrative_pipeline(
        self,
        bundle: PredictionBundle,
        raw_content: str,
        provider: str,
        errors: list[str],
    ) -> ReportGenerationResult | None:
        """尝试解析 LLM 输出为 ReportNarrative 并渲染最终报告。

        Returns:
            ReportGenerationResult 如果成功，None 如果失败。
        """
        # 1. 解析 JSON
        narrative = self._parse_narrative(raw_content)
        if narrative is None:
            errors.append(f"{provider}_json_parse_failed")
            return None

        # 2. 验证叙述内容
        is_valid, validation_errors = self.report_builder.validate_narrative(
            bundle, narrative
        )
        if not is_valid:
            for ve in validation_errors:
                errors.append(f"{provider}_narrative_validation: {ve}")
            return None

        # 3. 代码渲染最终报告
        final_content = self.report_builder.build_markdown(bundle, narrative)

        # 4. 验证最终报告格式
        if not self.report_builder.is_valid_generated_report(bundle, final_content):
            errors.append(f"{provider}_final_report_invalid")
            return None

        return ReportGenerationResult(
            content=final_content,
            provider=provider,
            generated=True,
            errors=tuple(errors),
        )

    @staticmethod
    def _parse_narrative(raw_content: str) -> ReportNarrative | None:
        """仅接受恰好包含三个字符串字段的纯 JSON 对象。"""
        if not raw_content or not raw_content.strip():
            return None

        try:
            data = json.loads(raw_content.strip())
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM output as strict JSON")
            return None

        required_fields = {"key_factors", "main_risks", "reasoning_summary"}
        if not isinstance(data, dict) or set(data) != required_fields:
            return None
        if any(not isinstance(data[field], str) for field in required_fields):
            return None

        key_factors = data["key_factors"].strip()
        main_risks = data["main_risks"].strip()
        reasoning_summary = data["reasoning_summary"].strip()
        if not all((key_factors, main_risks, reasoning_summary)):
            return None

        return ReportNarrative(
            key_factors=key_factors,
            main_risks=main_risks,
            reasoning_summary=reasoning_summary,
        )


def _sanitize_error(error: str) -> str:
    """脱敏错误信息：移除 API Key、请求头、完整响应。"""
    import re as _re

    sanitized = error
    # 移除可能的 Bearer token
    sanitized = _re.sub(r"Bearer\s+\S+", "Bearer <REDACTED>", sanitized)
    # 移除 URL 中的凭据
    sanitized = _re.sub(r"://[^:@]+:[^@]+@", "://<REDACTED>:<REDACTED>@", sanitized)
    # 截断过长错误信息
    if len(sanitized) > 500:
        sanitized = sanitized[:500] + "..."
    return sanitized
