from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import LLMConfig, load_config


@dataclass(frozen=True)
class LLMResult:
    content: str
    provider: str
    used_backup: bool


class LLMRouter:
    def __init__(
        self,
        external_base_url: str | None = None,
        external_api_key: str | None = None,
        external_model: str | None = None,
        backup_base_url: str | None = None,
        backup_model: str | None = None,
        retries: int | None = None,
        timeout_seconds: int | None = None,
        config: LLMConfig | None = None,
    ) -> None:
        llm_config = config or load_config().llm
        self.external_base_url = external_base_url or llm_config.external_base_url
        self.external_api_key = external_api_key or llm_config.external_api_key
        self.external_model = external_model or llm_config.external_model
        self.backup_base_url = backup_base_url or llm_config.backup_base_url
        self.backup_model = backup_model or llm_config.backup_model
        self.retries = retries if retries is not None else llm_config.retries
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else llm_config.timeout_seconds
        )

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        try:
            content = self.generate_external(system_prompt, user_prompt)
            return LLMResult(content=content, provider="external", used_backup=False)
        except Exception:
            pass

        try:
            content = self.generate_backup(system_prompt, user_prompt)
            return LLMResult(
                content=content, provider="ollama_backup", used_backup=True
            )
        except Exception:
            return LLMResult(
                content=user_prompt, provider="local_template", used_backup=True
            )

    def generate_external(self, system_prompt: str, user_prompt: str) -> str:
        if not self.external_base_url or not self.external_api_key:
            raise RuntimeError("external model is not configured")

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._chat_completion(
                    self.external_base_url,
                    self.external_api_key,
                    self.external_model,
                    system_prompt,
                    user_prompt,
                )
            except Exception as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(0.4 * (2**attempt))
        raise RuntimeError("external model request failed") from last_error

    def generate_backup(self, system_prompt: str, user_prompt: str) -> str:
        return self._chat_completion(
            self.backup_base_url,
            "ollama",
            self.backup_model,
            system_prompt,
            user_prompt,
        )

    def _chat_completion(
        self,
        base_url: str,
        api_key: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc
        return body["choices"][0]["message"]["content"]
