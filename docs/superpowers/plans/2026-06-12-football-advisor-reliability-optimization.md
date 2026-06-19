# 足球预测可靠性与按需执行优化实施计划

> **供执行代理使用：** 必须逐任务实施并在任务间复核；推荐使用 `superpowers:subagent-driven-development`，也可使用 `superpowers:executing-plans`。所有步骤使用复选框跟踪。

**目标：** 修复比赛实体、竞彩赔率、新闻和报告链路的正确性问题，并将预测流程改造成适合“仅预测时开机”的按需同步与启动补偿模式。

**架构：** 以标准 `match_id` 作为所有结构化数据和文本数据的唯一比赛主键。预测请求只阻塞执行目标比赛关键同步；赛后采集、历史核验和反馈分析由持久化任务状态控制，在预测响应后或手工命令中补偿执行。所有关键失败进入明确错误或 No Bet，不允许用静默降级伪装成功。

**技术栈：** Python 3.14、DuckDB、ChromaDB、FastAPI、Ollama、OpenAI-compatible API、`unittest`。

---

## 一、范围与硬约束

本计划只处理以下范围：

1. 标准比赛与竞彩比赛实体统一。
2. 竞彩五种玩法统一绑定标准比赛。
3. 预测链路改为目标比赛按需同步。
4. 关机期间遗漏任务的启动补偿。
5. 新闻按比赛过滤并修复空结果误报。
6. 外部模型、Qwen 备用模型和固定模板的正确回退。
7. 报告事实约束、抓取安全与异常可观测性。
8. 移除未经校准的循环特征，建立概率校准门禁。
9. 决定已采集未使用数据的去留和接入顺序。

本轮不做：

- 不增加新的比赛或赔率供应商。
- 不引入消息队列、常驻任务服务或云端调度。
- 不自动下注，不改变 No Bet 的安全定位。
- 不直接让 LLM 修改概率、价值差或投注建议。
- 不在同一实施阶段同时重构全部数据库和全部采集器。

每个阶段单独执行、单独验收。单次实施任务建议控制在 25,000 Token 内；达到上限时停在已通过测试的阶段边界，不跨阶段留下半成品。

## 二、目标数据流

```text
OpenWebUI / FastAPI
  -> 解析比赛
  -> CanonicalMatchResolver 确认唯一标准 match_id
  -> TargetMatchSyncService 同步目标比赛关键数据
       -> Sporttery 五玩法
       -> 目标球队统计、伤停、阵容、天气
       -> 目标比赛新闻
  -> DataReadinessGate 检查映射、完整性和 30 分钟时效
  -> DuckDBFeatureBuilder
  -> ProbabilityEngine
  -> OddsValueEngine
  -> NoBetPolicy
  -> PredictionLogger
  -> ReportGenerator（外部模型 -> Qwen -> 固定模板）
  -> OpenWebUI 返回报告
  -> MaintenanceRunner 响应后执行到期维护任务
```

维护任务顺序固定为：

```text
赛后补采
-> 受影响球队滚动统计刷新
-> 待验证预测核验
-> 投注台账结算
-> 有新增验证样本时执行反馈统计
-> 更新任务游标与状态
```

## 三、文件结构

计划新增：

- `football_advisor/match_resolver.py`：标准比赛解析和 provider 比赛映射。
- `football_advisor/target_sync.py`：目标比赛关键数据同步。
- `football_advisor/job_runner.py`：到期维护任务和持久化状态。
- `football_advisor/jobs.py`：维护任务命令行入口。
- `football_advisor/report_generator.py`：报告生成器协议和模型回退编排。
- `football_advisor/url_safety.py`：抓取 URL、DNS 和重定向安全校验。
- `scripts/reconcile_sporttery_matches.py`：历史 `M_ST_*` 重复比赛迁移工具。
- `tests/test_match_resolver.py`
- `tests/test_target_sync.py`
- `tests/test_job_runner.py`
- `tests/test_report_generator.py`
- `tests/test_url_safety.py`
- `tests/test_prediction_components.py`

计划修改：

