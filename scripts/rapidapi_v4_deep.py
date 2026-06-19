"""RapidAPI v4 deep — SportAPI7 深度探索 + SofaScore 更多端点"""
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
                    for k in keys:
                        v = data[k]
                        if isinstance(v, list):
                            preview += f" | {k}: [{len(v)}]"
                            if len(v) > 0 and isinstance(v[0], dict):
                                preview += f" keys={list(v[0].keys())[:8]}"
                                # 展示第一项的前3个字段
                                for fk in list(v[0].keys())[:3]:
                                    fv = str(v[0][fk])[:60]
                                    preview += f" | {fk}={fv}"
                        elif isinstance(v, dict):
                            preview += f" | {k}: dict keys={list(v.keys())[:6]}"
                        else:
                            preview += f" | {k}={str(v)[:50]}"
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
        print(f"  {preview[:500]}")
        return {"name": name, "status": status, "size": size, "preview": preview}
    except Exception as e:
        print(f"\n  {name}: ERROR - {e}")
        return {"name": name, "status": "ERROR", "size": 0, "preview": str(e)[:200]}


HOST = "sportapi7.p.rapidapi.com"
TODAY = "2026-06-09"

# ====== 先获取一个真实event ======
print("=== 获取第一个比赛 ===")
conn = http.client.HTTPSConnection(HOST, timeout=15)
headers = {
    'x-rapidapi-key': TOKEN,
    'x-rapidapi-host': HOST,
    'Content-Type': 'application/json'
}
conn.request("GET", f"/api/v1/sport/football/scheduled-events/{TODAY}", headers=headers)
res = conn.getresponse()
data = json.loads(res.read())
first_event = data["events"][0]
event_id = first_event["id"]
tid = first_event["tournament"]["uniqueTournament"]["id"]
sid = first_event["season"]["id"]

home = first_event["homeTeam"]["name"]
away = first_event["awayTeam"]["name"]
print(f"  First match: {home} vs {away} (event={event_id}, tid={tid}, sid={sid})")
print(f"  Full first_event keys: {list(first_event.keys())}")
print(f"  tournament keys: {list(first_event['tournament'].keys())}")
print(f"  season: {first_event['season']}")
print(f"  status: {first_event['status']}")


# ====== 深度测试每个子端点 ======
print("\n\n=== 深度测试 ===")

# Event details
test(HOST, f"/api/v1/event/{event_id}", "SPORT7_event_detail")

# Incidents (goals, cards, subs)
test(HOST, f"/api/v1/event/{event_id}/incidents", "SPORT7_incidents")

# Lineups
test(HOST, f"/api/v1/event/{event_id}/lineups", "SPORT7_lineups")

# Statistics
test(HOST, f"/api/v1/event/{event_id}/statistics", "SPORT7_statistics")

# Standings
test(HOST, f"/api/v1/unique-tournament/{tid}/season/{sid}/standings/total", "SPORT7_standings_total")
test(HOST, f"/api/v1/unique-tournament/{tid}/season/{sid}/standings/home", "SPORT7_standings_home")
test(HOST, f"/api/v1/unique-tournament/{tid}/season/{sid}/standings/away", "SPORT7_standings_away")

# Odds
test(HOST, f"/api/v1/event/{event_id}/odds/1/all", "SPORT7_odds_provider1")

# H2H (尝试)
test(HOST, f"/api/v1/event/{event_id}/h2h", "SPORT7_h2h")


# ====== SofaScore: 更多端点 ======
HOST_SOFA = "sofascore.p.rapidapi.com"

# 试从categories拿到event
test(HOST_SOFA, "/tournaments/get-scheduled-events?sport=football&date=2026-06-09", "SOFA_scheduled")
test(HOST_SOFA, "/tournaments/get-matches?tournamentId=1&seasonId=63820", "SOFA_matches_t1")
test(HOST_SOFA, "/matches/get-h2h?homeTeamId=1&awayTeamId=2", "SOFA_h2h_teams")


# ====== 获取第一个category ======
resp2 = test(HOST, f"/api/v1/sport/football/{TODAY}/0/categories", "SPORT7_cats_detail")

# 取第一个category的事件
conn2 = http.client.HTTPSConnection(HOST, timeout=15)
conn2.request("GET", f"/api/v1/sport/football/{TODAY}/0/categories", headers=headers)
cats = json.loads(conn2.read())["categories"]
if cats:
    cat_id = cats[0]["category"]["id"]
    cat_name = cats[0]["category"]["name"]
    print(f"\n\n=== Category: {cat_name} (id={cat_id}) ===")
    test(HOST, f"/api/v1/category/{cat_id}/scheduled-events/{TODAY}", f"SPORT7_cat_{cat_name}")

print("\n\n全部深度测试完成")
