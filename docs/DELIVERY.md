# 本地交付说明

## 交付目标

当前交付形态是本地 FastAPI 后台 + OpenWebUI 工具入口。OpenWebUI 是唯一用户前端，FastAPI 负责预测编排、数据同步、查询、No Bet 门禁和报告生成。

## 交付前检查

在项目根目录执行：

```powershell
.\.runtime\python\python.exe scripts\verify_delivery_readiness.py
.\.runtime\python\python.exe scripts\smoke_test_worldcup_readiness.py --home-team Greece --away-team Italy --date 2026-06-07 --match-id M_ST_2040143 --league "International Friendly"
.\.runtime\python\python.exe -m unittest discover -s tests -v
```

`verify_delivery_readiness.py` 只检查本地文件、依赖导入和环境变量是否存在，不联网、不读取远端服务，也不会输出真实密钥值。

## 启动后台

```powershell
start_fastapi.bat
```

默认服务地址：

- 本机访问：`http://127.0.0.1:8000`
- Docker 内 OpenWebUI 访问：`http://host.docker.internal:8000`
- 健康检查：`http://127.0.0.1:8000/health`
- 接口文档：`http://127.0.0.1:8000/docs`

如果 OpenWebUI 在 Docker 中运行，启动前设置：

```powershell
set FOOTBALL_HOST=0.0.0.0
start_fastapi.bat
```

## OpenWebUI 接入

将 `openwebui_tools/football_advisor_tools.py` 作为 OpenWebUI 工具导入。

工具默认后端地址是：

```text
http://host.docker.internal:8000
```

如果 OpenWebUI 与 FastAPI 都在本机非 Docker 环境运行，把工具 Valves 中的 `API_BASE_URL` 改为：

```text
http://127.0.0.1:8000
```

`predict_match` 支持以下可选参数；能明确提供时应优先传入，避免自然语言解析命中错误比赛：

- `home_team`
- `away_team`
- `kickoff_time`
- `match_id`
- `market`

## 必要配置

复制 `.env.example` 为 `.env`，只在本地填入真实值。不要把真实 API Key、Token、Cookie 或连接串写入代码、文档、测试快照或对话内容。

最小本地运行可使用默认 DuckDB 路径：

```text
FOOTBALL_DUCKDB_PATH=football_system.db
```

可选增强项按需配置：

- `FOOTBALL_EXTERNAL_LLM_BASE_URL`
- `FOOTBALL_EXTERNAL_LLM_API_KEY`
- `FOOTBALL_SERPER_API_KEY`
- `FOOTBALL_SEARXNG_BASE_URL`
- `FOOTBALL_SPORTTERY_BASE_URL`
- `API_FOOTBALL_TOKEN`
- `FOOTBALL_DATA_API_TOKEN`
- `THESPORTSDB_API_TOKEN`
- `THE_ODDS_API_TOKEN`
- `RAPIDAPI_TOKEN`
- `ODDS_FEED_RAPID_HOST`
- `ODDS_FEED_RAPID_BASE_URL`

`THE_ODDS_API_TOKEN` 当前接入为第三方 1X2 赔率交叉校验源。只有当 API 响应中出现 `matched_volume`、`totalMatched`、`traded_volume`、`volume`、`liquidity` 等字段时，系统才会把它标记为资金流候选源；否则不能称为真实资金流。

RapidAPI 需要同时配置 host 和具体 endpoint path。`ODDS_FEED_RAPID_BASE_URL` 不能只写 API 根域名，必须写到可返回比赛赔率/订单簿 JSON 的具体接口路径。真实 token 不得写入文档或对话。

## 真实门禁

通用比赛赛前真实门禁命令：

```powershell
.\.runtime\python\python.exe scripts\smoke_test_worldcup_readiness.py --home-team Greece --away-team Italy --date 2026-06-07 --match-id M_ST_2040143 --league "International Friendly"
```

世界杯赛前真实门禁命令：

```powershell
.\.runtime\python\python.exe scripts\smoke_test_worldcup_readiness.py --home-team Mexico --away-team "South Africa" --date 2026-06-09
```

该命令会读取本地 `.env` 并可能调用外部服务。执行前确认本地配置已脱敏管理，输出中不得打印真实密钥。

## 中国竞彩网官方竞彩足球同步

临时同步官方公开固定奖金，不写入 `.env`：

```powershell
$env:FOOTBALL_SPORTTERY_BASE_URL='https://www.sporttery.cn'
.\.runtime\python\python.exe scripts\sync_sporttery_odds.py
```

如果希望长期启用，把以下变量写入本地 `.env`：

```text
FOOTBALL_SPORTTERY_BASE_URL=https://www.sporttery.cn
```

同步脚本会执行以下动作：

- 初始化或更新 DuckDB schema。
- 写入明确维护的 Sporttery 基础实体和 provider mapping。
- 抓取中国竞彩网官方公开 `had` 胜平负固定奖金与 `hhad` 让球胜平负固定奖金。
- 先写入 `staging.stg_odds`，再经 `SportteryOfficialWeb` mapping 合并到 `core.fact_odds_capital_flow`。
- 未映射赛事只停留在 staging，不自动编造球队实体。

## 交付边界

- 系统是赛前辅助决策工具，不是自动投注系统。
- LLM 只生成报告，不生成或改写概率。
- 数据不足、赔率缺失、新闻信号缺失、同步失败或质量门禁失败时，必须进入 No Bet。
- 未配置交易所资金流 provider 时不单独触发同步质量失败；报告必须标注“资金流数据未接入，风险评估缺少该维度”。
- No Bet 报告不得在结论区展示“最佳价值候选”，只能展示模型倾向观察和门禁原因。
- 友谊赛近期状态只能作为状态补源，不能替代目标赛赔率、阵容或伤停。
