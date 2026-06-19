"""端到端验证：葡萄牙 VS 尼日利亚 完整预测流程（含赛后采集 + 滑窗）。"""
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
print("端到端验证：葡萄牙 VS 尼日利亚")
print(f"开始时间: {datetime.now(timezone.utc).isoformat()}")
print("=" * 60)

pipeline = PredictionPipeline()
report = pipeline.predict(MatchRequest(
    query="Portugal vs Nigeria International Friendly",
    home_team="Portugal",
    away_team="Nigeria",
    kickoff_time=datetime(2026, 6, 10, 19, 0, tzinfo=timezone.utc),
    mode=AnalysisMode.DEEP,
))

# 检查关键输出
checks = {
    "流水线产出": len(report) > 0,
    "包含预测": "预测" in report or "概率" in report or "胜" in report,
}
print("\n" + "=" * 60)
print("验证结果")
print("=" * 60)
for name, ok in checks.items():
    print(f"  {name}: {'OK' if ok else 'FAIL'}")

print(f"\n报告长度: {len(report)} 字符")
print(f"\n报告前 3000 字符:")
print(report[:3000])
if len(report) > 3000:
    print(f"\n... (总计 {len(report)} 字符)")