- `football_advisor/db_schema.py`
- `football_advisor/sporttery_client.py`
- `football_advisor/feature_builder.py`
- `football_advisor/sync.py`
- `football_advisor/pipeline.py`
- `football_advisor/news_ingestion.py`
- `football_advisor/news_tools.py`
- `football_advisor/llm_router.py`
- `football_advisor/report.py`
- `football_advisor/text_fetcher.py`
- `football_advisor/api.py`
- `football_advisor/verification_engine.py`
- `football_advisor/feature_enhancers.py`
- `football_advisor/probability_engine.py`
- `football_advisor/no_bet_policy.py`
- `football_advisor/config.py`
- `data/provider_mappings/provider_league_mappings.csv`
- `data/provider_mappings/provider_team_mappings.csv`
- 对应现有测试文件
- `docs/ARCHITECTURE.md`
- `docs/PROGRESS.md`

---

## 阶段一：修复比赛身份和竞彩五玩法融合

### 任务 1：建立 provider 比赛到标准比赛的显式映射

**文件：**

- 修改：`football_advisor/db_schema.py`
- 新增：`football_advisor/match_resolver.py`
- 新增测试：`tests/test_match_resolver.py`

- [ ] **步骤 1：先写失败测试**

覆盖以下行为：

```python
def test_resolves_sporttery_match_to_existing_world_cup_match():
    resolved = resolver.resolve(
        provider_name="SportteryOfficialWeb",
        provider_match_id="2040164",
        provider_league_id="世界杯",
        provider_home_team_id="加拿大",
        provider_away_team_id="波黑",
        kickoff_time=datetime(2026, 6, 13, 3, 0),
    )
    assert resolved.system_match_id == "WC2026_M003"
    assert resolved.resolution_method == "mapped_teams_and_time"


def test_rejects_unmapped_provider_team_instead_of_using_raw_name():
    with self.assertRaises(MatchResolutionError):
        resolver.resolve(
            provider_name="SportteryOfficialWeb",
            provider_match_id="unknown",
            provider_league_id="世界杯",
            provider_home_team_id="未映射球队",
            provider_away_team_id="波黑",
            kickoff_time=datetime(2026, 6, 13, 3, 0),
        )
```

- [ ] **步骤 2：运行测试并确认失败**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_match_resolver -v
```

预期：因映射表和解析器尚不存在而失败。

- [ ] **步骤 3：新增显式比赛映射表**

在 `db_schema.py` 中加入：

```sql
CREATE TABLE IF NOT EXISTS core.dim_provider_match_mapping (
    provider_name VARCHAR NOT NULL,
    provider_match_id VARCHAR NOT NULL,
    system_match_id VARCHAR NOT NULL,
    resolution_method VARCHAR NOT NULL,
    resolution_confidence DOUBLE NOT NULL,
    verified_at TIMESTAMP NOT NULL,
    PRIMARY KEY (provider_name, provider_match_id)
);
```

解析规则固定为：

1. 已存在 provider 比赛映射时直接使用。
2. 否则必须先完成联赛、主队、客队三项 provider 映射。
3. 在 `core.fact_match_schedule` 中按标准联赛、标准主客队和开赛时间正负 12 小时匹配。
4. 唯一命中时写入 provider 比赛映射。
5. 零命中或多命中时明确报错，不创建原始中文 ID 的核心比赛。

- [ ] **步骤 4：补齐四队和世界杯的 Sporttery 映射**

在映射 CSV 中加入中国竞彩网的：

- `世界杯`
- `加拿大`
- `波黑`
- `美国`
- `巴拉圭`

系统球队 ID 必须复用现有 `WC_TEAM_*`，不得创建第二套 ID。

- [ ] **步骤 5：运行测试并确认通过**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_match_resolver tests.test_provider_mapping_importer -v
```

- [ ] **步骤 6：阶段提交**

```powershell
git add football_advisor/db_schema.py football_advisor/match_resolver.py tests/test_match_resolver.py data/provider_mappings/provider_league_mappings.csv data/provider_mappings/provider_team_mappings.csv
git commit -m "fix: enforce canonical Sporttery match mapping"
```

### 任务 2：禁止 Sporttery 原始 ID 绕过映射

**文件：**

- 修改：`football_advisor/sporttery_client.py:385`
- 修改测试：`tests/test_sporttery_client.py`

- [ ] **步骤 1：新增回归测试**

测试必须确认：

```python
assert "M_ST_2040164" not in core_match_ids
assert "WC2026_M003" in core_match_ids
assert canada_bosnia_odds_match_id == "WC2026_M003"
```

