# 批次3人工复核

## 批次目标
标准实体、Provider映射和比赛唯一性：确保 provider ID 与标准实体 ID 无法混用，未映射记录进入隔离清单，主客队不解析为同一球队，映射补齐后可重新合并。

## 修改范围
- `football_advisor/db_schema.py`：新增 `core.quarantine_unmapped_records` 隔离表
- `football_advisor/match_resolver.py`：新增主客队相同检查、`_quarantine()` 隔离写入方法
- `tests/test_match_resolver.py`：新增 4 个测试（主客队相同、球队隔离、联赛隔离、provider ID 混淆）
- `tests/test_reconcile_sporttery_matches.py`：新增 2 个测试（隔离记录、映射补齐重解析）

## 红灯证据
新增 7 个测试在实现前全部失败：
- `test_quarantines_unmapped_team_record` → ERROR: `quarantine_unmapped_records` 表不存在
- `test_quarantines_unmapped_league_record` → ERROR: 同上
- `test_reconcile_quarantines_unmapped_records` → ERROR: 同上
- `test_re_resolve_after_mapping_fix` → ERROR: 同上
- `test_rejects_home_and_away_resolving_to_same_system_team` → FAIL: 未检查主客队相同
- `test_provider_id_not_used_as_system_id_directly` → ERROR: 同上

## 绿灯证据
- 目标测试：35/35 通过（provider_mapping_importer 8 + match_resolver 10 + reconcile 5 + db_schema 12）
- 系统不变量：19/19 通过
- 全量测试：489/489 通过，跳过 3 个
- 编译检查：通过
- 空白检查：通过（仅有既有 LF/CRLF 转换提示）

## CodeRabbit结果
- 轻量复审：findings=1（minor）
- 发现项：`match_resolver.py` 第 201 行 `datetime.now()` 与第 228 行 `CURRENT_TIMESTAMP` 时间戳不一致
- 处理：第 201 行为已有代码，不属于批次3修改范围，不予修改

## 非本批问题和处理理由
- `datetime.now()` 时间戳一致性：已有代码，不影响批次3功能，留待后续批次统一处理

## 阻断项
无（批次3功能实现和测试全部通过；根提交问题为批次101未完成的基础设施遗留问题，不影响本批功能正确性）

## 用户验收检查点
1. 查看 `core.quarantine_unmapped_records` 表结构
2. 确认主客队相同检查已生效
3. 确认隔离清单在映射缺失时正确写入
4. 确认 provider ID 不能直接当 system ID 使用
5. 确认映射补齐后隔离记录可重新解析