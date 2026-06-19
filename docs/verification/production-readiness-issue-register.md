# 生产落地全局问题台账

> 批次 0 产出 | 初始登记：2026-06-15 | 基于 `2026-06-15-production-readiness-closure-design.md` 全量风险逐项登记

## 登记协议

- 每项唯一编号 `R-XXX`
- 严重度：`CRITICAL` / `HIGH` / `MEDIUM` / `LOW`
- 分类：`INTERNAL_DEFECT` / `DATA_INVARIANT_FAILED` / `CONFIG_BLOCKED` / `PROVIDER_BLOCKED` / `SAFE_DATA_GAP` / `PERFORMANCE_FAILED` / `SECURITY_FAILED` / `VERIFICATION_INCOMPLETE`
- 状态：`OPEN` / `IN_PROGRESS` / `FIXED` / `USER_DEFERRED` / `USER_REJECTED` / `EXTERNAL_BLOCKED`
- 每个问题标注所属批次和验收测试

---

## 问题台账

### R-001：测试存在跳过和失败（CODE_READY 阻断）

- **严重度**：HIGH
- **分类**：VERIFICATION_INCOMPLETE
- **描述**：全量测试 412 运行中 1 失败（TheSportsDB 无映射球队搜索行为）和 3 跳过（回测/交易所重构后测试未更新）
- **证据**：`unittest discover -s tests -v` 输出
- **影响**：无法声明 CODE_READY
- **所属批次**：批次 1-2 统一修复测试契约后处理
- **状态**：OPEN
- **验收测试**：全量测试 0 失败 0 跳过（经批准的非故意跳过除外）

### R-002：编译状态（CODE_READY 检查）

- **严重度**：LOW
- **分类**：VERIFICATION_INCOMPLETE
- **描述**：编译检查通过（退出码 0），但仅确认无语法错误
- **证据**：`compileall -q` 输出为空
- **影响**：无当前影响，保留为基线记录
- **所属批次**：批次 0（本批即登记）
- **状态**：OPEN（基线取证）

### R-003：单元测试可能访问真实网络

- **严重度**：HIGH
- **分类**：INTERNAL_DEFECT
- **描述**：未验证所有单元测试在无网络环境下是否仍能通过。测试禁网机制缺失。
- **证据**：设计文档第 3.1 节要求"单元测试不得访问真实网络"
- **影响**：测试不可靠，CI 环境可能因网络波动而失败
- **所属批次**：批次 1
- **状态**：OPEN
- **验收测试**：`tests/test_system_invariants.py` 新增禁网断言

### R-004：验证状态未完成时可能输出就绪结论

- **严重度**：HIGH
- **分类**：INTERNAL_DEFECT
- **描述**：`verification_engine.py` 中 `verification_status=INCOMPLETE` 时 `final_status` 可能非 None
- **证据**：设计文档第 4 节统一失败分类
- **影响**：未完成验证被误标为就绪，导致生产误判
- **所属批次**：批次 1
- **状态**：OPEN
- **验收测试**：`tests/test_verification_engine.py` 新增断言

### R-005：数据库迁移幂等性未验证

- **严重度**：HIGH
- **分类**：INTERNAL_DEFECT
- **描述**：全新库初始化、历史库迁移、二次迁移不重复列不丢数据、迁移失败恢复未验证
- **证据**：设计文档第 3.2 节 DEPLOYMENT_READY 要求
- **影响**：生产部署时数据库状态不可预测
- **所属批次**：批次 2
- **状态**：OPEN
- **验收测试**：`tests/test_database_migration.py` + `scripts/verify_database_migration.py`

### R-006：Provider 映射缺失时静默丢弃数据

- **严重度**：HIGH
- **分类**：DATA_INVARIANT_FAILED
- **描述**：未映射的 provider 记录可能被静默丢弃而不进入隔离清单，映射补齐后无法重放恢复
- **证据**：设计文档第 5 节修复架构中的"provider 实体映射"步骤
- **影响**：数据丢失不可恢复
- **所属批次**：批次 3
- **状态**：OPEN
- **验收测试**：`tests/test_provider_mapping_importer.py`、`tests/test_match_resolver.py`

