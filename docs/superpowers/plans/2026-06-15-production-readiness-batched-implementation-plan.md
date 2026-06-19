# 生产落地分批修复实施计划

> **代理执行强制要求：** 必须使用 `superpowers:executing-plans` 逐批执行；每批完成自动验收、按风险策略执行外部复审、提交和用户验收后，才能进入下一批。所有步骤使用复选框跟踪。

**目标：** 将生产落地全量闭环设计拆成可独立实施、可验证、可回滚、必须经用户二次验收的20个批次，避免一次修改过大，也避免问题被静默推迟到后续才暴露。

**架构：** 保持现有 OpenWebUI -> FastAPI -> 固定预测流水线 -> DuckDB/ChromaDB -> 概率/价值/No Bet -> LLM叙述架构。先建立证据与基础数据正确性，再修同步和竞彩主链，随后完成候选数据调研与用户审批，最后修模型、报告、入口、安全、恢复和四场穿测。

**技术栈：** Python、`unittest`、DuckDB、ChromaDB、FastAPI、OpenWebUI、PowerShell、CodeRabbit、JSON验收证据。

---

## 一、最高执行指令

执行优先级固定如下，低层规则不得覆盖高层规则：

1. 用户最新明确指令及根目录 `AGENTS.md`。
2. `docs/PROJECT_REQUIREMENTS.md` 的“数据调研与用户决策最高规则”。
3. 竞彩足球五玩法、固定预测流程、No Bet、LLM边界等系统不变量。
4. `docs/superpowers/specs/2026-06-15-production-readiness-closure-design.md`。
5. 本分批实施计划。
6. 现有代码、旧计划和历史实现。

发现冲突时必须停止当前批次，列出冲突文件、规则和影响；不得自行选择方便实现的版本。

## 二、批次大小和停止规则

- 每批原则上最多修改6个生产代码文件、6个直接测试文件和必要文档；超过时必须继续拆分。
- 每批只处理一个主故障域，不顺手重构无关代码。
- 每批单次执行Token上限18,000；达到上限必须停止并报告已完成、未完成和证据，禁止压缩验证步骤。
- 每批必须先做全仓影响扫描，再写失败测试，再做最小实现。
- 每批必须执行目标测试、系统不变量测试、全量测试、编译检查和 `git diff --check`。
- 每批必须判定 `external_review_required`，并把本批实际改动写入 `changed_files`：生产预测链路、概率/权重/No Bet、数据库 schema/迁移、同步链路、安全/API/外部请求/密钥处理、阶段收口和最终合并前必须执行限定范围 CodeRabbit 或等价外部复审，且有效问题在当前批次闭环后复审为0。验收器会自动扫描 `changed_files`，发现高风险路径却写成 false 时必须阻断。仅文档、证据、测试补丁或开发验收脚本的低风险批次可设置为 false，但必须记录外部复审尝试结果或未执行理由，并通过本地硬门禁。
- 每批必须更新 `docs/PROGRESS.md`，维护 `docs/verification/batches/batch-N-manifest.json` 和 `docs/verification/batches/batch-N-review.md`，再由 `scripts/finalize_batch.py` 自动生成 `docs/verification/batches/batch-N-evidence.json`，并形成一个独立提交。
- 每批必须通过 `scripts/finalize_batch.py` 自动生成 evidence，并通过 `scripts/verify_batch_completion.py` 的提交前和提交后门禁；脚本未通过时，不得宣称批次完成。
- 批次关闭流程不得只依赖执行者手工记忆命令序列。必须完成“批次101：批次关闭自动化入口固化”，创建统一入口 `scripts/close_batch.py`，由该脚本编排 evidence 生成、pre-commit 验证、提交后 post-commit 验证和最终工作区洁净检查。批次101完成后，后续批次关闭只能使用统一入口或 Git hook / CI 的同等自动入口；手工逐条执行命令只允许用于诊断失败原因，不能作为正式闭环路径。
- 预提交阶段必须确保批次编号可被 `scripts/enforce_batch_gate.py` 解析：优先使用 `FOOTBALL_ADVISOR_BATCH=N` 或包含 `batch-N` 的分支名；提交信息中的 `批次N` 仅用于提交后校验。
- 本地必须先执行 `scripts/install_git_hooks.ps1`，将 `core.hooksPath` 设置为 `.githooks`；未安装 hooks 的环境必须依赖CI阻断，不能直接合并。
- 提交后向用户给出验收报告并停止。只有用户明确回复“批次N验收通过”，才允许开始下一批。
- 用户未通过时，只能继续修复当前批次；不得开始下一批，不得改写旧提交隐藏过程，使用后续修复提交保留审计轨迹。
- 回滚只使用 `git revert <批次提交>`，不得使用破坏性重置。

## 三、每批统一执行模板

以下步骤适用于全部批次，批次章节只列本批特有内容：

- [ ] **步骤1：确认工作区和依赖**

```powershell
git status --short
git branch --show-current
git log -1 --oneline
```

