# 足球预测顾问生产闭环修复实施计划

> **执行代理必读：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 按任务逐项实施。所有步骤使用复选框跟踪，禁止跨阶段并行修改同一运行链路。

**目标：** 修复真实四场穿测暴露的数据库迁移、竞彩目标匹配、就绪门禁、预测日志、模型校准、新闻性能和 URL 安全问题，使正式预测流程能够明确成功或明确失败，不再返回假成功结果。

**架构：** 保留现有 `OpenWebUI -> FastAPI -> PredictionPipeline -> DuckDB/ChromaDB -> 概率/价值/No Bet -> 报告` 主架构。先在现有边界内补齐启动迁移、目标比赛数据覆盖检查和失败传播，再关闭未经校准的增强与占位概率，最后处理性能、安全和数据利用；不新增博彩平台，不更换数据库，不重写预测引擎。

**技术栈：** Python 3.14、DuckDB、ChromaDB、FastAPI、`unittest`、中国竞彩足球公开数据、外部 OpenAI-compatible 报告模型、Ollama Qwen 备用报告模型。

---

## 执行原则

1. 阶段一未通过前，不做新数据源接入。
2. 竞彩目标比赛没有映射时必须明确失败，禁止使用全局抓取成功代替目标成功。
3. 数据不足时允许输出确定性的 `No Bet` 诊断报告，但禁止使用占位 Elo、攻防强度生成看似真实的概率。
4. 校准门禁未通过时，固定标记模型未校准并禁止输出可投注建议。
5. 每个任务先写失败测试，再做最小实现，再运行相关测试和全量测试。
6. 每个任务单独提交；任何真实穿测失败都停止后续阶段并修复。

## 文件职责映射

- `football_advisor/db_schema.py`：数据库建表、增量迁移和必需 schema 校验。
- `football_advisor/target_sync.py`：目标比赛竞彩五玩法同步结果和组件级就绪状态。
- `football_advisor/sporttery_client.py`：官方竞彩抓取、五玩法落库和开售状态记录。
- `football_advisor/sync.py`：预测前同步协调及目标同步结果传播。
- `football_advisor/pipeline.py`：固定预测流程、错误传播、概率与报告边界。
- `football_advisor/feature_builder.py`：标准比赛和球队 ID、结构化特征读取。
- `football_advisor/verification_engine.py`：预测日志及赛后核验。
- `football_advisor/probability_engine.py`：基线概率和受门禁控制的增强参数。
- `football_advisor/config.py`：校准状态和增强开关。
- `football_advisor/news_tools.py`、`football_advisor/reranker.py`：不调用 Qwen 的新闻检索重排。
- `football_advisor/llm_router.py`、`football_advisor/report_generator.py`：外部模型、Qwen、模板各调用一次的报告回退。
- `football_advisor/url_safety.py`、`football_advisor/text_fetcher.py`、`football_advisor/api.py`：URL 抓取和管理入口安全。
- `football_advisor/feature_enhancers.py`：积分榜、阵容、伤停等上下文增强，不直接绕过校准门禁。
- `scripts/smoke_test_worldcup_readiness.py`：真实世界杯目标比赛就绪检查。
- `scripts/walkthrough_e2e.py`：可注入报告生成器的完整穿测。
- `docs/PROGRESS.md`、`docs/ARCHITECTURE.md`：实施结果和当前生产能力。

---

## 阶段一：修复生产闭环阻断

### 任务 1：启动时强制执行数据库迁移

**文件：**
- 修改：`football_advisor/db_schema.py:865-918`
- 修改：`football_advisor/pipeline.py:37-83`
- 修改测试：`tests/test_db_schema.py`
- 修改测试：`tests/test_pipeline.py`

- [ ] **步骤 1：编写旧库迁移失败测试**

在临时 DuckDB 中先创建旧版 `core.fact_prediction_log`，再调用 `initialize_database()`，断言以下列存在：

```python
required = {"diff_summary", "version", "home_team_id", "away_team_id"}
columns = {
    row[0]
    for row in connection.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'core'
          AND table_name = 'fact_prediction_log'
        """
    ).fetchall()
}
self.assertTrue(required.issubset(columns))
```

- [ ] **步骤 2：运行测试并确认失败**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_db_schema -v
```

预期：旧表缺少新增列时测试失败。

- [ ] **步骤 3：让迁移失败严格抛错**

将 `_migrate_new_columns()` 中 `information_schema` 查询异常的静默跳过改成带上下文抛错：

```python
except Exception as exc:
    raise RuntimeError(
        f"Migration inspection failed: table={table}, column={col_name}"
    ) from exc
