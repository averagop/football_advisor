from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from football_advisor.db_schema import apply_schema
from football_advisor.worldcup_basic_importer import import_worldcup_basic_data


class WorldCupBasicImporterTests(unittest.TestCase):
    def test_imports_confirmed_group_fixtures_and_last5_stats_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            schedule_csv = temp_path / "world-cup-2026-schedule.csv"
            stats_csv = temp_path / "world_cup_2026_teams_last_5_matches_data.csv"
            schedule_csv.write_text(
                "\n".join(
                    [
                        "match_number,stage,group,date,time_et,time_local,team_a,team_b,venue,city,country,status,source",
                        "1,Group Stage,A,2026-06-11,15:00,13:00,Mexico,South Africa,Estadio Azteca,Mexico City,Mexico,confirmed_group_fixture,source",
                        "2,Group Stage,A,2026-06-11,22:00,20:00,Korea Republic,Czechia,Estadio Akron,Guadalajara,Mexico,confirmed_group_fixture,source",
                        "73,Round of 32,,2026-06-28,15:00,15:00,Group A Winner,Group B Runner-up,Venue,City,Country,bracket_slot,source",
                    ]
                ),
                encoding="utf-8",
            )
            stats_csv.write_text(
                "\n".join(
                    [
                        "rank,team,group,rating,avg_squad_age,avg_trans_value,W,D,L,GF,GA,GD,W%",
                        "15,Mexico,A,1681.03,26.1,5.11,3,2,0,8,1,7,60.0",
                        "24,South Korea,A,1594.35,27.7,5.16,3,0,2,8,5,3,60.0",
                        "36,Czechia,A,1471.24,27.1,5.41,3,2,0,13,5,8,60.0",
                        "40,South Africa,A,1401.56,26.6,1.17,1,2,2,6,7,-1,20.0",
                    ]
                ),
                encoding="utf-8",
            )
            database_path = temp_path / "worldcup.duckdb"

            with duckdb.connect(str(database_path)) as connection:
                apply_schema(connection)
                result = import_worldcup_basic_data(
                    connection,
                    schedule_csv_path=schedule_csv,
                    team_stats_csv_path=stats_csv,
                )

                fixtures = connection.execute(
                    """
                    SELECT match_id, home_team_id, away_team_id, match_time,
                           is_neutral_venue
                    FROM core.fact_match_schedule
                    ORDER BY match_id
                    """
                ).fetchall()
                rolling_count = connection.execute(
                    "SELECT COUNT(*) FROM core.fact_team_rolling_stats"
                ).fetchone()[0]
                quality_flags = {
                    row[0]
                    for row in connection.execute(
                        "SELECT DISTINCT data_quality_flag FROM core.fact_team_rolling_stats"
                    ).fetchall()
                }
                derived_quality_flag = connection.execute(
                    """
                    SELECT derived_team_stats_quality_flag
                    FROM core.view_llm_match_prediction_base
                    WHERE match_id = 'WC2026_M001'
                    """
                ).fetchone()[0]

        self.assertEqual(result.inserted_counts["fixtures"], 2)
        self.assertEqual(result.skipped_counts["bracket_slots"], 1)
        self.assertEqual(fixtures[0][0], "WC2026_M001")
        self.assertEqual(fixtures[0][1], "WC_TEAM_MEX")
        self.assertEqual(fixtures[0][2], "WC_TEAM_RSA")
        self.assertEqual(fixtures[0][3].isoformat(), "2026-06-11T19:00:00")
        self.assertTrue(fixtures[0][4])
        self.assertEqual(fixtures[1][0], "WC2026_M002")
        self.assertEqual(fixtures[1][1], "WC_TEAM_KOR")
        self.assertEqual(fixtures[1][2], "WC_TEAM_CZE")
        self.assertEqual(fixtures[1][3].isoformat(), "2026-06-12T02:00:00")
        self.assertEqual(rolling_count, 4)
        self.assertEqual(quality_flags, {"WORLD_CUP_LAST5_SNAPSHOT"})
        self.assertTrue(derived_quality_flag)


if __name__ == "__main__":
    unittest.main()
