# 系统架构

## 当前推荐架构

```text
OpenWebUI
  |
  v
FastAPI 编排服务
  |
  +--> AdvisorConfig
  |      +--> DuckDB / ChromaDB / LLM / Search / Sync 参数
  |
  +--> DataSyncCoordinator
  |      +--> 结构化数据同步 -> DuckDB
  |      +--> 新闻文本同步 -> ChromaDB
  |
  +--> query_match_stats -> DuckDB
  |
  +--> search_team_news -> ChromaDB
  |
  +--> DuckDBFeatureBuilder
  |      +--> core.view_llm_match_prediction_base
  |      +--> MatchFeatures
  |
  +--> Feature Engineering
  |
  +--> ProbabilityEngine
  |      +--> Poisson 比分分布
  |      +--> Elo 主客强弱
  |      +--> 近期状态修正
  |
  +--> OddsValueEngine
  |      +--> 赔率隐含概率
  |      +--> 返还率归一化
  |      +--> 模型概率价值差
  |      +--> 1X2 / 大小球 / +/-0.5 让球 / 竞彩五玩法价值评估
  |
  +--> NoBetPolicy
  |      +--> 数据时效
  |      +--> 未来时间戳穿越
  |      +--> 同步状态与新闻信号缺失
  |      +--> 价值阈值
  |      +--> 风险等级
  |      +--> DuckDB 数据质量旗标
  |
  +--> LLMRouter
         +--> 外部 OpenAI-compatible LLM
         +--> Ollama Qwen2.5-7B backup
```

## 正式运行入口

- OpenWebUI 是唯一正式用户前端，负责自然语言交互与报告展示。
- FastAPI 是唯一业务后台，负责预测编排、同步、查询、搜索、入库、No Bet 和 LLM 路由。
- Codex、Antigravity、Trae、豆包等对话/IDE 工具仅作为开发、调试、回测、手动同步和运维入口，可以调用 FastAPI、CLI、MCP 或 OpenAPI 工具，但不作为正式 GUI，也不绕过后台流程直接生成分析。
- 第一阶段运行形态是本地 Python 服务和脚本，不优先打包 EXE；稳定后可增加 Windows 启动脚本和计划任务。

```text
用户
  -> OpenWebUI
  -> FastAPI /predict 或 /sync/*
  -> Football Advisor pipeline
  -> DuckDB + ChromaDB + LLMRouter
  -> OpenWebUI 返回报告

开发/运维
  -> Codex / Antigravity / Trae / 豆包
  -> FastAPI / football_advisor CLI / MCP / OpenAPI 工具
  -> 同一套后台流程
```

多对话工具不得直接访问 DuckDB 或 ChromaDB，不得让各自内置 LLM 绕过 `ProbabilityEngine`、`OddsValueEngine`、`NoBetPolicy` 和 `LLMRouter` 生成正式报告。所有入口必须复用同一后台 API、CLI 或工具层。

## 配置层

- `football_advisor.config` 是当前统一配置入口，负责集中管理 DuckDB、ChromaDB、LLM、搜索与同步 provider 参数。
- `PredictionPipeline` 默认通过 `load_config()` 构建 `DataSyncCoordinator`、`ChromaNewsSearchTool`、`DuckDBStatsQueryTool` 和 `LLMRouter`，测试或工具调用可显式传入 `AdvisorConfig` 覆盖默认配置。
- 当前配置层只负责参数集中管理，不自动触发外部 provider 调用；真实 SearchRouter、外部体育 API adapter 和新闻入库闭环仍需单独实现。
- 现有环境变量前缀统一为 `FOOTBALL_`，包括 `FOOTBALL_DUCKDB_PATH`、`FOOTBALL_CHROMA_*`、`FOOTBALL_EXTERNAL_LLM_*`、`FOOTBALL_SEARXNG_BASE_URL`、`FOOTBALL_SERPER_API_KEY` 和同步 source 名称；`FOOTBALL_GOOGLE_SEARCH_API_KEY` 仅作为 Serper key 兼容入口保留。
- ChromaDB embedding 默认配置为 `FOOTBALL_CHROMA_EMBEDDING_MODEL=BAAI/bge-m3`，`FOOTBALL_CHROMA_EMBEDDING_MODEL_VERSION` 默认同模型名；collection 创建时显式写入 `hnsw:space=cosine`、embedding 模型名和版本元数据。

