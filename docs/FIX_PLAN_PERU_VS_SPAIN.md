# Peru vs Spain 预测链路修复方案

> 2026-06-08 | 基于真实现场诊断

---

## 一、问题溯源

预测 `Peru vs Spain` 时，系统返回 No Bet + 占位特征，追踪发现 **一条链路被三个缺陷掐断**：

```
predict(Peru, Spain)
  │
  ├── ① 8 源并行同步 → 4 源成功（但赔率未被有效利用）
  │
  ├── ② DuckDB 视图查询 → 不在视图中 → 降级占位特征  ← 缺陷 A
  │
  ├── ③ 概率模型 → 用假的占位特征 → 不可信
  ├── ④ 赔率价值引擎 → 缺少赔率 → 不通过           ← 缺陷 B+C
  ├── ⑤ No Bet 门禁 → 触发
  └── ⑥ LLM 报告 → "不建议投注"
```

---

## 二、缺陷清单

| 编号 | 缺陷 | 根因 | 影响面 |
|---|---|---|---|
| A | 视图 INNER JOIN 过滤非注册联赛 | `db_schema.py` L435: `JOIN dim_league_mapping` | 任何非 6 大联赛的比赛被视图丢弃 |
| B | 竞彩只抓了 2 种玩法（缺 3 种） | `sporttery_client.py` L31: `poolCode=hhad,had` | 比分/总进球/半全场数据缺失 |
| C | TheOddsAPI 时间窗口导致 33 条数据 0 匹配 | `third_party_odds_client.py` L303: ±12h 严格过滤，我们传的是 `datetime.now()` | 有赔率数据但被错误丢弃 |
| D | 无串行回退机制 | `multi_source_coordinator.py` L76-L85: 8 源纯并行 | 一个源失败无人补、A拿到了数据但条件不匹配无人反应 |

---

## 三、修复方案（4 项，按优先级排序）

### 修复 A：视图 LEFT JOIN（必须修，破局第一步）

**文件**: `football_advisor/db_schema.py`

**位置**: `view_match_feature_base` 视图定义，约第 435 行

**改动**:
```sql
-- 之前（INNER JOIN — 不在 league 表里的直接丢弃）
FROM core.fact_match_schedule s
JOIN core.dim_league_mapping l ON s.system_league_id = l.system_league_id

-- 之后（LEFT JOIN — 联赛名未知也保留）
FROM core.fact_match_schedule s
LEFT JOIN core.dim_league_mapping l ON s.system_league_id = l.system_league_id
```

同时 SELECT 中 `l.league_standard_name` 改为：
```sql
COALESCE(l.league_standard_name, 'Unknown') AS league_standard_name
```

**理由**: `dim_league_mapping` 是辅助映射表，不是门禁表。联赛名未知不应成为丢弃比赛的理由。零破坏性。

**验收**: `M_PER_ESP_FRIENDLY` 出现在 `view_match_feature_base` 中。

---

### 修复 B：竞彩 poolCode 扩展到全量 5 种玩法

**文件**: `football_advisor/sporttery_client.py`

**位置**: 约第 30-33 行，`MATCH_CALCULATOR_URL` 常量

**改动**:
```python
# 之前
MATCH_CALCULATOR_URL = (
    "https://webapi.sporttery.cn/gateway/uniform/football/"
    "getMatchCalculatorV1.qry?channel=c&poolCode=hhad,had"
)

# 之后
MATCH_CALCULATOR_URL = (
    "https://webapi.sporttery.cn/gateway/uniform/football/"
    "getMatchCalculatorV1.qry?channel=c&poolCode=hhad,had,crs,ttg,hafu"
)
```

**对应玩法**:
| poolCode | 中文 | 存储字段 |
|---|---|---|
| `had` | 胜平负 | 已有，`spf_odds` |
| `hhad` | 让球胜平负 | 已有，`rqspf_odds` |
| `crs` | 比分 | 需要加，存入 `core.fact_odds_capital_flow(odds_type='CORRECT_SCORE')` |
| `ttg` | 总进球 | 需要加，存入 `core.fact_odds_capital_flow(odds_type='TOTAL_GOALS')` |
| `hafu` | 半全场胜平负 | 需要加，存入 `core.fact_odds_capital_flow(odds_type='HT_FT')` |

