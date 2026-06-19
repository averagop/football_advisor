from __future__ import annotations

import argparse
import importlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from football_advisor.models import ExecutionResult


REQUIRED_PATHS = (
    Path("start_fastapi.bat"),
    Path("football_advisor/api.py"),
    Path("openwebui_tools/football_advisor_tools.py"),
    Path("docs/PROJECT_REQUIREMENTS.md"),
    Path("docs/ARCHITECTURE.md"),
    Path("docs/PROGRESS.md"),
    Path("docs/PROJECT_ANALYSIS.md"),
)

REQUIRED_ENV_KEYS = ("FOOTBALL_DUCKDB_PATH",)
OPTIONAL_ENV_KEYS = (
    "FOOTBALL_EXTERNAL_LLM_BASE_URL",
    "FOOTBALL_EXTERNAL_LLM_API_KEY",
    "FOOTBALL_SERPER_API_KEY",
    "FOOTBALL_SEARXNG_BASE_URL",
    "FOOTBALL_SPORTTERY_BASE_URL",
    "API_FOOTBALL_TOKEN",
    "FOOTBALL_DATA_API_TOKEN",
    "THESPORTSDB_API_TOKEN",
)


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    passed: bool
    detail: str
    required: bool = True


def collect_path_checks(root: Path) -> list[ReadinessCheck]:
    checks: list[ReadinessCheck] = []
    for relative_path in REQUIRED_PATHS:
        checks.append(
            ReadinessCheck(
                name=str(relative_path),
                passed=(root / relative_path).exists(),
                detail="已找到" if (root / relative_path).exists() else "缺少交付必要文件",
            )
        )
    return checks


def parse_env_file(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def summarize_env_checks(values: dict[str, str]) -> list[ReadinessCheck]:
    checks: list[ReadinessCheck] = []
    for key in REQUIRED_ENV_KEYS:
        configured = bool(values.get(key))
        checks.append(
            ReadinessCheck(
                name=key,
                passed=configured,
                detail="已配置，值已隐藏" if configured else "未配置必需变量",
            )
        )
    for key in OPTIONAL_ENV_KEYS:
        configured = bool(values.get(key) or os.environ.get(key))
        checks.append(
            ReadinessCheck(
                name=key,
                passed=configured,
                detail="已配置，值已隐藏" if configured else "未配置，可按需启用",
                required=False,
            )
        )
    return checks


def collect_import_checks(root: Path) -> list[ReadinessCheck]:
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)

    checks: list[ReadinessCheck] = []
    for module_name in (
        "fastapi",
        "uvicorn",
        "duckdb",
        "football_advisor.api",
        "openwebui_tools.football_advisor_tools",
    ):
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            checks.append(
                ReadinessCheck(module_name, False, f"导入失败: {exc.__class__.__name__}")
            )
        else:
            checks.append(ReadinessCheck(module_name, True, "可导入"))
    return checks


def collect_readiness_checks(root: Path, env_path: Path) -> list[ReadinessCheck]:
    checks = []
    checks.extend(collect_path_checks(root))
    checks.extend(summarize_env_checks(parse_env_file(env_path)))
    checks.extend(collect_import_checks(root))
    return checks


def build_execution_result(checks: list[ReadinessCheck]) -> ExecutionResult:
    missing = [
        f"{check.name}: {check.detail}"
        for check in checks
        if check.required and not check.passed
    ]
    stages = [
        {
            "name": check.name,
            "passed": check.passed,
            "required": check.required,
            "detail": check.detail,
        }
        for check in checks
    ]
    failures = [
        {
            "name": check.name,
            "detail": check.detail,
        }
        for check in checks
        if check.required and not check.passed
    ]
    if missing:
        return ExecutionResult(
            verification_status="INCOMPLETE",
            final_status=None,
            missing_evidence=missing,
            stages=stages,
            failures=failures,
        )
    return ExecutionResult(
        verification_status="COMPLETE",
        final_status="SAFE_DEGRADED",
        missing_evidence=[],
        stages=stages,
        failures=[],
    )


def format_checks(checks: list[ReadinessCheck]) -> str:
    lines = ["# 本地交付就绪检查", ""]
    for check in checks:
        status = "通过" if check.passed else ("失败" if check.required else "提示")
        scope = "必需" if check.required else "可选"
        lines.append(f"- [{status}] {check.name} ({scope})：{check.detail}")
    blocking = [check for check in checks if check.required and not check.passed]
    lines.append("")
    if blocking:
        lines.append(f"结论：未就绪，存在 {len(blocking)} 个阻断项。")
    else:
        lines.append("结论：本地交付基础项已就绪。外部数据源能力仍以真实烟测结果为准。")
    result = build_execution_result(checks)
    lines.append("")
    lines.append(
        f"机器验收：verification_status={result.verification_status}, "
        f"final_status={result.final_status}"
    )
    if result.missing_evidence:
        lines.append("缺失证据：")
        lines.extend(f"- {item}" for item in result.missing_evidence)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本地交付就绪检查")
    parser.add_argument(
        "--root",
        default=Path(__file__).resolve().parents[1],
        type=Path,
        help="项目根目录",
    )
    parser.add_argument(
        "--env-path",
        default=None,
        type=Path,
        help="环境变量文件路径，默认使用项目根目录下的 .env",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    env_path = args.env_path.resolve() if args.env_path else root / ".env"
    checks = collect_readiness_checks(root, env_path)
    print(format_checks(checks))
    return 1 if any(check.required and not check.passed for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