```

- [ ] **步骤 4：在管线构造最前面初始化数据库**

在创建 `PredictionLogger`、特征构造器和其他数据库依赖前执行：

```python
from .db_schema import initialize_database

db_path = self.config.duckdb.database_path
initialize_database(db_path)
```

所有入口，包括 FastAPI、OpenWebUI 工具和直接脚本调用，都通过 `PredictionPipeline` 获得同一迁移保障。

- [ ] **步骤 5：验证旧库副本迁移**

```powershell
Copy-Item football_system.db tmp\migration_acceptance.duckdb
$env:FOOTBALL_DUCKDB_PATH='tmp\migration_acceptance.duckdb'
.\.runtime\python\python.exe -c "from football_advisor.pipeline import PredictionPipeline; PredictionPipeline(); print('迁移成功')"
```

预期：输出“迁移成功”，新增表和列均存在；原库不被修改。

- [ ] **步骤 6：提交**

```powershell
git add football_advisor/db_schema.py football_advisor/pipeline.py tests/test_db_schema.py tests/test_pipeline.py
git commit -m "fix: 启动时强制执行数据库迁移"
```

### 任务 2：修复竞彩目标比赛假成功

**文件：**
- 修改：`football_advisor/db_schema.py`
- 修改：`football_advisor/sporttery_client.py`
- 修改：`football_advisor/target_sync.py:121-222`
- 修改测试：`tests/test_sporttery_client.py`
- 修改测试：`tests/test_target_sync.py`

- [ ] **步骤 1：新增竞彩玩法状态表测试**

新增表必须保存每场五种玩法的状态：

```sql
CREATE TABLE IF NOT EXISTS core.fact_sporttery_market_status (
    match_id VARCHAR NOT NULL,
    market_type VARCHAR NOT NULL,
    sale_status VARCHAR NOT NULL,
    snapshot_time TIMESTAMP NOT NULL,
    source_provider VARCHAR NOT NULL,
    PRIMARY KEY (match_id, market_type, snapshot_time)
);
```

允许的 `sale_status` 只有：`OPEN`、`NOT_ON_SALE`、`MAPPING_MISSING`、`SYNC_FAILED`。

- [ ] **步骤 2：写目标匹配失败测试**

构造“全局抓取 20 场成功，但目标 `WC2026_M008` 无 provider match mapping”的测试，断言：

```python
self.assertEqual(result.status, "failed_target_mapping_missing")
self.assertEqual(result.details["match_id"], "WC2026_M008")
```

- [ ] **步骤 3：写五玩法覆盖测试**

目标比赛存在映射时，逐项检查：

```python
expected = {"SPF", "RQSPF", "CRS", "TTG", "HAFU"}
self.assertEqual(set(result.details["market_statuses"]), expected)
```

已开售玩法必须有赔率行；未开售玩法必须持久化 `NOT_ON_SALE`，不能仅靠缺行推断。

- [ ] **步骤 4：实现目标级覆盖查询**

在 `_sync_sporttery()` 获取全局抓取结果后，查询目标 `system_match_id`：

```python
mapping = conn.execute(
    """
    SELECT provider_match_id
    FROM core.dim_provider_match_mapping
    WHERE provider_name = 'SportteryOfficialWeb'
      AND system_match_id = ?
    """,
    [match_id],
).fetchone()
```

无映射立即返回 `failed_target_mapping_missing`。有映射后读取五玩法状态；只有五项均为 `OPEN` 或 `NOT_ON_SALE`，且所有 `OPEN` 项都有赔率记录时，才返回 `success`。

- [ ] **步骤 5：修复半全场元数据键比较**

将比较项全部转为小写，避免 `key.lower()` 与驼峰字符串永远不相等：

```python
if key.endswith("f") or key.lower() in {
    "goalline", "goallinevalue", "id", "updatedate", "updatetime"
}:
    continue
```

- [ ] **步骤 6：运行相关测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_sporttery_client tests.test_target_sync -v
```

预期：全局成功但目标缺失、目标未开售、目标五玩法完整三类测试全部通过。

- [ ] **步骤 7：提交**

