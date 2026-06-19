import urllib.request
import urllib.error
import json
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        API_BASE_URL: str = Field(
            default="http://host.docker.internal:8000",
            description="Football Advisor FastAPI 服务地址。Docker 内用 host.docker.internal，本地用 127.0.0.1。",
        )
        PREDICT_TIMEOUT: int = Field(
            default=120,
            description="预测请求超时秒数（LLM 报告生成可能需要较长时间）。",
        )
        BACKTEST_TIMEOUT: int = Field(
            default=60,
            description="回测请求超时秒数。",
        )

    def __init__(self):
        self.valves = self.Valves()

    # ── 连接检测 ─────────────────────────────────────────────

    def check_api_status(self) -> str:
        """
        检测 Football Advisor FastAPI 后端服务是否在线。
        当用户询问"服务是否正常"、"API 状态"或需要排查连接问题时使用。
        :return: API 状态摘要。
        """
        url = f"{self.valves.API_BASE_URL}/health"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                status = data.get("status", "unknown")
                return f"Football Advisor API 状态: **{status}** (服务地址: {self.valves.API_BASE_URL})"
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", str(e))
            if isinstance(reason, ConnectionRefusedError) or "refused" in str(reason).lower():
                return (
                    f"Football Advisor API 未运行。\n"
                    f"请先启动 FastAPI 服务: `python -m uvicorn football_advisor.api:app --host 0.0.0.0 --port 8000`\n"
                    f"目标地址: {self.valves.API_BASE_URL}"
                )
            return f"无法连接到 Football Advisor API: {reason}"
        except Exception as e:
            return f"Football Advisor API 连接异常: {str(e)}"

    # ── 回测 ─────────────────────────────────────────────────

    def run_backtest(
        self, limit: int = 50, match_id_prefix: str = "SQLITE_MATCH_"
    ) -> str:
        """
        对历史比赛执行 Walk-Forward 回测，输出 ROI、胜率、资金曲线和 No Bet 原因分布。
        当用户询问历史表现、ROI、回测、胜率或资金曲线时使用此工具。

        :param limit: 回测样本数量，范围 1~10000，默认 50。
        :param match_id_prefix: 筛选比赛 ID 前缀，默认 SQLITE_MATCH_。
        :return: 结构化 Markdown 回测报告。
        """
        try:
            normalized_limit = int(limit)
        except (TypeError, ValueError):
            return "回测参数错误: limit 必须是整数。"

        normalized_limit = max(1, min(normalized_limit, 10000))
        url = f"{self.valves.API_BASE_URL}/backtest"
        payload = {"limit": normalized_limit, "match_id_prefix": match_id_prefix}

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.valves.BACKTEST_TIMEOUT) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            status = result.get("status")
            if status == "success":
                return _format_backtest_report(result)
            elif status == "no_data":
                return f"回测无数据: {result.get('message', '未找到符合条件的比赛。')}"
            elif status == "error":
                return f"回测失败: {result.get('message', '服务器内部错误。')}"
            else:
                return f"回测异常状态: {status}"

        except urllib.error.URLError as e:
            return _connection_error_message(e, self.valves.API_BASE_URL)
        except Exception as e:
            return f"回测请求失败: {str(e)}"

    # ── 预测 ─────────────────────────────────────────────────

    def predict_match(
        self,
        query: str,
        mode: str = "standard",
        home_team: str = "",
        away_team: str = "",
        kickoff_time: str = "",
        match_id: str = "",
        market: str = "",
    ) -> str:
        """
        对一场足球比赛发起赛前预测分析，返回完整的结构化报告。
        当用户询问具体比赛预测、投注建议或比赛分析时使用此工具。

        查询格式示例:
          - "Arsenal vs Chelsea"
          - "曼城 vs 利物浦"
          - "Real Madrid v Barcelona"

        :param query: 自然语言比赛查询（如 "Arsenal vs Chelsea"）。
        :param mode: 分析模式，'standard' 或 'deep'。
        :param home_team: 可选主队标准名称。
        :param away_team: 可选客队标准名称。
        :param kickoff_time: 可选开球时间，ISO 8601 格式。
        :param match_id: 可选内部比赛 ID。
        :param market: 可选玩法市场，例如 1x2。
        :return: 完整 Markdown 预测分析报告。
        """
        url = f"{self.valves.API_BASE_URL}/predict"
        payload = {"query": query, "mode": mode}
        for key, value in {
            "home_team": home_team,
            "away_team": away_team,
            "kickoff_time": kickoff_time,
            "match_id": match_id,
            "market": market,
        }.items():
            if value:
                payload[key] = value

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.valves.PREDICT_TIMEOUT) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                report = result.get("report", "")
                if not report:
                    return "预测服务返回了空报告，请检查输入或稍后重试。"
                return report

        except urllib.error.URLError as e:
            return _connection_error_message(e, self.valves.API_BASE_URL)
        except Exception as e:
            return f"预测请求失败: {str(e)}"


# ── 辅助函数 ─────────────────────────────────────────────────

def _format_backtest_report(result: dict) -> str:
    bets = result.get("bets", 0)
    roi = result.get("roi", 0.0)
    staged_win_rate = result.get("staged_win_rate")
    max_dd = result.get("max_drawdown")
    avg_odds = result.get("average_odds")
    equity = result.get("equity_curve", [])
    no_bet = result.get("no_bet_samples", 0)
    reasons = result.get("no_bet_reasons", {})
    metrics = result.get("metrics") or {}

    lines = [
        "## 回测结果",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 总投注数 | {bets} |",
        f"| ROI | {roi:.2%} |",
    ]
    if staged_win_rate is not None:
        lines.append(f"| 命中率 | {staged_win_rate:.2%} |")
    if max_dd is not None:
        lines.append(f"| 最大回撤 | {max_dd:.2f} 单位 |")
    if avg_odds is not None:
        lines.append(f"| 平均赔率 | {avg_odds:.2f} |")

    if metrics:
        lines.append("")
        lines.append("### 校准指标")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        if metrics.get("brier_score") is not None:
            lines.append(f"| Brier Score | {metrics['brier_score']:.4f} |")
        if metrics.get("log_loss") is not None:
            lines.append(f"| LogLoss | {metrics['log_loss']:.4f} |")
        if metrics.get("accuracy") is not None:
            lines.append(f"| Accuracy | {metrics['accuracy']:.2%} |")
        if metrics.get("sample_size") is not None:
            lines.append(f"| 校准样本 | {metrics['sample_size']} |")

    if no_bet:
        lines.append("")
        lines.append("### No Bet 跳过原因")
        lines.append("")
        for reason, count in reasons.items():
            lines.append(f"- **{reason}**: {count} 场")

    if equity:
        preview = equity[:10] + (["..."] if len(equity) > 10 else [])
        lines.append("")
        lines.append(f"### 资金曲线 (前 10 点)")
        lines.append(f"```\n{preview}\n```")

    return "\n".join(lines)


def _connection_error_message(error: urllib.error.URLError, base_url: str) -> str:
    reason = getattr(error, "reason", str(error))
    reason_str = str(reason).lower()
    if "refused" in reason_str or "connectionrefused" in reason_str:
        return (
            f"Football Advisor API 服务未运行。\n"
            f"请在项目目录执行启动命令:\n"
            f"```\nstart_fastapi.bat\n```\n"
            f"或手动启动:\n"
            f"```\npython -m uvicorn football_advisor.api:app --host 0.0.0.0 --port 8000\n```\n"
            f"目标地址: {base_url}"
        )
    return f"无法连接到 Football Advisor API ({base_url}): {reason}"
