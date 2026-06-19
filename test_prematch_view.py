"""验证：预采集的摩洛哥vs南非数据能否被 FeatureBuilder 查到。"""
import sys
sys.path.insert(0, ".")
import os

# 加载 .env
env_path = ".env"
if os.path.exists(env_path):
    with open(env_path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

from football_advisor.config import load_config
from football_advisor.query_tools import DuckDBStatsQueryTool

config = load_config()
qt = DuckDBStatsQueryTool()

print("=== 测试1: FeatureBuilder 查 Morocco vs South Africa ===")
sql = """
    SELECT match_id, home_team_name, away_team_name, status, home_elo, away_elo,
           home_attack_strength, away_attack_strength
    FROM core.view_llm_match_prediction_base
    WHERE lower(home_team_name) = lower('Morocco')
      AND lower(away_team_name) = lower('South Africa')
    LIMIT 1
"""
result = qt.query_match_stats(sql, [])
if result.rows:
    row = result.rows[0]
    print(f"  找到! match_id={row['match_id']}")
    print(f"  home={row['home_team_name']} elo={row['home_elo']} att={row['home_attack_strength']}")
    print(f"  away={row['away_team_name']} elo={row['away_elo']} att={row['away_attack_strength']}")
    print(f"  status={row['status']}")
else:
    print("  未找到! 视图没有返回行")

print("\n=== 测试2: 查预采集的 match_id 是否存在 ===")
sql2 = """
    SELECT match_id, home_team_name, away_team_name, status
    FROM core.view_llm_match_prediction_base
    WHERE match_id = 'API_FOOTBALL_1489369'
    LIMIT 1
"""
result2 = qt.query_match_stats(sql2, [])
if result2.rows:
    row = result2.rows[0]
    print(f"  存在! {row['match_id']} | {row['home_team_name']} vs {row['away_team_name']} | {row['status']}")
else:
    print("  不存在！预采集的比赛没有进入视图")

print("\n=== 测试3: 直接查 fact_match_schedule ===")
sql3 = """
    SELECT match_id, home_team_id, away_team_id, status, system_league_id
    FROM core.fact_match_schedule
    WHERE match_id = 'API_FOOTBALL_1489369'
"""
result3 = qt.query_match_stats(sql3, [])
if result3.rows:
    row = result3.rows[0]
    print(f"  {row['match_id']} | {row['home_team_id']} vs {row['away_team_id']} | {row['status']} | {row['system_league_id']}")
    print(f"  fact_match_schedule 有数据")

print("\n=== 测试4: 查 dim_team_mapping 队名 ===")
sql4 = """
    SELECT system_team_id, team_standard_name
    FROM core.dim_team_mapping
    WHERE system_team_id IN ('WC_TEAM_MAR', 'WC_TEAM_RSA')
"""
result4 = qt.query_match_stats(sql4, [])
for row in result4.rows:
    print(f"  {row['system_team_id']} = '{row['team_standard_name']}'")

print("\n=== 测试5: 查滚动统计是否存在 ===")
sql5 = """
    SELECT team_id, COUNT(*) as cnt
    FROM core.fact_team_rolling_stats
    WHERE team_id IN ('WC_TEAM_MAR', 'WC_TEAM_RSA')
    GROUP BY team_id
"""
result5 = qt.query_match_stats(sql5, [])
for row in result5.rows:
    print(f"  {row['team_id']}: {row['cnt']} 条")
if not result5.rows:
    print("  无滚动统计数据")

print("\n=== 测试6: 查 Elo历史 ===")
sql6 = """
    SELECT team_id, MAX(elo_rating_after) as max_elo
    FROM core.fact_team_elo_history
    WHERE team_id IN ('WC_TEAM_MAR', 'WC_TEAM_RSA')
    GROUP BY team_id
"""
result6 = qt.query_match_stats(sql6, [])
for row in result6.rows:
    print(f"  {row['team_id']}: max_elo={row['max_elo']}")
if not result6.rows:
    print("  无Elo历史")