另加未映射测试：未映射比赛只保留在 staging，同步结果包含 `skipped_unmapped_count=1`，核心表不得出现原始中文球队 ID。

- [ ] **步骤 2：确认测试失败**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_sporttery_client -v
```

- [ ] **步骤 3：最小修改合并 SQL**

删除以下回退：

```sql
COALESCE(lm.system_league_id, s.provider_league_id)
COALESCE(hm.system_team_id, s.provider_home_team_id)
COALESCE(am.system_team_id, s.provider_away_team_id)
```

`_merge_schedule()` 和 `_merge_odds()` 必须通过 `core.dim_provider_match_mapping` 取得 `system_match_id`。未映射记录不进入 `core.*`。

- [ ] **步骤 4：修复竞彩明细赔率静默失败**

`staging.stg_sporttery_odds_detail` 不存在或写入失败时抛出带上下文的同步错误；不得继续返回成功。

- [ ] **步骤 5：增加确定性空响应重试**

中国竞彩网请求在 HTTP 成功但解析结果为空时，按 `0.5 秒 -> 1.0 秒` 最多重试两次，不使用随机退避。第三次仍为空时返回 `empty_after_retries`，并让目标同步进入关键数据失败和 No Bet。测试使用依次返回“空、完整数据”的 fake transport，确认第二次成功；另测三次为空时明确失败。

- [ ] **步骤 6：运行定向测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_sporttery_client tests.test_sync_sporttery_odds -v
```

- [ ] **步骤 7：阶段提交**

```powershell
git add football_advisor/sporttery_client.py tests/test_sporttery_client.py tests/test_sync_sporttery_odds.py
git commit -m "fix: merge Sporttery data through canonical match ids"
```

### 任务 3：将竞彩详细玩法从 staging 合并到核心事实表

**文件：**

- 修改：`football_advisor/db_schema.py`
- 修改：`football_advisor/sporttery_client.py`
- 修改：`football_advisor/feature_builder.py:106`
- 修改测试：`tests/test_feature_builder.py`

- [ ] **步骤 1：写失败测试**

```python
def test_standard_world_cup_match_loads_all_sporttery_markets():
    features = builder.build(MatchRequest(query="加拿大 波黑", match_id="WC2026_M003"))
    assert features.odds_1x2
    assert features.odds_rqspf
    assert features.odds_correct_score
    assert features.odds_total_goals
    assert features.odds_half_full
```

- [ ] **步骤 2：新增核心明细赔率表**

```sql
CREATE TABLE IF NOT EXISTS core.fact_sporttery_odds_detail (
    match_id VARCHAR NOT NULL,
    odds_type VARCHAR NOT NULL,
    outcome_key VARCHAR NOT NULL,
    odds_value DOUBLE NOT NULL,
    snapshot_time TIMESTAMP NOT NULL,
    source_provider VARCHAR NOT NULL,
    PRIMARY KEY (match_id, odds_type, outcome_key, snapshot_time)
);
```

- [ ] **步骤 3：合并详细赔率**

使用 `dim_provider_match_mapping` 把 `provider_match_id` 转为标准 `match_id` 后写入核心事实表。

- [ ] **步骤 4：移除 `M_ST_` 前缀耦合**

`DuckDBFeatureBuilder._sporttery_detail_odds()` 改为直接按标准 `match_id` 查询核心事实表，不再解析 provider ID。

