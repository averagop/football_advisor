# 交付标准最终优化方案

## 结论

当前无法达到正式交付标准，不是概率模型本身不可用，也不是所有数据源都不可用，而是以下问题叠加：

1. 正式 OpenWebUI 入口参数不完整，调试链路比正式用户链路更精准。
2. 预测前同步后缺少宽表新鲜度复查，可能出现同步成功但预测仍读旧数据。
3. No Bet 报告仍展示“最佳价值候选”，存在用户误读为投注建议的风险。
4. 交易所资金流缺省被当成同步质量失败，当前阶段规则过严。
5. 友谊赛仍使用世界杯新闻查询词，新闻语境容易跑偏。

交付目标不是让每场比赛都输出投注建议，而是保证：

- 数据不足时稳定 No Bet。
- No Bet 报告不诱导下注。
- OpenWebUI 正式入口和内部脚本结果一致。
- 预测前同步能证明关键数据新鲜。
- 资金流、新闻、赔率、球队状态缺失时的业务规则清楚。

## 优先级零：消除误导和正式链路阻塞

### 一、No Bet 报告去误导化

**做什么**

- 修改 `football_advisor/report.py`。
- 当 `policy.recommendation=NO_BET` 时，不再展示“最佳价值候选”。
- 改为展示“模型倾向观察”，并明确写出“因门禁未通过，不构成投注建议”。

**怎么做**

- 在 `ReportBuilder.build_markdown()` 中根据 `policy.recommendation` 分支生成结论区。
- No Bet 分支只保留模型概率、赔率价值差和门禁原因，不把任何 outcome 标成投注候选。
- 对应补充 `tests/test_report.py`：
  - No Bet 报告不包含“最佳价值候选”。
  - No Bet 报告包含“模型倾向观察”。
  - Bet 报告仍可展示正式价值候选。

**为什么这么做**

No Bet 是强制安全门。即使模型计算出正 edge，也不能在报告结论区以“候选”形式强化投注暗示。

**责任边界**

- 我可以直接完成。
- 不需要用户提供 API 或额外资料。

## 二、交易所资金流改为风险增强项

**做什么**

- 当前阶段把交易所资金流定义为风险增强项，不作为交付硬依赖。
- 未配置资金流 provider 时，不触发同步失败。
- 报告中标注“资金流数据未接入，风险评估缺少该维度”。

**怎么做**

- 修改 `football_advisor/pipeline.py` 的同步状态判断。
- 对 `source=exchange_provider` 且 `status=skipped_no_provider_configured` 的结果：
  - 不设置 `sync_data_quality_flag=True`。
  - 在 `features.context` 中记录 `missing_exchange_flow_flag=True`。
- 修改 `football_advisor/report.py`：
  - 在数据与证据区展示资金流状态。
  - 若资金流缺失，只作为风险说明，不单独触发 No Bet。
- 修改 `tests/test_pipeline.py` 和 `tests/test_core.py`：
  - 未配置资金流时，只要结构化数据、赔率、新闻新鲜完整，不因资金流缺失触发 No Bet。
  - 已配置资金流但同步失败时，仍进入同步质量风险。

**为什么这么做**

资金流不能替代赛程、赔率、球队状态、伤停、新闻等核心事实。当前系统已经有官方竞彩赔率、API-Football、football-data.org、TheSportsDB 和新闻链路；资金流更适合做盘口风险增强，而不是第一阶段交付硬门槛。

**责任边界**

- 业务口径需要用户确认：当前推荐“资金流非硬依赖”。
- 代码调整我可以直接完成。

## 三、OpenWebUI 入口参数补齐

**做什么**

- 扩展 `openwebui_tools/football_advisor_tools.py`。
- 预测工具支持可选参数：
  - `home_team`
  - `away_team`
  - `kickoff_time`
  - `match_id`
  - `market`

**怎么做**

- 修改 `predict_match()` 函数签名，继续保留 `query` 和 `mode`。
- 构造 payload 时只传入非空参数。
- 补充 `tests/test_openwebui_tools.py`：
  - 默认仍只发送 `query` 和 `mode`。
  - 提供主客队、开球时间、match_id 时会完整透传到 `/predict`。

**为什么这么做**

正式用户入口不能弱于调试脚本。希腊 VS 意大利这类比赛需要稳定命中本地 `match_id` 和开球时间，否则自然语言解析可能命中错误赛事或无法命中。

**责任边界**

- 我可以直接完成。
- 不需要用户提供 API。

## 四、友谊赛新闻查询词修正

**做什么**

- 修改 `football_advisor/sync.py` 的 `_build_news_search_query()`。
- 只有世界杯赛事才加入 `World Cup`。
- 友谊赛和未知赛事使用通用赛前新闻词。

**怎么做**

- 优先从 `request.market`、`request.query` 或 DuckDB 宽表上下文中识别赛事类型。
- 最小可落地方案：
  - 如果 query 或 league 明确包含世界杯，再使用世界杯词。
  - 否则使用：
    `主队 vs 客队 injury suspension lineup press conference training tactical news`