## 搜索与文本同步架构

新闻、战术、舆情等非结构化文本采用混合搜索入口：

```text
SearchRouter
  +--> SearXNG 本地搜索（默认优先）
  +--> Serper.dev Google Search API（备用/补充）
  |
  v
网页正文抓取
  -> 清洗
  -> 去重 content_hash
  -> 切块
  -> bge-m3 embedding
  -> ChromaDB football_advisor_news collection
  -> 结构化摘要写入 DuckDB core.fact_news_signal_summary
```

搜索结果不能直接作为报告事实。必须先经过抓取、清洗、去重、入库、检索和证据引用。SearXNG 与 Serper 只负责发现公开网页和新闻入口，不负责概率、赔率价值判断或 No Bet。

`NewsIngester` 与 `ChromaNewsSearchTool` 打开 ChromaDB collection 时必须复用同一套 bge-m3 embedding 配置，避免入库和检索使用不同向量空间。新闻 chunk metadata 同步记录 embedding 模型名与版本，便于后续距离阈值校准和 collection 迁移。

数据源优先级：

```text
StructuredDataRouter
  -> DuckDB 新鲜缓存
  -> 中国竞彩网官方公开 API/页面：竞彩足球固定奖金/赔率（主赔率源）
  -> 本地 CSV/历史数据
  -> 免费结构化源：TheSportsDB、football-data.org
  -> 低成本结构化 API：API-Football
  -> 试用日期源：Sportmonks、iSports API
  -> 人工确认
  -> No Bet / 数据不足

TextDataRouter
  -> ChromaDB 新鲜缓存
  -> SearXNG 本地搜索
  -> Serper.dev Google Search API
  -> 网页抓取、清洗、去重、embedding 入库
  -> 文本信号抽取与摘要沉淀
```

### SearchRouter 预览入口

`football_advisor.search_router.SearchRouter` 是当前搜索链路的最小落地入口，只负责公开网页搜索预览与搜索质量验证。

- FastAPI 暴露 `POST /search/preview`。
- 默认优先使用 `FOOTBALL_SEARXNG_BASE_URL` 指向的 SearXNG JSON 搜索。
- 当 SearXNG 调用失败或正文抓取无有效文本且已配置 `FOOTBALL_SERPER_API_KEY` 时，回退到 Serper.dev Google Search API；当前兼容 `FOOTBALL_GOOGLE_SEARCH_API_KEY` 作为 Serper key。
- 返回结果统一包含标题、URL、摘要、provider 与可选 score，并显式标记 `ingested=False`。
- 该入口不抓取网页正文、不清洗、不去重、不写入 ChromaDB、不写入 DuckDB，也不进入正式预测报告事实链路。
- 未配置搜索 provider 时返回 `skipped_no_provider_configured`，不得由 LLM 或占位数据补齐。

### TextFetcher 抓取预览入口

`football_advisor.text_fetcher.TextFetcher` 是搜索结果进入后续文本同步前的最小抓取预览层。

- FastAPI 暴露 `POST /search/fetch-preview`。
- 该入口接收 URL 列表，抓取 HTML，执行基础正文清洗，并计算清洗后正文的 `sha256` 内容 hash。
- 同批抓取结果按内容 hash 去重，避免重复网页进入后续候选集合。
- 仅允许 `http` 与 `https` URL；本地文件和其他 scheme 会返回 `unsupported_url_scheme`。
- 返回结果显式标记 `ingested=False`。
- 该入口不写入 ChromaDB、不写入 DuckDB、不执行 embedding、不做 LLM 摘要抽取，也不进入正式预测报告事实链路。
- 后续入库闭环必须继续保持边界：ChromaDB 存正文和向量，DuckDB 只存结构化新闻摘要与质量字段。

免费搜索适合新闻、战术、舆情和公开来源发现。赔率、赛程、比分、伤停、阵容等关键结构化字段必须走 StructuredDataRouter；搜索结果只能作为线索或辅助证据，不能作为唯一事实来源。若结构化源无法确认，必须进入 No Bet/数据不足状态，不得由 LLM 猜测补齐。

## 物美价廉数据策略

默认成本策略：