**需要增加解析逻辑**: 在 `_parse_match_calculator_payload()` 中处理新增 3 种玩法的数据结构。API 返回的 JSON 中每个 `subMatchList` 包含多个 `match` 对象，每个 `match` 的 `oddsList` 数组里包含不同 `poolCode` 的赔率。

**理由**: 竞彩 5 种玩法数据来自同一个 API，只需改 poolCode。Peru vs Spain 的让球胜平负赔率 (2.68/3.60/2.09) 确认已开售。扩大范围 = 更多比赛能拿到赔率。

**验收**: `M_PER_ESP_FRIENDLY` 的 RQSPF 赔率写入 `staging.stg_odds` 并 merge 到 `core.fact_odds_capital_flow`。

---

### 修复 C：TheOddsAPI 时间匹配放宽

**文件**: `football_advisor/third_party_odds_client.py`

**位置**: 约第 296-306 行，`_event_matches` 函数

**改动**:
```python
def _event_matches(event, home_team, away_team, kickoff_time):
    # 队名匹配（不变）
    event_home = str(event.get("home_team") or event.get("home") or "").strip()
    event_away = str(event.get("away_team") or event.get("away") or "").strip()
    if home_team and event_home and event_home.casefold() != home_team.strip().casefold():
        return False
    if away_team and event_away and event_away.casefold() != away_team.strip().casefold():
        return False

    # 时间匹配 — 放宽：12h → 48h，且 kickoff_time 空时跳过
    # 之前: abs(event_time - kickoff_time) > 12h → 丢弃
    # 之后: abs(event_time - kickoff_time) > 48h → 丢弃（赛事跨天更宽容）
    #       若 kickoff_time 为 None → 不加时间过滤
    if kickoff_time:
        event_time = _parse_datetime(event.get("commence_time") or event.get("start_time"))
        if event_time and abs((event_time - kickoff_time.astimezone(timezone.utc)).total_seconds()) > 48 * 3600:
            return False
    return True
```

**理由**: 我们传入的 `kickoff_time` 是 `datetime.now()`（即"当前时间"）而非真实开球时间。Peru vs Spain 开球时间 6/9 10:00，当前时间 6/8 12:00 → 相差 22 小时，超过了旧的 12 小时窗口。放宽到 48 小时后匹配通过。

**验收**: 再跑预测时 TheOddsAPI 的 `matched_event_count > 0`。

---

### 修复 D：新增同步后赔率补漏检查

**文件**: `football_advisor/multi_source_coordinator.py`

**位置**: `fetch_all()` 方法 `return` 之前，`asyncio.run` 之后

**改动**: 在所有并行源完成后，检查赔率覆盖情况：
```python
# 并行同步之后，检查赔率是否满足最低要求
has_rqspf = any(
    r.provider_name == "SportteryOfficialWeb"
    and r.status == "success"
    and r.data.get("rqspf_odds_count", 0) > 0
    for r in results
)
has_1x2 = any(
    r.provider_name in ("TheOddsAPI", "RapidAPI-OddsFeed", "SportteryOfficialWeb", "API-Football")
    and r.status == "success"
    and r.data.get("odds_count", 0) > 0
    for r in results
)

# 如果 SPF 缺但有 RQSPF，标记可用（让球胜平负可作为替代赔率信号）
if not has_1x2 and has_rqspf:
    logger.info("缺少 SPF 赔率，但 RQSPF 可用，RQSPF 将作为赔率替代信号")
```

**理由**: 当前 8 源并行跑完后直接收结果，不检查、不补漏。这步让系统至少知道"虽然没有 SPF，但 RQSPF 可用"，给后续概率模型一个可用信号，而不是直接 No Bet。

---

## 七、实施顺序（更新版）

| 步骤 | 修复 | 预计改动 | 优先级 | 预计时间 |
|---|---|---|---|---|
| 第 1 步 | A: LEFT JOIN | 2 行 SQL | **最高** | 即时 |
| 第 2 步 | B: poolCode 扩展 | ∼30 行 Python | **最高** | 即时 |
| 第 3 步 | C: 时间窗口放宽 | 1 行改数字 | 高 | 即时 |
| 第 4 步 | D: 补漏检查 | ∼20 行 Python | 中 | 即时 |
| 第 5 步 | E: 串行回退机制 | ∼200 行 Python | 中 | 2-3 小时 |

> 第 1-4 步可以一起做，第 5 步是独立的架构能力新增。

---

## 八、验收标准（更新版）