### R-007：时区处理不统一，可能导致未来数据穿越

- **严重度**：CRITICAL
- **分类**：DATA_INVARIANT_FAILED
- **描述**：naive 时间、带时区时间和数据库时间未统一转 UTC。新鲜度可能使用本地采集时间而非 `source_updated_at`。未来来源时间未拒绝。
- **证据**：设计文档第 2 节最高原则第 7 条
- **影响**：回测数据泄漏、新鲜度误判、No Bet 错误放行
- **所属批次**：批次 4
- **状态**：OPEN
- **验收测试**：`tests/test_target_sync.py`、`tests/test_feature_builder.py`、`tests/test_backtest_engine.py`

### R-008：Staging 去重和幂等写入未验证

- **严重度**：MEDIUM
- **分类**：DATA_INVARIANT_FAILED
- **描述**：同一载荷重复同步可能增加逻辑重复行，部分失败可能回滚已成功来源
- **证据**：设计文档第 5 节 staging 去重步骤
- **影响**：数据重复、同步不一致
- **所属批次**：批次 5
- **状态**：OPEN
- **验收测试**：`tests/test_multi_source_coordinator.py`

### R-009：同步缺少 30 分钟缓存、超时和取消机制

- **严重度**：HIGH
- **分类**：PERFORMANCE_FAILED
- **描述**：每次预测可能重复调用外部 provider，无缓存复用、无超时终止、无可取消机制
- **证据**：设计文档第 2 节最高原则第 9 条
- **影响**：外部调用风暴、单个来源失败拖死整个流程
- **所属批次**：批次 6
- **状态**：OPEN
- **验收测试**：`tests/test_external_sync.py`、`tests/test_target_sync.py`

### R-010：竞彩五玩法链路不完整

- **严重度**：CRITICAL
- **分类**：DATA_INVARIANT_FAILED
- **描述**：SPF/RQSPF 进入 `stg_odds`，CRS/TTG/HAFU 进入 `stg_sporttery_odds_detail` 的链路需要验证。未开售语义需明确为 `sporttery_play_type_not_offered`。第三方赔率不能替代竞彩。
- **证据**：设计文档第 2 节最高原则第 1-2 条，PROGRESS 中竞彩规则
- **影响**：竞彩核心功能不完整，可能导致错误的投注建议
- **所属批次**：批次 7
- **状态**：OPEN
- **验收测试**：`tests/test_sporttery_client.py`、`scripts/verify_sporttery_5playtypes.py`

### R-011：候选数据无治理机制

- **严重度**：HIGH
- **分类**：INTERNAL_DEFECT
- **描述**：候选数据（已采集未使用、建议新增、外部来源）缺少统一登记、调研模板和审批状态机。AI 可能自行决定数据用途或弃用。
- **证据**：设计文档第 2 节最高原则第 11-13 条，全量闭环设计数据治理章节
- **影响**：数据使用无追溯、AI 越权决策、数据价值未评估即接入或弃用
- **所属批次**：批次 8
- **状态**：OPEN
- **验收测试**：`tests/test_data_governance.py`

### R-012：核心模型特征缺乏独立调研

- **严重度**：HIGH
- **分类**：VERIFICATION_INCOMPLETE
- **描述**：Poisson 进球、Elo/动态 Elo、近期战绩、进失球、主客场、中立场等核心特征未经过独立数据调研（来源、覆盖率、时效、穿越风险、单特征/消融指标）
- **证据**：设计文档第 2 节最高原则第 12 条，PROJECT_ANALYSIS 待调研基线
- **影响**：特征质量不可知，权重无统计依据
- **所属批次**：批次 9
- **状态**：OPEN
- **验收**：调研报告 + `tests/test_data_research.py`

### R-013：球队表现和比赛动机数据未调研

