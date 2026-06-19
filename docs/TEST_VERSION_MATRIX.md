# 测试版本矩阵

## 当前门禁

- 当前版本：`v1.0.0-rc`
- 状态：`已验收`
- 规则：版本内列出的验证命令全部在本地通过，并把结果记录到 `docs/PROGRESS.md` 后，才允许标记为 `已验收`。
- 2026-05-31 修订：`已验收` 只表示本地原型、回归测试和回测脚本通过；真实 provider 映射、真实 API-Football 独立赔率/统计端点、真实交易所资金流仍是生产前置条件，未完成前不得宣称实盘部署完成。

## 版本门禁

| 版本 | 状态 | 目标 | 必须验证 | 退出标准 |
| --- | --- | --- | --- | --- |
| `v0.7.0-readiness-fix` | 已验收 | 修复审查确认的关键正确性缺口，不新增功能。 | `.\.runtime\python\python.exe -m unittest tests.test_exchange_sync tests.test_pipeline tests.test_api_football_sync -v` | 交易所数据能进入特征视图，自定义配置能进入同步层，provider 失败详情可见。 |
| `v0.8.0-real-data-sync` | 已验收 | 让外部 provider 同步可审计、可追踪、可复核。 | `.\.runtime\python\python.exe -m unittest tests.test_api_football_sync tests.test_external_sync -v` | provider 行数能报告 staged、merged、skipped，且不会编造实体。 |
| `v0.9.0-openwebui-smoke` | 已验收 | 证明 OpenWebUI 通过 FastAPI 后端路径调用，不绕过业务流程。 | `.\.runtime\python\python.exe -m unittest tests.test_api_endpoints tests.test_openwebui_tools -v` | `/predict` 和 `/backtest` 能通过工具层访问，且不暴露任意 SQL 或 Python 执行。 |
| `v1.0.0-rc` | 已验收 | 执行最终发布候选版验证。 | `.\.runtime\python\python.exe -m unittest discover -s tests -v`; `.\.runtime\python\python.exe run_backtest.py`; `git diff --check` | 全量本地测试通过，回测脚本完成，所有已知生产限制已记录。 |

## 验收规则

- OpenWebUI 必须保持为唯一正式用户前端。
- DuckDB 和 ChromaDB 必须按数据类型保持分离。
- 任何 LLM 都不得生成或修改概率。
- 预测前必须先同步；失败或跳过的同步状态必须进入 No Bet 判断。
- 如果唯一通过的数据路径仍是 mock，且限制没有记录，不得把版本标记为 `已验收`。

## 变更记录

| 日期 | 版本 | 状态 | 说明 |
| --- | --- | --- | --- |
| 2026-05-30 | `v0.7.0-readiness-fix` | 计划中 | 基于生产预备审查发现创建初始测试版本矩阵。 |
| 2026-05-30 | `v0.7.0-readiness-fix` | 已验收 | 修复交易所资金流赔率类型、Pipeline 配置传递和 API-Football 失败审计，并通过版本门禁测试。 |
| 2026-05-30 | `v0.8.0-real-data-sync` | 已验收 | 补充 provider staged、merged、skipped 计数，验证未映射实体跳过且不自动编造。 |
| 2026-05-30 | `v0.9.0-openwebui-smoke` | 已验收 | 完成整条 pipeline 的 fake 替换，并新增 OpenWebUI 工具烟测，不暴露直接 SQL/Python 执行。 |
| 2026-05-30 | `v1.0.0-rc` | 已验收 | 执行了最终发布候选版验证，初始化与表结构脚本修复完毕，回测顺利完成。 |