修复完成后，运行 `Peru vs Spain` 预测应满足：

1. `view_match_feature_base` 视图中包含 `M_PER_ESP_FRIENDLY`（修复 A）
2. `core.fact_odds_capital_flow` 中有 RQSPF 赔率: 2.68 / 3.60 / 2.09（修复 B）
3. 报告不再是"占位特征降级"，而是基于真实数据的分析（修复 A）
4. TheOddsAPI 的 33 个 event 中有 Peru vs Spain 的匹配（修复 C）
5. 若 SPF 仍缺失，报告明确标注"SPF 未开售，使用 RQSPF 替代"（修复 D）
6. **Phase 3 串行补漏可手动触发验证**：模拟 API-Football token 失效 → 系统检测到缺口 → 自动依次尝试其他源（修复 E）

---

## 六、修复 E：串行回退机制（新增架构能力）

> **这是对问题"一个 API 没数据继续查下一个 API 直到拿到有用数据"的完整方案。**

### 6.1 现状

当前 `MultiSourceCoordinator.fetch_all()` 是纯并行模型：

```
Sporttery ──┐
football-data ─┤
API-Football ─┼── asyncio.gather → 收结果 → 返回
TheSportsDB  ─┤   各源互不感知
TheOddsAPI   ─┤   一个失败 ≠ 触发另一个
...          ─┘   一个拿到空数据 ≠ 有人补
```

这意味着：
- TheOddsAPI 拿到 33 个 event 但 0 个匹配 → **没人知道，没人补**
- API-Football 按日期找不到比赛 → **没人知道，没人补**
- Sporttery 拿到赔率但 SPF 未开售 → **没人知道 RQSPF 可用**

### 6.2 设计目标

将同步流程从 **纯并行** 改为 **并行 + 串行补漏**：

```
fetch_all(home_team, away_team, kickoff_time)
  │
  ├── ★ Phase 1: 并行同步（现有逻辑，不改一字）
  │     └── 8 源 asyncio.gather → results
  │
  ├── ★ Phase 2: 数据缺口分析（新增）
  │     └── _assess_data_gaps(results, home_team, away_team)
  │         → 返回 {missing_spf_odds: True, missing_1x2: True, ...}
  │
  └── ★ Phase 3: 串行补漏（新增）
        └── _fill_data_gaps(gaps, results, home_team, away_team, kickoff_time)
            → 一个源一个源试，拿到有用数据就停
```

### 6.3 数据类型定义与优先级链

每一种"比赛资料"有独立的优先级链：

| 数据类型 | 含义 | 优先级链（从左到右：先试先停） |
|---|---|---|
| `spf_odds` | 胜平负赔率 | Sporttery → TheOddsAPI → API-Football → RapidAPI-OddsFeed |
| `rqspf_odds` | 让球胜平负 | Sporttery → (无其他源提供) |
| `correct_score_odds` | 比分赔率 | Sporttery → (无其他源提供) |
| `total_goals_odds` | 总进球赔率 | Sporttery → (无其他源提供) |
| `half_full_odds` | 半全场赔率 | Sporttery → (无其他源提供) |
| `match_schedule` | 赛程 + 开球时间 | API-Football → football-data.org → Sportmonks → iSports |
| `match_stats` | 技术统计 | API-Football → football-data.org → Sportmonks |
| `team_squad` | 球员名单 | TheSportsDB → API-Football |
| `recent_form` | 近期状态(近N场) | API-Football → football-data.org |
| `head_to_head` | 历史交锋 | TheSportsDB → API-Football |
| `standings` | 积分榜 | football-data.org → API-Football → Sportmonks |

**规则**：
1. 只有 Phase 1 并行同步后**仍然缺失**的数据类型才触发 Phase 3 补漏
2. Phase 3 中**已跑过的源直接跳过**（不重复调用）
3. 按优先级链依次尝试，**第一个返回有用数据的立即停止**
4. 所有补漏源的调用方法独立定义，与 Phase 1 的解耦

### 6.4 Phase 2: 数据缺口分析逻辑

新增方法 `_assess_data_gaps()`：

