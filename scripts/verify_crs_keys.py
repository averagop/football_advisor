"""验证 CRS 比分 key 解析，确认"其它"标签是否正确"""
import urllib.request
import json

URL = "https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry?channel=c&poolCode=hhad,had,crs,ttg,hafu"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.sporttery.cn/jc/jsq/zqhhgg/",
}

req = urllib.request.Request(URL, headers=headers)
with urllib.request.urlopen(req, timeout=15) as resp:
    data = json.loads(resp.read())

groups = data["value"]["matchInfoList"]

# 找一场有完整 CRS 的比赛
for group in groups:
    for m in group.get("subMatchList", []):
        crs = m.get("crs")
        if crs and isinstance(crs, dict):
            home = m.get("homeTeamAbbName")
            away = m.get("awayTeamAbbName")
            
            # 分类 CRS keys
            regular = {}
            special = {}
            unknown = {}
            for k, v in crs.items():
                if k in ("goalLine", "goalLineValue", "updateDate", "updateTime"):
                    continue
                if k.endswith("f"):  # 资金流向，跳过
                    continue
                if k.startswith("s") and "s" in k[1:]:
                    parts = k[1:].split("s")
                    if len(parts) == 2:
                        h, a = parts[0], parts[1]
                        if h.isdigit() and a.isdigit():
                            regular[f"{h}:{a}"] = v
                        elif a in ("sh", "sd", "sa"):
                            special[k] = v
                        else:
                            unknown[k] = v
                    else:
                        unknown[k] = v
                else:
                    unknown[k] = v
            
            print(f"\n=== {home} vs {away} ===")
            print(f"  常规比分({len(regular)}个): {sorted(regular.keys(), key=lambda x: (int(x.split(':')[0]), int(x.split(':')[1])))[:15]}...")
            print(f"  特殊比分({len(special)}个): {special}")
            if unknown:
                print(f"  未知key({len(unknown)}个): {unknown}")
            
            # 也检查 hafu 和 ttg 的 key
            hafu = m.get("hafu", {})
            if isinstance(hafu, dict):
                hafu_keys = [k for k in hafu if not k.endswith("f") and k not in ("goalLine", "goalLineValue", "updateDate", "updateTime")]
                print(f"  HAFU keys({len(hafu_keys)}个): {sorted(hafu_keys)}")
            
            ttg = m.get("ttg", {})
            if isinstance(ttg, dict):
                ttg_keys = [k for k in ttg if not k.endswith("f") and k not in ("goalLine", "goalLineValue", "updateDate", "updateTime")]
                print(f"  TTG keys({len(ttg_keys)}个): {sorted(ttg_keys)}")
            
            break
    else:
        continue
    break