- **严重度**：MEDIUM
- **分类**：VERIFICATION_INCOMPLETE
- **描述**：xG、射门质量、控球/压迫、H2H、积分榜、赛事阶段、出线动机等未独立调研
- **证据**：设计文档批次 10
- **影响**：高级特征不可用
- **所属批次**：批次 10
- **状态**：OPEN
- **验收**：调研报告 + `tests/test_data_research.py`

### R-014：阵容、伤停和球员重要性数据未调研

- **严重度**：MEDIUM
- **分类**：PROVIDER_BLOCKED
- **描述**：名单、首发、阵型、伤停、停赛、球员重要性、俱乐部表现等数据未调研。当前穿测中 lineup_injury 组件因无供应商配置而失败。
- **证据**：PROGRESS 2026-06-14 已知限制
- **影响**：阵容伤停无法评估
- **所属批次**：批次 11
- **状态**：OPEN
- **验收**：调研报告 + `tests/test_data_research.py`

### R-015：赛程环境数据未调研

- **严重度**：LOW
- **分类**：VERIFICATION_INCOMPLETE
- **描述**：休息天数、旅行距离、时差、天气、场地、草皮、海拔、裁判等未独立调研
- **证据**：设计文档批次 12，PROJECT_MEMORY 中标为"暂缓"
- **影响**：环境因素不可用
- **所属批次**：批次 12
- **状态**：OPEN
- **验收**：调研报告 + `tests/test_data_research.py`

### R-016：新闻、战术和舆情数据未调研

- **严重度**：MEDIUM
- **分类**：PROVIDER_BLOCKED
- **描述**：新闻搜索 API（SearXNG/Serper）未配置，新闻采集链路未验证。文本相似度、重排、LLM 结构化抽取、来源引用和风险未评估。
- **证据**：PROGRESS 2026-06-14 已知限制
- **影响**：新闻信号无法进入预测
- **所属批次**：批次 13
- **状态**：OPEN
- **验收**：调研报告 + `tests/test_data_research.py`

### R-017：赔率、市场和资金流未调研

- **严重度**：MEDIUM
- **分类**：PROVIDER_BLOCKED
- **描述**：竞彩赔率变化、第三方赔率、市场一致性、资金流、交易所数据未独立调研。第三方资金流不可得。
- **证据**：设计文档批次 14，PROGRESS 2026-06-14 已知限制
- **影响**：赔率趋势和市场信号不可用
- **所属批次**：批次 14
- **状态**：OPEN
- **验收**：调研报告 + `tests/test_data_research.py`

### R-018：特征血缘和默认值治理缺失

- **严重度**：HIGH
- **分类**：INTERNAL_DEFECT
- **描述**：未批准特征可能进入概率/No Bet/报告通道。不同球队可能因静默默认值生成相同特征。必填特征缺失可能不触发 No Bet。
- **证据**：设计文档批次 15
- **影响**：特征使用不可追溯、默认值导致错误预测
- **所属批次**：批次 15（依赖批次 9-14 用户决策）
- **状态**：OPEN（BLOCKED_BY 批次 9-14 用户审批）
- **验收测试**：`tests/test_feature_builder.py`、`tests/test_data_governance.py`

### R-019：概率模型未校准，权重无依据

- **严重度**：CRITICAL
- **分类**：VERIFICATION_INCOMPLETE
- **描述**：未批准或未校准模型可能标记 `BET_READY=true`。Poisson 权重 0.72、Elo 权重 0.28、状态修正 ±0.035 均为硬编码无校准。价值阈值和市场偏差阈值也属于待调研基线。
- **证据**：设计文档第 3.4 节 BET_READY，PROJECT_REQUIREMENTS 中的硬编码参数标记
- **影响**：概率模型输出不可靠，投注建议无统计支撑
- **所属批次**：批次 16
- **状态**：OPEN
- **验收测试**：`tests/test_core.py`、`tests/test_calibration_engine.py`

### R-020：LLM 可能输出投注结论或改写事实

