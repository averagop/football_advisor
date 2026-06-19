import duckdb
import os
import sys

db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "football_system.db")
if not os.path.exists(db_path):
    print(f"数据库文件不存在: {db_path}")
    sys.exit(1)

conn = duckdb.connect(db_path)

print("=== dim_league_mapping 表结构 ===")
try:
    cols = conn.execute("DESCRIBE core.dim_league_mapping").fetchall()
    for col in cols:
        print(f"  {col}")
except Exception as e:
    print(f"  失败: {e}")

print("\n=== dim_team_mapping 表结构 ===")
try:
    cols = conn.execute("DESCRIBE core.dim_team_mapping").fetchall()
    for col in cols:
        print(f"  {col}")
except Exception as e:
    print(f"  失败: {e}")

print("\n=== dim_league_mapping 全部记录 ===")
try:
    leagues = conn.execute("SELECT * FROM core.dim_league_mapping").fetchall()
    print(f"联赛映射总数: {len(leagues)}")
    for row in leagues:
        print(f"  {row}")
except Exception as e:
    print(f"  失败: {e}")

print("\n=== dim_team_mapping 全部记录 ===")
try:
    teams = conn.execute("SELECT * FROM core.dim_team_mapping").fetchall()
    print(f"球队映射总数: {len(teams)}")
    for row in teams:
        print(f"  {row}")
except Exception as e:
    print(f"  失败: {e}")

print("\n=== fact_team_rolling_stats 记录数 ===")
try:
    cnt = conn.execute("SELECT COUNT(*) FROM core.fact_team_rolling_stats").fetchone()[0]
    print(f"滚动统计数: {cnt}")
except Exception as e:
    print(f"  失败: {e}")

print("\n=== fact_odds_capital_flow 记录数 ===")
try:
    cnt = conn.execute("SELECT COUNT(*) FROM core.fact_odds_capital_flow").fetchone()[0]
    print(f"赔率记录数: {cnt}")
except Exception as e:
    print(f"  失败: {e}")

conn.close()
print("\n=== B-0 预检完成 ===")