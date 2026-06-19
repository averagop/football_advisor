# 世界杯预测优先可用性实施计划

> **给执行代理：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 按任务逐项执行。本计划使用复选框语法跟踪状态。

**目标：** 在 2026-06-09 前优先补齐世界杯预测所需的最小可用链路，让项目能对指定世界杯比赛输出受 No Bet 保护的标准化预测报告。

**架构：** 保持既定链路：解析请求 -> 同步数据 -> DuckDB 与 ChromaDB 分离查询 -> 特征工程 -> 概率模型 -> 赔率价值引擎 -> No Bet -> LLM 报告。短期只补世界杯和五大联赛相关能力，不接入巴西等非目标联赛，不扩展自动下注或新前端。

**技术栈：** Python、DuckDB、ChromaDB、FastAPI、OpenWebUI、API-Football、TheSportsDB、football-data.org、unittest、本地 `.runtime` Python。

---

## 当前判断

当前仓库已经具备世界杯与五大联赛的基础框架：

- `scripts/seed_worldcup_data.py` 已包含 `WC_WORLD_CUP_2026` 和 32 支国家队基础实体。
- `data/provider_mappings/provider_league_mappings.csv` 已包含 World Cup 在 football-data.org、API-Football、TheSportsDB、Sportmonks、iSports-API 的联赛映射。
- `data/provider_mappings/provider_team_mappings.csv` 已包含 API-Football 的 32 支世界杯球队映射，以及 TheSportsDB 的部分国家队映射。
- 现有多源同步、provider mapping、No Bet、报告生成和 OpenWebUI 工具测试已有基础。

最紧急缺口不是扩大联赛覆盖，而是把世界杯指定比赛的结构化数据、赔率/统计、新闻信号和报告入口串成可验证闭环。五大联赛只作为历史强弱、球员状态和球队背景的辅助数据，不作为本轮新增赛事目标。

## 方案校验结论

当前计划方向通过，但原计划还不够硬，必须补强三项门禁：

1. 数据达标门禁：指定世界杯比赛必须先同步结构化源，再同步搜索/新闻文本源；缺赛程、缺球队映射、缺 1X2 赔率、缺新闻信号或关键数据超过 30 分钟时，必须进入 No Bet。
2. 报告达标门禁：最终报告必须覆盖项目要求的胜平负、让球胜平负、比分参考、大小球、风险、概率、赔率隐含概率、价值差、数据更新时间、新闻舆情、No Bet 原因和数据来源。
3. 搜索入库门禁：网络搜索不是可选项。世界杯最终名单、赛前伤停、训练状态、战术口径、发布会和舆情变化会直接影响赛前判断；搜索结果必须经过抓取、清洗、去重、ChromaDB 入库和 DuckDB 摘要沉淀后才能进入预测上下文。

当前公开信息校验：

- FIFA 官方赛程显示 2026 世界杯首场比赛在 2026-06-11 开始，因此 2026-06-09 是系统上线/验收截止日，不是比赛日。
- FIFA 已确认最终名单已公布，但严重伤病或疾病仍可能在球队首场比赛前 24 小时内替换，因此 2026-06-09 前必须保留名单和伤停再同步。
- FIFA 决赛抽签已经映射出小组赛分组与对阵，因此目标比赛清单可以按官方分组和赛程做 provider mapping 校验。

## 需要用户配合

请在本地 `.env` 配置真实值，不要通过对话发送密钥：

```env
FOOTBALL_DATA_API_TOKEN=<YOUR_FOOTBALL_DATA_TOKEN>
API_FOOTBALL_TOKEN=<YOUR_API_FOOTBALL_TOKEN>
THESPORTSDB_API_TOKEN=<YOUR_THESPORTSDB_TOKEN>
FOOTBALL_SEARXNG_BASE_URL=<YOUR_SEARXNG_URL>
FOOTBALL_GOOGLE_SEARCH_API_KEY=<YOUR_GOOGLE_API_KEY>
FOOTBALL_GOOGLE_SEARCH_CX=<YOUR_GOOGLE_CX>
FOOTBALL_EXTERNAL_LLM_BASE_URL=<YOUR_OPENAI_COMPATIBLE_BASE_URL>
FOOTBALL_EXTERNAL_LLM_API_KEY=<YOUR_EXTERNAL_LLM_API_KEY>
FOOTBALL_EXTERNAL_LLM_MODEL=<YOUR_MODEL_NAME>
```

还需要用户提供一份 6 月 9 日要预测的世界杯比赛清单，推荐格式：

```text
比赛1：主队 vs 客队，开球时间，玩法范围
比赛2：主队 vs 客队，开球时间，玩法范围
```

