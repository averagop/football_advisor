# 批次 1 复核与补修报告

> 执行日期：2026-06-17

## 批次目标

修复“测试禁网和统一验收结果契约”批次中未闭环的问题，并修正旧 evidence 中 CodeRabbit 状态自相矛盾、commit 错误和产物缺失的问题。

## 修复内容

| 文件 | 说明 |
|------|------|
| `tests/__init__.py` | 单元测试包导入时安装禁网守卫 |
| `tests/network_guard.py` | 统一阻断 `urllib.request.urlopen`、`socket.create_connection`、`http.client` 和 `requests` 常见真实网络入口 |
| `tests/test_system_invariants.py` | 增加全局禁网真实入口测试 |
| `scripts/verify_delivery_readiness.py` | 接入 `ExecutionResult`，直接脚本执行时也能导入项目包 |
| `tests/test_delivery_readiness.py` | 覆盖缺失证据进入 `missing_evidence` 和完成状态输出 |
| `scripts/verify_batch_completion.py` | 补强 CodeRabbit 说明矛盾、原始产物、commit 和 git HEAD 失败关闭检查 |
| `tests/test_verify_batch_completion.py` | 覆盖批次1假通过、误报关键词和 git HEAD 失败关闭 |
| `docs/verification/coderabbit/batch-1-repair-review.jsonl` | 保存 CodeRabbit 原始复审结果 |

## 关键修复结论

1. 旧批次1只在 `TheSportsDBClient` 中禁网，不能代表全局单元测试禁网；现已在测试入口统一阻断常见真实网络调用。
2. 旧 `ExecutionResult` 只是孤立数据类；现已接入 `scripts/verify_delivery_readiness.py`，缺失必需证据时输出 `INCOMPLETE`，完成基础检查时输出互斥终态。
3. 旧 evidence 的 `commit` 指向错误提交，`required_artifacts` 为空，且把 CodeRabbit 无法运行写成成功；现已改为当前 HEAD、列出产物、保存外部复审原始产物。
4. CodeRabbit 对 `scripts` 修复范围和批次1原始 `football_advisor` 高风险范围均完成轻量复审，结果 `findings=0`。

## 验证记录

| 命令 | 结果 |
|------|------|
| 红灯测试：`tests.test_system_invariants.NetworkIsolationTests tests.test_delivery_readiness` | 修复前失败，失败原因与预期一致 |
| 目标测试：`tests.test_system_invariants.NetworkIsolationTests tests.test_delivery_readiness` | 13/13 通过 |
| `scripts/verify_delivery_readiness.py --root . --env-path .env` | 通过，输出 `verification_status=COMPLETE, final_status=SAFE_DEGRADED` |
| `tests.test_verify_batch_completion` | 23/23 通过 |
| 全量测试：`unittest discover -s tests -v` | 456 项通过，3 项历史跳过 |
| 编译检查：`compileall -q football_advisor scripts tests` | 通过 |
| `git diff --check` | 通过，仅有既有 LF/CRLF 转换提示 |
| CodeRabbit `scripts` 轻量复审 | `findings=0` |
| CodeRabbit `football_advisor` 轻量复审 | `findings=0` |
| 批次 1 `pre-commit` 门禁 | 通过 |

## 已知边界

1. 本批修复的是测试禁网和验收结果契约，不代表整个项目已经生产就绪。
2. `verify_delivery_readiness.py` 的结论为 `SAFE_DEGRADED`，因为它只证明本地交付基础项，不证明外部真实数据源和四场穿测全部就绪。
3. 3 个历史跳过测试仍按 R-001 子项留待后续批次处理。
4. `batch-1-evidence.json` 的 `commit` 表示本批开始前的基线提交；提交后门禁会校验该值等于 `HEAD^`，避免自引用提交哈希导致无法闭环。

## 下一步

批次 1 已达到用户验收条件；形成独立提交后再执行 `post-commit` 门禁。
