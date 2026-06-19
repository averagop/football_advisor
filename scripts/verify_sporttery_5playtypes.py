"""验证 Sporttery 5种玩法全量入库"""
import sys
sys.path.insert(0, ".")

import duckdb
from football_advisor import db_schema
from football_advisor.sporttery_client import SportteryClient
from datetime import date

from datetime import date, timedelta

# 初始化 DB
db_path = "data/football_advisor.duckdb"
conn = duckdb.connect(db_path)
db_schema.apply_schema(conn)
print("✓ Schema 初始化完成")

# 获取竞彩数据（使用明天日期，因为比赛日期可能跨天）
client = SportteryClient()
tomorrow = date.today() + timedelta(days=1)
result = client.fetch_football_odds(conn, target_date=tomorrow)
print(f"\n=== 同步结果 (日期={tomorrow}) ===")
for k, v in sorted(result.items()):
    print(f"  {k}: {v}")

# 验证各玩法入库
print(f"\n=== staging.stg_odds ===")
rows = conn.execute("SELECT odds_type, COUNT(*) as cnt FROM staging.stg_odds WHERE source_provider = 'SportteryOfficialWeb' GROUP BY odds_type").fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]} 条")

print(f"\n=== staging.stg_sporttery_odds_detail ===")
try:
    rows = conn.execute("SELECT odds_type, COUNT(*) as cnt FROM staging.stg_sporttery_odds_detail WHERE source_provider = 'SportteryOfficialWeb' GROUP BY odds_type").fetchall()
    for r in rows:
        print(f"  {r[0]}: {r[1]} 条")

    # 抽样看看数据结构
    print(f"\n=== 比分 抽样 (前3条) ===")
    samples = conn.execute("SELECT provider_match_id, score_key, odds_value FROM staging.stg_sporttery_odds_detail WHERE odds_type='CORRECT_SCORE' LIMIT 3").fetchall()
    for s in samples:
        print(f"  {s[0]}: {s[1]} = {s[2]}")

    print(f"\n=== 总进球 抽样 (前3条) ===")
    samples = conn.execute("SELECT provider_match_id, score_key, odds_value FROM staging.stg_sporttery_odds_detail WHERE odds_type='TOTAL_GOALS' LIMIT 3").fetchall()
    for s in samples:
        print(f"  {s[0]}: {s[1]} = {s[2]}")

    print(f"\n=== 半全场 抽样 (前3条) ===")
    samples = conn.execute("SELECT provider_match_id, score_key, odds_value FROM staging.stg_sporttery_odds_detail WHERE odds_type='HALF_FULL' LIMIT 3").fetchall()
    for s in samples:
        print(f"  {s[0]}: {s[1]} = {s[2]}")
except Exception as e:
    print(f"  ERROR: {e}")

# 找一场5种玩法全有的比赛验证
print(f"\n=== 5种玩法全覆盖的比赛 ===")
match = conn.execute("""
    SELECT s.provider_match_id, s.provider_home_team_id, s.provider_away_team_id,
           o1.cnt as spf, o2.cnt as rqspf, o3.cnt as crs, o4.cnt as ttg, o5.cnt as hafu
    FROM staging.stg_match_schedule s
    LEFT JOIN (SELECT provider_match_id, COUNT(*) as cnt FROM staging.stg_odds WHERE odds_type='1X2' GROUP BY provider_match_id) o1 ON s.provider_match_id = o1.provider_match_id
    LEFT JOIN (SELECT provider_match_id, COUNT(*) as cnt FROM staging.stg_odds WHERE odds_type='SPORTTERY_RQSPF' GROUP BY provider_match_id) o2 ON s.provider_match_id = o2.provider_match_id
    LEFT JOIN (SELECT provider_match_id, COUNT(*) as cnt FROM staging.stg_sporttery_odds_detail WHERE odds_type='CORRECT_SCORE' GROUP BY provider_match_id) o3 ON s.provider_match_id = o3.provider_match_id
    LEFT JOIN (SELECT provider_match_id, COUNT(*) as cnt FROM staging.stg_sporttery_odds_detail WHERE odds_type='TOTAL_GOALS' GROUP BY provider_match_id) o4 ON s.provider_match_id = o4.provider_match_id
    LEFT JOIN (SELECT provider_match_id, COUNT(*) as cnt FROM staging.stg_sporttery_odds_detail WHERE odds_type='HALF_FULL' GROUP BY provider_match_id) o5 ON s.provider_match_id = o5.provider_match_id
    WHERE s.source_provider = 'SportteryOfficialWeb'
      AND o1.cnt > 0 AND o2.cnt > 0 AND o3.cnt > 0 AND o4.cnt > 0 AND o5.cnt > 0
    LIMIT 1
""").fetchone()

if match:
    print(f"  {match[1]} vs {match[2]} (id={match[0]})")
    print(f"  SPF={match[3]} | RQSPF={match[4]} | CRS={match[5]} | TTG={match[6]} | HAFU={match[7]}")
else:
    print("  无 (可能所有比赛都没有5种全开)")

conn.close()
print("\n✓ 验证完成")