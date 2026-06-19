from __future__ import annotations

import html
import http.client
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from .match_resolver import CanonicalMatchResolver, MatchResolutionError


@dataclass(frozen=True)
class SportteryMatch:
    provider_match_id: str
    league_name: str
    kickoff_time: datetime
    home_team: str
    away_team: str
    spf_odds: dict[str, float] | None = None
    rqspf_handicap: float | None = None
    rqspf_odds: dict[str, float] | None = None
    crs_odds: dict[str, float] | None = None       # {"1:0": 7.5, "2:0": 10.0, ...}
    ttg_odds: dict[str, float] | None = None       # {"0": 10.5, "1": 4.4, "2": 3.25, ...}
    hafu_odds: dict[str, float] | None = None      # {"hh": 2.35, "hd": 18.0, ...}


class SportteryClient:
    """Client for official Sporttery football fixed-bonus odds pages."""

    PROVIDER_NAME = "SportteryOfficialWeb"
    MATCH_CALCULATOR_URL = (
        "https://webapi.sporttery.cn/gateway/uniform/football/"
        "getMatchCalculatorV1.qry?channel=c&poolCode=hhad,had,crs,ttg,hafu"
    )

    def __init__(self, base_url: str = "https://www.sporttery.cn") -> None:
        self.base_url = base_url.rstrip("/")

    def fetch_football_odds(
        self,
        connection: Any,
        target_date: date | None = None,
    ) -> dict[str, Any]:
        # 空响应重试: 0.5s -> 1.0s, 最多重试2次
        for attempt in range(3):
            if attempt > 0:
                delay = 0.5 * attempt
                time.sleep(delay)
            payload = self._http_get(self.MATCH_CALCULATOR_URL)
            if payload:
                break
        else:
            return _empty_result("empty_after_retries")

        matches = self._parse_payload(payload)
        if not matches:
            # 回退到 HTML 页面
            for attempt_html in range(3):
                if attempt_html > 0:
                    time.sleep(0.5 * attempt_html)
                payload = self._http_get("/jc/jsq/zqspf/")
                if payload:
                    break
            else:
                return _empty_result("empty_after_retries")
            matches = self._parse_payload(payload)

        if target_date is not None:
            matches = [m for m in matches if m.kickoff_time.date() == target_date]
        if not matches:
            return _empty_result("success")

        return self._sink_matches(connection, matches)

    def _parse_payload(self, payload: str) -> list[SportteryMatch]:
        stripped = payload.strip()
        if not stripped:
            return []
        if stripped.startswith("{") or stripped.startswith("["):
            return self._parse_json_payload(stripped)
        json_from_script = self._extract_application_json(stripped)
        if json_from_script:
            return self._parse_json_payload(json_from_script)
        return self._parse_html_data_rows(stripped)

    def _parse_json_payload(self, payload: str) -> list[SportteryMatch]:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return []
        sporttery_matches = self._parse_match_calculator_payload(parsed)
        if sporttery_matches:
            return sporttery_matches
        raw_matches = parsed.get("matches", []) if isinstance(parsed, dict) else parsed
        if not isinstance(raw_matches, list):
            return []

        matches: list[SportteryMatch] = []
        for item in raw_matches:
            if not isinstance(item, dict):
                continue
            match = self._match_from_mapping(item)
            if match is not None:
                matches.append(match)
        return matches

    def _parse_match_calculator_payload(self, parsed: Any) -> list[SportteryMatch]:
        if not isinstance(parsed, dict):
            return []
        value = parsed.get("value")
        if not isinstance(value, dict):
            return []
        groups = value.get("matchInfoList")
        if not isinstance(groups, list):
            return []

        matches: list[SportteryMatch] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            raw_matches = group.get("subMatchList")
            if not isinstance(raw_matches, list):
                continue
            for item in raw_matches:
                if not isinstance(item, dict):
                    continue
                match = self._match_from_mapping(_official_match_mapping(item))
                if match is not None:
                    matches.append(match)
        return matches

    def _parse_html_data_rows(self, payload: str) -> list[SportteryMatch]:
        matches: list[SportteryMatch] = []
        for row in re.findall(r"<tr\b[^>]*>", payload, flags=re.IGNORECASE):
            attrs = {
                name.replace("-", "_"): html.unescape(value)
                for name, value in re.findall(
                    r"\bdata-([a-zA-Z0-9_-]+)\s*=\s*\"([^\"]*)\"",
                    row,
                )
            }
            if not attrs:
                continue
            match = self._match_from_mapping(
                {
                    "match_id": attrs.get("match_id"),
                    "league": attrs.get("league"),
                    "kickoff_time": attrs.get("kickoff"),
                    "home_team": attrs.get("home"),
                    "away_team": attrs.get("away"),
                    "spf": {
                        "home": attrs.get("spf_home"),
                        "draw": attrs.get("spf_draw"),
                        "away": attrs.get("spf_away"),
                    },
                    "rqspf": {
                        "handicap": attrs.get("rq"),
                        "home": attrs.get("rqspf_home"),
                        "draw": attrs.get("rqspf_draw"),
                        "away": attrs.get("rqspf_away"),
                    },
                }
            )
            if match is not None:
                matches.append(match)
        return matches

    def _match_from_mapping(self, item: dict[str, Any]) -> SportteryMatch | None:
        provider_match_id = _string_value(
            item.get("match_id") or item.get("num") or item.get("id")
        )
        league_name = _string_value(item.get("league") or item.get("league_name"))
        home_team = _string_value(item.get("home_team") or item.get("home"))
        away_team = _string_value(item.get("away_team") or item.get("away"))
        kickoff_time = _parse_datetime(
            item.get("kickoff_time") or item.get("match_time") or item.get("kickoff")
        )
        spf_odds = _three_way_odds(item.get("spf") or item.get("odds"))
        rqspf = item.get("rqspf")
        rqspf_odds = _three_way_odds(rqspf)
        rqspf_handicap: float | None = None
        if isinstance(rqspf, dict):
            rqspf_handicap = _to_float(
                rqspf.get("handicap") or rqspf.get("line") or rqspf.get("rq")
            )
        crs_odds: dict[str, float] | None = None
        ttg_odds: dict[str, float] | None = None
        hafu_odds: dict[str, float] | None = None
        raw_crs = item.get("crs_odds")
        raw_ttg = item.get("ttg_odds")
        raw_hafu = item.get("hafu_odds")
        if isinstance(raw_crs, dict):
            crs_odds = {str(k): float(v) for k, v in raw_crs.items()}
        if isinstance(raw_ttg, dict):
            ttg_odds = {str(k): float(v) for k, v in raw_ttg.items()}
        if isinstance(raw_hafu, dict):
            hafu_odds = {str(k): float(v) for k, v in raw_hafu.items()}

        if (
            not provider_match_id
            or not league_name
            or not home_team
            or not away_team
            or kickoff_time is None
        ):
            return None

        return SportteryMatch(
            provider_match_id=provider_match_id,
            league_name=league_name,
            kickoff_time=kickoff_time,
            home_team=home_team,
            away_team=away_team,
            spf_odds=spf_odds,
            rqspf_handicap=rqspf_handicap,
            rqspf_odds=rqspf_odds,
            crs_odds=crs_odds,
            ttg_odds=ttg_odds,
            hafu_odds=hafu_odds,
        )

    def _extract_application_json(self, payload: str) -> str | None:
        match = re.search(
            r"<script[^>]+type=[\"']application/json[\"'][^>]*>(.*?)</script>",
            payload,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return None
        return html.unescape(match.group(1)).strip()

    def _sink_matches(
        self,
        connection: Any,
        matches: list[SportteryMatch],
    ) -> dict[str, Any]:
        schedule_rows = [
            (
                match.provider_match_id,
                match.league_name,
                match.home_team,
                match.away_team,
                _as_utc_naive(match.kickoff_time),
                "PRE-MATCH",
                self.PROVIDER_NAME,
            )
            for match in matches
        ]
        odds_rows: list[tuple[Any, ...]] = []
        detail_rows: list[tuple[Any, ...]] = []
        market_status_rows: list[tuple[Any, ...]] = []
        snapshot_time = datetime.now(timezone.utc).replace(tzinfo=None)
        for match in matches:
            market_status_rows.extend(
                (
                    match.provider_match_id,
                    market_type,
                    "OPEN" if odds else "NOT_ON_SALE",
                    snapshot_time,
                    self.PROVIDER_NAME,
                )
                for market_type, odds in (
                    ("SPF", match.spf_odds),
                    ("RQSPF", match.rqspf_odds),
                    ("CRS", match.crs_odds),
                    ("TTG", match.ttg_odds),
                    ("HAFU", match.hafu_odds),
                )
            )
            if match.spf_odds is not None:
                odds_rows.append(
                    (
                        match.provider_match_id,
                        "1X2",
                        self.PROVIDER_NAME,
                        snapshot_time,
                        None,
                        match.spf_odds["home"],
                        match.spf_odds["draw"],
                        match.spf_odds["away"],
                        self.PROVIDER_NAME,
                    )
                )
            if match.rqspf_odds is not None:
                odds_rows.append(
                    (
                        match.provider_match_id,
                        "SPORTTERY_RQSPF",
                        self.PROVIDER_NAME,
                        snapshot_time,
                        match.rqspf_handicap,
                        match.rqspf_odds["home"],
                        match.rqspf_odds["draw"],
                        match.rqspf_odds["away"],
                        self.PROVIDER_NAME,
                    )
                )
            # 比分: 每对 {score: odds} 存一行
            if match.crs_odds:
                for score, odds_val in match.crs_odds.items():
                    detail_rows.append(
                        (
                            match.provider_match_id,
                            "CORRECT_SCORE",
                            score,
                            odds_val,
                            snapshot_time,
                            self.PROVIDER_NAME,
                        )
                    )
            # 总进球: 每对 {goals: odds} 存一行
            if match.ttg_odds:
                for goals, odds_val in match.ttg_odds.items():
                    detail_rows.append(
                        (
                            match.provider_match_id,
                            "TOTAL_GOALS",
                            goals,
                            odds_val,
                            snapshot_time,
                            self.PROVIDER_NAME,
                        )
                    )
            # 半全场: 每对 {label: odds} 存一行
            if match.hafu_odds:
                for label, odds_val in match.hafu_odds.items():
                    detail_rows.append(
                        (
                            match.provider_match_id,
                            "HALF_FULL",
                            label,
                            odds_val,
                            snapshot_time,
                            self.PROVIDER_NAME,
                        )
                    )

        connection.executemany(
            """
            INSERT INTO staging.stg_match_schedule (
                provider_match_id,
                provider_league_id,
                provider_home_team_id,
                provider_away_team_id,
                match_time,
                status,
                source_provider
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            schedule_rows,
        )
        if odds_rows:
            connection.executemany(
                """
                INSERT INTO staging.stg_odds (
                    provider_match_id,
                    odds_type,
                    bookmaker_name,
                    snapshot_time,
                    handicap_line,
                    home_odds,
                    draw_odds,
                    away_odds,
                    source_provider
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                odds_rows,
            )
        if detail_rows:
            connection.executemany(
                """
                INSERT INTO staging.stg_sporttery_odds_detail (
                    provider_match_id,
                    odds_type,
                    score_key,
                    odds_value,
                    snapshot_time,
                    source_provider
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                detail_rows,
            )

        provider_match_ids = [m.provider_match_id for m in matches]
        mapping_errors = self._resolve_matches(connection, matches)
        self._merge_schedule(connection, provider_match_ids)
        self._merge_odds(connection, provider_match_ids)
        self._merge_detail_odds(connection, provider_match_ids)
        self._merge_market_status(connection, market_status_rows)
        return {
            "status": "success",
            "match_count": len(matches),
            "odds_count": len(odds_rows),
            "detail_count": len(detail_rows),
            "merged_match_count": self._count_core_matches(connection, provider_match_ids),
            "merged_odds_count": self._count_core_odds(connection, provider_match_ids),
            "skipped_unmapped_count": max(
                0,
                len(matches) - self._count_core_matches(connection, provider_match_ids),
            ),
            "rqspf_odds_count": sum(1 for m in matches if m.rqspf_odds is not None),
            "spf_odds_count": sum(1 for m in matches if m.spf_odds is not None),
            "correct_score_count": sum(1 for m in matches if m.crs_odds),
            "total_goals_count": sum(1 for m in matches if m.ttg_odds),
            "half_full_count": sum(1 for m in matches if m.hafu_odds),
            "market_status_count": len(market_status_rows),
            "mapping_error_count": len(mapping_errors),
            "mapping_errors": mapping_errors,
        }

    def _resolve_matches(
        self,
        connection: Any,
        matches: list[SportteryMatch],
    ) -> list[dict[str, str]]:
        resolver = CanonicalMatchResolver(connection)
        errors: list[dict[str, str]] = []
        for match in matches:
            try:
                resolver.resolve(
                    provider_name=self.PROVIDER_NAME,
                    provider_match_id=match.provider_match_id,
                    provider_league_id=match.league_name,
                    provider_home_team_id=match.home_team,
                    provider_away_team_id=match.away_team,
                    kickoff_time=match.kickoff_time,
                )
            except MatchResolutionError as exc:
                errors.append(
                    {
                        "provider_match_id": match.provider_match_id,
                        "error": str(exc),
                    }
                )
        return errors

    def _merge_schedule(self, connection: Any, provider_match_ids: list[str]) -> None:
        if not provider_match_ids:
            return
        placeholders = ",".join("?" for _ in provider_match_ids)
        connection.execute(
            f"""
            INSERT INTO core.fact_match_schedule (
                match_id,
                season,
                system_league_id,
                match_time,
                home_team_id,
                away_team_id,
                status,
                source_provider,
                updated_at,
                etl_insert_timestamp
            )
            SELECT
                m.system_match_id,
                strftime(s.match_time, '%Y'),
                COALESCE(lm.system_league_id, s.provider_league_id),
                s.match_time,
                COALESCE(hm.system_team_id, s.provider_home_team_id),
                COALESCE(am.system_team_id, s.provider_away_team_id),
                COALESCE(s.status, 'PRE-MATCH'),
                s.source_provider,
                CAST(CURRENT_TIMESTAMP AS TIMESTAMP),
                CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
            FROM staging.stg_match_schedule s
            INNER JOIN core.dim_provider_match_mapping m
                ON s.source_provider = m.provider_name
               AND s.provider_match_id = m.provider_match_id
            LEFT JOIN core.dim_provider_league_mapping lm
              ON s.source_provider = lm.provider_name
             AND s.provider_league_id = lm.provider_league_id
            LEFT JOIN core.dim_provider_team_mapping hm
              ON s.source_provider = hm.provider_name
             AND s.provider_home_team_id = hm.provider_team_id
            LEFT JOIN core.dim_provider_team_mapping am
              ON s.source_provider = am.provider_name
             AND s.provider_away_team_id = am.provider_team_id
            WHERE s.source_provider = ?
              AND s.provider_match_id IN ({placeholders})
            ON CONFLICT (match_id)
            DO UPDATE SET
                season = excluded.season,
                system_league_id = excluded.system_league_id,
                match_time = excluded.match_time,
                home_team_id = excluded.home_team_id,
                away_team_id = excluded.away_team_id,
                status = excluded.status,
                source_provider = excluded.source_provider,
                updated_at = excluded.updated_at,
                etl_insert_timestamp = excluded.etl_insert_timestamp
            """,
            [self.PROVIDER_NAME, *provider_match_ids],
        )

    def _merge_odds(self, connection: Any, provider_match_ids: list[str]) -> None:
        if not provider_match_ids:
            return
        placeholders = ",".join("?" for _ in provider_match_ids)
        connection.execute(
            f"""
            INSERT INTO core.fact_odds_capital_flow (
                match_id,
                snapshot_time,
                odds_type,
                bookmaker_name,
                handicap_line,
                home_odds,
                draw_odds,
                away_odds,
                source_provider
            )
            SELECT
                m.system_match_id,
                o.snapshot_time,
                o.odds_type,
                o.bookmaker_name,
                o.handicap_line,
                o.home_odds,
                o.draw_odds,
                o.away_odds,
                o.source_provider
            FROM staging.stg_odds o
            INNER JOIN core.dim_provider_match_mapping m
                ON o.source_provider = m.provider_name
               AND o.provider_match_id = m.provider_match_id
            WHERE o.source_provider = ?
              AND o.provider_match_id IN ({placeholders})
            ON CONFLICT (match_id, snapshot_time, odds_type, bookmaker_name)
            DO UPDATE SET
                handicap_line = excluded.handicap_line,
                home_odds = excluded.home_odds,
                draw_odds = excluded.draw_odds,
                away_odds = excluded.away_odds,
                source_provider = excluded.source_provider
            """,
            [self.PROVIDER_NAME, *provider_match_ids],
        )

    def _merge_detail_odds(self, connection: Any, provider_match_ids: list[str]) -> None:
        if not provider_match_ids:
            return
        placeholders = ",".join("?" for _ in provider_match_ids)
        connection.execute(
            f"""
            INSERT INTO core.fact_sporttery_odds_detail (
                match_id,
                odds_type,
                outcome_key,
                odds_value,
                snapshot_time,
                source_provider
            )
            SELECT
                m.system_match_id,
                d.odds_type,
                d.score_key,
                d.odds_value,
                d.snapshot_time,
                d.source_provider
            FROM staging.stg_sporttery_odds_detail d
            INNER JOIN core.dim_provider_match_mapping m
                ON d.source_provider = m.provider_name
               AND d.provider_match_id = m.provider_match_id
            WHERE d.source_provider = ?
              AND d.provider_match_id IN ({placeholders})
            ON CONFLICT (match_id, odds_type, outcome_key, snapshot_time) DO NOTHING
            """,
            [self.PROVIDER_NAME, *provider_match_ids],
        )

    def _merge_market_status(
        self,
        connection: Any,
        market_status_rows: list[tuple[Any, ...]],
    ) -> None:
        if not market_status_rows:
            return
        connection.executemany(
            """
            INSERT INTO core.fact_sporttery_market_status (
                match_id,
                market_type,
                sale_status,
                snapshot_time,
                source_provider
            )
            SELECT
                mapping.system_match_id,
                ?,
                ?,
                ?,
                ?
            FROM core.dim_provider_match_mapping mapping
            WHERE mapping.provider_name = ?
              AND mapping.provider_match_id = ?
            ON CONFLICT (match_id, market_type, snapshot_time)
            DO UPDATE SET
                sale_status = excluded.sale_status,
                source_provider = excluded.source_provider
            """,
            [
                (market_type, sale_status, snapshot_time, source_provider, source_provider, provider_match_id)
                for (
                    provider_match_id,
                    market_type,
                    sale_status,
                    snapshot_time,
                    source_provider,
                ) in market_status_rows
            ],
        )

    def _count_core_matches(self, connection: Any, provider_match_ids: list[str]) -> int:
        if not provider_match_ids:
            return 0
        placeholders = ",".join("?" for _ in provider_match_ids)
        return int(
            connection.execute(
                f"""
                SELECT COUNT(DISTINCT m.system_match_id)
                FROM core.dim_provider_match_mapping m
                WHERE m.provider_name = ?
                  AND m.provider_match_id IN ({placeholders})
                """,
                [self.PROVIDER_NAME, *provider_match_ids],
            ).fetchone()[0]
        )

    def _count_core_odds(self, connection: Any, provider_match_ids: list[str]) -> int:
        if not provider_match_ids:
            return 0
        placeholders = ",".join("?" for _ in provider_match_ids)
        return int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM core.fact_odds_capital_flow o
                INNER JOIN core.dim_provider_match_mapping m
                    ON o.match_id = m.system_match_id
                WHERE m.provider_name = ?
                  AND m.provider_match_id IN ({placeholders})
                """,
                [self.PROVIDER_NAME, *provider_match_ids],
            ).fetchone()[0]
        )

    def _http_get(self, path: str) -> str | None:
        url = path if path.startswith("http") else self.base_url + path
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0 Safari/537.36"
                ),
                "Accept": "application/json,text/plain,*/*",
                "Referer": "https://www.sporttery.cn/jc/jsq/zqspf/",
                "Origin": "https://www.sporttery.cn",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, http.client.RemoteDisconnected):
            return None


