"""直接测试 API-Football 各端点，看免费层到底返回什么数据。"""
import sys, os, json, urllib.request, urllib.error

env_path = ".env"
if os.path.exists(env_path):
    with open(env_path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

token = os.environ.get("API_FOOTBALL_TOKEN", "")
base_url = os.environ.get("API_FOOTBALL_BASE_URL", "https://v3.football.api-sports.io")

def api_call(path, label):
    url = f"{base_url}{path}"
    req = urllib.request.Request(url, headers={"x-apisports-key": token})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  {label}: ERROR: {e}")
        return None

    if isinstance(data, dict):
        remaining = resp.headers.get("x-ratelimit-requests-remaining", "?")
        results = data.get("response", [])
        count = len(results) if isinstance(results, list) else (1 if results else 0)
        errors = data.get("errors", [])
        print(f"  {label}: {count} results (limit remaining: {remaining})")
        if errors:
            print(f"    errors: {errors}")
        return results
    return None

print("=== 直接测试 API-Football 端点 ===\n")

# 1. 6月12日 fixture（确认摩洛哥vs南非还在）
print("1. 赛程: /fixtures?date=2026-06-12")
r1 = api_call("/fixtures?date=2026-06-12", "fixtures 6/12")
if r1:
    for f in r1[:3]:
        t = f.get("teams", {})
        l = f.get("league", {})
        s = f.get("fixture", {}).get("status", {}).get("short", "")
        print(f"    {l.get('name')} | {t.get('home',{}).get('name')} vs {t.get('away',{}).get('name')} | {s} | id={f.get('fixture',{}).get('id')}")

# 2. 赔率
print("\n2. 赔率: /odds?fixture=1489369")
r2 = api_call("/odds?fixture=1489369", "odds")
if r2:
    for item in r2[:1]:
        for bm in item.get("bookmakers", [])[:1]:
            print(f"    bookmaker: {bm.get('name')}")
            for bet in bm.get("bets", []):
                vals = [(v.get("value"), v.get("odd")) for v in bet.get("values", [])]
                print(f"    {bet.get('name')}: {vals}")

# 3. 阵容
print("\n3. 阵容: /fixtures/lineups?fixture=1489369")
r3 = api_call("/fixtures/lineups?fixture=1489369", "lineups")
if r3:
    for team in r3:
        tname = team.get("team", {}).get("name", "")
        formation = team.get("formation", "")
        starters = len(team.get("startXI", []))
        subs = len(team.get("substitutes", []))
        print(f"    {tname}: formation={formation}, starters={starters}, subs={subs}")

# 4. 球队近期战绩 - 摩洛哥
print("\n4. 近期战绩: /fixtures?team=1530&last=10 (Morocco)")
r4 = api_call("/fixtures?team=1530&last=10", "recent form")
if r4:
    for f in r4[:3]:
        t = f.get("teams", {})
        g = f.get("goals", {})
        d = f.get("fixture", {}).get("date", "")
        s = f.get("fixture", {}).get("status", {}).get("short", "")
        print(f"    {d} | {t.get('home',{}).get('name')} {g.get('home')}-{g.get('away')} {t.get('away',{}).get('name')} | {s}")

# 5. H2H
print("\n5. H2H: /fixtures/headtohead?h2h=1530-1540")
r5 = api_call("/fixtures/headtohead?h2h=1530-1540", "h2h")
if r5:
    for f in r5[:3]:
        t = f.get("teams", {})
        g = f.get("goals", {})
        d = f.get("fixture", {}).get("date", "")
        print(f"    {d} | {t.get('home',{}).get('name')} {g.get('home')}-{g.get('away')} {t.get('away',{}).get('name')}")

# 6. 球队统计
print("\n6. 球队统计: /teams/statistics?team=1530&league=1&season=2026")
r6 = api_call("/teams/statistics?team=1530&league=1&season=2026", "team stats")
if r6:
    fixtures = r6.get("fixtures", {}) if isinstance(r6, dict) else {}
    goals = r6.get("goals", {}) if isinstance(r6, dict) else {}
    print(f"    fixtures: {fixtures}")
    print(f"    goals: {goals}")

# 7. 积分榜
print("\n7. 积分榜: /standings?league=1&season=2026")
r7 = api_call("/standings?league=1&season=2026", "standings")
if r7:
    for lb in r7[:1]:
        standings = lb.get("league", {}).get("standings", [])
        print(f"    standings groups: {len(standings)}")
        for group in standings[:1]:
            for entry in group[:3]:
                t = entry.get("team", {}).get("name", "")
                r = entry.get("rank", "")
                pts = entry.get("points", "")
                print(f"      {r}. {t} - {pts}pts")

# 8. 伤停
print("\n8. 伤停: /injuries?team=1530 (Morocco)")
r8 = api_call("/injuries?team=1530", "injuries")
if r8:
    for inj in r8[:3]:
        p = inj.get("player", {})
        print(f"    {p.get('name')} | type={p.get('type')} | {p.get('reason')}")