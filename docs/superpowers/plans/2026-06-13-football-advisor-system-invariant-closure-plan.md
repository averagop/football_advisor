# Football Advisor 系统不变量最终收口实施计划

> **执行要求：** 必须按任务顺序实施。每个任务先写失败测试，再做最小实现，再运行定向测试。任何任务未满足验收条件，不得在 `docs/PROGRESS.md` 中声明“生产闭环完成”。

**目标：** 对前两次修复做反向验收，修复第三次审查确认的跨模块语义缺陷，并建立不可绕过的业务不变量验收，确保后续不会再次出现“代码已提交、测试全绿，但真实流程仍未闭环”的情况。

**总体方案：** 保留现有 OpenWebUI、DuckDB、ChromaDB、概率引擎、价值引擎和报告路由，不引入新框架或新数据库。修复集中在竞彩主数据隔离、时间语义、赛场属性、数据就绪证据、预测日志语义、确定性报告、同步缓存和验收口径。所有第三方赔率只做交叉校验，不能进入竞彩主赔率选择。

**技术栈：** Python、DuckDB、unittest、现有外部 OpenAI 兼容模型路由、现有 Sporttery/API-Football/新闻采集链路。

---

## 一、为什么前两次仍有问题

前两次修复并非无效。多数单点缺陷已经修复，当前全量测试也能通过。问题在于验收主要围绕“函数是否存在、异常是否抛出、脚本是否退出 0”，没有持续验证以下跨模块业务不变量：

1. **主数据不变量缺失：** 文档规定竞彩赔率最高优先，但市场视图和多源协调器仍允许第三方赔率成为实际 SPF 数据。
2. **字段语义不变量缺失：** `predicted_outcome` 实际写入“最佳价值选项”，赛后却按“模型预测结果”核验。
3. **证据不变量缺失：** 阵容伤停就绪使用通用上下文更新时间兜底，导致没有阵容伤停事实也可能显示新鲜。
4. **时间不变量缺失：** 不同模块对无时区时间采用不同解释，未来时间戳可能被误判为新鲜。
5. **报告边界不变量缺失：** 虽然定义了 `ReportNarrative`，最终报告仍由模型整篇生成，确定性事实没有真正封闭。
6. **生产验收口径混淆：** “明确失败且安全降级”被计入穿测通过，证明了安全性，却没有证明生产数据可用性。
7. **计划状态没有反向核对：** 两份计划的复选框未更新，提交记录成为主要完成证据，任务完成与业务闭环之间缺少可审计映射。

本计划不再按“发现一个问题增加一个补丁”推进，而是先固定系统不变量，再用集成测试和真实穿测证明不变量成立。

---

## 二、前两次计划反向验收结论

### 2.1 2026-06-12 可靠性优化计划

| 任务 | 当前结论 | 反向验收结果 |
|---|---|---|
| 1 显式 provider 比赛映射 | 已修复 | 四场目标比赛和球队存在显式竞彩映射，未发现按名称猜测进入核心表。 |
| 2 禁止原始 `M_ST_*` 绕过 | 已修复 | 未映射数据留在 staging，核心流程使用标准 `match_id`。 |
| 3 竞彩详细玩法合并到 core | 已修复 | 五玩法状态和详细赔率已有核心表与特征读取链路。 |
| 4 历史重复数据对账 | 已修复 | 有迁移脚本和回归测试；正式库仍需在部署时执行迁移检查。 |
| 5 目标比赛同步服务 | 部分修复 | 服务已接入，但同一次预测仍可能重复同步竞彩，30 分钟缓存未真正闭环。 |
| 6 持久化任务状态与补偿 | 已修复 | 任务状态、失败记录和补偿测试存在。 |
| 7 响应后维护 | 已修复 | API 已使用后台维护，预测日志失败会阻止报告生成。 |
| 8 按标准比赛检索新闻 | 已修复 | 代码按标准比赛过滤并保留失败状态；真实目标比赛新闻为空属于数据可用性问题。 |
| 9 模型与模板回退 | 已修复 | 外部模型、Qwen、模板调用次数边界已有测试。 |
| 10 报告事实确定性渲染 | **未修复** | `ReportNarrative` 未被使用；模型仍返回整篇报告，验证器未真正阻止新增数字。 |
| 11 任意 URL 抓取风险 | 已修复 | 私网、DNS、重定向、端口和管理令牌边界已有测试。 |
| 12 剩余可观测性 | 部分修复 | 多数静默异常已处理，但差异分析和赔率趋势仍存在吞错或仅日志后继续的路径。 |
| 13 赔率趋势不改概率 | 已修复 | 默认配置下赔率趋势不再修改基础概率。 |
| 14 walk-forward 校准门禁 | 部分修复 | 未校准强制 No Bet 已实现，但训练/核验日志语义受 `predicted_outcome` 错写污染。 |
| 15 按价值使用已采集数据 | 部分修复 | 接入边界已收敛，但阵容伤停就绪可误报，目标比赛相关表仍为空。 |
| 16 完整测试与两场验收 | 部分修复 | 技术测试通过；真实同步失败被当作验收通过，未证明生产可用。 |