期望：位于 `codex/fix-production-readiness-closure`；工作区不存在无法解释的改动。

- [ ] **步骤2：执行修改前全仓影响扫描**

使用 PowerShell `Get-ChildItem ... | Select-String` 搜索目标类、函数、表、状态码、文档和测试。输出必须写入本批验收记录，不能只检查计划列出的文件。

- [ ] **步骤3：先写失败测试并确认红灯**

```powershell
.\.runtime\python\python.exe -m unittest <本批目标测试> -v
```

期望：新增测试因目标缺陷失败；必须记录失败用例名和实际错误。若测试直接通过，先证明现有行为是否已经满足要求，不得制造无效修改。

- [ ] **步骤4：实施最小修复**

只修改使失败测试通过所需代码。不得降低断言、删除测试、伪造数据、把内部失败改标为外部阻塞，或用LLM补齐缺失事实。

- [ ] **步骤5：执行目标验收**

运行本批列出的目标测试和查询，期望零失败、零错误。

- [ ] **步骤6：执行统一回归**

```powershell
$env:PYTHONPYCACHEPREFIX='tmp\compile_cache'
.\.runtime\python\python.exe -m compileall -q football_advisor scripts tests openwebui_tools
.\.runtime\python\python.exe -m unittest tests.test_system_invariants -v
.\.runtime\python\python.exe -m unittest discover -s tests -v
git diff --check
```

任何失败都必须在当前批次解释并处理。历史已知失败也不能直接忽略，必须证明与本批无关并登记。

- [ ] **步骤7：独立审查和反向扫描**

从仓库根目录执行：

```powershell
D:\Codex\UserProfile\.codex\bin\coderabbit.cmd review --agent -t uncommitted --dir <本批高风险目录> -c AGENTS.md
```

同时反向搜索旧字段、旧状态、错误来源优先级和重复实现。若 `external_review_required=true`，CodeRabbit 发现必须逐项验证，只修仍成立的问题；若为 false，必须在 evidence/review 中写明风险判定依据。

- [ ] **步骤8：记录、提交并停止**

更新 `docs/PROGRESS.md`，记录日期、修改、红灯、绿灯、全量测试统计、外部复审策略和结果、已知限制和下一步。维护本批 manifest JSON 和 review Markdown，然后由 finalize 脚本生成 evidence JSON：

```text
docs/verification/batches/batch-N-manifest.json
docs/verification/batches/batch-N-review.md
docs/verification/batches/batch-N-evidence.json
```

提交前执行：

```powershell
.\.runtime\python\python.exe scripts\finalize_batch.py --batch N --mode pre-commit
.\.runtime\python\python.exe scripts\verify_batch_completion.py --batch N --mode pre-commit
```

提交后执行：

```powershell
.\.runtime\python\python.exe scripts\verify_batch_completion.py --batch N --mode post-commit
```

Git hooks 和CI使用统一入口：

```powershell
.\.runtime\python\python.exe scripts\enforce_batch_gate.py --mode pre-commit
.\.runtime\python\python.exe scripts\enforce_batch_gate.py --mode post-commit
```

只有两个门禁均通过，才能输出用户验收包并停止。若任一门禁失败，必须继续修复当前批次或明确报告“批次N未通过”，不得进入下一批。

用户验收包固定包含：批次目标、修改文件、缺陷根因、行为变化、测试统计、关键证据、evidence文件、review文件、外部复审策略和结果、剩余限制、提交号、回滚命令、用户人工检查步骤。

### 批次证据文件契约

每批在完成步骤5、步骤6、步骤7并更新 `docs/PROGRESS.md` 后，必须生成 evidence/review。只要本批修改了代码、测试、脚本、文档、配置或验收材料，都必须生成；只读取证批次也必须生成。

`batch-N-evidence.json` 必须符合 `docs/verification/templates/batch-evidence.schema.json`，并至少包含：

- `schema_version`：固定为 `1.0`。
- `batch`：当前批次编号，必须与命令参数一致。
- `commit`：提交前填当前 `git log -1 --oneline` 的提交号或计划提交号，提交后更新为本批提交号。
- `changed_files`：本批实际修改文件列表，必须与 `git diff --name-only` 或本批提交文件清单一致；验收器用它自动判定是否强制外部复审。
- `required_artifacts`：本批必须存在的产物路径，如测试、脚本、证据报告、台账。
- `review_file`：本批人工复核文件路径。
- `commands`：本批所有关键命令、退出码和结果摘要；任何非0退出码都会阻断。
- `external_review_required`：是否强制外部复审。高风险批次、阶段收口和最终合并前必须为 `true`。
- `coderabbit`：是否执行、退出码、未处理问题数和摘要；当 `external_review_required=true` 时，未执行或失败都会阻断；当为 false 时只记录事实，不作为硬阻断。
- `progress_updated`：是否已更新 `docs/PROGRESS.md`。
- `independent_commit`：是否形成独立提交。
- `blocking_gaps`：未解决阻断项；非空时不得进入下一批。

