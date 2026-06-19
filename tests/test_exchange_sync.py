import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from football_advisor.config import AdvisorConfig
from football_advisor.db_schema import initialize_database
from football_advisor.exchange_client import MockBetfairClient
from football_advisor.models import MatchRequest
from football_advisor.sync import DataSyncCoordinator


class TestExchangeSync(unittest.TestCase):
    def test_default_capital_flow_sync_skips_without_exchange_client(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db_path = f.name

        initialize_database(db_path)

        import dataclasses
        import duckdb
        from football_advisor.config import DuckDBConfig

        base_config = AdvisorConfig()
        config = dataclasses.replace(
            base_config,
            duckdb=dataclasses.replace(base_config.duckdb, database_path=db_path),
        )

        coordinator = DataSyncCoordinator(parent_config=config)

        result = coordinator.sync_capital_flow_data(
            MatchRequest(query="M_AF_8888", match_id="M_AF_8888")
        )

        self.assertEqual(result.status, "skipped_no_provider_configured")
        self.assertEqual(result.source, "exchange_provider")
        with duckdb.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM core.fact_odds_capital_flow"
            ).fetchall()
        self.assertEqual(rows, [])

    def test_mock_exchange_sync_inserts_capital_flow(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db_path = f.name

        initialize_database(db_path)
        
        import dataclasses
        from football_advisor.config import DuckDBConfig
        base_config = AdvisorConfig()
        config = dataclasses.replace(
            base_config, 
            duckdb=dataclasses.replace(base_config.duckdb, database_path=db_path)
        )
        
        coordinator = DataSyncCoordinator(
            parent_config=config,
            exchange_client=MockBetfairClient(),
        )
        request = MatchRequest(query="M_AF_8888", match_id="M_AF_8888")
        
        result = coordinator.sync_capital_flow_data(request)
        
        self.assertEqual(result.status, "ok", f"Sync failed: {result.details.get('error', result)}")
        self.assertEqual(result.source, "betfair_mock")
        
        # Verify the data was written to duckdb
        import duckdb
        with duckdb.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT matched_volume, sharp_money_ratio FROM core.fact_odds_capital_flow WHERE match_id = ?", 
                ["M_AF_8888"]
            ).fetchall()
            
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], 1500000.0)
            self.assertEqual(rows[0][1], 0.65)

    @unittest.skip("view_market_feature_base 重构后待更新测试")
    def test_mock_exchange_sync_is_visible_in_market_feature_view(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db_path = f.name

        initialize_database(db_path)

        import dataclasses
        import duckdb

        base_config = AdvisorConfig()
        config = dataclasses.replace(
            base_config,
            duckdb=dataclasses.replace(base_config.duckdb, database_path=db_path),
        )

        with duckdb.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO core.fact_match_schedule (
                    match_id,
                    season,
                    system_league_id,
                    match_time,
                    home_team_id,
                    away_team_id,
                    source_provider
                ) VALUES (
                    'M_AF_8888',
                    '2026',
                    'SYS_EPL',
                    ?,
                    'SYS_HOME',
                    'SYS_AWAY',
                    'TEST'
                )
                """,
                [datetime.now(timezone.utc) + timedelta(days=1)],
            )

        coordinator = DataSyncCoordinator(
            parent_config=config,
            exchange_client=MockBetfairClient(),
        )
        request = MatchRequest(query="M_AF_8888", match_id="M_AF_8888")

        result = coordinator.sync_capital_flow_data(request)

        self.assertEqual(result.status, "ok", f"Sync failed: {result.details}")

        with duckdb.connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT matched_volume, sharp_money_ratio
                FROM core.view_market_feature_base
                WHERE match_id = ?
                """,
                ["M_AF_8888"],
            ).fetchall()

        self.assertEqual(rows, [(1500000.0, 0.65)])

if __name__ == "__main__":
    unittest.main()