结论：10 项已修复，5 项部分修复，1 项未修复。

### 2.2 2026-06-13 生产收口计划

| 任务 | 当前结论 | 反向验收结果 |
|---|---|---|
| 1 启动迁移 | 已修复 | 启动迁移和旧库兼容测试已存在。 |
| 2 目标竞彩假成功 | 已修复 | 目标比赛五玩法逐项核验，空数据不再返回 success。 |
| 3 组件就绪门禁 | 部分修复 | 已接入 No Bet，但阵容伤停证据和未来时间戳判断不正确。 |
| 4 日志失败终止 | 部分修复 | 日志写入失败会终止；差异分析失败仍继续，且日志字段业务含义错误。 |
| 5 删除占位概率 | 已修复 | 特征不可用时不再生成占位概率。 |
| 6 模型校准门禁 | 部分修复 | 门禁有效，但日志语义错误会污染后续校准和绩效统计。 |
| 7 新闻阶段移除 Qwen | 已修复 | 新闻阶段未发现 Qwen 压缩调用。 |
| 8 报告回退去重 | 已修复 | 每个模型最多调用一次。 |
| 9 URL 校验失败关闭 | 已修复 | 安全校验失败时拒绝访问。 |
| 10 抓取预览鉴权 | 已修复 | 入口默认关闭并要求管理令牌。 |
| 11 四场目标映射 | 已修复 | 四场比赛静态映射检查通过。 |
| 12 有价值数据接入 | 部分修复 | 逻辑边界已有，但数据缺失、阵容伤停误报和中立场缺失仍影响结论。 |
| 13 精简初始化与重复烟测 | 已修复 | 冗余初始化和脚本级重复检查已清理；预测内重复同步是新的跨模块问题。 |
| 14 四场真实穿测 | 部分修复 | 四场均安全输出 No Bet，但实时竞彩、新闻和多源数据均未成功返回，不能称为生产闭环。 |

结论：9 项已修复，5 项部分修复。

### 2.3 当前四场穿测事实

当前测试库中四场流程均可走到报告生成，并保持 No Bet；但实时同步共同出现：

- `sporttery: empty_after_retries`
- `multi_source: no_source_coverage`
- `news_provider_placeholder: empty_search`
- 交易所数据跳过

因此当前只能确认：**失败可见、不会伪造概率、不会绕过 No Bet、报告可生成。**

当前不能确认：**实时竞彩五玩法可获取、目标新闻可获取、阵容伤停可获取、30 分钟缓存生效、生产数据链路可用。**

---

## 三、本轮必须固定的系统不变量

以下不变量必须先写入集成测试，任何后续实现不得绕过：

1. `official_sporttery_is_primary`：竞彩五玩法的开售状态和赔率只来自 `SportteryOfficialWeb`。
2. `third_party_odds_are_cross_check_only`：第三方赔率不能填补“竞彩未开售”或“竞彩同步失败”。
3. `model_prediction_is_not_value_candidate`：模型结果、价值候选、策略建议和真实投注是四种不同对象。
4. `no_bet_has_no_synthetic_profit`：No Bet 预测可以核验模型准确率，但不能计算虚构投注盈亏。
5. `all_critical_timestamps_are_utc`：关键时间统一按 UTC 解释，未来时间戳直接失败。
6. `neutral_match_has_no_home_advantage`：中立场不应用默认主场优势和 Elo 主场加成。
7. `readiness_requires_component_evidence`：阵容、伤停、新闻等组件只能由对应事实和对应更新时间证明。
8. `llm_writes_narrative_only`：模型只能生成三个叙述字段，所有数字和结构化事实由代码渲染。
9. `one_prediction_one_component_sync`：单次预测每个外部组件最多同步一次，30 分钟内命中缓存不得再次调用。
10. `acceptance_statuses_are_mutually_exclusive`：生产就绪、安全降级、外部阻塞三种结果互斥，不得把后两者写成生产就绪。

---

## 四、实施任务

以下每个任务完成后，必须在同一任务内更新 `docs/PROGRESS.md`，记录日期、完成工作、验证、已知限制和下一步，并与该任务代码一起提交。

### 任务 0：先建立系统不变量回归测试

**文件：**

- 新增：`tests/test_system_invariants.py`
- 修改：`tests/test_db_schema.py`
- 修改：`tests/test_pipeline.py`
- 修改：`tests/test_target_sync.py`

**步骤 1：写出当前必然失败的集成测试**

至少覆盖：

