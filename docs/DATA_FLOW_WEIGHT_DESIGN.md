# 多源数据流程多维度权重设计方案

> 2026-06-11 | v5 — iSports 复测完成；因 HTTPS 证书异常且官方仅公布 HTTP 接口，暂不进入生产链

---

## 一、实测结论：真正可用的数据源有 15 个

### 1.1 可用源清单（13个全功能 + 2个部分可用）

| 序号 | 数据源 | 类型 | 状态 | 响应量 | 核心数据类型 |
|---|---|---|---|---|---|
| 1 | **API-Football** | 独立API | ✅ | ~5KB/场 | 赛程/阵容/近期状态/H2H/技术统计 |
| 2 | **SportAPI7** | RapidAPI | ✅✅ **v4复活** | **451KB/110场** | 赛程+阵容+事件+积分榜+赔率(17盘口)+H2H |
| 3 | **FootballHighlights** | RapidAPI | ✅ | 25-31KB | 258国/893联赛/23967队/156场+球队统计 |
| 4 | **Sporttery 竞彩** | 官方爬虫 | ✅ | ~2KB/场 | SPF/RQSPF/比分/总进球/半全场 |
| 5 | **OddsFeed** | RapidAPI | ✅ | 59-81KB | 亚盘(独有)+大小球(独有)+1X2+事件目录 |
| 6 | **TheOddsAPI** | 独立API | ✅ | ~1KB/场 | 多博彩公司聚合赔率 |
| 7 | **TheSportsDB** | 独立API | ✅ | ~3KB/场 | 阵容/H2H |
| 8 | **TodayFootballPrediction** | RapidAPI | ✅ | 29KB | 502联赛目录+集锦 |
| 9 | **AllsportsAPI2** | RapidAPI | ✅ | 17KB | 积分榜+技术统计+实时赔率+射门热力图 |
| 10 | **FootAPI7** | RapidAPI | ✅ | 28KB | 球员+射门热力图+积分榜 |
| 11 | **football-data.org** | 独立API | ✅ | ~1KB/场 | 积分榜/赛程 |
| 12 | **新闻搜索** | 独立 | ✅ | 文本 | 赛前情报/战术分析 |
| 13 | **Football Prediction API** | RapidAPI | ✅ | 3.1KB | 1X2+双重机会+AI预测 |
| 14 | **SofaScore** | RapidAPI | ⚠️ v4 部分 | 94KB | 271个足球分类目录 |
| 15 | **FreeAPILiveFootball** | RapidAPI | ⚠️ 部分 | 4-6KB | 球员搜索(仅此端点可用) |

### 1.2 仍然不可用或不满足生产安全要求

| 数据源 | 最终结果 | 处理 |
|---|---|---|
| **Betigolo** | 401 "disabled for your subscription" | **已删除**（你决定放弃） |
| SportAPI7 | → 已复活（v4） | — |
| SofaScore | → 已部分复活（v4） | — |
| **iSports-API** | 已配置令牌；`https://api.isportsapi.com` 与 `https://api2.isportsapi.com` 均证书主机名不匹配；官方文档仅公布 HTTP 主机 | **不发送令牌到明文 HTTP，暂不进入生产链** |

### 1.3 v3→v4 关键变化

| API | v3结果 | v4结果 | 复活原因 |
|---|---|---|---|
| **SportAPI7** | 全部404 | ✅ **#1加权分8.0** | 拿到了正确的 Quick Start Guide。正确端点: `/api/v1/sport/football/scheduled-events/{date}` 返回110场/天, 451KB，每个比赛可查: event详情/incidents/lineups(21KB)/standings(total+home+away)/odds(17盘口)/H2H(含manager duel) |
| **SofaScore** | 204 No Content | ⚠️ **271个足球分类** | 正确端点: `/categories/list?sport=football` 返回94KB分类数据。但 tournament 级端点仍204 |
| **Betigolo** | 401 | **已删除** | 你决定放弃 |

### 1.4 iSports v5 复测结论

