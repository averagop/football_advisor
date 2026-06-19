# 落地执行方案

> 创建时间：2026-05-31
> 分支：`feat-production-readiness-review`
> 当前状态：**B-5 数据质量 9 项校验已补齐，待真实外部数据源生产烟测**
>
> 每完成一个步骤，请在下方对应复选框打勾 `[x]`，并更新文档底部的变更记录。
>
> **本方案与总方案的对齐基准**：`docs/PROJECT_REQUIREMENTS.md`（需求）、`docs/ARCHITECTURE.md`（架构）、`docs/PROJECT_ANALYSIS.md`（现状分析）。

---

## 验证结论

| 验证项 | 结果 | 说明 |
|--------|------|------|
| `openwebui_tools/football_advisor_tools.py` | 存在 | OpenWebUI 工具注册脚本已就绪 |
| `football_advisor/provider_mapping_importer.py` | 存在 | Provider 映射导入器已就绪 |
| `football_advisor/api_football_client.py` | 存在 | 赔率+统计端点已接入，injuries/lineups 未接入 |
| `football_advisor/sync.py` | 存在 | DataSyncCoordinator 已编排 API-Football |
| `start_fastapi.bat` | 存在 | Windows 启动脚本已就绪 |
| `data/provider_mappings/` | 不存在 | B-1 步骤需要创建此目录 |
| `data/raw/` | 存在 | 含 database.sqlite、games.json、players_merged.json |
| `.env.example` | 存在 | 所有 FOOTBALL_* 配置项完整 |
| `docs/superpowers/plans/` | 存在 | 已有 roadmap 和 provider-mapping 计划 |
| 全量测试 | 114 通过 | `unittest discover -s tests -v` |
| 世界杯数据覆盖 | football-data.org 免费 ✅ / API-Football 免费 ✅ | 2026 世界杯（6月11日开幕）两个免费源均可覆盖 |

---

## 阶段 A：环境修复与配置就绪（阻塞性前置）

> 目标：让系统在本地环境中真正能跑起来，并验证关键路由。

### A-1：修复 qwen2.5:7b 崩溃

- [ ] 执行

> **[2026-05-31 实测验证]**：通过 `docker exec ollama ollama run qwen2.5:7b "你好，请回复 ok"` 验证成功。环境已挂载 GPU，未发生 127 错误，此项确认安全，目前环境无需修复。

**问题根因**：Ollama 容器配置了 `OLLAMA_GPU_LAYERS=999`，在无 GPU 直通的 Docker Desktop on Windows 环境下，qwen2.5:7b（4.4GB）加载时 GPU VRAM 超时 5.5 秒，runner 进程被杀，exit status 127。bge-m3（566MB）不受影响。

**修复操作**：

```powershell
docker stop ollama
docker rm ollama
# 重新 run 时去掉 --env OLLAMA_GPU_LAYERS=999
```

**验证标准**：

```powershell
ollama run qwen2.5:7b "你好，请回复 ok"
```

预期：不掉线，返回 ok。

---

### A-2：配置 .env 文件

- [ ] 执行

> **[2026-05-31 实测验证]**：`.env` 文件已存在，且 FastAPI 服务对 `/health` 返回了 `{"status": "ok"}`。核心服务就绪。

**操作**：

1. 复制 `.env.example` 为 `.env`（项目根目录）
2. 填入你已有的真实 API Key

**最小配置（无外部 API 也能跑）**：

```ini
FOOTBALL_DUCKDB_PATH=football_system.db
FOOTBALL_CHROMA_OLLAMA_BASE_URL=http://localhost:11434/api/embeddings
FOOTBALL_BACKUP_LLM_BASE_URL=http://localhost:11434/v1
```

**推荐配置（有 API Key 时，按物美价廉原则：免费优先，付费在后）**：

```ini
# ---- 外部 LLM（提升报告质量）----
FOOTBALL_EXTERNAL_LLM_BASE_URL=<你的LLM地址>
FOOTBALL_EXTERNAL_LLM_API_KEY=<你的API_KEY>

# ---- football-data.org（免费赛程/比分，优先）----
FOOTBALL_DATA_API_TOKEN=<你的API_TOKEN>

# ---- API-Football（免费+付费，免费计划已含赔率/统计/伤停/阵容/预测）----
API_FOOTBALL_TOKEN=<你的API_TOKEN>

# ---- 新闻搜索（SearXNG 或 Google）----
FOOTBALL_SEARXNG_BASE_URL=<你的SEARXNG地址>
```

**验证标准**：

```powershell
start_fastapi.bat
```

浏览器访问 `http://127.0.0.1:8000/health`，返回 `{"status":"ok"}`。

---

### A-3：LLM 路由验证

- [ ] 执行

> **[对应需求]**：PROJECT_REQUIREMENTS.md L21-22 — 外部 LLM 优先，Qwen 备份。

**前置条件**：A-1、A-2 已完成

**验证内容**：

1. 外部 LLM 已配置时，确认 `/predict` 报告由外部 LLM 生成（非 local Qwen）
2. 外部 LLM 不可用时，确认 `/predict` 报告自动降级到本地 Qwen2.5-7B
3. 验证 LLM 报告中的概率值来自概率引擎（非 LLM 编造），LLM 只负责文本化报告

**验证标准**：

```powershell
# 场景 1：外部 LLM 可用
curl -X POST http://127.0.0.1:8000/predict -H "Content-Type: application/json" -d "{\"query\": \"曼城 vs 利物浦\"}"
# 检查报告开头：应显示 "generated_by: external_llm"
```

```powershell
# 场景 2：模拟外部 LLM 不可用（临时注释 .env 中外部 LLM 配置后重启）
curl -X POST http://127.0.0.1:8000/predict -H "Content-Type: application/json" -d "{\"query\": \"曼城 vs 利物浦\"}"
# 检查报告开头：应显示 "generated_by: backup_llm (qwen2.5:7b)"
```

---

## 阶段 B：数据源补全（核心价值提升）

> 目标：让预测链路吃上真实数据。**严格遵守物美价廉原则**：免费为主，太贵的不要。
> **数据源优先级**：竞彩（Sporttery）是赔率和玩法状态的 Priority 1 权威主体，必须先完成采集、映射、五玩法落库和可用性验证；其他免费源可并行调用，但只用于赛程、统计、伤停和赔率偏差核验，不得替代竞彩赔率或竞彩未开售玩法。
> **世界杯优先**：2026 世界杯 6 月 11 日开幕，多个免费数据源均已覆盖世界杯，B-1 Provider Mapping 需优先录入世界杯联赛/球队映射。