```python
def test_newer_third_party_odds_cannot_override_sporttery(): ...
def test_not_on_sale_cannot_be_filled_by_third_party_odds(): ...
def test_value_candidate_is_not_logged_as_model_outcome(): ...
def test_no_bet_verification_does_not_create_profit_loss(): ...
def test_future_component_timestamp_is_not_fresh(): ...
def test_neutral_match_removes_home_advantage(): ...
def test_lineup_readiness_requires_lineup_or_injury_evidence(): ...
def test_llm_output_cannot_replace_deterministic_report_facts(): ...
def test_prediction_sync_calls_each_component_once(): ...
```

**步骤 2：确认测试在当前代码上失败**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_system_invariants -v
```

**验收：** 失败原因必须与上述不变量一致，不允许因测试环境、导入错误或随机数据失败。

**提交：**

```powershell
git add tests/test_system_invariants.py tests/test_db_schema.py tests/test_pipeline.py tests/test_target_sync.py docs/PROGRESS.md
git commit -m "test: 固化足球预测系统业务不变量"
```

---

### 任务 1：强制隔离竞彩主赔率与第三方交叉校验赔率

**文件：**

- 修改：`football_advisor/db_schema.py`
- 修改：`football_advisor/multi_source_coordinator.py`
- 修改：`football_advisor/feature_builder.py`
- 修改：`football_advisor/odds_value_engine.py`
- 修改测试：`tests/test_db_schema.py`
- 修改测试：`tests/test_multi_source_coordinator.py`
- 修改测试：`tests/test_feature_builder.py`

**步骤 1：修复市场视图的 provider 混选**

`core.view_market_feature_base` 中竞彩 SPF/RQSPF 必须增加：

```sql
WHERE o.source_provider = 'SportteryOfficialWeb'
```

不得仅按 `(match_id, odds_type)` 对所有供应商共同排序。第三方赔率建立独立交叉校验 CTE，只输出：

```text
cross_check_home_odds
cross_check_draw_odds
cross_check_away_odds
cross_check_source_provider
cross_check_updated_at
```

这些字段只进入风险上下文，不进入竞彩价值计算。

**步骤 2：拆分“官方玩法可用”与“第三方覆盖”判断**

将 `_has_spf_odds()` 改成两个职责明确的方法：

```python
def _has_official_sporttery_spf(self, match_id: str) -> bool: ...
def _has_cross_check_1x2(self, match_id: str) -> bool: ...
```

`SPF` 缺口只能由第一个方法关闭。竞彩为 `NOT_ON_SALE` 或同步失败时，第三方赔率不能把官方组件标记为成功。

**步骤 3：增加来源字段断言**

`DuckDBFeatureBuilder` 构建竞彩赔率时，若来源不是 `SportteryOfficialWeb`，抛出 `FeatureDataUnavailable`，不得静默使用。

**步骤 4：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_db_schema tests.test_multi_source_coordinator tests.test_feature_builder tests.test_system_invariants -v
```

**验收：** 插入时间更新的第三方 1X2 后，官方竞彩赔率、开售状态和价值计算均不变化；交叉校验字段可以变化。

**提交：**

```powershell
git add football_advisor/db_schema.py football_advisor/multi_source_coordinator.py football_advisor/feature_builder.py football_advisor/odds_value_engine.py tests/test_db_schema.py tests/test_multi_source_coordinator.py tests/test_feature_builder.py tests/test_system_invariants.py docs/PROGRESS.md
git commit -m "fix: 强制竞彩赔率主数据隔离"
```

---

### 任务 2：修正预测日志、模型核验和投注绩效语义

**文件：**

- 修改：`football_advisor/db_schema.py`
- 修改：`football_advisor/verification_engine.py`
- 修改：`football_advisor/backtest_engine.py`
- 修改：`football_advisor/calibration_engine.py`
- 修改测试：`tests/test_verification_engine.py`
- 修改测试：`tests/test_backtest_loader.py`
- 修改测试：`tests/test_calibration_engine.py`

**步骤 1：把四类语义分列存储**

在 `core.fact_prediction_log` 增加：

```sql
model_predicted_outcome VARCHAR,
model_predicted_probability DOUBLE,
value_market_type VARCHAR,
value_candidate_outcome VARCHAR,
value_candidate_probability DOUBLE,
value_candidate_odds DOUBLE,
value_candidate_edge DOUBLE,
evaluation_semantics_version INT DEFAULT 2
```

现有 `predicted_outcome` 和相关旧列仅做兼容读取，不再写入新记录。

**步骤 2：模型结果按最大模型概率写入**

```python
model_outcome, model_probability = max(
    (("HOME", probs.home_win), ("DRAW", probs.draw), ("AWAY", probs.away_win)),
    key=lambda item: item[1],
)
```

价值候选继续从 `value.best_value` 单独写入，不能覆盖模型结果。

**步骤 3：分开准确率与投注收益**

- 模型准确率：比较 `model_predicted_outcome` 与实际赛果。
- 校准指标：使用三分类完整概率，不使用最佳价值候选。
- 投注收益：只读取 `core.fact_bet_ledger` 中用户实际记录的投注。
- `policy_recommendation = 'NO_BET'` 时，预测日志的 `profit_loss_unit` 必须为 `NULL`。