```powershell
git add football_advisor/db_schema.py football_advisor/sporttery_client.py football_advisor/target_sync.py tests/test_sporttery_client.py tests/test_target_sync.py
git commit -m "fix: 按目标比赛核验竞彩五玩法"
```

### 任务 3：把组件级就绪门禁接入正式预测流程

**文件：**
- 修改：`football_advisor/target_sync.py:15-174`
- 修改：`football_advisor/sync.py:310-322`
- 修改：`football_advisor/pipeline.py:90-133`
- 修改：`football_advisor/models.py`
- 修改测试：`tests/test_target_sync.py`
- 修改测试：`tests/test_pipeline.py`

- [ ] **步骤 1：写就绪状态传播测试**

同步结果中竞彩为缺失或过期时，管线必须保留具体组件：

```python
self.assertFalse(bundle.readiness.ready)
self.assertIn("sporttery_odds", bundle.readiness.missing_critical)
self.assertEqual(bundle.policy.recommendation, "no_bet")
```

- [ ] **步骤 2：让同步协调器返回结构化结果**

`sync_before_prediction()` 不再展平并丢失 `critical_ok`：

```python
def sync_before_prediction(self, request: MatchRequest) -> TargetSyncResult:
    service = TargetMatchSyncService(
        parent_config=self.parent_config,
        sync_coordinator=self,
    )
    return service.sync_for_prediction(request)
```

- [ ] **步骤 3：从数据库构造 `DataReadinessGate`**

为 `DataReadinessGate` 增加 `load(connection, match_id, max_age_minutes)`，分别读取赛程、竞彩、球队统计、阵容伤停和新闻时间。禁止使用把不同组件更新时间合并为单值的 `LEAST` 表达式。

- [ ] **步骤 4：将门禁写入预测包和 No Bet 上下文**

```python
context["readiness"] = readiness.check()
context["no_bet_data_quality_flag"] = (
    context.get("no_bet_data_quality_flag", False)
    or not readiness.check()["ready"]
)
```

报告必须列出具体缺失或过期组件，而不是统一显示“数据质量不足”。

- [ ] **步骤 5：运行测试**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_target_sync tests.test_pipeline tests.test_report -v
```

- [ ] **步骤 6：提交**

```powershell
git add football_advisor/target_sync.py football_advisor/sync.py football_advisor/pipeline.py football_advisor/models.py tests/test_target_sync.py tests/test_pipeline.py tests/test_report.py
git commit -m "fix: 接入组件级预测就绪门禁"
```

### 任务 4：预测日志失败必须终止继续流程

**文件：**
- 修改：`football_advisor/db_schema.py:400-470`
- 修改：`football_advisor/feature_builder.py`
- 修改：`football_advisor/pipeline.py:135-170`
- 修改：`football_advisor/verification_engine.py:90-130`
- 修改：`football_advisor/prediction_diff.py:403-441`
- 修改测试：`tests/test_pipeline.py`
- 修改测试：`tests/test_verification_engine.py`

- [ ] **步骤 1：把标准球队 ID加入特征视图和上下文**

视图必须明确输出：

```sql
s.home_team_id AS home_team_id,
s.away_team_id AS away_team_id,
```

`FeatureBuilder` 将其写入 `features.context`，不再用队名冒充 ID。

- [ ] **步骤 2：写日志失败传播测试**

```python
with self.assertRaises(PredictionLoggingError):
    pipeline.generate_report(bundle)
self.assertEqual(report_generator.calls, 0)
```

- [ ] **步骤 3：删除日志异常吞没**

改为直接调用：

```python
prediction_id = self.prediction_logger.log_prediction(bundle)
```

日志失败时不得继续差异分析或报告生成。

- [ ] **步骤 4：使用标准 ID写日志**

```python
home_team_id = str(features.context["home_team_id"])
away_team_id = str(features.context["away_team_id"])
```

缺少标准 ID 时抛出 `PredictionLoggingError`。

- [ ] **步骤 5：删除差异分析中的数据库静默失败**

`diff_summary` 和 `version` 更新失败时记录带预测 ID 的异常并重新抛出，不保留 `except Exception: pass`。

- [ ] **步骤 6：运行测试并提交**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_pipeline tests.test_verification_engine tests.test_prediction_diff -v
git add football_advisor/db_schema.py football_advisor/feature_builder.py football_advisor/pipeline.py football_advisor/verification_engine.py football_advisor/prediction_diff.py tests
git commit -m "fix: 强制预测日志和标准球队ID一致"
```

---

