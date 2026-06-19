# 足球预测顾问 - 世界杯预测就绪方案

> **目标：** 修复 10 项遗留问题（4 项数据 + 6 项测试），使项目达到世界杯实时预测可用状态。
>
> **当前状态：** SAFE_DEGRADED（流程可执行，数据不可用）
>
> **目标状态：** 四场穿测至少一场为 PRODUCTION_READY

---

## 一、问题清单

### 数据/配置（4 项，阻塞预测）

| 编号 | 问题 | 影响 | 修复方式 |
|------|------|------|---------|
| D1 | 8 支球队无 Sporttery 映射 → `dim_provider_match_mapping` 为空 | 竞彩同步全部失败 | 补齐映射 + 执行 reconcile |
| D2 | `is_neutral_venue = FALSE` | 中立场虚假主场优势 | UPDATE 数据库 |
| D3 | 4 场比赛新闻为空 | 新闻信号缺失 | 执行新闻采集 |
| D4 | 数据过期 7.5 天 | 触发 No Bet | 更新球队统计 |

### 测试（6 项，不阻塞但需修复）

| 编号 | 测试 | 根因 |
|------|------|------|
| T1 | `test_internal_pipeline_failure_is_failed` | `_determine_conclusion()` 签名变更：测试传 5 参，实际只有 4 参 |
| T2 | `test_production_ready_requires_cache_mode_and_full_readiness` | 同上 |
| T3 | `test_predict_endpoint` | Mock 报告格式不匹配 |
| T4 | `test_loads_walk_forward_samples_with_historical_freshness` | 回测加载器数据格式变更 |
| T5 | `test_staged_win_rate_and_equity_curve` | 回测测试数据格式变更 |
| T6 | `test_mock_exchange_sync_is_visible_in_market_feature_view` | 交易所同步 Mock 不匹配 |

---

## 二、执行步骤

### 阶段一：数据修复（使预测链路可用）

#### 步骤 1：修正中立场标记（D2）

无外部依赖，直接执行。

```sql
UPDATE core.fact_match_schedule
SET is_neutral_venue = TRUE
WHERE system_league_id = 'WC_WORLD_CUP_2026'
  AND is_neutral_venue = FALSE;
```

**验收：** 四场目标比赛的 `is_neutral_venue` 变为 TRUE，穿测中主场优势归零。

#### 步骤 2：补齐球队映射 + 对账（D1）

分两步：

**2a. 补齐 8 支球队的 Sporttery 映射到 `provider_team_mappings.csv`**

8 支缺失球队：德国(WC_TEAM_GER)、库拉索岛(WC_TEAM_CUW)、荷兰(WC_TEAM_NED)、日本(WC_TEAM_JPN)、科特迪瓦(WC_TEAM_CIV)、厄瓜多尔(WC_TEAM_ECU)、瑞典(WC_TEAM_SWE)、突尼斯(WC_TEAM_TUN)

关键挑战：需要竞彩网的 `provider_team_id`。竞彩网使用数字 ID（如 428=卡塔尔、403=瑞士）。获取方式：
- 从竞彩网世界杯对阵页面抓取（需分析 API 响应中的 team_id 字段）
- 或者手动从 staging 数据中交叉匹配

执行顺序：
1. 调用竞彩网 API 获取世界杯比赛列表 → 提取球队 ID
2. 将 8 支球队的 `provider_name=SportteryOfficialWeb, provider_team_id=<数字>, system_team_id=<WC_TEAM_*>` 追加到 CSV
3. 执行 `reconcile_sporttery_matches.py` 创建 `dim_provider_match_mapping`

**2b. 执行竞彩同步**

映射就位后，重新执行目标比赛同步。

**验收：**
- `dim_provider_match_mapping` 中有 4 场比赛的 Sporttery 映射
- `fact_odds_capital_flow` 有四场比赛的 SPF 赔率
- `fact_sporttery_odds_detail` 有四场比赛的比分/总进球/半全场赔率
- 同步状态从 `recent_failure` 变为 `success`