- 补充测试：
  - `Greece vs Italy` 不生成 `World Cup` 查询词。
  - 世界杯比赛仍生成 `World Cup` 查询词。

**为什么这么做**

友谊赛使用世界杯关键词会拉入不相关结果，影响新闻风险判断和最终 No Bet 解释。

**责任边界**

- 我可以直接完成。
- 不需要用户提供 API。

## 优先级一：补齐新鲜度闭环和通用验收

### 五、预测前同步后宽表复查

**做什么**

- 在预测流程中增加同步后复查。
- 复查对象：
  - 结构化数据更新时间。
  - 市场赔率更新时间。
  - 新闻更新时间。
  - `critical_data_age_minutes`。
  - `no_bet_data_quality_flag`。

**怎么做**

- 在 `DuckDBFeatureBuilder` 读取宽表后，把 `critical_data_age_minutes` 和质量旗标保留在 `features.context`。
- 在 `PredictionPipeline` 中，如果同步结果为 success 但宽表仍超过 30 分钟：
  - 正常进入 No Bet。
  - 报告必须明确写出是哪类数据陈旧。
- 增加测试：
  - 同步成功但宽表过期，报告为 No Bet。
  - 同步成功且宽表新鲜，不因数据年龄触发 No Bet。

**为什么这么做**

同步成功不等于预测可用。最终应该以模型实际读取的宽表数据为准。

**责任边界**

- 我可以直接完成。
- 不需要用户提供 API。

### 六、新增通用比赛门禁脚本

**做什么**

- 新增 `scripts/smoke_test_match_readiness.py`。
- 保留 `scripts/smoke_test_worldcup_readiness.py` 作为世界杯封装。

**怎么做**

- 将当前门禁脚本中通用逻辑抽出。
- 参数支持：
  - `--home-team`
  - `--away-team`
  - `--date`
  - `--match-id`
  - `--competition-context`
- 友谊赛默认走通用新闻查询词。
- 输出仍然脱敏，不打印 token。

**为什么这么做**

希腊 VS 意大利是国际友谊赛，不应长期依赖世界杯命名的脚本做交付验收。

**责任边界**

- 我可以直接完成。
- 不需要用户提供 API。

### 七、修复本地诊断脚本编码

**做什么**

- 修复 `.env` 读取编码问题。

**怎么做**

- 所有直接读取 `.env` 的脚本统一使用 `encoding="utf-8-sig"`。
- 优先复用现有配置加载逻辑，避免每个脚本单独解析。
- 至少修复：
  - `scripts/test_prediction.py`
  - 后续新增的通用门禁脚本。

**为什么这么做**

当前 `scripts/test_prediction.py` 已经在本地因默认 GBK 读取 `.env` 崩溃，影响交付验收可靠性。

**责任边界**

- 我可以直接完成。
- 不需要用户提供 API。

## 优先级二：端到端验收和文档

### 八、OpenWebUI 端到端验收

**做什么**

- 启动 FastAPI 后，通过 OpenWebUI 工具等价请求跑希腊 VS 意大利。

**怎么做**

- 本地脚本层可以先调用 `openwebui_tools/football_advisor_tools.py` 的 `predict_match()`。
- 如果需要真实 OpenWebUI 容器验收，则用户需要在本地导入工具并确认 Valves 中的 `API_BASE_URL`。

**为什么这么做**

交付入口是 OpenWebUI，不是内部 Python 脚本。正式验收必须覆盖用户实际入口。

**责任边界**

- 我可以完成工具级模拟验收。
- 如果要在真实 OpenWebUI 页面中验收，需要用户本地配合导入工具。

### 九、更新交付文档和进度

**做什么**

- 更新：
  - `docs/DELIVERY.md`
  - `docs/PROGRESS.md`
  - 必要时更新 `docs/ARCHITECTURE.md`

**怎么做**

- 明确写入：
  - 资金流是风险增强项。
  - No Bet 报告展示规则。
  - 友谊赛最低数据要求。
  - OpenWebUI 参数传递方式。
  - 通用门禁脚本命令。

**为什么这么做**

交付标准必须可复现、可验收、可解释，不能只存在于对话里。

**责任边界**

- 我可以直接完成。
- 不需要用户提供 API。

## 资金流数据来源推荐

### 推荐口径

当前阶段不建议把资金流作为硬依赖。资金流应作为风险增强项：

- 未接入资金流：不阻断预测，报告标注缺失。
- 已接入但失败：上调风险，必要时叠加其他风险进入 No Bet。
- 已接入且出现异常：作为盘口风险信号进入 No Bet policy。

### 免费优先排序

#### 一、现有数据源生成弱资金流替代信号

**来源**

- 中国竞彩网官方固定奖金。
- API-Football 赔率。
- football-data.org 赛程与比分。
- 本地 DuckDB 历史赔率快照。

**可得到什么**

- 赔率变化方向。
- 赔率变化幅度。
- 官方竞彩与第三方赔率偏差。
- 盘口风险弱信号。

**不能得到什么**