def _empty_result(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "match_count": 0,
        "odds_count": 0,
        "detail_count": 0,
        "merged_match_count": 0,
        "merged_odds_count": 0,
        "skipped_unmapped_count": 0,
        "rqspf_odds_count": 0,
        "spf_odds_count": 0,
        "correct_score_count": 0,
        "total_goals_count": 0,
        "half_full_count": 0,
    }


def _official_match_mapping(item: dict[str, Any]) -> dict[str, Any]:
    match_date = _string_value(item.get("matchDate"))
    match_time = _string_value(item.get("matchTime"))
    kickoff_time = f"{match_date}T{match_time}" if match_date and match_time else ""
    had = item.get("had") if isinstance(item.get("had"), dict) else {}
    hhad = item.get("hhad") if isinstance(item.get("hhad"), dict) else {}
    crs_raw = item.get("crs") if isinstance(item.get("crs"), dict) else {}
    ttg_raw = item.get("ttg") if isinstance(item.get("ttg"), dict) else {}
    hafu_raw = item.get("hafu") if isinstance(item.get("hafu"), dict) else {}

    result: dict[str, Any] = {
        "match_id": item.get("matchId") or item.get("matchNumStr"),
        "league": item.get("leagueAbbName") or item.get("leagueAllName"),
        "kickoff_time": kickoff_time,
        "home_team": item.get("homeTeamAbbName") or item.get("homeTeamAllName"),
        "away_team": item.get("awayTeamAbbName") or item.get("awayTeamAllName"),
    }
    if had:
        result["spf"] = {
            "home": had.get("h"),
            "draw": had.get("d"),
            "away": had.get("a"),
        }
    if hhad:
        result["rqspf"] = {
            "handicap": hhad.get("goalLineValue") or hhad.get("goalLine"),
            "home": hhad.get("h"),
            "draw": hhad.get("d"),
            "away": hhad.get("a"),
        }

    # 比分 crs: 键名 sXXsXX → "1:0", sXXsXXf → 资金流向(忽略)
    if crs_raw:
        crs_odds: dict[str, float] = {}
        for key, val in crs_raw.items():
            if key.endswith("f") or key in ("goalLine", "goalLineValue", "updateDate", "updateTime"):
                continue
            # s01s02 → "1:2"
            score = _parse_crs_key(key)
            odds_val = _to_float(val)
            if score and odds_val is not None:
                crs_odds[score] = odds_val
        if crs_odds:
            result["crs_odds"] = crs_odds

    # 总进球 ttg: 键名 s0-s7 → "0"-"7+"
    if ttg_raw:
        ttg_odds: dict[str, float] = {}
        for key, val in ttg_raw.items():
            if key.endswith("f") or key in ("goalLine", "goalLineValue", "updateDate", "updateTime"):
                continue
            # s0 → "0", s7 → "7+"
            goals = _parse_ttg_key(key)
            odds_val = _to_float(val)
            if goals is not None and odds_val is not None:
                ttg_odds[goals] = odds_val
        if ttg_odds:
            result["ttg_odds"] = ttg_odds

    # 半全场 hafu: 键名 hh→胜胜, hd→胜平, ha→胜负, dh→平胜, dd→平平, da→平负, ah→负胜, ad→负平, aa→负负
    if hafu_raw:
        hafu_odds: dict[str, float] = {}
        for key, val in hafu_raw.items():
            if key.endswith("f") or key.lower() in {
                "goalline",
                "goallinevalue",
                "id",
                "updatedate",
                "updatetime",
            }:
                continue
            label = _parse_hafu_key(key)
            odds_val = _to_float(val)
            if label and odds_val is not None:
                hafu_odds[label] = odds_val
        if hafu_odds:
            result["hafu_odds"] = hafu_odds

    return result