- **严重度**：HIGH
- **分类**：INTERNAL_DEFECT
- **描述**：LLM 报告生成器可能新增数字、球队、比分、赔率或投注结论。外部 LLM 失败后回退链路不完整。
- **证据**：设计文档批次 17，重定向规则 R8（LLM 只返回三字段）
- **影响**：LLM 幻觉导致错误投注建议
- **所属批次**：批次 17
- **状态**：OPEN
- **验收测试**：`tests/test_report.py`、`tests/test_report_generator.py`

### R-021：API 安全、性能和恢复门禁未验证

- **严重度**：HIGH
- **分类**：SECURITY_FAILED
- **描述**：SQL 注入防护、URL 抓取 SSRF、管理接口鉴权、日志脱敏、request_id 贯穿、阶段超时取消、数据库恢复均未独立验证
- **证据**：设计文档批次 18
- **影响**：生产安全风险
- **所属批次**：批次 18
- **状态**：OPEN
- **验收测试**：`tests/test_api.py`、`tests/test_url_safety.py`

### R-022：四场全模式穿测未执行

- **严重度**：CRITICAL
- **分类**：VERIFICATION_INCOMPLETE
- **描述**：德国 vs 库拉索、荷兰 vs 日本、科特迪瓦 vs 厄瓜多尔、瑞典 vs 突尼斯四场全模式穿测（全新库、历史库迁移、live、cache、故障注入）未完成
- **证据**：设计文档批次 19，PROGRESS 2026-06-14 穿测仅执行 live 模式
- **影响**：无法确认全链路可用性
- **所属批次**：批次 19
- **状态**：OPEN
- **验收测试**：`tests/test_walkthrough_e2e.py` + `scripts/walkthrough_e2e.py --mode fixture/live/cache`

### R-023：中立场标记在 feature_builder 中未生效

- **严重度**：HIGH
- **分类**：INTERNAL_DEFECT
- **描述**：系统不变量测试 `test_feature_builder_has_no_neutral_venue_awareness` 显示 `feature_builder` 没有 `is_neutral_venue` 处理，总是返回正值主场优势
- **证据**：系统不变量测试输出
- **影响**：世界杯中立场比赛错误施加主场优势
- **所属批次**：批次 4（UTC/时间/中立场）或批次 15（特征血缘）
- **状态**：OPEN
- **验收测试**：`tests/test_system_invariants.py` 相关测试通过后更新

### R-024：同步缺少实际缓存/去重机制

- **严重度**：MEDIUM
- **分类**：INTERNAL_DEFECT
- **描述**：系统不变量测试 `test_prediction_sync_has_cache_or_dedup_mechanism` 显示 `TargetMatchSyncService` 需要实际缓存/去重机制
- **证据**：系统不变量测试输出
- **影响**：重复外部调用
- **所属批次**：批次 6
- **状态**：OPEN
- **验收测试**：`tests/test_target_sync.py`

---

## 外部阻塞清单

| 编号 | 描述 | 依赖 | 状态 |
|------|------|------|------|
| EXT-001 | SearXNG / Serper API 未配置 | 用户提供 API 密钥或本地 SearXNG 实例 | BLOCKED |
| EXT-002 | API-Football 或其他实时球队统计源未配置 | 用户提供 API 密钥 | BLOCKED |
| EXT-003 | 阵容/伤病实时数据源未配置 | 用户提供 API 密钥或数据源 | BLOCKED |
| EXT-004 | 第三方赔率资金流未配置 | 用户提供 API 密钥 | BLOCKED |
| EXT-005 | 外部 OpenAI-compatible LLM 需用户确认模型选择 | 用户选择并配置 DeepSeek/其他 | 等待用户决策 |

---

## 旧验收结论与需更新项

| 项目 | 描述 | 批次依赖 |
|------|------|----------|
| 跳过测试更新 | `test_core.py:598`、`test_exchange_sync.py:80`、`test_backtest_loader.py:14` 三个跳过测试需更新或正式弃用 | 批次 2-5 |
| 2026-06-14 穿测结论 | 仅 live 模式，需补充 fixture/cache/故障注入模式 | 批次 19 |
| PRODUCTION_READY 声明 | 当前不得使用，只能使用 SAFE_DEGRADED 或 EXTERNAL_BLOCKED | 全部批次完成后 |