```python
def _assess_data_gaps(
    self,
    results: list[SourceResult],
    home_team: str,
    away_team: str,
) -> dict[str, bool]:
    """分析 8 源并行同步后，哪些数据仍然缺失。"""
    import duckdb
    conn = duckdb.connect(self.duckdb_path)

    gaps = {}

    # 1. 赛程检查: 数据库中是否有这场比赛?
    match_exists = conn.execute(
        "SELECT 1 FROM core.fact_match_schedule m "
        "JOIN core.dim_team_mapping h ON m.home_team_id = h.system_team_id "
        "JOIN core.dim_team_mapping a ON m.away_team_id = a.system_team_id "
        "WHERE lower(h.team_standard_name) = lower(?) "
        "AND lower(a.team_standard_name) = lower(?) "
        "LIMIT 1",
        [home_team, away_team]
    ).fetchone()
    gaps["missing_match_schedule"] = match_exists is None

    # 2. SPF 赔率: 核心赔付
    spf_odds_count = conn.execute(
        "SELECT COUNT(*) FROM core.fact_odds_capital_flow o "
        "JOIN core.fact_match_schedule m ON o.match_id = m.match_id "
        "JOIN core.dim_team_mapping h ON m.home_team_id = h.system_team_id "
        "JOIN core.dim_team_mapping a ON m.away_team_id = a.system_team_id "
        "WHERE o.odds_type = '1X2' "
        "AND lower(h.team_standard_name) = lower(?) "
        "AND lower(a.team_standard_name) = lower(?)",
        [home_team, away_team]
    ).fetchone()[0]
    gaps["missing_spf_odds"] = spf_odds_count == 0

    # 3. RQSPF 赔率: 替代赔率信号
    rqspf_count = conn.execute(
        "SELECT COUNT(*) FROM core.fact_odds_capital_flow o "
        "JOIN core.fact_match_schedule m ON o.match_id = m.match_id "
        "JOIN core.dim_team_mapping h ON m.home_team_id = h.system_team_id "
        "JOIN core.dim_team_mapping a ON m.away_team_id = a.system_team_id "
        "WHERE o.odds_type IN ('RQSPF', 'SPORTTERY_RQSPF') "
        "AND lower(h.team_standard_name) = lower(?) "
        "AND lower(a.team_standard_name) = lower(?)",
        [home_team, away_team]
    ).fetchone()[0]
    gaps["missing_rqspf_odds"] = rqspf_count == 0

    # 4. 阵容
    squad_count = conn.execute(
        "SELECT COUNT(*) FROM core.fact_team_squad s "
        "JOIN core.dim_team_mapping t ON s.team_id = t.system_team_id "
        "WHERE lower(t.team_standard_name) IN (lower(?), lower(?))",
        [home_team, away_team]
    ).fetchone()[0]
    gaps["missing_squad"] = squad_count == 0

    # 5. 历史交锋
    h2h_count = conn.execute(
        "SELECT COUNT(*) FROM core.fact_head_to_head h "
        "JOIN core.dim_team_mapping t1 ON h.team1_id = t1.system_team_id "
        "JOIN core.dim_team_mapping t2 ON h.team2_id = t2.system_team_id "
        "WHERE (lower(t1.team_standard_name) = lower(?) AND lower(t2.team_standard_name) = lower(?)) "
        "OR (lower(t1.team_standard_name) = lower(?) AND lower(t2.team_standard_name) = lower(?))",
        [home_team, away_team, away_team, home_team]
    ).fetchone()[0]
    gaps["missing_head_to_head"] = h2h_count == 0

    # 6. 近期状态
    form_count = conn.execute(
        "SELECT COUNT(*) FROM core.fact_match_context_summary c "
        "JOIN core.dim_team_mapping t ON c.team_id = t.system_team_id "
        "WHERE lower(t.team_standard_name) IN (lower(?), lower(?))",
        [home_team, away_team]
    ).fetchone()[0]
    gaps["missing_recent_form"] = form_count == 0

    conn.close()
    return gaps
```

### 6.5 Phase 3: 串行补漏逻辑

新增方法 `_fill_data_gaps()`：

