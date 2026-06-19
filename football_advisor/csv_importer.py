from __future__ import annotations

import csv
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REQUIRED_CSV_FILES = (
    "competitions.csv",
    "clubs.csv",
    "games.csv",
    "club_games.csv",
)


@dataclass(frozen=True)
class CSVImportResult:
    inserted_counts: dict[str, int]
    skipped_records: list[dict[str, str]] = field(default_factory=list)


class CSVImportError(RuntimeError):
    pass


def import_csv_directory(connection: Any, csv_dir: str | Path) -> CSVImportResult:
    root = Path(csv_dir)
    _require_files(root)

    competitions = _read_csv(root / "competitions.csv")
    clubs = _read_csv(root / "clubs.csv")
    games = _read_csv(root / "games.csv")
    club_games = _read_csv(root / "club_games.csv")

    league_rows = _build_league_rows(competitions)
    team_rows = _build_team_rows(clubs)
    team_ids = {row[0] for row in team_rows}

    match_rows, imported_game_ids, skipped_records = _build_match_rows(games, team_ids)
    game_dates = {_raw_game_id(str(row[0])): row[3] for row in match_rows}
    rolling_rows = _build_rolling_rows(
        club_games, game_dates, imported_game_ids, team_ids
    )

    _begin_transaction(connection)
    try:
        _insert_rows(
            connection,
            """
            INSERT INTO core.dim_league_mapping (
                system_league_id, league_standard_name, country
            ) VALUES (?, ?, ?)
            """,
            league_rows,
        )
        _insert_rows(
            connection,
            """
            INSERT INTO core.dim_team_mapping (
                system_team_id,
                team_standard_name,
                country,
                stadium_name
            ) VALUES (?, ?, ?, ?)
            """,
            team_rows,
        )
        _insert_rows(
            connection,
            """
            INSERT INTO core.fact_match_schedule (
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
            INSERT INTO core.fact_team_rolling_stats (
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
        _commit_transaction(connection)
    except Exception as exc:
        _rollback_transaction(connection)
        raise exc

    return CSVImportResult(
        inserted_counts={
            "leagues": len(league_rows),
            "teams": len(team_rows),
            "matches": len(match_rows),
            "rolling_stats": len(rolling_rows),
        },
        skipped_records=skipped_records,
    )


def _require_files(root: Path) -> None:
    missing = [name for name in REQUIRED_CSV_FILES if not (root / name).exists()]
    if missing:
        raise CSVImportError(f"Missing required CSV files: {', '.join(missing)}")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _build_league_rows(rows: list[dict[str, str]]) -> list[tuple[object, ...]]:
    result = []
    seen = set()
    for row in rows:
        league_id = _clean(row.get("competition_id"))
        if not league_id or league_id in seen:
            continue
        seen.add(league_id)
        result.append(
            (
                league_id,
                _clean(row.get("name")) or league_id,
                _clean(row.get("country_name")),
            )
        )
    return result


def _build_team_rows(rows: list[dict[str, str]]) -> list[tuple[object, ...]]:
    result = []
    seen = set()
    for row in rows:
        club_id = _clean(row.get("club_id"))
        if not club_id:
            continue
        system_team_id = _team_id(club_id)
        if system_team_id in seen:
            continue
        seen.add(system_team_id)
        result.append(
            (
                system_team_id,
                _clean(row.get("name")) or system_team_id,
                None,
                _clean(row.get("stadium_name")),
            )
        )
    return result


def _build_match_rows(
    rows: list[dict[str, str]],
    team_ids: set[object],
) -> tuple[list[tuple[object, ...]], set[str], list[dict[str, str]]]:
    result = []
    imported_game_ids: set[str] = set()
    skipped_records: list[dict[str, str]] = []

    for row in rows:
        game_id = _clean(row.get("game_id"))
        home_team_id = _team_id(_clean(row.get("home_club_id")))
        away_team_id = _team_id(_clean(row.get("away_club_id")))
        if not game_id:
            skipped_records.append({"record": "", "reason": "missing_game_id"})
            continue
        if home_team_id not in team_ids or away_team_id not in team_ids:
            skipped_records.append({"record": game_id, "reason": "unmapped_team"})
            continue

        match_id = _match_id(game_id)
        match_time = _parse_match_time(row.get("date"))
        home_score = _optional_int(row.get("home_club_goals"))
        away_score = _optional_int(row.get("away_club_goals"))
        status = (
            "FINISHED"
            if home_score is not None and away_score is not None
            else "PRE-MATCH"
        )
        result.append(
            (
                match_id,
                _clean(row.get("season")),
                _clean(row.get("competition_id")),
                match_time,
                status,
                home_score,
                away_score,
                home_team_id,
                away_team_id,
                _clean(row.get("referee")),
                None,
                None,
                None,
                None,
                "CSV_GAMES",
                match_time,
            )
        )
        imported_game_ids.add(game_id)

    return result, imported_game_ids, skipped_records


def _build_rolling_rows(
    rows: list[dict[str, str]],
    game_dates: dict[str, datetime],
    imported_game_ids: set[str],
    team_ids: set[object],
) -> list[tuple[object, ...]]:
    by_team: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        game_id = _clean(row.get("game_id"))
        team_id = _team_id(_clean(row.get("club_id")))
        if game_id in imported_game_ids and team_id in team_ids:
            by_team[team_id].append(row)

    result = []
    for team_id, team_games in by_team.items():
        history: deque[tuple[int, int, float]] = deque(maxlen=5)
        sorted_games = sorted(
            team_games,
            key=lambda item: (
                game_dates[_clean(item.get("game_id"))],
                _clean(item.get("game_id")),
            ),
        )
        for row in sorted_games:
            game_id = _clean(row.get("game_id"))
            match_time = game_dates[game_id]
            if history:
                goals_for = sum(item[0] for item in history) / len(history)
                goals_against = sum(item[1] for item in history) / len(history)
                points_per_match = sum(item[2] for item in history) / len(history)
                result.append(
                    (
                        _match_id(game_id),
                        team_id,
                        match_time - timedelta(days=1),
                        "CSV_TRANSFERMARKT_ROLLING",
                        round(goals_for, 4),
                        round(goals_against, 4),
                        None,
                        None,
                        round(points_per_match, 4),
                        round(max(goals_for / 1.4, 0.01), 4),
                        round(max(goals_against / 1.2, 0.01), 4),
                        0.5,
                        0.0,
                        "CSV_PRIOR_MATCHES_ONLY",
                    )
                )
            own_goals = _optional_int(row.get("own_goals"))
            opponent_goals = _optional_int(row.get("opponent_goals"))
            if own_goals is None or opponent_goals is None:
                continue
            history.append(
                (own_goals, opponent_goals, _points_for(own_goals, opponent_goals))
            )
    return result


def _insert_rows(connection: Any, sql: str, rows: list[tuple[object, ...]]) -> None:
    if rows:
        connection.executemany(sql, rows)


def _clean(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _team_id(value: str) -> str:
    return f"CLUB_{value}" if value else ""


def _match_id(value: str) -> str:
    return f"GAME_{value}"


def _raw_game_id(match_id: str) -> str:
    return match_id.removeprefix("GAME_")


def _parse_match_time(value: object | None) -> datetime:
    raw = _clean(value)
    if not raw:
        raise CSVImportError("Match row is missing date")
    return datetime.fromisoformat(raw)


def _optional_int(value: object | None) -> int | None:
    raw = _clean(value)
    if not raw:
        return None
    return int(raw)


def _points_for(goals_for: int, goals_against: int) -> float:
    if goals_for > goals_against:
        return 3.0
    if goals_for == goals_against:
        return 1.0
    return 0.0


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
