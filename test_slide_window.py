"""验证日期滑窗机制。"""
import sys
sys.path.insert(0, ".")
import os
from datetime import datetime, timezone

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
from football_advisor.multi_source_coordinator import MultiSourceCoordinator

config = load_config()
from dataclasses import replace
config = replace(config, sync=replace(config.sync, date_slide_max_days=7))
print(f"date_slide_max_days = {config.sync.date_slide_max_days}")

started = datetime.now(timezone.utc)
coordinator = MultiSourceCoordinator(
    sync_config=config.sync,
    duckdb_path=config.duckdb.database_path,
)
result = coordinator.fetch_all(
    home_team="Portugal",
    away_team="Nigeria",
    kickoff_time=datetime(2026, 6, 10, 19, 0, tzinfo=timezone.utc),
)

elapsed = (datetime.now(timezone.utc) - started).total_seconds()
print(f"\n总耗时: {elapsed:.1f}s")
print(f"成功: {result.success_count}, 失败: {result.failure_count}, 跳过: {result.skipped_count}")
print(f"总计 SourceResult 数量: {len(result.results)}")

for r in sorted(result.results, key=lambda x: x.provider_name):
    slide_info = ""
    if r.data.get("date_slide_offset_days"):
        slide_info = f" [滑窗+{r.data['date_slide_offset_days']}天, 日期={r.data.get('date_slide_search_date', '?')}]"
    exhausted = " [滑窗已用尽]" if r.data.get("date_slide_exhausted") else ""
    print(f"  {r.provider_name}: status={r.status}{slide_info}{exhausted}")
    if r.data.get("match_count") or r.data.get("odds_count") or r.data.get("event_count"):
        print(f"    match_count={r.data.get('match_count')}, odds_count={r.data.get('odds_count')}, event_count={r.data.get('event_count')}")
    if r.error:
        print(f"    error={r.error[:100]}")