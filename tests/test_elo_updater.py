from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from football_advisor.db_schema import apply_schema
from football_advisor.elo_updater import EloUpdater


class EloUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.duckdb"
        self.connection = duckdb.connect(str(self.db_path))
        apply_schema(self.connection)
        self.updater = EloUpdater()
        
        # Setup teams
        self.connection.execute("""
            INSERT INTO core.dim_league_mapping (system_league_id, league_standard_name, country)
            VALUES ('L1', 'League 1', 'England');
            
            INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name, elo_rating_base)
            VALUES 
                ('T1', 'Team 1', 1500.0),
                ('T2', 'Team 2', 1600.0);
        """)

    def tearDown(self):
        self.connection.close()
        self.temp_dir.cleanup()

    def test_update_unprocessed_matches_calculates_and_inserts_elo(self):
        self.connection.execute("""
            INSERT INTO core.fact_match_schedule (
                match_id, season, system_league_id, match_time, 
                home_team_id, away_team_id, status, home_score, away_score, 
                source_provider, updated_at
            ) VALUES (
                'M1', '25-26', 'L1', TIMESTAMP '2026-05-01 15:00:00',
                'T1', 'T2', 'FINISHED', 2, 1, 'TEST', CURRENT_TIMESTAMP
            );
        """)
        
        processed = self.updater.update_unprocessed_matches(self.connection)
        self.assertEqual(processed, 1)
        
        history = self.connection.execute("""
            SELECT team_id, elo_rating_before, elo_rating_after
            FROM core.fact_team_elo_history
            ORDER BY team_id
        """).fetchall()
        
        self.assertEqual(len(history), 2)
        
        t1_hist = history[0]
        self.assertEqual(t1_hist[0], 'T1')
        self.assertEqual(t1_hist[1], 1500.0)
        self.assertGreater(t1_hist[2], 1500.0) # Won
        
        t2_hist = history[1]
        self.assertEqual(t2_hist[0], 'T2')
        self.assertEqual(t2_hist[1], 1600.0)
        self.assertLess(t2_hist[2], 1600.0) # Lost

    def test_update_ignores_unfinished_matches(self):
        self.connection.execute("""
            INSERT INTO core.fact_match_schedule (
                match_id, season, system_league_id, match_time, 
                home_team_id, away_team_id, status, home_score, away_score, 
                source_provider, updated_at
            ) VALUES (
                'M1', '25-26', 'L1', TIMESTAMP '2026-05-01 15:00:00',
                'T1', 'T2', 'PRE-MATCH', NULL, NULL, 'TEST', CURRENT_TIMESTAMP
            );
        """)
        
        processed = self.updater.update_unprocessed_matches(self.connection)
        self.assertEqual(processed, 0)
        
        history_count = self.connection.execute("SELECT COUNT(*) FROM core.fact_team_elo_history").fetchone()[0]
        self.assertEqual(history_count, 0)
        
    def test_update_uses_latest_history_for_subsequent_matches(self):
        self.connection.execute("""
            INSERT INTO core.fact_match_schedule (
                match_id, season, system_league_id, match_time, 
                home_team_id, away_team_id, status, home_score, away_score, 
                source_provider, updated_at
            ) VALUES 
            (
                'M1', '25-26', 'L1', TIMESTAMP '2026-05-01 15:00:00',
                'T1', 'T2', 'FINISHED', 2, 1, 'TEST', CURRENT_TIMESTAMP
            ),
            (
                'M2', '25-26', 'L1', TIMESTAMP '2026-05-08 15:00:00',
                'T2', 'T1', 'FINISHED', 1, 1, 'TEST', CURRENT_TIMESTAMP
            );
        """)
        
        # M1 and M2 processed sequentially
        processed = self.updater.update_unprocessed_matches(self.connection)
        self.assertEqual(processed, 2)
        
        t1_history = self.connection.execute("""
            SELECT match_id, elo_rating_before, elo_rating_after
            FROM core.fact_team_elo_history
            WHERE team_id = 'T1'
            ORDER BY record_date ASC
        """).fetchall()
        
        self.assertEqual(len(t1_history), 2)
        m1 = t1_history[0]
        m2 = t1_history[1]
        
        self.assertEqual(m1[0], 'M1')
        self.assertEqual(m1[1], 1500.0)
        self.assertEqual(m2[0], 'M2')
        self.assertEqual(m2[1], m1[2]) # M2 before uses M1 after

    def test_view_match_feature_base_picks_up_dynamic_elo(self):
        self.connection.execute("""
            INSERT INTO core.fact_match_schedule (
                match_id, season, system_league_id, match_time, 
                home_team_id, away_team_id, status, home_score, away_score, 
                source_provider, updated_at
            ) VALUES (
                'M1', '25-26', 'L1', TIMESTAMP '2026-05-01 15:00:00',
                'T1', 'T2', 'FINISHED', 2, 0, 'TEST', CURRENT_TIMESTAMP
            ),
            (
                'M2', '25-26', 'L1', TIMESTAMP '2026-05-10 15:00:00',
                'T1', 'T2', 'PRE-MATCH', NULL, NULL, 'TEST', CURRENT_TIMESTAMP
            );
        """)
        
        self.updater.update_unprocessed_matches(self.connection)
        
        features = self.connection.execute("""
            SELECT home_elo, away_elo
            FROM core.view_match_feature_base
            WHERE match_id = 'M2'
        """).fetchone()
        
        self.assertIsNotNone(features)
        
        # T1 won M1, so their Elo should be > 1500
        self.assertGreater(features[0], 1500.0)
        # T2 lost M1, so their Elo should be < 1600
        self.assertLess(features[1], 1600.0)

if __name__ == '__main__':
    unittest.main()
