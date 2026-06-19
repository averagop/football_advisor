from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

import urllib.error
import urllib.request


class ApiFootballClient:
    """Client for API-Football structured market and team data."""

    PROVIDER_NAME = "API-Football"

    def __init__(
        self,
        api_token: str | None = None,
        base_url: str = "https://v3.football.api-sports.io",
    ):
        self.api_token = api_token
        self.base_url = base_url.rstrip("/")

    def fetch_match_data(
        self,
        connection: Any,
        kickoff_time: datetime,
        home_team: str | None,
        away_team: str | None,
    ) -> dict[str, Any]:
        """Fetch one match's structured odds and stats into staging and core."""
        raw_data = self._http_get(f"/fixtures?date={kickoff_time.strftime('%Y-%m-%d')}")

        if not raw_data or "response" not in raw_data:
            return _empty_result("failed_or_empty")

        fixtures = raw_data["response"]
        if not fixtures:
            return _empty_result("success")

        if self._uses_mock_response():
            return self._process_mocked_response(connection, fixtures)

        fixture = self._select_fixture(fixtures, home_team, away_team)
        if fixture is None:
            return _empty_result("match_not_found")

        return self._process_real_fixture_response(connection, fixture)

    def fetch_recent_friendlies(
        self,
        connection: Any,
        target_match_time: datetime,
        home_team: str | None,
        away_team: str | None,
        date_from: date,
        date_to: date,
    ) -> dict[str, Any]:
        """Fetch finished senior national-team friendlies as recent form."""
        target_match_id = self._find_target_match_id(
            connection,
            target_match_time,
            home_team,
            away_team,
        )
        if not target_match_id:
            return _empty_recent_friendlies_result("target_match_not_found")

        team_mapping = self._load_target_provider_team_mapping(
            connection,
            home_team,
            away_team,
        )
        if not team_mapping:
            return _empty_recent_friendlies_result("target_team_mapping_not_found")

        rows: list[tuple[Any, ...]] = []
        recent_results: dict[str, dict[str, Any]] = {}
        friendly_count = 0
        skipped_unmapped_count = 0
        current_date = date_from
        while current_date <= date_to:
            payload = self._http_get(f"/fixtures?date={current_date.isoformat()}") or {}
            for fixture in payload.get("response", []):
                if not _is_finished_world_friendly(fixture):
                    continue
                goals = fixture.get("goals", {})
                home_goals = _to_float(goals.get("home"))
                away_goals = _to_float(goals.get("away"))
                if home_goals is None or away_goals is None:
                    continue
                friendly_count += 1
                fixture_time = _parse_fixture_datetime(
                    fixture.get("fixture", {}).get("date")
                )
                teams = fixture.get("teams", {})
                for side, goals_for, goals_against in (
                    ("home", home_goals, away_goals),
                    ("away", away_goals, home_goals),
                ):
                    provider_team_id = str(teams.get(side, {}).get("id", ""))
                    system_team_id = team_mapping.get(provider_team_id)
                    if not system_team_id:
                        skipped_unmapped_count += 1
                        continue
                    _record_recent_result(
                        recent_results,
                        system_team_id,
                        goals_for,
                        goals_against,
                    )
                    rows.append(
                        (
                            target_match_id,
                            system_team_id,
                            fixture_time,
                            self.PROVIDER_NAME,
                            goals_for,
                            goals_against,
                            _points_for(goals_for, goals_against),
                            _clamp(goals_for / 1.4, 0.65, 1.55),
                            _clamp(goals_against / 1.1, 0.65, 1.55),
                            0.5,
                            0.0,
                            "API_FOOTBALL_RECENT_FRIENDLY",
                        )
                    )
            current_date += timedelta(days=1)

        if rows:
            connection.executemany(
                """
                INSERT INTO core.fact_team_rolling_stats (
                    match_id,
                    team_id,
                    record_date,
                    source_provider,
                    rolling_goals_for,
                    rolling_goals_against,
                    recent_points_per_match,
                    attack_strength,
                    defense_strength,
                    motivation_coefficient,
                    key_missing_weight,
                    data_quality_flag
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (match_id, team_id, record_date, source_provider)
                DO UPDATE SET
                    rolling_goals_for = excluded.rolling_goals_for,
                    rolling_goals_against = excluded.rolling_goals_against,
                    recent_points_per_match = excluded.recent_points_per_match,
                    attack_strength = excluded.attack_strength,
                    defense_strength = excluded.defense_strength,
                    motivation_coefficient = excluded.motivation_coefficient,
                    key_missing_weight = excluded.key_missing_weight,
                    data_quality_flag = excluded.data_quality_flag
                """,
                rows,
            )
            self._upsert_recent_context(connection, target_match_id, recent_results)

        return {
            "status": "success",
            "friendly_count": friendly_count,
            "mapped_team_stats_count": len(rows),
            "skipped_unmapped_count": skipped_unmapped_count,
        }

    def _upsert_recent_context(
        self,
        connection: Any,
        target_match_id: str,
        recent_results: dict[str, dict[str, Any]],
    ) -> None:
        if not recent_results:
            return
        row = connection.execute(
            """
            SELECT home_team_id, away_team_id
            FROM core.fact_match_schedule
            WHERE match_id = ?
            """,
            [target_match_id],
        ).fetchone()
        if not row:
            return
        home_team_id, away_team_id = str(row[0]), str(row[1])
        home = recent_results.get(home_team_id, {})
        away = recent_results.get(away_team_id, {})
        connection.execute(
            """
            INSERT INTO core.fact_match_context_summary (
                match_id,
                home_recent_wins,
                home_recent_draws,
                home_recent_losses,
                away_recent_wins,
                away_recent_draws,
                away_recent_losses,
                home_home_points_per_match,
                away_away_points_per_match,
                source_provider,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CAST(CURRENT_TIMESTAMP AS TIMESTAMP))
            ON CONFLICT (match_id) DO UPDATE SET
                home_recent_wins = excluded.home_recent_wins,
                home_recent_draws = excluded.home_recent_draws,
                home_recent_losses = excluded.home_recent_losses,
                away_recent_wins = excluded.away_recent_wins,
                away_recent_draws = excluded.away_recent_draws,
                away_recent_losses = excluded.away_recent_losses,
                home_home_points_per_match = excluded.home_home_points_per_match,
                away_away_points_per_match = excluded.away_away_points_per_match,
                source_provider = excluded.source_provider,
                updated_at = excluded.updated_at
            """,
            [
                target_match_id,
                home.get("wins"),
                home.get("draws"),
                home.get("losses"),
                away.get("wins"),
                away.get("draws"),
                away.get("losses"),
                _average(home.get("points", [])),
                _average(away.get("points", [])),
                self.PROVIDER_NAME,
            ],
        )

    def _process_mocked_response(
        self,
        connection: Any,
        fixtures: list[dict[str, Any]],
    ) -> dict[str, Any]:
        stg_odds_rows: list[tuple[Any, ...]] = []
        stg_stats_rows: list[tuple[Any, ...]] = []

        for fixture in fixtures:
            match_id = str(fixture["fixture"]["id"])
            home_team_id = str(fixture["teams"]["home"]["id"])
            away_team_id = str(fixture["teams"]["away"]["id"])

            odds_data = fixture.get("odds", {}).get("1x2")
            if odds_data:
                stg_odds_rows.append(
                    (
                        match_id,
                        "1X2",
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        self.PROVIDER_NAME,
                        _to_float(odds_data.get("home")),
                        _to_float(odds_data.get("draw")),
                        _to_float(odds_data.get("away")),
                        self.PROVIDER_NAME,
                    )
                )

            stats_data = fixture.get("stats")
            if stats_data:
                home_stats = stats_data.get("home", {})
                stg_stats_rows.append(
                    (
                        match_id,
                        home_team_id,
                        _to_float(home_stats.get("goals_for")),
                        _to_float(home_stats.get("goals_against")),
                        _to_float(home_stats.get("xg_for")),
                        _to_float(home_stats.get("xg_against")),
                        self.PROVIDER_NAME,
                    )
                )

                away_stats = stats_data.get("away", {})
                stg_stats_rows.append(
                    (
                        match_id,
                        away_team_id,
                        _to_float(away_stats.get("goals_for")),
                        _to_float(away_stats.get("goals_against")),
                        _to_float(away_stats.get("xg_for")),
                        _to_float(away_stats.get("xg_against")),
                        self.PROVIDER_NAME,
                    )
                )

        return self._sink_rows(connection, stg_odds_rows, stg_stats_rows)

    def _process_real_fixture_response(
        self,
        connection: Any,
        fixture: dict[str, Any],
    ) -> dict[str, Any]:
        fixture_id = str(fixture["fixture"]["id"])
        odds_payload = self._http_get(f"/odds?fixture={fixture_id}") or {
            "response": []
        }
        stats_payload = self._http_get(
            f"/fixtures/statistics?fixture={fixture_id}"
        ) or {"response": []}
        injuries_payload = self._http_get(
            f"/injuries?fixture={fixture_id}"
        ) or {"response": []}
        lineups_payload = self._http_get(
            f"/fixtures/lineups?fixture={fixture_id}"
        ) or {"response": []}

        stg_odds_rows = self._extract_real_odds_rows(fixture_id, odds_payload)
        stg_stats_rows = self._extract_real_stats_rows(fixture_id, stats_payload)
        stg_injury_rows = self._extract_real_injury_rows(fixture_id, injuries_payload)
        stg_lineup_rows = self._extract_real_lineup_rows(fixture_id, lineups_payload)

        self._sink_injury_rows(connection, stg_injury_rows)
        self._sink_lineup_rows(connection, stg_lineup_rows)

        if not stg_odds_rows and not stg_stats_rows:
            return {
                **_empty_result("fixture_matched_no_market_stats"),
                "missing_market_stats_count": 1,
                "injury_count": len(stg_injury_rows),
                "lineup_count": len(stg_lineup_rows),
            }
        result = self._sink_rows(connection, stg_odds_rows, stg_stats_rows)
        result["injury_count"] = len(stg_injury_rows)
        result["lineup_count"] = len(stg_lineup_rows)
        return result

    def _extract_real_odds_rows(
        self,
        fixture_id: str,
        payload: dict[str, Any],
    ) -> list[tuple[Any, ...]]:
        rows: list[tuple[Any, ...]] = []
        for item in payload.get("response", []):
            for bookmaker in item.get("bookmakers", []):
                bookmaker_name = bookmaker.get("name") or self.PROVIDER_NAME
                for bet in bookmaker.get("bets", []):
                    if str(bet.get("name", "")).casefold() not in {
                        "match winner",
                        "1x2",
                    }:
                        continue
                    prices = {
                        str(value.get("value", "")).casefold(): value.get("odd")
                        for value in bet.get("values", [])
                    }
                    home = _to_float(prices.get("home"))
                    draw = _to_float(prices.get("draw"))
                    away = _to_float(prices.get("away"))
                    if home is None or draw is None or away is None:
                        continue
                    rows.append(
                        (
                            fixture_id,
                            "1X2",
                            datetime.now(timezone.utc).strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            ),
                            bookmaker_name,
                            home,
                            draw,
                            away,
                            self.PROVIDER_NAME,
                        )
                    )
        return rows

    def _extract_real_stats_rows(
        self,
        fixture_id: str,
        payload: dict[str, Any],
    ) -> list[tuple[Any, ...]]:
        rows: list[tuple[Any, ...]] = []
        for item in payload.get("response", []):
            provider_team_id = str(item.get("team", {}).get("id", ""))
            if not provider_team_id:
                continue
            stats = {
                str(stat.get("type", "")).casefold(): stat.get("value")
                for stat in item.get("statistics", [])
            }
            rows.append(
                (
                    fixture_id,
                    provider_team_id,
                    None,
                    None,
                    _to_float(stats.get("expected goals")),
                    None,
                    self.PROVIDER_NAME,
                )
            )
        return rows

    def _sink_rows(
        self,
        connection: Any,
        stg_odds_rows: list[tuple[Any, ...]],
        stg_stats_rows: list[tuple[Any, ...]],
    ) -> dict[str, Any]:
        if stg_odds_rows:
            connection.executemany(
                """
                INSERT INTO staging.stg_odds (
                    provider_match_id, odds_type, snapshot_time, bookmaker_name,
                    home_odds, draw_odds, away_odds, source_provider
                ) VALUES (?, ?, CAST(? AS TIMESTAMP), ?, ?, ?, ?, ?)
                """,
                stg_odds_rows,
            )

        if stg_stats_rows:
            connection.executemany(
                """
                INSERT INTO staging.stg_team_stats (
                    provider_match_id, provider_team_id,
                    goals_for, goals_against, xg_for, xg_against, source_provider
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                stg_stats_rows,
            )

        odds_match_ids = sorted({row[0] for row in stg_odds_rows})
        stats_match_ids = sorted({row[0] for row in stg_stats_rows})
        eligible_stats_count = self._count_mapped_stats(connection, stats_match_ids)
        self._merge_odds(connection, odds_match_ids)
        self._merge_stats(connection, stats_match_ids)

        staged_count = len(stg_stats_rows)
        return {
            "status": "success",
            "odds_count": len(stg_odds_rows),
            "stats_count": len(stg_stats_rows),
            "staged_count": staged_count,
            "merged_count": eligible_stats_count,
            "skipped_unmapped_count": max(0, staged_count - eligible_stats_count),
            "missing_market_stats_count": 0,
        }

    def _count_mapped_stats(
        self,
        connection: Any,
        provider_match_ids: list[str],
    ) -> int:
        if not provider_match_ids:
            return 0
        placeholders = ",".join("?" for _ in provider_match_ids)
        return int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM staging.stg_team_stats s
                JOIN core.dim_provider_team_mapping m
                  ON s.source_provider = m.provider_name
                 AND s.provider_team_id = m.provider_team_id
                WHERE s.source_provider = ?
                  AND s.provider_match_id IN ({placeholders})
                """,
                [self.PROVIDER_NAME, *provider_match_ids],
            ).fetchone()[0]
        )

    def _merge_odds(self, connection: Any, provider_match_ids: list[str]) -> None:
        if not provider_match_ids:
            return
        placeholders = ",".join("?" for _ in provider_match_ids)
        try:
            connection.execute(
                f"""
                INSERT INTO core.fact_odds_capital_flow (
                    match_id, snapshot_time, odds_type, bookmaker_name,
                    home_odds, draw_odds, away_odds, source_provider
                )
                SELECT
                    'M_AF_' || provider_match_id,
                    snapshot_time,
                    odds_type,
                    bookmaker_name,
                    home_odds,
                    draw_odds,
                    away_odds,
                    source_provider
                FROM staging.stg_odds
                WHERE source_provider = ?
                  AND provider_match_id IN ({placeholders})
                ON CONFLICT (match_id, snapshot_time, odds_type, bookmaker_name)
                DO NOTHING
                """,
                [self.PROVIDER_NAME, *provider_match_ids],
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to merge API-Football odds to core: {exc}"
            ) from exc

    def _merge_stats(self, connection: Any, provider_match_ids: list[str]) -> None:
        if not provider_match_ids:
            return
        placeholders = ",".join("?" for _ in provider_match_ids)
        try:
            connection.execute(
                f"""
                INSERT INTO core.fact_team_rolling_stats (
                    match_id, team_id, record_date, source_provider,
                    rolling_goals_for, rolling_goals_against,
                    rolling_xg_for, rolling_xg_against, data_quality_flag
                )
                SELECT
                    'M_AF_' || s.provider_match_id,
                    m.system_team_id,
                    CURRENT_TIMESTAMP,
                    s.source_provider,
                    s.goals_for,
                    s.goals_against,
                    s.xg_for,
                    s.xg_against,
                    'API_FOOTBALL_SINGLE_MATCH_STATS'
                FROM staging.stg_team_stats s
                JOIN core.dim_provider_team_mapping m
                  ON s.source_provider = m.provider_name
                 AND s.provider_team_id = m.provider_team_id
                WHERE s.source_provider = ?
                  AND s.provider_match_id IN ({placeholders})
                ON CONFLICT (match_id, team_id, record_date, source_provider)
                DO UPDATE SET
                    rolling_goals_for = excluded.rolling_goals_for,
                    rolling_goals_against = excluded.rolling_goals_against,
                    rolling_xg_for = excluded.rolling_xg_for,
                    rolling_xg_against = excluded.rolling_xg_against,
                    data_quality_flag = excluded.data_quality_flag
                """,
                [self.PROVIDER_NAME, *provider_match_ids],
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to merge API-Football stats to core: {exc}"
            ) from exc

    def _extract_real_injury_rows(
        self,
        fixture_id: str,
        payload: dict[str, Any],
    ) -> list[tuple[Any, ...]]:
        rows: list[tuple[Any, ...]] = []
        for item in payload.get("response", []):
            player = item.get("player", {})
            player_name = player.get("name", "")
            if not player_name:
                continue
            team = item.get("team", {})
            team_name = team.get("name", "")
            injury_type = item.get("type", "")
            reason = item.get("reason", "")
            rows.append((
                fixture_id,
                player_name,
                team_name,
                injury_type,
                reason,
                self.PROVIDER_NAME,
            ))
        return rows

    def _extract_real_lineup_rows(
        self,
        fixture_id: str,
        payload: dict[str, Any],
    ) -> list[tuple[Any, ...]]:
        rows: list[tuple[Any, ...]] = []
        for item in payload.get("response", []):
            team = item.get("team", {})
            team_name = team.get("name", "")
            formation = item.get("formation", "")
            for player in item.get("startXI", []):
                player_info = player.get("player", {})
                player_name = player_info.get("name", "")
                if not player_name:
                    continue
                rows.append((
                    fixture_id,
                    player_name,
                    team_name,
                    "starter",
                    formation,
                    self.PROVIDER_NAME,
                ))
            for player in item.get("substitutes", []):
                player_info = player.get("player", {})
                player_name = player_info.get("name", "")
                if not player_name:
                    continue
                rows.append((
                    fixture_id,
                    player_name,
                    team_name,
                    "substitute",
                    formation,
                    self.PROVIDER_NAME,
                ))
        return rows

    def _sink_injury_rows(
        self,
        connection: Any,
        rows: list[tuple[Any, ...]],
    ) -> None:
        if not rows:
            return
        try:
            connection.executemany(
                """
                INSERT INTO staging.stg_injuries (
                    provider_match_id, player_name, team_name,
                    injury_type, reason, source_provider
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        except Exception:
            pass

    def _sink_lineup_rows(
        self,
        connection: Any,
        rows: list[tuple[Any, ...]],
    ) -> None:
        if not rows:
            return
        try:
            connection.executemany(
                """
                INSERT INTO staging.stg_lineups (
                    provider_match_id, player_name, team_name,
                    role, formation, source_provider
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        except Exception:
            pass

    def _http_get(self, path: str) -> dict[str, Any] | None:
        if self._uses_mock_response():
            return self._mock_response(path)

        url = self.base_url + path
        req = urllib.request.Request(url, headers={"x-apisports-key": self.api_token})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode())
        except urllib.error.URLError:
            return None

    def fetch_team_last_matches(
        self,
        connection: Any,
        target_match_id: str,
        provider_team_id: str,
        system_team_id: str,
        last_n: int = 10,
    ) -> dict[str, Any]:
        """Fetch last N finished matches for a given team and aggregate to rolling stats.

        Uses season-based queries because national team fixtures are often scheduled
        far in the future, making `last=N` ineffective for historical data.
        """
        # Try current and previous two seasons
        seasons = _recent_seasons()
        all_fixtures: list[dict[str, Any]] = []
        for season in seasons:
            raw_data = self._http_get(
                f"/fixtures?team={provider_team_id}&season={season}&status=FT"
            )
            if not raw_data or "response" not in raw_data:
                continue
            season_fixtures = raw_data["response"]
            if season_fixtures:
                all_fixtures.extend(season_fixtures)
                if len(all_fixtures) >= last_n:
                    break

        if not all_fixtures:
            return {
                "status": "success",
                "team_provider_id": provider_team_id,
                "team_system_id": system_team_id,
                "match_count": 0,
                "mapped_stats_count": 0,
            }

        # Sort by date descending and take most recent last_n
        all_fixtures.sort(
            key=lambda f: f.get("fixture", {}).get("date", ""),
            reverse=True,
        )
        fixtures = all_fixtures[:last_n]

        rows: list[tuple[Any, ...]] = []
        recent_results: dict[str, dict[str, Any]] = {}
        mapped_count = 0
        for fixture in fixtures:
            goals = fixture.get("goals", {})
            home_goals = _to_float(goals.get("home"))
            away_goals = _to_float(goals.get("away"))
            if home_goals is None or away_goals is None:
                continue
            fixture_time = _parse_fixture_datetime(fixture.get("fixture", {}).get("date"))
            teams = fixture.get("teams", {})
            # Determine which side is our target team
            if str(teams.get("home", {}).get("id")) == provider_team_id:
                goals_for, goals_against = home_goals, away_goals
            elif str(teams.get("away", {}).get("id")) == provider_team_id:
                goals_for, goals_against = away_goals, home_goals
            else:
                continue

            _record_recent_result(
                recent_results,
                system_team_id,
                goals_for,
                goals_against,
            )
            rows.append(
                (
                    target_match_id,
                    system_team_id,
                    fixture_time,
                    self.PROVIDER_NAME,
                    goals_for,
                    goals_against,
                    _points_for(goals_for, goals_against),
                    _clamp(goals_for / 1.4, 0.65, 1.55),
                    _clamp(goals_against / 1.1, 0.65, 1.55),
                    0.5,
                    0.0,
                    "API_FOOTBALL_TEAM_LASTN",
                )
            )
            mapped_count += 1

        if rows:
            connection.executemany(
                """
                INSERT INTO core.fact_team_rolling_stats (
                    match_id,
                    team_id,
                    record_date,
                    source_provider,
                    rolling_goals_for,
                    rolling_goals_against,
                    recent_points_per_match,
                    attack_strength,
                    defense_strength,
                    motivation_coefficient,
                    key_missing_weight,
                    data_quality_flag
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (match_id, team_id, record_date, source_provider)
                DO UPDATE SET
                    rolling_goals_for = excluded.rolling_goals_for,
                    rolling_goals_against = excluded.rolling_goals_against,
                    recent_points_per_match = excluded.recent_points_per_match,
                    attack_strength = excluded.attack_strength,
                    defense_strength = excluded.defense_strength,
                    motivation_coefficient = excluded.motivation_coefficient,
                    key_missing_weight = excluded.key_missing_weight,
                    data_quality_flag = excluded.data_quality_flag
                """,
                rows,
            )
            # Update context summary if both teams done
            if recent_results:
                self._upsert_team_recent_context(connection, target_match_id, recent_results)

        return {
            "status": "success",
            "team_provider_id": provider_team_id,
            "team_system_id": system_team_id,
            "match_count": len(fixtures),
            "mapped_stats_count": mapped_count,
        }

    def _upsert_team_recent_context(
        self,
        connection: Any,
        target_match_id: str,
        recent_results: dict[str, dict[str, Any]],
    ) -> None:
        """Update match context summary with accumulated win/draw/loss from fetched matches."""
        if not recent_results:
            return
        row = connection.execute(
            """
            SELECT home_team_id, away_team_id
            FROM core.fact_match_schedule
            WHERE match_id = ?
            """,
            [target_match_id],
        ).fetchone()
        if not row:
            return
        home_team_id, away_team_id = str(row[0]), str(row[1])
        home = recent_results.get(home_team_id, {})
        away = recent_results.get(away_team_id, {})

        # Check if we already have a row
        existing = connection.execute(
            """
            SELECT 1 FROM core.fact_match_context_summary WHERE match_id = ?
            """,
            [target_match_id],
        ).fetchone()

        if existing:
            # Always provide 8 values (4 home + 4 away), None means keep existing
            update_rows: list[Any] = [
                home.get("wins"), home.get("draws"), home.get("losses"),
                _average(home.get("points", [])),
                away.get("wins"), away.get("draws"), away.get("losses"),
                _average(away.get("points", [])),
            ]
            connection.execute(
                """
                UPDATE core.fact_match_context_summary SET
                    home_recent_wins = COALESCE(?, home_recent_wins),
                    home_recent_draws = COALESCE(?, home_recent_draws),
                    home_recent_losses = COALESCE(?, home_recent_losses),
                    home_home_points_per_match = COALESCE(?, home_home_points_per_match),
                    away_recent_wins = COALESCE(?, away_recent_wins),
                    away_recent_draws = COALESCE(?, away_recent_draws),
                    away_recent_losses = COALESCE(?, away_recent_losses),
                    away_away_points_per_match = COALESCE(?, away_away_points_per_match),
                    source_provider = ?,
                    updated_at = CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
                WHERE match_id = ?
                """,
                [*update_rows, self.PROVIDER_NAME, target_match_id],
            )
        else:
            connection.execute(
                """
                INSERT INTO core.fact_match_context_summary (
                    match_id,
                    home_recent_wins,
                    home_recent_draws,
                    home_recent_losses,
                    away_recent_wins,
                    away_recent_draws,
                    away_recent_losses,
                    home_home_points_per_match,
                    away_away_points_per_match,
                    source_provider,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CAST(CURRENT_TIMESTAMP AS TIMESTAMP))
                """,
                [
                    target_match_id,
                    home.get("wins"),
                    home.get("draws"),
                    home.get("losses"),
                    away.get("wins"),
                    away.get("draws"),
                    away.get("losses"),
                    _average(home.get("points", [])),
                    _average(away.get("points", [])),
                    self.PROVIDER_NAME,
                ],
            )

    def _uses_mock_response(self) -> bool:
        return not self.api_token or self.api_token == "mock_token"

    def _select_fixture(
        self,
        fixtures: list[dict[str, Any]],
        home_team: str | None,
        away_team: str | None,
    ) -> dict[str, Any] | None:
        if not fixtures:
            return None
        if not home_team or not away_team:
            return fixtures[0]

        home_key = home_team.strip().casefold()
        away_key = away_team.strip().casefold()
        for fixture in fixtures:
            teams = fixture.get("teams", {})
            home = teams.get("home", {})
            away = teams.get("away", {})
            if (
                str(home.get("name", "")).strip().casefold() == home_key
                and str(away.get("name", "")).strip().casefold() == away_key
            ):
                return fixture
            if (
                str(home.get("id", "")).strip().casefold() == home_key
                and str(away.get("id", "")).strip().casefold() == away_key
            ):
                return fixture
        return None

    def _find_target_match_id(
        self,
        connection: Any,
        target_match_time: datetime,
        home_team: str | None,
        away_team: str | None,
    ) -> str | None:
        if not home_team or not away_team:
            return None
        target_time = _as_utc_naive(target_match_time)
        rows = connection.execute(
            """
            SELECT s.match_id, s.match_time
            FROM core.fact_match_schedule s
            JOIN core.dim_team_mapping h ON s.home_team_id = h.system_team_id
            JOIN core.dim_team_mapping a ON s.away_team_id = a.system_team_id
            WHERE lower(h.team_standard_name) = lower(?)
              AND lower(a.team_standard_name) = lower(?)
              AND s.match_time BETWEEN ? AND ?
            """,
            [
                home_team,
                away_team,
                target_time - timedelta(days=7),
                target_time + timedelta(days=7),
            ],
        ).fetchall()
        if not rows:
            return None
        row = min(rows, key=lambda item: abs((item[1] - target_time).total_seconds()))
        return str(row[0])

    def _load_target_provider_team_mapping(
        self,
        connection: Any,
        home_team: str | None,
        away_team: str | None,
    ) -> dict[str, str]:
        names = [name.casefold() for name in (home_team, away_team) if name]
        if not names:
            return {}
        placeholders = ",".join("?" for _ in names)
        rows = connection.execute(
            f"""
            SELECT m.provider_team_id, m.system_team_id
            FROM core.dim_provider_team_mapping m
            JOIN core.dim_team_mapping t ON m.system_team_id = t.system_team_id
            WHERE m.provider_name = ?
              AND lower(t.team_standard_name) IN ({placeholders})
            """,
            [self.PROVIDER_NAME, *names],
        ).fetchall()
        return {
            str(provider_team_id): str(system_team_id)
            for provider_team_id, system_team_id in rows
        }

    def _mock_response(self, path: str) -> dict[str, Any]:
        return {
            "response": [
                {
                    "fixture": {
                        "id": 888881,
                        "date": datetime.now(timezone.utc).strftime(
                            "%Y-%m-%dT%H:%M:%SZ"
                        ),
                    },
                    "teams": {
                        "home": {"id": "SYS_ARS"},
                        "away": {"id": "SYS_CHE"},
                    },
                    "odds": {"1x2": {"home": 2.1, "draw": 3.4, "away": 3.5}},
                    "stats": {
                        "home": {
                            "goals_for": 12.0,
                            "goals_against": 4.0,
                            "xg_for": 10.5,
                            "xg_against": 3.2,
                        },
                        "away": {
                            "goals_for": 8.0,
                            "goals_against": 9.0,
                            "xg_for": 7.8,
                            "xg_against": 8.5,
                        },
                    },
                }
            ]
        }


def _empty_result(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "odds_count": 0,
        "stats_count": 0,
        "staged_count": 0,
        "merged_count": 0,
        "skipped_unmapped_count": 0,
        "missing_market_stats_count": 0,
    }


def _empty_recent_friendlies_result(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "friendly_count": 0,
        "mapped_team_stats_count": 0,
        "skipped_unmapped_count": 0,
    }


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_finished_world_friendly(fixture: dict[str, Any]) -> bool:
    league = fixture.get("league", {})
    if league.get("id") != 10:
        if str(league.get("name", "")).casefold() != "friendlies":
            return False
    if str(league.get("country", "")).casefold() != "world":
        return False
    status = fixture.get("fixture", {}).get("status", {})
    if str(status.get("short", "")).casefold() != "ft":
        return False
    teams = fixture.get("teams", {})
    return all(
        _is_senior_mens_team_name(str(teams.get(side, {}).get("name", "")))
        for side in ("home", "away")
    )


def _is_senior_mens_team_name(name: str) -> bool:
    normalized = f" {name.casefold()} "
    blocked_tokens = (
        " u17 ",
        " u18 ",
        " u19 ",
        " u20 ",
        " u21 ",
        " u23 ",
        " women ",
        " woman ",
        " w ",
        " (w) ",
    )
    return bool(name.strip()) and not any(token in normalized for token in blocked_tokens)


def _parse_fixture_datetime(value: Any) -> datetime:
    if not value:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    return _as_utc_naive(parsed)


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _points_for(goals_for: float, goals_against: float) -> float:
    if goals_for > goals_against:
        return 3.0
    if goals_for == goals_against:
        return 1.0
    return 0.0


def _record_recent_result(
    results: dict[str, dict[str, Any]],
    system_team_id: str,
    goals_for: float,
    goals_against: float,
) -> None:
    result = results.setdefault(
        system_team_id,
        {"wins": 0, "draws": 0, "losses": 0, "points": []},
    )
    points = _points_for(goals_for, goals_against)
    result["points"].append(points)
    if points == 3.0:
        result["wins"] += 1
    elif points == 1.0:
        result["draws"] += 1
    else:
        result["losses"] += 1


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _recent_seasons() -> list[int]:
    """Return recent seasons for team fixture queries, newest first."""
    current_year = datetime.now(timezone.utc).year
    return [current_year, current_year - 1, current_year - 2]