```text
0 元基础层
  -> DuckDB/ChromaDB 本地缓存
  -> 本地 CSV/历史数据
  -> TheSportsDB 免费能力
  -> football-data.org 免费能力
  -> SearXNG 自建搜索
  -> Serper.dev Google Search API
  -> 本地 bge-m3 / bge-reranker

低成本增强层
  -> API-Football Pro 等低成本结构化 API
  -> 外部 OpenAI-compatible LLM 仅用于最终报告或必要的结构化文本抽取
```

付费调用必须按需触发：

- 本地缓存 30 分钟内有效时不调用外部源。
- 免费结构化源满足赛程/比分/球队基础信息时不调用付费 API。
- API-Football 等付费源优先只补赔率、伤停、阵容、关键统计等高价值字段。
- Serper 搜索只在 SearXNG 结果不足或质量低时调用。
- 外部 LLM 只在最终报告或必要的结构化文本抽取时调用；相同比赛短时间内复用结果。

## 模型调用边界

- 外部 OpenAI-compatible LLM API 是可选增强项，不是本地最小闭环的强依赖。
- 未配置外部 OpenAI-compatible LLM API 时，Ollama `qwen2.5:7b` 可作为自动报告生成引擎。
- 已配置外部 OpenAI-compatible LLM API 时，外部 LLM 优先用于报告生成，Ollama `qwen2.5:7b` 作为失败后的备用引擎。
- Codex 不作为生产运行时 LLM 接口，不被 FastAPI 自动调用来生成报告。
- 固定 Markdown 模板是 LLM 全部失败时的兜底输出。
- LLM 不得直接生成或改写概率，不得替代概率模型、赔率价值引擎、DuckDB/ChromaDB 查询或 No Bet 判断。

## 数据质量门禁

数据进入预测前必须经过四层质量判断：

```text
1. 算法硬校验
   -> 字段完整性
   -> 类型和值域
   -> 30 分钟新鲜度
   -> 未来数据穿越
   -> 实体映射
   -> 去重
   -> 来源可信度
   -> 多源一致性
   -> 异常波动

2. 本地向量/重排初筛
   -> bge-m3 判断文本相关性
   -> bge-reranker-v2-m3 重排搜索和新闻结果
   -> 噪声文本不得入库为有效证据

3. 外部 LLM 结构化抽取
   -> 抽取伤停、战术、舆情、首发倾向等信号
   -> 写入 generated_by、source_text_hash、confidence、requires_cross_check
   -> 结果为模型抽取信号，不直接覆盖事实数据

4. No Bet 最终门禁
   -> 规则校验、来源可信度、多源一致性、时间新鲜度共同决定 quality flags
   -> 质量不足时进入 No Bet 或数据不足状态
```

## 数据调研与准入治理

所有数据源和特征在生产中的用途由“调研证据 + 用户审批”决定，不由AI或单次实现任务自行决定。

```text
候选数据登记
-> 数据源与API/搜索能力调研
-> 覆盖率、时效性、实体映射和成本审计
-> 防未来穿越检查
-> 基线、单特征、组合和消融实验
-> 时间外滚动回测与概率校准
-> 分赛事稳定性和故障影响评估
-> 生成用户审阅报告
-> 用户批准用途或决定延期/弃用
-> 固化数据准入清单、模型版本和权重
```

候选数据登记至少包括：Elo、动态Elo、近期战绩、进失球、xG、射门质量、主客场和中立场、阵容、首发、阵型、伤停、停赛、球员重要性、休息天数、旅行距离、历史交锋、积分榜、赛事阶段、出线动机、天气、场地、海拔、裁判、新闻、发布会、战术、舆情、竞彩五玩法和赔率变化、第三方赔率、资金流、球员名单、俱乐部表现、教练变化及历史比赛事件衍生指标。

每项数据只能处于以下状态之一：未调研、调研中、报告待审核、批准进入概率、批准进入No Bet或风险、批准进入报告、批准进入离线校准、用户决定延期、用户决定弃用。

调研报告未获用户批准时：

- 不得永久删除采集或映射能力。
- 不得宣称该数据无价值。
- 不得把临时经验权重标记为正式模型参数。
- 可以出于安全原因暂不进入在线概率，但必须记录为“待调研/待决定”，并保持 No Bet 边界。

## 预测标准和权重发布

预测标准必须同时具备业务依据和统计依据：

