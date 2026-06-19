# 生产落地基线快照

> 批次 0 产出 | 取证日期：2026-06-15

## 环境基线

| 项目 | 值 |
|------|-----|
| 分支 | `codex/fix-production-readiness-closure` |
| 最新提交 | `a70710d` — docs: 拆分生产落地修复验收批次 |
| 提交时间 | 2026-06-15 13:43:49 +0800 |
| Python | 3.14.5 |
| DuckDB | 1.5.2 |
| 操作系统 | Windows |
| 工作区状态 | clean（无未提交改动） |

## 编译状态

```powershell
.\.runtime\python\python.exe -m compileall -q football_advisor scripts tests openwebui_tools
```

**结果：** 通过（退出码 0，无输出即无错误）

## git diff --check

**结果：** 通过（无空白符违规）

## 系统不变量测试

```powershell
.\.runtime\python\python.exe -m unittest tests.test_system_invariants -v
```

**结果：** 12/12 通过

## 全量测试

```powershell
.\.runtime\python\python.exe -m unittest discover -s tests -v
```

**结果：** 412 运行 / 1 失败 / 3 跳过

### 跳过清单

| 文件 | 行号 | 原因 |
|------|------|------|
| `tests/test_core.py` | 598 | BacktestEngine 重构后待更新测试 |
| `tests/test_exchange_sync.py` | 80 | view_market_feature_base 重构后待更新测试 |
| `tests/test_backtest_loader.py` | 14 | view_llm_match_prediction_base 重构后待更新测试 |

### 失败清单

| 测试 | 错误 |
|------|------|
| `test_free_key_does_not_search_non_arsenal_team_without_mapping` (TheSportsDBClientTests) | `AssertionError: 'success' != 'no_squad_found'` — 无映射球队免费密钥搜索行为与预期不符 |

## 数据库路径

| 用途 | 路径 |
|------|------|
| 主 DuckDB | `D:\Knowledge_Base-duckdb\football_system.db` |
| .env | `D:\Knowledge_Base-duckdb\.env` |

## 当前就绪状态

- `CODE_READY`: ❌ 未达标（存在失败测试、跳过的测试未更新）
- `DEPLOYMENT_READY`: ❌ 未达标
- `MATCH_READY`: ❌ 未达标（外部数据未就绪）
- `BET_READY`: ❌ 未达标（模型未校准）
- 系统运行状态：`SAFE_DEGRADED`