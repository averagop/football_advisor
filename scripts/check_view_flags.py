"""Check DuckDB view flags after data sync."""
import sys
sys.path.insert(0, ".")
import duckdb
from football_advisor.db_schema import apply_schema

conn = duckdb.connect("football_system.db")
apply_schema(conn)

row = conn.execute("""
SELECT missing_team_stats_flag, home_team_stats_quality_flag, away_team_stats_quality_flag,
       missing_1x2_odds_flag, news_source_count,
       critical_data_age_minutes, no_bet_data_quality_flag,
       derived_team_stats_quality_flag
FROM core.view_llm_match_prediction_base
WHERE match_id = 'M_ST_2040143'
""").fetchone()

cols = ["missing_team_stats", "home_quality", "away_quality",
        "missing_1x2_odds", "news_count",
        "data_age_min", "no_bet_flag", "derived_quality"]
print("View flags:")
for c, v in zip(cols, row):
    print(f"  {c}: {v}")

# Check fact_team_rolling_stats data_quality_flag values
qualities = conn.execute("""
SELECT DISTINCT team_id, data_quality_flag
FROM core.fact_team_rolling_stats
WHERE match_id = 'M_ST_2040143'
""").fetchall()
print("\nTeam stats quality flags:")
for q in qualities:
    print(f"  {q}")

conn.close()