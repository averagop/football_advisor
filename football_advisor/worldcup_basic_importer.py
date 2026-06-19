from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

WORLD_CUP_LEAGUE_ID = "WC_WORLD_CUP_2026"
SOURCE_PROVIDER = "WORLD_CUP_2026_LOCAL_FILES"
STATS_QUALITY_FLAG = "WORLD_CUP_LAST5_SNAPSHOT"


@dataclass(frozen=True)
class WorldCupBasicImportResult:
    inserted_counts: dict[str, int] = field(default_factory=dict)
    skipped_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TeamIdentity:
    system_team_id: str
    standard_name: str
    country: str


@dataclass(frozen=True)
class TeamSnapshot:
    rating: float
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int

    @property
    def matches(self) -> int:
        return max(1, self.wins + self.draws + self.losses)

    @property
    def points_per_match(self) -> float:
        return (self.wins * 3 + self.draws) / self.matches


TEAM_ALIASES: dict[str, TeamIdentity] = {
    "algeria": TeamIdentity("WC_TEAM_ALG", "Algeria", "Algeria"),
    "argentina": TeamIdentity("WC_TEAM_ARG", "Argentina", "Argentina"),
    "australia": TeamIdentity("WC_TEAM_AUS", "Australia", "Australia"),
    "austria": TeamIdentity("WC_TEAM_AUT", "Austria", "Austria"),
    "belgium": TeamIdentity("WC_TEAM_BEL", "Belgium", "Belgium"),
    "bosnia and herzegovina": TeamIdentity("WC_TEAM_BIH", "Bosnia-Herzegovina", "Bosnia-Herzegovina"),
    "bosnia herzegovina": TeamIdentity("WC_TEAM_BIH", "Bosnia-Herzegovina", "Bosnia-Herzegovina"),
    "brazil": TeamIdentity("WC_TEAM_BRA", "Brazil", "Brazil"),
    "cabo verde": TeamIdentity("WC_TEAM_CPV", "Cape Verde", "Cape Verde"),
    "cape verde": TeamIdentity("WC_TEAM_CPV", "Cape Verde", "Cape Verde"),
    "canada": TeamIdentity("WC_TEAM_CAN", "Canada", "Canada"),
    "colombia": TeamIdentity("WC_TEAM_COL", "Colombia", "Colombia"),
    "congo dr": TeamIdentity("WC_TEAM_COD", "DR Congo", "DR Congo"),
    "croatia": TeamIdentity("WC_TEAM_CRO", "Croatia", "Croatia"),
    "curacao": TeamIdentity("WC_TEAM_CUW", "Curacao", "Curacao"),
    "czechia": TeamIdentity("WC_TEAM_CZE", "Czechia", "Czechia"),
    "cote d ivoire": TeamIdentity("WC_TEAM_CIV", "Ivory Coast", "Ivory Coast"),
    "ivory coast": TeamIdentity("WC_TEAM_CIV", "Ivory Coast", "Ivory Coast"),
    "dr congo": TeamIdentity("WC_TEAM_COD", "DR Congo", "DR Congo"),
    "ecuador": TeamIdentity("WC_TEAM_ECU", "Ecuador", "Ecuador"),
    "egypt": TeamIdentity("WC_TEAM_EGY", "Egypt", "Egypt"),
    "england": TeamIdentity("WC_TEAM_ENG", "England", "England"),
    "france": TeamIdentity("WC_TEAM_FRA", "France", "France"),
    "germany": TeamIdentity("WC_TEAM_GER", "Germany", "Germany"),
    "ghana": TeamIdentity("WC_TEAM_GHA", "Ghana", "Ghana"),
    "haiti": TeamIdentity("WC_TEAM_HAI", "Haiti", "Haiti"),
    "iran": TeamIdentity("WC_TEAM_IRN", "Iran", "Iran"),
    "iraq": TeamIdentity("WC_TEAM_IRQ", "Iraq", "Iraq"),
    "japan": TeamIdentity("WC_TEAM_JPN", "Japan", "Japan"),
    "jordan": TeamIdentity("WC_TEAM_JOR", "Jordan", "Jordan"),
    "korea republic": TeamIdentity("WC_TEAM_KOR", "South Korea", "South Korea"),
    "south korea": TeamIdentity("WC_TEAM_KOR", "South Korea", "South Korea"),
    "mexico": TeamIdentity("WC_TEAM_MEX", "Mexico", "Mexico"),
    "morocco": TeamIdentity("WC_TEAM_MAR", "Morocco", "Morocco"),
    "netherlands": TeamIdentity("WC_TEAM_NED", "Netherlands", "Netherlands"),
    "new zealand": TeamIdentity("WC_TEAM_NZL", "New Zealand", "New Zealand"),
    "norway": TeamIdentity("WC_TEAM_NOR", "Norway", "Norway"),
    "panama": TeamIdentity("WC_TEAM_PAN", "Panama", "Panama"),
    "paraguay": TeamIdentity("WC_TEAM_PAR", "Paraguay", "Paraguay"),
    "portugal": TeamIdentity("WC_TEAM_POR", "Portugal", "Portugal"),
    "qatar": TeamIdentity("WC_TEAM_QAT", "Qatar", "Qatar"),
    "saudi arabia": TeamIdentity("WC_TEAM_KSA", "Saudi Arabia", "Saudi Arabia"),
    "scotland": TeamIdentity("WC_TEAM_SCO", "Scotland", "Scotland"),
    "senegal": TeamIdentity("WC_TEAM_SEN", "Senegal", "Senegal"),
    "south africa": TeamIdentity("WC_TEAM_RSA", "South Africa", "South Africa"),
    "spain": TeamIdentity("WC_TEAM_ESP", "Spain", "Spain"),
    "sweden": TeamIdentity("WC_TEAM_SWE", "Sweden", "Sweden"),
    "switzerland": TeamIdentity("WC_TEAM_SUI", "Switzerland", "Switzerland"),
    "tunisia": TeamIdentity("WC_TEAM_TUN", "Tunisia", "Tunisia"),
    "turkiye": TeamIdentity("WC_TEAM_TUR", "Turkiye", "Turkiye"),
    "united states": TeamIdentity("WC_TEAM_USA", "USA", "United States"),
    "usa": TeamIdentity("WC_TEAM_USA", "USA", "United States"),
    "uruguay": TeamIdentity("WC_TEAM_URU", "Uruguay", "Uruguay"),
    "uzbekistan": TeamIdentity("WC_TEAM_UZB", "Uzbekistan", "Uzbekistan"),
}


