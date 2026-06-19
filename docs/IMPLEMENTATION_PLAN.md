# 足球预测系统实施计划

> 更新时间：2026-05-24

## 目标

把当前原型推进到可验证的本地生产最小闭环：真实 DuckDB schema 可初始化、现有 CSV 可确定性导入、预测链路可从真实结构化数据生成特征、No Bet 能拦截数据质量风险，并能用历史样本完成 walk-forward 回测。

## 最佳方案结论

当前不推翻既有架构。继续保持 OpenWebUI -> FastAPI 编排服务 -> DuckDB/ChromaDB 双库隔离 -> 概率模型 -> 赔率价值引擎 -> No Bet -> LLM 报告 的固定流程。

正式运行形态采用“OpenWebUI 唯一正式前端 + FastAPI 后台服务 + Codex 运维/开发操作入口”的组合：

- OpenWebUI 是唯一面向日常用户的 GUI，不再额外开发桌面 GUI 或独立网页前端。
- FastAPI 是唯一业务编排后台，负责预测、同步、搜索、入库、模型路由和审计日志。
- Codex、Antigravity、Trae、豆包等对话/IDE 工具可以作为开发、调试、回测、手动同步和维护入口，但不作为正式用户前端，不绕过后台流程直接生成投注分析。
- 多对话工具统一通过 FastAPI、CLI 或 MCP/OpenAPI 工具调用同一套后台能力；不为每个对话工具复制一套业务逻辑。
- 本地运行优先采用 Python 服务和脚本形态，而不是第一阶段打包 EXE；稳定后可增加 Windows 启动脚本和计划任务。

多对话工具接入边界：

```text
OpenWebUI
  -> 正式用户 GUI

Codex / Antigravity / Trae / 豆包
  -> 开发、运维、调试、手动同步、回测入口
  -> 通过 FastAPI / CLI / MCP / OpenAPI 调用后台
  -> 不直接访问 DuckDB/ChromaDB
  -> 不直接让 LLM 生成正式投注分析
```

其中 Antigravity 和 Trae 优先按 MCP/OpenAPI 工具接入；Codex 优先按 CLI 或本地 FastAPI 调用；豆包若当前环境支持自定义工具、OpenAPI 或 MCP，则接入同一后台接口，否则仅作为人工交互入口，不承担自动数据库更新职责。

正式数据更新与搜索方案采用“自建 SearXNG + Google Programmable Search JSON API 备用”的混合搜索架构：

```text
OpenWebUI
  -> Football Advisor FastAPI
  -> SearchRouter
       -> SearXNG 本地搜索优先
       -> Google Programmable Search JSON API 兜底/补充
  -> 网页正文抓取与清洗
  -> 去重、切块、bge-m3 embedding
  -> ChromaDB football_advisor_news collection
  -> 新闻/战术/舆情结构化摘要沉淀到 DuckDB
```

搜索和爬取只作为新闻、战术、舆情等非结构化数据的来源补充；赔率、赛程、比分、球队统计等结构化数据仍必须进入 DuckDB。搜索结果不能直接当作事实写入报告，必须经过抓取、清洗、去重、入库、检索和证据引用。

数据源调用顺序采用分层策略：

```text
1. 本地缓存优先
   -> DuckDB / ChromaDB 中 30 分钟内的可用数据直接使用

2. 结构化数据优先走低成本结构化链路
   -> 本地 CSV/历史数据
   -> TheSportsDB
   -> football-data.org
   -> API-Football 等低成本付费 API
   -> 人工确认或 No Bet

3. 文本数据优先走免费搜索链路
   -> ChromaDB 缓存
   -> SearXNG 搜索公开网页
   -> 结果不足或质量低时补充 Google Programmable Search JSON API
   -> 抓取、清洗、去重、embedding 入库

4. 仍无法满足质量要求
   -> 不由 LLM 猜测
   -> 写入明确失败状态
   -> 预测进入 No Bet 或数据不足状态
```

因此“先用免费搜索找，没有再调用 API”只适用于新闻、战术、舆情、发布会等文本数据。对赔率、赛程、比分、伤停、阵容、球队统计等会影响概率和 No Bet 的关键结构化字段，搜索结果只能作为线索或辅助证据；主事实必须来自 DuckDB、CSV/历史数据、免费结构化源、低成本结构化 API 或人工确认。

物美价廉的推荐配置：