- 2026-06-11 使用本地已更新的 `ISPORTS_API_TOKEN` 调用赛程检测，结果为 `status=error`、`http_status=n/a`、`count=0`。
- 两个 HTTPS 主机均在 TLS 证书校验阶段失败，令牌未获得安全发送条件。
- 官方文档列出的 `http://api.isportsapi.com` 和 `http://api2.isportsapi.com` 可建立匿名连接，但明文 HTTP 不允许承载 API 令牌。
- 客户端与冒烟测试已增加明文传输拦截；HTTP 返回 `skipped_insecure_transport`，HTTPS 传输失败返回脱敏的 `transport_error`。

---

## 二、全部可用源的实测数据能力

### 2.1 原始能力矩阵（15源完整版）

| 能力维度 | SportAPI7(R) | API-Football | FootballHighlights(R) | Sporttery | OddsFeed(R) | TheOddsAPI | TheSportsDB | TodayFootPred(R) | AllsportsAPI2(R) | FootAPI7(R) | football-data | FootballPred(R) | SofaScore(R) | FreeLiveFoot(R) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **1X2 赔率** | ✓(17盘口!) | ✓(免费受限) | ✗ | ✓(SPF) | ✓ | ✓(多公司) | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ |
| **让球赔率(RQSPF)** | ✗ | ✗ | ✗ | ✓ | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **亚盘(Asian Handicap)** | ✗ | ✗ | ✗ | ✗ | ✓**(独有)** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **大小球(Over/Under)** | ✗ | ✗ | ✗ | ✗ | ✓**(独有)** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **比分/总进球/半全场** | ✗ | ✗ | ✗ | ✓(需扩poolCode) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **双重机会(1X/12/X2)** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✓(独有) | ✗ | ✗ |
| **实时赔率(Live)** | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ | ✓(独有) | ✗ | ✗ | ✗ | ✗ | ✗ |
| **赛程/开球时间** | ✓(110场/天) | ✓ | ✓(156场/天) | ✗ | ✓(事件目录) | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ |
| **比赛状态实时** | ✓(status code) | ✗ | ✓(1分钟刷新) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **事件(进球/红黄牌)** | ✓(incidents) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **球员/阵容** | ✓(21KB+formation) | ✓ | ✗ | ✗ | ✗ | ✗ | ✓(最强) | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ | ✓ |
| **近期状态(近N场)** | ✗ | ✓(最强) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **历史交锋(H2H)** | ✓(含manager) | ✓ | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **技术统计(xG/射门等)** | ✓(赛后) | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **球队赛季统计** | ✗ | ✓ | ✓(主/客/总) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **射门热力图** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✓(独有) | ✓(独有) | ✗ | ✗ | ✗ | ✗ |
| **积分榜** | ✓(total+home+away) | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ | ✓(最强) | ✗ | ✗ | ✗ |
| **AI 预测** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✓(独有) | ✗ | ✗ |
| **联赛目录/发现** | 69 categories | ~50+ | 893联赛 | 仅受注 | ✗ | ~20+ | ~30 | 502(独有) | 锦标赛 | 按场次 | ~20 | 5 | 271分类 | ✗ |
| **教练H2H** | ✓**(独有!)** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **每请求数据量** | 451KB/110场 | ~5KB | 25-31KB | ~2KB | 59-81KB | ~1KB | ~3KB | 29KB | 17KB | 28KB | ~1KB | 3.1KB | 94KB | 4-6KB |

> **(R)** = RapidAPI 平台

### 2.2 SportAPI7 独有能力详解（v4 新登顶）

| 端点 | 数据 | 实测结果 |
|---|---|---|
| `/api/v1/sport/football/scheduled-events/{date}` | 110场/天, 451KB | ✅ 含tournament/season/homeTeam/awayTeam/status/startTimestamp |
| `/api/v1/event/{id}` | 比赛详情 | ✅ 9KB, eventState/roundInfo/customId/homeScore/awayScore/time |
| `/api/v1/event/{id}/incidents` | 进球/红黄牌/替换/VAR | ✅ (未开赛=0条) |
| `/api/v1/event/{id}/lineups` | 阵容+阵型 | ✅ **21KB!** players+supportStaff+formation+playerColor+missingPlayers |
| `/api/v1/event/{id}/statistics` | 技术统计 | ⚠️ 未开赛返回404，赛后待验证 |
| `/api/v1/event/{id}/odds/{providerId}/all` | 赔率 | ✅ **17个盘口!** 8KB, marketId 1-17 |
| `/api/v1/event/{id}/h2h` | H2H+教练对战 | ✅ teamDuel{homeWins/awayWins/draws} + managerDuel — **独有!** |
| `/api/v1/unique-tournament/{id}/season/{sid}/standings/total` | 总积分榜 | ✅ 21KB |
| `/api/v1/unique-tournament/{id}/season/{sid}/standings/home` | 主场积分榜 | ✅ 21KB |
| `/api/v1/unique-tournament/{id}/season/{sid}/standings/away` | 客场积分榜 | ✅ 21KB |
| `/api/v1/sport/football/{date}/0/categories` | 69个分类 | ✅ 38KB |
| `/api/v1/category/{id}/scheduled-events/{date}` | 按分类查比赛 | ✅ |

