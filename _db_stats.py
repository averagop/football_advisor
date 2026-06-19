import duckdb
conn = duckdb.connect('football_system.db')
print("=== 比赛状态分布 ===")
for r in conn.execute("SELECT status, COUNT(*) AS cnt FROM core.fact_match_schedule GROUP BY status").fetchall():
    print(f"  {r[0]}: {r[1]}")
print(f"\n滚动统计数据行数: {conn.execute('SELECT COUNT(*) FROM core.fact_team_rolling_stats').fetchone()[0]}")
print(f"Elo历史行数: {conn.execute('SELECT COUNT(*) FROM core.fact_team_elo_history').fetchone()[0]}")
print(f"\n=== 已完成比赛(FINISHED) ===")
for r in conn.execute("SELECT match_id, home_team_id, away_team_id, home_score, away_score, match_time FROM core.fact_match_schedule WHERE status='FINISHED' LIMIT 10").fetchall():
    print(f"  {r}")
conn.close()