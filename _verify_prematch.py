import duckdb
conn = duckdb.connect("football_system.db")

print("=== dim_collection_state ===")
for r in conn.execute("SELECT * FROM core.dim_collection_state").fetchall():
    print(f"  {r[0]}: last={r[1]}")

r1 = conn.execute("""SELECT COUNT(*) FROM core.fact_match_schedule
    WHERE status='PRE-MATCH' AND source_provider='API-Football'""").fetchone()
print(f"\nPRE-MATCH: {r1[0]}")

r2 = conn.execute("SELECT COUNT(*) FROM core.fact_match_schedule WHERE source_provider='API-Football-detail'").fetchone()
print(f"FINISHED-detail (recent form): {r2[0]}")

r3 = conn.execute("SELECT COUNT(*) FROM core.fact_match_schedule WHERE source_provider='API-Football-h2h'").fetchone()
print(f"FINISHED-h2h: {r3[0]}")

r4 = conn.execute("SELECT COUNT(*) FROM staging.stg_odds_snapshot").fetchone()
print(f"odds_snapshot: {r4[0]}")

r5 = conn.execute("SELECT COUNT(*) FROM core.fact_team_rolling_stats WHERE source_provider='API_FOOTBALL_TEAM_STATS'").fetchone()
print(f"team_stats: {r5[0]}")

r6 = conn.execute("SELECT COUNT(*) FROM core.fact_league_standings").fetchone()
print(f"standings: {r6[0]}")

r7 = conn.execute("SELECT COUNT(*) FROM staging.stg_injuries WHERE source_provider='API-Football'").fetchone()
print(f"injuries: {r7[0]}")

print("\n=== PRE-MATCH ===")
for r in conn.execute("SELECT match_id, home_team_id, away_team_id, match_time, system_league_id FROM core.fact_match_schedule WHERE status='PRE-MATCH' AND source_provider='API-Football' ORDER BY match_time LIMIT 10").fetchall():
    print(f"  {r[0]} | {r[1]} vs {r[2]} | {r[3]} | {r[4]}")

print("\n=== FINISHED (recent form) ===")
for r in conn.execute("SELECT match_id, home_team_id, away_team_id, home_score, away_score FROM core.fact_match_schedule WHERE source_provider='API-Football-detail' ORDER BY match_time DESC LIMIT 5").fetchall():
    print(f"  {r[0]} | {r[1]} vs {r[2]} | {r[3]}:{r[4]}")

print("\n=== standings ===")
for r in conn.execute("SELECT system_league_id, system_team_id, rank_position, points, played FROM core.fact_league_standings LIMIT 5").fetchall():
    print(f"  {r[0]} | {r[1]} | rank={r[2]} pts={r[3]} pld={r[4]}")

print("\n=== injuries ===")
for r in conn.execute("SELECT team_name, player_name, injury_type, reason FROM staging.stg_injuries WHERE source_provider='API-Football' LIMIT 5").fetchall():
    print(f"  {r[0]} | {r[1]} | {r[2]} | {r[3]}")

conn.close()