玩法范围只需写当前需要的：`胜平负`、`比分参考`、`大小球`、`让球胜平负`。如果没有赔率源或赔率为空，系统必须输出 No Bet，不得用 LLM 猜赔率。

---

## 数据达标门禁

指定比赛进入正式预测前必须满足：

- 赛程：`core.fact_match_schedule` 命中目标比赛，主客队、开球时间、联赛 ID、状态、来源和更新时间完整。
- 实体映射：目标主队和客队至少在一个结构化 provider 中有显式 `core.dim_provider_team_mapping`，不得临时猜 ID。
- 赔率：胜平负预测必须有完整 1X2 赔率；若请求大小球或让球胜平负，对应盘口和赔率也必须存在。缺失时只允许输出 No Bet 或该玩法不可评估。
- 球队强弱：至少有 Elo、近期状态或历史滚动统计；若只有低阶派生数据，必须带数据质量标记并进入高风险或 No Bet。
- 大名单与伤停：必须同步官方最终名单，并用搜索/新闻链路在预测前复查伤停、停赛、训练和发布会信息。
- 新闻舆情：`core.fact_news_signal_summary` 必须存在，`news_source_count > 0`，`news_last_updated_at` 不超过 30 分钟；否则 No Bet。
- 数据时效：结构化关键数据和新闻摘要进入预测时不得超过 30 分钟。
- 来源审计：报告中必须能列出结构化 provider、搜索 provider、正文 hash 或新闻摘要审计字段。

## 结果达标门禁

正式报告必须符合项目标准：

- 必须包含：胜平负倾向、让球胜平负倾向、比分参考、大小球倾向、总进球参考、置信度、风险等级、是否建议投注、模型概率、赔率隐含概率、价值差、数据更新时间、历史交锋、近期状态、主客场表现、伤病、赔率变化、天气、新闻舆情、核心胜负手、主要风险点、No Bet 原因、数据来源、推理链摘要。
- 概率必须来自 `ProbabilityEngine`，赔率价值必须来自 `OddsValueEngine`，No Bet 必须来自 `NoBetPolicy`。
- LLM 只能组织语言，不能生成或改写概率、赔率、比分事实和投注结论。
- 不允许输出“稳胆”“必胜”“稳赚”“稳赢”等确定性或收益承诺。

---

## 文件结构

- 修改：`tests/test_provider_mapping_importer.py`，补充世界杯 provider mapping 覆盖测试。
- 修改：`tests/test_external_sync.py`，补充世界杯 fixture 经 provider mapping 写入 `core.fact_match_schedule` 的 fake 测试。
- 修改：`tests/test_api_football_sync.py`，补充世界杯赔率/统计端点 fake 测试，证明缺赔率时可审计。
- 修改：`tests/test_search_router.py`，补充世界杯搜索查询覆盖，确保未配置搜索源时明确跳过。
- 修改：`tests/test_text_fetcher.py`，补充世界杯名单/伤停正文抓取、去重和短文本拒绝测试。
- 修改：`tests/test_news_ingestion.py`，补充世界杯新闻摘要写入 DuckDB 的门禁测试。
- 修改：`scripts/smoke_test_multi_source_sync.py`，补充世界杯目标比赛参数校验输出，不改变 provider 边界。
- 新增：`scripts/smoke_test_worldcup_readiness.py`，聚合世界杯赛前可用性检查。
- 修改：`docs/PROGRESS.md`，记录执行结果、验证、限制和下一步。

---

### 任务 1：确认世界杯映射可导入

**文件：**

- 修改：`tests/test_provider_mapping_importer.py`

- [ ] **步骤 1：写世界杯映射导入测试**

新增测试，使用临时 CSV 验证 API-Football World Cup 联赛和两支球队能导入：

```python
def test_imports_world_cup_api_football_mappings(self):
    (self.mapping_dir / "provider_league_mappings.csv").write_text(
        "provider_name,provider_league_id,system_league_id,provider_league_name\n"
        "API-Football,1,WC_WORLD_CUP_2026,World Cup\n",
        encoding="utf-8",
    )
    (self.mapping_dir / "provider_team_mappings.csv").write_text(
        "provider_name,provider_team_id,system_team_id,provider_team_name\n"
        "API-Football,15,WC_TEAM_USA,USA\n"
        "API-Football,14,WC_TEAM_MEX,Mexico\n",
        encoding="utf-8",
    )

    self.connection.execute(
        """
        INSERT INTO core.dim_league_mapping (system_league_id, league_standard_name)
        VALUES ('WC_WORLD_CUP_2026', 'FIFA World Cup 2026');

        INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name)
        VALUES
            ('WC_TEAM_USA', 'USA'),
            ('WC_TEAM_MEX', 'Mexico');
        """
    )

    result = import_provider_mappings(self.connection, self.mapping_dir)

    self.assertEqual(result.league_inserted, 1)
    self.assertEqual(result.team_inserted, 2)
    self.assertEqual(result.league_skipped, 0)
    self.assertEqual(result.team_skipped, 0)
```