#### 步骤 3：更新球队统计 + 新闻（D3、D4）

**3a. 球队统计更新**

依赖于 API-Football 或 Football-Data.org 的数据源。如果这些 API 可用：
- 对 8 支目标球队执行滚动统计刷新
- 确保 `fact_team_rolling_stats.record_date` 是最近 30 天内的

**3b. 新闻采集**

对 4 场目标比赛执行新闻搜索和入库流程：
- SearchRouter → TextFetcher → NewsIngester（bge-m3 embedding）→ ChromaDB

**验收：**
- `critical_data_age_minutes` 降到 30 以内
- 新闻数量 > 0（或明确返回 empty_search 且系统正确处理）

---

### 阶段二：测试修复（使测试套件全绿）

#### 步骤 4：修复 T1、T2（_determine_conclusion 签名）

`test_walkthrough_e2e.py` 中两处调用传了 5 个参数（含 `mode`），但实际函数只接受 4 个参数。

修复方式：从测试调用中移除 `mode` 参数，或给 `_determine_conclusion()` 增加 `mode` 参数并在函数内使用。

#### 步骤 5：修复 T3（Mock 报告格式）

检查 `test_api_endpoints.py` 中 `test_predict_endpoint` 的 Mock 返回值，与 `ReportGenerationResult` 格式对比，修复不匹配。

#### 步骤 6：修复 T4、T5、T6（回测/交易所）

检查对应测试文件的 Mock 数据格式，与当前数据模型对齐。

**验收：** `python -m unittest discover -s tests -v` 全部通过（412 测试，0 失败，0 错误）。

---

### 阶段三：最终验收

#### 步骤 7：不变量测试

```powershell
.\.runtime\python\python.exe -m unittest tests.test_system_invariants -v
```

12 项全部通过。

#### 步骤 8：端到端穿测

```powershell
.\.runtime\python\python.exe scripts\walkthrough_e2e.py
```

四场全部可执行。至少一场达到 PRODUCTION_READY（关键数据就绪），其余可 SAFE_DEGRADED 但原因必须是外部阻塞（如竞彩未开售），不能是内部缺陷。

#### 步骤 9：全量测试

```powershell
.\.runtime\python\python.exe -m compileall -q football_advisor scripts
.\.runtime\python\python.exe -m unittest discover -s tests -v
```

全部通过。

---

## 三、风险与依赖

| 步骤 | 外部依赖 | 风险 |
|------|---------|------|
| 2a | 竞彩网 API 可访问 | 如果竞彩网接口不可用，需手动查询 provider_team_id |
| 3a | API-Football 或 Football-Data.org API Key | 如果 API 不可用，球队统计可暂跳过_
| 3b | 新闻搜索 API + Ollama（bge-m3 embedding）| 需 Ollama 运行中 |
| 全流程 | 网络连接 | 所有外部数据源需要网络 |

## 四、完成定义

以下条件全部满足，方案完成：

1. ✅ 12 条不变量测试全部通过
2. ✅ `is_neutral_venue = TRUE`（4 场世界杯比赛）
3. ✅ 4 场比赛有竞彩映射和赔率数据
4. ✅ 数据新鲜度在 30 分钟内（或正确标记过期）
5. ✅ 412 个测试全部通过（0 失败，0 错误）
6. ✅ 四场穿测可执行，至少一场 PRODUCTION_READY
7. ✅ `git diff --check` 通过

---

## 五、执行顺序

```text
步骤1（D2 中立场）
  → 步骤2a（球队映射）
    → 步骤2b（竞彩同步）
      → 步骤4-6（测试修复，可并行）
        → 步骤3a-3b（统计+新闻，可并行）
          → 步骤7（不变量测试）
            → 步骤8（端到端穿测）
              → 步骤9（全量测试）
```

步骤 1 和步骤 4-6 完全无依赖，可先执行。步骤 2 是最关键瓶颈。