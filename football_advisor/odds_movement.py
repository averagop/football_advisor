"""赔率走势分析引擎。

从 staging.stg_odds_snapshot 读取多日赔率快照，检测赔率变动信号，
输出"赔率趋势"作为预测模型的辅助输入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class OddsMovementSignal:
    match_id: str
    market_type: str  # "1X2"
    signal_type: str  # "home_drop", "away_drop", "draw_drop", "stable", "sharp_move"
    direction: str  # "home", "away", "draw", "none"
    first_odds: dict[str, float] = field(default_factory=dict)
    last_odds: dict[str, float] = field(default_factory=dict)
    change_pct: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0  # 信号置信度 0-1
    summary: str = ""


class OddsMovementEngine:
    """赔率走势分析引擎。

    分析逻辑:
      - 降赔（赔率下跌）= 市场看好 → 正向信号
      - 赔率稳定 → 无信号
      - 骤变（单日变化 >15%）→ 可能有内幕消息 → 高置信度信号
    """

    # 阈值配置
    SHARP_MOVE_THRESHOLD = 0.15   # 单日变动超过 15% 视为骤变
    SIGNIFICANT_MOVE = 0.05        # 累计变动超过 5% 视为显著趋势
    MIN_SNAPSHOT_DAYS = 2          # 至少需要 2 天快照才能分析

    def __init__(self, duckdb_path: str = "football_system.db") -> None:
        self._duckdb_path = duckdb_path

    def analyze_match(self, match_id: str) -> OddsMovementSignal | None:
        """分析单场比赛的赔率走势。"""
        import duckdb

        with duckdb.connect(self._duckdb_path) as conn:
            snapshots = conn.execute(
                """SELECT snapshot_date, home_odds, draw_odds, away_odds
                   FROM staging.stg_odds_snapshot
                   WHERE match_id = ?
                     AND market_type = '1X2'
                   ORDER BY snapshot_date ASC""",
                [match_id],
            ).fetchall()

        if len(snapshots) < self.MIN_SNAPSHOT_DAYS:
            return None

        first = snapshots[0]
        last = snapshots[-1]

        first_odds = {"home": float(first[1]), "draw": float(first[2]), "away": float(first[3])}
        last_odds = {"home": float(last[1]), "draw": float(last[2]), "away": float(last[3])}

        # 计算变动百分比（赔率下跌 = 正值）
        change = {
            k: round((first_odds[k] - last_odds[k]) / first_odds[k], 4)
            if first_odds[k] != 0 else 0.0
            for k in first_odds
        }

        # 检测骤变（逐日检查）
        has_sharp = False
        sharp_target = ""
        largest_sharp_change = 0.0
        for i in range(1, len(snapshots)):
            prev = {"home": float(snapshots[i - 1][1]), "draw": float(snapshots[i - 1][2]), "away": float(snapshots[i - 1][3])}
            curr = {"home": float(snapshots[i][1]), "draw": float(snapshots[i][2]), "away": float(snapshots[i][3])}
            for k in ("home", "draw", "away"):
                if prev[k] != 0:
                    day_change = abs((prev[k] - curr[k]) / prev[k])
                    if (
                        day_change > self.SHARP_MOVE_THRESHOLD
                        and day_change > largest_sharp_change
                    ):
                        has_sharp = True
                        sharp_target = k
                        largest_sharp_change = day_change

        # 判定信号类型
        if has_sharp:
            signal_type = "sharp_move"
            direction = sharp_target
            confidence = 0.85
            summary = f"赔率骤变：{sharp_target}方向单日变动超过 {self.SHARP_MOVE_THRESHOLD * 100:.0f}%，可能有突发新闻或内幕信息。"
        else:
            max_change = max(abs(change["home"]), abs(change["draw"]), abs(change["away"]))
            if max_change < self.SIGNIFICANT_MOVE:
                signal_type = "stable"
                direction = "none"
                confidence = 0.3
                summary = "赔率稳定，市场无明显倾向变化。"
            else:
                best_direction = max(change, key=lambda k: change[k])  # 最大降赔方向
                signal_type = f"{best_direction}_drop"
                direction = best_direction
                confidence = min(0.7, 0.4 + abs(change[best_direction]) * 4)
                label_map = {"home": "主胜", "draw": "平局", "away": "客胜"}
                summary = (
                    f"{label_map.get(best_direction, best_direction)}赔率持续下跌 "
                    f"({abs(change[best_direction]) * 100:.1f}%)，市场资金向{label_map.get(best_direction, best_direction)}倾斜。"
                )

        return OddsMovementSignal(
            match_id=match_id,
            market_type="1X2",
            signal_type=signal_type,
            direction=direction,
            first_odds=first_odds,
            last_odds=last_odds,
            change_pct=change,
            confidence=confidence,
            summary=summary,
        )

    def batch_analyze(self, match_ids: list[str]) -> dict[str, OddsMovementSignal]:
        """批量分析多场比赛的赔率走势。"""
        results: dict[str, OddsMovementSignal] = {}
        for mid in match_ids:
            signal = self.analyze_match(mid)
            if signal:
                results[mid] = signal
        return results

    def get_matches_with_snapshots(self) -> list[str]:
        """返回有赔率快照记录的所有比赛。"""
        import duckdb

        with duckdb.connect(self._duckdb_path) as conn:
            rows = conn.execute(
                """SELECT DISTINCT match_id FROM staging.stg_odds_snapshot
                   GROUP BY match_id
                   HAVING COUNT(DISTINCT snapshot_date) >= ?""",
                [self.MIN_SNAPSHOT_DAYS],
            ).fetchall()
        return [str(row[0]) for row in rows]
