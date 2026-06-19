from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import urllib.error
import urllib.parse
import urllib.request


class TheSportsDBClient:
    """Free API client for TheSportsDB — squad and historical match data.

    TheSportsDB 是免费众包体育数据库，提供：
    - 国家队/俱乐部阵容
    - 历史比赛记录
    - 世界杯 League ID = 4429
    """

    PROVIDER_NAME = "TheSportsDB"
    BASE_URL = "https://www.thesportsdb.com/api/v1/json"

    def __init__(self, api_token: str | None = None, base_url: str | None = None):
        self.api_token = api_token
        self.base_url = (base_url or self.BASE_URL).rstrip("/")

    def fetch_squad(
        self,
        connection: Any,
        home_team: str | None,
        away_team: str | None,
        kickoff_time: datetime | None = None,
    ) -> dict[str, Any]:
        if not home_team or not away_team:
            return self._empty_result("missing_team_names")

        home_players = self._fetch_players_for_team(home_team, connection)
        away_players = self._fetch_players_for_team(away_team, connection)
        home_events = self._fetch_events_for_team(home_team, connection, previous=True)
        away_events = self._fetch_events_for_team(away_team, connection, previous=True)

        squad_count = len(home_players) + len(away_players)
        history_count = len(home_events) + len(away_events)
        if squad_count == 0:
            return self._empty_result("no_squad_found")

        stg_rows: list[tuple[Any, ...]] = []
        for player in home_players:
            stg_rows.append((
                home_team,
                player.get("strPlayer", ""),
                player.get("strPosition", ""),
                player.get("strThumb", ""),
                self.PROVIDER_NAME,
            ))
        for player in away_players:
            stg_rows.append((
                away_team,
                player.get("strPlayer", ""),
                player.get("strPosition", ""),
                player.get("strThumb", ""),
                self.PROVIDER_NAME,
            ))

        if stg_rows and connection is not None:
            try:
                connection.executemany(
                    """
                    INSERT OR IGNORE INTO staging.stg_player_squad (
                        team_name, player_name, position, thumbnail_url, source_provider
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    stg_rows,
                )
            except Exception:
                pass

        return {
            "status": "success",
            "squad_count": squad_count,
            "history_count": history_count,
        }

    def fetch_historical_matches(
        self,
        team_name: str,
        connection: Any | None = None,
    ) -> dict[str, Any]:
        team_id = self._lookup_team_id(team_name, connection)
        if not team_id:
            return self._empty_result("no_history_found")

        data = self._http_get(f"/eventslast.php?id={urllib.parse.quote(team_id)}")
        if not data or "results" not in data:
            return self._empty_result("no_history_found")

        events = data.get("results", [])
        count = len(events) if isinstance(events, list) else 0
        return {
            "status": "success",
            "squad_count": 0,
            "history_count": count,
        }

    def list_sports(self) -> list[dict[str, Any]]:
        data = self._http_get("/all_sports.php")
        sports = data.get("sports") if data else None
        return sports if isinstance(sports, list) else []

    def list_leagues(self) -> list[dict[str, Any]]:
        data = self._http_get("/all_leagues.php")
        leagues = data.get("leagues") if data else None
        return leagues if isinstance(leagues, list) else []

    def list_teams_in_league(self, league: str) -> list[dict[str, Any]]:
        if not league:
            return []
        param = "id" if str(league).isdigit() else "l"
        data = self._http_get(f"/search_all_teams.php?{param}={urllib.parse.quote(str(league))}")
        teams = data.get("teams") if data else None
        return teams if isinstance(teams, list) else []

    def list_players_in_team(self, team_id: str) -> list[dict[str, Any]]:
        if not team_id:
            return []
        data = self._http_get(f"/lookup_all_players.php?id={urllib.parse.quote(str(team_id))}")
        return self._extract_players(data)

    def lookup_team(self, team_id: str) -> dict[str, Any] | None:
        return self._lookup_first(f"/lookupteam.php?id={urllib.parse.quote(str(team_id))}", "teams")

    def lookup_player(self, player_id: str) -> dict[str, Any] | None:
        return self._lookup_first(f"/lookupplayer.php?id={urllib.parse.quote(str(player_id))}", "players")

    def lookup_event(self, event_id: str) -> dict[str, Any] | None:
        return self._lookup_first(f"/lookupevent.php?id={urllib.parse.quote(str(event_id))}", "events")

    def lookup_venue(self, venue_id: str) -> dict[str, Any] | None:
        return self._lookup_first(f"/lookupvenue.php?id={urllib.parse.quote(str(venue_id))}", "venues")

    def fetch_next_events(
        self,
        team_name: str,
        connection: Any | None = None,
    ) -> dict[str, Any]:
        events = self._fetch_events_for_team(team_name, connection, previous=False)
        if not events:
            return {"status": "no_events_found", "event_count": 0}
        return {"status": "success", "event_count": len(events)}

    def _http_get(self, path: str) -> dict[str, Any] | None:
        if not self.api_token:
            return None
        if self.api_token == "mock_token":
            return self._mock_response(path)

        # 单元测试禁网：非 mock_token 时禁止真实网络请求
        import os
        if os.environ.get("FOOTBALL_UNIT_TEST_NETWORK_BLOCK", "0") == "1":
            raise RuntimeError(
                "单元测试禁止访问真实网络。请使用 mock_token 或设置环境变量。"
            )

        url = f"{self.base_url}/{self.api_token}{path}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError):
            return None

    def _lookup_team_id(self, team_name: str, connection: Any | None = None) -> str | None:
        mapped_team_id = self._lookup_mapped_team_id(connection, team_name)
        if mapped_team_id:
            return mapped_team_id
        if self.api_token == "123" and team_name.lower() != "arsenal":
            return None
        data = self._http_get(f"/searchteams.php?t={urllib.parse.quote(team_name)}")
        if not data or "teams" not in data:
            return None
        teams = data.get("teams")
        if not teams or not isinstance(teams, list) or not isinstance(teams[0], dict):
            return None
        team_id = teams[0].get("idTeam")
        return str(team_id) if team_id else None

    def _lookup_mapped_team_id(
        self,
        connection: Any | None,
        team_name: str,
    ) -> str | None:
        if connection is None:
            return None
        try:
            row = connection.execute(
                """
                SELECT p.provider_team_id
                FROM core.dim_provider_team_mapping p
                JOIN core.dim_team_mapping t
                  ON p.system_team_id = t.system_team_id
                WHERE p.provider_name = ?
                  AND lower(t.team_standard_name) = lower(?)
                LIMIT 1
                """,
                [self.PROVIDER_NAME, team_name],
            ).fetchone()
        except Exception:
            return None
        if not row:
            return None
        return str(row[0]) if row[0] else None

    def _fetch_players_for_team(
        self,
        team_name: str,
        connection: Any | None = None,
    ) -> list[dict[str, Any]]:
        team_id = self._lookup_team_id(team_name, connection)
        if not team_id:
            return []
        data = self._http_get(f"/lookup_all_players.php?id={urllib.parse.quote(team_id)}")
        return self._extract_players(data)

    def _fetch_events_for_team(
        self,
        team_name: str,
        connection: Any | None = None,
        previous: bool = True,
    ) -> list[dict[str, Any]]:
        team_id = self._lookup_team_id(team_name, connection)
        if not team_id:
            return []
        endpoint = "eventslast.php" if previous else "eventsnext.php"
        data = self._http_get(f"/{endpoint}?id={urllib.parse.quote(team_id)}")
        return self._extract_events(data)

    def _extract_players(self, data: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not data or "teams" not in data:
            players = data.get("player") if data else None
            return players if isinstance(players, list) else []
        teams = data.get("teams")
        if not teams or not isinstance(teams, list):
            return []
        players = teams[0].get("strPlayers", []) if isinstance(teams[0], dict) else []
        return players if isinstance(players, list) else []

    def _extract_events(self, data: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not data:
            return []
        for key in ("results", "events"):
            events = data.get(key)
            if isinstance(events, list):
                return events
        return []

    def _lookup_first(self, path: str, selector: str) -> dict[str, Any] | None:
        data = self._http_get(path)
        values = data.get(selector) if data else None
        if not isinstance(values, list) or not values or not isinstance(values[0], dict):
            return None
        return values[0]

    def _empty_result(self, status: str) -> dict[str, Any]:
        return {"status": status, "squad_count": 0, "history_count": 0}

    def _mock_response(self, path: str) -> dict[str, Any]:
        if "all_sports" in path:
            return {"sports": [{"idSport": "102", "strSport": "Soccer"}]}
        if "all_leagues" in path:
            return {
                "leagues": [
                    {
                        "idLeague": "4328",
                        "strLeague": "English Premier League",
                        "strSport": "Soccer",
                    }
                ]
            }
        if "search_all_teams" in path:
            return {
                "teams": [
                    {
                        "idTeam": "133604",
                        "strTeam": "Arsenal",
                        "strLeague": "English Premier League",
                    }
                ]
            }
        if "lookupteam" in path:
            return {"teams": [{"idTeam": "133604", "strTeam": "Arsenal"}]}
        if "lookupplayer" in path:
            return {"players": [{"idPlayer": "34145937", "strPlayer": "Mock Player"}]}
        if "lookupevent" in path:
            return {"events": [{"idEvent": "441613", "strEvent": "Mock Event"}]}
        if "lookupvenue" in path:
            return {"venues": [{"idVenue": "16163", "strVenue": "Mock Venue"}]}
        if "searchteams" in path:
            return {
                "teams": [
                    {
                        "idTeam": "133604",
                        "strTeam": "Arsenal",
                    }
                ]
            }
        if "lookup_all_players" in path:
            return {
                "player": [
                    {
                        "idPlayer": "34145937",
                        "strPlayer": "Mock Player 1",
                        "strPosition": "Forward",
                        "strThumb": "",
                    },
                    {
                        "idPlayer": "34145938",
                        "strPlayer": "Mock Player 2",
                        "strPosition": "Midfielder",
                        "strThumb": "",
                    },
                ]
            }
        if "eventsnext" in path:
            return {
                "events": [
                    {"idEvent": "3", "strEvent": "Arsenal vs Tottenham"},
                ]
            }
        if "eventslast" in path:
            return {
                "results": [
                    {"idEvent": "1", "strEvent": "Arsenal vs Chelsea"},
                    {"idEvent": "2", "strEvent": "Arsenal vs Liverpool"},
                ]
            }
        return {}
