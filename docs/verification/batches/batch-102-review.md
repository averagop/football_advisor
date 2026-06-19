# 批次 102 复核记录

## 范围

- 修复公开仓库首次发布后的 GitHub Actions 批次门禁执行环境。
- 移除 workflow 对仓库级 `FOOTBALL_ADVISOR_BATCH` 变量的固定依赖，避免后续检查被锁定到旧批次。
- 为 Windows Actions 设置 Python UTF-8 输出，避免中文门禁信息触发编码错误。

## 风险判断

- 本批次只调整 CI 门禁配置和对应测试，不修改业务预测逻辑。
- `.github/workflows/batch-gate.yml` 属于高风险路径，必须保留外部复审产物。

## 结论

- 待自动 evidence 和 CodeRabbit 产物闭合后进入提交。
