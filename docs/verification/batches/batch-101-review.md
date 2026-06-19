# 批次101人工复核

## 批次目标

把批次关闭流程固化为统一自动入口，避免后续批次依赖执行者或其它 AI 手工记忆命令序列。验收与门禁工作只负责判断批次是否具备机器证据，不参与业务批次推进。

## 修改范围

| 文件 | 操作 | 说明 |
|------|------|------|
| `scripts/close_batch.py` | 新增 | 串联 evidence 生成、pre-commit 验证、提交、post-commit 验证和最终工作区检查，并把 manifest 声明的 CodeRabbit 原始产物纳入允许与暂存清单 |
| `tests/test_close_batch.py` | 新增 | 覆盖未解释脏文件、finalize 失败、post-commit 失败、提交后仍脏、缺少 manifest、CodeRabbit 产物暂存和成功路径 |
| `docs/superpowers/plans/2026-06-15-production-readiness-batched-implementation-plan.md` | 修改 | 固化批次101作为门禁自动化治理批次，并要求后续批次使用统一关闭入口 |
| `docs/PROJECT_REQUIREMENTS.md` | 修改 | 把批次关闭自动化写入 R16 规则 |
| `docs/ARCHITECTURE.md` | 修改 | 说明 `close_batch.py` 属于开发验收治理层 |
| `docs/PROGRESS.md` | 修改 | 记录批次101执行状态 |

## 红灯证据

- `.\.runtime\python\python.exe -m unittest tests.test_close_batch -v` 在实现前失败：
  - `ModuleNotFoundError: No module named 'scripts.close_batch'`

## 绿灯证据

- `.\.runtime\python\python.exe -m unittest tests.test_close_batch -v`：7/7 通过。
- 覆盖的关键行为：
  - 未纳入本批 manifest 的脏文件会失败关闭。
  - manifest 声明的 CodeRabbit 原始产物会被纳入允许改动和提交暂存清单。
  - `finalize_batch.py --mode pre-commit` 失败时不会提交。
  - post-commit 门禁失败时返回“批次N未通过”。
  - 提交后工作区仍脏会失败关闭。
  - 缺少 manifest 时不会执行 Git 命令。
  - 成功路径按固定顺序生成 evidence、验证、暂存、提交、post-commit 验证和最终状态检查。

## 全量回归结果

- `.\.runtime\python\python.exe -m unittest tests.test_close_batch tests.test_finalize_batch tests.test_verify_batch_completion -v`：41/41 通过。
- `.\.runtime\python\python.exe -m compileall -q scripts tests`：通过。
- `git diff --check`：退出码 0，仅有既有 LF/CRLF 转换警告。
- `.\.runtime\python\python.exe scripts\finalize_batch.py --batch 101 --mode pre-commit`：失败关闭，已生成 `docs/verification/batches/batch-101-evidence.json`，阻断项为 CodeRabbit 未成功执行。
- `.\.runtime\python\python.exe scripts\verify_batch_completion.py --batch 101 --mode pre-commit`：失败关闭，阻断项为 CodeRabbit 未成功执行。

## CodeRabbit结果

已尝试执行：

```powershell
D:\Codex\UserProfile\.codex\bin\coderabbit.cmd review --agent -t uncommitted --base HEAD --dir scripts -c AGENTS.md
```

历史结果：两次均在约 244 秒超时，第二次已按权限规则提权重试；随后轻量复审首次触发限流。

最新结果：

```powershell
D:\Codex\UserProfile\.codex\bin\coderabbit.cmd review --agent --light -t uncommitted --base HEAD --dir scripts -c AGENTS.md
```

结果：成功完成，`findings=0`。原始产物已记录到 `docs/verification/coderabbit/batch-101-review.jsonl`。由于本批新增 `scripts/close_batch.py`，属于开发验收脚本改动，`external_review_required` 必须为 `true`；当前已取得有效外部复审结论。

## 非本批问题和处理理由

- 当前工作区已有非批次101未提交修改，不应并入批次101。
- 当前工作区已有若干 `.before-migration.bak` 未跟踪文件，疑似测试生成副本，本批不擅自删除。

## 阻断项

- 当前工作区存在非批次101改动，`scripts/close_batch.py --batch 101` 按设计会失败关闭。
- `batch-101-evidence.json` 当前如实采集到非批次101改动和若干 `.before-migration.bak` 文件，说明工作区仍混有非本批改动；正式关闭前必须隔离或处理这些遗留项。
- 批次101尚未形成独立提交，post-commit 门禁尚未通过。

## 用户验收检查点

1. 查看 `tests/test_close_batch.py` 中失败路径是否覆盖半完成批次、脏工作区和 post-commit 失败问题。
2. 查看 `scripts/close_batch.py` 是否只编排开发验收流程，不进入 OpenWebUI/FastAPI 预测运行时。
3. 确认后续批次正式关闭只能使用统一入口、hook 或 CI 的机器证据。
