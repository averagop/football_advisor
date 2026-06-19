"""RapidAPI v4 — SportAPI7 + SofaScore 正确端点重测"""
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
                    # 深度检查
                    for k in keys:
                        v = data[k]
                        if isinstance(v, list):
                            preview += f" | {k}: [{len(v)}]"
                            if len(v) > 0 and isinstance(v[0], dict):
                                preview += f" keys={list(v[0].keys())[:6]}"
                        elif isinstance(v, dict):
                            preview += f" | {k}: dict keys={list(v.keys())[:6]}"
                        else:
                            preview += f" | {k}={str(v)[:40]}"
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


# ====== SportAPI7: 正确端点 ======
HOST = "sportapi7.p.rapidapi.com"
TODAY = "2026-06-09"
TOMORROW = "2026-06-10"

# Flow 1: 今天所有足球比赛
test(HOST, f"/api/v1/sport/football/scheduled-events/{TOMORROW}", "SPORT7_football_tomorrow")
test(HOST, f"/api/v1/sport/football/scheduled-events/{TODAY}", "SPORT7_football_today")

# 类别
test(HOST, f"/api/v1/sport/football/{TODAY}/0/categories", "SPORT7_categories")

# 也试试明天
test(HOST, f"/api/v1/sport/football/{TOMORROW}/0/categories", "SPORT7_categories_tomorrow")


# ====== SofaScore: 正确端点 ======
HOST_SOFA = "sofascore.p.rapidapi.com"

test(HOST_SOFA, "/categories/list?sport=football", "SOFA_categories_list")
test(HOST_SOFA, "/categories/list?sport=football&limit=5", "SOFA_categories_5")

# 也试其他端点
test(HOST_SOFA, "/tournaments/get-featured-events?sport=football", "SOFA_featured_events")
test(HOST_SOFA, "/tournaments/get-featured-events", "SOFA_featured_events_no_param")

print("\n\n全部测试完成")
