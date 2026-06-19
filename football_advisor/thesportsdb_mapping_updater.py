from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol

from .thesportsdb_client import TheSportsDBClient


PROVIDER_NAME = "TheSportsDB"
TEAM_HEADER = [
    "provider_name",
    "provider_team_id",
    "system_team_id",
    "provider_team_name",
]
LEAGUE_HEADER = [
    "provider_name",
    "provider_league_id",
    "system_league_id",
    "provider_league_name",
]


class TeamListClient(Protocol):
    def list_teams_in_league(self, league: str) -> list[dict[str, object]]:
        ...


@dataclass(frozen=True)
class MappingCandidate:
    provider_name: str
    provider_team_id: str
    system_team_id: str
    provider_team_name: str


@dataclass(frozen=True)
class MappingUpdateResult:
    added: int = 0
    skipped_existing: int = 0
    skipped_unmatched: int = 0
    skipped_ambiguous: int = 0
    skipped_conflicting_provider_id: int = 0
    fetched_leagues: int = 0
    candidates: list[MappingCandidate] = field(default_factory=list)


def update_thesportsdb_team_mappings(
    mapping_dir: str | Path,
    client: TeamListClient,
    write: bool = True,
) -> MappingUpdateResult:
    root = Path(mapping_dir)
    team_path = root / "provider_team_mappings.csv"
    league_path = root / "provider_league_mappings.csv"

    team_rows = _read_csv(team_path, TEAM_HEADER)
    league_rows = _read_csv(league_path, LEAGUE_HEADER)
    existing_system_ids = {
        row["system_team_id"]
        for row in team_rows
        if row["provider_name"] == PROVIDER_NAME
    }
    existing_provider_ids = {
        row["provider_team_id"]
        for row in team_rows
        if row["provider_name"] == PROVIDER_NAME
    }
    target_aliases = _target_aliases(team_rows, existing_system_ids)
    league_ids = [
        row["provider_league_id"]
        for row in league_rows
        if row["provider_name"] == PROVIDER_NAME and row["provider_league_id"]
    ]

    official_teams = _fetch_official_teams(client, league_ids)
    candidates: list[MappingCandidate] = []
    skipped_unmatched = 0
    skipped_ambiguous = 0
    skipped_conflicting_provider_id = 0

    for system_team_id in sorted(target_aliases):
        aliases = target_aliases[system_team_id]
        matches = _find_unique_team_match(aliases, official_teams)
        if len(matches) == 1:
            team = matches[0]
            provider_team_id = str(team["idTeam"])
            if provider_team_id in existing_provider_ids:
                skipped_conflicting_provider_id += 1
                continue
            candidates.append(
                MappingCandidate(
                    provider_name=PROVIDER_NAME,
                    provider_team_id=provider_team_id,
                    system_team_id=system_team_id,
                    provider_team_name=str(team["strTeam"]),
                )
            )
        elif matches:
            skipped_ambiguous += 1
        else:
            skipped_unmatched += 1

    if write and candidates:
        _append_team_rows(team_path, candidates)

    return MappingUpdateResult(
        added=len(candidates),
        skipped_existing=len(existing_system_ids),
        skipped_unmatched=skipped_unmatched,
        skipped_ambiguous=skipped_ambiguous,
        skipped_conflicting_provider_id=skipped_conflicting_provider_id,
        fetched_leagues=len(league_ids),
        candidates=candidates,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="补齐 TheSportsDB provider_team_mappings.csv 映射。",
    )
    parser.add_argument(
        "--mapping-dir",
        default="data/provider_mappings",
        help="provider mapping CSV 目录。",
    )
    parser.add_argument(
        "--api-token",
        default="123",
        help="TheSportsDB v1 API key，免费测试 key 为 123。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只预览可追加映射，不写入 CSV。",
    )
    args = parser.parse_args(argv)

    client = TheSportsDBClient(api_token=args.api_token)
    result = update_thesportsdb_team_mappings(
        args.mapping_dir,
        client,
        write=not args.dry_run,
    )
    mode = "预览" if args.dry_run else "写入"
    print(
        f"{mode}完成：新增 {result.added} 条，已存在 {result.skipped_existing} 条，"
        f"未匹配 {result.skipped_unmatched} 条，多义 {result.skipped_ambiguous} 条，"
        f"provider ID 冲突 {result.skipped_conflicting_provider_id} 条，"
        f"扫描联赛 {result.fetched_leagues} 个。"
    )
    for candidate in result.candidates:
        print(
            f"{candidate.provider_name},{candidate.provider_team_id},"
            f"{candidate.system_team_id},{candidate.provider_team_name}"
        )
    return 0


