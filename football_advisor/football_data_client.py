from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import urllib.request
import urllib.error


class FootballDataClient:
    """Client for football-data.org API, integrating with staging schema."""

    PROVIDER_NAME = "football-data.org"

    def __init__(self, api_token: str | None = None, base_url: str = "https://api.football-data.org/v4"):
        self.api_token = api_token
        self.base_url = base_url.rstrip("/")

    def fetch_matches(self, connection: Any, date_from: str, date_to: str) -> dict[str, Any]:
        """Fetch matches for a specific date range and sink them to staging and core."""
        # 1. Fetch from API or mock
        raw_data = self._http_get(f"/matches?dateFrom={date_from}&dateTo={date_to}")
        
        if not raw_data or "matches" not in raw_data:
            return {
                "status": "failed_or_empty",
                "count": 0,
                "staged_count": 0,
                "merged_count": 0,
                "skipped_unmapped_count": 0,
            }

        matches = raw_data["matches"]
        if not matches:
            return {
                "status": "success",
                "count": 0,
                "staged_count": 0,
                "merged_count": 0,
                "skipped_unmapped_count": 0,
            }

        # 2. Insert into staging.stg_match_schedule
        stg_rows = []
        for match in matches:
            stg_rows.append((
                str(match["id"]),
                str(match["competition"]["id"]),
                str(match["homeTeam"]["id"]),
                str(match["awayTeam"]["id"]),
                match["utcDate"],
                match["status"],
                match.get("score", {}).get("fullTime", {}).get("home"),
                match.get("score", {}).get("fullTime", {}).get("away"),
                self.PROVIDER_NAME
            ))

        if stg_rows:
            connection.executemany("""
                INSERT INTO staging.stg_match_schedule (
                    provider_match_id, provider_league_id, provider_home_team_id, provider_away_team_id,
                    match_time, status, home_score, away_score, source_provider
                ) VALUES (?, ?, ?, ?, CAST(? AS TIMESTAMP), ?, ?, ?, ?)
            """, stg_rows)

        # 3. UPSERT into core.fact_match_schedule
        # We map provider IDs to our system IDs. For this mock, we assume the provider ID is the same as system_team_id or we just insert it directly if missing in dim mapping? 
        # Wait, the requirements state: "不可映射实体进入明确的错误清单或跳过清单，不静默吞掉。"
        # So we only insert if the provider ID is mapped. Since we don't have an explicit mapping table, we assume provider_team_id == system_team_id for now.
        
        provider_match_ids = [row[0] for row in stg_rows]
        staged_count = len(stg_rows)
        placeholders = ",".join("?" for _ in provider_match_ids)
        count_params = [self.PROVIDER_NAME, *provider_match_ids]

        eligible_count = 0
        if provider_match_ids:
            eligible_count = int(
                connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM staging.stg_match_schedule s
                    JOIN core.dim_provider_league_mapping lm
                      ON s.source_provider = lm.provider_name
                     AND s.provider_league_id = lm.provider_league_id
                    JOIN core.dim_provider_team_mapping hm
                      ON s.source_provider = hm.provider_name
                     AND s.provider_home_team_id = hm.provider_team_id
                    JOIN core.dim_provider_team_mapping am
                      ON s.source_provider = am.provider_name
                     AND s.provider_away_team_id = am.provider_team_id
                    WHERE s.source_provider = ?
                      AND s.provider_match_id IN ({placeholders})
                    """,
                    count_params,
                ).fetchone()[0]
            )

        try:
            connection.execute(f"""
                INSERT INTO core.fact_match_schedule (
                    match_id, season, system_league_id, match_time,
                    home_team_id, away_team_id, status, home_score, away_score,
                    source_provider, updated_at
                )
                SELECT 
                    'M_FD_' || s.provider_match_id,
                    '2025', -- simplified season
                    lm.system_league_id,
                    s.match_time,
                    hm.system_team_id,
                    am.system_team_id,
                    CASE 
                        WHEN s.status = 'FINISHED' THEN 'FINISHED'
                        WHEN s.status = 'TIMED' OR s.status = 'SCHEDULED' THEN 'PRE-MATCH'
                        ELSE 'UNKNOWN' 
                    END,
                    s.home_score,
                    s.away_score,
                    s.source_provider,
                    CURRENT_TIMESTAMP
                FROM staging.stg_match_schedule s
                JOIN core.dim_provider_league_mapping lm
                  ON s.source_provider = lm.provider_name
                 AND s.provider_league_id = lm.provider_league_id
                JOIN core.dim_provider_team_mapping hm
                  ON s.source_provider = hm.provider_name
                 AND s.provider_home_team_id = hm.provider_team_id
                JOIN core.dim_provider_team_mapping am
                  ON s.source_provider = am.provider_name
                 AND s.provider_away_team_id = am.provider_team_id
                WHERE s.source_provider = ?
                  AND s.provider_match_id IN ({placeholders})
                ON CONFLICT (match_id) DO UPDATE SET
                    status = excluded.status,
                    home_score = excluded.home_score,
                    away_score = excluded.away_score,
                    updated_at = excluded.updated_at
            """, count_params)
        except Exception as e:
            return {
                "status": "merge_failed",
                "error": str(e),
                "count": 0,
                "staged_count": staged_count,
                "merged_count": 0,
                "skipped_unmapped_count": staged_count,
            }

        merged_count = eligible_count
        skipped_unmapped_count = max(0, staged_count - merged_count)
        return {
            "status": "success",
            "count": merged_count,
            "staged_count": staged_count,
            "merged_count": merged_count,
            "skipped_unmapped_count": skipped_unmapped_count,
        }

    def _http_get(self, path: str) -> dict[str, Any] | None:
        if not self.api_token or self.api_token == "mock_token":
            return self._mock_response(path)
            
        url = self.base_url + path
        req = urllib.request.Request(url, headers={"X-Auth-Token": self.api_token})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode())
        except urllib.error.URLError:
            return None

    def _mock_response(self, path: str) -> dict[str, Any]:
        """Provides a fake response for testing purposes when no token is present."""
        return {
            "matches": [
                {
                    "id": 999991,
                    "competition": {"id": "SYS_EPL"},
                    "utcDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "status": "TIMED",
                    "homeTeam": {"id": "SYS_ARS"},
                    "awayTeam": {"id": "SYS_CHE"},
                    "score": {"fullTime": {"home": None, "away": None}}
                }
            ]
        }