- [ ] **步骤 5：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_feature_builder tests.test_sporttery_client -v
```

- [ ] **步骤 6：阶段提交**

```powershell
git add football_advisor/db_schema.py football_advisor/sporttery_client.py football_advisor/feature_builder.py tests/test_feature_builder.py tests/test_sporttery_client.py
git commit -m "feat: bind all Sporttery markets to canonical matches"
```

### 任务 4：迁移历史重复比赛

**文件：**

- 新增：`scripts/reconcile_sporttery_matches.py`
- 新增测试：`tests/test_reconcile_sporttery_matches.py`

- [ ] **步骤 1：写事务迁移测试**

验证 `M_ST_2040164 -> WC2026_M003`、`M_ST_2040165 -> WC2026_M004` 后：

- 赔率全部迁移。
- 重复赛程被删除。
- 标准比赛特征仍保留。
- 重复执行结果不变。
- 任一步失败时事务回滚。

- [ ] **步骤 2：实现 `--dry-run` 和显式 `--db-path`**

命令必须拒绝缺少 `--db-path`，避免再次误操作默认生产库：

```powershell
.\.runtime\python\python.exe scripts\reconcile_sporttery_matches.py --db-path football_system.db --dry-run
```

- [ ] **步骤 3：先在临时数据库执行，再执行正式库**

正式执行前必须输出待迁移比赛、赔率行数和冲突数，但不能输出敏感配置。

---

## 阶段二：预测链路瘦身与关机补偿

### 任务 5：新增目标比赛同步服务

**文件：**

- 新增：`football_advisor/target_sync.py`
- 修改：`football_advisor/sync.py:288`
- 新增测试：`tests/test_target_sync.py`

- [ ] **步骤 1：定义结果契约并写失败测试**

```python
@dataclass(frozen=True)
class TargetSyncResult:
    match_id: str
    critical_results: tuple[SyncResult, ...]
    optional_results: tuple[SyncResult, ...]
    completed_at: datetime

    @property
    def critical_ok(self) -> bool:
        return all(result.status in {"success", "fresh_cache"} for result in self.critical_results)
```

测试必须确认预测前不会调用 `sync_pre_match_results()`、`sync_post_match_results()`、全库滚动刷新或历史核验。

- [ ] **步骤 2：实现目标同步顺序**

固定顺序：

1. 标准比赛解析。
2. 中国竞彩五玩法。
3. 目标比赛结构化数据。
4. 目标比赛新闻。
5. 数据新鲜度复查。

资金流未配置时是可选缺失，不得伪装成功，也不得单独阻断预测。

- [ ] **步骤 3：按组件独立判断新鲜度**

新增 `DataReadinessGate`，分别检查赛程状态、官方竞彩赔率、球队统计、伤停/阵容、新闻摘要的更新时间。不得继续用一个 `LEAST(...)` 时间戳把静态赛程日期和实时赔率混成单一年龄。任何必需组件超过 30 分钟或缺失时记录具体组件并进入 No Bet；天气、交易所资金流等可选组件缺失只提升风险并写入报告。

- [ ] **步骤 4：修改 `sync_before_prediction()`**

仅委托 `TargetMatchSyncService`。保留旧的赛前和赛后方法供维护任务调用，不删除采集器。

- [ ] **步骤 5：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_target_sync tests.test_pipeline tests.test_external_sync -v
```

- [ ] **步骤 6：阶段提交**

```powershell
git add football_advisor/target_sync.py football_advisor/sync.py tests/test_target_sync.py tests/test_pipeline.py
git commit -m "refactor: limit prediction sync to target match"
```

### 任务 6：建立持久化任务状态和启动补偿

**文件：**

- 修改：`football_advisor/db_schema.py`
- 新增：`football_advisor/job_runner.py`
- 新增：`football_advisor/jobs.py`
- 新增测试：`tests/test_job_runner.py`

- [ ] **步骤 1：扩展任务状态表**

保留现有 `last_collected_date`，新增：

```sql
ALTER TABLE core.dim_collection_state ADD last_success_at TIMESTAMP;
ALTER TABLE core.dim_collection_state ADD cursor_value VARCHAR;
ALTER TABLE core.dim_collection_state ADD status VARCHAR DEFAULT 'IDLE';
ALTER TABLE core.dim_collection_state ADD failure_count INT DEFAULT 0;
ALTER TABLE core.dim_collection_state ADD last_error VARCHAR;
ALTER TABLE core.dim_collection_state ADD next_due_at TIMESTAMP;
ALTER TABLE core.dim_collection_state ADD started_at TIMESTAMP;
```

迁移必须先检查列是否存在；不能通过 `except Exception: pass` 吞掉错误。

- [ ] **步骤 2：定义固定任务**

```python
JOB_ORDER = (
    "post_match_collection",
    "rolling_stats_refresh",
    "prediction_verification",
    "bet_settlement",
    "feedback_analysis",
    "pre_match_warmup",
)
```

任务串行执行，状态转换限定为 `IDLE -> RUNNING -> SUCCESS/FAILED`。发现遗留 `RUNNING` 时视为上次关机中断，重新执行该任务。

