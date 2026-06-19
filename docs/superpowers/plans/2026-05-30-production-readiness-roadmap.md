# 生产预备版本落地实施计划

> **给执行代理的要求：** 实施本计划时必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，按任务逐项执行。任务使用复选框（`- [ ]`）跟踪状态。

**目标：** 在不扩大产品范围的前提下，把当前足球辅助决策原型推进为可测试、可验收的发布候选版本。

**架构：** 保持现有 OpenWebUI -> FastAPI -> DuckDB/ChromaDB -> 概率模型/赔率价值/No Bet -> LLM 报告链路。每个版本只解决一个明确验收边界，并用聚焦测试证明后再进入下一版本。

**技术栈：** Python、DuckDB、ChromaDB、FastAPI、OpenWebUI Tools、unittest、本地 `.runtime` Python。

---

## 范围边界

本计划不新增预测策略、不新增独立前端、不实现自动下注、不引入 Kelly 配资、不新增模型族。它只让既定系统更可靠地测试、验收和部署。

## 版本顺序

- `v0.7.0-readiness-fix`：修复审查确认的关键正确性缺口。
- `v0.8.0-real-data-sync`：把外部数据适配器从 mock 形态推进到可审计 provider 边界。
- `v0.9.0-openwebui-smoke`：证明 OpenWebUI 能通过后端服务调用系统，不绕过业务流程。
- `v1.0.0-rc`：执行最终本地发布候选验证，并记录剩余生产限制。

## 文件职责图

- 新增：`docs/TEST_VERSION_MATRIX.md` - 按版本号记录测试门禁和验收标准。
- 修改：`football_advisor/exchange_client.py` - 规范交易所赔率类型和资金流 payload 边界。
- 修改：`football_advisor/sync.py` - 正确传递配置、保留 provider 失败详情、集中写入资金流 payload。
- 修改：`football_advisor/pipeline.py` - 将完整 `AdvisorConfig` 传给同步协调器。
- 修改：`football_advisor/api_football_client.py` - 移除生产路径中的 mock 结构假设。
- 修改：`football_advisor/football_data_client.py` - 显式报告已映射和已跳过行数。
- 修改：`init_db.py` - 初始化 schema，而不是只创建数据库文件。
- 修改：`show_tables.py` - 安全列出表。
- 修改：`tests/test_exchange_sync.py` - 证明交易所资金流能进入特征视图。
- 修改：`tests/test_pipeline.py` - 证明配置传递和同步失败审计。
- 修改：`tests/test_api_football_sync.py` - 证明 provider 失败可见。
- 修改：`tests/test_api_endpoints.py` - 用阶段级集成烟测替代整条 pipeline mock。
- 修改：`docs/PROGRESS.md` - 每完成一个版本后记录进度。

---

### 任务 1：建立测试版本矩阵

**文件：**
- 新增：`docs/TEST_VERSION_MATRIX.md`
- 修改：`docs/PROGRESS.md`

- [ ] **步骤 1：创建版本矩阵文档**

创建 `docs/TEST_VERSION_MATRIX.md`，包含以下结构：

```markdown
# 测试版本矩阵

## 当前门禁

- 当前版本：v0.7.0-readiness-fix
- 状态：计划中
- 规则：版本内列出的验证命令全部在本地通过后，才允许标记为已验收。

## 版本列表

| 版本 | 目标 | 必须验证 | 退出标准 |
| --- | --- | --- | --- |
| v0.7.0-readiness-fix | 修复审查确认的关键正确性缺口 | 单元测试、聚焦集成测试 | 已知数据链路不匹配全部清除 |
| v0.8.0-real-data-sync | 让外部 provider 同步可审计 | provider 测试、同步测试 | provider 失败可见，可映射行数可统计 |
| v0.9.0-openwebui-smoke | 证明 OpenWebUI 工具路径可用 | FastAPI 烟测、OpenWebUI 工具烟测 | `/predict` 和 `/backtest` 能通过工具层调用 |
| v1.0.0-rc | 发布候选版验证 | 全量测试、回测、文档复核 | 所有已知限制明确记录，无严重缺口 |
```

- [ ] **步骤 2：记录进度**

在 `docs/PROGRESS.md` 顶部新增：

```markdown
### 已完成（生产预备实施计划与测试版本矩阵）

- 新增 `docs/superpowers/plans/2026-05-30-production-readiness-roadmap.md`，把后续落地拆成版本化验收任务。
- 新增 `docs/TEST_VERSION_MATRIX.md`，定义从 `v0.7.0-readiness-fix` 到 `v1.0.0-rc` 的测试门禁。

### 验证（生产预备实施计划与测试版本矩阵）

- 文档审查：确认本轮仅新增计划和测试版本框架，未修改业务代码。

### 已知限制（生产预备实施计划与测试版本矩阵）

- 该版本只建立执行框架，不修复业务逻辑缺口。

### 下一步建议（生产预备实施计划与测试版本矩阵）

- 从 `v0.7.0-readiness-fix` 开始按任务执行，优先修复资金流视图不匹配和配置传递问题。
```

- [ ] **步骤 3：验证仅文档变更**

运行：

```powershell
git diff -- docs/superpowers/plans/2026-05-30-production-readiness-roadmap.md docs/TEST_VERSION_MATRIX.md docs/PROGRESS.md
```

预期：只出现两个计划文档和进度文件的变更。

---