def import_worldcup_basic_data(
    connection: Any,
    schedule_csv_path: str | Path | None = None,
    team_stats_csv_path: str | Path | None = None,
) -> WorldCupBasicImportResult:
    snapshots = _read_team_snapshots(team_stats_csv_path) if team_stats_csv_path else {}
    fixtures = _read_confirmed_group_fixtures(schedule_csv_path) if schedule_csv_path else []

    connection.execute("BEGIN TRANSACTION")
    try:
        _upsert_world_cup_league(connection)
        team_ids = _upsert_teams(connection, fixtures, snapshots)
        fixture_count = _upsert_fixtures(connection, fixtures)
        rolling_count = _upsert_rolling_stats(connection, fixtures, snapshots)
        context_count = _upsert_context_summaries(connection, fixtures, snapshots)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise

    bracket_slots = 0
    if schedule_csv_path:
        bracket_slots = _count_bracket_slots(Path(schedule_csv_path))

    return WorldCupBasicImportResult(
        inserted_counts={
            "teams": len(team_ids),
            "fixtures": fixture_count,
            "rolling_stats": rolling_count,
            "context_summaries": context_count,
        },
        skipped_counts={"bracket_slots": bracket_slots},
    )


def _upsert_world_cup_league(connection: Any) -> None:
    connection.execute(
        """
        INSERT INTO core.dim_league_mapping (
            system_league_id, league_standard_name, country, updated_at
        ) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (system_league_id) DO UPDATE SET
            league_standard_name = excluded.league_standard_name,
            country = excluded.country,
            updated_at = excluded.updated_at
        """,
        [WORLD_CUP_LEAGUE_ID, "FIFA World Cup 2026", "International"],
    )


def _upsert_teams(
    connection: Any,
    fixtures: list[dict[str, Any]],
    snapshots: dict[str, TeamSnapshot],
) -> set[str]:
    identities: dict[str, TeamIdentity] = {}
    for fixture in fixtures:
        identities[fixture["home"].system_team_id] = fixture["home"]
        identities[fixture["away"].system_team_id] = fixture["away"]
    for team_id in snapshots:
        identity = _identity_from_team_id(team_id)
        if identity:
            identities[team_id] = identity

    for identity in identities.values():
        snapshot = snapshots.get(identity.system_team_id)
        elo = snapshot.rating if snapshot else 1500.0
        connection.execute(
            """
            INSERT INTO core.dim_team_mapping (
                system_team_id, team_standard_name, country, elo_rating_base, updated_at
            ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (system_team_id) DO UPDATE SET
                team_standard_name = excluded.team_standard_name,
                country = excluded.country,
                elo_rating_base = excluded.elo_rating_base,
                updated_at = excluded.updated_at
            """,
            [identity.system_team_id, identity.standard_name, identity.country, elo],
        )
    return set(identities)


def _upsert_fixtures(connection: Any, fixtures: list[dict[str, Any]]) -> int:
    for fixture in fixtures:
        connection.execute(
            """
            INSERT INTO core.fact_match_schedule (
                match_id,
                season,
                system_league_id,
                match_time,
                home_team_id,
                away_team_id,
                status,
                is_neutral_venue,
                source_provider,
                updated_at
            ) VALUES (?, '2026', ?, ?, ?, ?, 'PRE-MATCH', TRUE, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (match_id) DO UPDATE SET
                season = excluded.season,
                system_league_id = excluded.system_league_id,
                match_time = excluded.match_time,
                home_team_id = excluded.home_team_id,
                away_team_id = excluded.away_team_id,
                status = excluded.status,
                is_neutral_venue = excluded.is_neutral_venue,
                source_provider = excluded.source_provider,
                updated_at = excluded.updated_at
            """,
            [
                fixture["match_id"],
                WORLD_CUP_LEAGUE_ID,
                fixture["match_time"],
                fixture["home"].system_team_id,
                fixture["away"].system_team_id,
                SOURCE_PROVIDER,
            ],
        )
    return len(fixtures)