- 业务依据：竞彩玩法规则、No Bet安全规则、数据新鲜度和LLM边界。
- 数据依据：数据质量、覆盖率、映射率和来源可靠性。
- 模型依据：时间外回测、Brier Score、LogLoss、校准曲线、基线改进和消融实验。
- 稳定性依据：国家队/俱乐部、赛事阶段、主客场/中立场和不同时间窗口上的表现。
- 运维依据：API费用、延迟、限流、失败率和缓存能力。
- 决策依据：用户对调研报告和推荐用途的明确批准。

现有 `poisson_weight=0.72`、Elo反向权重、状态修正幅度、主场Elo加成、半场比例、5%价值阈值和15%市场偏差阈值均属于待调研基线参数。在专项调研、回测、校准和用户批准前，不得将其描述为正式预测标准，且必须维持未校准 No Bet。

本地 `qwen2.5:7b` 仅作为外部报告模型失败后的备用生成引擎，不参与新闻压缩、重排或数据质量裁决。外部 LLM 也不能绕过规则硬校验和 No Bet，只能辅助文本抽取、解释和报告生成。

## 已落地代码模块

- `football_advisor/models.py`：核心数据结构。
- `football_advisor/db_schema.py`：DuckDB `core` schema、物理表与三层宽表视图 DDL。
- `football_advisor/feature_builder.py`：从 DuckDB 宽表读取真实结构化特征并构造 `MatchFeatures`。
- `football_advisor/sqlite_importer.py`：从 `database.sqlite` 确定性导入联赛、球队、球员、比赛、H2H 上下文、prior-only rolling stats 和 1X2 赔率。
- `football_advisor/worldcup_basic_importer.py`：从本地世界杯赛程 CSV 和最近 5 场球队统计 CSV 导入确认小组赛、国家队基础实体和静态近期状态快照；淘汰赛占位不入库，静态快照以 `WORLD_CUP_LAST5_SNAPSHOT` 质量旗标进入 No Bet 门禁。
- `football_advisor/pdf_text_extractor.py`：通用 PDF 文本抽取工具，供裁判、名单、赛制、公开资料等 PDF 做文本化预处理；默认只返回文本、页数、元数据和主题摘要，不直接写入 DuckDB 或 ChromaDB。
- `football_advisor/probability_engine.py`：Poisson + Elo + 近期状态概率模型，并输出比分、总进球和半全场概率桶；阵型、H2H 和疲劳增强默认不改变概率，只有校准批准后才可显式开启。
- `football_advisor/odds_value_engine.py`：1X2、大小球、+/-0.5 让球及竞彩 SPF/RQSPF/比分/总进球/半全场赔率隐含概率与价值差。
- `football_advisor/no_bet_policy.py`：No Bet 与风险拦截，包括模型未校准、过期数据、未来时间戳、同步失败/跳过和新闻信号缺失；在全部已开售竞彩玩法中选择最大有效价值差。
- `football_advisor/backtest_engine.py`：历史回测与未来数据穿越检查。
- `football_advisor/calibration_engine.py`：Brier Score、LogLoss、命中率。
- `football_advisor/query_tools.py`：DuckDB SELECT-only 查询工具。
- `football_advisor/news_tools.py`：ChromaDB 新闻语义检索边界。
- `football_advisor/sync.py`：同步前置边界。
- `football_advisor/sporttery_client.py`：中国竞彩网官方公开 API/页面竞彩足球固定奖金/赔率客户端，写入 staging 后经 provider mapping 合并到 DuckDB core。
- `football_advisor/football_data_client.py`：football-data.org 赛程/比分同步客户端，经过 provider mapping 后合并到 core。
- `football_advisor/api_football_client.py`：API-Football 赔率、统计、伤停、阵容同步客户端。
- `football_advisor/thesportsdb_client.py`：TheSportsDB 阵容、历史、联赛/球队/球员 ID 查询客户端。
- `football_advisor/sportmonks_client.py`：Sportmonks 按日期赛程试用源客户端，仅落 staging。
- `football_advisor/isports_client.py`：iSports API 按日期赛程试用源客户端，仅落 staging。
- `football_advisor/multi_source_coordinator.py`：8 源并行调度与独立失败隔离。
- `football_advisor/llm_router.py`：外部 LLM 与本地 Qwen 备用路由。
- `football_advisor/report.py`：固定 Markdown 报告模板与外部 LLM 输出事实片段校验。
- `football_advisor/pipeline.py`：预测流程编排，并将同步状态和新闻信号运行时质量写入 No Bet 上下文。
- `football_advisor/api.py`：FastAPI `/health`、`/predict` 和管理接口；抓取预览仅在显式启用且管理令牌校验通过时注册和执行。
- `scripts/smoke_test_multi_source_sync.py`：provider mapping 后多源同步 staging→core 写库烟测。
- `scripts/smoke_test_provider_apis.py`：8 源 API/公开页面脱敏连通性烟测。
- `scripts/smoke_test_worldcup_readiness.py`：统一赛前就绪门禁；支持 `--league` 和 `--match-id`，目标球队必须存在 `SportteryOfficialWeb` 映射。

