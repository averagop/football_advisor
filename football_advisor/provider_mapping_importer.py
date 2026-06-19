from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProviderMappingImportResult:
    league_inserted: int = 0
    league_skipped: int = 0
    team_inserted: int = 0
    team_skipped: int = 0

    def as_details(self) -> dict[str, str]:
        return {
            "provider_league_mapping_inserted": str(self.league_inserted),
            "provider_league_mapping_skipped": str(self.league_skipped),
            "provider_team_mapping_inserted": str(self.team_inserted),
            "provider_team_mapping_skipped": str(self.team_skipped),
        }


def import_provider_mappings(
    connection: Any,
    mapping_dir: str | Path,
) -> ProviderMappingImportResult:
    root = Path(mapping_dir)
    league_rows = _read_csv(
        root / "provider_league_mappings.csv",
        [
            "provider_name",
            "provider_league_id",
            "system_league_id",
            "provider_league_name",
        ],
    )
    team_rows = _read_csv(
        root / "provider_team_mappings.csv",
        [
            "provider_name",
            "provider_team_id",
            "system_team_id",
            "provider_team_name",
        ],
    )

    league_inserted, league_skipped = _upsert_league_mappings(
        connection, league_rows
    )
    team_inserted, team_skipped = _upsert_team_mappings(connection, team_rows)
    return ProviderMappingImportResult(
        league_inserted=league_inserted,
        league_skipped=league_skipped,
        team_inserted=team_inserted,
        team_skipped=team_skipped,
    )


def _read_csv(path: Path, required_fields: list[str]) -> list[dict[str, str]]:
    if not path.exists():
        return []
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


def _upsert_league_mappings(
    connection: Any,
    rows: list[dict[str, str]],
) -> tuple[int, int]:
    inserted = 0
    skipped = 0
    for row in rows:
        if (
            not row["provider_name"]
            or not row["provider_league_id"]
            or not row["system_league_id"]
        ):
            skipped += 1
            continue
        exists = connection.execute(
            "SELECT 1 FROM core.dim_league_mapping WHERE system_league_id = ?",
            [row["system_league_id"]],
        ).fetchone()
        if not exists:
            skipped += 1
            continue
        connection.execute(
            """
            INSERT INTO core.dim_provider_league_mapping (
                provider_name, provider_league_id, system_league_id,
                provider_league_name, updated_at
            ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (provider_name, provider_league_id) DO UPDATE SET
                system_league_id = excluded.system_league_id,
                provider_league_name = excluded.provider_league_name,
                updated_at = excluded.updated_at
            """,
            [
                row["provider_name"],
                row["provider_league_id"],
                row["system_league_id"],
                row["provider_league_name"],
            ],
        )
        inserted += 1
    return inserted, skipped


def _upsert_team_mappings(
    connection: Any,
    rows: list[dict[str, str]],
) -> tuple[int, int]:
    inserted = 0
    skipped = 0
    for row in rows:
        if (
            not row["provider_name"]
            or not row["provider_team_id"]
            or not row["system_team_id"]
        ):
            skipped += 1
            continue
        exists = connection.execute(
            "SELECT 1 FROM core.dim_team_mapping WHERE system_team_id = ?",
            [row["system_team_id"]],
        ).fetchone()
        if not exists:
            skipped += 1
            continue
        connection.execute(
            """
            INSERT INTO core.dim_provider_team_mapping (
                provider_name, provider_team_id, system_team_id,
                provider_team_name, updated_at
            ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (provider_name, provider_team_id) DO UPDATE SET
                system_team_id = excluded.system_team_id,
                provider_team_name = excluded.provider_team_name,
                updated_at = excluded.updated_at
            """,
            [
                row["provider_name"],
                row["provider_team_id"],
                row["system_team_id"],
                row["provider_team_name"],
            ],
        )
        inserted += 1
        _delete_stale_same_name_team_mappings(connection, row)
    return inserted, skipped


def _delete_stale_same_name_team_mappings(
    connection: Any,
    row: dict[str, str],
) -> None:
    if not row["provider_team_name"]:
        return
    connection.execute(
        """
        DELETE FROM core.dim_provider_team_mapping
        WHERE provider_name = ?
          AND system_team_id = ?
          AND lower(provider_team_name) = lower(?)
          AND provider_team_id <> ?
        """,
        [
            row["provider_name"],
            row["system_team_id"],
            row["provider_team_name"],
            row["provider_team_id"],
        ],
    )