def _parse_crs_key(key: str) -> str | None:
    """s01s02 → '1:2', s1sh → '胜其它', s1sd → '平其它', s1sa → '负其它'"""
    # 特殊: 胜其它/平其它/负其它 — 格式固定，直接匹配
    if key == "s1sh":
        return "胜其它"
    if key == "s1sd":
        return "平其它"
    if key == "s1sa":
        return "负其它"
    # 常规比分 sXXsYY
    match = _CRS_KEY_RE.match(key)
    if not match:
        return None
    home = match.group(1)
    away = match.group(2)
    home_display = str(int(home))
    away_display = str(int(away))
    return f"{home_display}:{away_display}"


def _parse_ttg_key(key: str) -> str | None:
    """s0 → '0', s7 → '7+'"""
    if key == "s7":
        return "7+"
    if key.startswith("s") and key[1:].isdigit():
        n = int(key[1:])
        if 0 <= n <= 6:
            return str(n)
    return None


def _parse_hafu_key(key: str) -> str | None:
    """hh → '胜胜', hd → '胜平', ..."""
    mapping = {
        "hh": "胜胜", "hd": "胜平", "ha": "胜负",
        "dh": "平胜", "dd": "平平", "da": "平负",
        "ah": "负胜", "ad": "负平", "aa": "负负",
    }
    # 也支持带 f 后缀（资金流向）
    clean = key.removesuffix("f")
    return mapping.get(clean)


_CRS_KEY_RE = __import__("re").compile(r"^s(\d+|[1]s[ahd])s(\d+|s[ahd])$")


def _three_way_odds(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    home = _to_float(
        value.get("home") or value.get("win") or value.get("h") or value.get("胜")
    )
    draw = _to_float(value.get("draw") or value.get("d") or value.get("平"))
    away = _to_float(value.get("away") or value.get("loss") or value.get("a") or value.get("负"))
    if home is None or draw is None or away is None:
        return None
    if home <= 1.0 or draw <= 1.0 or away <= 1.0:
        return None
    return {"home": home, "draw": draw, "away": away}


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
