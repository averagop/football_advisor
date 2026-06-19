"""RapidAPI v3 深度测试 — 对成功的API深入探索"""
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
        data_types = []
        
        if status == 200 and size > 0:
            try:
                data = json.loads(body)
                if isinstance(data, dict):
                    keys = list(data.keys())
                    preview = f"keys: {keys}"
                    # 检查嵌套结构
                    if 'data' in data and isinstance(data['data'], list) and len(data['data']) > 0:
                        item = data['data'][0]
                        if isinstance(item, dict):
                            preview += f" | data[0].keys: {list(item.keys())[:8]}"
                            preview += f" | total_data_items: {len(data['data'])}"
                        if 'pagination' in data:
                            preview += f" | pagination: {data['pagination']}"
                    if 'plan' in data:
                        preview += f" | plan: {data['plan']}"
                elif isinstance(data, list):
                    preview = f"list[{len(data)}]"
                    if len(data) > 0 and isinstance(data[0], dict):
                        preview += f" keys: {list(data[0].keys())[:6]}"
            except Exception as e:
                preview = str(body[:200])
        else:
            preview = body.decode('utf-8', errors='replace')[:200]
        
        print(f"\n{'='*60}")
        print(f"  {name}")
        print(f"  {path}")
        print(f"  STATUS={status} | SIZE={size}B")
        print(f"  {preview}")
        return {"name": name, "status": status, "size": size, "preview": preview}
    except Exception as e:
        print(f"\n  {name}: ERROR - {e}")
        return {"name": name, "status": "ERROR", "size": 0, "preview": str(e)[:200]}


# ====== FootballHighlights: 深挖所有端点 ======
HOST_HL = "football-highlights-api.p.rapidapi.com"

# 国家队
test(HOST_HL, "/countries", "HL_countries")
test(HOST_HL, "/countries/GB", "HL_country_GB")

# 联赛
test(HOST_HL, "/leagues?limit=5", "HL_leagues_5")
test(HOST_HL, "/leagues/104", "HL_league_104(UCL)")

# 球队
test(HOST_HL, "/teams?limit=5", "HL_teams_5")
test(HOST_HL, "/teams?name=Spain", "HL_teams_Spain")

# 比赛
test(HOST_HL, "/matches?date=2026-06-09&limit=3", "HL_matches_today")
test(HOST_HL, "/matches?date=2026-06-08&limit=3", "HL_matches_yesterday")
test(HOST_HL, "/matches?leagueId=104&limit=3", "HL_matches_UCL")


# ====== FreeAPILiveFootball: 深挖 ======
HOST_FREE = "free-api-live-football-data.p.rapidapi.com"

test(HOST_FREE, "/football-players-search?search=Spain", "FREE_players_Spain")
test(HOST_FREE, "/football-players-search?search=Messi", "FREE_players_Messi")
test(HOST_FREE, "/football-players-search?search=Ronaldo", "FREE_players_Ronaldo")

# 尝试其他可能的端点
for path in [
    "/football-teams",
    "/football-team?name=Spain",
    "/football-fixtures?date=2026-06-09",
    "/football-matches?date=2026-06-09",
    "/football-standings?league=39",
    "/football-live-scores",
    "/football-live",
]:
    test(HOST_FREE, path, f"FREE_{path}")


# ====== OddsFeed: 带event_ids ======
HOST_ODDS = "odds-feed.p.rapidapi.com"

# 用事件ID列表重试
test(HOST_ODDS, "/api/v1/markets/feed?placing=LIVE&market_name=1X2&bet_type=BACK&page=0&event_ids=845,123,435,22,842,844&period=FULL_TIME_AND_OT", "ODDS_with_event_ids")

# 试不同的market_name
for market in ["MATCH_ODDS", "OVER_UNDER_25", "BOTH_TEAMS_TO_SCORE", "CORRECT_SCORE"]:
    test(HOST_ODDS, f"/api/v1/markets/feed?placing=PREMATCH&market_name={market}&bet_type=BACK&page=0&event_ids=845,123,435&period=FULL_TIME", f"ODDS_{market}")

# 搜索事件
test(HOST_ODDS, "/api/v1/search?text=Spain", "ODDS_search_Spain")
test(HOST_ODDS, "/api/v1/events?date=2026-06-09", "ODDS_events_today")


# ====== SofaScore: 不同参数 ======
HOST_SOFA = "sofascore.p.rapidapi.com"

test(HOST_SOFA, "/tournaments/get-featured-events", "SOFA_featured_events")
test(HOST_SOFA, "/tournaments/get-matches?tournamentId=1&seasonId=2026", "SOFA_matches_tournament")
test(HOST_SOFA, "/matches/get-h2h-events?homeTeamId=1&awayTeamId=2", "SOFA_h2h_teams")

print("\n\n全部深度测试完成")