## 阶段二：收敛准确性与性能

### 任务 5：移除生产占位概率

**文件：**
- 修改：`football_advisor/pipeline.py:257-291`
- 修改：`football_advisor/api.py:108-125`
- 修改测试：`tests/test_pipeline.py`
- 修改测试：`tests/test_api_endpoints.py`

- [ ] **步骤 1：写特征不可用测试**

```python
with self.assertRaises(FeatureDataUnavailable):
    pipeline.build_prediction_bundle(request)
```

- [ ] **步骤 2：删除 `_build_initial_features()` 的固定 Elo 和攻防值**

当 DuckDB 特征视图不可用时抛出明确异常：

```python
raise FeatureDataUnavailable(
    match_id=request.match_id or "",
    reason=fallback_reason,
)
```

- [ ] **步骤 3：API 返回明确不可预测状态**

FastAPI 捕获该异常并返回 `503`，响应中只包含 `match_id`、`status=data_unavailable` 和脱敏原因，不返回概率。

- [ ] **步骤 4：运行测试并提交**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_pipeline tests.test_api_endpoints -v
git add football_advisor/pipeline.py football_advisor/api.py tests/test_pipeline.py tests/test_api_endpoints.py
git commit -m "fix: 禁止数据缺失时生成占位概率"
```

### 任务 6：落实模型校准门禁

**文件：**
- 修改：`football_advisor/config.py`
- 修改：`football_advisor/probability_engine.py:11-180`
- 修改：`football_advisor/policy.py`
- 修改：`scripts/calibrate_probability_model.py`
- 修改测试：`tests/test_calibration_engine.py`
- 修改测试：`tests/test_core.py`

- [ ] **步骤 1：增加明确的模型配置**

```python
@dataclass(frozen=True)
class ProbabilityConfig:
    poisson_weight: float = 0.72
    model_version: str = "baseline-uncalibrated"
    calibrated: bool = False
    enable_enhanced_boosts: bool = False
```

- [ ] **步骤 2：未经批准时关闭增强参数**

```python
if self.enable_enhanced_boosts:
    raw = self._apply_enhanced_boosts(raw, features, is_home=True)
```

阵型、H2H、疲劳只可作为报告上下文，不能影响概率。

- [ ] **步骤 3：未校准时强制 No Bet**

`NoBetPolicy` 增加原因 `model_uncalibrated`。`calibrated=False` 时不允许 `Bet`，但在结构化数据有效时可保留诊断概率。

- [ ] **步骤 4：校准脚本只在门禁通过时输出批准参数**

通过条件同时满足：

```python
approved = (
    metrics.sample_size >= 50
    and metrics.brier_score < 0.25
    and metrics.brier_score < baseline_metrics.brier_score
)
```

未通过时退出码为 `2`，不得覆盖生产配置。

- [ ] **步骤 5：验证当前数据仍被拒绝**

```powershell
.\.runtime\python\python.exe scripts\calibrate_probability_model.py --db-path football_system.db --limit 50 --min-train 10
```

预期：Brier 约 `0.70`，门禁失败，生产保持 `baseline-uncalibrated` 和强制 `No Bet`。

- [ ] **步骤 6：运行测试并提交**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_calibration_engine tests.test_core -v
git add football_advisor/config.py football_advisor/probability_engine.py football_advisor/policy.py scripts/calibrate_probability_model.py tests/test_calibration_engine.py tests/test_core.py
git commit -m "fix: 强制执行概率模型校准门禁"
```

### 任务 7：彻底移除新闻阶段 Qwen 调用

**文件：**
- 修改：`football_advisor/reranker.py:359-416`
- 修改：`football_advisor/news_tools.py:39-90`
- 修改测试：`tests/test_news_tools.py`

- [ ] **步骤 1：写无压缩器调用测试**

```python
reranker = TwoStageReranker(compressor_model=None, scorer=fake_scorer)
result = reranker.rerank("query", documents)
self.assertEqual(fake_compressor.calls, 0)
```

- [ ] **步骤 2：将压缩器改为真正可选**

```python
self.compressor = (
    OllamaCompressor(base_url=base_url, model=compressor_model, timeout=timeout)
    if compressor_model
    else None
)
```

```python
compressed_texts = (
    self.compressor.compress_batch(query, documents)
    if self.compressor
    else [document.get("text", "") for document in documents]
)
```

- [ ] **步骤 3：重排异常必须保留状态**

