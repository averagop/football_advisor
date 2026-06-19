"""RapidAPI v3 第三轮 — 深挖已确认可用的API"""
import http.client
import json
import os

TOKEN = os.environ.get("RAPIDAPI_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit("请先在本地环境变量 RAPIDAPI_TOKEN 中配置 RapidAPI 令牌")

def test(host, path, name):
    headers = {
        'x-rapidapi-key': TOKEN,
        'x-rapidapi-host': host,
        'Content-Type': 'application/json'
    }
    try:
        conn = http.client.HTTPSConnection(host, timeout=15)
        conn.request("GET", path, headers=headers)
        res = conn.getresponse()
        body = res.read()
        status = res.status
        size = len(body)
        preview = ""
        
        if status == 200 and size > 0:
            try:
                data = json.loads(body)
                if isinstance(data, dict):
                    keys = list(data.keys())
                    preview = f"keys: {keys}"
                    if 'data' in data and isinstance(data['data'], list) and len(data['data']) > 0:
                        item = data['data'][0]
                        if isinstance(item, dict):
                            preview += f" | data[0].keys: {list(item.keys())[:10]}"
                            preview += f" | total: {len(data['data'])}"
                            # 展示第一项的样本值
                            for k in list(item.keys())[:4]:
                                v = str(item[k])[:60]
                                preview += f" | {k}={v}"
                    if 'plan' in data:
                        preview += f" | plan={data['plan']}"
                elif isinstance(data, list):
                    preview = f"list[{len(data)}]"
                    if len(data) > 0 and isinstance(data[0], dict):
                        preview += f" keys: {list(data[0].keys())[:6]}"
            except Exception as e:
                preview = str(body[:300])
        else:
            preview = body.decode('utf-8', errors='replace')[:200]
        
        print(f"\n{'='*60}")
        print(f"  {name}")
        print(f"  {path}")
        print(f"  S={status} | {size}B")
        print(f"  {preview}")
        return {"name": name, "status": status, "size": size, "preview": preview}
    except Exception as e:
        print(f"\n  {name}: ERROR - {e}")
        return {"name": name, "status": "ERROR", "size": 0, "preview": str(e)[:200]}


# ====== OddsFeed: 用正确的 market_name 重试 ======
HOST_ODDS = "odds-feed.p.rapidapi.com"

# 从events拿到真实event_id
test(HOST_ODDS, "/api/v1/events?date=2026-06-09&limit=3", "ODDS_events_3")

# 正确market: 1X2, OVER_UNDER, ASIAN_HANDICAP, HOME_AWAY, BOTH_TEAMS_TO_SCORE
for market in ["1X2", "OVER_UNDER", "ASIAN_HANDICAP", "HOME_AWAY", "BOTH_TEAMS_TO_SCORE"]:
    test(HOST_ODDS, f"/api/v1/markets/feed?placing=PREMATCH&market_name={market}&bet_type=BACK&page=0&event_ids=845,123,435,22,842,844&period=FULL_TIME", f"ODDS_{market}")

# 试试LIVE
test(HOST_ODDS, "/api/v1/markets/feed?placing=LIVE&market_name=1X2&bet_type=BACK&page=0&event_ids=845,123,435&period=FULL_TIME_AND_OT", "ODDS_LIVE_1X2")


# ====== FootballHighlights: 深挖 match 详情 ======
HOST_HL = "football-highlights-api.p.rapidapi.com"

# 取真实match id
test(HOST_HL, "/matches?date=2026-06-09&limit=1", "HL_match_sample")

# 用真实id查详情 (需要先知道id)
# 假设今天比赛有数据
test(HOST_HL, "/matches?date=2026-06-09&limit=1", "HL_match_detail_placeholder")

# 球队统计
test(HOST_HL, "/teams/statistics/553?fromDate=2023-08-06", "HL_team_stats_553")

# 联赛 standings
test(HOST_HL, "/leagues/104/standings?season=2022", "HL_standings_UCL_2022")

# 试其他可能的路径
for path in [
    "/matches?date=2026-06-09&countryCode=GB&limit=2",
    "/matches?leagueId=104&date=2026-06-09&limit=2",
]:
    test(HOST_HL, path, f"HL_{path}")


# ====== FreeAPILiveFootball: 试更多路径 ======
HOST_FREE = "free-api-live-football-data.p.rapidapi.com"

for path in [
    "/football-live-score",
    "/football-fixtures",
    "/football-standings",
    "/football-team-search?name=Spain",
    "/football-league-search?name=Premier",
    "/football-match?date=2026-06-09",
    "/api/v1/football/leagues",
    "/api/v1/football/teams",
]:
    test(HOST_FREE, path, f"FREE_{path}")


# ====== SofaScore: 最后一次尝试 ======
HOST_SOFA = "sofascore.p.rapidapi.com"

for path in [
    "/tournaments/get-all",
    "/tournaments/get-by-category?category=football",
    "/api/v1/tournaments/featured-events",
    "/featured-events",
]:
    test(HOST_SOFA, path, f"SOFA_{path}")

print("\n\n全部深度测试完成")
