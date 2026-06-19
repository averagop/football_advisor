"""
RapidAPI 10 接口 — 正确 endpoint 全量实测
基于用户提供的真实 RapidAPI 代码片段
"""
import json, os, urllib.request, urllib.error
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
env = dict(os.environ)
env_path = ROOT / ".env"
if env_path.exists():
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            env[key] = value

TOKEN = env.get("RAPIDAPI_TOKEN", "")


def call(host: str, path: str, need_key: bool = True) -> dict:
    url = f"https://{host}{path}"
    hdrs = {"x-rapidapi-host": host}
    if need_key:
        hdrs["x-rapidapi-key"] = TOKEN
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"_raw": raw[:300]}
            return {"ok": True, "size": len(raw), "data": data, "url": url}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")[:200]
        except:
            pass
        return {"ok": False, "code": e.code, "body": body, "url": url}
    except Exception as e:
        return {"ok": False, "error": str(e)[:150], "url": url}


def classify(data: Any) -> dict[str, bool]:
    if data is None:
        return {}
    text = json.dumps(data, ensure_ascii=False, default=str).lower()

    def has(*words):
        return any(w.lower() in text for w in words)

    return {
        "odds_1x2": has("odds", "1x2", "home_odds", "away_odds") and not has("rqspf"),
        "odds_double_chance": has("1x", "x2", "1_2", "double"),
        "odds_rqspf": has("rqspf", "handicap", "asian_handicap"),
        "fixtures": has("fixture", "match_date", "kickoff", "event_date"),
        "players": has("player", "squad", "lineup", "roster"),
        "predictions": has("prediction", "probability", "win_percent"),
        "standings": has("standing", "rank", "position"),
        "h2h": has("head_to_head", "h2h", "get-h2h"),
        "stats": has("statistic", "possession", "xg", "shots"),
        "live_odds": has("live", "inplay", "placing"),
        "tournament": has("tournament", "season"),
        "shotmap": has("shotmap", "shot"),
        "highlights": has("highlight", "video", "media"),
        "leagues": has("league", "competition"),
    }


def extract_stats(data: Any) -> str:
    if data is None:
        return ""
    if isinstance(data, list):
        return f"list[{len(data)}], first_keys={list(data[0].keys())[:5] if data and isinstance(data[0], dict) else '?'}"
    if isinstance(data, dict):
        keys = list(data.keys())[:8]
        sub = []
        for k in ("data", "response", "results", "events", "matches", "items", "predictions", "leagues", "teams"):
            v = data.get(k)
            if isinstance(v, list):
                sub.append(f"{k}=len({len(v)})")
            elif isinstance(v, dict):
                sub.append(f"{k}=dict({len(v)}keys)")
        extra = f", {', '.join(sub)}" if sub else ""
        return f"keys={keys}{extra}"
    return "?"


# ═══════════════════════════════════════════
# 10 个 API — 使用用户提供的正确 endpoint
# ═══════════════════════════════════════════
APIS = [
    ("01_FootballPrediction", "football-prediction-api.p.rapidapi.com",
     "/api/v2/predictions?market=classic&iso_date=2026-06-09&federation=UEFA",
     "胜平负赔率+AI预测 — 参数:market/iso_date/federation"),

    ("02_FootballHighlights", "football-highlights-api.p.rapidapi.com",
     "/teams/Spain",
     "球队集锦 — 需要团队名/ID"),

    ("03_SportAPI7", "sportapi7.p.rapidapi.com",
     "/api/v1/event/15508283/atbat/983367/pitches",
     "棒球投球数据 — 需探索足球端点"),

    ("04_AllsportsAPI2", "allsportsapi2.p.rapidapi.com",
     "/api/tournament/17/season/76986/statistics/info",
     "赛事统计 — 参数:tournament_id/season_id"),

    ("05_TodayFootballPrediction", "today-football-prediction.p.rapidapi.com",
     "/leagues/",
     "联赛列表 — 无参数"),

    ("06_SofaScore", "sofascore.p.rapidapi.com",
     "/matches/get-h2h-events",
     "H2H事件 — 需查看文档获取参数"),

    ("07_Betigolo", "betigolo-predictions.p.rapidapi.com",
     "/handball/2021-10-18",
     "手球预测 — 需探索足球端点"),

    ("08_FreeAPILiveFootball", "free-api-live-football-data.p.rapidapi.com",
     "/football-players-search?search=Spain",
     "球员搜索 — 参数:search"),

    ("09_FootAPI7", "footapi7.p.rapidapi.com",
     "/api/match/14015387/shotmap",
     "射门热力图 — 参数:match_id"),

    ("10_OddsFeed", "odds-feed.p.rapidapi.com",
     "/api/v1/markets/feed?placing=LIVE&market_name=1X2&bet_type=BACK&page=0&event_ids=845,123,435,22,842,844,845&period=FULL_TIME_AND_OT",
     "实时赔率市场 — 参数:market_name/event_ids/placing"),
]