`batch-N-review.md` 必须使用以下结构：

```markdown
# 批次N人工复核

## 批次目标
## 修改范围
## 红灯证据
## 绿灯证据
## 全量回归结果
## CodeRabbit结果
## 非本批问题和处理理由
## 阻断项
## 用户验收检查点
```

示例：

```json
{
  "schema_version": "1.0",
  "batch": 0,
  "commit": "7608eed",
  "changed_files": [
    "docs/verification/production-readiness-baseline.md",
    "docs/verification/production-readiness-issue-register.md"
  ],
  "required_artifacts": [
    "docs/verification/production-readiness-baseline.md",
    "docs/verification/production-readiness-issue-register.md"
  ],
  "review_file": "docs/verification/batches/batch-0-review.md",
  "commands": [
    {
      "name": "全量测试",
      "command": ".\\.runtime\\python\\python.exe -m unittest discover -s tests -v",
      "exit_code": 1,
      "summary": "存在已登记历史失败，批次未通过"
    }
  ],
  "coderabbit": {
    "ran": true,
    "exit_code": 0,
    "unhandled_issues": 0,
    "summary": "本批范围无未处理问题"
  },
  "external_review_required": true,
  "progress_updated": true,
  "independent_commit": true,
  "blocking_gaps": [
    "全量测试仍有未修复失败"
  ]
}
```

pre-commit 模式只校验证据结构和命令结果；post-commit 模式额外要求 `git status --short` 为空。任一模式失败时，必须在最终回复写明“批次N未通过”，并列出脚本输出的阻断项。

## 四、问题发现防分批规则

每批开始前必须对当前故障域执行一次完整影响扫描，结束后执行一次反向扫描。新问题按以下规则处理：

- 与当前根因相同或会使本批验收失真：纳入当前批次，必要时继续拆小，但不能推迟后宣称本批通过。
- 是当前批次的前置阻塞：停止实施，先修订计划并向用户报告，未经用户确认不得跨越。
- 与当前批次无直接关系：立即登记到全局问题清单，给出证据、严重度、所属后续批次和不影响当前验收的理由；不得只在对话中口头提及。
- 无法归类：新增风险类别和验收门禁后再继续，禁止凭经验忽略。

任何批次都不得使用“基本完成”“应该可用”“大概率没问题”。只能使用测试和证据支持的状态。

---

## 五、20个实施批次

### 批次0：冻结基线和建立全局问题台账

**性质：** 只取证，不修改生产行为。

**文件：**
- 创建：`docs/verification/production-readiness-baseline.md`
- 创建：`docs/verification/production-readiness-issue-register.md`
- 修改：`docs/PROGRESS.md`

**任务：**
- [ ] 记录分支、提交、Python/DuckDB版本、数据库路径脱敏摘要和工作区状态。
- [ ] 运行当前编译、全量测试、系统不变量测试和四场只读数据库审计。
- [ ] 将设计报告中的全部风险逐项登记：编号、严重度、证据、影响、所属批次、状态、验收测试。
- [ ] 对已有跳过测试、失败脚本、旧验收结论和外部阻塞建立独立清单。

**验收命令：**
```powershell
.\.runtime\python\python.exe -m unittest discover -s tests -v
.\.runtime\python\python.exe scripts\smoke_test_worldcup_readiness.py --help
git diff --check
```

**用户人工验收：** 核对问题台账是否包含此前提出的流程、API、搜索、权重、数据使用、四场穿测、安全和AI纪律问题。通过前不改生产代码。

### 批次1：测试禁网和统一验收结果契约

**生产文件：** `football_advisor/models.py`、`football_advisor/verification_engine.py`

**测试/脚本：** `tests/test_system_invariants.py`、`tests/test_verification_engine.py`、`tests/test_delivery_readiness.py`、`scripts/verify_delivery_readiness.py`

**失败测试必须覆盖：**
- 单元测试意外调用真实网络时立即失败。
- `verification_status=INCOMPLETE` 时 `final_status` 必须为 `None`。
- 只有证据完整时允许四种互斥最终状态。
- 缺失场景必须逐项进入 `missing_evidence`。

**目标验收：**
```powershell
.\.runtime\python\python.exe -m unittest tests.test_verification_engine tests.test_delivery_readiness tests.test_system_invariants -v
```

**用户人工验收：** 查看一份完整证据和一份缺失证据JSON，确认不会把“未验完”写成生产就绪。

### 批次2：数据库初始化、迁移幂等和恢复前置

**生产文件：** `football_advisor/db_schema.py`

**测试/脚本：** `tests/test_db_schema.py`，创建 `scripts/verify_database_migration.py`，创建 `tests/test_database_migration.py`

**失败测试必须覆盖：**
- 全新空库一次初始化成功。
- 同一库二次初始化不重复列、不丢数据。
- 旧结构迁移成功，版本号只前进不倒退。
- 迁移中途失败时原库副本仍可读取。
- 视图只依赖迁移后真实存在的列。

