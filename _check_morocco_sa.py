import duckdb
conn = duckdb.connect('football_system.db')

print("=== view_llm_match_prediction_base 中 Morocco vs South Africa ===")
rows = conn.execute("""
    SELECT * FROM core.view_llm_match_prediction_base 
    WHERE home_team_name='Morocco' AND away_team_name='South Africa'
""").fetchall()
print(f"匹配行数: {len(rows)}")
for r in rows[:3]:
    print(r)

print("\n=== fact_match_schedule 中 Morocco vs South Africa ===")
rows2 = conn.execute("""
    SELECT * FROM core.fact_match_schedule 
    WHERE home_team='Morocco' AND away_team='South Africa'
""").fetchall()
print(f"匹配行数: {len(rows2)}")
for r in rows2:
    print(r)

print("\n=== stg_odds 中 Morocco vs South Africa ===")
rows3 = conn.execute("""
    SELECT * FROM staging.stg_odds 
    WHERE home_team='Morocco' AND away_team='South Africa'
""").fetchall()
print(f"匹配行数: {len(rows3)}")
for r in rows3:
    print(r)

print("\n=== 预采集赛程 (PRE-MATCH, 2026年6月) ===")
rows4 = conn.execute("""
    SELECT * FROM core.fact_match_schedule 
    WHERE status='PRE-MATCH' AND match_time >= '2026-06-01'
    ORDER BY match_time
""").fetchall()
print(f"匹配行数: {len(rows4)}")
for r in rows4:
    print(r)

conn.close()