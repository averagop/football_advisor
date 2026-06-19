from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any


CAPITAL_FLOW_FIELD_NAMES = {
    "matched_volume",
    "matchedvolume",
    "matchedVolume",
    "total_matched",
    "totalmatched",
    "totalMatched",
    "traded_volume",
    "tradedvolume",
    "tradedVolume",
    "volume",
    "liquidity",
}


class ThirdPartyOddsClient:
    THE_ODDS_PROVIDER = "TheOddsAPI"
    RAPID_PROVIDER = "RapidAPI-OddsFeed"

    def __init__(
        self,
        the_odds_api_token: str | None = None,
        the_odds_api_base_url: str = "https://api.the-odds-api.com/v4",
        the_odds_api_sport_keys: tuple[str, ...] = ("soccer_international_friendlies",),
        rapidapi_token: str | None = None,
        odds_feed_rapid_host: str | None = None,
        odds_feed_rapid_base_url: str | None = None,
        timeout_seconds: int = 15,
    ) -> None:
        self.the_odds_api_token = the_odds_api_token
        self.the_odds_api_base_url = the_odds_api_base_url.rstrip("/")
        self.the_odds_api_sport_keys = the_odds_api_sport_keys
        self.rapidapi_token = rapidapi_token
        self.odds_feed_rapid_host = odds_feed_rapid_host
        self.odds_feed_rapid_base_url = (
            odds_feed_rapid_base_url.rstrip("/") if odds_feed_rapid_base_url else None
        )
        self.timeout_seconds = timeout_seconds

    def fetch_the_odds_api_odds(
        self,
        connection: Any,
        home_team: str | None,
        away_team: str | None,
        kickoff_time: datetime | None,
    ) -> dict[str, Any]:
        if not self.the_odds_api_token:
            return _skipped("skipped_no_token")

        all_events: list[dict[str, Any]] = []
        for sport_key in self._candidate_the_odds_sport_keys():
            try:
                payload = self._http_json(self._the_odds_api_url(sport_key))
            except Exception:
                continue
            if isinstance(payload, list):
                all_events.extend(item for item in payload if isinstance(item, dict))
            if _has_matching_event(all_events, home_team, away_team, kickoff_time):
                break

        return self.process_the_odds_api_payload(
            connection,
            all_events,
            home_team,
            away_team,
            kickoff_time,
        )

    def fetch_rapidapi_odds_feed(
        self,
        connection: Any,
        home_team: str | None,
        away_team: str | None,
        kickoff_time: datetime | None,
    ) -> dict[str, Any]:
        if not self.rapidapi_token:
            return _skipped("skipped_no_token")
        if not self.odds_feed_rapid_host or not self.odds_feed_rapid_base_url:
            return _skipped("skipped_no_endpoint")

        payload = self._http_json(
            self._rapidapi_url(home_team, away_team, kickoff_time),
            headers={
                "x-rapidapi-key": self.rapidapi_token,
                "x-rapidapi-host": self.odds_feed_rapid_host,
            },
        )
        return self.process_rapidapi_payload(
            connection,
            payload,
            home_team,
            away_team,
            kickoff_time,
        )

    def process_the_odds_api_payload(
        self,
        connection: Any,
        payload: Any,
        home_team: str | None,
        away_team: str | None,
        kickoff_time: datetime | None,
    ) -> dict[str, Any]:
        events = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
        return self._process_events(
            connection,
            events,
            home_team,
            away_team,
            kickoff_time,
            self.THE_ODDS_PROVIDER,
        )

    def process_rapidapi_payload(
        self,
        connection: Any,
        payload: Any,
        home_team: str | None,
        away_team: str | None,
        kickoff_time: datetime | None,
    ) -> dict[str, Any]:
        events = _extract_event_dicts(payload)
        return self._process_events(
            connection,
            events,
            home_team,
            away_team,
            kickoff_time,
            self.RAPID_PROVIDER,
        )

    def _process_events(
        self,
        connection: Any,
        events: list[dict[str, Any]],
        home_team: str | None,
        away_team: str | None,
        kickoff_time: datetime | None,
        source_provider: str,
    ) -> dict[str, Any]:
        capital_flow_fields = sorted(_capital_flow_fields(events))
        matched_events = [
            event
            for event in events
            if _event_matches(event, home_team, away_team, kickoff_time)
        ]
        odds_rows: list[tuple[Any, ...]] = []
        merged_count = 0

        for event in matched_events:
            event_id = str(event.get("id") or event.get("event_id") or "")
            event_time = _parse_datetime(event.get("commence_time") or event.get("start_time"))
            snapshot_time = datetime.now(timezone.utc)
            matched_volume = _event_matched_volume(event)
            for bookmaker in _bookmakers(event):
                odds = _extract_h2h_odds(bookmaker, event)
                if not odds:
                    continue
                bookmaker_name = str(
                    bookmaker.get("title")
                    or bookmaker.get("name")
                    or bookmaker.get("key")
                    or source_provider
                )
                odds_rows.append(
                    (
                        event_id,
                        "1X2",
                        bookmaker_name,
                        snapshot_time,
                        None,
                        odds["home"],
                        odds["draw"],
                        odds["away"],
                        source_provider,
                    )
                )
                match_id = _find_internal_match_id(
                    connection, home_team, away_team, event_time or kickoff_time
                )
                if match_id:
                    _insert_core_odds(
                        connection,
                        match_id,
                        snapshot_time,
                        "1X2",
                        bookmaker_name,
                        odds["home"],
                        odds["draw"],
                        odds["away"],
                        source_provider,
                        matched_volume,
                    )
                    merged_count += 1

        _insert_staging_odds(connection, odds_rows)
        return {
            "status": "success" if odds_rows else "no_matching_odds",
            "event_count": len(events),
            "matched_event_count": len(matched_events),
            "odds_count": len(odds_rows),
            "merged_odds_count": merged_count,
            "capital_flow_fields": capital_flow_fields,
        }

    def _the_odds_api_url(self, sport_key: str) -> str:
        params = urllib.parse.urlencode(
            {
                "apiKey": self.the_odds_api_token or "",
                "regions": "us,uk,eu",
                "markets": "h2h",
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            }
        )
        return f"{self.the_odds_api_base_url}/sports/{sport_key}/odds?{params}"

    def _candidate_the_odds_sport_keys(self) -> tuple[str, ...]:
        configured = tuple(dict.fromkeys(self.the_odds_api_sport_keys))
        discovered: list[str] = []
        try:
            payload = self._http_json(
                f"{self.the_odds_api_base_url}/sports?"
                + urllib.parse.urlencode({"apiKey": self.the_odds_api_token or ""})
            )
        except Exception:
            payload = []
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key") or "")
                if key.startswith("soccer_") and key not in configured:
                    discovered.append(key)
        return (*configured, *discovered[:3])

    def _rapidapi_url(
        self,
        home_team: str | None,
        away_team: str | None,
        kickoff_time: datetime | None,
    ) -> str:
        params = {
            "home": home_team or "",
            "away": away_team or "",
        }
        if kickoff_time:
            params["date"] = kickoff_time.astimezone(timezone.utc).date().isoformat()
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v})
        separator = "&" if "?" in (self.odds_feed_rapid_base_url or "") else "?"
        return f"{self.odds_feed_rapid_base_url}{separator}{query}" if query else str(self.odds_feed_rapid_base_url)

    def _http_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        request = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


