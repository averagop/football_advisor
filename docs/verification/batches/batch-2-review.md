# 批次2人工复核

## 批次目标

为数据库初始化流程添加 schema 版本追踪机制，确保：
1. 全新空库一次初始化成功。
2. 同一库二次初始化不重复列、不丢数据（幂等）。
3. 旧结构迁移成功，版本号只前进不倒退。
4. 迁移中途失败时原库副本仍可读取。
5. 视图只依赖迁移后真实存在的列。

## 修改范围

| 文件 | 操作 | 说明 |
|------|------|------|
| `football_advisor/db_schema.py` | 修改 | 新增 `CURRENT_SCHEMA_VERSION` 常量、`core.schema_version` 表定义、`_record_schema_version` 函数 |
| `tests/test_database_migration.py` | 新增 | 7 个迁移专项测试 |
| `scripts/verify_database_migration.py` | 新增 | fixture 模式数据库迁移验证脚本 |

## 红灯证据

修复前运行 `tests/test_database_migration.py`：
- `test_fresh_database_initializes_all_required_tables` → **FAIL**：缺失 `core.schema_version`
- `test_schema_version_table_exists_and_has_required_columns` → **FAIL**：表不存在
- `test_schema_version_advances_never_regresses` → **ERROR**：表不存在
- `test_legacy_database_migration_sets_version_properly` → **ERROR**：表不存在
- `test_all_views_query_all_columns_exist` → **ERROR**：duckdb_views() 列名不匹配

结果：2 FAIL, 3 ERROR, 2 OK

## 绿灯证据

修复后全部通过：
- 目标测试：`tests.test_db_schema + tests.test_database_migration` → 19/19 通过
- 迁移验证脚本：`scripts/verify_database_migration.py --mode fixture` → 4/4 检查通过
- 系统不变量：19/19 通过
- 全量测试：463/463 通过，3 跳过

## 全量回归结果

```
编译：通过
系统不变量测试：19/19 OK
全量测试：463/463 OK (3 skipped)
git diff --check：通过
```

## CodeRabbit结果

已由 `scripts/finalize_batch.py` 从原始产物 `docs/verification/coderabbit/batch-2-review.jsonl` 解析：
- `findings=1`
- `fixed=1`
- `unhandled_issues=0`

注意：本文件不再手写 CodeRabbit 成功字段，最终以自动生成的 `batch-2-evidence.json` 为准。

## 非本批问题和处理理由

- 3 个历史跳过测试 → 与批次2无关，留待后续批次处理
- `verify_database_migration.py` 仅支持 fixture 模式 → 当前符合批次要求，后续批次可扩展

## 阻断项

无。批次2已于 2026-06-18 通过 pre-commit 和 post-commit 门禁，正式关闭。

- 独立提交：`503a6b5`（`chore(gate): 批次2 pre-commit evidence 生成，关闭批次2`）
- pre-commit 门禁：通过（21/21 目标测试 + 4/4 迁移验证 + 34/34 门禁测试 + 空白检查）
- post-commit 门禁：通过
- evidence 文件：`docs/verification/batches/batch-2-evidence.json`

## 用户验收检查点

1. 查看新库初始化结果：`scripts/verify_database_migration.py --mode fixture` 输出中 `fresh_initialization` 检查
2. 查看二次迁移幂等：`idempotent_second_init` 检查，列数不变
3. 查看旧结构迁移：`legacy_migration` 检查，`is_neutral_venue` 列已添加
4. 查看备份恢复：`backup_readable` 检查，备份表数与原始一致
5. 表结构差异：对比 `core.schema_version` 表结构（version, applied_at, migration_name）
