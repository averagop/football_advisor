"""测试 Sporttery API 5种玩法全量返回"""
import urllib.request
import json

URL = "https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry?channel=c&poolCode=hhad,had,crs,ttg,hafu"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.sporttery.cn/jc/jsq/zqhhgg/",
    "Origin": "https://www.sporttery.cn",
}

req = urllib.request.Request(URL, headers=headers)
with urllib.request.urlopen(req, timeout=15) as resp:
    data = json.loads(resp.read())

print("=== API Response Structure ===")
print(f"Top keys: {list(data.keys())}")
print(f"value keys: {list(data['value'].keys())}")

groups = data["value"]["matchInfoList"]
print(f"\nTotal groups: {len(groups)}")

# 找一个有完整数据的比赛
for group in groups:
    matches = group.get("subMatchList", [])
    for m in matches[:2]:  # 只看前2场
        match_id = m.get("matchId") or m.get("matchNumStr")
        home = m.get("homeTeamAbbName")
        away = m.get("awayTeamAbbName")
        league = m.get("leagueAbbName")
        
        # 检查5种玩法
        had = m.get("had")
        hhad = m.get("hhad")
        crs = m.get("crs")
        ttg = m.get("ttg")
        hafu = m.get("hafu")
        
        print(f"\n--- Match: {home} vs {away} ({league}) ---")
        print(f"  had(SPF):      {type(had).__name__} | {'有' if had else '无'}")
        if had and isinstance(had, dict):
            print(f"    内容: {had}")
        print(f"  hhad(RQSPF):   {type(hhad).__name__} | {'有' if hhad else '无'}")
        if hhad and isinstance(hhad, dict):
            print(f"    内容: {hhad}")
        print(f"  crs(比分):     {type(crs).__name__} | {'有' if crs else '无'}")
        if crs and isinstance(crs, dict):
            print(f"    keys: {list(crs.keys())}")
            # 比分可能是复合结构
            for k, v in crs.items():
                if isinstance(v, (dict, list)):
                    print(f"    {k}: {type(v).__name__} = {str(v)[:200]}")
                else:
                    print(f"    {k}: {v}")
        print(f"  ttg(总进球):   {type(ttg).__name__} | {'有' if ttg else '无'}")
        if ttg and isinstance(ttg, dict):
            print(f"    keys: {list(ttg.keys())}")
            for k, v in ttg.items():
                if isinstance(v, (dict, list)):
                    print(f"    {k}: {type(v).__name__} = {str(v)[:200]}")
                else:
                    print(f"    {k}: {v}")
        print(f"  hafu(半全场):  {type(hafu).__name__} | {'有' if hafu else '无'}")
        if hafu and isinstance(hafu, dict):
            print(f"    keys: {list(hafu.keys())}")
            for k, v in hafu.items():
                if isinstance(v, (dict, list)):
                    print(f"    {k}: {type(v).__name__} = {str(v)[:200]}")
                else:
                    print(f"    {k}: {v}")

# 统计
total_matches = 0
has_had = 0
has_hhad = 0
has_crs = 0
has_ttg = 0
has_hafu = 0

for group in groups:
    matches = group.get("subMatchList", [])
    for m in matches:
        total_matches += 1
        if m.get("had"): has_had += 1
        if m.get("hhad"): has_hhad += 1
        if m.get("crs"): has_crs += 1
        if m.get("ttg"): has_ttg += 1
        if m.get("hafu"): has_hafu += 1

print(f"\n\n=== 统计 ({total_matches} 场比赛) ===")
print(f"  had (胜平负):        {has_had}/{total_matches} ({has_had*100//total_matches}%)")
print(f"  hhad (让球胜平负):   {has_hhad}/{total_matches} ({has_hhad*100//total_matches}%)")
print(f"  crs (比分):          {has_crs}/{total_matches} ({has_crs*100//total_matches}%)")
print(f"  ttg (总进球):        {has_ttg}/{total_matches} ({has_ttg*100//total_matches}%)")
print(f"  hafu (半全场):       {has_hafu}/{total_matches} ({has_hafu*100//total_matches}%)")

# 找一场5种玩法全有的比赛作为样本
print("\n\n=== 找5种玩法全覆盖的比赛 ===")
for group in groups:
    matches = group.get("subMatchList", [])
    for m in matches:
        if m.get("had") and m.get("hhad") and m.get("crs") and m.get("ttg") and m.get("hafu"):
            print(f"  {m.get('homeTeamAbbName')} vs {m.get('awayTeamAbbName')} ({m.get('leagueAbbName')})")
            print(f"  had: {m['had']}")
            print(f"  hhad: {m['hhad']}")
            print(f"  crs: {json.dumps(m['crs'], ensure_ascii=False, indent=2)[:500]}")
            print(f"  ttg: {json.dumps(m['ttg'], ensure_ascii=False, indent=2)[:500]}")
            print(f"  hafu: {json.dumps(m['hafu'], ensure_ascii=False, indent=2)[:500]}")
            break
    else:
        continue
    break