---

## 三、多维度评分体系

### 3.1 评分维度定义（不变）

| 维度 | 英文 | 权重 | 打分标准 |
|---|---|---|---|
| 数据完整度 | Completeness | **0.25** | 覆盖的数据种类数 |
| 数据新鲜度 | Freshness | **0.15** | 实时=10，<30分钟=8，当天=4 |
| 数据准确度 | Accuracy | **0.20** | 官方=10，多源交叉=8，AI预测=4 |
| 覆盖广度 | Coverage | **0.10** | >100联赛=10，50-100=8 |
| 数据独特性 | Uniqueness | **0.15** | 3种独有=10，2种=8，1种=6 |
| 稳定性 | Stability | **0.10** | 免费不限=10，有月限=6-8 |
| 响应速度 | Latency | **0.05** | <1s=8，<5s=4 |

> 加权公式：`总分 = C×0.25 + F×0.15 + A×0.20 + Cv×0.10 + U×0.15 + S×0.10 + L×0.05`

### 3.2 v5 评分结果（15个可用源 + 1个待修复源）

| 排名 | 数据源 | C | F | A | Cv | U | S | L | **加权分** | 主力场景 |
|---|---|---|---|---|---|---|---|---|---|---|
| **1** | **SportAPI7(R)** | 9 | 8 | 8 | 10 | 8 | 6 | 3 | **8.00** | 赛程+阵容+事件+积分榜+赔率(17盘口)+H2H+教练H2H |
| 2 | API-Football | 9 | 8 | 8 | 10 | 4 | 6 | 6 | **7.80** | 赛程+状态+统计+阵容+H2H |
| 2 | FootballHighlights(R) | 6 | 8 | 8 | 10 | 8 | **10** | 6 | **7.80** | 比赛数据+球队统计+联赛目录 |
| 4 | Sporttery 竞彩 | 5 | 10 | 10 | 4 | 8 | 6 | 8 | **7.60** | 官方赔率(SPF/RQSPF/比分/总进球/半全场) |
| 5 | OddsFeed(R) | 4 | 8 | 8 | 6 | **10** | 6 | 4 | **6.70** | 亚盘+大小球+1X2+事件目录 |
| 6 | TheOddsAPI | 3 | 8 | 8 | 6 | 4 | 8 | 6 | **6.40** | 多博彩公司赔率聚合 |
| 7 | TheSportsDB | 4 | 6 | 6 | 6 | 6 | 10 | 8 | **6.00** | 阵容+H2H |
| 8 | TodayFootPred(R) | 2 | 6 | 6 | 10 | 10 | 6 | 4 | **5.80** | 502联赛目录+集锦 |
| 9 | AllsportsAPI2(R) | 4 | 6 | 6 | 6 | 8 | 6 | 4 | **5.60** | 实时赔率+技术统计+射门 |
| 10 | FootAPI7(R) | 3 | 6 | 7 | 4 | 8 | 6 | 4 | **5.50** | 球员+射门热力图+积分榜 |
| 11 | football-data.org | 3 | 8 | 8 | 6 | 2 | 6 | 8 | **5.40** | 积分榜+赛程 |
| 12 | 新闻搜索 | 1 | 10 | 6 | 10 | 10 | 4 | 4 | **5.20** | 文本情报 |
| 13 | FootballPred(R) | 4 | 4 | 4 | 4 | 8 | 6 | 8 | **4.80** | 低级别联赛+双重机会+AI |
| 14 | FreeAPILiveFootball(R) | 1 | 6 | 6 | 6 | 4 | 6 | 8 | **4.55** | 球员搜索 |
| 15 | SofaScore(R) ⚠️ | 1 | 6 | 6 | 8 | 4 | 4 | 4 | **4.35** | 271足球分类目录 |
| 16 | iSports-API ⚠️ | 2 | 4 | 3 | 2 | 2 | 1 | 6 | **2.60** | 接口能力丰富但本轮无法安全鉴权验证，不进入生产链 |