- [ ] **步骤 2：运行聚焦测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_provider_mapping_importer -v
```

预期：全部通过。

---

### 任务 2：证明世界杯赛程可写入 DuckDB core

**文件：**

- 修改：`tests/test_external_sync.py`

- [ ] **步骤 1：新增世界杯 fixture fake 测试**

使用 `FootballDataClient` 子类或现有 client fake 响应，构造 World Cup 比赛写入测试：

```python
def test_world_cup_fixture_merges_to_core_with_provider_mapping(self):
    self.connection.execute(
        """
        INSERT INTO core.dim_league_mapping (system_league_id, league_standard_name, country)
        VALUES ('WC_WORLD_CUP_2026', 'FIFA World Cup 2026', 'International');

        INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name, elo_rating_base)
        VALUES
            ('WC_TEAM_USA', 'USA', 1780.0),
            ('WC_TEAM_MEX', 'Mexico', 1720.0);

        INSERT INTO core.dim_provider_league_mapping (
            provider_name, provider_league_id, system_league_id, provider_league_name
        ) VALUES ('football-data.org', 'WC', 'WC_WORLD_CUP_2026', 'FIFA World Cup');

        INSERT INTO core.dim_provider_team_mapping (
            provider_name, provider_team_id, system_team_id, provider_team_name
        ) VALUES
            ('football-data.org', '772', 'WC_TEAM_USA', 'USA'),
            ('football-data.org', '769', 'WC_TEAM_MEX', 'Mexico');
        """
    )

    class WorldCupFootballDataClient(FootballDataClient):
        def _mock_response(self, path):
            return {
                "matches": [
                    {
                        "id": 880001,
                        "competition": {"id": "WC"},
                        "utcDate": "2026-06-09T20:00:00Z",
                        "status": "TIMED",
                        "homeTeam": {"id": "772"},
                        "awayTeam": {"id": "769"},
                        "score": {"fullTime": {"home": None, "away": None}},
                    }
                ]
            }

    client = WorldCupFootballDataClient(api_token="mock_token")
    res = client.fetch_matches(self.connection, "2026-06-09", "2026-06-09")

    self.assertEqual(res["status"], "success")
    self.assertEqual(res["staged_count"], 1)
    self.assertEqual(res["merged_count"], 1)
    self.assertEqual(res["skipped_unmapped_count"], 0)
```

- [ ] **步骤 2：运行聚焦测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_external_sync.ExternalSyncTests.test_world_cup_fixture_merges_to_core_with_provider_mapping -v
```

预期：测试通过。

---

### 任务 3：补世界杯赔率/统计可审计烟测

**文件：**

- 修改：`tests/test_api_football_sync.py`

- [ ] **步骤 1：新增世界杯 API-Football fake 测试**

构造 API-Football fixture、odds、statistics fake 响应，验证：

- `odds_count` 大于 `0` 时写入 `core.fact_odds_capital_flow`。
- `stats_count` 大于 `0` 时写入 `core.fact_team_rolling_stats`。
- 赔率或统计为空时返回 `missing_market_stats_count`，并保留 No Bet 门禁。

- [ ] **步骤 2：运行聚焦测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_api_football_sync -v
```

预期：全部通过。

---

### 任务 4：补世界杯搜索与新闻入库门禁

**文件：**

- 修改：`tests/test_search_router.py`
- 修改：`tests/test_text_fetcher.py`
- 修改：`tests/test_news_ingestion.py`

- [ ] **步骤 1：补搜索查询测试**

新增测试，查询格式必须包含两队、World Cup、squad、injury、press conference 等关键词，未配置搜索源时返回 `skipped_no_provider_configured`，不得伪造新闻信号。

- [ ] **步骤 2：补正文抓取测试**

新增测试，覆盖世界杯名单或伤停 HTML 正文抓取、清洗、内容 hash、重复正文去重和短文本拒绝。

- [ ] **步骤 3：补新闻摘要门禁测试**

新增测试，构造两篇有效世界杯新闻文档，调用 `NewsIngester.ingest_news()` 后断言：

- `core.fact_news_signal_summary` 写入目标 `match_id`。
- `news_source_count >= 2`。
- `source_text_hash` 为 64 位十六进制。
- `requires_cross_check=True`。

- [ ] **步骤 4：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_search_router tests.test_text_fetcher tests.test_news_ingestion -v
```