def _upsert_rolling_stats(
    connection: Any,
    fixtures: list[dict[str, Any]],
    snapshots: dict[str, TeamSnapshot],
) -> int:
    rows = []
    for fixture in fixtures:
        for side in ("home", "away"):
            identity = fixture[side]
            snapshot = snapshots.get(identity.system_team_id)
            if not snapshot:
                continue
            rows.append(
                (
                    fixture["match_id"],
                    identity.system_team_id,
                    fixture["match_time"] - timedelta(days=1),
                    SOURCE_PROVIDER,
                    snapshot.goals_for / snapshot.matches,
                    snapshot.goals_against / snapshot.matches,
                    snapshot.points_per_match,
                    _attack_strength(snapshot),
                    _defense_strength(snapshot),
                    0.5,
                    0.0,
                    STATS_QUALITY_FLAG,
                )
            )
    if not rows:
        return 0
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
        ON CONFLICT (match_id, team_id, record_date, source_provider) DO UPDATE SET
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
    return len(rows)


def _upsert_context_summaries(
    connection: Any,
    fixtures: list[dict[str, Any]],
    snapshots: dict[str, TeamSnapshot],
) -> int:
    rows = []
    for fixture in fixtures:
        home_snapshot = snapshots.get(fixture["home"].system_team_id)
        away_snapshot = snapshots.get(fixture["away"].system_team_id)
        if not home_snapshot and not away_snapshot:
            continue
        rows.append(
            (
                fixture["match_id"],
                home_snapshot.wins if home_snapshot else 0,
                home_snapshot.draws if home_snapshot else 0,
                home_snapshot.losses if home_snapshot else 0,
                away_snapshot.wins if away_snapshot else 0,
                away_snapshot.draws if away_snapshot else 0,
                away_snapshot.losses if away_snapshot else 0,
                home_snapshot.points_per_match if home_snapshot else None,
                away_snapshot.points_per_match if away_snapshot else None,
                SOURCE_PROVIDER,
            )
        )
    if not rows:
        return 0
    connection.executemany(
        """
        INSERT INTO core.fact_match_context_summary (
            match_id,
            h2h_home_wins,
            h2h_draws,
            h2h_away_wins,
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
        ) VALUES (?, 0, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
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
        rows,
    )
    return len(rows)


def _read_confirmed_group_fixtures(path: str | Path) -> list[dict[str, Any]]:
    fixtures = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("status") != "confirmed_group_fixture":
                continue
            home = _resolve_team(row["team_a"])
            away = _resolve_team(row["team_b"])
            fixtures.append(
                {
                    "match_id": f"WC2026_M{int(row['match_number']):03d}",
                    "match_time": _parse_et_datetime(row["date"], row["time_et"]),
                    "home": home,
                    "away": away,
                }
            )
    return fixtures


def _read_team_snapshots(path: str | Path) -> dict[str, TeamSnapshot]:
    snapshots: dict[str, TeamSnapshot] = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            identity = _resolve_team(row["team"])
            snapshots[identity.system_team_id] = TeamSnapshot(
                rating=float(row["rating"]),
                wins=int(row["W"]),
                draws=int(row["D"]),
                losses=int(row["L"]),
                goals_for=int(row["GF"]),
                goals_against=int(row["GA"]),
            )
    return snapshots


def _count_bracket_slots(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return sum(1 for row in csv.DictReader(handle) if row.get("status") == "bracket_slot")


def _parse_et_datetime(date_value: str, time_value: str) -> datetime:
    local_dt = datetime.strptime(f"{date_value} {time_value}", "%Y-%m-%d %H:%M")
    eastern = local_dt.replace(tzinfo=ZoneInfo("America/New_York"))
    return eastern.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _resolve_team(name: str) -> TeamIdentity:
    normalized = _normalize_team_name(name)
    if normalized in TEAM_ALIASES:
        return TEAM_ALIASES[normalized]
    slug = re.sub(r"[^A-Z0-9]+", "", normalized.upper())
    team_code = (slug[:12] or "UNKNOWN").ljust(3, "X")
    standard_name = name.strip()
    return TeamIdentity(f"WC_TEAM_{team_code}", standard_name, standard_name)


def _identity_from_team_id(team_id: str) -> TeamIdentity | None:
    for identity in TEAM_ALIASES.values():
        if identity.system_team_id == team_id:
            return identity
    return None


def _normalize_team_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name.strip().replace("’", "'"))
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_name.lower()).strip()


def _attack_strength(snapshot: TeamSnapshot) -> float:
    return _clamp((snapshot.goals_for / snapshot.matches) / 1.4, 0.65, 1.55)


def _defense_strength(snapshot: TeamSnapshot) -> float:
    return _clamp((snapshot.goals_against / snapshot.matches) / 1.1, 0.65, 1.55)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