results = []
print("=" * 100)
print("RapidAPI 10 接口 — 正确 endpoint 全量实测")
print("=" * 100)

for name, host, path, desc in APIS:
    print(f"\n{'─'*80}")
    print(f"{name}: {desc}")
    print(f"  端点: {path[:80]}")

    r = call(host, path, need_key=(name != "08_FreeAPILiveFootball"))

    if r["ok"]:
        cl = classify(r["data"])
        types = [k for k, v in cl.items() if v]
        stats = extract_stats(r["data"])
        print(f"  ✓ 成功 ({r['size']}B) → {stats}")
        print(f"  数据类型: {types}")
        results.append({
            "name": name, "host": host, "path": path, "status": "ok",
            "size": r["size"], "types": types, **cl,
        })
    else:
        code = r.get("code", "?")
        body = r.get("body", "")[:100]
        print(f"  ✗ HTTP {code} — {body}")
        results.append({
            "name": name, "host": host, "path": path, "status": f"HTTP{code}",
            "body": body,
        })

# ═══════════════════════════════════════════
# 探索非足球 API 的足球端点
# ═══════════════════════════════════════════
print("\n\n" + "=" * 100)
print("探索非足球 API 的足球相关端点")
print("=" * 100)

EXPLORE = [
    ("03_SportAPI7", "sportapi7.p.rapidapi.com", [
        "/api/v1/soccer/matches/2026-06-09",
        "/api/v1/football/fixtures/date/2026-06-09",
        "/api/v1/football/team/search?name=Peru",
        "/api/v1/soccer/team/409",  # Peru ID?
    ]),
    ("07_Betigolo", "betigolo-predictions.p.rapidapi.com", [
        "/football/2026-06-09",
        "/api/v1/football/predictions?date=2026-06-09",
        "/api/football/today",
        "/soccer/2026-06-09",
    ]),
    ("04_AllsportsAPI2", "allsportsapi2.p.rapidapi.com", [
        "/api/football/team/search?name=Peru",
        "/api/soccer/team/409/statistics",
        "/api/football/match/h2h?home=Peru&away=Spain",
    ]),
    ("05_TodayFootballPrediction", "today-football-prediction.p.rapidapi.com", [
        "/predictions/today",
        "/api/predictions/football/2026-06-09",
        "/football/matches?date=2026-06-09",
    ]),
    ("06_SofaScore", "sofascore.p.rapidapi.com", [
        "/api/v1/team/search?name=Spain",
        "/matches/team/409/events",
        "/api/v1/sport/football/events/2026-06-09",
    ]),
]

for name, host, paths in EXPLORE:
    for path in paths:
        r = call(host, path)
        if r["ok"]:
            cl = classify(r["data"])
            types = [k for k, v in cl.items() if v]
            print(f"  {name} → ✓ {path[:60]} → {r['size']}B, types={types[:5]}")
        else:
            print(f"  {name} → ✗ {path[:60]} → HTTP {r.get('code','?')}")

# ═══════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════
print("\n\n" + "=" * 100)
print("汇总能力矩阵")
print("=" * 100)

dims = ["odds_1x2", "odds_double_chance", "odds_rqspf", "fixtures",
        "players", "predictions", "standings", "h2h", "stats",
        "live_odds", "tournament", "shotmap", "highlights", "leagues"]

print(f"{'API':<35} " + " ".join(f"{d[:8]:<9}" for d in dims))
print("-" * (35 + 9*len(dims)))
for r in results:
    row = " ".join(f"{'✓' if r.get(d) else '✗':<9}" for d in dims)
    print(f"{r['name']:<35} {row}")

# 保存
out = ROOT / "data" / "rapidapi_v2_test_results.json"
out.parent.mkdir(parents=True, exist_ok=True)
clean = []
for r in results:
    cr = {}
    for k, v in r.items():
        if isinstance(v, (str, int, float, bool, list, type(None))):
            cr[k] = v
        elif k == "data" and isinstance(v, (dict, list)):
            cr[k] = {"_summary": extract_stats(v)}
        else:
            cr[k] = str(type(v).__name__)
    clean.append(cr)
out.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n保存到: {out}")