```python
# 数据类型 → 优先级链（源名称列表）
_FALLBACK_PRIORITY: dict[str, list[str]] = {
    "missing_spf_odds":       ["SportteryOfficialWeb", "TheOddsAPI",
                                "API-Football", "RapidAPI-OddsFeed"],
    "missing_rqspf_odds":     ["SportteryOfficialWeb"],
    "missing_correct_score":  ["SportteryOfficialWeb"],
    "missing_total_goals":    ["SportteryOfficialWeb"],
    "missing_half_full":      ["SportteryOfficialWeb"],
    "missing_match_schedule": ["API-Football", "football-data.org",
                                "Sportmonks", "iSports-API"],
    "missing_squad":          ["TheSportsDB", "API-Football"],
    "missing_recent_form":    ["API-Football", "football-data.org", "Sportmonks"],
    "missing_head_to_head":   ["TheSportsDB", "API-Football"],
    "missing_match_stats":    ["API-Football", "football-data.org", "Sportmonks"],
}

# 源名称 → 补漏调用方法
_FALLBACK_METHODS: dict[str, str] = {
    "SportteryOfficialWeb":  "_fetch_sporttery_fallback",
    "TheOddsAPI":            "_fetch_the_odds_api_fallback",
    "API-Football":          "_fetch_api_football_fallback",
    "RapidAPI-OddsFeed":     "_fetch_rapidapi_odds_feed_fallback",
    "football-data.org":     "_fetch_football_data_fallback",
    "Sportmonks":            "_fetch_sportmonks_fallback",
    "iSports-API":           "_fetch_isports_fallback",
    "TheSportsDB":           "_fetch_thesportsdb_fallback",
}


def _fill_data_gaps(
    self,
    gaps: dict[str, bool],
    phase1_results: list[SourceResult],
    home_team: str,
    away_team: str,
    kickoff_time: datetime | None = None,
) -> list[SourceResult]:
    """
    串行补漏：对于 Phase 1 后仍缺失的数据类型，
    按优先级链逐一尝试。拿到有用数据立即停止。
    """
    extra_results: list[SourceResult] = []

    # 收集 Phase 1 已调用过的源（避免重复）
    already_called = {r.provider_name for r in phase1_results}

    for gap_key, is_missing in gaps.items():
        if not is_missing:
            continue  # 数据已满足，跳过

        priority_chain = self._FALLBACK_PRIORITY.get(gap_key, [])
        if not priority_chain:
            continue

        # 按优先级尝试
        for provider_name in priority_chain:
            if provider_name in already_called:
                # 第1轮已调用 → 检查其返回值有没有满足这个缺口
                phase1 = next(
                    (r for r in phase1_results if r.provider_name == provider_name),
                    None
                )
                if phase1 and self._result_satisfies_gap(phase1, gap_key):
                    # Phase 1 已经满足了，不补
                    break
                # Phase 1 没满足 → 继续试下一个源
                continue

            # 这个源没有被 Phase 1 调用过 → 调用补漏方法
            method_name = self._FALLBACK_METHODS.get(provider_name)
            if not method_name:
                continue
            method = getattr(self, method_name, None)
            if not method or not callable(method):
                continue

            try:
                result = method(home_team, away_team, kickoff_time)
                extra_results.append(result)

                if result.status == "success" and self._result_satisfies_gap(result, gap_key):
                    # 拿到有用数据 → 停止该缺口的补漏
                    break
            except Exception:
                # 该源失败 → 继续试下一个
                pass

    return extra_results


def _result_satisfies_gap(
    self, result: SourceResult, gap_key: str
) -> bool:
    """判断一个源的返回值是否满足了某个数据缺口。"""
    if result.status != "success":
        return False
    data = result.data or {}

    satisfaction_map = {
        "missing_spf_odds":       lambda d: (d.get("odds_count") or 0) > 0,
        "missing_rqspf_odds":     lambda d: (d.get("rqspf_odds_count") or 0) > 0,
        "missing_correct_score":  lambda d: (d.get("correct_score_count") or 0) > 0,
        "missing_total_goals":    lambda d: (d.get("total_goals_count") or 0) > 0,
        "missing_half_full":      lambda d: (d.get("half_full_count") or 0) > 0,
        "missing_match_schedule": lambda d: (d.get("events_count") or d.get("staged_count") or 0) > 0,
        "missing_squad":          lambda d: (d.get("squad_count") or 0) > 0,
        "missing_recent_form":    lambda d: (d.get("friendly_count") or d.get("events_count") or 0) > 0,
        "missing_head_to_head":   lambda d: (d.get("history_count") or d.get("h2h_count") or 0) > 0,
        "missing_match_stats":    lambda d: (d.get("stats_count") or 0) > 0,
    }

    checker = satisfaction_map.get(gap_key)
    return checker(data) if checker else False
```

### 6.6 补漏方法的命名约定

每个补漏方法以 `_fallback` 后缀，目的与 Phase 1 的并行方法区分：