禁止 `except Exception: pass`。检索结果增加 `rerank_status=failed`，并记录脱敏错误；原始文档仍可继续使用。

- [ ] **步骤 4：运行测试并提交**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_news_tools -v
git add football_advisor/reranker.py football_advisor/news_tools.py tests/test_news_tools.py
git commit -m "fix: 新闻重排不再调用Qwen压缩"
```

### 任务 8：消除报告回退中的重复 Qwen 调用

**文件：**
- 修改：`football_advisor/llm_router.py`
- 修改：`football_advisor/report_generator.py:45-119`
- 修改测试：`tests/test_report_generator.py`

- [ ] **步骤 1：写调用次数测试**

覆盖三种情况：外部成功时 Qwen 调用零次；外部失败时 Qwen 调用一次；两者失败时模板调用一次。

- [ ] **步骤 2：提供单一职责方法**

```python
def generate_external(self, system_prompt: str, user_prompt: str) -> str:
    if not self.external_base_url or not self.external_api_key:
        raise RuntimeError("external model is not configured")
    last_error: Exception | None = None
    for attempt in range(self.retries + 1):
        try:
            return self._chat_completion(
                self.external_base_url,
                self.external_api_key,
                self.external_model,
                system_prompt,
                user_prompt,
            )
        except Exception as exc:
            last_error = exc
            if attempt < self.retries:
                time.sleep(0.4 * (2**attempt))
    raise RuntimeError("external model request failed") from last_error

def generate_backup(self, system_prompt: str, user_prompt: str) -> str:
    return self._chat_completion(
        self.backup_base_url,
        "ollama",
        self.backup_model,
        system_prompt,
        user_prompt,
    )
```

`ReportGenerator` 按顺序调用这两个公开方法，不再先调用包含完整回退链的 `generate()`。

- [ ] **步骤 3：运行测试并提交**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_report_generator tests.test_pipeline -v
git add football_advisor/llm_router.py football_advisor/report_generator.py tests/test_report_generator.py tests/test_pipeline.py
git commit -m "fix: 报告回退每个模型只调用一次"
```

---

## 阶段三：安全加固

### 任务 9：URL 校验改为失败关闭并补齐请求前校验

**文件：**
- 修改：`football_advisor/url_safety.py:56-137`
- 修改：`football_advisor/text_fetcher.py:29-76`
- 修改测试：`tests/test_url_safety.py`
- 修改测试：`tests/test_text_fetcher.py`

- [ ] **步骤 1：写 DNS 失败测试**

```python
with patch("socket.getaddrinfo", side_effect=socket.gaierror()):
    safe, reason = validate_url("https://unresolved.example")
self.assertFalse(safe)
self.assertEqual(reason, "dns_resolution_failed:unresolved.example")
```

- [ ] **步骤 2：DNS 失败改为拒绝**

```python
except socket.gaierror:
    return False, f"dns_resolution_failed:{parsed.hostname}"
```

- [ ] **步骤 3：实现并注册请求处理器**

`SafeHTTPHandler.http_open()` 和 `SafeHTTPSHandler.https_open()` 在连接前再次调用 `validate_url(req.full_url)`；`build_safe_urlopen()` 注册重定向、HTTP 和 HTTPS 三个处理器。

- [ ] **步骤 4：保持端口白名单**

继续只允许 `80` 和 `443`。不得采纳放宽任意端口的建议。

