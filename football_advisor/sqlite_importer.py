from __future__ import annotations

import sqlite3
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

BOOKMAKERS_1X2 = ("B365", "BW", "IW", "LB", "PS", "WH", "SJ", "VC", "GB", "BS")


@dataclass(frozen=True)
class SQLiteImportResult:
    inserted_counts: dict[str, int]


def import_sqlite_database(
    connection: Any, sqlite_path: str | Path
) -> SQLiteImportResult:
    source_path = Path(sqlite_path)
    source = sqlite3.connect(source_path)
    try:
        source.row_factory = sqlite3.Row
        league_rows = _league_rows(source)
        team_rows = _team_rows(source)
        player_rows = _player_rows(source)
        match_rows = _match_rows(source)
        context_rows = _context_rows(source)
        rolling_rows = _rolling_rows(source)
        odds_rows = _odds_rows(source)
    finally:
        source.close()

    _begin_transaction(connection)
    try:
        _insert_rows(
            connection,
            """
            INSERT OR IGNORE INTO core.dim_league_mapping (
                system_league_id, league_standard_name, country
            ) VALUES (?, ?, ?)
            """,
            league_rows,
        )
        _insert_rows(
            connection,
            """
            INSERT OR IGNORE INTO core.dim_team_mapping (
                system_team_id,
                team_standard_name
            ) VALUES (?, ?)
            """,
            team_rows,
        )
        _insert_rows(
            connection,
            """
            INSERT OR IGNORE INTO core.dim_player_mapping (
                system_player_id,
                player_standard_name
            ) VALUES (?, ?)
            """,
            player_rows,
        )
        _insert_rows(
            connection,
            """
            INSERT OR IGNORE INTO core.fact_match_schedule (
                match_id,
                season,
                system_league_id,
                match_time,
                status,
                home_score,
                away_score,
                home_team_id,
                away_team_id,
                referee_id,
                weather_condition,
                temperature,
                rest_days_home,
                rest_days_away,
                source_provider,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            match_rows,
        )
        _insert_rows(
            connection,
            """
            INSERT OR IGNORE INTO core.fact_match_context_summary (
                match_id,
                h2h_home_wins,
                h2h_draws,
                h2h_away_wins,
                source_provider,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            context_rows,
        )
        _insert_rows(
            connection,
            """
            INSERT OR IGNORE INTO core.fact_team_rolling_stats (
                match_id,
                team_id,
                record_date,
                source_provider,
                rolling_goals_for,
                rolling_goals_against,
                rolling_xg_for,
                rolling_xg_against,
                recent_points_per_match,
                attack_strength,
                defense_strength,
                motivation_coefficient,
                key_missing_weight,
                data_quality_flag
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rolling_rows,
        )
        _insert_rows(
            connection,
            """
            INSERT OR IGNORE INTO core.fact_odds_capital_flow (
                match_id,
                snapshot_time,
                time_to_kickoff,
                odds_type,
                bookmaker_name,
                home_odds,
                draw_odds,
                away_odds,
                implied_home_probability,
                implied_draw_probability,
                implied_away_probability,
                source_provider
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            odds_rows,
        )
    except Exception:
        _rollback_transaction(connection)
        raise
    else:
        _commit_transaction(connection)

    return SQLiteImportResult(
        inserted_counts={
            "leagues": len(league_rows),
            "teams": len(team_rows),
            "players": len(player_rows),
            "matches": len(match_rows),
            "contexts": len(context_rows),
            "rolling_stats": len(rolling_rows),
            "odds": len(odds_rows),
        }
    )


def _league_rows(source: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [
        (_league_id(row["id"]), row["league_name"], row["country_name"])
        for row in source.execute("""
            SELECT League.id, League.name AS league_name, Country.name AS country_name
            FROM League
            LEFT JOIN Country ON Country.id = League.country_id
            ORDER BY League.id
            """)
    ]


def _team_rows(source: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [
        (
            _team_id(row["team_api_id"]),
            row["team_long_name"]
            or row["team_short_name"]
            or _team_id(row["team_api_id"]),
        )
        for row in source.execute("""
            SELECT team_api_id, team_long_name, team_short_name
            FROM Team
            WHERE team_api_id IS NOT NULL
            ORDER BY team_api_id
            """)
    ]


def _player_rows(source: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [
        (
            _player_id(row["player_api_id"]),
            row["player_name"] or _player_id(row["player_api_id"]),
        )
        for row in source.execute("""
            SELECT player_api_id, player_name
            FROM Player
            WHERE player_api_id IS NOT NULL
            ORDER BY player_api_id
            """)
    ]


def _match_rows(source: sqlite3.Connection) -> list[tuple[object, ...]]:
    rows = []
    for record in _match_records(source):
        rows.append(
            (
                record["match_id"],
                record["season"],
                _league_id(record["league_id"]),
                record["match_time"],
                "FINISHED",
                record["home_goals"],
                record["away_goals"],
                _team_id(record["home_team_api_id"]),
                _team_id(record["away_team_api_id"]),
                None,
                None,
                None,
                None,
                None,
                "SQLITE_EURO_SOCCER",
                record["match_time"],
            )
        )
    return rows


def _context_rows(source: sqlite3.Connection) -> list[tuple[object, ...]]:
    records = _match_records(source)
    rows = []
    prior_by_pair: dict[tuple[int, int], dict[tuple[int, int], int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for record in records:
        pair_key = tuple(
            sorted((record["home_team_api_id"], record["away_team_api_id"]))
        )
        pair_history = prior_by_pair[pair_key]
        home_wins = pair_history[
            (record["home_team_api_id"], record["away_team_api_id"])
        ]
        draws = pair_history[(0, 0)]
        away_wins = pair_history[
            (record["away_team_api_id"], record["home_team_api_id"])
        ]
        rows.append(
            (
                record["match_id"],
                home_wins,
                draws,
                away_wins,
                "SQLITE_EURO_SOCCER_CONTEXT",
                record["match_time"] - timedelta(days=1),
            )
        )
        if record["home_goals"] > record["away_goals"]:
            pair_history[(record["home_team_api_id"], record["away_team_api_id"])] += 1
        elif record["home_goals"] == record["away_goals"]:
            pair_history[(0, 0)] += 1
        else:
            pair_history[(record["away_team_api_id"], record["home_team_api_id"])] += 1
    return rows


def _rolling_rows(source: sqlite3.Connection) -> list[tuple[object, ...]]:
    by_team: dict[int, list[tuple[dict[str, Any], int, int]]] = defaultdict(list)
    for record in _match_records(source):
        by_team[record["home_team_api_id"]].append(
            (record, record["home_goals"], record["away_goals"])
        )
        by_team[record["away_team_api_id"]].append(
            (record, record["away_goals"], record["home_goals"])
        )

    rows = []
    for team_api_id, team_matches in by_team.items():
        history: deque[tuple[int, int, float]] = deque(maxlen=5)
        for record, goals_for, goals_against in sorted(
            team_matches,
            key=lambda item: (item[0]["match_time"], item[0]["source_id"]),
        ):
            if history:
                avg_for = sum(item[0] for item in history) / len(history)
                avg_against = sum(item[1] for item in history) / len(history)
                points = sum(item[2] for item in history) / len(history)
                rows.append(
                    (
                        record["match_id"],
                        _team_id(team_api_id),
                        record["match_time"] - timedelta(days=1),
                        "SQLITE_EURO_SOCCER_ROLLING",
                        round(avg_for, 4),
                        round(avg_against, 4),
                        None,
                        None,
                        round(points, 4),
                        round(max(avg_for / 1.4, 0.01), 4),
                        round(max(avg_against / 1.2, 0.01), 4),
                        0.5,
                        0.0,
                        "SQLITE_PRIOR_MATCHES_ONLY",
                    )
                )
            history.append(
                (goals_for, goals_against, _points_for(goals_for, goals_against))
            )
    return rows


def _odds_rows(source: sqlite3.Connection) -> list[tuple[object, ...]]:
    available_columns = _table_columns(source, "Match")
    select_columns = [
        "id",
        "match_api_id",
        "date",
        *(
            column
            for bookmaker in BOOKMAKERS_1X2
            for column in _bookmaker_columns(bookmaker)
            if column in available_columns
        ),
    ]
    rows = []
    for row in source.execute(
        f"SELECT {', '.join(select_columns)} FROM Match ORDER BY date, id"
    ):
        match_time = _parse_sqlite_datetime(row["date"])
        snapshot_time = match_time - timedelta(hours=1)
        for bookmaker in BOOKMAKERS_1X2:
            home_col, draw_col, away_col = _bookmaker_columns(bookmaker)
            if not {home_col, draw_col, away_col}.issubset(available_columns):
                continue
            home_odds = row[home_col]
            draw_odds = row[draw_col]
            away_odds = row[away_col]
            if not _valid_odds(home_odds, draw_odds, away_odds):
                continue
            implied_home = 1.0 / float(home_odds)
            implied_draw = 1.0 / float(draw_odds)
            implied_away = 1.0 / float(away_odds)
            rows.append(
                (
                    _match_id(row["match_api_id"] or row["id"]),
                    snapshot_time,
                    60,
                    "1X2",
                    bookmaker,
                    float(home_odds),
                    float(draw_odds),
                    float(away_odds),
                    implied_home,
                    implied_draw,
                    implied_away,
                    "SQLITE_EURO_SOCCER",
                )
            )
    return rows


def _match_records(source: sqlite3.Connection) -> list[dict[str, Any]]:
    records = []
    for row in source.execute("""
        SELECT
            id,
            match_api_id,
            season,
            league_id,
            date,
            home_team_api_id,
            away_team_api_id,
            home_team_goal,
            away_team_goal
        FROM Match
        WHERE home_team_api_id IS NOT NULL
          AND away_team_api_id IS NOT NULL
          AND league_id IS NOT NULL
          AND home_team_goal IS NOT NULL
          AND away_team_goal IS NOT NULL
        ORDER BY date, id
        """):
        records.append(
            {
                "source_id": row["id"],
                "match_id": _match_id(row["match_api_id"] or row["id"]),
                "season": row["season"],
                "league_id": row["league_id"],
                "match_time": _parse_sqlite_datetime(row["date"]),
                "home_team_api_id": row["home_team_api_id"],
                "away_team_api_id": row["away_team_api_id"],
                "home_goals": row["home_team_goal"],
                "away_goals": row["away_team_goal"],
            }
        )
    return records


def _goals_for_team(record: dict[str, Any], team_api_id: int) -> int:
    if record["home_team_api_id"] == team_api_id:
        return int(record["home_goals"])
    return int(record["away_goals"])


def _points_for(goals_for: int, goals_against: int) -> float:
    if goals_for > goals_against:
        return 3.0
    if goals_for == goals_against:
        return 1.0
    return 0.0


def _insert_rows(connection: Any, sql: str, rows: list[tuple[object, ...]]) -> None:
    if rows:
        connection.executemany(sql, rows)


def _begin_transaction(connection: Any) -> None:
    execute = getattr(connection, "execute", None)
    if execute:
        execute("BEGIN TRANSACTION")


def _commit_transaction(connection: Any) -> None:
    execute = getattr(connection, "execute", None)
    if execute:
        execute("COMMIT")


def _rollback_transaction(connection: Any) -> None:
    execute = getattr(connection, "execute", None)
    if execute:
        execute("ROLLBACK")


def _table_columns(source: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in source.execute(f'PRAGMA table_info("{table_name}")')}


def _bookmaker_columns(bookmaker: str) -> tuple[str, str, str]:
    return f"{bookmaker}H", f"{bookmaker}D", f"{bookmaker}A"


def _valid_odds(*values: object) -> bool:
    return all(value is not None and float(value) > 1.0 for value in values)


def _parse_sqlite_datetime(value: object) -> datetime:
    return datetime.fromisoformat(str(value).strip())


def _league_id(value: object) -> str:
    return f"SQLITE_LEAGUE_{value}"


def _team_id(value: object) -> str:
    return f"SQLITE_TEAM_{value}"


def _player_id(value: object) -> str:
    return f"SQLITE_PLAYER_{value}"


def _match_id(value: object) -> str:
    return f"SQLITE_MATCH_{value}"
