"""Debug: test API-Football team last matches directly."""
import sys
sys.path.insert(0, ".")

import os
import duckdb
from datetime import datetime, timezone

# Load .env
env_path = ".env"
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, raw = line.split("=", 1)
                key = key.strip()
                value = raw.strip().strip("\"'")
                os.environ[key] = value

from football_advisor.api_football_client import ApiFootballClient

client = ApiFootballClient(api_token=os.environ.get("API_FOOTBALL_TOKEN"))
conn = duckdb.connect("football_system.db")

# Check provider team IDs
rows = conn.execute("""
SELECT m.provider_team_id, m.system_team_id, t.team_standard_name
FROM core.dim_provider_team_mapping m
JOIN core.dim_team_mapping t ON m.system_team_id = t.system_team_id
WHERE m.provider_name = 'API-Football'
  AND lower(t.team_standard_name) IN (lower('Greece'), lower('Italy'))
""").fetchall()
print("Provider team mappings:")
for r in rows:
    print(f"  {r}")

# Check target match ID
ref_time = datetime(2026, 6, 8, 3, 0, tzinfo=timezone.utc)
target_id = client._find_target_match_id(conn, ref_time, "Greece", "Italy")
print(f"Target match ID: {target_id}")

if target_id:
    # Italy
    result = client.fetch_team_last_matches(conn, target_id, "768", "WC_TEAM_ITA", 10)
    print(f"\nItaly last matches: {result}")

    # Greece
    result = client.fetch_team_last_matches(conn, target_id, "1117", "WC_TEAM_GRE", 10)
    print(f"Greece last matches: {result}")

# Check DB after
stats = conn.execute("""
SELECT match_id, team_id, record_date, data_quality_flag, rolling_goals_for, rolling_goals_against
FROM core.fact_team_rolling_stats
WHERE match_id = 'M_ST_2040143'
ORDER BY team_id, record_date
LIMIT 20
""").fetchall()
print(f"\nfact_team_rolling_stats rows: {len(stats)}")
for s in stats:
    print(f"  {s}")

ctx = conn.execute("""
SELECT * FROM core.fact_match_context_summary WHERE match_id = 'M_ST_2040143'
""").fetchone()
print(f"\nfact_match_context_summary: {ctx}")

conn.close()