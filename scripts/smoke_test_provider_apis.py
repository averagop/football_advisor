from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="脱敏测试已配置体育数据 API。")
    parser.add_argument(
        "--date",
        default=datetime.now(timezone.utc).date().isoformat(),
        help="按 UTC 日期测试赛程接口，格式 YYYY-MM-DD。",
    )
    args = parser.parse_args(argv)
    env = _load_env(ROOT / ".env")

    checks = [
        _check_sporttery(env),
        _check_football_data(env, args.date),
        _check_api_football(env, args.date),
        _check_thesportsdb(env),
        _check_sportmonks(env, args.date),
        _check_isports(env, args.date),
    ]
    for check in checks:
        print(
            f"{check['provider']}: configured={check['configured']} "
            f"status={check['status']} http_status={check['http_status']} "
            f"count={check['count']} endpoint={check['endpoint']}"
        )
    failed_statuses = {"error", "invalid_token_format"}
    return 0 if all(c["status"] not in failed_statuses for c in checks) else 1


def _load_env(path: Path) -> dict[str, str]:
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
        if key and value and key not in env:
            env[key] = value
    return env


def _check_sporttery(env: dict[str, str]) -> dict[str, Any]:
    base_url = (env.get("FOOTBALL_SPORTTERY_BASE_URL") or "").rstrip("/")
    endpoint = (
        "gateway/uniform/football/"
        "getMatchCalculatorV1.qry?channel=c&poolCode=hhad,had"
    )
    if not base_url:
        return {
            "provider": "SportteryOfficialWeb",
            "configured": False,
            "status": "missing_base_url",
            "http_status": "n/a",
            "count": 0,
            "endpoint": endpoint,
        }
    url = f"https://webapi.sporttery.cn/{endpoint}"
    return _get_json_count("SportteryOfficialWeb", url, "value.matchInfoList", endpoint)


def _check_thesportsdb(env: dict[str, str]) -> dict[str, Any]:
    token = env.get("THESPORTSDB_API_TOKEN")
    base_url = env.get(
        "THESPORTSDB_BASE_URL",
        "https://www.thesportsdb.com/api/v1/json",
    ).rstrip("/")
    if not token:
        return _missing("TheSportsDB", "all_sports.php")
    endpoint = "all_sports.php"
    url = f"{base_url}/{urllib.parse.quote(token)}/{endpoint}"
    return _get_json_count("TheSportsDB", url, "sports", endpoint)


def _check_football_data(env: dict[str, str], date_value: str) -> dict[str, Any]:
    token = env.get("FOOTBALL_DATA_API_TOKEN")
    base_url = env.get(
        "FOOTBALL_DATA_BASE_URL",
        "https://api.football-data.org/v4",
    ).rstrip("/")
    endpoint = "competitions/"
    if not token:
        return _missing("football-data.org", endpoint)
    if _has_invalid_token_format(token):
        return _invalid_token_format("football-data.org", endpoint)
    url = f"{base_url}/{endpoint}"
    headers = {"X-Auth-Token": token}
    return _get_json_count(
        "football-data.org",
        url,
        "competitions",
        endpoint,
        headers,
    )


def _check_api_football(env: dict[str, str], date_value: str) -> dict[str, Any]:
    token = env.get("API_FOOTBALL_TOKEN")
    base_url = env.get(
        "API_FOOTBALL_BASE_URL",
        "https://v3.football.api-sports.io",
    ).rstrip("/")
    endpoint = f"fixtures?date={date_value}"
    if not token:
        return _missing("API-Football", endpoint)
    if _has_invalid_token_format(token):
        return _invalid_token_format("API-Football", endpoint)
    url = f"{base_url}/{endpoint}"
    headers = {"x-apisports-key": token}
    return _get_json_count("API-Football", url, "response", endpoint, headers)


