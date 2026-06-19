"""墨西哥 VS 南非 - 2026 世界杯完整预测测试。"""
import sys
sys.path.insert(0, ".")

import os
import logging
from datetime import datetime, timezone

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from football_advisor.pipeline import PredictionPipeline
from football_advisor.models import MatchRequest, AnalysisMode

print("=" * 60)
print("墨西哥 VS 南非 - 2026 世界杯预测")
print(f"开始: {datetime.now(timezone.utc).isoformat()}")
print("=" * 60)

pipeline = PredictionPipeline()
report = pipeline.predict(MatchRequest(
    query="Mexico vs South Africa World Cup 2026",
    match_id="M_ST_2040162",
    home_team="Mexico",
    away_team="South Africa",
    kickoff_time=datetime(2026, 6, 11, 19, 0, tzinfo=timezone.utc),
    mode=AnalysisMode.DEEP,
))

print("\n" + "=" * 60)
print("预测结果")
print("=" * 60)
print(report[:6000])
if len(report) > 6000:
    print(f"\n... (总计 {len(report)} 字符)")
