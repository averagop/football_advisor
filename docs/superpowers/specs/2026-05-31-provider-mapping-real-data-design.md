# Provider Mapping 与真实结构化数据接入设计

## 目标

本轮目标是补齐真实结构化 provider 数据进入 `core.*` 前的最小可信闭环。系统必须先通过显式 provider mapping 将外部联赛、球队 ID 转换为内部系统 ID，再把可校验的赛程、赔率、统计、伤停和阵容数据写入 DuckDB。未映射、字段缺失或来源不满足要求的数据必须跳过并记录审计信息，不得自动编造实体或使用 mock 结构补齐。

## 当前问题

当前主线已经加固了 mock 隔离和 provider 边界：默认资金流同步不会写入模拟数据，`football-data.org` 与 `API-Football` 导入也会对未映射实体计数并跳过。但真实 provider mapping 字典的导入流程尚未落地，API-Football 的真实赔率、统计、伤停、阵容等独立端点也尚未完整编排。

如果直接继续接真实端点，会出现两个风险：

- 外部 provider ID 与系统 ID 不一致时，真实数据无法安全合并到 `core.*`。
- 赔率或统计字段缺失时，系统可能只得到 fixture 命中状态，却无法形成可用预测数据，只能进入 No Bet。

## 方案选择

推荐方案是先实现 provider mapping 字典导入，再接 API-Football 关键独立端点，最后用真实端到端样本验证。

备选方案一是先完成 OpenWebUI 实盘对话测试。该方案能更快体验界面，但不会解决结构化数据质量问题。

备选方案二是先做 ChromaDB 新闻入库和距离过滤。该方案能改善新闻舆情证据，但在结构化赔率、统计、伤停数据未打通前，对正式投注建议的帮助有限。

因此本轮采用推荐方案。它最贴近当前卡点，改动范围清晰，并且符合项目“先结构化闭环，后文本与外部实时源”的路线。

## 范围

本轮包含：

- provider league mapping 与 provider team mapping 的确定性导入。
- `football-data.org` 和 `API-Football` 共享映射表的读取与审计。
- API-Football 独立赔率、统计、伤停、阵容端点的最小接入边界。
- 同步结果中的 staged、merged、skipped、missing_market、provider_failure 等审计字段。
- 对 No Bet 数据质量门禁的回归验证。
- `docs/PROGRESS.md` 同步记录。

本轮不包含：

- 真实交易所客户端实现。
- ChromaDB 新闻正文入库闭环。
- 模型权重调参、校准优化或新增投注策略。
- OpenWebUI 界面注册操作自动化。
- 大规模 provider 实体自动消歧。

## 数据设计

现有 `core.dim_provider_league_mapping` 和 `core.dim_provider_team_mapping` 保持为 provider 数据进入 `core.*` 的唯一映射入口。

导入输入采用本地文件，建议放在 `data/provider_mappings/` 下，使用简单 CSV：

- `provider_league_mappings.csv`
- `provider_team_mappings.csv`

字段使用最小集合：

```text
provider_name,provider_league_id,system_league_id,provider_league_name
provider_name,provider_team_id,system_team_id,provider_team_name
```

导入规则：

- `provider_name + provider_*_id` 必须唯一。
- `system_*_id` 必须已存在于内部维度表。
- 重复映射以最后一次导入覆盖同一 provider key。
- 缺少内部实体时跳过该行并记录 skipped。
- 不通过字符串相似度自动创建系统球队或联赛。

## 数据流

```text
本地 provider mapping CSV
-> 映射导入器校验字段和内部 ID
-> 写入 core.dim_provider_*_mapping
-> DataSyncCoordinator 调用外部 provider
-> provider 响应先进入 staged 解析
-> 通过 mapping 转换为 system ID
-> 可用数据合并到 core.fact_* 表
-> 同步审计进入 SyncResult.details
-> PredictionPipeline 把同步质量写入 NoBetPolicy 上下文
```

API-Football 关键端点按优先级接入：

1. 赔率端点：只写入真实 1X2、让球、大小球盘口和赔率。
2. 统计端点：只写入可映射球队的赛前或赛后统计，字段缺失时跳过。
3. 伤停端点：写入结构化摘要字段，不把新闻文本写入 DuckDB。
4. 阵容端点：只在 provider 返回明确阵容字段时写入，不猜测首发。

fixture 命中但缺少赔率或统计时，继续返回明确状态，例如 `fixture_matched_no_market_stats`，并触发数据质量门禁。

## 错误处理

- 未配置真实 provider token：返回 `skipped_no_provider_configured`。
- provider 请求失败：记录 `provider_failures`，不得静默降级为 mock。
- provider 实体未映射：跳过对应记录并增加 `skipped_unmapped_count`。
- provider 响应缺少关键字段：记录 `missing_required_fields_count`。
- fixture 命中但没有可用市场或统计：记录 `missing_market_stats_count`。
- 部分端点成功、部分失败：保留成功写入，同时在同步审计中记录失败端点，预测仍由 No Bet 判断是否拦截。

所有错误状态必须进入 `SyncResult.details`，供测试、日志和 No Bet 上下文使用。

## 测试设计

测试只使用 fake provider 响应和临时 DuckDB，不依赖真实 API Key。

必须覆盖：

- provider mapping CSV 正常导入。
- 未映射 system ID 的 mapping 行被跳过并计数。
- provider 数据通过 mapping 写入 `core.*`。
- 未映射 provider team id 不会自动创建球队。
- fixture 命中但缺少赔率或统计时返回明确状态。
- provider 请求失败时保留 `provider_failures`。
- 预测链路能把同步失败、同步跳过或市场数据缺失传入 No Bet。

验证命令沿用项目现有运行时：

```powershell
.\.runtime\python\python.exe -m unittest tests.test_external_sync tests.test_api_football_sync tests.test_exchange_sync -v
.\.runtime\python\python.exe -m unittest discover -s tests -v
```

## 验收标准

- provider mapping 导入器不依赖 LLM，不做模糊猜测。
- 真实 provider 数据进入 `core.*` 前必须经过 `core.dim_provider_*_mapping`。
- mock provider 只能在测试中显式注入，生产默认路径不得写入 mock 数据。
- 同步审计字段能区分 staged、merged、skipped、missing 和 failure。
- 缺赔率、缺统计、同步失败或未配置 provider 时，预测链路继续触发 No Bet。
- 全量单元测试通过。
- `docs/PROGRESS.md` 记录完成内容、验证、限制和下一步。