**步骤 4：隔离旧日志**

历史 `evaluation_semantics_version IS NULL OR < 2` 的记录不得进入新准确率、校准和 ROI 汇总。不得根据旧字段猜测修复历史语义。

**步骤 5：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_verification_engine tests.test_backtest_loader tests.test_calibration_engine tests.test_system_invariants -v
```

**验收：** 同一预测中“模型最可能主胜、最佳价值为客胜、策略 No Bet”时，三者均被正确保存；赛后模型命中按主胜核验，ROI 不产生记录。

**提交：**

```powershell
git add football_advisor/db_schema.py football_advisor/verification_engine.py football_advisor/backtest_engine.py football_advisor/calibration_engine.py tests/test_verification_engine.py tests/test_backtest_loader.py tests/test_calibration_engine.py tests/test_system_invariants.py docs/PROGRESS.md
git commit -m "fix: 分离模型预测价值候选与投注绩效"
```

---

### 任务 3：统一 UTC 时间语义并拒绝未来数据

**文件：**

- 新增：`football_advisor/time_utils.py`
- 修改：`football_advisor/target_sync.py`
- 修改：`football_advisor/feature_builder.py`
- 修改：`football_advisor/sync.py`
- 修改：`football_advisor/db_schema.py`
- 修改测试：`tests/test_target_sync.py`
- 修改测试：`tests/test_feature_builder.py`
- 修改测试：`tests/test_db_schema.py`

**步骤 1：建立唯一时间转换入口**

```python
UTC = timezone.utc

def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

def utc_now() -> datetime:
    return datetime.now(UTC)
```

项目内关键新鲜度判断不得再各自实现 `_as_utc()` 或直接把无时区时间解释为本地时间。

**步骤 2：明确 DuckDB 约定**

现有 `TIMESTAMP` 字段统一存储“UTC 无时区值”，写入前使用 `as_utc(value).replace(tzinfo=None)`；读取后统一附加 UTC。不要在本轮把全库迁移为 `TIMESTAMPTZ`，避免扩大改动范围。

**步骤 3：拒绝未来时间戳**

组件时间满足以下任一条件时状态为 `invalid_future`：

```python
age_minutes < -1.0
```

`invalid_future` 必须进入关键缺口，不能因 `age <= 30` 被判定为 fresh。

**步骤 4：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_target_sync tests.test_feature_builder tests.test_db_schema tests.test_system_invariants -v
```

**验收：** 在 Asia/Shanghai 主机上写入 UTC、UTC 无时区和未来时间，三个模块得到完全一致的新鲜度结论。

**提交：**

```powershell
git add football_advisor/time_utils.py football_advisor/target_sync.py football_advisor/feature_builder.py football_advisor/sync.py football_advisor/db_schema.py tests/test_target_sync.py tests/test_feature_builder.py tests/test_db_schema.py tests/test_system_invariants.py docs/PROGRESS.md
git commit -m "fix: 统一关键数据UTC时间语义"
```

---

### 任务 4：补齐中立场语义并移除世界杯虚假主场优势

**文件：**

- 修改：`football_advisor/db_schema.py`
- 修改：`football_advisor/worldcup_basic_importer.py`
- 修改：`football_advisor/csv_importer.py`
- 修改：`football_advisor/feature_builder.py`
- 修改：`football_advisor/probability_engine.py`
- 修改测试：`tests/test_worldcup_basic_importer.py`
- 修改测试：`tests/test_feature_builder.py`
- 修改测试：`tests/test_core.py`

**步骤 1：增加最小必要赛场字段**

在 `core.fact_match_schedule` 增加：

```sql
is_neutral_venue BOOLEAN DEFAULT FALSE,
venue_country_code VARCHAR
```

本轮不新增完整场馆维表。世界杯赛程导入时明确写入 `is_neutral_venue = TRUE`。

**步骤 2：特征层显式输出主场优势**

```python
home_advantage = 0.0 if is_neutral_venue else configured_home_advantage
```

将 `is_neutral_venue`、`venue_country_code`、`home_advantage` 写入 `features.context`。

**步骤 3：概率引擎只使用特征中的主场优势**

删除概率引擎内部无条件 `+65 Elo` 或等价默认加成，避免特征层和概率层重复或冲突。