**目标验收：**
```powershell
.\.runtime\python\python.exe -m unittest tests.test_db_schema tests.test_database_migration -v
.\.runtime\python\python.exe scripts\verify_database_migration.py --mode fixture
```

**用户人工验收：** 查看新库、历史库、二次迁移和失败恢复四项结果及表结构差异。

### 批次101：批次关闭自动化入口固化

**性质：** 开发验收治理修复，不改变生产预测运行时。

**前置原因：** 只依赖执行者手工串联 evidence 生成、提交和 post-commit 验证，容易产生半完成状态。因此必须把批次关闭动作固化为统一自动入口。

**生产文件：** 无。

**脚本/测试/文档：**
- 创建：`scripts/close_batch.py`
- 创建：`tests/test_close_batch.py`
- 修改：`scripts/finalize_batch.py`
- 修改：`scripts/verify_batch_completion.py`
- 修改：`docs/superpowers/plans/2026-06-15-production-readiness-batched-implementation-plan.md`
- 修改：`docs/PROGRESS.md`

**失败测试必须覆盖：**
- 工作区存在未提交或未跟踪改动时，`close_batch.py` 必须失败关闭，并列出具体路径；不得继续生成“完成”结论。
- 批次号缺失或与分支名 / 参数不一致时必须失败关闭。
- `finalize_batch.py --mode pre-commit` 失败时，`close_batch.py` 必须停止，不得提交。
- 提交后 `verify_batch_completion.py --mode post-commit` 失败时，`close_batch.py` 必须返回非0，并输出“批次N未通过”。
- `batch-N-evidence.json` 在提交后仍有未提交差异时必须失败。
- 高风险改动被 evidence 标记为 `external_review_required=false` 时必须失败。
- 成功路径必须证明：脚本生成 evidence、执行 pre-commit 验证、创建独立提交、执行 post-commit 验证、最终工作区干净。

**统一入口行为：**
```powershell
.\.runtime\python\python.exe scripts\close_batch.py --batch N --message "chore: close batch N"
```

脚本必须按固定顺序执行：
1. 读取 `git status --short --branch` 并拒绝无法解释的既有改动。
2. 检查 `docs/verification/batches/batch-N-manifest.json` 和 `batch-N-review.md` 存在。
3. 调用 `scripts/finalize_batch.py --batch N --mode pre-commit`。
4. 调用 `scripts/verify_batch_completion.py --batch N --mode pre-commit`。
5. 暂存本批 evidence、review、manifest、进度、测试和脚本改动。
6. 创建独立提交，提交信息必须包含 `批次N`。
7. 调用 `scripts/verify_batch_completion.py --batch N --mode post-commit`。
8. 再次读取 `git status --short`，非空则失败关闭。

**目标验收：**
```powershell
.\.runtime\python\python.exe -m unittest tests.test_close_batch tests.test_finalize_batch tests.test_verify_batch_completion -v
.\.runtime\python\python.exe -m compileall -q scripts tests
git diff --check
```

**批次101自身门禁：**
```powershell
.\.runtime\python\python.exe scripts\finalize_batch.py --batch 101 --mode pre-commit
.\.runtime\python\python.exe scripts\verify_batch_completion.py --batch 101 --mode pre-commit
.\.runtime\python\python.exe scripts\verify_batch_completion.py --batch 101 --mode post-commit
```

**用户人工验收：** 查看 `close_batch.py` 的失败路径测试和成功路径测试；确认批次101通过后，后续批次不再接受“我已手工跑过若干命令”的口头闭环，只接受统一入口、hook 或 CI 的机器证据。

### 批次3：标准实体、Provider映射和比赛唯一性

**生产文件：** `football_advisor/provider_mapping_importer.py`、`football_advisor/match_resolver.py`、`football_advisor/reconcile_sporttery_matches.py`、`football_advisor/db_schema.py`

**测试：** `tests/test_provider_mapping_importer.py`、`tests/test_match_resolver.py`、`tests/test_reconcile_sporttery_matches.py`、`tests/test_db_schema.py`

**失败测试必须覆盖：**
- provider ID、provider名称和标准实体ID不能混用。
- 未映射记录进入隔离清单，不能静默丢弃。
- 同一比赛只解析为一个标准 `match_id`。
- 映射补齐后 staging 数据可重新合并进入 core。
- 主客队不能解析为同一标准球队。

**目标验收：**
```powershell
.\.runtime\python\python.exe -m unittest tests.test_provider_mapping_importer tests.test_match_resolver tests.test_reconcile_sporttery_matches tests.test_db_schema -v
```

**用户人工验收：** 检查四场8支球队、联赛、比赛ID映射表和隔离记录，确认无串队、无悬空映射。

### 批次4：UTC、来源时间和未来数据穿越

