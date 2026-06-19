"""Test API-Football raw endpoints."""
import sys
sys.path.insert(0, ".")

import urllib.request, json, os

env_path = ".env"
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip("\"'")

token = os.environ.get("API_FOOTBALL_TOKEN", "")
base = "https://v3.football.api-sports.io"

def fetch(url):
    req = urllib.request.Request(url, headers={"x-apisports-key": token})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())

# Test Italy (768) with status=FT
print("=== Italy (768) last=10, status=FT ===")
data = fetch(f"{base}/fixtures?team=768&last=10&status=FT")
print(f"Results: {data.get('results', 0)}")
for f in (data.get("response") or [])[:5]:
    fi = f.get("fixture", {})
    te = f.get("teams", {})
    le = f.get("league", {})
    print(f"  {fi.get('date')}: {te.get('home',{}).get('name')} vs {te.get('away',{}).get('name')} ({le.get('name')}) status={fi.get('status',{}).get('short')}")

# Test without status filter
print("\n=== Italy (768) last=10, no status filter ===")
data = fetch(f"{base}/fixtures?team=768&last=10")
print(f"Results: {data.get('results', 0)}")
for f in (data.get("response") or [])[:5]:
    fi = f.get("fixture", {})
    te = f.get("teams", {})
    le = f.get("league", {})
    print(f"  {fi.get('date')}: {te.get('home',{}).get('name')} vs {te.get('away',{}).get('name')} ({le.get('name')}) status={fi.get('status',{}).get('short')}")

# Test Greece (1117)
print("\n=== Greece (1117) last=10, no status filter ===")
data = fetch(f"{base}/fixtures?team=1117&last=10")
print(f"Results: {data.get('results', 0)}")
for f in (data.get("response") or [])[:5]:
    fi = f.get("fixture", {})
    te = f.get("teams", {})
    le = f.get("league", {})
    print(f"  {fi.get('date')}: {te.get('home',{}).get('name')} vs {te.get('away',{}).get('name')} ({le.get('name')}) status={fi.get('status',{}).get('short')}")

# Test with season=2024
print("\n=== Italy (768) season=2024 ===")
data = fetch(f"{base}/fixtures?team=768&season=2024")
print(f"Results: {data.get('results', 0)}")
for f in (data.get("response") or [])[:5]:
    fi = f.get("fixture", {})
    te = f.get("teams", {})
    le = f.get("league", {})
    print(f"  {fi.get('date')}: {te.get('home',{}).get('name')} vs {te.get('away',{}).get('name')} ({le.get('name')}) status={fi.get('status',{}).get('short')}")

# Test the team info endpoint
print("\n=== Team info: Italy (768) ===")
data = fetch(f"{base}/teams?id=768")
print(f"Results: {data.get('results', 0)}")
if data.get("response"):
    t = data["response"][0].get("team", {})
    print(f"  Name: {t.get('name')}, Country: {t.get('country')}, National: {t.get('national')}")

# Test team info: Greece (1117)
print("\n=== Team info: Greece (1117) ===")
data = fetch(f"{base}/teams?id=1117")
print(f"Results: {data.get('results', 0)}")
if data.get("response"):
    t = data["response"][0].get("team", {})
    print(f"  Name: {t.get('name')}, Country: {t.get('country')}, National: {t.get('national')}")