**步骤 4：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_worldcup_basic_importer tests.test_feature_builder tests.test_core tests.test_system_invariants -v
```

**验收：** 同一组球队和统计只切换 `is_neutral_venue` 时，中立场不包含主场加成；非中立场保持当前配置行为。

**提交：**

```powershell
git add football_advisor/db_schema.py football_advisor/worldcup_basic_importer.py football_advisor/csv_importer.py football_advisor/feature_builder.py football_advisor/probability_engine.py tests/test_worldcup_basic_importer.py tests/test_feature_builder.py tests/test_core.py tests/test_system_invariants.py docs/PROGRESS.md
git commit -m "fix: 补齐中立场与主场优势语义"
```

---

### 任务 5：让阵容伤停和新闻就绪只依赖真实组件证据

**文件：**

- 修改：`football_advisor/db_schema.py`
- 修改：`football_advisor/target_sync.py`
- 修改：`football_advisor/api_football_client.py`
- 修改：`football_advisor/news_ingestion.py`
- 修改：`football_advisor/feature_enhancers.py`
- 修改测试：`tests/test_target_sync.py`
- 修改测试：`tests/test_api_football_sync.py`
- 修改测试：`tests/test_news_ingestion.py`

**步骤 1：删除通用时间兜底**

阵容伤停就绪不得再使用：

```sql
COALESCE(injury_data_updated_at, updated_at)
```

改为要求以下证据至少一项存在，并且 provider 比赛、球队映射完整：

- `staging.stg_injuries` 中目标比赛有效记录；
- `staging.stg_lineups` 中目标比赛有效记录；
- `core.fact_match_context_summary.injury_data_updated_at` 非空，且对应来源和映射质量合格。

先新增组件证据表，供就绪判断和后续缓存共同使用：

```sql
CREATE TABLE IF NOT EXISTS core.fact_target_sync_state (
    match_id VARCHAR NOT NULL,
    component VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    last_attempt_at TIMESTAMP,
    last_success_at TIMESTAMP,
    source_updated_at TIMESTAMP,
    status VARCHAR NOT NULL,
    row_count INT DEFAULT 0,
    empty_confirmed BOOLEAN DEFAULT FALSE,
    error_code VARCHAR,
    PRIMARY KEY (match_id, component, provider)
);
```

如果供应商明确返回“当前无伤停”，必须写入 `status = 'success'`、`row_count = 0`、`empty_confirmed = TRUE` 的组件证据，不能用零行或通用上下文更新时间表示成功。

**步骤 2：新闻就绪要求目标比赛证据**

`fact_news_signal_summary.updated_at` 不能替代 `news_last_updated_at`。空搜索必须记录 `empty_search`，并保持关键组件缺失，不得生成空摘要冒充成功。

**步骤 3：增强器不再吞掉组件失败**

可选增强器失败可以继续基础预测，但必须写入：

```python
context["enhancer_status"][component] = {
    "status": "failed",
    "error_type": type(exc).__name__,
}
```

关键增强器失败必须进入 readiness 和 No Bet 原因。

**步骤 4：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_target_sync tests.test_api_football_sync tests.test_news_ingestion tests.test_feature_enhancers tests.test_system_invariants -v
```

**验收：** 只更新 `fact_match_context_summary.updated_at` 时，阵容伤停仍为 missing；只有真实行或明确空结果证据才可判定新鲜。

**提交：**

```powershell
git add football_advisor/db_schema.py football_advisor/target_sync.py football_advisor/api_football_client.py football_advisor/news_ingestion.py football_advisor/feature_enhancers.py tests/test_target_sync.py tests/test_api_football_sync.py tests/test_news_ingestion.py tests/test_feature_enhancers.py tests/test_system_invariants.py docs/PROGRESS.md
git commit -m "fix: 以组件事实证明阵容伤停与新闻就绪"
```

---

### 任务 6：真正落实“模型只写叙述，代码渲染事实”

**文件：**

- 修改：`football_advisor/models.py`
- 修改：`football_advisor/report.py`
- 修改：`football_advisor/report_generator.py`
- 修改：`football_advisor/pipeline.py`
- 修改测试：`tests/test_report.py`
- 修改测试：`tests/test_report_generator.py`
- 修改测试：`tests/test_pipeline.py`

**步骤 1：报告模型只返回结构化叙述**

外部模型和 Qwen 的响应必须解析为：

```json
{
  "key_factors": "...",
  "main_risks": "...",
  "reasoning_summary": "..."
}
```

任何额外字段、Markdown 整篇报告或解析失败均判定该模型结果无效，进入下一级回退。

**步骤 2：由 `ReportBuilder` 生成最终 Markdown**

新增或调整为：

```python
def build_markdown(
    self,
    bundle: PredictionBundle,
    narrative: ReportNarrative | None = None,
) -> str:
    ...
```

比赛、概率、竞彩五玩法赔率、价值差、No Bet、就绪状态、来源和更新时间全部从 `PredictionBundle` 确定性渲染。

**步骤 3：校验叙述边界**

叙述中出现以下内容时拒绝模型结果并回退到代码摘要：

- 不在比赛实体集合中的球队或球员；
- 不在证据数字集合中的百分比、赔率、比分或时间；
- 与 `policy.recommendation` 冲突的投注结论；
- 声称已开售但官方状态为 `NOT_ON_SALE` 的玩法。

实现时使用结构化解析和数字 token 比对，不使用仅靠字符串 `pass` 的伪校验。