- [ ] **步骤 3：实现幂等游标**

赛后采集按最后成功日期继续；滚动统计只刷新本轮受影响球队；反馈分析只在新增已验证预测时运行。

- [ ] **步骤 4：实现 CLI**

```powershell
.\.runtime\python\python.exe -m football_advisor.jobs status
.\.runtime\python\python.exe -m football_advisor.jobs run-due
.\.runtime\python\python.exe -m football_advisor.jobs run post-match
.\.runtime\python\python.exe -m football_advisor.jobs run pre-match --days 2
```

所有命令支持 `--db-path`；修改数据库的命令输出任务名、状态、处理游标和脱敏错误。

- [ ] **步骤 5：测试关机恢复**

测试先写入 `RUNNING` 状态，再重新创建 Runner，确认任务被重新执行且数据不重复。

- [ ] **步骤 6：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_job_runner tests.test_collection_regressions -v
```

- [ ] **步骤 7：阶段提交**

```powershell
git add football_advisor/db_schema.py football_advisor/job_runner.py football_advisor/jobs.py tests/test_job_runner.py
git commit -m "feat: add resumable maintenance jobs"
```

### 任务 7：预测响应后执行到期维护任务

**文件：**

- 修改：`football_advisor/api.py`
- 修改：`football_advisor/pipeline.py`
- 修改测试：`tests/test_api.py`、`tests/test_pipeline.py`

- [ ] **步骤 1：从 `PredictionPipeline.predict()` 移出维护行为**

删除或迁移：

- `verify_pending_predictions()`
- 每次预测执行的 `feedback_engine.analyze()`
- 全局赛前、赛后采集

预测日志保留在预测链路；差异分析只在日志写入成功后执行。

- [ ] **步骤 2：修复日志失败假成功**

`PredictionLogger.log_prediction()` 写入失败必须抛出 `PredictionLoggingError`，不得返回未落库的 `prediction_id`。

- [ ] **步骤 3：使用 FastAPI `BackgroundTasks`**

预测报告先返回，再调用 `MaintenanceRunner.run_due()`。后台任务失败只更新任务状态和日志，不修改已返回的预测结果。

- [ ] **步骤 4：测试立即关机语义**

模拟后台任务未完成，确认下次 `run_due()` 能依据持久化状态重新执行。

- [ ] **步骤 5：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_api tests.test_pipeline tests.test_job_runner -v
```

---

## 阶段三：新闻、报告、模型回退与安全

### 任务 8：新闻按标准比赛过滤

**文件：**

- 修改：`football_advisor/news_ingestion.py`
- 修改：`football_advisor/news_tools.py:43`
- 修改：`football_advisor/sync.py:179`
- 修改测试：`tests/test_news_ingestion.py`、`tests/test_news_tools.py`

- [ ] **步骤 1：统一新闻 metadata**

每个 ChromaDB 文档必须包含：

```python
{
    "match_id": "WC2026_M003",
    "home_team_id": "WC_TEAM_CAN",
    "away_team_id": "WC_TEAM_BIH",
    "kickoff_date": "2026-06-13",
    "published_at": "2026-06-12T10:00:00Z",
    "source_url": "https://example.invalid/article",
}
```

- [ ] **步骤 2：修改检索接口**

```python
def search_match_news(self, request: MatchRequest, limit: int = 8) -> NewsSearchResult:
    ...
```

Chroma 查询必须带 `match_id` 过滤；没有标准比赛 ID 时不得退回全局集合结果。

- [ ] **步骤 3：修复零结果状态**

搜索零结果、抓取零有效正文、入库零文档分别返回 `empty_search`、`empty_fetch`、`empty_ingestion`。这些状态不得被 `_is_acceptable_sync_status()` 当作成功。

- [ ] **步骤 4：移除默认 Qwen 新闻压缩**

新闻重排默认只使用 `bge-reranker-v2-m3`。Qwen 不参与每次预测前的新闻处理，保持其“报告备用模型”边界。

- [ ] **步骤 5：修复现有两项失败测试并补串场测试**

```python
def test_usa_paraguay_never_returns_canada_bosnia_documents():
    result = tool.search_match_news(usa_paraguay_request)
    assert all(doc["metadata"]["match_id"] == "WC2026_M004" for doc in result.documents)
```

