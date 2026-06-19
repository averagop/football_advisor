import argparse
import sys
from pathlib import Path

import duckdb

# Add project root to path so we can import from football_advisor
sys.path.insert(0, str(Path(__file__).parent.parent))
from football_advisor.config import load_config


def migrate(database_path: str = None):
    if not database_path:
        database_path = load_config().duckdb.database_path

    print(f"Migrating DuckDB database at: {database_path}")
    try:
        with duckdb.connect(database_path) as conn:
            # Check if table exists before altering
            table_exists = conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema = 'core' AND table_name = 'fact_news_signal_summary'"
            ).fetchone()

            if not table_exists:
                print(
                    "Table core.fact_news_signal_summary does not exist. No migration needed."
                )
                return

            print(
                "Adding audit columns to core.fact_news_signal_summary if they do not exist..."
            )
            conn.execute(
                "ALTER TABLE core.fact_news_signal_summary ADD COLUMN IF NOT EXISTS generated_by VARCHAR"
            )
            conn.execute(
                "ALTER TABLE core.fact_news_signal_summary ADD COLUMN IF NOT EXISTS source_text_hash VARCHAR"
            )
            conn.execute(
                "ALTER TABLE core.fact_news_signal_summary ADD COLUMN IF NOT EXISTS confidence DOUBLE"
            )
            conn.execute(
                "ALTER TABLE core.fact_news_signal_summary ADD COLUMN IF NOT EXISTS requires_cross_check BOOLEAN"
            )

            # Update existing rows with defaults for new columns if they are null
            print("Updating existing rows with default values...")
            conn.execute("""
                UPDATE core.fact_news_signal_summary
                SET 
                    generated_by = COALESCE(generated_by, 'legacy_migration'),
                    source_text_hash = COALESCE(
                        source_text_hash,
                        sha256(
                            COALESCE(match_id, '') || ':' ||
                            COALESCE(source_provider, '') || ':' ||
                            COALESCE(CAST(news_sentiment_score AS VARCHAR), '') || ':' ||
                            COALESCE(CAST(news_risk_flag AS VARCHAR), '') || ':' ||
                            COALESCE(CAST(news_source_count AS VARCHAR), '')
                        )
                    ),
                    confidence = COALESCE(confidence, 0.5),
                    requires_cross_check = COALESCE(requires_cross_check, TRUE)
                WHERE generated_by IS NULL
                   OR source_text_hash IS NULL
                   OR confidence IS NULL
                   OR requires_cross_check IS NULL
            """)

            # Re-apply schema just in case there are other missing pieces or views that need refreshing
            print("Re-applying core schema to ensure all views are up-to-date...")
            from football_advisor.db_schema import apply_schema

            apply_schema(conn)

            print("Migration completed successfully.")

    except Exception as e:
        print(f"Migration failed: {e}")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate news audit fields in DuckDB.")
    parser.add_argument(
        "--db", type=str, help="Path to DuckDB database (default: loaded from config)"
    )
    args = parser.parse_args()
    migrate(args.db)