---

## 四、按数据类型的数据流优先级链（v5）

```
【赔率 Odds — W=40（最高优先级，无赔率=No Bet）】
  Priority 1: Sporttery 竞彩 → 官方 SPF+RQSPF+比分+总进球+半全场
  Priority 2: SportAPI7 (RapidAPI) — v4新增！→ 17个赔率盘口(8KB)
  Priority 3: OddsFeed (RapidAPI) → 1X2+亚盘+大小球
  Priority 4: TheOddsAPI → 多博彩公司聚合
  Priority 5: AllsportsAPI2 (RapidAPI) → 实时赔率(live_odds)
  Priority 6: Football Prediction API → 低级别联赛1X2+双重机会

【亚盘 Asian Handicap — W=15】
  Priority 1: OddsFeed (RapidAPI) — 唯一来源

【大小球 Over/Under — W=10】
  Priority 1: OddsFeed (RapidAPI) — 唯一来源

【赛程 Schedule — W=25】
  Priority 1: SportAPI7 (RapidAPI) — v4新增！→ 110场/天, 451KB
  Priority 2: API-Football → 最全面的赛程
  Priority 3: FootballHighlights (RapidAPI) → 156场/天
  Priority 4: OddsFeed (RapidAPI) → 100事件/天
  Priority 5: football-data.org

【阵容 Squad — W=15】
  Priority 1: SportAPI7 (RapidAPI) — v4新增！→ 21KB阵容(players+formation+supportStaff)
  Priority 2: TheSportsDB → 专攻阵容
  Priority 3: API-Football → lineup/squad
  Priority 4: FootAPI7 (RapidAPI) → 球员数据
  Priority 5: FreeAPILiveFootball (RapidAPI) → 球员搜索

【近期状态 Recent Form — W=10】
  Priority 1: API-Football → fetch_recent_friendlies()

【历史交锋 H2H — W=5】
  Priority 1: SportAPI7 (RapidAPI) — v4新增！→ teamDuel + managerDuel(独有)
  Priority 2: TheSportsDB
  Priority 3: API-Football

【积分榜 Standings — W=3】
  Priority 1: SportAPI7 (RapidAPI) — v4新增！→ total+home+away 三种积分榜(21KB each)
  Priority 2: football-data.org

【比赛事件 Incidents — W=5（新增）】
  Priority 1: SportAPI7 (RapidAPI) — v4新增！→ 进球/红黄牌/替换/VAR

【球队统计 Team Stats — W=8】
  Priority 1: FootballHighlights (RapidAPI) → 主/客/总 胜/负/平/进球/失球
  Priority 2: API-Football → xG/射门/控球
  Priority 3: SportAPI7 (RapidAPI) → statistics(赛后)

【比赛状态 Match State — W=8】
  Priority 1: FootballHighlights (RapidAPI) → 1分钟刷新
  Priority 2: SportAPI7 (RapidAPI) → status.code/description/type

【外部预测 External Prediction — W=2】
  Priority 1: Football Prediction API (RapidAPI)

【联赛目录 League Discovery — W=3】
  Priority 1: FootballHighlights (RapidAPI) → 893联赛
  Priority 2: TodayFootballPrediction (RapidAPI) → 502联赛+集锦
  Priority 3: SofaScore (RapidAPI) → 271分类

【新闻/情报 News】
  Priority 1: SearXNG → Serper
```

---

## 五、数据流程架构（v4最终版）