## DuckDB 标准 schema

结构化数据统一落在 DuckDB `core` schema，当前 DDL 由 `football_advisor/db_schema.py` 管理。

维度表：

- `core.dim_league_mapping`：联赛统一 ID 与标准名称。
- `core.dim_team_mapping`：球队统一 ID、跨数据源 ID、Elo 基准。
- `core.dim_provider_league_mapping`：外部 provider 联赛 ID 到系统联赛 ID 的显式映射。
- `core.dim_provider_team_mapping`：外部 provider 球队 ID 到系统球队 ID 的显式映射。
- `core.dim_player_mapping`：球员统一 ID、跨数据源 ID、位置。
- `core.dim_referee_profile`：裁判执法尺度与方差画像。

事实表：

- `core.fact_match_schedule`：比赛时间轴锚点、主客队、状态、天气、比分与入库时间。
- `core.fact_team_rolling_stats`：球队滚动 xG、xGA、PPDA、近期积分、攻防强度、伤停折损。
- `core.fact_odds_capital_flow`：1X2、让球、大小球、开盘/最新盘口、水位、资金流与交易量。
- `core.fact_match_context_summary`：历史交锋、近期状态、主客场表现、核心缺阵与阵容状态摘要。
- `core.fact_news_signal_summary`：从 ChromaDB/新闻流程沉淀出的结构化新闻舆情摘要，不存新闻正文。
- `core.fact_match_events_unified`：事件级坐标与 xG/xT 等高密度结构化事件数据。
- `core.fact_league_standings`：按标准联赛、标准球队和赛季保存积分榜事实；在线增强只允许读取与目标比赛联赛、赛季完全一致的记录。

在线特征增强边界：

- 阵容和伤停必须同时通过 provider 比赛映射、provider 球队映射、标准球员映射，并且采集时间不超过 30 分钟，才可进入报告和风险上下文。
- 阵容、伤停、阵型和积分榜在专项调研与用户批准前不得修改 xG 或比赛概率；该限制是临时安全状态，不代表永久排除。
- `core.fact_match_events_unified`、价值评估明细和球员详细事件在专项调研与用户批准前仅用于离线分析与校准；历史滚动衍生指标是否进入在线预测必须经过同一准入流程。

宽表视图分三层：

- `core.view_match_feature_base`：比赛、球队、近期状态、H2H、伤停、天气、裁判。
- `core.view_market_feature_base`：1X2、让球、大小球、开盘/最新赔率、赔率变化、资金流。
- `core.view_llm_match_prediction_base`：面向 `DuckDBFeatureBuilder` 和报告生成的清洗后摘要视图。

宽表必须暴露数据质量字段：

- `critical_data_age_minutes`
- `missing_1x2_odds_flag`
- `missing_team_stats_flag`
- `missing_context_flag`
- `no_bet_data_quality_flag`

`fact_team_rolling_stats` 的视图读取必须满足：

```sql
record_date <= match_time - INTERVAL 30 MINUTE
```

以防止回测或预测读取未来数据。

## 关键边界

