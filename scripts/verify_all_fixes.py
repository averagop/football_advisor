"""全链路验证: Fix A+B+C+D+E 完整性检查"""
import sys
sys.path.insert(0, ".")

import duckdb
from football_advisor import db_schema
from football_advisor.sporttery_client import SportteryClient
from football_advisor.multi_source_coordinator import MultiSourceCoordinator
from football_advisor.config import SyncConfig
from datetime import date, timedelta

# ====== 1. 初始化 Schema ======
db_path = "data/football_advisor.duckdb"
conn = duckdb.connect(db_path)
db_schema.apply_schema(conn)
print("✓ Schema 初始化")

# ====== 2. 清空旧数据 ======
conn.execute("DELETE FROM staging.stg_match_schedule WHERE source_provider = 'SportteryOfficialWeb'")
conn.execute("DELETE FROM staging.stg_odds WHERE source_provider = 'SportteryOfficialWeb'")
conn.execute("DELETE FROM staging.stg_sporttery_odds_detail WHERE source_provider = 'SportteryOfficialWeb'")
print("✓ 旧数据清空")

# ====== 3. 同步竞彩数据 ======
client = SportteryClient()
tomorrow = date.today() + timedelta(days=1)
result = client.fetch_football_odds(conn, target_date=tomorrow)
print(f"\n=== Sporttery 同步结果 ===")
print(f"  matches={result['match_count']}")
print(f"  merged_matches={result['merged_match_count']}")  # 关键指标!
print(f"  odds_count={result['odds_count']}")
print(f"  detail_count={result['detail_count']}")
print(f"  spf_count={result['spf_odds_count']}")
print(f"  rqspf_count={result['rqspf_odds_count']}")
print(f"  crs_count={result['correct_score_count']}")
print(f"  ttg_count={result['total_goals_count']}")
print(f"  hafu_count={result['half_full_count']}")

# ====== 4. Fix A 验证: LEFT JOIN 后 merged_match_count 应 > 0 ======
if result['merged_match_count'] > 0:
    print(f"\n✓ Fix A 成功! merged_match_count={result['merged_match_count']} (之前为0)")
    # 进一步验证 core 表中有数据
    core_count = conn.execute("""
        SELECT COUNT(*) FROM core.fact_match_schedule 
        WHERE source_provider = 'SportteryOfficialWeb'
    """).fetchone()[0]
    print(f"  core.fact_match_schedule 有 {core_count} 行")
else:
    print(f"\n✗ Fix A 可能未生效: merged_match_count=0")

# ====== 5. view_match_feature_base 可用性验证 ======
try:
    view_count = conn.execute("SELECT COUNT(*) FROM core.view_match_feature_base").fetchone()[0]
    print(f"\n✓ view_match_feature_base: {view_count} 行 (LEFT JOIN 后应包含未映射联赛)")
except Exception as e:
    print(f"\n✗ view 查询失败: {e}")

# ====== 6. Fix B 验证: 5种玩法 ======
detail_counts = conn.execute("""
    SELECT odds_type, COUNT(*) FROM staging.stg_sporttery_odds_detail 
    WHERE source_provider = 'SportteryOfficialWeb' 
    GROUP BY odds_type
""").fetchall()
print(f"\n=== Fix B: 5种玩法入库 ===")
for d in detail_counts:
    print(f"  {d[0]}: {d[1]} 条")

# 抽查 CRS "其它" 标签
other = conn.execute("""
    SELECT score_key, odds_value FROM staging.stg_sporttery_odds_detail 
    WHERE odds_type='CORRECT_SCORE' AND score_key LIKE '%其它%'
""").fetchall()
if other:
    print(f"\n✓ CRS '其它': {[(o[0], o[1]) for o in other]}")

# ====== 7. Fix C 验证: 48h 时间窗口 ======
from football_advisor.third_party_odds_client import _event_matches
from datetime import datetime, timezone, timedelta as dt_timedelta
test_time = datetime(2026, 6, 10, 2, 0, 0, tzinfo=timezone.utc)
# 模拟 24h 前的事件（12h 会拒绝，48h 应接受）
event_24h = {"home_team": "TestH", "away_team": "TestA", "commence_time": "2026-06-09T02:00:00Z"}
result = _event_matches(event_24h, "TestH", "TestA", test_time)
print(f"\n=== Fix C: 48h时间窗口 ===")
print(f"  24h偏移事件 匹配: {result} (12h会False, 48h应True)" if result else "  24h偏移事件 匹配失败")

# ====== 8. Fix D+E 验证: 缺口分析 + 串行回退 ======
coordinator = MultiSourceCoordinator(
    sync_config=SyncConfig(),
    duckdb_path=db_path,
)
# Test: 用一场竞彩有数据的比赛，验证无缺口时正确跳过
print(f"\n=== Fix D+E: 匈牙利 vs 哈萨克 ===")
sr = coordinator.fetch_all("匈牙利", "哈萨克")
print(f"  Phase 1 源数: {len(sr.results)}")
# 找 Sporttery 结果
from football_advisor.multi_source_coordinator import SourceResult
sporttery_sr = next((r for r in sr.results if r.provider_name == "SportteryOfficialWeb"), None)
if sporttery_sr:
    print(f"  Sporttery: {sporttery_sr.status}")
    print(f"  spf_odds_count: {sporttery_sr.data.get('spf_odds_count')}")
    unsold = sporttery_sr.data.get('lottery_unsold_playtypes', [])
    if unsold:
        print(f"  竞彩未开售玩法: {unsold}")
    else:
        print(f"  竞彩5种玩法全部开售 ✓")

conn.close()
print("\n✓ 全链路验证完成")