def _skipped(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "event_count": 0,
        "matched_event_count": 0,
        "odds_count": 0,
        "merged_odds_count": 0,
        "capital_flow_fields": [],
    }


def _extract_event_dicts(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("events", "data", "response", "results", "matches"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return [payload] if _bookmakers(payload) else []


def _event_matches(
    event: dict[str, Any],
    home_team: str | None,
    away_team: str | None,
    kickoff_time: datetime | None,
) -> bool:
    event_home = str(event.get("home_team") or event.get("home") or "").strip()
    event_away = str(event.get("away_team") or event.get("away") or "").strip()
    if home_team and event_home and event_home.casefold() != home_team.strip().casefold():
        return False
    if away_team and event_away and event_away.casefold() != away_team.strip().casefold():
        return False
    if kickoff_time:
        event_time = _parse_datetime(event.get("commence_time") or event.get("start_time"))
        if event_time and abs((event_time - kickoff_time.astimezone(timezone.utc)).total_seconds()) > 48 * 3600:
            return False
    return True


def _has_matching_event(
    events: list[dict[str, Any]],
    home_team: str | None,
    away_team: str | None,
    kickoff_time: datetime | None,
) -> bool:
    return any(_event_matches(event, home_team, away_team, kickoff_time) for event in events)


def _bookmakers(event: dict[str, Any]) -> list[dict[str, Any]]:
    value = event.get("bookmakers") or event.get("books") or event.get("odds")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _extract_h2h_odds(
    bookmaker: dict[str, Any], event: dict[str, Any]
) -> dict[str, float] | None:
    markets = bookmaker.get("markets")
    if isinstance(markets, list):
        for market in markets:
            if isinstance(market, dict) and str(market.get("key", "")).lower() in {"h2h", "match_odds", "1x2"}:
                odds = _odds_from_outcomes(
                    market.get("outcomes"),
                    str(event.get("home_team") or event.get("home") or ""),
                    str(event.get("away_team") or event.get("away") or ""),
                )
                if odds:
                    return odds
    return _odds_from_mapping(bookmaker)


def _odds_from_outcomes(
    outcomes: Any, home_team: str, away_team: str
) -> dict[str, float] | None:
    if not isinstance(outcomes, list):
        return None
    result: dict[str, float] = {}
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        name = str(outcome.get("name") or "").strip()
        price = _float_or_none(outcome.get("price") or outcome.get("odds"))
        if price is None:
            continue
        if name.casefold() == home_team.casefold():
            result["home"] = price
        elif name.casefold() == away_team.casefold():
            result["away"] = price
        elif name.casefold() in {"draw", "tie", "x"}:
            result["draw"] = price
    return result if {"home", "draw", "away"}.issubset(result) else None


def _odds_from_mapping(bookmaker: dict[str, Any]) -> dict[str, float] | None:
    mapping = {
        "home": _first_float(bookmaker, ("home_odds", "homeOdds", "home", "1")),
        "draw": _first_float(bookmaker, ("draw_odds", "drawOdds", "draw", "x")),
        "away": _first_float(bookmaker, ("away_odds", "awayOdds", "away", "2")),
    }
    return mapping if all(value is not None for value in mapping.values()) else None


def _first_float(source: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _float_or_none(source.get(key))
        if value is not None:
            return value
    return None


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 1.0 else None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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


def _event_matched_volume(event: dict[str, Any]) -> float | None:
    for key in CAPITAL_FLOW_FIELD_NAMES:
        value = _float_or_none(event.get(key))
        if value is not None:
            return value
    return None


def _find_internal_match_id(
    connection: Any,
    home_team: str | None,
    away_team: str | None,
    kickoff_time: datetime | None,
) -> str | None:
    if not home_team or not away_team or not kickoff_time:
        return None
    row = connection.execute(
        """
        SELECT s.match_id
        FROM core.fact_match_schedule s
        JOIN core.dim_team_mapping h ON s.home_team_id = h.system_team_id
        JOIN core.dim_team_mapping a ON s.away_team_id = a.system_team_id
        WHERE lower(h.team_standard_name) = lower(?)
          AND lower(a.team_standard_name) = lower(?)
          AND abs(date_diff('minute', s.match_time, ?)) <= 720
        ORDER BY abs(date_diff('minute', s.match_time, ?)) ASC
        LIMIT 1
        """,
        [home_team, away_team, kickoff_time, kickoff_time],
    ).fetchone()
    return str(row[0]) if row else None


def _insert_staging_odds(connection: Any, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    connection.executemany(
        """
        INSERT INTO staging.stg_odds (
            provider_match_id, odds_type, bookmaker_name, snapshot_time,
            handicap_line, home_odds, draw_odds, away_odds, source_provider
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _insert_core_odds(
    connection: Any,
    match_id: str,
    snapshot_time: datetime,
    odds_type: str,
    bookmaker_name: str,
    home_odds: float,
    draw_odds: float,
    away_odds: float,
    source_provider: str,
    matched_volume: float | None,
) -> None:
    connection.execute(
        """
        INSERT INTO core.fact_odds_capital_flow (
            match_id, snapshot_time, odds_type, bookmaker_name,
            home_odds, draw_odds, away_odds, matched_volume, source_provider
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (match_id, snapshot_time, odds_type, bookmaker_name)
        DO UPDATE SET
            home_odds = excluded.home_odds,
            draw_odds = excluded.draw_odds,
            away_odds = excluded.away_odds,
            matched_volume = excluded.matched_volume,
            source_provider = excluded.source_provider
        """,
        [
            match_id,
            snapshot_time,
            odds_type,
            bookmaker_name,
            home_odds,
            draw_odds,
            away_odds,
            matched_volume,
            source_provider,
        ],
    )
