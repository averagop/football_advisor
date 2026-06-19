"""Run full prediction for Greece vs Italy."""
import sys
sys.path.insert(0, ".")

import os
from datetime import datetime, timezone

# Load .env
env_path = ".env"
if os.path.exists(env_path):
    with open(env_path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip("\"'")

from football_advisor.pipeline import PredictionPipeline
from football_advisor.models import MatchRequest

pipeline = PredictionPipeline()
report = pipeline.predict(MatchRequest(
    query="Greece vs Italy",
    home_team="Greece",
    away_team="Italy",
    kickoff_time=datetime(2026, 6, 8, 3, 0, tzinfo=timezone.utc),
    match_id="M_ST_2040143",
))

print(report[:3000])
