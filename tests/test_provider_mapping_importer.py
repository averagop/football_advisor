from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from football_advisor.db_schema import apply_schema
from football_advisor.provider_mapping_importer import import_provider_mappings


class ProviderMappingImporterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.mapping_dir = self.root / "provider_mappings"
        self.mapping_dir.mkdir()
        self.connection = duckdb.connect(":memory:")
        apply_schema(self.connection)
        self.connection.execute(
            """
            INSERT INTO core.dim_league_mapping (system_league_id, league_standard_name)
            VALUES ('SYS_EPL', 'English Premier League');

            INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name)
            VALUES
                ('SYS_ARS', 'Arsenal'),
                ('SYS_CHE', 'Chelsea');
            """
        )

    def tearDown(self):
        self.connection.close()
        self.temp_dir.cleanup()

    def test_imports_valid_provider_mappings(self):
        (self.mapping_dir / "provider_league_mappings.csv").write_text(
            "provider_name,provider_league_id,system_league_id,provider_league_name\n"
            "API-Football,39,SYS_EPL,Premier League\n",
            encoding="utf-8",
        )
        (self.mapping_dir / "provider_team_mappings.csv").write_text(
            "provider_name,provider_team_id,system_team_id,provider_team_name\n"
            "API-Football,42,SYS_ARS,Arsenal\n"
            "API-Football,49,SYS_CHE,Chelsea\n",
            encoding="utf-8",
        )

        result = import_provider_mappings(self.connection, self.mapping_dir)

        self.assertEqual(result.league_inserted, 1)
        self.assertEqual(result.team_inserted, 2)
        self.assertEqual(result.league_skipped, 0)
        self.assertEqual(result.team_skipped, 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT system_team_id FROM core.dim_provider_team_mapping "
                "WHERE provider_name = 'API-Football' AND provider_team_id = '42'"
            ).fetchone()[0],
            "SYS_ARS",
        )

    def test_skips_rows_with_missing_internal_ids(self):
        (self.mapping_dir / "provider_league_mappings.csv").write_text(
            "provider_name,provider_league_id,system_league_id,provider_league_name\n"
            "API-Football,39,SYS_UNKNOWN,Premier League\n",
            encoding="utf-8",
        )
        (self.mapping_dir / "provider_team_mappings.csv").write_text(
            "provider_name,provider_team_id,system_team_id,provider_team_name\n"
            "API-Football,42,SYS_UNKNOWN,Arsenal\n",
            encoding="utf-8",
        )

        result = import_provider_mappings(self.connection, self.mapping_dir)

        self.assertEqual(result.league_inserted, 0)
        self.assertEqual(result.team_inserted, 0)
        self.assertEqual(result.league_skipped, 1)
        self.assertEqual(result.team_skipped, 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM core.dim_provider_team_mapping"
            ).fetchone()[0],
            0,
        )

    def test_duplicate_provider_key_updates_existing_mapping(self):
        (self.mapping_dir / "provider_league_mappings.csv").write_text(
            "provider_name,provider_league_id,system_league_id,provider_league_name\n",
            encoding="utf-8",
        )
        (self.mapping_dir / "provider_team_mappings.csv").write_text(
            "provider_name,provider_team_id,system_team_id,provider_team_name\n"
            "API-Football,42,SYS_ARS,Old Arsenal\n",
            encoding="utf-8",
        )
        import_provider_mappings(self.connection, self.mapping_dir)
        (self.mapping_dir / "provider_team_mappings.csv").write_text(
            "provider_name,provider_team_id,system_team_id,provider_team_name\n"
            "API-Football,42,SYS_ARS,Arsenal FC\n",
            encoding="utf-8",
        )

        result = import_provider_mappings(self.connection, self.mapping_dir)

        self.assertEqual(result.team_inserted, 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT provider_team_name FROM core.dim_provider_team_mapping "
                "WHERE provider_name = 'API-Football' AND provider_team_id = '42'"
            ).fetchone()[0],
            "Arsenal FC",
        )

    def test_import_prunes_stale_same_name_team_mapping_after_id_correction(self):
        self.connection.execute(
            """
            INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name)
            VALUES ('WC_TEAM_ITA', 'Italy');

            INSERT INTO core.dim_provider_team_mapping (
                provider_name, provider_team_id, system_team_id, provider_team_name
            ) VALUES ('API-Football', '13', 'WC_TEAM_ITA', 'Italy');
            """
        )
        (self.mapping_dir / "provider_league_mappings.csv").write_text(
            "provider_name,provider_league_id,system_league_id,provider_league_name\n",
            encoding="utf-8",
        )
        (self.mapping_dir / "provider_team_mappings.csv").write_text(
            "provider_name,provider_team_id,system_team_id,provider_team_name\n"
            "API-Football,768,WC_TEAM_ITA,Italy\n",
            encoding="utf-8",
        )

        result = import_provider_mappings(self.connection, self.mapping_dir)

        self.assertEqual(result.team_inserted, 1)
        self.assertEqual(
            self.connection.execute(
                """
                SELECT provider_team_id, system_team_id, provider_team_name
                FROM core.dim_provider_team_mapping
                WHERE provider_name = 'API-Football'
                  AND system_team_id = 'WC_TEAM_ITA'
                """
            ).fetchall(),
            [("768", "WC_TEAM_ITA", "Italy")],
        )

    def test_imports_world_cup_api_football_mappings(self):
        self.connection.execute(
            """
            INSERT INTO core.dim_league_mapping (system_league_id, league_standard_name)
            VALUES ('WC_WORLD_CUP_2026', 'FIFA World Cup 2026');

            INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name)
            VALUES
                ('WC_TEAM_USA', 'USA'),
                ('WC_TEAM_MEX', 'Mexico');
            """
        )
        (self.mapping_dir / "provider_league_mappings.csv").write_text(
            "provider_name,provider_league_id,system_league_id,provider_league_name\n"
            "API-Football,1,WC_WORLD_CUP_2026,World Cup\n",
            encoding="utf-8",
        )
        (self.mapping_dir / "provider_team_mappings.csv").write_text(
            "provider_name,provider_team_id,system_team_id,provider_team_name\n"
            "API-Football,15,WC_TEAM_USA,USA\n"
            "API-Football,14,WC_TEAM_MEX,Mexico\n",
            encoding="utf-8",
        )

        result = import_provider_mappings(self.connection, self.mapping_dir)

        self.assertEqual(result.league_inserted, 1)
        self.assertEqual(result.team_inserted, 2)
        self.assertEqual(result.league_skipped, 0)
        self.assertEqual(result.team_skipped, 0)

    def test_imports_sporttery_official_web_mappings_from_repository_csv(self):
        self.connection.execute(
            """
            INSERT INTO core.dim_league_mapping (system_league_id, league_standard_name)
            VALUES ('INTL_FRIENDLY', 'International Friendly');

            INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name)
            VALUES
                ('WC_TEAM_CRO', 'Croatia'),
                ('WC_TEAM_SLO', 'Slovenia'),
                ('WC_TEAM_MAR', 'Morocco'),
                ('WC_TEAM_NOR', 'Norway'),
                ('WC_TEAM_GRE', 'Greece'),
                ('WC_TEAM_ITA', 'Italy'),
                ('WC_TEAM_COL', 'Colombia'),
                ('WC_TEAM_JOR', 'Jordan');
            """
        )
        repo_mapping_dir = Path(__file__).resolve().parents[1] / "data" / "provider_mappings"

        result = import_provider_mappings(self.connection, repo_mapping_dir)

        self.assertGreaterEqual(result.league_inserted, 1)
        self.assertGreaterEqual(result.team_inserted, 8)
        league = self.connection.execute(
            """
            SELECT system_league_id
            FROM core.dim_provider_league_mapping
            WHERE provider_name = 'SportteryOfficialWeb'
              AND provider_league_id = '国际赛'
            """
        ).fetchone()
        self.assertEqual(league, ("INTL_FRIENDLY",))
        team_count = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM core.dim_provider_team_mapping
            WHERE provider_name = 'SportteryOfficialWeb'
              AND provider_team_id IN (
                  '克罗地亚', '斯洛文尼', '424', '挪威',
                  '希腊', '意大利', '哥伦比亚', '约旦'
              )
            """
        ).fetchone()[0]
        self.assertEqual(team_count, 8)

    def test_repository_api_football_mappings_include_greece_italy_real_ids(self):
        self.connection.execute(
            """
            INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name)
            VALUES
                ('WC_TEAM_GRE', 'Greece'),
                ('WC_TEAM_ITA', 'Italy');
            """
        )
        repo_mapping_dir = Path(__file__).resolve().parents[1] / "data" / "provider_mappings"

        result = import_provider_mappings(self.connection, repo_mapping_dir)

        self.assertGreaterEqual(result.team_inserted, 2)
        self.assertEqual(
            self.connection.execute(
                """
                SELECT provider_team_id, system_team_id
                FROM core.dim_provider_team_mapping
                WHERE provider_name = 'API-Football'
                  AND provider_team_id IN ('1117', '768')
                ORDER BY provider_team_id
                """
            ).fetchall(),
            [("1117", "WC_TEAM_GRE"), ("768", "WC_TEAM_ITA")],
        )

    def test_repository_sporttery_mappings_include_requested_world_cup_teams(self):
        self.connection.execute(
            """
            INSERT INTO core.dim_league_mapping (system_league_id, league_standard_name)
            VALUES ('WC_WORLD_CUP_2026', 'FIFA World Cup 2026');

            INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name)
            VALUES
                ('WC_TEAM_MEX', 'Mexico'),
                ('WC_TEAM_RSA', 'South Africa'),
                ('WC_TEAM_KOR', 'South Korea'),
                ('WC_TEAM_CZE', 'Czechia');
            """
        )
        repo_mapping_dir = Path(__file__).resolve().parents[1] / "data" / "provider_mappings"

        import_provider_mappings(self.connection, repo_mapping_dir)

        self.assertEqual(
            self.connection.execute(
                """
                SELECT provider_league_id, system_league_id
                FROM core.dim_provider_league_mapping
                WHERE provider_name = 'SportteryOfficialWeb'
                  AND provider_league_id = '世界杯'
                """
            ).fetchall(),
            [("世界杯", "WC_WORLD_CUP_2026")],
        )
        self.assertEqual(
            self.connection.execute(
                """
                SELECT provider_team_id, system_team_id
                FROM core.dim_provider_team_mapping
                WHERE provider_name = 'SportteryOfficialWeb'
                  AND provider_team_id IN ('墨西哥', '南非', '韩国', '捷克')
                ORDER BY provider_team_id
                """
            ).fetchall(),
            [
                ("南非", "WC_TEAM_RSA"),
                ("墨西哥", "WC_TEAM_MEX"),
                ("捷克", "WC_TEAM_CZE"),
                ("韩国", "WC_TEAM_KOR"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
