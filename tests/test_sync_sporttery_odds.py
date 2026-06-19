from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import duckdb

from football_advisor.db_schema import apply_schema
from scripts import sync_sporttery_odds


class SyncSportteryOddsTests(unittest.TestCase):
    def test_missing_base_url_uses_public_official_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "sporttery.duckdb"
            env_path = Path(tmp) / ".env"
            env_path.write_text(f"FOOTBALL_DUCKDB_PATH={db_path}\n", encoding="utf-8")
            connection = MagicMock()
            connection.__enter__.return_value = connection
            connection.__exit__.return_value = False

            with (
                patch.object(sync_sporttery_odds.duckdb, "connect", return_value=connection) as connect,
                patch.object(sync_sporttery_odds, "apply_schema"),
                patch.object(sync_sporttery_odds, "ensure_sporttery_seed_entities"),
                patch.object(sync_sporttery_odds, "import_provider_mappings") as import_mappings,
                patch.object(sync_sporttery_odds.SportteryClient, "fetch_football_odds") as fetch,
            ):
                import_mappings.return_value.as_details.return_value = {}
                fetch.return_value = {
                    "status": "success",
                    "match_count": 1,
                    "odds_count": 2,
                    "merged_match_count": 1,
                    "merged_odds_count": 2,
                    "skipped_unmapped_count": 0,
                }
                result = sync_sporttery_odds.run_sync(env_path=env_path)

        self.assertEqual(result["status"], "success")
        self.assertTrue(connect.called)

    def test_configured_sync_applies_schema_and_returns_redacted_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "sporttery.duckdb"
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        f"FOOTBALL_DUCKDB_PATH={db_path}",
                        "FOOTBALL_SPORTTERY_BASE_URL=https://www.sporttery.cn",
                    ]
                ),
                encoding="utf-8",
            )
            connection = MagicMock()
            connection.__enter__.return_value = connection
            connection.__exit__.return_value = False

            with (
                patch.object(sync_sporttery_odds.duckdb, "connect", return_value=connection),
                patch.object(sync_sporttery_odds, "apply_schema") as apply_schema,
                patch.object(sync_sporttery_odds, "ensure_sporttery_seed_entities") as ensure_entities,
                patch.object(sync_sporttery_odds, "import_provider_mappings") as import_mappings,
                patch.object(sync_sporttery_odds.SportteryClient, "fetch_football_odds") as fetch,
            ):
                import_mappings.return_value.as_details.return_value = {
                    "provider_league_mapping_inserted": "1",
                    "provider_league_mapping_skipped": "0",
                    "provider_team_mapping_inserted": "8",
                    "provider_team_mapping_skipped": "0",
                }
                fetch.return_value = {
                    "status": "success",
                    "match_count": 2,
                    "odds_count": 4,
                    "merged_match_count": 1,
                    "merged_odds_count": 2,
                    "skipped_unmapped_count": 1,
                }
                result = sync_sporttery_odds.run_sync(env_path=env_path)

        apply_schema.assert_called_once_with(connection)
        ensure_entities.assert_called_once_with(connection)
        import_mappings.assert_called_once()
        fetch.assert_called_once()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["match_count"], 2)
        self.assertEqual(result["merged_odds_count"], 2)
        self.assertEqual(result["mapping"]["provider_team_mapping_inserted"], "8")
        self.assertNotIn("https://www.sporttery.cn", str(result))

    def test_ensure_sporttery_seed_entities_is_duckdb_compatible(self):
        connection = duckdb.connect(":memory:")
        try:
            apply_schema(connection)
            sync_sporttery_odds.ensure_sporttery_seed_entities(connection)

            league = connection.execute(
                """
                SELECT league_standard_name
                FROM core.dim_league_mapping
                WHERE system_league_id = 'INTL_FRIENDLY'
                """
            ).fetchone()
            team = connection.execute(
                """
                SELECT team_standard_name
                FROM core.dim_team_mapping
                WHERE system_team_id = 'WC_TEAM_SLO'
                """
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(league, ("International Friendly",))
        self.assertEqual(team, ("Slovenia",))


if __name__ == "__main__":
    unittest.main()