```text
0 元基础层
  -> 本地 CSV/历史数据
  -> DuckDB / ChromaDB 缓存
  -> TheSportsDB 免费能力
  -> football-data.org 免费能力
  -> SearXNG 自建搜索
  -> Google Programmable Search JSON API 免费额度
  -> bge-m3 / bge-reranker-v2-m3 / qwen2.5:7b 本地模型

低成本增强层
  -> API-Football Pro 等低成本结构化 API，用于赔率、伤停、阵容、关键统计补强
  -> 外部 OpenAI-compatible LLM，仅用于最终报告或必要的结构化文本抽取
```

不同数据类型的默认来源：

| 数据类型 | 首选 | 备用 | 搜索角色 |
| --- | --- | --- | --- |
| 赛程 | football-data.org / TheSportsDB / 本地 CSV | API-Football | 只作线索 |
| 比分 | football-data.org / TheSportsDB | API-Football | 只作线索 |
| 赔率 | API-Football / 可信赔率源 | 人工确认 | 不作唯一事实 |
| 伤停 | API-Football + 官方/新闻交叉验证 | 人工确认 | 重要辅助 |
| 阵容 | API-Football + 官方/新闻交叉验证 | 人工确认 | 重要辅助 |
| 球队状态 | DuckDB 历史滚动统计 | API-Football | 不作主源 |
| 新闻/战术/舆情 | SearXNG + ChromaDB | Google Search | 主入口 |

付费调用节流：

- 同一比赛短时间内复用同步结果。
- 免费结构化源满足基础字段时不调用付费 API。
- API-Football 优先只补关键字段，不做全量无差别拉取。
- Google 只在 SearXNG 结果不足或质量低时调用。
- 外部 LLM 只在最终报告或必要抽取时调用，失败后使用本地 Qwen 备用。

数据质量采用“四层门禁”：

```text
1. 算法硬校验
   -> 字段完整性、类型范围、时间新鲜度、未来数据穿越、实体映射、重复检测、来源可信度、多源一致性、异常波动

2. 本地向量/重排初筛
   -> bge-m3 用于文本相关性判断
   -> bge-reranker-v2-m3 用于搜索和新闻结果重排
   -> 噪声文本不得作为有效新闻证据入库

3. 外部 LLM 结构化抽取
   -> 从新闻/战术/舆情文本中抽取伤停、战术变化、首发倾向、风险信号
   -> 抽取结果必须带 generated_by、source_text_hash、confidence、requires_cross_check
   -> 抽取结果是“模型信号”，不直接覆盖事实数据

4. No Bet 最终门禁
   -> 根据规则、来源可信度、多源一致性、时间新鲜度和数据质量标记决定是否允许输出投注建议
```

本地 Qwen2.5-7B 不作为权威数据质量裁决器。它可以在明确标记为辅助的场景中参与非权威文本摘要，但不能决定事实真伪、不能覆盖结构化数据、不能降低 No Bet 门槛。外部 LLM 可以做更强的结构化抽取和解释，但同样不能绕过硬校验和 No Bet。

LLM 路由保持固定：

```text
未配置外部 OpenAI-compatible LLM API
  -> Ollama qwen2.5:7b 自动生成报告

已配置外部 OpenAI-compatible LLM API
  -> 外部 LLM 优先生成报告
  -> Ollama qwen2.5:7b 作为失败后的备用引擎

Codex
  -> 开发、调试、手动高级分析和运维入口
  -> 不作为 FastAPI 生产运行时 LLM 接口

固定 Markdown 模板
  -> 自动 LLM 全部失败时的兜底输出
```

LLM 不得直接生成或修改模型概率，不得绕过 No Bet，不得替代 DuckDB/ChromaDB 查询、赔率价值引擎或数据质量判断。

最佳落地路线是“先结构化闭环，后文本与外部实时源”：

1. 先用真实 DuckDB 执行现有 `football_advisor/db_schema.py`，修正 DDL 与视图兼容性。
2. 再建立现有 CSV 到 `core.dim_*`、`core.fact_*` 的确定性导入映射，优先覆盖赛程、球队、滚动状态、赔率、上下文摘要。
3. 用一轮或一个赛季历史样本跑通 `DuckDBFeatureBuilder` -> `ProbabilityEngine` -> `OddsValueEngine` -> `NoBetPolicy`。
4. 再接 ChromaDB 新闻入库，只把新闻正文、舆情、战术文本放入 ChromaDB，把结构化摘要写入 `core.fact_news_signal_summary`。
5. 最后接外部体育/赔率/新闻 API 和 OpenWebUI 工具入口。