- [ ] **步骤 5：运行测试并提交**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_url_safety tests.test_text_fetcher -v
git add football_advisor/url_safety.py football_advisor/text_fetcher.py tests/test_url_safety.py tests/test_text_fetcher.py
git commit -m "fix: URL抓取安全校验失败关闭"
```

### 任务 10：限制抓取预览管理入口

**文件：**
- 修改：`football_advisor/api.py:127-141`
- 修改：`.env.example`
- 修改测试：`tests/test_api_endpoints.py`

- [ ] **步骤 1：写管理令牌测试**

启用入口但没有 `X-Football-Admin-Token` 时返回 `401`；令牌错误返回 `403`；令牌正确才允许抓取。

- [ ] **步骤 2：只在启用时注册路由**

```python
if os.environ.get("FOOTBALL_ENABLE_FETCH_PREVIEW", "").lower() == "true":
    @app.post("/search/fetch-preview")
    def fetch_preview(
        payload: TextFetchPreviewPayload,
        x_football_admin_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        configured_token = os.environ.get("FOOTBALL_FETCH_PREVIEW_ADMIN_TOKEN", "")
        if not configured_token:
            raise HTTPException(status_code=503, detail="admin token is not configured")
        if x_football_admin_token is None:
            raise HTTPException(status_code=401, detail="admin token is required")
        if not secrets.compare_digest(x_football_admin_token, configured_token):
            raise HTTPException(status_code=403, detail="invalid admin token")
        documents = text_fetcher.fetch_many(payload.urls)
        return {"documents": [_as_response_dict(item) for item in documents]}
```

- [ ] **步骤 3：使用恒定时间比较令牌**

```python
if not secrets.compare_digest(provided_token, configured_token):
    raise HTTPException(status_code=403, detail="invalid admin token")
```

只记录是否配置，不记录令牌值。

- [ ] **步骤 4：运行测试并提交**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_api_endpoints -v
git add football_advisor/api.py .env.example tests/test_api_endpoints.py
git commit -m "fix: 限制抓取预览管理入口"
```

---

## 阶段四：数据利用和流程瘦身

### 任务 11：优先补齐四场竞彩实体映射

**文件：**
- 修改：`data/provider_team_mappings.csv`
- 修改：`data/provider_league_mappings.csv`（仅缺失时）
- 使用：`football_advisor/reconcile_sporttery_matches.py`
- 修改测试：`tests/test_reconcile_sporttery_matches.py`

- [ ] **步骤 1：从官方抓取结果提取八支球队原始名称和 provider ID**

目标球队：卡塔尔、瑞士、巴西、摩洛哥、海地、苏格兰、澳大利亚、土耳其。禁止根据中文名称猜测 provider ID。

- [ ] **步骤 2：先执行 dry-run 对账**

```powershell
.\.runtime\python\python.exe -m football_advisor.reconcile_sporttery_matches tmp\migration_acceptance.duckdb --dry-run
```

预期：四场均唯一匹配；任何歧义或零匹配都停止，不写库。

- [ ] **步骤 3：导入映射并正式对账**

导入后断言四个 `system_match_id` 均存在 `SportteryOfficialWeb` 映射，且主客队 ID 与赛程一致。

- [ ] **步骤 4：提交**

```powershell
git add data/provider_team_mappings.csv data/provider_league_mappings.csv tests/test_reconcile_sporttery_matches.py
git commit -m "data: 补齐四场世界杯竞彩实体映射"
```

### 任务 12：只接入已经采集但确有价值的数据

**文件：**
- 修改：`football_advisor/feature_enhancers.py:419-560`
- 修改：`football_advisor/pre_match_collector.py`
- 修改测试：`tests/test_feature_enhancers.py`

- [ ] **步骤 1：积分榜增强器读取正确事实表**

`StandingsEnhancer` 从 `core.fact_league_standings` 读取赛季排名和积分，不再把滚动统计列当作积分榜来源。

- [ ] **步骤 2：伤停和阵容只进入风险上下文**

在球员映射、比赛映射和更新时间均有效时，写入确认人数、来源和更新时间；校准门禁通过前不改变 xG 或概率。

- [ ] **步骤 3：保持以下数据离线使用**

`fact_match_events_unified`、球员身价、详细球员事件仅用于训练、回测和赛后核验，不进入在线预测主链。

- [ ] **步骤 4：运行测试并提交**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_feature_enhancers -v
git add football_advisor/feature_enhancers.py football_advisor/pre_match_collector.py tests/test_feature_enhancers.py
git commit -m "fix: 收敛积分榜阵容伤停数据利用"
```

### 任务 13：删除预测主链中的无效初始化和重复烟测

**文件：**
- 修改：`football_advisor/pipeline.py:73-83`
- 修改：`scripts/smoke_test_worldcup_readiness.py`
- 删除：`scripts/smoke_test_match_readiness.py`（功能合并并验证后）
- 修改：`docs/ARCHITECTURE.md`
- 修改：`docs/PROGRESS.md`

- [ ] **步骤 1：移除主链未使用实例**

`PostMatchVerifier`、`BetLedger`、`FeedbackEngine` 只在维护任务实际使用处构造，不在每次 `PredictionPipeline` 初始化时创建。

- [ ] **步骤 2：合并重复就绪脚本**

保留一个支持联赛和比赛参数的 `smoke_test_worldcup_readiness.py`。映射门禁必须检查指定 provider：

```sql
WHERE m.system_team_id = t.system_team_id
  AND m.provider_name = 'SportteryOfficialWeb'
```

禁止“任意 provider 存在即通过”。

- [ ] **步骤 3：更新文档事实**

删除 `ARCHITECTURE.md` 中已经完成或与当前代码冲突的“待接入”描述；`PROGRESS.md` 如实记录真实四场结果、校准未通过和已知限制。

- [ ] **步骤 4：运行测试并提交**

```powershell
.\.runtime\python\python.exe -m unittest tests.test_smoke_test_worldcup_readiness tests.test_pipeline -v
git add football_advisor/pipeline.py scripts/smoke_test_worldcup_readiness.py scripts/smoke_test_match_readiness.py docs/ARCHITECTURE.md docs/PROGRESS.md tests
git commit -m "refactor: 精简预测初始化和重复就绪检查"
```

---

## 最终验收

### 任务 14：执行全量测试和四场真实穿测

**文件：**
- 修改：`scripts/walkthrough_e2e.py`
- 修改：`docs/PROGRESS.md`

- [ ] **步骤 1：运行全量测试**

```powershell
.\.runtime\python\python.exe -m unittest discover -s tests -v
```

预期：全部通过，且测试输出中不得出现被吞掉的 DuckDB schema 异常。

- [ ] **步骤 2：运行编译和差异检查**

```powershell
$env:PYTHONPYCACHEPREFIX='tmp\compile_cache'
.\.runtime\python\python.exe -m compileall -q football_advisor scripts
git diff --check
```

预期：退出码均为 `0`。

- [ ] **步骤 3：执行四场就绪检查**

分别检查：

```text
Qatar vs Switzerland
Brazil vs Morocco
Haiti vs Scotland
Australia vs Turkiye
```

每场必须满足：

- 存在标准 `match_id`。
- 八支球队均有 `SportteryOfficialWeb` 球队映射。
- 四场均有竞彩比赛映射，或明确报告官方未发布该场。
- 五玩法每项为 `OPEN` 或 `NOT_ON_SALE`。
- `OPEN` 玩法存在目标比赛赔率行。
- 同步状态不得在目标数据为零时显示 `success`。

- [ ] **步骤 4：执行对话注入报告穿测**

使用注入的测试报告生成器替代规定模型，只替换报告叙述阶段。同步、特征、概率、价值、No Bet、日志和差异分析必须继续执行真实代码。

- [ ] **步骤 5：检查性能**

每场执行冷启动一次、30 分钟内缓存命中两次：

- 冷启动不超过 `120` 秒。
- 缓存命中不超过 `30` 秒。
- 新闻阶段不得出现 Ollama/Qwen 请求。
- 报告阶段每个模型最多调用一次。

- [ ] **步骤 6：检查安全和失败传播**

- DNS 失败 URL 被拒绝。
- 私网、环回、链路本地、凭据 URL、非 80/443 端口被拒绝。
- 抓取预览未配置令牌时不可访问。
- 预测日志失败时请求失败，不返回成功报告。
- 特征不可用时不返回概率。
- 校准未通过时所有比赛保持 `No Bet`。

- [ ] **步骤 7：更新最终进度记录并提交**

```powershell
git add scripts/walkthrough_e2e.py docs/PROGRESS.md
git commit -m "test: 完成四场真实生产闭环验收"
```

## 完成定义

只有同时满足以下条件，才能声明本轮可靠性优化完成：

1. 现有数据库副本启动后自动迁移成功。
2. 四场目标竞彩数据不存在假成功。
3. 组件级就绪状态进入 `NoBetPolicy` 和报告。
4. 预测日志成功写入标准球队 ID；失败会终止请求。
5. 数据不可用时不生成占位概率。
6. 当前校准不合格状态被生产门禁识别。
7. 新闻阶段不调用 Qwen，报告回退不重复调用 Qwen。
8. URL 安全测试和管理令牌测试全部通过。
9. 四场真实穿测满足明确成功或明确失败，不允许静默降级。
10. 全量测试、编译检查和 `git diff --check` 全部通过。

## 明确不做

- 不新增其他博彩平台替代竞彩。
- 不引入新的模型框架或数据库。
- 不在校准失败时调整概率权重追求更好看的四场结果。
- 不让赔率直接改变基础概率。
- 不删除未确认归属的根目录数据文件或用户脚本。
- 不把球员身价、比赛事件等离线数据直接塞入在线主链。
