"""清理数据后重新验证 5 种玩法"""
import sys
sys.path.insert(0, ".")

import duckdb
from football_advisor import db_schema
from football_advisor.sporttery_client import SportteryClient
from datetime import date, timedelta

db_path = "data/football_advisor.duckdb"
conn = duckdb.connect(db_path)
db_schema.apply_schema(conn)

# 清空旧数据
conn.execute("DELETE FROM staging.stg_match_schedule WHERE source_provider = 'SportteryOfficialWeb'")
conn.execute("DELETE FROM staging.stg_odds WHERE source_provider = 'SportteryOfficialWeb'")
conn.execute("DELETE FROM staging.stg_sporttery_odds_detail WHERE source_provider = 'SportteryOfficialWeb'")
print("✓ 旧数据已清空")

# 重新获取
client = SportteryClient()
tomorrow = date.today() + timedelta(days=1)
result = client.fetch_football_odds(conn, target_date=tomorrow)
print(f"\n=== 同步结果 (日期={tomorrow}) ===")
for k, v in sorted(result.items()):
    print(f"  {k}: {v}")

# 验证入库
print(f"\n=== staging.stg_odds ===")
rows = conn.execute("SELECT odds_type, COUNT(*) cnt FROM staging.stg_odds WHERE source_provider = 'SportteryOfficialWeb' GROUP BY odds_type").fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]} 条")

print(f"\n=== staging.stg_sporttery_odds_detail ===")
rows = conn.execute("SELECT odds_type, COUNT(*) cnt FROM staging.stg_sporttery_odds_detail WHERE source_provider = 'SportteryOfficialWeb' GROUP BY odds_type").fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]} 条")

# 匈牙利 vs 哈萨克 完整数据
print(f"\n=== 匈牙利 vs 哈萨克 (id=2040187) 完整数据 ===")
match = conn.execute("""
    SELECT 
        (SELECT COUNT(*) FROM staging.stg_odds WHERE provider_match_id='2040187' AND odds_type='1X2') as spf,
        (SELECT COUNT(*) FROM staging.stg_odds WHERE provider_match_id='2040187' AND odds_type='SPORTTERY_RQSPF') as rqspf,
        (SELECT COUNT(*) FROM staging.stg_sporttery_odds_detail WHERE provider_match_id='2040187' AND odds_type='CORRECT_SCORE') as crs,
        (SELECT COUNT(*) FROM staging.stg_sporttery_odds_detail WHERE provider_match_id='2040187' AND odds_type='TOTAL_GOALS') as ttg,
        (SELECT COUNT(*) FROM staging.stg_sporttery_odds_detail WHERE provider_match_id='2040187' AND odds_type='HALF_FULL') as hafu
""").fetchone()
print(f"  SPF={match[0]} | RQSPF={match[1]} | CRS={match[2]} | TTG={match[3]} | HAFU={match[4]}")

# HAFU 详细
print(f"\n=== HAFU 详细 ===")
rows = conn.execute("SELECT score_key, odds_value FROM staging.stg_sporttery_odds_detail WHERE provider_match_id='2040187' AND odds_type='HALF_FULL' ORDER BY score_key").fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]}")

# CRS 特殊键
print(f"\n=== CRS '其它' 键 ===")
rows = conn.execute("SELECT score_key, odds_value FROM staging.stg_sporttery_odds_detail WHERE provider_match_id='2040187' AND odds_type='CORRECT_SCORE' AND score_key LIKE '%其它%'").fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]}")

conn.close()
print("\n✓ 验证完成")