### B-0：竞彩五玩法接入与落库验证（阻塞性前置）

- [ ] 执行

**最高优先规则**：必须先完成中国竞彩足球的胜平负（SPF）、让球胜平负（RQSPF）、比分（CRS）、总进球（TTG）和半全场胜平负（HAFU）接入，再处理补充数据源。任何一种已开售玩法未成功落库时，阶段 B 不得判定完成。

**操作**：

1. 先确认目标比赛、联赛、主队和客队已存在标准实体；缺失时先补齐标准实体，不得创建悬空 provider 映射。
2. 从竞彩官方数据链路采集比赛、销售状态、截止时间和五种玩法赔率，保留来源时间、采集时间、比赛编号、玩法代码和原始载荷引用。
3. 创建或复用 `data/provider_mappings/`，在 `provider_league_mappings.csv` 和 `provider_team_mappings.csv` 中增加 `SportteryOfficialWeb` 对应的联赛、主队和客队映射；未映射记录必须进入隔离和待处理清单，不得静默丢弃。
4. 将 SPF、RQSPF 三路赔率写入 `staging.stg_odds`，将 CRS、TTG、HAFU 明细写入 `staging.stg_sporttery_odds_detail`；必须幂等写入并保留玩法销售状态。
5. 对每场比赛验证五种玩法：已开售则赔率必须完整且可查询，未开售则明确记录 `sporttery_play_type_not_offered`，不得用第三方同类数据补位。
6. 只有竞彩数据已在 DuckDB 中可查询、映射命中、玩法状态可区分、来源时间满足要求后，才允许处理 API-Football 等补充源。

**验证标准**：

- `staging.stg_odds` 可查询目标比赛的 SPF、RQSPF 三路赔率和来源时间。
- `staging.stg_sporttery_odds_detail` 可查询目标比赛的 CRS、TTG、HAFU 明细或明确的未开售状态。
- 所有竞彩记录均能关联标准比赛、联赛和球队；失败记录有隔离原因和可重放证据。
- 补充源关闭时，竞彩五玩法采集和查询仍可独立完成。
- 竞彩缺失或关键玩法状态未知时，预测流程必须进入 No Bet，不得用其他赔率源替代。

---

### B-0.1：世界杯数据预检（阻塞性前置）

- [ ] 执行

> **[2026-05-31 代码审查]**：Provider Mapping 存在两层依赖，CSV 映射写入前必须先确保 DuckDB 核心表中已有世界杯记录，否则数据会被三层防线静默丢弃。

**问题根因**：系统有三道防线拦截无映射的外部数据：