```
predict(home_team, away_team, kickoff_time)
  │
  ├── ★ Phase 1: 并行同步 (10源并行)
  │     ├── 竞彩爬虫 (Sporttery)
  │     ├── SportAPI7 (RapidAPI)          ← v4复活! #1源
  │     ├── API-Football
  │     ├── FootballHighlights (RapidAPI)
  │     ├── OddsFeed (RapidAPI)
  │     ├── TheOddsAPI
  │     ├── TheSportsDB
  │     ├── football-data.org
  │     ├── Football Prediction API (RapidAPI)
  │     └── asyncio.gather → results
  │
  ├── ★ Phase 2: 数据缺口分析
  ├── ★ Phase 3: 串行补漏（按优先级链）
  └── ★ Phase 4: 新闻搜索
```

---

## 六、实施优先级

| 优先级 | 改动 | 影响的API | 涉及文件 |
|---|---|---|---|
| **P0 立即** | 修复 A(LEFT JOIN) + B(poolCode) + C(时间窗口48h) | db/sporttery/third_party | db_schema.py, sporttery_client.py, third_party_odds_client.py |
| **P1 高** | 集成 **SportAPI7**: 赛程+阵容+赔率(17盘口)+积分榜+H2H+事件 | SportAPI7(RapidAPI) | multi_source_coordinator.py |
| **P1 高** | 集成 FootballHighlights: 比赛赛程+球队统计+比赛状态 | FootballHighlights(RapidAPI) | multi_source_coordinator.py |
| **P1 高** | 集成 OddsFeed: 亚盘+大小球+1X2赔率+事件目录 | OddsFeed(RapidAPI) | multi_source_coordinator.py |
| **P2 中** | 集成 FreeAPILiveFootball 球员搜索 + SofaScore 分类目录 | FreeAPILiveFootball, SofaScore | multi_source_coordinator.py |
| **P2 中** | 实现 Phase 2(缺口分析) + Phase 3(串行补漏) | 全部源 | multi_source_coordinator.py, sync.py |

---

## 七、集成指南（新增/更新源）

### 7.1 SportAPI7 — 最强 RapidAPI 源（v4复活）

```
Base URL: https://sportapi7.p.rapidapi.com
Headers: x-rapidapi-key + x-rapidapi-host

核心流程 (Flow 1):
  Step 1: GET /api/v1/sport/football/scheduled-events/{date}
          → 110场比赛, 451KB, 返回 events[].id
  Step 2: 对每个 event:
    GET /api/v1/event/{id}              → 比赛详情(9KB)
    GET /api/v1/event/{id}/incidents    → 事件(进球/红黄牌/VAR)
    GET /api/v1/event/{id}/lineups      → 阵容+阵型(21KB)
    GET /api/v1/event/{id}/statistics   → 技术统计(赛后)
    GET /api/v1/event/{id}/odds/1/all   → 赔率(17个盘口, 8KB)
    GET /api/v1/event/{id}/h2h          → H2H+教练对战(独有!)
    GET /api/v1/unique-tournament/{tid}/season/{sid}/standings/{type}
                                        → 积分榜(total/home/away, 21KB)

分类流程 (Flow 2):
  Step 1: GET /api/v1/sport/football/{date}/0/categories → 69个分类
  Step 2: GET /api/v1/category/{id}/scheduled-events/{date} → 分类比赛

实测比赛样例: Almería vs CD Castellón (LaLiga 2, event=16264088)
  状态: Not started (code=0)
  赔率: 17个市场 (marketId 1-17)
  阵容: 21KB (formation + players + supportStaff)
  H2H: teamDuel{homeWins, awayWins, draws} + managerDuel
```

### 7.2 SofaScore — 足球分类目录

```
Base URL: https://sofascore.p.rapidapi.com

可用端点:
  GET /categories/list?sport=football → 271个足球分类(94KB)

限制: tournament/match 级端点返回204(可能需付费)
```

### 7.3 FootballHighlights（不变）

```
Base URL: https://football-highlights-api.p.rapidapi.com
核心端点: /countries, /leagues, /teams, /matches, /teams/statistics/{id}
特点: BASIC免费版无限制
```

### 7.4 OddsFeed（不变）

```
Base URL: https://odds-feed.p.rapidapi.com
核心端点: /api/v1/events, /api/v1/markets/feed
独有: ASIAN_HANDICAP, OVER_UNDER
流程: events → event_ids → markets/feed
```
