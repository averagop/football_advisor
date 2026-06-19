from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any


class ISportsClient:
    PROVIDER_NAME = "iSports-API"
    BASE_URL = "https://api.isportsapi.com"

    def __init__(self, api_token: str | None = None, base_url: str | None = None):
        self.api_token = api_token
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self._last_error: str | None = None

    def fetch_matches_by_date(self, connection: Any, match_date: date) -> dict[str, Any]:
        if not self.api_token:
            return self._empty_result("skipped_no_token")
        if urllib.parse.urlsplit(self.base_url).scheme.lower() != "https":
            return self._empty_result("skipped_insecure_transport")

        data = self._http_get(
            f"/sport/football/schedule?date={urllib.parse.quote(match_date.isoformat())}"
        )
        if data is None and self._last_error:
            return {
                **self._empty_result("error"),
                "error": self._last_error,
            }
        matches = self._extract_matches(data)
        if not matches:
            return self._empty_result("no_matches_found")

        staged_count = self._stage_matches(connection, matches)
        return {
            "status": "success",
            "match_count": len(matches),
            "staged_count": staged_count,
        }

    def _http_get(self, path: str) -> dict[str, Any] | None:
        self._last_error = None
        if self.api_token == "mock_token":
            return self._mock_response(path)

        separator = "&" if "?" in path else "?"
        url = (
            f"{self.base_url}{path}{separator}"
            f"api_key={urllib.parse.quote(self.api_token or '')}"
        )
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=10) as response:
                return json.loads(response.read().decode())
        except urllib.error.URLError:
            self._last_error = "transport_error"
            return None
        except json.JSONDecodeError:
            self._last_error = "invalid_json"
            return None

    def _extract_matches(self, data: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not data:
            return []
        for key in ("data", "matches", "result"):
            matches = data.get(key)
            if isinstance(matches, list):
                return matches
        return []

    def _stage_matches(
        self,
        connection: Any,
        matches: list[dict[str, Any]],
    ) -> int:
        if connection is None:
            return 0
        rows = []
        for match in matches:
            match_id = match.get("matchId") or match.get("match_id") or match.get("id")
            league_id = match.get("leagueId") or match.get("league_id")
            match_time = (
                match.get("matchTime")
                or match.get("match_time")
                or match.get("startTime")
                or match.get("time")
            )
            if not match_id or not match_time:
                continue
            rows.append(
                (
                    str(match_id),
                    str(league_id or ""),
                    str(match.get("homeId") or match.get("home_id") or ""),
                    str(match.get("awayId") or match.get("away_id") or ""),
                    str(match_time),
                    str(match.get("status") or ""),
                    _optional_int(match.get("homeScore") or match.get("home_score")),
                    _optional_int(match.get("awayScore") or match.get("away_score")),
                    self.PROVIDER_NAME,
                )
            )
        if not rows:
            return 0
        try:
            connection.executemany(
                """
                INSERT INTO staging.stg_match_schedule (
                    provider_match_id, provider_league_id, provider_home_team_id,
                    provider_away_team_id, match_time, status, home_score,
                    away_score, source_provider
                ) VALUES (?, ?, ?, ?, CAST(? AS TIMESTAMP), ?, ?, ?, ?)
                """,
                rows,
            )
        except Exception:
            return 0
        return len(rows)

    def _empty_result(self, status: str) -> dict[str, Any]:
        return {"status": status, "match_count": 0, "staged_count": 0}

    def _mock_response(self, path: str) -> dict[str, Any]:
        return {
            "data": [
                {
                    "matchId": "2001",
                    "leagueId": "17",
                    "homeId": "42",
                    "awayId": "49",
                    "matchTime": "2026-06-02 19:30:00",
                    "status": "0",
                    "homeScore": None,
                    "awayScore": None,
                }
            ]
        }


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
