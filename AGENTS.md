# Project Memory: Football Betting Advisor

## Always Read First

For every new Codex conversation in this workspace, read these files before planning or editing:

- `docs/PROJECT_REQUIREMENTS.md`
- `docs/ARCHITECTURE.md`
- `docs/PROGRESS.md`
- `docs/PROJECT_ANALYSIS.md`

Treat them as the persistent source of truth for this project.

## 投注平台最高优先规则

- **目标投注平台是中国竞彩足球（Sporttery）**，不是任何其他博彩平台。
- **只有 5 种玩法有效**：胜平负（SPF）、让球胜平负（RQSPF）、比分（CRS）、总进球（TTG）、半全场胜平负（HAFU）。
- **竞彩的赔率数据必须拿到** — 不能遗漏、不能跳过、不能因"数据不足"而放弃。每一种玩法都要入库到 `staging.stg_odds`（三路赔率）或 `staging.stg_sporttery_odds_detail`（比分/总进球/半全场）。
- **预测和分析以竞彩的赔率结构为准**。其他数据源的赔率（API-Football、TheOddsAPI、OddsFeed 等）是辅助参考，不得替代竞彩的主体地位。
- **如果竞彩的某种玩法未对某场比赛开售**，标记为"竞彩未开售"，其他源的同类型数据不能直接用作替代（玩法规则不同）。
- **竞彩数据的优先级高于一切**，在数据流程中永远排在 Priority 1。

## Working Rules

- Keep OpenWebUI as the only user-facing frontend entry.
- Keep DuckDB and ChromaDB strictly separated:
  - DuckDB stores structured numerical/tabular data only.
  - ChromaDB stores unstructured news, tactical text, and sentiment text only.
- Do not use the local Qwen2.5-7B model as the primary prediction or reasoning engine.
- Use the external OpenAI-compatible LLM as the main report-generation engine.
- Use local Qwen2.5-7B only as the backup report-generation engine after external LLM failure.
- The LLM must not invent match probabilities. Probabilities must come from the probability/value engines.
- Prediction workflow must stay fixed:
  parse request -> sync data -> query DuckDB and ChromaDB separately -> feature engineering -> probability model -> odds value engine -> No Bet policy -> LLM report.
- Before any prediction, data sync must run first and critical data must be no older than 30 minutes.
- SQL access must remain SELECT-only, single-statement, and limited.
- This system is a betting decision-support and analysis system, not an automatic betting system and not a guaranteed-profit system.

## 协作资料规则

- 执行任务过程中如果需要 API Key、账号权限、外部资料、业务口径、数据样例或其他必要信息，应及时向用户说明所需内容、用途和推荐提供格式。
- 涉及 API Key、Token、账号、Cookie、私有数据集、商业资料或其他敏感信息时，默认按保密资料处理，只索取完成当前任务所必需的最小范围，并优先让用户在本地自行配置真实值。
- 需要敏感配置时，应只告知用户变量名、用途、放置位置、占位符示例和本地验证命令；真实值不得通过对话发送给 Codex。
- 真实密钥、Token、账号密码、Cookie、完整私有资料和未脱敏业务数据不得写入代码、文档、测试快照、提交信息、日志、终端输出总结或对话最终回复。
- 需要示例时必须使用占位符或脱敏格式，例如 `<YOUR_API_KEY>`、`sk-****abcd`，不得构造看似真实的密钥。
- 优先建议用户通过本地环境变量、未纳入版本控制的 `.env` 文件、密钥管理器或运行时配置注入敏感信息；仓库中只保留 `.env.example`、配置说明和读取逻辑。
- 校验敏感配置时，只验证变量是否存在、格式是否明显合法、服务是否可连接，输出内容必须脱敏；不得打印完整环境变量、请求头、连接串或响应中的敏感字段。
- 编写测试时使用 fake 值、mock provider、临时测试密钥或用户本地注入的环境变量；不得让测试依赖仓库内的真实凭据。
- 调用外部服务、联网命令、第三方工具或会产生远端记录的操作前，应确认该操作是否会发送敏感信息，并避免把无关上下文、密钥或私有数据传出本地环境。
- 如果发现敏感信息已经进入仓库、日志或输出，应立即提示风险、停止扩散，并建议轮换密钥、清理记录和补充忽略规则。

## Progress Update Rule

After every meaningful implementation change, update `docs/PROGRESS.md` in the same turn with:

- Date
- Completed work
- Verification performed
- Known limitations
- Next recommended step

If a change alters architecture or product intent, also update `docs/ARCHITECTURE.md` or `docs/PROJECT_REQUIREMENTS.md`.