### 任务 2：v0.7.0 交易所资金流正确性

**文件：**
- 修改：`football_advisor/exchange_client.py`
- 修改：`football_advisor/sync.py`
- 测试：`tests/test_exchange_sync.py`

- [ ] **步骤 1：先写失败测试**

新增测试：执行交易所同步写入后，查询 `core.view_market_feature_base`，断言能读到 `matched_volume` 和 `sharp_money_ratio`。

修复前预期：因为 `1x2` 与 `1X2` 不匹配，视图行缺失或字段为空。

- [ ] **步骤 2：运行聚焦测试**

运行：

```powershell
.\.runtime\python\python.exe -m unittest tests.test_exchange_sync -v
```

预期：新增的视图层断言失败。

- [ ] **步骤 3：最小实现修复**

把写入的赔率类型规范为 `1X2`。如重构交易所客户端为返回 payload，则数据库写入保留在同步编排层完成。

- [ ] **步骤 4：验证**

运行：

```powershell
.\.runtime\python\python.exe -m unittest tests.test_exchange_sync tests.test_feature_builder -v
```

预期：选定测试全部通过。

---

### 任务 3：v0.7.0 配置传递与同步审计

**文件：**
- 修改：`football_advisor/pipeline.py`
- 修改：`football_advisor/sync.py`
- 测试：`tests/test_pipeline.py`

- [ ] **步骤 1：先写配置传递失败测试**

构造 `PredictionPipeline(config=custom_config)`，其中 DuckDB 路径为自定义路径。断言内部创建的 `DataSyncCoordinator.parent_config.duckdb.database_path` 等于该自定义路径。

修复前预期：使用默认路径。

- [ ] **步骤 2：先写 provider 失败审计测试**

注入 API-Football 失败路径，断言 `SyncResult.details` 包含失败 provider 和错误信息，而不是静默降级。

修复前预期：失败详情缺失。

- [ ] **步骤 3：最小实现修复**

创建 `DataSyncCoordinator` 时传入 `parent_config=self.config`，并在 fallback 前收集 provider 失败详情。

- [ ] **步骤 4：验证**

运行：

```powershell
.\.runtime\python\python.exe -m unittest tests.test_pipeline tests.test_api_football_sync -v
```

预期：选定测试全部通过。

---

### 任务 4：v0.8.0 Provider 边界加固

**文件：**
- 修改：`football_advisor/api_football_client.py`
- 修改：`football_advisor/football_data_client.py`
- 测试：`tests/test_api_football_sync.py`
- 测试：`tests/test_external_sync.py`

- [ ] **步骤 1：新增映射计数测试**

断言 provider 同步返回 `staged_count`、`merged_count` 和 `skipped_unmapped_count`。

- [ ] **步骤 2：新增未映射实体测试**

输入一个不存在于 `core.dim_team_mapping` 的 provider team id。

预期结果：该行被显式跳过，计数可见，不自动编造球队实体。

- [ ] **步骤 3：最小实现映射计数**

不新增实体系统。通过确定性 SQL 查询统计 staging 行数、合并行数和跳过行数。

- [ ] **步骤 4：验证**

运行：

```powershell
.\.runtime\python\python.exe -m unittest tests.test_api_football_sync tests.test_external_sync -v
```

预期：provider 同步测试通过，返回详情可审计。

---

### 任务 5：v0.9.0 API 与 OpenWebUI 烟测

**文件：**
- 修改：`tests/test_api_endpoints.py`
- 修改：`openwebui_tools/football_advisor_tools.py`
- 修改：`docs/OPENWEBUI_INTEGRATION.md`

- [ ] **步骤 1：替换整条 pipeline mock**

使用阶段级 fake 对象，证明 `/predict` 调用了真实 pipeline 方法，并保留关键阶段顺序。

- [ ] **步骤 2：新增 OpenWebUI 工具烟测断言**

mock `urllib.request.urlopen`，断言 `predict_match()` 只向 `/predict` 发送 `query` 和 `mode`，不暴露任意 SQL 或 Python 执行。

- [ ] **步骤 3：验证**

运行：

```powershell
.\.runtime\python\python.exe -m unittest tests.test_api_endpoints tests.test_openwebui_tools -v
```

预期：API 和工具烟测全部通过。

---

### 任务 6：v1.0.0-rc 发布候选验证

**文件：**
- 修改：`init_db.py`
- 修改：`show_tables.py`
- 修改：`docs/PROGRESS.md`
- 修改：`docs/TEST_VERSION_MATRIX.md`

- [ ] **步骤 1：修复初始化脚本**

`init_db.py` 应调用 `initialize_database("football_system.db")`，并打印 schema 初始化完成。

- [ ] **步骤 2：修复表查看脚本**

`show_tables.py` 应使用 `SHOW TABLES` 或 `information_schema.tables`。任何预览查询必须放入错误处理内。

- [ ] **步骤 3：运行全量验证**

运行：

```powershell
.\.runtime\python\python.exe -m unittest discover -s tests -v
.\.runtime\python\python.exe run_backtest.py
git diff --check
```

预期：测试通过，回测脚本完成，diff 检查无空白错误。

- [ ] **步骤 4：更新版本矩阵**

只有在命令输出已核验后，才把对应版本标记为“已验收”。

---

## 交接规则

用户确认本框架后，才开始执行任务。执行时按版本顺序推进，每完成一个版本就停下来复核。