**生产文件：** `football_advisor/time_utils.py`、`football_advisor/target_sync.py`、`football_advisor/feature_builder.py`、`football_advisor/backtest_engine.py`

**测试：** `tests/test_target_sync.py`、`tests/test_feature_builder.py`、`tests/test_backtest_engine.py`、`tests/test_system_invariants.py`

**失败测试必须覆盖：**
- naive时间、带时区时间和数据库时间统一转为UTC。
- 新鲜度使用 `source_updated_at`，不得用本地采集时间冒充。
- 未来来源时间直接拒绝并产生失败证据。
- 回测只能读取预测时点以前的数据。
- 中立场不应用主场优势时间相关回退。

**目标验收：**
```powershell
.\.runtime\python\python.exe -m unittest tests.test_target_sync tests.test_feature_builder tests.test_backtest_engine tests.test_system_invariants -v
```

**用户人工验收：** 查看正常、过期、未来时间三组证据，确认30分钟规则可解释。

### 批次5：Staging去重、重放和目标比赛对账

**生产文件：** `football_advisor/multi_source_coordinator.py`、`football_advisor/reconcile_sporttery_matches.py`、`football_advisor/sporttery_client.py`

**测试：** `tests/test_multi_source_coordinator.py`、`tests/test_reconcile_sporttery_matches.py`、`tests/test_sporttery_client.py`、`tests/test_smoke_test_multi_source_sync.py`

**失败测试必须覆盖：**
- 同一载荷重复同步不增加逻辑重复行。
- 目标比赛对账不能误处理同轮其他比赛。
- 映射缺失后补齐并重放可恢复数据。
- 部分失败不把已成功来源回滚成失败。
- 原始载荷、来源时间和幂等键可追溯。

**目标验收：**
```powershell
.\.runtime\python\python.exe -m unittest tests.test_multi_source_coordinator tests.test_reconcile_sporttery_matches tests.test_sporttery_client tests.test_smoke_test_multi_source_sync -v
```

**用户人工验收：** 比较首次同步、重复同步和映射修复重放后的行数及差异。

### 批次6：同步证据、30分钟缓存、超时和取消

**生产文件：** `football_advisor/sync.py`、`football_advisor/target_sync.py`、`football_advisor/multi_source_coordinator.py`、`football_advisor/db_schema.py`

**测试：** `tests/test_external_sync.py`、`tests/test_target_sync.py`、`tests/test_multi_source_coordinator.py`、`tests/test_pipeline.py`

**失败测试必须覆盖：**
- 每个组件每次预测最多调用一次。
- 成功缓存30分钟内复用，过期后重新调用。
- 失败缓存只抑制风暴，不能标记成功。
- provider超时可终止并产生机器失败记录。
- 同步返回状态与数据库重新加载后的 DataReadinessGate 一致。
- 空伤停、空阵容必须有供应商明确空结果证据。

**目标验收：**
```powershell
.\.runtime\python\python.exe -m unittest tests.test_external_sync tests.test_target_sync tests.test_multi_source_coordinator tests.test_pipeline -v
```

**用户人工验收：** 核对冷启动、立即重跑、缓存过期、单源超时四组调用次数和状态。

### 批次7：竞彩五玩法主链和未开售语义

**生产文件：** `football_advisor/sporttery_client.py`、`football_advisor/reconcile_sporttery_matches.py`、`football_advisor/target_sync.py`、`football_advisor/db_schema.py`

**测试/脚本：** `tests/test_sporttery_client.py`、`tests/test_sync_sporttery_odds.py`、`tests/test_smoke_test_worldcup_readiness.py`、`tests/test_system_invariants.py`、`scripts/verify_sporttery_5playtypes.py`

**失败测试必须覆盖：**
- SPF、RQSPF进入 `staging.stg_odds`。
- CRS、TTG、HAFU进入 `staging.stg_sporttery_odds_detail`。
- 已开售但赔率不完整为失败。
- 未开售明确为 `sporttery_play_type_not_offered`。
- 第三方赔率不能替代竞彩或改变竞彩玩法状态。
- 竞彩关键状态未知时强制 No Bet。

**目标验收：**
```powershell
.\.runtime\python\python.exe -m unittest tests.test_sporttery_client tests.test_sync_sporttery_odds tests.test_smoke_test_worldcup_readiness tests.test_system_invariants -v
.\.runtime\python\python.exe scripts\verify_sporttery_5playtypes.py
```

**用户人工验收：** 查看四场逐玩法矩阵、来源、销售状态、赔率完整性和未开售原因。

### 批次8：候选数据登记、调研模板和审批状态机

**生产文件：** 创建 `football_advisor/data_governance.py`，修改 `football_advisor/db_schema.py`、`football_advisor/config.py`

**研究工具/数据/文档：** 创建 `football_advisor/data_research.py`、`scripts/run_candidate_data_research.py`、`data/governance/data_candidate_registry.json`、`docs/data-research/README.md`、`docs/data-research/RESEARCH_TEMPLATE.md`

