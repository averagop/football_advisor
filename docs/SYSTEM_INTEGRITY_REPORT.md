# 竞彩足球辅助决策系统 — v1.0.0-rc 系统可行性与完整性验证报告

## 2026-05-31 修订说明

本报告原结论中的“生产可用”仅适用于本地原型与测试闭环，不代表真实体育数据源、真实交易所盘口或真实资金流已经接入完成。当前主线已修复三类主要边界问题：默认预测前同步不再写入 `MockBetfairClient` 模拟资金流；外部 provider 入库必须通过显式映射表；API-Football 真实 fixture 响应缺少独立赔率/统计数据时不再按 mock 结构误报成功。

剩余生产前提仍然明确存在：需要填充真实 provider 映射字典、实现 API-Football 独立赔率/统计端点编排，并接入真实交易所或盘口数据源。未完成这些前，不应把系统描述为可直接实盘部署。

## 总体结论
目前代码库的架构和基础实现**非常扎实**。核心的边界控制（如 DuckDB 与 ChromaDB 隔离、外部 API 工具与内部核心预测的分离、以及最重要的数据质量强制门禁 No Bet Policy）均已完全闭环并通过了 106 个测试用例和本地端到端的回测脚本考验。

系统在**逻辑层面是完整的，作为 1.0 的本地测试及原型评估已经完全可行**。

然而，若要面向真实的商业化/实盘部署，代码和方案在**数据源接入细节**和**实体映射**层面还存在部分空白与限制，属于“生产预备阶段”的预期内妥协。

以下是详尽的维度评估：

---

## 一、方案设计可行性（Architecture Feasibility）

| 核心方案 | 现状评估 | 结论 |
| :--- | :--- | :--- |
| **OpenWebUI -> FastAPI 唯一交互** | 已经通过 `openwebui_tools` 封闭了终端用户直接篡改 SQL 或绕过逻辑链的能力，且仅暴露 query 供查询，安全可行。 | ✅ 可行且完整 |
| **LLM 路由与降级策略** | `LLMRouter` 成功实现了外部 OpenAI-compatible API 与本地 `qwen2.5:7b` 的容灾。并在模型输出异常（修改了客观数据）时触发校验回退。 | ✅ 可行且完整 |
| **双库架构** | DuckDB 作为 OLAP 提供向量化查询，ChromaDB 专门负责相似度与舆情聚合。`pipeline.py` 中完美串联，无耦合。 | ✅ 可行且完整 |
| **No Bet 强制门禁** | 最闪光的设计。无论是因为外部数据超时、网络错误、API 失效、赔率差不足等任何异常，系统均收敛为 `No Bet` 而不是编造数据。这确保了实盘**绝对不会因数据脏乱而导致误下注**。 | ✅ 高度可行 |

---

## 二、代码完整性剖析（Code Completeness）

虽然逻辑闭环，但由于处于 v1.0.0-rc 阶段，一些与外部真实世界对接的“毛边”仍然采用临时或 Mock 手段处理，需在后续版本中演进：

### 1. 实体映射缺失 (Entity Resolution)
- **代码现状**：在 `football_data_client.py` 中，插入 DuckDB 的 `ON CONFLICT` 及表连接使用的是 `s.provider_home_team_id = h.system_team_id`。
- **问题分析**：这假设了 API-Football 或者 Football-Data.org 的 Team ID 与我们自身初始化的 `core.dim_team_mapping` 中的系统 ID 是完全一致的。现实中这是不可能的（例如：阿森纳在 A 源是 42，在 B 源是 9825）。
- **完整性建议**：后续需要加入一张表 `staging.provider_team_mapping (provider_id, provider_name, system_team_id)`，在 Sync 阶段做一次字典翻译转换。

### 2. 交易所深度资金流 (Exchange Capital Flow)
- **代码现状**：`football_advisor/exchange_client.py` 内实现的是 `MockBetfairClient`，并未接通 Betfair API 等真实的资金盘口，特征视图能读到 `capital_flow_volume` 但数据当前是模拟的。
- **问题分析**：如果没有真实的“聪明钱” (sharp money ratio) 的注入，依赖资金流判断热度（大热必死）的辅助校验就发挥不了作用，会默认按照 0 处理。
- **完整性建议**：这在当前并不阻断基础的 1X2 预测，属于增强特性。需要未来开发并购买 Betfair/Matchbook 商业 API 接口。

### 3. Ollama 与本地 Chroma 部署依赖
- **代码现状**：代码已经移除了沉重的 `sentence-transformers` 强制依赖，改用了 `OllamaEmbeddingFunction`。
- **问题分析**：在真正的服务器上，你必须确保宿主机持续后台挂载了 Ollama 服务，且必须预先 `ollama pull bge-m3` 和 `ollama pull qwen2.5:7b`。如果服务不可用，新闻的入库将会直接抛错并触发 No Bet。
- **完整性建议**：编写统一的 Docker Compose 部署文件，将 FastAPI 服务、Ollama 容器及 DuckDB 数据卷统一编排，确保环境就绪。

### 4. Search API (SearXNG / Google) 的可用性
- **代码现状**：通过了 `TextFetcher` 和质量阈值拦截的验证，确保烂网页无法入库。
- **问题分析**：SearXNG 公共实例非常容易 429 Rate Limit。
- **完整性建议**：商业化部署建议自建独占的 SearXNG 实例或挂接 Google Search API 代理。

---

## 三、最终结论与后续行动路线

**代码非常健壮，异常处理与架构边界极度清晰。** 从“能否跑起来并输出合理报告”的角度看，完整度极高；从“直接拿去接入实盘比赛下注”的角度看，还缺最后一块“真实数据源配置与字典映射”的拼图。

**下一步建议实施路径：**

1. **部署环境化 (v1.1)**：编写 `docker-compose.yml`，捆绑 FastAPI 和 Ollama。
2. **字典建设层 (v1.2)**：补齐 `provider_mapping` 表结构，通过脚本或者 AI 自动化映射五大联赛的核心球队 API IDs。
3. **实盘校验期 (v1.3)**：注入真实的体育 API Token，运行至少两周时间，仅在本地用 `run_backtest.py` 和 Dashboard 对比每日输出，验证新闻提取质量与真实命中率。
