# OpenWebUI 集成指南

本系统将 OpenWebUI 作为唯一面向用户的正式前端。为了防止用户绕过后台流程直接操作 DuckDB 或 ChromaDB，我们通过 FastAPI 提供了受控的业务编排接口，并将这些接口封装为 OpenWebUI 的自定义工具（Tools）。

## 架构说明

```text
OpenWebUI (前端 GUI)
  │
  │ (HTTP POST /predict, /backtest 等)
  ▼
FastAPI (Football Advisor 业务后台)
  │
  ├── DuckDBFeatureBuilder (结构化特征数据)
  ├── SearchRouter / TextFetcher (外围新闻数据抓取)
  └── OddsValueEngine / NoBetPolicy (核心概率与策略引擎)
```

**核心设计原则**：
1. **隔离性**：用户无法通过 OpenWebUI 直接发送 SQL 给 DuckDB。所有数据层查询对用户黑盒，统一由 FastAPI 暴露的受控 Endpoint 负责。
2. **唯一出口**：外部 LLM 不再直接读取 DuckDB 数据；预测请求经过 FastAPI 后台的各类引擎处理后，最终结果通过 API 返回给 OpenWebUI 供展示或格式化。

## 集成步骤

### 1. 启动后端 FastAPI 服务

确保 Football Advisor 后端系统正在运行并监听请求。
```bash
python -m uvicorn football_advisor.api:app --host 0.0.0.0 --port 8000
```

### 2. 在 OpenWebUI 注册自定义工具

OpenWebUI 提供了 Python 脚本机制来注册工具。您可以使用我们提供的预置脚本：

1. 打开 OpenWebUI 的管理页面 (Admin Panel)。
2. 导航到 **Workspace** -> **Tools** -> 点击 **"+" (Create Tool)**。
3. 将项目目录中 `openwebui_tools/football_advisor_tools.py` 的全部内容粘贴到代码框中。
4. 将工具命名为 `FootballAdvisor` 或其他易于辨识的名称。
5. 保存并启用该工具。

### 3. 配置 Valves（环境变量）

在使用工具前，请确保设定正确的服务端点：
- **API_BASE_URL**：指向你的 FastAPI 后台地址。
  - 如果 OpenWebUI 运行在 Docker 容器内，而 FastAPI 运行在物理宿主机上，请设置为 `http://host.docker.internal:8000`。
  - 如果两者在同一网络内，请设置为实际内网 IP 或域名。

## 支持的工具列表

| 工具名称 | 对应 API 路由 | 描述 |
| --- | --- | --- |
| `predict_match` | `POST /predict` | 用于发起一次赛前预测。传入自然语言 `query`，后台将自动调度 DuckDB 数据、新闻查询与赔率判断，最终返回综合投资建议。 |
| `run_backtest` | `POST /backtest` | 用于触发资金曲线（Equity Curve）和历史表现回测。支持指定样本数量上限，返回结构化的回测 ROI 与命中率结果。 |

## 高级提示：资金曲线绘制

对于 `run_backtest`，后台 `POST /backtest` 端点会返回原始的 JSON 数组结构。在 `football_advisor_tools.py` 的脚本中，我们将其格式化为 Markdown 返回给 OpenWebUI 的对话模型。
如果希望前端实现动态 Echarts 绘制，可以：
1. 修改 Tool 脚本直接返回 JSON 字符串。
## 附带的本地回测脚本 (`run_backtest.py`)

如果您尚未将系统部署至 OpenWebUI 前端，或仅希望在本地验证模型的基线表观，您也可以直接运行代码根目录下的 `run_backtest.py` 脚本：
```bash
python run_backtest.py
```
这将在本地自动加载所有的历史比赛，跑完 Walk-Forward 预测逻辑后，输出详细的资金曲线数据至 `equity_curve.json` 并生成可读的 `backtest_report.md`。这对于系统优化阶段（Stage 6.3 数据源调整前）的调参非常有帮助。