**步骤 4：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_report tests.test_report_generator tests.test_pipeline tests.test_system_invariants -v
```

**验收：** 注入一份包含虚假 72%、虚假比分 3:0 和错误“建议投注”的模型响应，最终报告不得出现这些内容，且事实段与 `PredictionBundle` 完全一致。

**提交：**

```powershell
git add football_advisor/models.py football_advisor/report.py football_advisor/report_generator.py football_advisor/pipeline.py tests/test_report.py tests/test_report_generator.py tests/test_pipeline.py tests/test_system_invariants.py docs/PROGRESS.md
git commit -m "fix: 限制大模型仅生成报告叙述"
```

---

### 任务 7：去除单次预测重复同步并落实 30 分钟缓存

**文件：**

- 修改：`football_advisor/db_schema.py`
- 修改：`football_advisor/target_sync.py`
- 修改：`football_advisor/sync.py`
- 修改：`football_advisor/multi_source_coordinator.py`
- 修改测试：`tests/test_target_sync.py`
- 修改测试：`tests/test_multi_source_coordinator.py`
- 修改测试：`tests/test_pipeline.py`

**步骤 1：复用目标比赛组件证据表**

复用任务 5 建立的 `core.fact_target_sync_state`。缓存命中必须同时满足：状态成功、来源与当前组件一致、组件证据有效、`last_success_at` 不超过 30 分钟。

**步骤 2：预测开始先判断缓存**

组件存在合格数据且 `last_success_at` 不超过 30 分钟时返回 `fresh_cache`，不得调用外部服务。

**步骤 3：单次调用图去重**

`TargetMatchSyncService` 负责竞彩后，`DataSyncCoordinator`/`MultiSourceCoordinator` 本次调用必须显式排除 Sporttery。使用本次请求内的 `attempted_components` 集合保证每个组件最多执行一次。

**步骤 4：失败缓存不能伪装成功**

失败状态可以短时间抑制重试风暴，但返回值必须是 `recent_failure`，并继续触发 No Bet；不得返回 `fresh_cache`。

**步骤 5：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_target_sync tests.test_multi_source_coordinator tests.test_pipeline tests.test_system_invariants -v
```

**验收：** 冷启动每个外部组件调用一次；立即重复预测调用零次并返回 `fresh_cache`；缓存过期后重新调用一次；失败缓存保持失败语义。

**提交：**

```powershell
git add football_advisor/db_schema.py football_advisor/target_sync.py football_advisor/sync.py football_advisor/multi_source_coordinator.py tests/test_target_sync.py tests/test_multi_source_coordinator.py tests/test_pipeline.py tests/test_system_invariants.py docs/PROGRESS.md
git commit -m "fix: 去重目标同步并落实三十分钟缓存"
```

---

### 任务 8：隔离赔率趋势的玩法和供应商

**文件：**

- 修改：`football_advisor/feature_enhancers.py`
- 修改测试：`tests/test_feature_enhancers.py`

**步骤 1：限定单一可比序列**

官方竞彩赔率趋势查询至少按以下条件分组和过滤：

```sql
WHERE match_id = ?
  AND odds_type = '1X2'
  AND source_provider = 'SportteryOfficialWeb'
  AND bookmaker_name = 'SportteryOfficialWeb'
```

官方竞彩趋势和第三方市场趋势分别计算，不能把不同玩法、不同供应商的最早与最新快照拼成一条趋势。

**步骤 2：无可比序列时返回明确状态**

少于两个同源同玩法快照时：

```python
context["odds_trend_status"] = "insufficient_comparable_snapshots"
```

不得返回伪趋势，也不得静默 `None`。

**步骤 3：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_feature_enhancers tests.test_system_invariants -v
```

**验收：** 混入更新的 TOTAL_GOALS、HANDICAP 或其他 bookmaker 后，竞彩 SPF 趋势结果不变化。

**提交：**

```powershell
git add football_advisor/feature_enhancers.py tests/test_feature_enhancers.py tests/test_system_invariants.py docs/PROGRESS.md
git commit -m "fix: 隔离赔率趋势的玩法与来源"
```

---

### 任务 9：按预测价值补齐目标数据，不扩大采集范围

**文件：**

- 修改：`football_advisor/worldcup_basic_importer.py`
- 修改：`football_advisor/api_football_client.py`
- 修改：`football_advisor/news_ingestion.py`
- 修改：`football_advisor/target_sync.py`
- 修改：`football_advisor/no_bet_policy.py`
- 修改测试：对应 importer、sync、news 和 policy 测试

**步骤 1：立即接入的高价值数据**

- 中立场标记：进入基础概率。
- 竞彩五玩法：进入官方价值评估。
- 阵容、伤停、停赛：只进入风险和报告；校准批准前不改概率。
- 目标比赛新闻：只进入风险和叙述，不覆盖结构化事实。
- 天气：有可信结构化来源时进入风险和报告；缺失保持 `missing_optional`。
- 裁判：赛程存在明确 `referee_id` 后才读取画像；未指派不得按联赛或名称猜测。

**步骤 2：保持离线的数据**

以下数据不进入在线热路径：

- `fact_match_events_unified` 事件级数据；
- 球员详细身价；
- 未映射球员名单；
- 未校准的阵型、积分榜、H2H 概率修正；
- 用户未明确记录的模拟投注。

**步骤 3：停止无消费者采集**

任何采集器在没有明确消费者、来源映射、质量门禁和测试前默认不由预测流程调用。不要删除已有离线表，只停止在线同步。

**步骤 4：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_worldcup_basic_importer tests.test_api_football_sync tests.test_news_ingestion tests.test_target_sync tests.test_core -v
```