- [ ] **步骤 6：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_news_tools tests.test_news_ingestion tests.test_pipeline -v
```

### 任务 9：修复外部模型、Qwen 和模板回退

**文件：**

- 新增：`football_advisor/report_generator.py`
- 修改：`football_advisor/llm_router.py`
- 修改：`football_advisor/pipeline.py:155`
- 新增测试：`tests/test_report_generator.py`

- [ ] **步骤 1：定义明确结果类型**

```python
@dataclass(frozen=True)
class ReportGenerationResult:
    content: str
    provider: str
    generated: bool
    errors: tuple[str, ...] = ()
```

`used_backup` 不再表示“内容无效”。外部模型和 Qwen 的输出都必须经过同一校验器。

- [ ] **步骤 2：写回退测试**

覆盖：

1. 外部模型有效：返回外部报告。
2. 外部模型失败、Qwen 有效：返回 Qwen 报告。
3. 外部模型无效、Qwen 有效：返回 Qwen 报告。
4. 两者都失败或无效：返回固定模板。
5. 模型错误原因写日志，但不包含 API Key、请求头和完整响应。

- [ ] **步骤 3：拆分计算和报告阶段**

```python
def build_prediction_bundle(self, request: MatchRequest) -> PredictionBundle:
    ...

def generate_report(self, bundle: PredictionBundle) -> str:
    ...
```

开发穿测可对 `PredictionBundle` 使用注入的测试报告生成器；正式 API 仍固定使用 `ReportGenerator`，不得允许用户绕过概率、价值和 No Bet。

- [ ] **步骤 4：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_report_generator tests.test_pipeline tests.test_report -v
```

### 任务 10：将报告事实改为确定性渲染

**文件：**

- 修改：`football_advisor/report.py`
- 修改：`football_advisor/models.py`
- 修改测试：`tests/test_report.py`

- [ ] **步骤 1：事实段全部由代码渲染**

模型只允许生成以下叙述字段：

```python
@dataclass(frozen=True)
class ReportNarrative:
    key_factors: str
    main_risks: str
    reasoning_summary: str
```

概率、赔率、隐含概率、价值差、更新时间、伤停、天气、新闻来源和 No Bet 原因全部由 `PredictionBundle` 确定性渲染。

- [ ] **步骤 2：加强叙述校验**

叙述中出现的新数字、未在证据集合中的球队、球员、伤停或来源时判定无效，回退到代码生成摘要。

- [ ] **步骤 3：修复 Elo 展示**

报告直接读取 `features.home.elo` 和 `features.away.elo`，不得从缺少 Elo 的 `context` 默认成 1500。

- [ ] **步骤 4：覆盖全部必需章节**

测试逐项检查需求文档列出的全部报告字段，包括推理链摘要和五种竞彩玩法。

### 任务 11：修复任意 URL 抓取风险

**文件：**

- 新增：`football_advisor/url_safety.py`
- 修改：`football_advisor/text_fetcher.py:54`
- 修改：`football_advisor/api.py:126`
- 新增测试：`tests/test_url_safety.py`
- 修改测试：`tests/test_text_fetcher.py`、`tests/test_api_endpoints.py`

- [ ] **步骤 1：写安全测试**

必须拒绝：

- `localhost` 和环回地址。
- RFC1918 私网地址。
- 链路本地地址和 `169.254.169.254`。
- IPv6 环回、私网、链路本地和 IPv4 映射地址。
- DNS 解析到私网的域名。
- 公网 URL 重定向到私网地址。
- 缺少主机名、包含用户凭据或使用非 80/443 端口的 URL。

- [ ] **步骤 2：实现每跳校验**

`url_safety.py` 使用 `ipaddress` 和 `socket.getaddrinfo()` 校验目标；自定义重定向处理器在每次跳转前重新解析和校验。

- [ ] **步骤 3：限制管理入口**

`/search/fetch-preview` 默认关闭；只有显式配置 `FOOTBALL_ENABLE_FETCH_PREVIEW=true` 时注册。若服务允许非本机访问，该入口必须要求管理令牌。

