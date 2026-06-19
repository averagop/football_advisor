"""Quick DB check for Portugal vs Nigeria."""
import duckdb

conn = duckdb.connect('football_system.db')

print("=== 球队映射 ===")
r = conn.execute("""
    SELECT system_team_id, team_standard_name FROM core.dim_team_mapping
    WHERE system_team_id IN ('WC_TEAM_POR', 'WC_TEAM_NGA')
""").fetchall()
for row in r:
    print(row)

print("\n=== Provider Team Mapping ===")
r = conn.execute("""
    SELECT system_team_id, provider_name, COUNT(1) AS cnt
    FROM core.dim_provider_team_mapping
    WHERE system_team_id IN ('WC_TEAM_POR', 'WC_TEAM_NGA')
    GROUP BY system_team_id, provider_name
""").fetchall()
for row in r:
    print(row)

print("\n=== Team Rolling Stats ===")
r = conn.execute("""
    SELECT team_id, COUNT(1) AS cnt
    FROM core.fact_team_rolling_stats
    WHERE team_id IN ('WC_TEAM_POR', 'WC_TEAM_NGA')
    GROUP BY team_id
""").fetchall()
for row in r:
    print(row)

print("\n=== Match Schedule (Portugal or Nigeria) ===")
r = conn.execute("""
    SELECT match_id, home_team_id, away_team_id, match_time, status
    FROM core.fact_match_schedule
    WHERE home_team_id IN ('WC_TEAM_POR', 'WC_TEAM_NGA')
       OR away_team_id IN ('WC_TEAM_POR', 'WC_TEAM_NGA')
    ORDER BY match_time
""").fetchall()
for row in r:
    print(row)

print("\n=== Odds staging (Sporttery) ===")
try:
    r = conn.execute("SELECT COUNT(1) FROM staging.stg_odds").fetchone()
    print(f"staging.stg_odds rows: {r[0]}")
except Exception as e:
    print(f"staging.stg_odds: {e}")

print("\n=== Elo 评分 ===")
r = conn.execute("""
    SELECT system_team_id, team_standard_name, elo_rating
    FROM core.dim_team_mapping
    WHERE system_team_id IN ('WC_TEAM_POR', 'WC_TEAM_NGA')
""").fetchall()
for row in r:
    print(row)

conn.close()