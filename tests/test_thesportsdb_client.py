from __future__ import annotations

import unittest

from football_advisor.thesportsdb_client import TheSportsDBClient


class TheSportsDBClientTests(unittest.TestCase):
    def test_mock_fetch_squad_returns_data(self):
        client = TheSportsDBClient(api_token="mock_token")
        result = client.fetch_squad(None, "Arsenal", "Chelsea")
        self.assertEqual(result["status"], "success")
        self.assertGreater(result["squad_count"], 0)

    def test_no_token_returns_empty(self):
        client = TheSportsDBClient(api_token=None)
        result = client.fetch_squad(None, "Arsenal", "Chelsea")
        self.assertEqual(result["status"], "no_squad_found")

    def test_missing_team_names_returns_empty(self):
        client = TheSportsDBClient(api_token="mock_token")
        result = client.fetch_squad(None, None, None)
        self.assertEqual(result["status"], "missing_team_names")

    def test_fetch_historical_matches_mock(self):
        client = TheSportsDBClient(api_token="mock_token")
        result = client.fetch_historical_matches("Arsenal")
        self.assertEqual(result["status"], "success")
        self.assertGreater(result["history_count"], 0)

    def test_fetch_squad_includes_documented_history_endpoint(self):
        client = TheSportsDBClient(api_token="mock_token")
        result = client.fetch_squad(None, "Arsenal", "Chelsea")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["history_count"], 4)

    def test_documented_lookup_endpoints_return_mock_data(self):
        client = TheSportsDBClient(api_token="mock_token")
        self.assertEqual(client.lookup_team("133604")["idTeam"], "133604")
        self.assertEqual(client.lookup_player("34145937")["idPlayer"], "34145937")
        self.assertEqual(client.lookup_event("441613")["idEvent"], "441613")
        self.assertEqual(client.lookup_venue("16163")["idVenue"], "16163")

    def test_documented_list_endpoints_return_mock_data(self):
        client = TheSportsDBClient(api_token="mock_token")
        self.assertEqual(client.list_sports()[0]["strSport"], "Soccer")
        self.assertEqual(client.list_leagues()[0]["idLeague"], "4328")
        self.assertEqual(client.list_teams_in_league("English Premier League")[0]["idTeam"], "133604")
        self.assertEqual(client.list_teams_in_league("4328")[0]["idTeam"], "133604")
        self.assertEqual(client.list_players_in_team("133604")[0]["idPlayer"], "34145937")
        self.assertEqual(client.fetch_next_events("Arsenal")["event_count"], 1)

    def test_team_id_list_uses_league_id_parameter_for_numeric_league(self):
        class RecordingClient(TheSportsDBClient):
            def __init__(self):
                super().__init__(api_token="mock_token")
                self.paths = []

            def _http_get(self, path):
                self.paths.append(path)
                return {"teams": [{"idTeam": "133604", "strTeam": "Arsenal"}]}

        client = RecordingClient()
        client.list_teams_in_league("4328")
        self.assertEqual(client.paths, ["/search_all_teams.php?id=4328"])

    def test_player_id_list_uses_team_id_endpoint(self):
        class RecordingClient(TheSportsDBClient):
            def __init__(self):
                super().__init__(api_token="mock_token")
                self.paths = []

            def _http_get(self, path):
                self.paths.append(path)
                return {"player": [{"idPlayer": "34145937", "strPlayer": "Mock Player"}]}

        client = RecordingClient()
        players = client.list_players_in_team("133604")
        self.assertEqual(players[0]["idPlayer"], "34145937")
        self.assertEqual(client.paths, ["/lookup_all_players.php?id=133604"])

    def test_mock_uses_documented_team_lookup_then_player_lookup(self):
        client = TheSportsDBClient(api_token="mock_token")
        result = client.fetch_squad(None, "Arsenal", "Chelsea")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["squad_count"], 4)

    def test_free_key_does_not_search_non_arsenal_team_without_mapping(self):
        client = TheSportsDBClient(api_token="123")
        result = client.fetch_squad(None, "Chelsea", "Liverpool")
        self.assertEqual(result["status"], "no_squad_found")

    def test_provider_mapping_ids_are_used_before_free_text_search(self):
        class MappingConnection:
            def __init__(self):
                self.params = None
                self.inserted_rows = []

            def execute(self, sql, params=None):
                self.params = params
                return self

            def fetchone(self):
                mapping = {"Arsenal": "133604", "Chelsea": "133610"}
                team_name = self.params[1]
                team_id = mapping.get(team_name)
                return (team_id,) if team_id else None

            def executemany(self, sql, rows):
                self.inserted_rows.extend(rows)

        class RecordingClient(TheSportsDBClient):
            def __init__(self):
                super().__init__(api_token="123")
                self.paths = []

            def _http_get(self, path):
                self.paths.append(path)
                return {
                    "player": [
                        {
                            "strPlayer": "Mapped Player",
                            "strPosition": "Forward",
                            "strThumb": "",
                        }
                    ]
                }

        connection = MappingConnection()
        client = RecordingClient()
        result = client.fetch_squad(connection, "Arsenal", "Chelsea")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["squad_count"], 2)
        self.assertEqual(
            client.paths,
            [
                "/lookup_all_players.php?id=133604",
                "/lookup_all_players.php?id=133610",
                "/eventslast.php?id=133604",
                "/eventslast.php?id=133610",
            ],
        )
        self.assertEqual(len(connection.inserted_rows), 2)

    def test_no_token_historical_returns_empty(self):
        client = TheSportsDBClient(api_token=None)
        result = client.fetch_historical_matches("Arsenal")
        self.assertEqual(result["status"], "no_history_found")


if __name__ == "__main__":
    unittest.main()