def _check_sportmonks(env: dict[str, str], date_value: str) -> dict[str, Any]:
    token = env.get("SPORTMONKS_API_TOKEN")
    base_url = env.get(
        "SPORTMONKS_BASE_URL",
        "https://api.sportmonks.com/v3/football",
    ).rstrip("/")
    endpoint = f"fixtures/date/{date_value}"
    if not token:
        return _missing("Sportmonks", endpoint)
    url = (
        f"{base_url}/{endpoint}?"
        f"api_token={urllib.parse.quote(token)}"
    )
    return _get_json_count("Sportmonks", url, "data", endpoint)


def _check_isports(env: dict[str, str], date_value: str) -> dict[str, Any]:
    token = env.get("ISPORTS_API_TOKEN")
    base_url = env.get("ISPORTS_BASE_URL", "https://api.isportsapi.com").rstrip("/")
    endpoint = f"sport/football/schedule?date={date_value}"
    if not token:
        return _missing("iSports-API", endpoint)
    if urllib.parse.urlsplit(base_url).scheme.lower() != "https":
        return {
            "provider": "iSports-API",
            "configured": True,
            "status": "skipped_insecure_transport",
            "http_status": "n/a",
            "count": 0,
            "endpoint": endpoint,
        }
    url = (
        f"{base_url}/{endpoint}&"
        f"api_key={urllib.parse.quote(token)}"
    )
    result = _get_json_count("iSports-API", url, "data", endpoint)
    if result["count"] == 0 and isinstance(result.get("payload"), dict):
        for key in ("matches", "result"):
            value = result["payload"].get(key)
            if isinstance(value, list):
                result["count"] = len(value)
                break
    result.pop("payload", None)
    return result


def _get_text_presence(provider: str, url: str, endpoint: str) -> dict[str, Any]:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "FootballAdvisor/1.0 (+local decision support)"},
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            http_status = response.status
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return {
            "provider": provider,
            "configured": True,
            "status": "error",
            "http_status": exc.code,
            "count": 0,
            "endpoint": endpoint,
        }
    except (urllib.error.URLError, TimeoutError):
        return {
            "provider": provider,
            "configured": True,
            "status": "error",
            "http_status": "n/a",
            "count": 0,
            "endpoint": endpoint,
        }
    return {
        "provider": provider,
        "configured": True,
        "status": "ok" if text.strip() else "empty_response",
        "http_status": http_status,
        "count": 1 if text.strip() else 0,
        "endpoint": endpoint,
    }


def _get_json_count(
    provider: str,
    url: str,
    selector: str,
    endpoint: str,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=15) as response:
            http_status = response.status
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return {
            "provider": provider,
            "configured": True,
            "status": "error",
            "http_status": exc.code,
            "count": 0,
            "endpoint": endpoint,
        }
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return {
            "provider": provider,
            "configured": True,
            "status": "error",
            "http_status": "n/a",
            "count": 0,
            "endpoint": endpoint,
        }
    values = _select_payload(payload, selector)
    count = len(values) if isinstance(values, list) else 0
    return {
        "provider": provider,
        "configured": True,
        "status": "ok",
        "http_status": http_status,
        "count": count,
        "endpoint": endpoint,
        "payload": payload,
    }


def _select_payload(payload: Any, selector: str) -> Any:
    current = payload
    for key in selector.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _missing(provider: str, endpoint: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "configured": False,
        "status": "missing_token",
        "http_status": "n/a",
        "count": 0,
        "endpoint": endpoint,
    }


def _invalid_token_format(provider: str, endpoint: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "configured": True,
        "status": "invalid_token_format",
        "http_status": "n/a",
        "count": 0,
        "endpoint": endpoint,
    }


def _has_invalid_token_format(token: str) -> bool:
    stripped = token.strip()
    return (
        ":" in stripped
        or stripped.startswith("<")
        or stripped.endswith(">")
        or any(char.isspace() for char in stripped)
    )


if __name__ == "__main__":
    raise SystemExit(main())