| Phase 1 并行方法 | Phase 3 补漏方法 | 说明 |
|---|---|---|
| `_fetch_sporttery(kickoff_time)` | `_fetch_sporttery_fallback(home_team, away_team, kickoff_time)` | 原来取当日全局 → 补漏时按队名过滤 |
| `_fetch_api_football(...)` | `_fetch_api_football_fallback(home_team, away_team, kickoff_time)` | 原来按日期 → 补漏时按球队ID + 放宽日期 |
| `_fetch_the_odds_api(...)` | `_fetch_the_odds_api_fallback(home_team, away_team, kickoff_time)` | 原来严格匹配 → 补漏时放宽匹配条件 |

**补漏方法与普通方法的区别**：
- 普通方法：尽量快速（并行），严格匹配条件
- 补漏方法：尽量兜底（串行），放宽匹配条件，专攻缺口

### 6.7 集成到 `DataSyncCoordinator.sync_before_prediction()`

[sync.py](file:///d:\Knowledge_Base-duckdb\football_advisor\sync.py) 中增加 Phase 3 调用：

```python
def sync_before_prediction(self, request: MatchRequest) -> SyncResult:
    # ...现有 Phase 1 逻辑（不变）...

    structured_results = self.multi_source.fetch_all(
        request.home_team, request.away_team, request.kickoff_time
    )

    # ★ 新增：Phase 2 + Phase 3
    gaps = self.multi_source._assess_data_gaps(
        structured_results.results, request.home_team, request.away_team
    )
    if any(gaps.values()):
        logger.info(f"检测到数据缺口: {[k for k,v in gaps.items() if v]}")
        extra_results = self.multi_source._fill_data_gaps(
            gaps, structured_results.results,
            request.home_team, request.away_team, request.kickoff_time
        )
        structured_results = self._merge_results(structured_results, extra_results)

    # ...继续现有流程...
```

### 6.8 以 Peru vs Spain 为例走一遍

| 阶段 | 发生了什么 | 结果 |
|---|---|---|
| Phase 1 并行 | Sporttery 成功（有 SPF+ RQSPF）、API-Football 成功（有比分无赔率）、TheOddsAPI 成功（33 事件 0 匹配）、TheSportsDB 成功（10 名球员） | 4 源成功 |
| Phase 2 缺口分析 | `missing_rqspf_odds=True` — 虽然 Sporttery 成功，但 RQSPF 赔率没有写入 core（是因为 match_id 未匹配到对应的比赛记录） | 检测到缺口 |
| Phase 3 串行补漏 | 缺口 `missing_rqspf_odds` → 优先级链只有 `SportteryOfficialWeb` → Phase 1 已调用 → 检查 Phase 1 结果 → RQSPF 赔率在 data 里 → 本次不补（数据已有，只是需要下游 merge 处理） | 不复调 |
| Phase 3（如果） | 如果 TheOddsAPI 在 Phase 1 失败（而非"33事件0匹配"），Phase 3 会按链试：TheOddsAPI → API-Football 赔率端点 → RapidAPI | 依次尝试取到赔率 |

**注**：本场景下 Phase 3 不会触发额外 API 调用，因为 Sporttery 的 RQSPF 数据已经在 Phase 1 拿到了。真正需要补漏的场景是：API-Football 日期没找到比赛，但 Sportmonks（如果配了 token）可以按球队名称找到。

### 6.9 真正触发的场景举例

| 场景 | Phase 1 结果 | Phase 3 行为 |
|---|---|---|
| TheOddsAPI token 失效 | Phase 1 的 TheOddsAPI 返回 `failed` | Phase 3：跳过 TheOddsAPI（已失败），试 API-Football odds 端点 |
| API-Football 日期查不到 | Phase 1 的 match_schedule 缺失 | Phase 3：依次试 football-data.org → Sportmonks → iSports |
| 竞彩网站今天没这比赛 | Phase 1 的 spf_odds 缺失 | Phase 3：依次试 TheOddsAPI → API-Football |

---

## 七、不改的项目

- **Sportmonks / iSports-API**: `.env` 未配 token，不在本次修复范围
- **RapidAPI-OddsFeed**: 缺 endpoint 路径，需用户补充配置后方可修复
- **API-Football 免费计划赔率覆盖率**: 商业限制，代码层面无法解决
- **竞彩 SPF 未开售**: 业务事实，非代码问题。RQSPF 可替代