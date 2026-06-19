"""直接测试修正后的 API 调用。"""
import json, urllib.request, urllib.error, os

# Load env
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
base = "https://v3.football.api-sports.io"

def call(path, label):
    url = f"{base}{path}"
    req = urllib.request.Request(url, headers={"x-apisports-key": token})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  {label}: ERROR: {e}")
        return None
    errors = data.get("errors", []) if isinstance(data, dict) else []
    resp_data = data.get("response", []) if isinstance(data, dict) else []
    count = len(resp_data) if isinstance(resp_data, list) else (1 if resp_data else 0)
    print(f"  {label}: {count} results" + (f" ERR:{errors}" if errors else ""))
    return resp_data

print("=== 修正后 API 测试 ===")
print("\n1. 近期战绩 (no &last=):")
r1 = call("/fixtures?team=16", "recent form")
if r1:
    for f in r1[:3]:
        t = f.get("teams", {})
        g = f.get("goals", {})
        d = f.get("fixture", {}).get("date", "")
        print(f"    {d} | {t.get('home',{}).get('name')} {g.get('home')}-{g.get('away')} {t.get('away',{}).get('name')}")

print("\n2. H2H (16-1531):")
r2 = call("/fixtures/headtohead?h2h=16-1531", "h2h")
if r2:
    for f in r2[:3]:
        t = f.get("teams", {})
        g = f.get("goals", {})
        d = f.get("fixture", {}).get("date", "")
        print(f"    {d} | {t.get('home',{}).get('name')} {g.get('home')}-{g.get('away')} {t.get('away',{}).get('name')}")

print("\n3. 球队统计 (season=2024):")
r3 = call("/teams/statistics?team=16&league=1&season=2024", "team stats")
if r3 and isinstance(r3, dict):
    fixtures = r3.get("fixtures", {})
    goals = r3.get("goals", {})
    print(f"    played={fixtures.get('played',{}).get('total')}")
    print(f"    wins={fixtures.get('wins',{}).get('total')}")
    print(f"    goals_for={goals.get('for',{}).get('total',{}).get('total')}")

print("\n4. 积分榜 (season=2024):")
r4 = call("/standings?league=1&season=2024", "standings")
if r4:
    for lb in r4[:1]:
        for group in lb.get("league", {}).get("standings", [])[:1]:
            for entry in group[:3]:
                print(f"    {entry.get('rank')}. {entry.get('team',{}).get('name')} - {entry.get('points')}pts")

print("\n5. 伤停 (season=2024):")
r5 = call("/injuries?team=16&season=2024", "injuries")
if r5:
    for inj in r5[:3]:
        p = inj.get("player", {})
        print(f"    {p.get('name')} | {p.get('type')} | {p.get('reason')}")