**测试：** 创建 `tests/test_data_governance.py`、`tests/test_data_research.py`，修改 `tests/test_db_schema.py`

**固定字段：** `data_id`、`name`、`definition`、`sources`、`collection_status`、`current_usage`、`research_status`、`research_report_path`、`evidence_level`、`leakage_risk`、`proposed_role`、`proposed_weight_range`、`user_decision`、`decision_time`、`approved_model_versions`、`owner`、`last_reviewed_at`。

**失败测试必须覆盖：**
- 字段不全、非法状态跃迁和无用户决定时拒绝准入。
- AI不能把状态直接改为“弃用”。
- 正式准入清单只包含用户已批准项。
- 产品硬规则与候选特征审批分离。
- 调研执行器必须输出数据快照哈希、查询截止时间、样本量、缺失率、映射率、指标和实验配置。
- 发现训练或验证样本晚于预测时点时调研失败，不得输出增量价值结论。
- 报告字段缺失、指标不可复现或数据不可用时，只能输出 `EVIDENCE_INCOMPLETE`。

**目标验收：**
```powershell
.\.runtime\python\python.exe -m unittest tests.test_data_governance tests.test_data_research tests.test_db_schema -v
```

**用户人工验收：** 审核候选登记是否覆盖当前已采集、已使用、已采集未使用和建议新增数据。此批只建立治理能力，不批准任何候选特征。

### 数据调研批次统一证据协议

批次9至14除通用批次模板外，还必须执行以下规则：

- 外部事实优先引用官方API文档、赛事/球队官方来源和原始研究论文；必须记录URL、访问日期、许可、费用和接口限制。
- API覆盖率、缺失率、延迟、映射率必须由实际脱敏请求或本地数据快照计算，不能引用营销页面数字代替本项目实测。
- 每项实验保存机器JSON，至少包含 `data_id`、`dataset_hash`、`cutoff_time_utc`、`sample_size`、`missing_rate`、`mapping_rate`、`baseline_metrics`、`single_feature_metrics`、`ablation_metrics`、`walk_forward_metrics`、`calibration_metrics`、`segment_metrics`、`leakage_checks`、`limitations`。
- 无API权限、样本不足或历史数据缺失时，必须输出 `EVIDENCE_INCOMPLETE` 和缺失项，不得生成虚构数值。
- Markdown调研报告中的所有指标必须能定位到对应JSON字段；引用外部结论必须标注来源，项目实证与外部证据不得混写。
- 报告必须同时给出支持使用、限制使用、反对使用和不使用损失四类内容。
- 用户未决定前只更新 `research_status=报告待审核`，不得更新 `user_decision` 或正式准入清单。

### 批次9：核心模型数据专项调研

**调研对象：** Poisson进球、Elo/动态Elo、近期战绩、进失球、主客场、中立场。

**文件：** 创建 `docs/data-research/01-core-model-features.md`，更新候选登记和 `docs/PROGRESS.md`。

**必须提供：** 来源/API、定义、覆盖率、缺失率、映射率、时效、未来穿越风险、单特征/组合/消融、walk-forward、Brier、LogLoss、校准、分赛事稳定性、支持和反对证据、候选权重范围、不使用损失。

**研究命令：**
```powershell
.\.runtime\python\python.exe scripts\run_candidate_data_research.py --group core_model --output data\governance\research_results\01-core-model-features.json
.\.runtime\python\python.exe -m unittest tests.test_data_research -v
```

**用户决策选项：** 进入正式概率、仅离线校准、延期、弃用。未获决定前保持未校准 No Bet，不修改正式权重。

### 批次10：球队表现和比赛动机数据专项调研

**调研对象：** xG、射门质量、控球/压迫、H2H、积分榜、赛事阶段、出线和轮换动机。

**文件：** 创建 `docs/data-research/02-team-performance-and-motivation.md`，更新候选登记和进度。

**研究命令：**
```powershell
.\.runtime\python\python.exe scripts\run_candidate_data_research.py --group team_performance_motivation --output data\governance\research_results\02-team-performance-and-motivation.json
.\.runtime\python\python.exe -m unittest tests.test_data_research -v
```

**用户验收：** 逐项决定进入概率、No Bet/风险、报告、离线校准、延期或弃用。任何一项未决定时不得自动接入。

### 批次11：阵容、伤停和球员重要性专项调研

**调研对象：** 名单、首发、阵型、伤停、停赛、球员重要性、俱乐部近期表现、教练变化。

**文件：** 创建 `docs/data-research/03-squad-injury-player.md`，更新候选登记和进度。

**研究命令：**
```powershell
.\.runtime\python\python.exe scripts\run_candidate_data_research.py --group squad_injury_player --output data\governance\research_results\03-squad-injury-player.json
.\.runtime\python\python.exe -m unittest tests.test_data_research -v
```

**额外门禁：** 球员身份映射、同名冲突、国家队与俱乐部时间窗、供应商明确空结果、临场首发发布时间。