预期：全部通过。

---

### 任务 5：新增世界杯赛前可用性烟测脚本

**文件：**

- 新增：`scripts/smoke_test_worldcup_readiness.py`

- [ ] **步骤 1：实现只读检查入口**

脚本参数：

```powershell
.\.runtime\python\python.exe scripts\smoke_test_worldcup_readiness.py --home-team USA --away-team Mexico --date 2026-06-09
```

脚本检查：

- schema 可初始化。
- `seed_worldcup_data.py` 已执行或对应实体存在。
- provider mapping 可导入且 World Cup 联赛映射存在。
- 指定主客队至少在一个 provider 中有映射。
- 搜索 provider 至少一个可用，或明确返回 `skipped_no_provider_configured` 并触发 No Bet。
- 运行 `MultiSourceCoordinator.fetch_all()` 后输出各 provider 状态、`staged_count`、`merged_count`、`odds_count`、`stats_count`。
- 执行搜索预览、正文抓取和新闻摘要写入检查。
- 不打印任何 token、请求头或完整敏感配置。

- [ ] **步骤 2：无 token 场景测试**

未配置 token 时，脚本必须返回明确的 `missing_token` 或 `skipped_no_token`，不能伪造通过。

- [ ] **步骤 3：运行本地烟测**

```powershell
.\.runtime\python\python.exe scripts\smoke_test_worldcup_readiness.py --home-team USA --away-team Mexico --date 2026-06-09
```

预期：输出每个 provider 的可用性状态。若 token 未配置，记录缺失配置；若 token 已配置，记录脱敏结果。

---

### 任务 6：执行预测链路最小端到端验证

**文件：**

- 修改：`tests/test_pipeline.py`
- 可选修改：`scripts/smoke_test_e2e.py`

- [ ] **步骤 1：新增世界杯 pipeline fake 测试**

用 fake sync、fake feature builder、fake news tool、fake LLM router 构造世界杯比赛，验证输出包含：

- 胜平负倾向。
- 比分参考。
- 大小球倾向。
- 模型概率。
- 赔率隐含概率。
- No Bet 原因或可投注状态。
- 数据更新时间。

- [ ] **步骤 2：验证 No Bet 优先级**

构造缺赔率或缺新闻信号场景，断言最终报告是 No Bet，而不是补造建议。

- [ ] **步骤 3：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_pipeline tests.test_report -v
```

预期：全部通过。

---

### 任务 7：OpenWebUI 调用路径最终检查

**文件：**

- 修改：`tests/test_openwebui_tools.py`
- 修改：`docs/OPENWEBUI_INTEGRATION.md`

- [ ] **步骤 1：验证工具只调用 `/predict`**

确认 OpenWebUI 工具只发送 `query`、`match_id` 或受控字段，不暴露任意 SQL、任意 Python 或直接数据库访问。

- [ ] **步骤 2：补充 6 月 9 日操作说明**

文档只写本地变量名和命令，不写真实密钥：

```powershell
.\.runtime\python\python.exe scripts\smoke_test_worldcup_readiness.py --home-team <HOME> --away-team <AWAY> --date 2026-06-09
.\.runtime\python\python.exe scripts\smoke_test_e2e.py --query "<HOME> vs <AWAY>"
```

- [ ] **步骤 3：验证**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_openwebui_tools tests.test_api_endpoints -v
```

预期：全部通过。

---

## 6 月 9 日前优先级

1. 先拿到用户指定比赛清单和玩法范围。
2. 先验证 World Cup 联赛和目标球队 provider mapping。
3. 先补搜索、抓取、新闻入库和新闻摘要门禁。
4. 先保证赛程、赔率、统计、新闻信号缺失时能明确 No Bet。
5. 再跑指定比赛的真实 provider 脱敏烟测。
6. 最后接 OpenWebUI 调用路径，确保用户只从正式入口拿报告。

## 不做事项

- 不补巴西联赛数据。
- 不批量扩展非世界杯、非五大联赛赛事。
- 不用 LLM 猜赔率、赛程、伤停、概率或比分。
- 不绕过 No Bet 输出投注建议。
- 不新增独立前端或自动下注能力。

## 自检记录

- 规格覆盖：本计划覆盖 2026-06-09 前世界杯预测可用性的最小闭环。
- 范围控制：只保留世界杯和五大联赛辅助范围，删除巴西免费层优先级。
- 测试策略：先 fake 测试证明映射和链路，再用本地 token 做脱敏真实烟测。
- 风险点：真实世界杯赛程、provider 可用字段和赔率覆盖取决于用户本地 token 套餐；缺失时必须进入 No Bet。
