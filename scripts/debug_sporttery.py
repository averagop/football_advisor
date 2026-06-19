"""排查 Sporttery 解析为什么返回 0 场"""
import json
import urllib.request

URL = "https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry?channel=c&poolCode=hhad,had,crs,ttg,hafu"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.sporttery.cn/jc/jsq/zqhhgg/",
    "Origin": "https://www.sporttery.cn",
}

req = urllib.request.Request(URL, headers=headers)
with urllib.request.urlopen(req, timeout=15) as resp:
    raw = resp.read()
    data = json.loads(raw)

print(f"Top keys: {list(data.keys())}")
print(f"value keys: {list(data['value'].keys())}")

groups = data["value"]["matchInfoList"]
print(f"Groups: {len(groups)}")

# 取第一组的第一个 match，看完整结构
if groups:
    group = groups[0]
    matches = group.get("subMatchList", [])
    if matches:
        m = matches[0]
        print(f"\n=== 第一场: {m.get('homeTeamAbbName')} vs {m.get('awayTeamAbbName')} ===")
        print(f"Full keys: {sorted(m.keys())}")
        print(f"matchId: {m.get('matchId')}")
        print(f"matchDate: {m.get('matchDate')}")
        print(f"matchTime: {m.get('matchTime')}")
        print(f"leagueAbbName: {m.get('leagueAbbName')}")
        print(f"homeTeamAbbName: {m.get('homeTeamAbbName')}")
        print(f"awayTeamAbbName: {m.get('awayTeamAbbName')}")
        print(f"\nhad: {m.get('had')}")
        print(f"hhad: {m.get('hhad')}")
        
        # Now trace through _official_match_mapping
        print(f"\n=== _official_match_mapping 结果 ===")
        match_date = m.get("matchDate")
        match_time = m.get("matchTime")
        kickoff = f"{match_date}T{match_time}" if match_date and match_time else ""
        print(f"kickoff_time: {kickoff}")
        
        had = m.get("had")
        hhad = m.get("hhad")
        print(f"had is dict: {isinstance(had, dict)}, hhad is dict: {isinstance(hhad, dict)}")
        if isinstance(had, dict):
            print(f"  had keys: {sorted(had.keys())}")
        if isinstance(hhad, dict):
            print(f"  hhad keys: {sorted(hhad.keys())}")
        
        # Check what _three_way_odds gets
        from football_advisor.sporttery_client import _three_way_odds
        spf = _three_way_odds(had if isinstance(had, dict) else None)
        rqspf = _three_way_odds(hhad if isinstance(hhad, dict) else None)
        print(f"_three_way_odds(had): {spf}")
        print(f"_three_way_odds(hhad): {rqspf}")

print(f"\n\n=== 统计: 26场中哪些缺少 had ===")
miss = 0
for group in groups:
    for m in group.get("subMatchList", []):
        had = m.get("had")
        if not had or not isinstance(had, dict) or not had.get("h"):
            miss += 1
            print(f"  缺SPF: {m.get('homeTeamAbbName')} vs {m.get('awayTeamAbbName')} ({m.get('leagueAbbName')})")
            print(f"    had={had}, hhad={m.get('hhad')}")

if miss == 0:
    print("  所有26场都有had!")
else:
    print(f"  共 {miss} 场缺 had")