### 批次12：赛程环境数据专项调研

**调研对象：** 休息天数、旅行距离、时差、天气、场地、草皮、海拔、裁判。

**文件：** 创建 `docs/data-research/04-schedule-environment.md`，更新候选登记和进度。

**研究命令：**
```powershell
.\.runtime\python\python.exe scripts\run_candidate_data_research.py --group schedule_environment --output data\governance\research_results\04-schedule-environment.json
.\.runtime\python\python.exe -m unittest tests.test_data_research -v
```

**额外门禁：** 比赛地点真实性、中立场、预报时点、防未来天气穿越、裁判未指派时不得猜测。

### 批次13：新闻、战术和舆情数据专项调研

**调研对象：** 新闻、发布会、战术、舆情、可信来源等级、文本相关性和时效。

**文件：** 创建 `docs/data-research/05-news-tactics-sentiment.md`，更新候选登记和进度。

**研究命令：**
```powershell
.\.runtime\python\python.exe scripts\run_candidate_data_research.py --group news_tactics_sentiment --output data\governance\research_results\05-news-tactics-sentiment.json
.\.runtime\python\python.exe -m unittest tests.test_data_research -v
```

**必须评估：** SearXNG、Serper、网页抓取、去重、ChromaDB、bge-m3、重排、LLM结构化抽取、来源引用、否定语义、提示注入和版权/许可风险。

**额外门禁：** 搜索摘要不能直接成为事实；未经用户批准不得改变概率。

### 批次14：赔率、市场和资金流专项调研

**调研对象：** 竞彩赔率变化、第三方赔率、市场一致性、资金流、交易所数据。

**文件：** 创建 `docs/data-research/06-market-and-capital-flow.md`，更新候选登记和进度。

**研究命令：**
```powershell
.\.runtime\python\python.exe scripts\run_candidate_data_research.py --group market_capital_flow --output data\governance\research_results\06-market-and-capital-flow.json
.\.runtime\python\python.exe -m unittest tests.test_data_research -v
```

**额外门禁：** 竞彩始终为五玩法主体；第三方只能作为经批准的交叉验证、风险或离线研究，不得替代竞彩。资金流不可得时不得构造代理值冒充真实资金流。

### 批次15：批准清单、特征血缘和默认值治理

**前置条件：** 批次9至14均已由用户逐项决定；延期项可保留候选，但必须有明确决定记录。

**生产文件：** `football_advisor/feature_builder.py`、`football_advisor/feature_enhancers.py`、`football_advisor/models.py`、`football_advisor/data_governance.py`

**测试：** `tests/test_feature_builder.py`、`tests/test_feature_enhancers.py`、`tests/test_data_governance.py`、`tests/test_system_invariants.py`

**失败测试必须覆盖：**
- 未批准特征不能进入概率、No Bet、报告或离线校准对应通道。
- 每个在线特征带来源表、实体、来源时间、默认值原因和批准版本。
- 不同球队不能因静默默认值生成相同特征。
- 必填特征缺失触发 No Bet；可选特征缺失为 `missing_optional`。

**目标验收：**
```powershell
.\.runtime\python\python.exe -m unittest tests.test_feature_builder tests.test_feature_enhancers tests.test_data_governance tests.test_system_invariants -v
```

**用户人工验收：** 核对批准清单与代码实际消费清单一一对应。

### 批次16：概率模型、权重发布、价值和No Bet

**生产文件：** `football_advisor/probability_engine.py`、`football_advisor/calibration_engine.py`、`football_advisor/odds_value_engine.py`、`football_advisor/no_bet_policy.py`、`football_advisor/odds_movement.py`

**测试：** `tests/test_core.py`、`tests/test_calibration_engine.py`、`tests/test_backtest_engine.py`、`tests/test_system_invariants.py`

**失败测试必须覆盖：**
- 未批准或未校准模型始终 `BET_READY=false`。
- 权重、阈值、数据版本和适用赛事可追溯。
- 中立场主场优势为0。
- 概率归一且输入默认值风险可见。
- 价值只使用竞彩对应玩法，未开售不计算替代价值。
- 赔率趋势只混合同源同玩法可比快照。
- No Bet不产生模拟盈亏或执行性投注建议。

**目标验收：**
```powershell
.\.runtime\python\python.exe -m unittest tests.test_core tests.test_calibration_engine tests.test_backtest_engine tests.test_system_invariants -v
```

**用户人工验收：** 查看模型卡、权重依据、回测指标、校准曲线摘要和No Bet样例；权重变更必须再次由用户明确批准。

### 批次17：LLM三字段叙述和确定性报告

**生产文件：** `football_advisor/report.py`、`football_advisor/report_generator.py`、`football_advisor/llm_router.py`、`football_advisor/pipeline.py`、`football_advisor/models.py`

**测试：** `tests/test_report.py`、`tests/test_report_generator.py`、`tests/test_pipeline.py`