- DuckDB 不存新闻正文或舆情向量。
- ChromaDB 不存结构化比赛统计或赔率表格。
- 查询工具不能内嵌同步逻辑。
- 同步工具不能代替查询工具。
- 报告生成不能覆盖或修改模型概率。
- 外部 LLM 输出必须通过报告事实片段校验；未通过时返回固定模板，不得直接返回未校验文本。
- No Bet 是强制安全门，不是可选提示。
- 概率模型未通过样本量、绝对 Brier Score 和相对基线改进三重门禁时，允许保留诊断概率，但必须强制 No Bet。
- LLM 只读取 `core.view_llm_match_prediction_base` 的结构化摘要；复杂 JOIN、赔率变化与数据质量判断由 DuckDB 视图封装。
- `no_bet_data_quality_flag` 必须进入 `NoBetPolicy`，不能只作为报告展示字段。
- PDF staging 写入 core 后不得存在同名球员跨多个国家队的记录；世界杯赛前门禁必须把 `PDF_%` 球员跨队污染作为硬失败项。
- 结构化核心源同步失败、新闻信号缺失或新闻信号过期必须触发 No Bet，不能由 LLM 猜测补齐。
- 未配置真实交易所客户端时，资金流同步必须跳过并作为风险维度缺失写入报告，不单独触发同步质量门禁；已配置资金流 provider 但同步失败时仍进入同步质量风险。
- 默认预测链路不得写入模拟盘口或模拟资金流。
- 外部 provider 数据进入 `core.*` 前必须经过 `core.dim_provider_*_mapping`，不得假设 provider id 与系统 id 完全一致。

## 生产能力状态

- 中国竞彩网官方 `getMatchCalculatorV1.qry` 已接入；四场目标世界杯比赛已完成官方场次、中文队名和标准实体对账。其他比赛仍必须通过显式映射维护，不得按名称猜测。
- football-data.org、API-Football、TheSportsDB 已完成脱敏烟测和 provider mapping 后的 staging/core 写入链路；具体端点可用性仍受订阅层限制。
- Sportmonks 与 iSports API 保持可选，只有本地配置真实 token 后才启用。
- 新闻链路通过 SearXNG/Serper、正文抓取、ChromaDB 和 DuckDB 结构化摘要闭环；未配置或数据过期时进入 No Bet。
- DuckDB 标准 schema、启动迁移和真实库副本迁移验收已完成；预测入口统一通过 `PredictionPipeline` 初始化数据库。
- `PostMatchVerifier`、`BetLedger` 和 `FeedbackEngine` 只由后台维护任务按需构造，不属于在线预测初始化。
- ChromaDB 文本入库与 embedding 配置。
- OpenWebUI 到 FastAPI 的正式接入方式。
- 定时任务与同步日志。
- 一个完整赛季以上的滚动回测。

## 系统不变量与边界保障（2026-06-13 闭环）

### 竞彩赔率隔离

- 所有竞彩五玩法（SPF/RQSPF/CRS/TTG/HAFU）的赔率数据**必须**来自 `SportteryOfficialWeb` 单一 provider。
- 视图 `view_market_feature_base` 包含独立的 `sporttery_ranked` CTE，在竞彩数据过滤后按 `snapshot_time` 排序取最新一条，确保第三方赔率不会伪装为竞彩赔率。
- 第三方赔率仅通过 `cross_check_1x2` CTE 做交叉校验，不进入概率/价值决策主链路。

### 预测日志语义分离

- `model_predicted_outcome`：模型概率最大项，用于赛后核验准确率。
- `value_candidate_outcome`：赔率价值最高但不一定通过策略门禁的候选，用于价值分析。
- `predicted_outcome`（旧列）：保留兼容，不用于核验。
- `PostMatchVerifier._verify_single` 使用 `model_predicted_outcome` 做核验，No Bet 时跳过盈亏计算。

### UTC 时间语义统一

- `time_utils.py`：`utc_now()` 统一获取当前 UTC 时间；`assert_utc()` 强制校验时区；`FutureTimestampError` 拒绝未来时间戳。
- `DataReadinessGate.check()`：未来时间戳标记为 `invalid_future`，不进入同步流程。

### 中立场主场优势清零

- `fact_match_schedule.is_neutral_venue` 字段标记中立场。
- `feature_builder._home_advantage()` 检测中立场时返回 0.0，不施加主场优势加成。

### 组件证据就绪

- `DataReadinessGate.load()` 移除所有 COALESCE 兜底。阵容/伤停/新闻的 `updated_at` 必须由真实组件写入，不得以其他字段伪造就绪。

### LLM 叙述与事实分离

- LLM 只输出 JSON 三字段叙述（`key_factors`、`main_risks`、`reasoning_summary`），所有数字（概率、赔率、比分）由 `ReportBuilder.build_markdown()` 代码渲染。
- `ReportNarrative.validate_narrative()` 校验叙述不包含虚假数字、虚假球队、禁止用语或与 No Bet 冲突的建议。
- 报告生成链路：LLM → JSON 解析 → `ReportNarrative` → `build_markdown(bundle, narrative)` → 最终报告。