def _read_csv(path: Path, required_fields: list[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [
            field for field in required_fields if field not in (reader.fieldnames or [])
        ]
        if missing:
            raise ValueError(f"{path.name} missing required fields: {', '.join(missing)}")
        return [
            {field: (row.get(field) or "").strip() for field in required_fields}
            for row in reader
        ]


def _target_aliases(
    team_rows: list[dict[str, str]],
    existing_system_ids: set[str],
) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {}
    for row in team_rows:
        system_team_id = row["system_team_id"]
        if (
            row["provider_name"] == PROVIDER_NAME
            or system_team_id in existing_system_ids
            or not _is_supported_system_team(system_team_id)
        ):
            continue
        aliases.setdefault(system_team_id, set()).add(row["provider_team_name"])
    return aliases


def _is_supported_system_team(system_team_id: str) -> bool:
    return system_team_id.startswith("TOP5_") or system_team_id.startswith("WC_TEAM_")


def _fetch_official_teams(
    client: TeamListClient,
    league_ids: Iterable[str],
) -> list[dict[str, str]]:
    teams_by_id: dict[str, dict[str, str]] = {}
    for league_id in league_ids:
        for team in client.list_teams_in_league(league_id):
            team_id = str(team.get("idTeam") or "").strip()
            team_name = str(team.get("strTeam") or "").strip()
            if not team_id or not team_name:
                continue
            teams_by_id[team_id] = {
                "idTeam": team_id,
                "strTeam": team_name,
                "strTeamAlternate": str(team.get("strTeamAlternate") or ""),
            }
    return list(teams_by_id.values())


def _find_unique_team_match(
    aliases: set[str],
    official_teams: list[dict[str, str]],
) -> list[dict[str, str]]:
    alias_keys = {_normalize_name(alias) for alias in aliases if alias}
    alias_compact_keys = {_compact_name(alias) for alias in aliases if alias}
    matches: list[dict[str, str]] = []
    for team in official_teams:
        team_names = _official_team_names(team)
        team_keys = {_normalize_name(name) for name in team_names if name}
        team_compact_keys = {_compact_name(name) for name in team_names if name}
        if alias_keys & team_keys or alias_compact_keys & team_compact_keys:
            matches.append(team)
    return matches


def _official_team_names(team: dict[str, str]) -> set[str]:
    names = {team["strTeam"]}
    for alt_name in team.get("strTeamAlternate", "").split(","):
        if alt_name.strip():
            names.add(alt_name.strip())
    return names


def _normalize_name(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    )
    lowered = ascii_value.lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


def _compact_name(value: str) -> str:
    tokens = [
        token
        for token in _normalize_name(value).split()
        if token
        not in {
            "fc",
            "cf",
            "club",
            "football",
            "calcio",
            "ss",
            "ssc",
            "ac",
            "bc",
            "afc",
            "ud",
            "de",
        }
    ]
    return "".join(tokens)


def _append_team_rows(path: Path, candidates: list[MappingCandidate]) -> None:
    if path.stat().st_size > 0:
        with path.open("rb") as handle:
            handle.seek(-1, 2)
            if handle.read(1) not in {b"\n", b"\r"}:
                with path.open("a", encoding="utf-8", newline="") as text_handle:
                    text_handle.write("\n")
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TEAM_HEADER, lineterminator="\n")
        for candidate in candidates:
            writer.writerow(
                {
                    "provider_name": candidate.provider_name,
                    "provider_team_id": candidate.provider_team_id,
                    "system_team_id": candidate.system_team_id,
                    "provider_team_name": candidate.provider_team_name,
                }
            )