**失败测试必须覆盖：**
- LLM只返回 `key_factors`、`main_risks`、`reasoning_summary`。
- LLM新增数字、球队、比分、赔率或投注结论时结果被拒绝。
- 外部LLM失败后回退Qwen，再失败回退纯代码模板。
- 叙述注入前后概率、价值、No Bet和竞彩状态完全不变。
- 禁投报告不展示诱导执行的价值候选。
- 所有用户可见状态和说明为中文。

**目标验收：**
```powershell
.\.runtime\python\python.exe -m unittest tests.test_report tests.test_report_generator tests.test_pipeline -v
```

**用户人工验收：** 对照结构化JSON和最终Markdown，确认LLM没有改写事实。

### 批次18：FastAPI、OpenWebUI、安全、性能和恢复门禁

**生产文件：** `football_advisor/api.py`、`openwebui_tools/football_advisor_tools.py`、`football_advisor/query_tools.py`、`football_advisor/url_safety.py`、`football_advisor/config.py`

**测试/脚本：** `tests/test_api.py`、`tests/test_api_endpoints.py`、`tests/test_openwebui_tools.py`、`tests/test_url_safety.py`、`tests/test_config.py`、`scripts/verify_delivery_readiness.py`

**失败测试必须覆盖：**
- 所有正式入口调用同一 `PredictionPipeline`。
- SQL只允许单条SELECT、参数化和LIMIT。
- URL抓取阻止本地、内网、危险重定向和非法协议。
- 管理接口鉴权，日志和错误脱敏。
- request_id贯穿全流程。
- 受控慢provider触发阶段超时和取消。
- 数据库副本迁移失败可恢复。

**目标验收：**
```powershell
.\.runtime\python\python.exe -m unittest tests.test_api tests.test_api_endpoints tests.test_openwebui_tools tests.test_url_safety tests.test_config -v
.\.runtime\python\python.exe scripts\verify_delivery_readiness.py
```

**用户人工验收：** 通过OpenWebUI工具和FastAPI分别调用同一场比赛，比较结构化结果一致性；查看安全失败和恢复演练证据。

### 批次19：四场全模式穿测和最终收口

**固定比赛：** 德国对库拉索、荷兰对日本、科特迪瓦对厄瓜多尔、瑞典对突尼斯。

**脚本/测试：** `scripts/walkthrough_e2e.py`、`tests/test_walkthrough_e2e.py`、`tests/test_smoke_test_worldcup_readiness.py`、`tests/test_delivery_readiness.py`

**每场必须执行：**
1. 全新库fixture。
2. 历史库迁移后fixture。
3. live真实同步。
4. 30分钟内cache重跑。
5. 由ChatGPT在对话中只提供三字段叙述，再继续同一后台报告和日志流程。
6. 新闻缺失、阵容缺失、单provider超时和LLM失败故障注入。

**机器证据必须包含：** 请求ID、提交、数据库路径、比赛实体、四级readiness、竞彩五玩法、组件状态、特征血缘、模型版本、概率、价值、No Bet、报告来源、阶段耗时、缺失证据和最终状态。

**目标验收：**
```powershell
.\.runtime\python\python.exe -m unittest tests.test_walkthrough_e2e tests.test_smoke_test_worldcup_readiness tests.test_delivery_readiness tests.test_system_invariants -v
.\.runtime\python\python.exe scripts\walkthrough_e2e.py --mode fixture
.\.runtime\python\python.exe scripts\walkthrough_e2e.py --mode live
.\.runtime\python\python.exe scripts\walkthrough_e2e.py --mode cache
```

**最终复核：** 全量测试、编译、`git diff --check`、CodeRabbit分目录审查、数据库恢复演练、发布门禁矩阵和文档一致性全部重新执行。

**用户人工验收：** 用户逐场核对报告和证据。只有用户明确通过四场及总门禁，才允许把项目状态写为相应就绪级别。外部条件不足时只能是 `SAFE_DEGRADED` 或 `EXTERNAL_BLOCKED`，不得写成 `PRODUCTION_READY`。

---

## 六、用户二次验收格式

每批结束后，代理必须停止并等待以下任一明确回复：

```text
批次N验收通过
```

或：

```text
批次N验收不通过：<具体问题>
```

没有明确“验收通过”不得推断用户已同意，不得开始下一批。

## 七、最终完成定义

- 20个批次全部有独立提交、自动验收记录、外部复审策略和结果、用户验收结论，并全部通过 `scripts/verify_batch_completion.py --mode post-commit`。
- 所有问题台账条目均为已修复、用户决定延期、用户决定弃用或有证据的外部阻塞，不允许无主状态。
- 所有候选数据均已调研并由用户决定用途；AI没有自行排除项。
- 所有正式特征、权重和阈值均可追溯到调研、实验、模型版本和用户批准。
- 四场全模式穿测和故障注入完成，结构化证据可复现。
- 只有全部当次证据支持时才可声明对应就绪状态；否则必须列出未完成项。
