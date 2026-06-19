# 竞彩足球辅助决策系统 — 代码审查、方案分析与后续开发建议报告

> 审查时间：2026-05-24
> 审查范围：所有 `football_advisor/` 源代码、`tests/`、`docs/` 文档
> 本文档是项目的持续改进指南，其他 AI 模型在接手项目时请首先阅读此建议。

---

## 总体架构评估

**整体方案非常合理，架构边界清晰，核心业务逻辑设计严谨。** 系统严格遵循以下架构规范：
1. **双库分离**：DuckDB 只存结构化数值/统计数据，ChromaDB 只存非结构化新闻舆情文本。
2. **安全防线**：No Bet 机制是强制安全门，凡是触发数据过期、数据质量不合格或无价值时，必须拦截预测并建议 No Bet。
3. **LLM 纯文本化**：LLM 仅作为报告生成器，绝对不参与概率计算，防止大模型数值幻觉。
4. **防穿越回测**：回测严格按 `match_time ASC` 进行，且特征计算采用 prior-only 机制，防未来数据泄漏。

---

## 🔴 已修复问题（2026-05-24 修复完成）

以下严重 Bug (P0) 和中度问题 (P1) 已在新分支 `fix/p0-p1-bugs` 中得到修复，且 31 个单元测试全部通过：

### 1. `pipeline.py` — 占位降级赔率泄漏安全漏洞【已修复】
- **问题**：在 DuckDB 特征未命中降级到 fallback 时，系统会使用硬编码的占位赔率 `{"home": 2.05, "draw": 3.20, "away": 3.45}`，可能导致误推荐投注。
- **修复**：清空了 fallback 中的硬编码赔率（设置为 `{}`），在 `context` 中增加了 `no_bet_data_quality_flag: True`。同时在 `NoBetPolicy.decide()` 中增加了对 `placeholder_fallback` 的显式拦截逻辑，一旦检测到直接建议 No Bet 并标记为高风险。

### 2. `probability_engine.py` — 主场优势计算量纲错误【已修复】
- **问题**：`_expected_home_goals()` 中将 `features.home.home_advantage`（值为比率，0.0 到 0.22）直接加到预估进球数（xG）上，导致量纲不匹配。
- **修复**：修改为主场优势的乘法比例加成：`* (1.0 + features.home.home_advantage)`。

### 3. `db_schema.py` — 视图时间戳的 1970 污染【已修复】
- **问题**：多源数据（结构化、赔率、新闻）在缺失某一项时，原本的 `LEAST + COALESCE` 结构会使整个数据更新时间 `data_updated_at` 回退到 `1970-01-01`，从而误触发数据过期拦截。
- **修复**：简化了 `view_match_feature_base` 和 `view_llm_match_prediction_base` 中的时间戳运算，改为更安全、更规范的 `COALESCE(LEAST(...), TIMESTAMP '1970-01-01')` 结构。若只有一侧数据缺失，依然保留另一侧的真实更新时间。

### 4. `api.py` — 顶层导入即初始化导致阻塞【已修复】
- **问题**：`app = create_app()` 放在顶层直接执行，导致没有安装 `fastapi` 的环境（如部分测试环境）导入包时会直接 crash。
- **修复**：将 `app = create_app()` 包裹在 `try...except ImportError` 块中。

### 5. `query_tools.py` & `feature_builder.py` — 存在 SQL 注入隐患【已修复】
- **问题**：在通过 `match_id` 或球队名拼接 SQL 查询时，使用了单引号转义，不够安全。
- **修复**：将 `DuckDBStatsQueryTool` 的 `query_match_stats` 改造为支持参数列表；重构 `DuckDBFeatureBuilder` 将 SQL 拼接重构为标准的 `?` 参数化占位符形式。

### 6. `csv_importer.py` — 缺乏事务保护【已修复】
- **问题**：在向 DuckDB 进行批量 CSV 插入时没有包裹在事务中，容易因意外中断产生脏数据。
- **修复**：为 `import_csv_directory` 里的多表插入增加了 `BEGIN TRANSACTION / COMMIT / ROLLBACK` 保护。

