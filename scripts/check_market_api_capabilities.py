"""脱敏检查 The Odds API / RapidAPI 是否具备赔率和资金流字段。

该脚本只做能力探测，不写 DuckDB，不打印 token、请求头或完整 URL。
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from football_advisor.config import load_config
from football_advisor.third_party_odds_client import (  # noqa: E402
    CAPITAL_FLOW_FIELD_NAMES,
    ThirdPartyOddsClient,
)


DEFAULT_ENV_PATH = ROOT / ".env"


def main(argv: list[str] | None = None) -> int:
    env = load_env(DEFAULT_ENV_PATH)
    config = load_config(env)

    print("# 市场 API 能力检查")
    print("")
    print("## 配置状态")
    for key in (
        "THE_ODDS_API_TOKEN",
        "THE_ODDS_API_BASE_URL",
        "RAPIDAPI_TOKEN",
        "ODDS_FEED_RAPID_HOST",
        "ODDS_FEED_RAPID_BASE_URL",
    ):
        print(f"- {key}: {redacted_status(env, key)}")

    print("")
    print("## 探测结果")
    the_odds_result = check_the_odds_api(config.sync)
    rapid_result = check_rapidapi_odds_feed(config.sync)
    _print_result(the_odds_result)
    _print_result(rapid_result)

    return 0


def load_env(path: Path) -> dict[str, str]:
    env = dict(os.environ)
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            env[key] = value
    return env


def redacted_status(env: dict[str, str], key: str) -> str:
    return "已配置" if env.get(key, "").strip() else "未配置"


def classify_payload(payload: Any) -> dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False)
    odds_available = bool(
        re.search(r'"(bookmakers|markets|outcomes|home_odds|homeOdds|h2h)"', text)
        and re.search(r'"(price|odds|home_odds|homeOdds)"', text)
    )
    capital_flow_fields = sorted(_capital_flow_fields(payload))
    return {
        "odds_available": odds_available,
        "capital_flow_fields": capital_flow_fields,
    }


def check_the_odds_api(sync_config: Any) -> dict[str, Any]:
    provider = "TheOddsAPI"
    if not sync_config.the_odds_api_token:
        return _skipped(provider, "未配置 token")
    try:
        client = ThirdPartyOddsClient(
            the_odds_api_token=sync_config.the_odds_api_token,
            the_odds_api_base_url=sync_config.the_odds_api_base_url,
            the_odds_api_sport_keys=sync_config.the_odds_api_sport_keys,
        )
        payload = []
        used_sport_key = ""
        last_error: Exception | None = None
        for sport_key in client._candidate_the_odds_sport_keys():
            try:
                candidate_payload = client._http_json(client._the_odds_api_url(sport_key))
            except Exception as exc:
                last_error = exc
                continue
            if isinstance(candidate_payload, list):
                payload = candidate_payload
                used_sport_key = sport_key
                if payload:
                    break
        if not used_sport_key and last_error is not None:
            raise last_error
        classified = classify_payload(payload)
        return {
            "provider": provider,
            "status": "ok",
            "sport_key": used_sport_key,
            "odds_available": classified["odds_available"],
            "capital_flow_fields": classified["capital_flow_fields"],
            "conclusion": _conclusion(classified["capital_flow_fields"]),
        }
    except Exception as exc:
        return _failed(provider, exc)


def check_rapidapi_odds_feed(sync_config: Any) -> dict[str, Any]:
    provider = "RapidAPI-OddsFeed"
    if not sync_config.rapidapi_token:
        return _skipped(provider, "未配置 RAPIDAPI_TOKEN")
    if not sync_config.odds_feed_rapid_host or not sync_config.odds_feed_rapid_base_url:
        return _skipped(provider, "未配置 ODDS_FEED_RAPID_HOST 或 ODDS_FEED_RAPID_BASE_URL")
    if "/" not in sync_config.odds_feed_rapid_base_url.replace("https://", "", 1).replace("http://", "", 1):
        return _skipped(provider, "只配置了 RapidAPI host，缺少具体 endpoint path")
    try:
        request = urllib.request.Request(
            sync_config.odds_feed_rapid_base_url,
            headers={
                "x-rapidapi-key": sync_config.rapidapi_token,
                "x-rapidapi-host": sync_config.odds_feed_rapid_host,
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        classified = classify_payload(payload)
        return {
            "provider": provider,
            "status": "ok",
            "odds_available": classified["odds_available"],
            "capital_flow_fields": classified["capital_flow_fields"],
            "conclusion": _conclusion(classified["capital_flow_fields"]),
        }
    except Exception as exc:
        return _failed(provider, exc)


def _capital_flow_fields(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in CAPITAL_FLOW_FIELD_NAMES:
                found.add(str(key))
            found.update(_capital_flow_fields(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_capital_flow_fields(item))
    return found


def _conclusion(capital_flow_fields: list[str]) -> str:
    if capital_flow_fields:
        return "可作为资金流候选源，需要再做字段语义确认"
    return "当前响应只证明赔率能力，不能证明真实资金流能力"


def _skipped(provider: str, reason: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "status": "skipped",
        "reason": reason,
        "odds_available": False,
        "capital_flow_fields": [],
        "conclusion": "未探测",
    }


def _failed(provider: str, exc: Exception) -> dict[str, Any]:
    return {
        "provider": provider,
        "status": "failed",
        "reason": _safe_error(str(exc)),
        "odds_available": False,
        "capital_flow_fields": [],
        "conclusion": "接口未成功返回，不能判定资金流能力",
    }


def _safe_error(text: str) -> str:
    scrubbed = re.sub(r"(?i)(apiKey|api_key|key|token)=([^&\s]+)", r"\1=***", text)
    return scrubbed[:240]


def _print_result(result: dict[str, Any]) -> None:
    print(f"- provider: {result['provider']}")
    print(f"  status: {result['status']}")
    if result.get("reason"):
        print(f"  reason: {result['reason']}")
    if result.get("sport_key"):
        print(f"  sport_key: {result['sport_key']}")
    print(f"  odds_available: {result['odds_available']}")
    print(f"  capital_flow_fields: {', '.join(result['capital_flow_fields']) or '无'}")
    print(f"  conclusion: {result['conclusion']}")


if __name__ == "__main__":
    raise SystemExit(main())