### 30 分钟缓存与去重

- `core.fact_target_sync_state` 表按 `(match_id, component, provider)` 主键存储同步状态。
- `TargetMatchSyncService._sync_with_cache()`：检查缓存（30 min TTL）→ 失败抑制（recent_failure 不重试）→ 执行同步 → 写入状态。
- `MultiSourceCoordinator.fetch_all(exclude_providers=...)` 支持显式排除 provider，竞彩已由 `_sync_sporttery()` 处理后跳过。
- 单次预测内 `_attempted` 集合去重，同一组件最多执行一次。

### 赔率趋势玩法隔离

- `OddsTrendEnhancer._query_odds_trend()` 限定条件：`odds_type='1X2'`、`source_provider='SportteryOfficialWeb'`、`bookmaker_name='SportteryOfficialWeb'`。
- 无可比序列时设置 `odds_trend_status = "insufficient_comparable_snapshots"`。

### 验收脚本

- `scripts/walkthrough_e2e.py`：四场世界杯 PRODUCTION_READY/SAFE_DEGRADED/EXTERNAL_BLOCKED/FAILED 四种互斥结论，支持 fixture/live/cache 三种模式，输出 JSON 证据。
- `scripts/finalize_batch.py`：批次 evidence 的唯一自动生成入口，读取批次 manifest，运行验收命令，采集当前 Git 改动、HEAD、进度记录和 CodeRabbit 原始产物，并写入 `docs/verification/batches/batch-N-evidence.json`。首次生成时也会把该 evidence 文件自身列入 `changed_files`，保证提交后 diff 与 evidence 对齐。关键字段不允许由人工或其它 AI 手写。
- `scripts/verify_batch_completion.py`：开发批次完成门禁，只接受 `scripts/finalize_batch.py` 生成的 evidence，校验批次 evidence/review、命令结果、外部复审策略、`PROGRESS.md` 更新、独立提交和提交后工作区状态。CodeRabbit 采用风险触发式硬门槛：高风险批次、阶段收口和合并前必须成功；低风险文档/证据/测试补丁批次可设置 `external_review_required=false`，但仍必须记录外部复审尝试结果或未执行理由，并通过本地硬门禁。post-commit 模式会现场读取 `HEAD^..HEAD` 实际提交改动判定高风险路径，防止空 `changed_files` 或手写 evidence 绕过复审；普通批次遇到 root commit 失败关闭；工作区必须完全干净，`batch-N-evidence.json` 自身未提交也会阻断。验收器同时校验 evidence 的 `commit` 锚点、代码改动必须列出 `required_artifacts`、高风险外部复审必须提供 `coderabbit.artifact` 原始产物，且会拦截 CodeRabbit 字段写成功但说明文字承认无法运行、超时、失败或超过限制的自相矛盾证据。该脚本不属于在线预测运行时，不进入 OpenWebUI/FastAPI 预测链路，只用于防止分批修复时遗漏证据或提前进入下一批。
- `scripts/enforce_batch_gate.py`：Git hook 和 CI 的统一入口，负责解析批次编号并调用 `verify_batch_completion.py`。本地通过 `.githooks/pre-commit` 执行 pre-commit 门禁，通过 `.githooks/pre-push` 执行 post-commit 门禁；远端通过 `.github/workflows/batch-gate.yml` 执行 post-commit 门禁和全量测试。CI checkout 必须使用完整历史，保证 post-commit 门禁可以读取 `HEAD^..HEAD`。该层仍是开发验收治理，不进入预测运行时。
- `scripts/audit_batch_acceptance.py`：只读验收审计入口，供验收角色判断其它 AI 的原始交付是否达到接收标准。该脚本只读取 `git status`、提交数量和 post-commit 门禁结果，不执行 `git add`、`git commit` 或批次关闭动作，避免验收者替执行者补救失败状态。
- `scripts/close_batch.py`：批次关闭统一编排入口，负责串联 `finalize_batch.py`、pre-commit 验证、独立提交、post-commit 验证和最终工作区洁净检查。该脚本同样只属于开发验收治理层，不进入 OpenWebUI/FastAPI 预测运行时；不得把手工逐条执行命令作为正式批次闭环依据。
