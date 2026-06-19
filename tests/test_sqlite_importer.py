from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from football_advisor.sqlite_importer import import_sqlite_database


class RecordingConnection:
    def __init__(self):
        self.batches: list[tuple[str, list[tuple[object, ...]]]] = []
        self.executed: list[str] = []

    def execute(self, sql: str):
        self.executed.append(sql)

    def executemany(self, sql: str, rows):
        self.batches.append((sql, list(rows)))


class SQLiteImporterTests(unittest.TestCase):
    def test_imports_core_entities_matches_and_bookmaker_odds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sqlite_path = Path(temp_dir) / "source.sqlite"
            self._create_source_sqlite(sqlite_path)

            connection = RecordingConnection()
            result = import_sqlite_database(connection, sqlite_path)

        self.assertEqual(result.inserted_counts["leagues"], 1)
        self.assertEqual(result.inserted_counts["teams"], 2)
        self.assertEqual(result.inserted_counts["players"], 1)
        self.assertEqual(result.inserted_counts["matches"], 1)
        self.assertEqual(result.inserted_counts["contexts"], 1)
        self.assertEqual(result.inserted_counts["rolling_stats"], 0)
        self.assertEqual(result.inserted_counts["odds"], 2)
        self.assertEqual(connection.executed[0], "BEGIN TRANSACTION")
        self.assertEqual(connection.executed[-1], "COMMIT")

        league_rows = rows_for(connection, "core.dim_league_mapping")
        team_rows = rows_for(connection, "core.dim_team_mapping")
        player_rows = rows_for(connection, "core.dim_player_mapping")
        match_rows = rows_for(connection, "core.fact_match_schedule")
        odds_rows = rows_for(connection, "core.fact_odds_capital_flow")
        self.assertTrue(
            all(
                sql.strip().startswith("INSERT OR IGNORE INTO")
                for sql, _ in connection.batches
            )
        )

        self.assertEqual(league_rows[0][0], "SQLITE_LEAGUE_1729")
        self.assertEqual(league_rows[0][1], "England Premier League")
        self.assertEqual(league_rows[0][2], "England")
        self.assertEqual(team_rows[0][0], "SQLITE_TEAM_100")
        self.assertEqual(player_rows[0][0], "SQLITE_PLAYER_900")
        self.assertEqual(match_rows[0][0], "SQLITE_MATCH_555")
        self.assertEqual(match_rows[0][7], "SQLITE_TEAM_100")
        self.assertEqual(match_rows[0][8], "SQLITE_TEAM_200")

        bookmakers = {row[4] for row in odds_rows}
        self.assertEqual(bookmakers, {"B365", "BW"})
        b365 = [row for row in odds_rows if row[4] == "B365"][0]
        self.assertEqual(b365[0], "SQLITE_MATCH_555")
        self.assertEqual(b365[5:8], (2.1, 3.3, 3.6))
        self.assertEqual(b365[11], "SQLITE_EURO_SOCCER")

    def test_generates_prior_only_rolling_stats_and_h2h_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sqlite_path = Path(temp_dir) / "source.sqlite"
            self._create_source_sqlite(sqlite_path)
            connection = sqlite3.connect(sqlite_path)
            try:
                connection.execute("""
                    INSERT INTO Match VALUES (
                        11,
                        1,
                        1729,
                        '2015/2016',
                        2,
                        '2015-08-15 00:00:00',
                        556,
                        100,
                        200,
                        3,
                        0,
                        1.80,
                        3.40,
                        4.20,
                        NULL,
                        NULL,
                        NULL,
                        NULL,
                        NULL,
                        NULL
                    )
                    """)
                connection.commit()
            finally:
                connection.close()

            target = RecordingConnection()
            result = import_sqlite_database(target, sqlite_path)

        self.assertEqual(result.inserted_counts["matches"], 2)
        self.assertEqual(result.inserted_counts["contexts"], 2)
        self.assertEqual(result.inserted_counts["rolling_stats"], 2)

        rolling_rows = rows_for(target, "core.fact_team_rolling_stats")
        home_second = [
            row
            for row in rolling_rows
            if row[0] == "SQLITE_MATCH_556" and row[1] == "SQLITE_TEAM_100"
        ][0]
        away_second = [
            row
            for row in rolling_rows
            if row[0] == "SQLITE_MATCH_556" and row[1] == "SQLITE_TEAM_200"
        ][0]
        self.assertEqual(home_second[4], 2.0)
        self.assertEqual(home_second[5], 1.0)
        self.assertEqual(home_second[8], 3.0)
        self.assertEqual(home_second[13], "SQLITE_PRIOR_MATCHES_ONLY")
        self.assertEqual(away_second[4], 1.0)
        self.assertEqual(away_second[5], 2.0)
        self.assertEqual(away_second[8], 0.0)

        context_rows = rows_for(target, "core.fact_match_context_summary")
        second_context = [row for row in context_rows if row[0] == "SQLITE_MATCH_556"][
            0
        ]
        self.assertEqual(second_context[1:4], (1, 0, 0))

    def _create_source_sqlite(self, sqlite_path: Path) -> None:
        connection = sqlite3.connect(sqlite_path)
        try:
            connection.executescript("""
                CREATE TABLE Country (
                    id INTEGER PRIMARY KEY,
                    name TEXT
                );
                CREATE TABLE League (
                    id INTEGER PRIMARY KEY,
                    country_id INTEGER,
                    name TEXT
                );
                CREATE TABLE Team (
                    id INTEGER PRIMARY KEY,
                    team_api_id INTEGER,
                    team_long_name TEXT,
                    team_short_name TEXT
                );
                CREATE TABLE Player (
                    id INTEGER PRIMARY KEY,
                    player_api_id INTEGER,
                    player_name TEXT,
                    player_fifa_api_id INTEGER,
                    birthday TEXT,
                    height REAL,
                    weight REAL
                );
                CREATE TABLE Match (
                    id INTEGER PRIMARY KEY,
                    country_id INTEGER,
                    league_id INTEGER,
                    season TEXT,
                    stage INTEGER,
                    date TEXT,
                    match_api_id INTEGER,
                    home_team_api_id INTEGER,
                    away_team_api_id INTEGER,
                    home_team_goal INTEGER,
                    away_team_goal INTEGER,
                    B365H REAL,
                    B365D REAL,
                    B365A REAL,
                    BWH REAL,
                    BWD REAL,
                    BWA REAL,
                    IWH REAL,
                    IWD REAL,
                    IWA REAL
                );

                INSERT INTO Country VALUES (1, 'England');
                INSERT INTO League VALUES (1729, 1, 'England Premier League');
                INSERT INTO Team VALUES (1, 100, 'Arsenal', 'ARS');
                INSERT INTO Team VALUES (2, 200, 'Chelsea', 'CHE');
                INSERT INTO Player VALUES (1, 900, 'Sample Player', 901, '1990-01-01 00:00:00', 180.0, 75.0);
                INSERT INTO Match VALUES (
                    10,
                    1,
                    1729,
                    '2015/2016',
                    1,
                    '2015-08-08 00:00:00',
                    555,
                    100,
                    200,
                    2,
                    1,
                    2.10,
                    3.30,
                    3.60,
                    2.05,
                    3.25,
                    3.70,
                    NULL,
                    NULL,
                    NULL
                );
                """)
        finally:
            connection.close()


def rows_for(
    connection: RecordingConnection, table_name: str
) -> list[tuple[object, ...]]:
    for sql, rows in connection.batches:
        if table_name in sql:
            return rows
    return []


if __name__ == "__main__":
    unittest.main()