**验收：** 每个在线采集字段在代码中能追踪到唯一消费者；每个在线消费者都能追踪到来源、时间和质量状态。

**提交：**

```powershell
git add football_advisor/worldcup_basic_importer.py football_advisor/api_football_client.py football_advisor/news_ingestion.py football_advisor/target_sync.py football_advisor/no_bet_policy.py tests/test_worldcup_basic_importer.py tests/test_api_football_sync.py tests/test_news_ingestion.py tests/test_target_sync.py tests/test_core.py tests/test_system_invariants.py docs/PROGRESS.md
git commit -m "fix: 收敛高价值数据采集与消费边界"
```

---

### 任务 10：重写验收脚本的结论模型

**文件：**

- 修改：`scripts/walkthrough_e2e.py`
- 修改：`tests/test_delivery_readiness.py`
- 修改：`tests/test_smoke_test_worldcup_readiness.py`

**步骤 1：定义互斥结论**

脚本最终只能返回：

```text
PRODUCTION_READY
SAFE_DEGRADED
EXTERNAL_BLOCKED
FAILED
```

- `PRODUCTION_READY`：实时关键数据成功，缓存复用成功，四场完整流程成功。
- `SAFE_DEGRADED`：关键数据缺失时明确 No Bet，未伪造事实，安全门禁有效。
- `EXTERNAL_BLOCKED`：代码链路正常，但真实供应商未配置、未开售、订阅限制或网络不可用。
- `FAILED`：代码异常、数据串场、错误来源替代、日志失败继续、未来数据误判等内部缺陷。

`SAFE_DEGRADED` 和 `EXTERNAL_BLOCKED` 均不得在文档中写成生产完成。

**步骤 2：增加三种运行模式**

```powershell
.\.runtime\python\python.exe scripts\walkthrough_e2e.py --mode fixture --db-path tmp\closure_fixture.duckdb
.\.runtime\python\python.exe scripts\walkthrough_e2e.py --mode live --db-path tmp\closure_live.duckdb
.\.runtime\python\python.exe scripts\walkthrough_e2e.py --mode cache --db-path tmp\closure_live.duckdb
```

- `fixture`：确定性数据验证内部业务不变量。
- `live`：验证真实供应商可用性。
- `cache`：紧接 live 再执行，验证外部组件零重复调用和小于 30 秒。

**步骤 3：保存机器可读证据**

在 `tmp/` 输出 JSON，至少包含：

```text
match_id
component_statuses
provider_call_counts
sporttery_market_statuses
readiness
policy_recommendation
report_provider
duration_seconds
final_status
```

不得输出密钥、令牌、Cookie 或完整请求头。

