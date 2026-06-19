from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any


class SportmonksClient:
    PROVIDER_NAME = "Sportmonks"
    BASE_URL = "https://api.sportmonks.com/v3/football"

    def __init__(self, api_token: str | None = None, base_url: str | None = None):
        self.api_token = api_token
        self.base_url = (base_url or self.BASE_URL).rstrip("/")

    def fetch_matches_by_date(self, connection: Any, match_date: date) -> dict[str, Any]:
        if not self.api_token:
            return self._empty_result("skipped_no_token")

        data = self._http_get(f"/fixtures/date/{match_date.isoformat()}")
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
        if self.api_token == "mock_token":
            return self._mock_response(path)

        separator = "&" if "?" in path else "?"
        url = (
            f"{self.base_url}{path}{separator}"
            f"api_token={urllib.parse.quote(self.api_token or '')}"
        )
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=10) as response:
                return json.loads(response.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError):
            return None

    def _extract_matches(self, data: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not data:
            return []
        matches = data.get("data")
        return matches if isinstance(matches, list) else []

    def _stage_matches(
        self,
        connection: Any,
        matches: list[dict[str, Any]],
    ) -> int:
        if connection is None:
            return 0
        rows = []
        for match in matches:
            match_id = match.get("id")
            league_id = match.get("league_id")
            starting_at = match.get("starting_at") or match.get("starting_at_timestamp")
            if not match_id or not starting_at:
                continue
            rows.append(
                (
                    str(match_id),
                    str(league_id or ""),
                    str(match.get("localteam_id") or ""),
                    str(match.get("visitorteam_id") or ""),
                    str(starting_at),
                    str(match.get("state_id") or match.get("status") or ""),
                    None,
                    None,
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
                    "id": 1001,
                    "league_id": 4328,
                    "localteam_id": 133604,
                    "visitorteam_id": 133601,
                    "starting_at": "2026-06-02 19:30:00",
                    "state_id": 1,
                }
            ]
        }