1. **CSV 导入层**（[provider_mapping_importer.py:L91-L97](file:///c:/Users/admin/.trae-cn/worktrees/Knowledge_Base-duckdb/feat-project-next-steps-uwaYZL/football_advisor/provider_mapping_importer.py#L91-L97)）：`system_league_id` 不在 `dim_league_mapping` 中 → 跳过该行
2. **预测视图层**（[db_schema.py:L347](file:///c:/Users/admin/.trae-cn/worktrees/Knowledge_Base-duckdb/feat-project-next-steps-uwaYZL/football_advisor/db_schema.py#L347)）：INNER JOIN `dim_league_mapping` → 无记录则预测视图返回空
3. **数据合并层**（[api_football_client.py:L321-L323](file:///c:/Users/admin/.trae-cn/worktrees/Knowledge_Base-duckdb/feat-project-next-steps-uwaYZL/football_advisor/api_football_client.py#L321-L323)）：JOIN `dim_provider_team_mapping` → 无映射则数据落入黑洞

**前置条件**：A-2 和 B-0 已完成

**操作**：

1. 检查 DuckDB 中是否已有世界杯/国家队记录：

```powershell
.\.runtime\python\python.exe -c "
import duckdb
conn = duckdb.connect('football_system.db')
# 检查世界杯联赛
leagues = conn.execute(\"SELECT * FROM core.dim_league_mapping WHERE league_standard_name LIKE '%World%' OR league_standard_name LIKE '%FIFA%'\").fetchall()
print('世界杯联赛记录:', leagues)
# 检查国家队（示例：巴西、德国、阿根廷等）
teams = conn.execute(\"SELECT * FROM core.dim_team_mapping WHERE team_standard_name IN ('Brazil', 'Germany', 'Argentina', 'France', 'Spain', 'England') LIMIT 10\").fetchall()
print('国家队记录数:', len(teams))
conn.close()
"
```

2. 如果缺失，从 `data/raw/` 或外部源补充世界杯 league/team 基础记录到 `core.dim_league_mapping` 和 `core.dim_team_mapping`
3. 确认 API-Football 2026 世界杯的 League ID（通常为 `1` 或 `15`）和 football-data.org 的 League Code（`WC`）

**验证标准**：DuckDB 中 `dim_league_mapping` 至少有一条世界杯记录，`dim_team_mapping` 至少有 32 条世界杯参赛国家队记录。

---

### B-1：补充 Provider Mapping CSV + 导入

- [ ] 执行

> **[2026-05-31 历史实测]**：当时执行导入脚本输出 `ProviderMappingImportResult(league_inserted=0, team_inserted=0)`，原因是目录缺失。现行流程已由 B-0 先创建或复用映射目录并补齐竞彩映射；本步骤继续补充和验证其他供应商映射。

**前置条件**：A-2、B-0 和 B-0.1 已完成

**操作**：

1. 确认 `data/provider_mappings/` 已由 B-0 创建，且竞彩映射仍然完整。
2. 在 `provider_league_mappings.csv` 中补充其他供应商映射：

```csv
provider_name,provider_league_id,system_league_id,provider_league_name
```

3. 在 `provider_team_mappings.csv` 中补充其他供应商映射：

```csv
provider_name,provider_team_id,system_team_id,provider_team_name
```

4. 执行导入：

```powershell
.\.runtime\python\python.exe -c "import duckdb; from football_advisor.provider_mapping_importer import import_provider_mappings; conn = duckdb.connect('football_system.db'); r = import_provider_mappings(conn, 'data/provider_mappings'); print(r)"
```

**说明**：`system_league_id` 和 `system_team_id` 的值来自 DuckDB 中 `core.dim_league_mapping` 和 `core.dim_team_mapping` 表。此步骤**是所有外部数据接入的前提**，无论使用免费还是付费 API，都需要 Provider Mapping 来将外部联赛/球队 ID 映射到系统内部 ID。**世界杯优先**：2026 世界杯（2026年6月11日开幕）联赛/球队映射应优先录入，确保世界杯开赛前预测链路可用。

> **⚠️ 两层依赖警示**：CSV 映射写入前，必须先通过 B-0 确认 `dim_league_mapping` 和 `dim_team_mapping` 中已有世界杯/国家队记录。Provider Mapping 导入器（[provider_mapping_importer.py:L91-L97](file:///c:/Users/admin/.trae-cn/worktrees/Knowledge_Base-duckdb/feat-project-next-steps-uwaYZL/football_advisor/provider_mapping_importer.py#L91-L97)）会校验 `system_league_id` 是否在 `dim_league_mapping` 中存在，不存在则跳过。**先补核心表，再写 CSV 映射，顺序不可颠倒。**

**验证标准**：导入器输出 `league_inserted` 和 `team_inserted` 数量大于 0。

---

### B-1.5：football-data.org 免费源接入（赛程/比分/积分榜）

- [ ] 执行

> **多源角色**：赛程框架和比分数据的主力源。与其他免费源（API-Football、TheSportsDB）**并行调用**，交叉验证比分一致性。
> **[对应需求]**：PROJECT_REQUIREMENTS.md L47-48 — 物美价廉原则，免费结构化源优先于付费 API。
> **[对应需求]**：PROJECT_REQUIREMENTS.md L49 — 赛程、比分等关键结构化数据优先走免费结构化源。

**前置条件**：A-2 已完成，且已配置 `FOOTBALL_DATA_API_TOKEN`

**说明**：football-data.org 提供免费 tier 的赛程、比分、积分榜数据（10 次/分钟），免费覆盖 12 个顶级联赛/杯赛，包含 **英超、德甲、西甲、意甲、法甲、欧冠、世界杯** 等。是多源交叉验证中赛程框架的主力源。

**修改文件**：

- `football_advisor/football_data_client.py`（如不存在则新建）：封装 football-data.org API
- `football_advisor/sync.py`：在 DataSyncCoordinator 中注册 football-data.org 作为赛程/比分同步源
- `tests/test_football_data_sync.py`：新增 fake 端点测试

**接入策略**：免费源数据缺失时回退到本地缓存，不触发 No Bet；仅在免费源和缓存均不可用时，才考虑启用付费计划（football-data.org 付费或 API-Football 付费）。

**验证标准**：

```powershell
.\.runtime\python\python.exe -m unittest tests.test_football_data_sync -v
```

---

### B-2：API-Football 免费计划接入 + 伤停/阵容端点补全

- [ ] 执行

> **多源角色**：统计、伤停和阵容数据的主要补充源；其赔率只能用于相对竞彩的偏差核验。与其他免费源（football-data.org、TheSportsDB）和试用日期源（Sportmonks、iSports API）并行调用，交叉验证赛程、统计和阵容一致性。

> **[2026-05-31 实测验证]**：查阅代码并执行测试（6/6通过），确认当前仅对接了 `/odds` 和 `/statistics`，未开发伤停和阵容端点。属实，待开发。
> **[2026-05-31 数据源核实]**：API-Football 并非纯付费 API。实地验证其提供 **$0/月免费计划**（100 请求/天），免费计划包含所有端点：Livescore、Fixtures、Line Ups、Injuries、In-play Odds、Pre-match Odds、Statistics、Predictions、Sidelined、Head 2 Head、Top Scorers、Players、Transfers、Trophies。限制：免费计划可用赛季数有限。覆盖 1200+ 联赛/杯赛，含世界杯、欧洲杯、五大联赛等。

**前置条件**：A-2 和 B-0 已完成，且已配置 `API_FOOTBALL_TOKEN`。API-Football 免费计划与 football-data.org、TheSportsDB、Sportmonks、iSports API 等补充源可并行调用；它们之间无先后依赖，但整体上不得先于或替代竞彩主体链路。

**当前状态**：赔率端点和统计端点已接入；伤停端点和阵容端点尚未接入。

**修改文件**：

- `football_advisor/api_football_client.py`：新增伤停和阵容端点解析方法
- `tests/test_api_football_sync.py`：新增 fake 端点测试

**接入策略**：缺失即 No Bet，不做猜测补齐。字段缺失时保守跳过，由 No Bet 门禁兜底。

> **⚠️ 世界杯冷启动风险**：API-Football 对于五大联赛的滚动统计（xG、控球率、PPDA）较完善，但对**国家队比赛极其短板**。国家队几年才踢一次大赛，大名单变化极大，DuckDB 中的国家队历史数据来自 SQLite/CSV 导入 → 标记 `SQLITE_PRIOR_MATCHES_ONLY` → 触发 `derived_team_stats_quality_flag`（[db_schema.py:L502-L504](file:///c:/Users/admin/.trae-cn/worktrees/Knowledge_Base-duckdb/feat-project-next-steps-uwaYZL/football_advisor/db_schema.py#L502-L504)）→ 触发 `no_bet_data_quality_flag`（[no_bet_policy.py:L61-L63](file:///c:/Users/admin/.trae-cn/worktrees/Knowledge_Base-duckdb/feat-project-next-steps-uwaYZL/football_advisor/no_bet_policy.py#L61-L63)）→ **No Bet**。世界杯小组赛第一轮，系统大概率因历史数据置信度太低持续输出 No Bet。**这是系统在按设计保护你，不要为了强行出票而修改代码调低阈值。** 随着小组赛推进，API-Football 实时数据逐步积累，No Bet 会自然减少。

**验证标准**：

```powershell
.\.runtime\python\python.exe -m unittest tests.test_api_football_sync -v
```

---

### B-2.5：多源交叉验证架构（核心新增）

- [ ] 执行

> **[2026-06-15 架构纠偏]**：竞彩先完成玩法状态和赔率落库，免费补充源再并行调用并交叉验证。此架构同时适用于世界杯和五大联赛，但任何补充源都不得覆盖或替代竞彩主体数据。

**架构原理**：

```
竞彩官方数据 ──→ 五玩法状态/赔率 ──→ Priority 1 主体数据
                               │
                     ┌→ football-data.org ──→ 赛程/比分
                     │
                     ├→ API-Football ────────→ 统计/伤停/辅助赔率核验
                     │
比赛请求 ──→ MultiSourceCoordinator ─┼→ TheSportsDB ──────────→ 阵容/历史
                     │
                     ├→ [Sportmonks] ────────→ 日期赛程/xG/Pressure Index（6/9-6/22试用期）
                     │
                     └→ [iSports API] ───────→ 日期赛程/亚洲盘口/水位（6/21-7/5试用期）
                              │
                              ▼
                     CrossValidator（交叉验证层）
                       - 比分一致性检查
                       - 相对竞彩赔率偏差检测（>5% 标记辅助源异常）
                       - 阵容差异标记（多源取并集）
                       - 数据源可信度加权
                              │
                              ▼
                     Staging → Core
```

**交叉验证规则**：

| 验证项 | 规则 | 异常处理 |
|--------|------|----------|
| 比分 | 多数一致则采纳，全部不一致则标记 `score_cross_check_failed` | 标记但不阻止入库，No Bet 层读取 flag 决定 |
| 赔率 | 竞彩已开售时以竞彩为权威主体；辅助源相对竞彩偏差 >5% 时标记 `odds_deviation_flag` | 保留竞彩原值并标记异常辅助源，禁止多源平均覆盖竞彩 |
| 竞彩玩法状态 | 竞彩某玩法未提供时记录 `sporttery_play_type_not_offered` | 不得用 API-Football 或其他源的同类型赔率替代 |
| 阵容 | 多源取并集，仅一个源报告的球员标记 `lineup_uncertain` | 纳入入库但标记不确定性 |
| 伤停 | 至少 2 个源确认才算有效伤停 | 单源报告的伤停不触发 No Bet |
| 数据覆盖 | 记录每个字段的来源覆盖情况到 `multi_source_coverage_flag` | 单源覆盖时降低该字段权重 |

**修改文件**：

- `football_advisor/multi_source_coordinator.py`（新建）：并行调度器，asyncio 同时请求多个源
- `football_advisor/cross_validator.py`（新建）：交叉验证规则引擎
- `football_advisor/sync.py`：将 `DataSyncCoordinator` 改为调用 `MultiSourceCoordinator`

**验证标准**：

```powershell
.\.runtime\python\python.exe -m unittest tests.test_multi_source_coordinator -v
.\.runtime\python\python.exe -m unittest tests.test_cross_validator -v
```

### B-2.6：TheSportsDB + Sportmonks/iSports 日期源接入

- [ ] 执行

> **多源角色**：TheSportsDB 提供国家队阵容/历史数据（众包开放数据库），Sportmonks 与 iSports API 按日期补充赛程和赛前数据。OddsMatrix 已删除，不再作为项目数据源。

**TheSportsDB 接入**：

- 注册免费 API Key（无需付费）
- 新建 `football_advisor/thesportsdb_client.py`
- 实现 `fetch_team_squad()`、`fetch_historical_matches()` 端点
- 世界杯 FIFA World Cup League ID = 4429

**Sportmonks/iSports 接入**：

- 新建 `football_advisor/sportmonks_client.py`
- 新建 `football_advisor/isports_client.py`
- Sportmonks 使用 `fixtures/date/{YYYY-MM-DD}`
- iSports 使用 `sport/football/schedule?date={YYYY-MM-DD}`
- 按 `kickoff_time` 的 UTC 日期启用；未传入时使用当前 UTC 日期
- 先写入 `staging.stg_match_schedule`，不绕过 provider mapping 直接写 core

**验证标准**：

```powershell
.\.runtime\python\python.exe -m unittest tests.test_thesportsdb_client -v
.\.runtime\python\python.exe -m unittest tests.test_sportmonks_client tests.test_isports_client -v
```

---

### B-3：新闻搜索 → 抓取 → 清洗 → ChromaDB 入库闭环

- [ ] 执行

> **[对应需求]**：PROJECT_REQUIREMENTS.md L50 — 新闻优先走 ChromaDB 缓存、SearXNG/Google Search，再进入抓取、清洗、去重和 embedding 入库。
> **[2026-05-31 实测验证]**：向 `/search/preview` 提交测试，返回 `skipped_no_provider_configured`。属实，无 API Key 时系统正确触发防御性降级。

**前置条件**：A-2 已完成，且已配置 `FOOTBALL_SEARXNG_BASE_URL` 或 Google Search API Key

**当前状态**：SearchRouter 和 TextFetcher 已就绪。真实 API Key 未配置时搜索链路返回 `skipped_no_provider_configured`。

**完整链路（需逐一验证）**：

```
搜索 → 抓取 → 清洗 → 去重 → bge-m3 embedding → ChromaDB 入库 → 结构化摘要 → DuckDB staging
```

**操作**：

1. 配置 SearXNG 或 Google Search Key
2. 执行搜索预览验证：

```powershell
curl -X POST http://127.0.0.1:8000/search/preview -H "Content-Type: application/json" -d "{\"query\": \"Arsenal injury news 2026\"}"
```

3. 执行抓取验证：

```powershell
curl -X POST http://127.0.0.1:8000/search/fetch-preview -H "Content-Type: application/json" -d "{\"url\": \"<搜索结果中的真实URL>\"}"
```

4. 验证 ChromaDB 中 news 集合有新增 embedding 记录
5. 验证 DuckDB `staging.stg_news` 中有对应的结构化摘要记录

**说明**：如果没有 SearXNG 实例，此步骤可跳过。新闻信号缺失时系统会触发 No Bet 门禁。

**验证标准**：搜索预览返回结果列表，且 ChromaDB 和 DuckDB staging 表中有对应记录。

---

### B-4：端到端真实烟测

- [ ] 执行

> **[2026-05-31 实测验证]**：`POST /predict` 正常返回 HTTP 200 及完整 Markdown 报告。报告中输出了 `No Bet` 原因（因缺少有效数据被强制拦截）。机制生效，属实。

**前置条件**：A-1、A-2、A-3 已完成

**操作**：

```powershell
curl -X POST http://127.0.0.1:8000/predict -H "Content-Type: application/json" -d "{\"query\": \"曼城 vs 利物浦\"}"
```

**检查清单**：

- [ ] 返回结构化 Markdown 报告
- [ ] 报告包含概率数值（非全 0）
- [ ] 报告包含数据来源说明
- [ ] 如果无外部 API，报告中明确显示 No Bet 原因
- [ ] 不存在 `1970-01-01` 时间戳
- [ ] 不存在 `placeholder_fallback` 字样
- [ ] 报告包含核心字段：胜平负倾向、比分参考、大小球倾向、置信度、风险等级、价值差、数据更新时间（对应 REQUIREMENTS.md L87-111）

**验证标准**：HTTP 200，返回完整报告。

---

### B-5：数据质量校验清单验证

- [x] 执行

> **[2026-05-31 落地验证]**：已补齐剩余 3 项校验（类型范围、重复检测、异常波动），`CrossValidator` 现在输出 `invalid_range_sources`、`duplicate_record_sources`、`anomaly_sources`，触发时进入 `cross_check_failed=True`，并由同步质量门禁进入 No Bet。

> **[对应需求]**：PROJECT_REQUIREMENTS.md L55-56 — 数据进入预测前必须经过规则硬校验：字段完整性、类型范围、时间新鲜度、未来数据穿越、实体映射、重复检测、来源可信度、多源一致性、异常波动检查。

**前置条件**：B-4 已完成

**验证内容**（逐项确认）：

- [x] **字段完整性**：必填字段缺失时触发 `missing_*_flag` 并进入 No Bet
- [x] **类型范围**：数值型字段在合理范围内（如赔率 1.01-1000，概率 0-1）
- [x] **时间新鲜度**：关键数据不超过 30 分钟（实时路径）或开赛前不超过 30 分钟（回测路径）
- [x] **未来数据穿越**：`data_age < 0` 触发 No Bet
- [x] **实体映射**：外部联赛/球队 ID 能正确映射到系统内部 ID
- [x] **重复检测**：同场比赛同数据源不重复入库
- [x] **来源可信度**：数据来源标记正确（`local_cache` / `free_structured` / `paid_api`）
- [x] **多源一致性**：多源数据冲突时有标记和仲裁逻辑
- [x] **异常波动**：赔率/统计突变 ≥ 3σ 时触发异常标记

**验证方式**：对一场真实比赛执行 `/predict`，检查日志和返回报告中的质量标记字段。

**验证标准**：9 项校验均有代码路径与测试覆盖；真实 provider 接入后再执行生产烟测确认日志与报告字段。

---

### 世界杯专项数据源（免费为主 + 试用白嫖）

> 以下数据源均已在 B-1.5/B-2/B-2.6 中纳入多源交叉验证架构。Sportmonks 和 iSports API 通过免费试用期临时增强数据质量，试用结束后回归纯免费源。
> 世界杯和五大联赛共用同一套数据源和交叉验证规则。

| 数据源 | 费用 | 世界杯角色 | 五大联赛角色 | 可用时段 |
|--------|------|-----------|-------------|----------|
| football-data.org | 0 元 | 赛程框架 + 比分 + 积分榜 | 赛程框架 + 比分 + 积分榜 | 全程 |
| API-Football | 0 元 | 赔率 + 统计 + 伤停 + 阵容 + 预测 | 赔率 + 统计 + 伤停 + 阵容 + 预测 | 全程 |
| TheSportsDB | 0 元 | 国家队阵容 + 历史数据 | 俱乐部阵容 + 历史数据 | 全程 |
| SearXNG | 0 元 | 新闻 → ChromaDB | 新闻 → ChromaDB | 全程 |
| **Sportmonks** | **0 元（14天试用）** | 日期赛程 + xG/Pressure Index/50+赔率 | 日期赛程补充 | 6/9-6/22 |
| **iSports API** | **0 元（15天试用）** | 日期赛程 + 亚洲盘口/水位变动 | 日期赛程补充 | 6/21-7/5 |

> **已淘汰的付费源**（不符合免费为主原则，且无可用免费试用）：Enetpulse（企业报价）、Stats Perform/Sportradar（企业级）、worldcupapi.com（€499/月）

---

### 世界杯白嫖排期：试用期最大化覆盖策略

> Sportmonks 和 iSports API 虽然付费计划不符合免费为主原则，但两者均提供免费试用期，可以在不花钱的前提下临时增强数据质量。

#### 试用条件

| 平台 | 试用期 | 限制 | 核心价值 |
|------|--------|------|----------|
| **Sportmonks** | **14 天** | 无风险测试，需要信用卡注册 | All-In 全端点：xG/Pressure Index/50+博彩公司赔率 |
| **iSports API** | **15 天** | 200 次/天/运动，2 分钟延迟 | 亚洲盘口水位秒级变动、让球/大小球市场数据 |

#### 世界杯赛程速览（39 天 / 104 场）

```
6/11 ─── 6/27 ─── 7/3 ─── 7/7 ─── 7/11 ─── 7/14 ─── 7/19
 ├─ 小组赛 ─┤ ├16强┤ ├8强┤ ├半决赛┤ ├3/4┤ ├决赛┤
  72 场        16场   8场    2场     1场    1场
```

#### 最优排期方案

```
6月                              7月
├────────┼────────┼────────┼────────┼────────┤
9        16       23       30       7        14       19

████████████████████████  Sportmonks 14天
6/9────────────────────────6/22
  小组赛第1轮全 + 第2轮半程（≈40场）

                ██████████████████████████████  iSports API 15天
                6/21────────────────────────────────7/5
                  小组赛第2轮后半 + 第3轮 + 16强 + 8强（≈35场）

░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  空窗期 → 免费源全程兜底
6/9────────────────────────────────────────────────────────7/19
（football-data.org + API-Football + TheSportsDB 全程在线）
```

#### 排期逻辑

| 时段 | 数据源 | 比赛 | 理由 |
|------|--------|------|------|
| 6/9-6/10 | Sportmonks | 预热 | 拉取参赛队历史数据、建立数据基线 |
| 6/11-6/20 | Sportmonks | 小组赛第1轮 | xG/Pressure Index 对初期冷启动至关重要 |
| 6/21-6/22 | **双源重叠** | 小组赛第2轮 | 唯一交叉验证窗口！两源同时拉取 |
| 6/23-6/27 | iSports API | 小组赛第3轮 | 出线关键轮，亚洲盘口大资金涌入 |
| 6/28-7/5 | iSports API | 16强+8强 | 淘汰赛赔率波动剧烈，亚盘优势最大化 |
| 7/6-7/19 | **仅免费源** | 半决赛+决赛 | 全球关注度最高，免费源和新闻数据已足够 |

#### 覆盖统计

```
总赛期:    39 天 / 104 场
试用覆盖:  27 天 / ≈75 场 (72%)
免费覆盖:  39 天 / 104 场 (100%，作为基线)
双源重叠:   2 天 / ≈6 场 (交叉验证黄金窗口)
空窗期:    14 天 / ≈29 场 (半决赛+决赛靠免费源)
```

#### 操作清单

| 截止日期 | 操作 | 备注 |
|----------|------|------|
| **6月9日** | 注册并启用 Sportmonks 14天试用 | 立即保存 API Token，覆盖 6/9-6/22 小组赛关键期 |
| **6月21日** | 注册并启用 iSports API 15天试用 | 立即保存 API Token，覆盖 6/21-7/5 小组赛后段与淘汰赛前段 |
| **6月22日** | `MultiSourceCoordinator` 双源同时拉取 | 交叉验证：比分/赔率/阵容多源比对 |
| **7月6日起** | 试用全部结束，回归纯免费源 | 半决赛和决赛数据密度低，免费源足够 |

> **关键提醒**：不要在 6 月 9 日之前激活任何试用。试用期按自然日计算，不是按比赛日。Sportmonks 需要信用卡注册（记得试用结束前取消），iSports API 限制每天 200 次调用需合理分配。

---

## 阶段 C：前端集成（OpenWebUI 真实验收）

> 目标：让 OpenWebUI 成为真正可用的用户入口。**OpenWebUI 是唯一前端入口**（REQUIREMENTS.md L17）。

### C-1：在 OpenWebUI 中注册 FootballAdvisor 工具

- [ ] 执行

> **[2026-05-31 实测验证]**：代码文件 `openwebui_tools/football_advisor_tools.py` 完整就绪。属实。

**前置条件**：A-1、A-2 已完成

**操作**：

1. 确保 FastAPI 已启动（`start_fastapi.bat`）
2. 浏览器访问 `http://127.0.0.1:3000`
3. 进入 Workspace → Tools（左下角 → 管理员面板 → 工具）
4. 点击 "+ 创建工具"
5. 粘贴 `openwebui_tools/football_advisor_tools.py` 全部代码
6. 在 Valves 中确认 `API_BASE_URL` = `http://host.docker.internal:8000`
7. 保存

**验证标准**：工具列表中出现 `predict_match`、`run_backtest`、`check_api_status` 三个工具。

---

### C-2：OpenWebUI 端到端对话测试

- [ ] 执行

> **[2026-05-31 实测验证]**：前端黑盒验证，需在 C-1/C-3 修复后手动在界面执行。

**前置条件**：C-1 已完成

**操作**：

1. 在 OpenWebUI 中新建对话
2. 选择模型（如 qwen2.5:7b）
3. 输入：`帮我分析一下曼城 vs 利物浦这场比赛`
4. 观察是否自动调用 `predict_match` 工具并返回报告

**验证标准**：收到完整的结构化分析报告，包含概率、赔率价值、风险等级等字段。

---

### C-3：FastAPI 网络连通性修复（按需）

- [ ] 执行

> **[2026-05-31 实测验证]**：`start_fastapi.bat` 确实硬编码了 `--host 127.0.0.1`。属实，这会阻塞 Docker 内 OpenWebUI 的访问。
> **[安全审查]**：已修正为默认 `127.0.0.1`，仅通过 `FOOTBALL_HOST=0.0.0.0` 环境变量开放 Docker 访问。

**前置条件**：C-1 已完成且遇到网络不通问题

**问题场景**：OpenWebUI 在 Docker 的 `ollama_default` 网络，FastAPI 在宿主机。

**修复操作**：

```powershell
set FOOTBALL_HOST=0.0.0.0
start_fastapi.bat
```

让 Docker 容器通过 `host.docker.internal:8000` 访问。

**验证标准**：

```powershell
docker exec openwebui python3 -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:8000/health').read())"
```

预期输出：`{"status":"ok"}`

---

## 阶段 D：生产化保障

> 目标：让系统长期稳定运行，满足日常使用需求。

### D-1：定时数据同步任务（必做）

- [ ] 执行

> **[对应需求]**：PROJECT_REQUIREMENTS.md L40 — "支持日常定时基础数据同步"。**这是需求，不是可选项。**
> **[2026-05-31 实测验证]**：项目内仅有执行脚本，无定时任务引擎。属实，需依赖宿主机任务计划程序。

**前置条件**：B-4 已完成

**操作**：创建 Windows 计划任务，每日定时执行数据同步脚本。

**验证标准**：计划任务执行后，DuckDB 中 `core.fact_match_schedule` 的 `updated_at` 时间刷新。

---

### D-2：真实交易所资金流接入（可选）

- [ ] 执行

> **[2026-05-31 实测验证]**：调用同步代码返回 `skipped_no_provider_configured`。属实，目前为 Mock 逻辑，缺真实交易所客户端。

**前置条件**：B-1 已完成，且有 Betfair 或其他交易所 API 凭据

**当前状态**：MockBetfairClient 仅用于测试。未配置真实客户端时资金流同步返回 `skipped_no_provider_configured`，触发 No Bet 质量门禁。**此步骤优先级最低**，因为赔率价值引擎已通过 API-Football 赔率端点工作，交易所资金流是增强信号而非必需。

**修改文件**：

- 新建真实交易所客户端
- 修改 `football_advisor/sync.py`，注入真实客户端

**验证标准**：资金流同步不再返回 `skipped_no_provider_configured`。

---

### D-3：历史回测参数校准 + 概率校准

- [ ] 执行

> **[对应需求]**：PROJECT_REQUIREMENTS.md L83-85 — "必须支持严格时间顺序回测，防止未来数据穿越" + "必须支持概率校准与指标评估"。
> **[2026-05-31 实测验证]**：执行 `run_backtest.py` 确认 200 场全数被拒。查阅 `db_schema.py` 确认使用了 `CURRENT_TIMESTAMP` 导致历史数据超龄。属实，致命 BUG，已修正。

**前置条件**：B-4 已完成

**已修正内容**（2026-05-31）：

- `backtest_engine.py`：`_with_historical_data_quality()` 中 `age > 30min` 锚点从 `CURRENT_TIMESTAMP` 改为 `kickoff_time`，保留 `derived_team_stats_quality_flag`，新增 `backtest_data_quality_tier` 分层标记

**当前门禁**：

```
no_bet_data_quality_flag =
    missing_team_stats
    OR derived_team_stats_quality_flag
    OR missing_1x2_odds_flag
    OR data_age_minutes < 0
    OR data_age_minutes > 30  (锚点: kickoff_time)
```

**待完成内容**：

1. **概率校准验证**：执行回测后检查 Brier Score、可靠性曲线、all_metrics 输出
2. **分层 ROI 报告**：利用 `backtest_data_quality_tier`（`basic_only` vs `full`）分层展示
3. **参数调优**：基于校准结果调整价值阈值（默认 5%）和风险等级阈值

**验证标准**：

```powershell
.\.runtime\python\python.exe run_backtest.py
```

回测投注数大于 0，Brier Score 有输出，分层 ROI 有意义。

---

## 执行优先级与依赖关系

```text
A-1（修复 qwen 崩溃）──────────────────────────── 必做
  │
  └→ A-2（配置 .env）─────────────────────────── 必做
        │
        └→ A-3（LLM 路由验证）───────────────── 必做
              │
              ├→ B-0（世界杯数据预检）────────── 必做（世界杯阻塞性前置）
              │
              ├→ B-1（Provider Mapping CSV）──── 必做（所有外部数据接入的前提）
              │     │
              │     ├→ B-1.5（football-data.org 免费）── 赛程/比分，多源并行
              │     │
              │     ├→ B-2（API-Football 免费）───────── 赔率/统计/伤停，多源并行
              │     │
              │     ├→ B-2.5（多源交叉验证架构）─────── 必做（核心新增）
              │     │
              │     ├→ B-2.6（TheSportsDB + Sportmonks/iSports 日期源）── 多源并行
              │
              ├→ B-3（新闻搜索 → ChromaDB 入库）──────── 有 SearXNG/Google 时做
              │
              ├→ B-4（端到端烟测）────────────── 必做
              │     │
              │     ├→ B-5（数据质量校验清单）── 必做
              │     │
              │     ├→ C-1（OpenWebUI 注册）──── 必做
              │     │     └→ C-2（对话验收）──── 必做
              │     │           └→ C-3（网络修复）── 按需
              │     │
              │     └→ D-1（定时同步）────────── 必做（需求要求）
              │           └→ D-3（回测+校准）─── 必做（需求要求）
              │
              └→ D-2（交易所接入）────────────── 可选（优先级最低）
```

### 推荐执行路径

**最简路径**（仅本地 Ollama，无外部 API Key）：

```text
A-1 → A-2 → A-3 → B-4 → B-5 → C-1 → C-2
```

**标准路径**（全部免费源 + 世界杯/五大联赛就绪）：

```text
A-1 → A-2 → A-3 → B-0 → B-0.1 → B-1 → B-1.5 → B-2 → B-2.5 → B-2.6 → B-4 → B-5 → C-1 → C-2 → D-1 → D-3
```

**完整路径**（全部免费源 + 新闻搜索 + 定时同步）：

```text
A-1 → A-2 → A-3 → B-0 → B-0.1 → B-1 → B-1.5 → B-2 → B-2.5 → B-2.6 → B-3 → B-4 → B-5 → C-1 → C-2 → C-3 → D-1 → D-3 → D-2
```

---

## 变更记录

| 日期 | 步骤 | 状态 | 说明 |
|------|------|------|------|
| 2026-05-31 | 全方案 | 二次审查 | 对照 PROJECT_REQUIREMENTS.md 逐条审查，发现 6 处不一致，全部修正 |
| 2026-05-31 | 新增 B-1.5 | 已添加 | football-data.org 免费结构化源接入，在 B-2 付费 API 之前，落实物美价廉原则 |
| 2026-05-31 | 新增 A-3 | 已添加 | LLM 路由验证（外部优先 → Qwen 备份），对应 REQUIREMENTS.md L21-22 |
| 2026-05-31 | 新增 B-5 | 已添加 | 数据质量校验清单验证（9 项硬校验），对应 REQUIREMENTS.md L55-56 |
| 2026-05-31 | B-3 扩展 | 已修改 | 从"搜索预览"扩展为完整链路（搜索 → 抓取 → 清洗 → ChromaDB → DuckDB），对应 REQUIREMENTS.md L50 |
| 2026-05-31 | D-1 优先级 | 已修正 | 从"可选"改为"必做"，对应 REQUIREMENTS.md L40 |
| 2026-05-31 | D-3 扩展 | 已修改 | 补充概率校准（Brier Score、可靠性曲线、分层 ROI），对应 REQUIREMENTS.md L85 |
| 2026-05-31 | D-2 优先级 | 已明确 | 标注为"优先级最低"，赔率价值引擎已通过 API-Football 赔率端点工作 |
| 2026-05-31 | 全方案 | 审查完成 | 13 步骤逐条对照需求/架构/代码审查，C-3 和 D-3 发现缺陷并修正 |
| 2026-05-31 | C-3 | 修正完成 | 默认改回 127.0.0.1（安全），仅 FOOTBALL_HOST=0.0.0.0 时开放 Docker 访问 |
| 2026-05-31 | D-3 | 修正完成 | 换锚点 kickoff_time 保留 age>30min + 保留 derived_quality + 新增分层标记 |
| 2026-05-31 | — | 方案创建 | 基于 v1.0.0-rc，在 `feat-production-readiness-review` 分支创建 |
| 2026-05-31 | B-2/A-2/优先级表 | 数据源核实修正 | 实地验证 4 个数据源网站，发现 API-Football 有 $0/月免费计划（全端点），方案原将其标记为"付费 API"有误。修正：B-2 从"付费增强"改为"免费计划接入+端点补全"，物美价廉优先级表新增 API-Football 免费计划为第 3 优先（0 元），补充世界杯覆盖列。确认 football-data.org 免费和 API-Football 免费均覆盖世界杯 |
| 2026-05-31 | 全方案 | 世界杯专项补充 | 验证结论表新增世界杯覆盖验证行；阶段B目标新增世界杯优先说明；B-1 补充世界杯联赛/球队映射优先录入；B-1.5 补充世界杯覆盖联赛列表；执行优先级图 B-2 标签修正；标准路径新增 B-2（API-Football 免费），标注"世界杯就绪" |
| 2026-05-31 | 全方案 | 世界杯落地分析修正 | 基于代码审查（Provider Mapping 三层防线、No Bet 冷启动链路）验证分析结论，写入方案：新增 B-0 世界杯数据预检步骤（阻塞性前置）；B-1 补充两层依赖警示；B-2 补充冷启动风险说明（小组赛大概率 No Bet）；新增「世界杯专项数据源与应急方案」章节（TheSportsDB 免费国家队数据 + Sportmonks €69-129/月 xG 平替 + 其他 4 个数据源评估）；执行优先级图新增 B-0；标准/完整路径新增 B-0 |
| 2026-05-31 | 全方案 | 免费为主 + 多源交叉验证重构 | 用户决策：免费为主，太贵的不要。淘汰高价付费源，确立免费/低成本数据源并行调用架构。新增 B-2.5 多源交叉验证步骤（MultiSourceCoordinator + CrossValidator，asyncio并行 + 5项交叉验证规则）。新增 B-2.6 TheSportsDB 免费源接入。物美价廉表从优先级排序改为并行无先后。阶段B目标重写为多源交叉验证概念。世界杯专项数据源章节简化为免费为主表。执行优先级图新增 B-2.5/B-2.6。标准/完整路径更新。明确此架构同时适用于世界杯和五大联赛。 |
| 2026-06-02 | 数据源 | 已修改 | 删除 OddsMatrix 数据源；Sportmonks 与 iSports API 改为按日期启用的试用数据源，先落 `staging.stg_match_schedule`，真实 token 配置后再做生产烟测。 |
| 2026-06-02 | 试用排期 | 已修正 | 按用户确认改为 Sportmonks 2026-06-09 注册并启用，覆盖 2026-06-09 至 2026-06-22；iSports API 2026-06-21 注册并启用，覆盖 2026-06-21 至 2026-07-05。 |
| 2026-05-31 | 全方案 | 试用白嫖排期策略 | 用户提供 Sportmonks（14天试用）和 iSports API（15天，200次/天，2分钟延迟）试用条件。设计最优排期：Sportmonks 6/9-6/22（≈40场，xG/Pressure Index覆盖小组赛关键期），iSports API 6/21-7/5（≈35场，亚洲盘口覆盖淘汰赛），6/21-22双源重叠交叉验证。试用覆盖 72%（75/104场），免费源全程兜底。多源架构图新增试用源 slot。操作清单含 6 个关键日期。世界杯数据源表新增 Sportmonks/iSports API 试用行和可用时段列。 |
| 2026-05-31 | B-5 | 已完成 | 补齐类型范围、重复检测、异常波动 3 项校验；新增交叉验证审计字段并接入同步质量门禁。 |

---

## 全方案审查结论（2026-05-31）

### 物美价廉原则落实

> **核心原则**：竞彩官方数据是赔率和玩法状态主体；免费补充源可并行调用并交叉验证，但不得覆盖竞彩原值或替代竞彩未开售玩法。成本只能影响补充源选型，不能降低竞彩五玩法的强制要求。

| 数据源 | 费用 | 用途 | 速率 | 世界杯 | 五大联赛 |
|--------|------|------|------|--------|----------|
| 中国竞彩足球（Sporttery） | 官方公开数据 | 五玩法销售状态和赔率，Priority 1 | 按官方链路限制 | 以实际开售为准 | 以实际开售为准 |
| 本地 DuckDB 缓存 | 0 元 | 2.6 万场历史数据，兜底 | — | 有历史 | 有历史 |
| football-data.org 免费 | 0 元 | 赛程/比分/积分榜 | 10次/分钟 | ✅ | ✅ |
| API-Football 免费 | 0 元 | 统计/伤停/阵容及辅助赔率核验 | 100次/天 | ✅ | ✅ |
| TheSportsDB 免费 | 0 元 | 国家队阵容/历史数据 | 免费 API | ✅ | ✅ |
| SearXNG 自建 | 0 元 | 新闻搜索 → ChromaDB | 无限制 | ✅ | ✅ |
| Sportmonks 试用 | 0 元（14天） | 日期赛程 + xG/Pressure Index | 试用期 | ✅ | 日期补充 |
| iSports API 试用 | 0 元（15天） | 日期赛程 + 亚洲盘口/水位 | 试用期 | ✅ | 日期补充 |

> **淘汰的付费源**（太贵，不符合免费为主原则）：Sportmonks（€129/月）、Enetpulse（企业报价）、iSports API（价格未知）、Stats Perform/Sportradar（企业级）、worldcupapi.com（€499/月）

### C-3 修正详情

**原方案缺陷**：默认监听 `0.0.0.0`，本机所有网卡可访问，暴露公网风险。

**修正**：
- 默认值 `127.0.0.1`
- 仅当用户显式 `set FOOTBALL_HOST=0.0.0.0` 时才开放
- Docker 访问提示改为条件显示

### D-3 修正详情

**原方案缺陷**（3 处）：
1. 删除了 `age > 30min` — 破坏 30 分钟新鲜度铁律
2. 用 `missing_context` 替代 `derived_quality` — 放行低阶数据
3. 无分层标记 — 无法区分数据质量

**修正后的 `_with_historical_data_quality()`**：

```
no_bet_data_quality_flag =
    missing_team_stats
    OR derived_team_stats_quality_flag    ← 保留
    OR missing_1x2_odds_flag
    OR data_age_minutes < 0               ← 未来数据穿越
    OR data_age_minutes > 30              ← 保留，锚点已改为 kickoff_time

backtest_data_quality_tier =
    "basic_only" | "full"                 ← 新增分层
```

**锚点对比**：

| 路径 | 修复前 | 修复后 |
|------|--------|--------|
| 实时预测 (NoBetPolicy) | datetime.now() | 不变 |
| 回测 (_with_historical_data_quality) | kickoff_time - updated_at（但 age 检查被删除） | kickoff_time - updated_at（age 检查保留） |

**门禁完整性验证**：

| 条件 | 实时路径 | 回测路径 | 30min 铁律 |
|------|---------|---------|-----------|
| `missing_team_stats` | 视图 SQL | 重算 | — |
| `derived_quality` | 视图 SQL | 重算 | — |
| `missing_1x2` | 视图 SQL | 重算 | — |
| `age < 0` | NoBetPolicy | 重算 | — |
| `age > 30min` | NoBetPolicy (anchor=now) | 重算 (anchor=kickoff_time) | 保留 |