- [ ] **步骤 4：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_url_safety tests.test_text_fetcher tests.test_api_endpoints -v
```

### 任务 12：修复 CodeRabbit 的剩余可观测性问题

**文件：**

- 修改：`football_advisor/verification_engine.py`
- 修改：`football_advisor/feature_enhancers.py`
- 修改：`football_advisor/db_schema.py`
- 修改：`football_advisor/sporttery_client.py`
- 修改对应测试

- [ ] **步骤 1：预测核验只按标准 ID 连接**

`fact_prediction_log` 新增 `home_team_id`、`away_team_id`；核验优先按 `match_id`，不得把显示名称与系统 ID 比较。

- [ ] **步骤 2：标准化临时比赛 ID**

新增共享 `normalize_identifier()`，统一去除首尾空白、折叠内部空白、转大写并替换非字母数字字符。优先要求正式预测已有标准 `match_id`；临时 ID 只供明确标记的开发模式使用。

- [ ] **步骤 3：防御非法阵型**

`_formation_midfield_count()` 对非数字字段返回默认值 3，不抛出 `ValueError`。

- [ ] **步骤 4：禁止数据库迁移静默吞错**

先查询 `information_schema.columns`，仅在列不存在时执行 `ALTER TABLE`；其他错误带表名和列名重新抛出。

- [ ] **步骤 5：运行定向测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_prediction_components tests.test_db_schema tests.test_sporttery_client -v
```

---

## 阶段四：准确性校准和数据利用收敛

### 任务 13：立即移除赔率趋势对概率的循环影响

**文件：**

- 修改：`football_advisor/probability_engine.py:140`
- 修改：`football_advisor/feature_enhancers.py`
- 修改：`football_advisor/no_bet_policy.py`
- 新增测试：`tests/test_prediction_components.py`

- [ ] **步骤 1：写不变性测试**

在球队统计、阵容和比赛上下文完全相同的情况下，仅改变竞彩赔率，模型基础概率必须保持不变；赔率只改变隐含概率、价值差和市场风险。

- [ ] **步骤 2：移除 OddsTrend 的 xG 调整**

赔率趋势保留为市场风险和报告信号，不再改变 `expected_home_goals`、`expected_away_goals`。

