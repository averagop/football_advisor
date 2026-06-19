from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from scripts.migrate_news_audit_fields import migrate


class NewsAuditMigrationTests(unittest.TestCase):
    def test_migration_backfills_source_text_hash_for_legacy_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "legacy_news.duckdb"
            with duckdb.connect(str(database_path)) as conn:
                conn.execute("CREATE SCHEMA core")
                conn.execute("""
                    CREATE TABLE core.fact_news_signal_summary (
                        match_id VARCHAR PRIMARY KEY,
                        news_sentiment_score DOUBLE,
                        news_risk_flag BOOLEAN,
                        news_source_count INT,
                        news_last_updated_at TIMESTAMP,
                        source_provider VARCHAR
                    )
                """)
                conn.execute("""
                    INSERT INTO core.fact_news_signal_summary (
                        match_id,
                        news_sentiment_score,
                        news_risk_flag,
                        news_source_count,
                        news_last_updated_at,
                        source_provider
                    ) VALUES (
                        'M_LEGACY',
                        0.25,
                        FALSE,
                        2,
                        TIMESTAMP '2026-05-27 12:00:00',
                        'legacy-provider'
                    )
                """)

            migrate(str(database_path))

            with duckdb.connect(str(database_path), read_only=True) as conn:
                row = conn.execute("""
                    SELECT generated_by, source_text_hash, confidence, requires_cross_check
                    FROM core.fact_news_signal_summary
                    WHERE match_id = 'M_LEGACY'
                """).fetchone()

        self.assertEqual(row[0], "legacy_migration")
        self.assertRegex(row[1], r"^[0-9a-f]{64}$")
        self.assertEqual(row[2], 0.5)
        self.assertTrue(row[3])


if __name__ == "__main__":
    unittest.main()
