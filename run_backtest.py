from football_advisor.backtest_engine import DuckDBBacktestMatchLoader, BacktestEngine, format_backtest_summary
import json
import logging
import os

logging.basicConfig(level=logging.INFO)

def main():
    print("Initializing database schema...")
    try:
        import duckdb
        from football_advisor.db_schema import apply_schema
        from football_advisor.sqlite_importer import import_sqlite_database
        from football_advisor.elo_updater import EloUpdater
        import sqlite3
        import tempfile
        
        with duckdb.connect("football_system.db") as conn:
            apply_schema(conn)
            
            # Create a mock SQLite db with some matches
            with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
                sqlite_path = f.name
                
            sqlite_conn = sqlite3.connect(sqlite_path)
            sqlite_conn.executescript("""
                CREATE TABLE Country (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE League (id INTEGER PRIMARY KEY, country_id INTEGER, name TEXT);
                CREATE TABLE Team (id INTEGER PRIMARY KEY, team_api_id INTEGER, team_long_name TEXT, team_short_name TEXT);
                CREATE TABLE Player (id INTEGER PRIMARY KEY, player_api_id INTEGER, player_name TEXT, player_fifa_api_id INTEGER, birthday TEXT, height REAL, weight REAL);
                CREATE TABLE Match (
                    id INTEGER PRIMARY KEY, country_id INTEGER, league_id INTEGER, season TEXT, stage INTEGER, date TEXT,
                    match_api_id INTEGER, home_team_api_id INTEGER, away_team_api_id INTEGER,
                    home_team_goal INTEGER, away_team_goal INTEGER,
                    B365H REAL, B365D REAL, B365A REAL
                );
                INSERT INTO Country VALUES (1, 'England');
                INSERT INTO League VALUES (1729, 1, 'England Premier League');
                INSERT INTO Team VALUES (1, 100, 'Arsenal', 'ARS');
                INSERT INTO Team VALUES (2, 200, 'Chelsea', 'CHE');
                INSERT INTO Team VALUES (3, 300, 'Man City', 'MCI');
                INSERT INTO Match VALUES (1, 1, 1729, '2015', 1, '2015-08-01 00:00:00', 501, 100, 200, 2, 1, 2.1, 3.3, 3.6);
                INSERT INTO Match VALUES (2, 1, 1729, '2015', 2, '2015-08-08 00:00:00', 502, 200, 300, 1, 1, 2.5, 3.1, 2.8);
                INSERT INTO Match VALUES (3, 1, 1729, '2015', 3, '2015-08-15 00:00:00', 503, 300, 100, 3, 0, 1.8, 3.5, 4.2);
                INSERT INTO Match VALUES (4, 1, 1729, '2015', 4, '2015-08-22 00:00:00', 504, 100, 300, 1, 2, 2.6, 3.2, 2.7);
            """)
            sqlite_conn.close()
            
            print("Importing mock SQLite data...")
            import_sqlite_database(conn, sqlite_path)
            os.remove(sqlite_path)
            
            print("Updating Elo ratings...")
            updater = EloUpdater()
            updater.update_unprocessed_matches(conn)
            
    except Exception as e:
        print(f"Schema init warning: {e}")
        import traceback
        traceback.print_exc()
        
    print("Loading backtest matches...")
    # Use the real database path or the default SQLite match prefixes
    loader = DuckDBBacktestMatchLoader(database_path="football_system.db")
    engine = BacktestEngine()
    
    # We will load up to 200 matches with the SQLITE_MATCH prefix (from test data)
    matches = loader.load_walk_forward_matches(limit=200, match_id_prefix="SQLITE_MATCH_")
    
    if not matches:
        print("No matches found for backtest. Using M_AF_ prefix instead...")
        matches = loader.load_walk_forward_matches(limit=200, match_id_prefix="M_AF_")
    
    if not matches:
        print("No historical matches available in the database. Generating backtest report with 0 samples.")
        report_content = "# Walk-forward 回测摘要\n\n数据不足，未能加载到足够的历史样本。"
    else:
        print(f"Found {len(matches)} historical matches. Running backtest engine...")
        result = engine.run(matches)
        
        # Save equity curve for potential web UI usage
        with open("equity_curve.json", "w") as f:
            json.dump(result.equity_curve, f)
            
        report_content = format_backtest_summary(result)
        
    print("Writing backtest report...")
    with open("backtest_report.md", "w", encoding="utf-8") as f:
        f.write(report_content)
        
    print("Backtest completed. Check backtest_report.md for results.")

if __name__ == "__main__":
    main()
