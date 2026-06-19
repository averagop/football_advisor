"""葡萄牙 VS 尼日利亚友谊赛 - 完整预测测试."""
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
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)

from football_advisor.pipeline import PredictionPipeline
from football_advisor.models import MatchRequest, AnalysisMode

print("=" * 60)
print("葡萄牙 VS 尼日利亚 - 友谊赛预测测试")
print(f"开始时间: {datetime.now(timezone.utc).isoformat()}")
print("=" * 60)

pipeline = PredictionPipeline()
report = pipeline.predict(MatchRequest(
    query="Portugal vs Nigeria",
    home_team="Portugal",
    away_team="Nigeria",
    kickoff_time=datetime(2026, 6, 10, 19, 0, tzinfo=timezone.utc),
    mode=AnalysisMode.DEEP,
))

print("\n" + "=" * 60)
print("预测结果")
print("=" * 60)
print(report[:5000])
if len(report) > 5000:
    print(f"\n... 输出被截断，总长度: {len(report)} 字符")