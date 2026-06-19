"""RapidAPI v3 重测 — 根据文档修正endpoint后重新测试"""
import http.client
import json
import os
import sys

TOKEN = os.environ.get("RAPIDAPI_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit("请先在本地环境变量 RAPIDAPI_TOKEN 中配置 RapidAPI 令牌")

def test(host, path, name, headers_extra=None):
    headers = {
        'x-rapidapi-key': TOKEN,
        'x-rapidapi-host': host,
        'Content-Type': 'application/json'
    }
    if headers_extra:
        headers.update(headers_extra)
    
    try:
        conn = http.client.HTTPSConnection(host, timeout=15)
        conn.request("GET", path, headers=headers)
        res = conn.getresponse()
        body = res.read()
        status = res.status
        size = len(body)
        
        if status == 200 and size > 0:
            try:
                data = json.loads(body)
                keys = list(data.keys()) if isinstance(data, dict) else f"list[{len(data)}]"
            except json.JSONDecodeError:
                keys = str(body[:100])
        else:
            keys = body.decode('utf-8', errors='replace')[:200]
        
        print(f"\n{'='*60}")
        print(f"  {name}")
        print(f"  GET {host}{path}")
        print(f"  STATUS: {status} | SIZE: {size}B")
        print(f"  RESPONSE: {keys[:300]}")
        return {"name": name, "status": status, "size": size, "body_preview": str(keys)[:300]}
    except Exception as e:
        print(f"\n{'='*60}")
        print(f"  {name}: ERROR - {e}")
        return {"name": name, "status": "ERROR", "size": 0, "body_preview": str(e)[:200]}

# ============ 测试 1: Betigolo — 修正为 /football ============
test(
    "betigolo-predictions.p.rapidapi.com",
    "/football/2021-10-18",
    "1_Betigolo_/football/2021-10-18"
)

# ============ 测试 2: FootballHighlights — 修正为 /countries ============
test(
    "football-highlights-api.p.rapidapi.com",
    "/countries",
    "2_FootballHighlights_/countries"
)

# ============ 测试 3: FootballHighlights — /leagues ============
test(
    "football-highlights-api.p.rapidapi.com",
    "/leagues",
    "3_FootballHighlights_/leagues"
)

# ============ 测试 4: SportAPI7 — 尝试足球端点 ============
# 尝试常见足球REST结构
for path in [
    "/api/v1/football/leagues",
    "/api/v1/football/matches",
    "/api/v1/soccer/leagues",
    "/api/v1/sport/1/leagues",
    "/api/v1/tournaments?category=football",
]:
    test(
        "sportapi7.p.rapidapi.com",
        path,
        f"4_SportAPI7_{path}"
    )

# ============ 测试 5: FreeAPILiveFootball — 检查更多端点 ============
for path in [
    "/football-leagues",
    "/football-players-search?search=m",
    "/leagues",
    "/football-live-scores",
]:
    test(
        "free-api-live-football-data.p.rapidapi.com",
        path,
        f"5_FreeAPILiveFootball_{path}"
    )

# ============ 测试 6: OddsFeed — 再试 ============
test(
    "odds-feed.p.rapidapi.com",
    "/api/v1/markets/feed?placing=LIVE&market_name=1X2&bet_type=BACK&page=0&period=FULL_TIME_AND_OT",
    "6_OddsFeed_retry"
)

# ============ 测试 7: SofaScore — 带参数 ============
# 先尝试events列表
for path in [
    "/tournaments/get-featured-events?category=football",
    "/tournaments/get-scheduled-events?date=2026-06-09",
    "/matches/get-h2h-events?matchId=123",
]:
    test(
        "sofascore.p.rapidapi.com",
        path,
        f"7_SofaScore_{path}"
    )

print("\n\n" + "="*60)
print("  全部测试完成")
print("="*60)
