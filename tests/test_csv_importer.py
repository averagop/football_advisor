from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from football_advisor.csv_importer import CSVImportError, import_csv_directory


class RecordingConnection:
    def __init__(self):
        self.batches: list[tuple[str, list[tuple[object, ...]]]] = []
        self.executed: list[str] = []

    def execute(self, sql: str):
        self.executed.append(sql)

    def executemany(self, sql: str, rows):
        self.batches.append((sql, list(rows)))


class FailingAfterTeamsConnection(RecordingConnection):
    def executemany(self, sql: str, rows):
        super().executemany(sql, rows)
        if "core.fact_match_schedule" in sql:
            raise RuntimeError("match insert failed")


class CSVImporterTests(unittest.TestCase):
    def test_imports_core_tables_and_uses_prior_matches_for_rolling_stats(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_dir = Path(temp_dir)
            write_minimal_csv_fixture(csv_dir)

            connection = RecordingConnection()
            result = import_csv_directory(connection, csv_dir)

        self.assertEqual(result.inserted_counts["leagues"], 1)
        self.assertEqual(result.inserted_counts["teams"], 2)
        self.assertEqual(result.inserted_counts["matches"], 2)
        self.assertEqual(result.inserted_counts["rolling_stats"], 2)
        self.assertEqual(result.skipped_records, [])
        self.assertEqual(connection.executed[0], "BEGIN TRANSACTION")
        self.assertEqual(connection.executed[-1], "COMMIT")

        league_rows = rows_for(connection, "core.dim_league_mapping")
        team_rows = rows_for(connection, "core.dim_team_mapping")
        match_rows = rows_for(connection, "core.fact_match_schedule")
        rolling_rows = rows_for(connection, "core.fact_team_rolling_stats")

        self.assertEqual(league_rows[0][0], "L1")
        self.assertEqual(team_rows[0][0], "CLUB_10")
        self.assertEqual(match_rows[0][0], "GAME_1")
        self.assertEqual(match_rows[0][7], "CLUB_10")
        self.assertEqual(match_rows[0][8], "CLUB_20")

        arsenal_second_match = [
            row for row in rolling_rows if row[0] == "GAME_2" and row[1] == "CLUB_10"
        ][0]
        self.assertEqual(arsenal_second_match[3], "CSV_TRANSFERMARKT_ROLLING")
        self.assertEqual(arsenal_second_match[4], 2.0)
        self.assertEqual(arsenal_second_match[5], 1.0)
        self.assertEqual(arsenal_second_match[8], 3.0)
        self.assertEqual(arsenal_second_match[13], "CSV_PRIOR_MATCHES_ONLY")

    def test_rolls_back_transaction_when_insert_batch_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_dir = Path(temp_dir)
            write_minimal_csv_fixture(csv_dir)
            connection = FailingAfterTeamsConnection()

            with self.assertRaises(RuntimeError):
                import_csv_directory(connection, csv_dir)

        self.assertEqual(connection.executed[0], "BEGIN TRANSACTION")
        self.assertNotIn("COMMIT", connection.executed)
        self.assertEqual(connection.executed[-1], "ROLLBACK")

    def test_skips_matches_with_unmapped_teams_without_inventing_entities(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_dir = Path(temp_dir)
            (csv_dir / "competitions.csv").write_text(
                "competition_id,name,country_name\nL1,Premier League,England\n",
                encoding="utf-8",
            )
            (csv_dir / "clubs.csv").write_text(
                "club_id,name,domestic_competition_id,stadium_name\n10,Arsenal,L1,Emirates Stadium\n",
                encoding="utf-8",
            )
            (csv_dir / "games.csv").write_text(
                "\n".join(
                    [
                        "game_id,competition_id,season,date,home_club_id,away_club_id,home_club_goals,away_club_goals,stadium,referee,home_club_name,away_club_name",
                        "1,L1,2025,2025-08-01,10,999,2,1,Emirates Stadium,Ref One,Arsenal,Unknown FC",
                    ]
                ),
                encoding="utf-8",
            )
            (csv_dir / "club_games.csv").write_text(
                "game_id,club_id,own_goals,opponent_id,opponent_goals,hosting,is_win\n",
                encoding="utf-8",
            )

            connection = RecordingConnection()
            result = import_csv_directory(connection, csv_dir)

        self.assertEqual(result.inserted_counts["matches"], 0)
        self.assertEqual(len(result.skipped_records), 1)
        self.assertEqual(result.skipped_records[0]["reason"], "unmapped_team")

    def test_missing_required_csv_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = RecordingConnection()

            with self.assertRaises(CSVImportError):
                import_csv_directory(connection, Path(temp_dir))


def rows_for(
    connection: RecordingConnection, table_name: str
) -> list[tuple[object, ...]]:
    for sql, rows in connection.batches:
        if table_name in sql:
            return rows
    return []


def write_minimal_csv_fixture(csv_dir: Path) -> None:
    (csv_dir / "competitions.csv").write_text(
        "\n".join(
            [
                "competition_id,name,country_name",
                "L1,Premier League,England",
            ]
        ),
        encoding="utf-8",
    )
    (csv_dir / "clubs.csv").write_text(
        "\n".join(
            [
                "club_id,name,domestic_competition_id,stadium_name",
                "10,Arsenal,L1,Emirates Stadium",
                "20,Chelsea,L1,Stamford Bridge",
            ]
        ),
        encoding="utf-8",
    )
    (csv_dir / "games.csv").write_text(
        "\n".join(
            [
                "game_id,competition_id,season,date,home_club_id,away_club_id,home_club_goals,away_club_goals,stadium,referee,home_club_name,away_club_name",
                "1,L1,2025,2025-08-01,10,20,2,1,Emirates Stadium,Ref One,Arsenal,Chelsea",
                "2,L1,2025,2025-08-08,10,20,3,0,Emirates Stadium,Ref Two,Arsenal,Chelsea",
            ]
        ),
        encoding="utf-8",
    )
    (csv_dir / "club_games.csv").write_text(
        "\n".join(
            [
                "game_id,club_id,own_goals,opponent_id,opponent_goals,hosting,is_win",
                "1,10,2,20,1,Home,1",
                "1,20,1,10,2,Away,0",
                "2,10,3,20,0,Home,1",
                "2,20,0,10,3,Away,0",
            ]
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