- 真实成交量。
- 真实交易额。
- 真实散户比例。
- 真实锐利资金比例。

**推荐用途**

第一阶段先用它做“市场变化风险”，不要叫真实资金流。

**是否需要用户配合**

不需要。现有条件下我可以直接优化。

#### 二、Betfair Exchange API

**来源特点**

- 官方交易所 API。
- Delayed App Key 无激活费，适合开发测试。
- Delayed App Key 可用延迟价格数据，但选择级成交量不可用；官方文档说明 Delayed App Key 不返回 `totalMatched` 和 `EX_ALL_OFFERS` 的部分成交量字段。
- Live App Key 可获取更完整资金流/成交量，但需要账户、审核和费用。

**可得到什么**

- 市场总成交额。
- 部分价格和盘口深度。
- Live 权限下可得到更完整选择级成交量。

**限制**

- 需要 Betfair 账户、应用密钥和会话 token。
- 商业用途需要 Betfair 批准。
- Live App Key 有成本和合规要求。

**推荐程度**

如果后续要接真实资金流，这是首选官方来源。

**是否需要用户配合**

需要用户本地配置：

- `BETFAIR_APP_KEY`
- `BETFAIR_SESSION_TOKEN` 或登录凭据方案
- 是否使用 delayed 还是 live key

真实值不得通过对话发送。

#### 三、Odds API 的交易所订单簿聚合接口

**来源特点**

- 提供 sports exchange order book 聚合入口。
- 文档显示支持 `betdaq`、`betfair`、`smarkets`、`matchbook`。
- 可返回 back/lay 档位、可用金额、部分来源的成交量和 total matched。
- 有免费 API key 起步，高频或更大覆盖通常需要付费。

**可得到什么**

- 多交易所盘口深度。
- back/lay 价格和可用 size。
- 部分交易所的 total matched。

**限制**

- 需要注册 API key。
- 免费额度和覆盖要以实际账户为准。
- 聚合源的数据完整性取决于上游交易所。

**推荐程度**

适合作为低成本快速接入方案，开发成本低于分别接多个交易所。

**是否需要用户配合**

需要用户本地配置：

- `ODDS_API_KEY`

真实值不得通过对话发送。

#### 四、Smarkets API

**来源特点**

- 官方说明提供交易所市场数据、订单簿和价格历史。
- 需要申请 API access。

**可得到什么**

- 实时订单簿。
- 价格历史。
- 交易所盘口信息。

**限制**

- 需要账户和 API access 审核。
- 覆盖、权限和地区限制需要实际确认。

**推荐程度**

作为 Betfair 之外的补充来源。

**是否需要用户配合**

需要用户申请 API access，并在本地配置 key/token。

#### 五、BETDAQ API

**来源特点**

- 官方 API 包含 read-only 和 secure methods。
- read-only 方法用于一般市场信息。

**可得到什么**

- 交易所市场信息。
- 价格、盘口相关数据。

**限制**

- 需要账户和 API 接入确认。
- 足球市场覆盖和成交量深度需要实际验证。

**推荐程度**

可作为备选交易所来源。

**是否需要用户配合**

需要用户申请或配置 BETDAQ 访问凭据。

## 不推荐作为资金流来源

- 普通赔率网站页面抓取：稳定性差，合规风险高。
- 论坛、社媒热度：只能做舆情，不是资金流。
- LLM 生成的“资金流判断”：禁止作为事实数据。
- 没有成交量字段的赔率 API：只能做赔率变化，不能称为资金流。

## 最终执行顺序

1. 修复 No Bet 报告误导。
2. 落地资金流非硬依赖口径。
3. 修正友谊赛新闻查询词。
4. 扩展 OpenWebUI 预测参数。
5. 增加同步后宽表新鲜度复查。
6. 新增通用比赛门禁脚本。
7. 修复 `.env` 编码问题。
8. 补 OpenWebUI 工具级端到端验收。
9. 更新交付文档和进度。

## 最终验收命令

```powershell
.\.runtime\python\python.exe -m compileall football_advisor tests scripts openwebui_tools
.\.runtime\python\python.exe -m unittest discover -s tests -v
.\.runtime\python\python.exe scripts\verify_delivery_readiness.py
.\.runtime\python\python.exe scripts\smoke_test_match_readiness.py --home-team Greece --away-team Italy --date 2026-06-07
```

验收通过后，还需要用 OpenWebUI 工具入口发起同一场比赛预测，确认正式入口和内部脚本结果一致。

## 参考资料

- Betfair Exchange API Application Keys：`https://betfair-developer-docs.atlassian.net/wiki/spaces/1smk3cen4v3lu3yomq5qye0ni/pages/2687105/Application+Keys`
- Smarkets API Documentation Resources：`https://help.smarkets.com/hc/en-gb/articles/34720906181021-Smarkets-API-Documentation-Resources`
- BETDAQ API：`https://api.betdaq.com/v2.0/docs/WhatIsTheAPI.aspx`
- Odds API sports exchange order book：`https://odds-api.net/docs`