**步骤 4：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_delivery_readiness tests.test_smoke_test_worldcup_readiness tests.test_system_invariants -v
```

**验收：** 模拟实时供应商为空时结果为 `SAFE_DEGRADED` 或 `EXTERNAL_BLOCKED`，不能是 `PRODUCTION_READY`；构造第三方替代竞彩时必须为 `FAILED`。

**提交：**

```powershell
git add scripts/walkthrough_e2e.py tests/test_delivery_readiness.py tests/test_smoke_test_worldcup_readiness.py tests/test_system_invariants.py docs/PROGRESS.md
git commit -m "test: 区分生产就绪安全降级与外部阻塞"
```

---

### 任务 11：执行四场 ChatGPT 对话注入穿测

**文件：**

- 修改：`scripts/walkthrough_e2e.py`
- 修改：`tests/test_pipeline.py`
- 修改：`tests/test_report.py`

**步骤 1：固定穿测边界**

四场比赛：

```text
卡塔尔 vs 瑞士
巴西 vs 摩洛哥
海地 vs 苏格兰
澳大利亚 vs 土耳其
```

完整执行：请求解析 → 同步 → DuckDB/ChromaDB 分查 → 特征 → 概率 → 价值 → No Bet → 预测日志 → 差异分析 → 报告。

仅替换报告叙述生成器。ChatGPT 对话返回值必须先转换为 `ReportNarrative`，不能直接替换最终 Markdown，更不能修改概率、价值、No Bet 或就绪结果。

**步骤 2：逐场核对**

每场必须检查：

- 标准 `match_id` 和球队映射唯一；
- 中立场标记正确；
- 竞彩五玩法均为 `OPEN` 或 `NOT_ON_SALE`；
- 第三方赔率没有替代竞彩；
- 概率和期望进球由引擎产生，报告注入前后完全一致；
- 日志同时保存模型结果和价值候选；
- No Bet 不产生模拟盈亏；
- 报告没有新增证据外数字；
- 实时失败时结论不是 `PRODUCTION_READY`。

**步骤 3：性能检查**

- 冷启动单场不超过 120 秒；
- 30 分钟内缓存命中单场不超过 30 秒；
- 同一预测内每个外部组件最多调用一次；
- 新闻阶段不调用 Ollama/Qwen；
- 报告阶段每个模型最多调用一次。

**验收：** 四场均有独立 JSON 证据，且最终状态与实际数据可用性一致。

**提交：**

```powershell
git add scripts/walkthrough_e2e.py tests/test_pipeline.py tests/test_report.py docs/PROGRESS.md
git commit -m "test: 完成四场对话注入系统不变量穿测"
```

---

### 任务 12：全量验证、CodeRabbit 复核和文档闭环

**文件：**

- 修改：`docs/ARCHITECTURE.md`
- 修改：`docs/PROJECT_REQUIREMENTS.md`
- 修改：`docs/PROGRESS.md`
- 更新：本计划复选状态和反向验收结论

**步骤 1：运行静态和全量测试**

```powershell
$env:PYTHONPYCACHEPREFIX='tmp\compile_cache'
.\.runtime\python\python.exe -m compileall -q football_advisor scripts tests
.\.runtime\python\python.exe -m unittest discover -s tests -v
git diff --check
```

**步骤 2：运行三类验收**

```powershell
.\.runtime\python\python.exe scripts\walkthrough_e2e.py --mode fixture --db-path tmp\closure_fixture.duckdb
.\.runtime\python\python.exe scripts\walkthrough_e2e.py --mode live --db-path tmp\closure_live.duckdb
.\.runtime\python\python.exe scripts\walkthrough_e2e.py --mode cache --db-path tmp\closure_live.duckdb
```

**步骤 3：运行 CodeRabbit**

从仓库根目录执行全范围或按模块分批复核。发现问题后必须逐项判断是否成立，不得自动照单修改。至少复核：

- `football_advisor/db_schema.py`
- `football_advisor/target_sync.py`
- `football_advisor/feature_builder.py`
- `football_advisor/probability_engine.py`
- `football_advisor/verification_engine.py`
- `football_advisor/report.py`
- `football_advisor/report_generator.py`
- `football_advisor/multi_source_coordinator.py`
- `scripts/walkthrough_e2e.py`

**步骤 4：文档只记录可证明结论**

`docs/PROGRESS.md` 必须分别记录：

- 已修复的内部代码问题；
- fixture 验收结果；
- live 验收结果；
- cache 验收结果；
- 外部供应商阻塞；
- 尚未取得的数据；
- 当前最终状态。

只有 live 结果为 `PRODUCTION_READY` 且 cache 验收通过时，才能写“生产闭环完成”。否则必须写 `SAFE_DEGRADED` 或 `EXTERNAL_BLOCKED`。

**步骤 5：最终提交**

```powershell
git add docs/ARCHITECTURE.md docs/PROJECT_REQUIREMENTS.md docs/PROGRESS.md docs/superpowers/plans/2026-06-13-football-advisor-system-invariant-closure-plan.md
git commit -m "docs: 完成系统不变量收口验收记录"
```

---

## 五、最终完成定义

必须同时满足以下条件，才允许声明本轮完成：

1. 十条系统不变量全部有失败测试和通过证据。
2. 竞彩五玩法只使用 `SportteryOfficialWeb`，第三方赔率只做交叉校验。
3. 中立场不使用主场优势。
4. 关键时间统一为 UTC，未来时间戳触发失败。
5. 阵容伤停和新闻就绪由真实组件证据证明。
6. 模型结果、价值候选、No Bet 和真实投注语义分离。
7. No Bet 不产生虚构收益。
8. LLM 只生成 `ReportNarrative`，最终报告由代码渲染。
9. 单次预测每个外部组件最多调用一次，30 分钟缓存可证明生效。
10. 四场对话注入穿测均完成，且报告注入不改变结构化结果。
11. 全量测试、编译、差异检查和 CodeRabbit 复核完成。
12. 文档结论与 live 验收状态一致，不再把安全降级写成生产就绪。

---

## 六、执行纪律与资源上限

- 单个任务执行上限：18,000 Token；超限时停止当前任务并明确报告卡点，不跨任务继续。
- 每个任务单独提交，不再使用一个大提交覆盖多个业务任务。
- 每个任务结束立即更新本计划对应状态和 `docs/PROGRESS.md`，不得等到最后批量补记。
- 若实时供应商不可用，不修改概率、阈值或测试数据追求“看起来通过”；状态保持 `EXTERNAL_BLOCKED`。
- 不新增投注平台、不新增模型框架、不新增数据库、不重写现有主流程。
- 不删除离线数据表，只收敛在线采集和消费边界。
