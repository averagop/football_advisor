from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from football_advisor.thesportsdb_mapping_updater import (
    update_thesportsdb_team_mappings,
)


class FakeTheSportsDBClient:
    def __init__(self, teams_by_league: dict[str, list[dict[str, object]]]):
        self.teams_by_league = teams_by_league
        self.requested_leagues: list[str] = []

    def list_teams_in_league(self, league: str) -> list[dict[str, object]]:
        self.requested_leagues.append(league)
        return self.teams_by_league.get(league, [])


class TheSportsDBMappingUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.mapping_dir = Path(self.temp_dir.name)
        (self.mapping_dir / "provider_league_mappings.csv").write_text(
            "provider_name,provider_league_id,system_league_id,provider_league_name\n"
            "TheSportsDB,4328,TOP5_EPL,English Premier League\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_appends_only_unique_official_matches(self):
        (self.mapping_dir / "provider_team_mappings.csv").write_text(
            "provider_name,provider_team_id,system_team_id,provider_team_name\n"
            "API-Football,34,TOP5_NEW,Newcastle United\n"
            "football-data.org,67,TOP5_NEW,Newcastle United FC\n"
            "API-Football,42,TOP5_ARS,Arsenal\n"
            "TheSportsDB,133604,TOP5_ARS,Arsenal\n",
            encoding="utf-8",
        )
        client = FakeTheSportsDBClient(
            {
                "4328": [
                    {
                        "idTeam": "134777",
                        "strTeam": "Newcastle United",
                        "strTeamAlternate": "Newcastle United FC",
                    }
                ]
            }
        )

        result = update_thesportsdb_team_mappings(self.mapping_dir, client)

        self.assertEqual(result.added, 1)
        self.assertEqual(client.requested_leagues, ["4328"])
        self.assertIn(
            "TheSportsDB,134777,TOP5_NEW,Newcastle United\n",
            (self.mapping_dir / "provider_team_mappings.csv").read_text(
                encoding="utf-8"
            ),
        )

    def test_dry_run_does_not_write_candidates(self):
        (self.mapping_dir / "provider_team_mappings.csv").write_text(
            "provider_name,provider_team_id,system_team_id,provider_team_name\n"
            "API-Football,34,TOP5_NEW,Newcastle United\n",
            encoding="utf-8",
        )
        client = FakeTheSportsDBClient(
            {"4328": [{"idTeam": "134777", "strTeam": "Newcastle United"}]}
        )

        result = update_thesportsdb_team_mappings(
            self.mapping_dir,
            client,
            write=False,
        )

        self.assertEqual(result.added, 1)
        content = (self.mapping_dir / "provider_team_mappings.csv").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("TheSportsDB,134777", content)

    def test_ambiguous_match_is_not_written(self):
        (self.mapping_dir / "provider_team_mappings.csv").write_text(
            "provider_name,provider_team_id,system_team_id,provider_team_name\n"
            "API-Football,1,TOP5_DUP,Example FC\n",
            encoding="utf-8",
        )
        client = FakeTheSportsDBClient(
            {
                "4328": [
                    {"idTeam": "1", "strTeam": "Example FC"},
                    {"idTeam": "2", "strTeam": "Example"},
                ]
            }
        )

        result = update_thesportsdb_team_mappings(self.mapping_dir, client)

        self.assertEqual(result.added, 0)
        self.assertEqual(result.skipped_ambiguous, 1)

    def test_existing_provider_id_conflict_is_not_written(self):
        (self.mapping_dir / "provider_team_mappings.csv").write_text(
            "provider_name,provider_team_id,system_team_id,provider_team_name\n"
            "TheSportsDB,133601,TOP5_CHE,Chelsea\n"
            "API-Football,66,TOP5_AVL,Aston Villa\n",
            encoding="utf-8",
        )
        client = FakeTheSportsDBClient(
            {"4328": [{"idTeam": "133601", "strTeam": "Aston Villa"}]}
        )

        result = update_thesportsdb_team_mappings(self.mapping_dir, client)

        self.assertEqual(result.added, 0)
        self.assertEqual(result.skipped_conflicting_provider_id, 1)
        content = (self.mapping_dir / "provider_team_mappings.csv").read_text(
            encoding="utf-8"
        )
        self.assertEqual(content.count("133601"), 1)

    def test_appends_after_file_without_trailing_newline(self):
        (self.mapping_dir / "provider_team_mappings.csv").write_text(
            "provider_name,provider_team_id,system_team_id,provider_team_name\n"
            "API-Football,34,TOP5_NEW,Newcastle United",
            encoding="utf-8",
        )
        client = FakeTheSportsDBClient(
            {"4328": [{"idTeam": "134777", "strTeam": "Newcastle United"}]}
        )

        update_thesportsdb_team_mappings(self.mapping_dir, client)

        self.assertIn(
            "API-Football,34,TOP5_NEW,Newcastle United\n"
            "TheSportsDB,134777,TOP5_NEW,Newcastle United\n",
            (self.mapping_dir / "provider_team_mappings.csv").read_text(
                encoding="utf-8"
            ),
        )


if __name__ == "__main__":
    unittest.main()