- [ ] **步骤 3：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_prediction_components tests.test_core -v
```

### 任务 14：建立 walk-forward 校准门禁

**文件：**

- 修改：`football_advisor/calibration_engine.py`
- 修改：`football_advisor/backtest_engine.py`
- 修改：`football_advisor/probability_engine.py`
- 新增：`scripts/calibrate_probability_model.py`
- 修改测试：`tests/test_calibration_engine.py`、`tests/test_backtest_loader.py`

- [ ] **步骤 1：冻结赛前特征快照**

校准数据必须满足 `record_date <= kickoff_time - 30 minutes`，并记录特征版本、参数版本和数据来源。

- [ ] **步骤 2：按时间滚动训练和验证**

不得随机切分。至少输出：

- Brier Score
- LogLoss
- 三分类准确率
- 分桶校准误差
- 各玩法样本数
- No Bet 后样本数和 ROI

- [ ] **步骤 3：设置启用门槛**

新增增强参数只有在独立时间窗口优于基线时才能进入生产。样本不足时保持基线模型并标记“未校准”，不能输出高置信度投注建议。

- [ ] **步骤 4：限制美国对巴拉圭类异常偏差**

新增市场偏离风险：模型概率与官方竞彩归一化概率偏差超过可配置阈值时提升风险等级；在完成校准前不得仅凭大偏差认定高价值。

### 任务 15：按价值接入已采集数据

**文件：**

- 修改：`football_advisor/feature_builder.py`
- 修改：`football_advisor/models.py`
- 修改：`football_advisor/no_bet_policy.py`
- 修改相应采集器和测试

- [ ] **步骤 1：第一批接入数据**

只接入：

- 映射且新鲜的伤停。
- 已确认首发和阵型。
- 当前赛事积分榜和赛季状态。
- 赔率快照形成的市场波动风险。

每项数据必须带 `source_provider`、`updated_at`、质量标记和标准球队/比赛 ID。

- [ ] **步骤 2：限定用途**

- 伤停和首发可进入特征，但权重需回测。
- 积分榜作为赛季状态辅助，不直接替代近期状态。
- 赔率快照只进入价值和风险，不进入基础概率。
- 新闻只生成风险与上下文，不覆盖结构化事实。

`NoBetPolicy` 必须把以下缺口作为明确原因：标准比赛映射缺失、官方已开售玩法缺失、主客队统计缺失、比赛上下文缺失、目标比赛新闻缺失、任一必需组件超过 30 分钟。不同缺口分别输出，不合并成模糊的“数据质量不足”。

- [ ] **步骤 3：暂不进入预测的数据**

- 比赛事件统一表：只用于赛后统计和模型训练。
- 球员名单与映射：只为首发、伤停和球员影响模型提供基础，不单独进入概率。
- 投注台账：只在用户明确记录投注时使用。
- 反馈调整参数：在完成回测验证前只展示，不修改概率或 No Bet 阈值。

- [ ] **步骤 4：停止无消费方的大规模采集**

没有明确消费方、质量门禁和测试的数据默认禁用采集，避免数据库体积和启动时间继续增长。

---

## 四、整体验证与穿测

### 任务 16：完整测试和两场验收

**文件：**

- 修改：`scripts/smoke_test_match_readiness.py`
- 新增：`scripts/smoke_test_target_prediction.py`
- 修改：`docs/ARCHITECTURE.md`
- 修改：`docs/PROGRESS.md`

- [ ] **步骤 1：运行静态和全量测试**

```powershell
.\.runtime\python\python.exe -m compileall football_advisor tests scripts
.\.runtime\python\python.exe -m unittest discover -s tests -v
git diff --check
```

预期：全部通过，不允许保留新闻重排器环境依赖造成的失败。

- [ ] **步骤 2：在临时数据库执行迁移和同步**

所有穿测脚本必须显式要求 `--db-path`。禁止隐式写入 `football_system.db`。

- [ ] **步骤 3：加拿大对波黑验收**

必须确认：

- 唯一标准比赛为 `WC2026_M003`。
- 不存在对应 `M_ST_*` 核心比赛。
- 五种竞彩玩法都绑定 `WC2026_M003`。
- 新闻 metadata 全部属于该比赛。
- 关键数据均不超过 30 分钟，或明确触发 No Bet。
- 报告事实与 `PredictionBundle` 一致。

- [ ] **步骤 4：美国对巴拉圭验收**

必须确认：

- 唯一标准比赛为 `WC2026_M004`。
- 不返回加拿大对波黑新闻。
- 模型和竞彩概率偏差进入风险说明。
- 在校准证据不足时不能因高价值差直接建议投注。

- [ ] **步骤 5：性能验收**

- 冷启动目标：120 秒内返回。
- 30 分钟缓存命中目标：30 秒内返回。
- 预测请求中不得出现全局赛后采集、全库滚动统计刷新和全历史反馈分析。
- 后台维护中断后，下次启动可以恢复且不重复污染数据。

- [ ] **步骤 6：重新执行 CodeRabbit**

```powershell
D:\Codex\UserProfile\.codex\bin\coderabbit.cmd review --agent --base-commit origin/main -c AGENTS.md
```

逐项人工确认 CodeRabbit 问题是否仍成立，不机械接受建议。

- [ ] **步骤 7：更新项目文档**

`docs/PROGRESS.md` 必须记录日期、完成工作、验证、限制和下一步；架构边界变化同步更新 `docs/ARCHITECTURE.md`。

---

## 五、阶段停止条件

任一情况发生时停止进入下一阶段：

1. 标准比赛仍出现重复 `match_id`。
2. 任一已开售竞彩玩法未绑定标准比赛。
3. 新闻仍可跨比赛返回。
4. 关键同步错误被记录为成功。
5. 全量测试仍有失败。
6. 迁移脚本不能证明幂等和事务回滚。
7. 预测概率仍受同一份待比较赔率直接调整。
8. 报告模型可以添加未经结构化证据支持的数值或事实。

## 六、推荐执行批次

按以下批次实施，禁止跨批次并行修改同一核心文件：

1. **批次 A，正确性：** 任务 1 至 4。
2. **批次 B，性能与恢复：** 任务 5 至 7。
3. **批次 C，文本与安全：** 任务 8 至 12。
4. **批次 D，准确性：** 任务 13 至 15。
5. **批次 E，最终验收：** 任务 16。

批次 A 完成前，项目保持 No Bet，不进行正式价值投注判断。批次 D 完成前，即使数据完整，也应对明显偏离市场的结果保持高风险或 No Bet。