## 交叉对比结论

### 与项目既定需求一致的内容

- 双库分离是正确边界：DuckDB 只存结构化数值/表格数据，ChromaDB 只存非结构化新闻舆情文本。
- LLM 只负责报告表达，不得直接生成概率或改写模型概率。
- 预测前必须同步数据，关键数据不得超过 30 分钟。
- 宽表视图投喂是正确方向，可减少 LLM 写复杂 SQL 和跨表推理幻觉。
- 多源实体映射是必要基础，尤其是球队、球员、联赛、裁判 ID。
- No Bet 必须作为强制安全门，不是提示字段。

### 需要收敛的内容

- 不建议一次性实现桌面文档中的完整 v7 架构。Markov 链、Kelly 配资、冷归档、事件级大规模 Parquet 分区都应放到真实结构化闭环之后。
- 不建议把高阶指标缺失时的推导值伪装成真实数据。可以有 `Derived_Fallback`，但必须进入数据质量标记，并默认提高风险或触发 No Bet。
- 不建议让本地 Qwen 参与 ETL 分类、实体消解裁决、概率计算或投注决策。它只保留为外部 LLM 失败后的报告生成备份。
- 不建议初期为所有字段和复合条件堆索引。DuckDB 的 ART 索引适合高选择性点查，但会增加写入/更新成本，且不加速 JOIN、聚合、排序。现阶段保留主键与必要 lookup 索引即可。

## 实施阶段

### 阶段 1：真实 DuckDB 集成验证

**目标：** 证明 `SCHEMA_SQL` 能在真实 DuckDB 引擎中执行，并且三层视图可查询。

**涉及文件：**

- `football_advisor/db_schema.py`
- `tests/test_db_schema.py`
- `requirements.txt`

**任务：**

- 安装项目依赖。
- 执行 `initialize_database("football_system.db")`。
- 新增或扩展集成测试：验证 `core` schema、核心表、三层视图存在。
- 用最小样本插入联赛、球队、比赛、球队滚动数据、赔率数据，查询 `core.view_llm_match_prediction_base`。
- 修复 DuckDB 方言兼容问题，只改必要 DDL/视图。

**验收：**

- `python -m unittest discover -s tests -v` 通过。
- 真实 DuckDB 中 `core.view_llm_match_prediction_base` 能返回一条完整样本。
- 缺赔率、缺球队滚动数据、数据超过 30 分钟时能产生数据质量标记。

### 阶段 2：CSV 到标准 schema 的确定性导入

**目标：** 把仓库现有 CSV 映射到 DuckDB 标准表，不依赖 LLM 做分类决策。

**优先数据源：**

- `competitions.csv`
- `clubs.csv`
- `games.csv`
- `club_games.csv`
- `match_stats.csv`
- `team_ratings.csv`
- `appearances.csv`
- `players.csv`
- `game_events.csv`

**涉及文件：**

- 新增：`football_advisor/csv_importer.py`
- 新增：`tests/test_csv_importer.py`
- 可能修改：`football_advisor/db_schema.py`

**任务：**

- 建立 CSV 字段到 `core.dim_league_mapping`、`core.dim_team_mapping`、`core.fact_match_schedule` 的映射。
- 由历史比赛和统计数据生成 `core.fact_team_rolling_stats`。
- 写入 `record_date <= match_time - INTERVAL 30 MINUTE` 的导入校验。
- 不可映射实体进入明确的错误清单或跳过清单，不静默吞掉。
- 为缺失 xG/PPDA 的样本写入 `data_quality_flag`，不得伪装成真实高阶数据。

**验收：**

- 至少一个联赛/赛季样本可导入。
- 导入后宽表可命中真实 `match_id`。
- 未来数据穿越样本会被拒绝或标记。

### 阶段 3：真实结构化预测闭环

**目标：** 用真实 DuckDB 特征替代占位特征，完成端到端预测和回测。

**涉及文件：**

- `football_advisor/feature_builder.py`
- `football_advisor/pipeline.py`
- `football_advisor/backtest_engine.py`
- `football_advisor/no_bet_policy.py`
- `tests/test_feature_builder.py`
- `tests/test_core.py`

**任务：**