---

## 🟡 后续开发建议（其他模型可参考实现）

以下是系统当前存在的**设计缺陷、未实现功能缺口**，建议后续接手的 AI 模型按优先级进行修改和迭代：

### 1. P2 改进：`news_tools.py` ChromaDB 检索距离过滤（高优先级）
- **问题**：ChromaDB 的 `query()` 默认会根据 `limit` 强行返回相似度最贴近的文档（即使相似度极低）。这会导致在没有相关新闻时，将 8 篇风马牛不相及的噪音新闻混入报告中。
- **建议**：在 `ChromaNewsSearchTool.search_team_news` 中对返回结果的 `distances` 设定 cosine 距离阈值限制（例如 `distance < 0.7`），只保留真正相关的队报。

### 2. P2 改进：`probability_engine.py` 模型权重参数化与回测校准优化
- **问题**：初版模型中的 Poisson 分布权重 `0.72` 和 Elo 权重 `0.28`，以及状态修正的 `±0.035` 幅度，均为硬编码常数，缺乏统计校准。
- **建议**：
  - 将这些常数提取为模型的 `__init__` 参数。
  - 基于回测数据，使用 `CalibrationEngine` 输出的 Brier Score 或 LogLoss 作为目标函数，加入一个简易的网格搜索（Grid Search）或 L-BFGS 参数调整机制，以对权重进行动态校准，提升命中率。

### 3. P2 改进：让球/大小球市场的 Odds 价值引擎支持
- **问题**：目前 `OddsValueEngine` 只支持 1X2 市场的隐含概率和价值差计算，无法支持竞彩最核心的“让球胜平负”和“总进球数/大小球”玩法。
- **建议**：
  - 在 `db_schema.py` 里的 `core.fact_odds_capital_flow` 引入让球盘盘口与大小球盘口。
  - 在 `OddsValueEngine` 中扩展 `assess_handicap()` 和 `assess_totals()` 方法。
  - 通过 Poisson 比分网格概率（已在 `ProbabilityResult` 暴露的比分矩阵和 over_2_5 概率）与让球/大小球盘口赔率进行价值对比。

### 4. P3 优化：基于校准或历史命中率的置信度转换
- **问题**：目前 `no_bet_policy.py` 中的 `confidence` 置信度计算非常简单（`min(0.86, max(0.35, edge * 7.0))`），只与价值差（edge）线性挂钩，86% 的上限设置也缺乏依据。
- **建议**：引入 Platt Calibration (普拉特校准) 或根据历史回测的“分段价值区间真实命中率”作为映射函数，使报告输出的置信度真正反映统计意义上的获胜概率。

### 5. P3 优化：回测引擎中增加投注命中率（Staged Win Rate）与资金曲线
- **问题**：目前 `backtest_engine.py` 的 ROI 统计仅针对单次投注 flat-bet 模式（赢则赚 `odds - 1.0`，输则亏 `1.0`），未区分策略过滤后的效果。
- **建议**：
  - 增加投注命中率（只统计被标记为可投注的样本）和全样本命中率的对比。
  - 引入资金曲线模拟（以初始资金 100 单位开始，模拟 flat-bet 或 Kelly 准则下的余额走势），计算最大实际资金回撤。

---

## 📋 架构规划与 staging 设计（中长期建议）

1. **Staging 暂存表设计**：
   - 现有的 `games.json`、`players_merged.json` 以及未来外部数据源的数据，不应直接写入 `core.*` 物理表。
   - 应建立 `staging.*` 模式。数据先落入 Staging 表，经过实体解析映射（Entity Resolution）确定球队/联赛 ID 后，再增量合并（UPSERT）到 `core.*` 维度与事实表中，防止脏数据污染数仓。
2. **Elo 动态更新**：
   - 建立 `core.fact_team_elo_history` 表。在导入历史比赛或同步最新赛果时，动态更新球队的 Elo，而不是使用 `dim_team_mapping` 里的静态基准分，以使概率模型的 Elo 预测更精确。