- 用导入后的真实比赛样本执行 `DuckDBFeatureBuilder`。
- 验证 `ProbabilityEngine` 输出胜平负、比分、大小球概率。
- 验证 `OddsValueEngine` 对 1X2 赔率做除水归一和价值差。
- 验证 `NoBetPolicy` 对过期数据、缺 1X2、缺球队状态、新闻风险、裁判高方差执行拦截。
- 用历史样本跑 walk-forward 回测，禁止读取赛后结果作为赛前特征。

**验收：**

- 一条真实历史比赛可以生成完整 Markdown 报告。
- 回测按 `match_time` 顺序执行。
- 数据质量不足时输出 No Bet，而不是占位预测。

### 阶段 4：ChromaDB 新闻入库与结构化摘要沉淀

**目标：** 让新闻舆情进入 ChromaDB，但只把结构化摘要写回 DuckDB。

**涉及文件：**

- `football_advisor/news_tools.py`
- `football_advisor/sync.py`
- 新增：`football_advisor/news_ingestion.py`
- 新增：`tests/test_news_ingestion.py`

**任务：**

- 创建固定 collection，例如 `football_match_news`。
- 文档 metadata 至少包含 `match_id`、`team_id`、`source`、`published_at`、`language`。
- 使用稳定 embedding 配置；collection 创建后不改变距离度量。
- 检索结果只供新闻舆情证据使用。
- 新闻情绪、新闻风险、来源数量、最后更新时间写入 `core.fact_news_signal_summary`。

**验收：**

- `search_team_news` 只访问 ChromaDB。
- DuckDB 不存新闻正文或向量。
- `news_last_updated_at` 超过 30 分钟时触发风险或 No Bet。

### 阶段 5：同步协调与 OpenWebUI 工具接入

**目标：** 让 OpenWebUI 作为唯一前端，通过 FastAPI/OpenAPI 或 MCP 工具调用预测服务。

**涉及文件：**

- `football_advisor/api.py`
- `football_advisor/sync.py`
- `README.md`
- 新增：`docs/OPENWEBUI_INTEGRATION.md`

**任务：**

- 明确 `/predict` 的 OpenAPI schema。
- 在预测入口强制先执行 `sync_before_prediction()`。
- 同步失败或关键数据过期时返回可解释错误或 No Bet。
- 文档化 OpenWebUI 接入方式，不让用户直接操作 DuckDB 或 ChromaDB。

**验收：**

- OpenWebUI 可调用 FastAPI `/predict`。
- 工具层只暴露预测/查询边界，不暴露任意 Python 或任意 SQL 执行。
- 外部 LLM 失败后才使用本地 Qwen2.5-7B 生成报告。

### 阶段 6：外部数据 API 与生产化增强

**目标：** 在本地闭环验证通过后接入实时供应商。

**候选接入顺序：**

1. 免费/低成本基础赛程与比分源，用于验证同步接口。
2. 覆盖赔率、xG、阵容、裁判、新闻的综合供应商。
3. 事件级数据或交易所资金流，仅在回测证明增益后接入。

**任务：**

- 每个外部 provider 单独 adapter。
- 所有 adapter 输出统一 staging 结构，再进入实体映射与 `core.fact_*`。
- 每条记录保留 `source_provider`、`etl_insert_timestamp`、`_change_request_type` 或等价审计字段。
- API 失败时明确返回状态，不能用 LLM 猜测补齐。

**验收：**

- 单 provider 失败不会破坏已有本地历史回测。
- 实时预测数据新鲜度满足 30 分钟要求。
- 多源冲突可追踪到来源。

## 不进入当前实施范围

- 自动下注、资金托管、真实投注执行。
- Kelly 配资建议作为投注动作输出。
- 让 LLM 自行编写复杂 SQL、重算赔率或生成模型概率。
- 大规模事件级冷归档优化，除非真实数据量已经证明需要。
- 用本地 Qwen 做主要预测、推理或 ETL 裁决。

## 外部核对来源

- DuckDB Indexing: https://duckdb.org/docs/current/guides/performance/indexing
- DuckDB Partitioned Writes: https://duckdb.org/docs/current/data/partitioning/partitioned_writes
- Chroma Collections: https://cookbook.chromadb.dev/core/collections/
- OpenWebUI Tools: https://docs.openwebui.com/features/plugin/tools/
- Sportmonks Football API endpoints: https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints
- football-data.org API quickstart: https://www.football-data.org/documentation/quickstart
- StatsBomb Open Data: https://github.com/statsbomb/open-data
