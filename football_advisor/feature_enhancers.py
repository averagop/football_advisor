"""
特征增强器 (Feature Enhancers)

在 feature_builder 构建核心特征后、probability_engine 计算概率前，
从 DuckDB 补充报告和风险上下文。未经校准批准的增强信号不得改变概率。

每个增强器独立查询、独立注入，单个失败不影响其他增强器。

增强器列表：
  - FormationEnhancer: 阵型 → 报告上下文
  - H2HEnhancer: 历史交锋 → 心理优势/劣势
  - RestDaysEnhancer: 休息天数 → 体能影响
  - OddsTrendEnhancer: 赔率变动 → 市场情绪
  - LineupConfirmationEnhancer: 阵容/伤停 → 风险上下文
  - StandingsEnhancer: 同联赛同赛季积分榜 → 赛季状态上下文
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from .models import MatchFeatures, TeamFeatures

logger = logging.getLogger(__name__)


# ============================================================
# 阵型分析
# ============================================================

# 阵型 → 进攻倾向分 (0=纯防守, 1=纯进攻)
FORMATION_ATTACK_BIAS: dict[str, float] = {
    "4-3-3": 0.80,
    "4-2-3-1": 0.75,
    "3-4-3": 0.85,
    "3-4-2-1": 0.75,
    "4-1-4-1": 0.65,
    "4-4-2": 0.50,
    "4-4-1-1": 0.45,
    "4-3-2-1": 0.55,
    "3-5-2": 0.60,
    "3-5-1-1": 0.50,
    "5-3-2": 0.30,
    "5-4-1": 0.20,
    "4-5-1": 0.30,
    "4-1-3-2": 0.70,
    "4-2-2-2": 0.55,
    "3-6-1": 0.25,
    "4-3-1-2": 0.70,
    "3-4-1-2": 0.65,
    "4-2-4": 0.90,
    "3-3-4": 0.95,
    "4-1-2-1-2": 0.60,
}


def _parse_formation(raw: str) -> str:
    """标准化阵型字符串。"""
    if not raw:
        return ""
    raw = raw.strip().replace(" ", "")
    if "-" not in raw:
        return ""
    parts = raw.split("-")
    if len(parts) < 2:
        return ""
    # 保留所有部分（如 4-2-3-1, 4-1-2-1-2）
    return raw


def _get_formation_bias(formation: str) -> float:
    """获取阵型进攻倾向分。"""
    if not formation:
        return 0.50
    return FORMATION_ATTACK_BIAS.get(formation, 0.50)


def _formation_midfield_count(formation: str) -> int:
    """估算中场人数（用于阵型克制分析），非数字字段返回默认值 3。"""
    if not formation:
        return 3
    parts = formation.split("-")
    try:
        if len(parts) == 3:
            return int(parts[1])  # 4-3-3 → 3
        if len(parts) == 4:
            return int(parts[1]) + int(parts[2])  # 4-2-3-1 → 2+3=5
        if len(parts) >= 5:
            return int(parts[1]) + int(parts[2])  # 4-1-2-1-2 → 1+2=3
    except (ValueError, TypeError):
        return 3
    return 3


def _formation_wing_count(formation: str) -> int:
    """估算边路人数。"""
    if not formation:
        return 2
    # 简化：4-3-3 有边锋，3-4-3 有翼卫
    if formation in ("4-3-3", "3-4-3", "4-2-3-1", "3-4-2-1", "4-2-4"):
        return 3
    if formation in ("5-3-2", "3-5-2", "5-4-1"):
        return 2
    return 2


class FormationEnhancer:
    """阵型增强器：从 stg_lineups 提取阵型，计算进攻/防守倾向。"""

    def __init__(self, database_path: str = "football_system.db") -> None:
        self._db = database_path

    def enhance(self, features: MatchFeatures, match_id: str) -> MatchFeatures:
        context = dict(features.context)
        try:
            result = self._query_formations(match_id)
            if not result:
                logger.debug("No formation data for %s", match_id)
                return features

            home_formation, away_formation = result
            home_bias = _get_formation_bias(home_formation)
            away_bias = _get_formation_bias(away_formation)

            # 阵型克制：中场人数差
            home_mid = _formation_midfield_count(home_formation)
            away_mid = _formation_midfield_count(away_formation)
            midfield_diff = home_mid - away_mid

            # 边路人数差
            home_wing = _formation_wing_count(home_formation)
            away_wing = _formation_wing_count(away_formation)
            wing_diff = home_wing - away_wing

            context["formation_home"] = home_formation
            context["formation_away"] = away_formation
            context["formation_home_attack_bias"] = round(home_bias, 2)
            context["formation_away_attack_bias"] = round(away_bias, 2)
            context["formation_midfield_diff"] = midfield_diff
            context["formation_wing_diff"] = wing_diff

            # 进攻倾向调整：主队进攻倾向 - 客队防守倾向
            # 4-3-3(0.80) vs 5-4-1(0.20) → 主队进攻优势 0.60
            attack_advantage = home_bias - (1.0 - away_bias)
            context["formation_attack_advantage"] = round(attack_advantage, 2)

            logger.info(
                "Formation: %s (%s, bias=%.2f) vs %s (%s, bias=%.2f), attack_adv=%.2f",
                features.home.name, home_formation, home_bias,
                features.away.name, away_formation, away_bias,
                attack_advantage,
            )
        except Exception as exc:
            logger.warning("Formation enhancer failed: %s", exc)

        return replace(features, context=context)

    def _query_formations(self, match_id: str) -> tuple[str, str] | None:
        import duckdb

        try:
            with duckdb.connect(self._db) as conn:
                row = conn.execute(
                    """
                    SELECT
                        MAX(CASE WHEN tm.system_team_id = ms.home_team_id
                            THEN l.formation END) AS home_formation,
                        MAX(CASE WHEN tm.system_team_id = ms.away_team_id
                            THEN l.formation END) AS away_formation
                    FROM staging.stg_lineups l
                    INNER JOIN core.dim_provider_match_mapping mm
                        ON l.provider_match_id = mm.provider_match_id
                       AND l.source_provider = mm.provider_name
                    INNER JOIN core.fact_match_schedule ms
                        ON mm.system_match_id = ms.match_id
                    INNER JOIN core.dim_provider_team_mapping tm
                        ON l.source_provider = tm.provider_name
                       AND LOWER(TRIM(l.team_name)) =
                           LOWER(TRIM(tm.provider_team_name))
                       AND tm.system_team_id IN
                           (ms.home_team_id, ms.away_team_id)
                    INNER JOIN core.dim_player_mapping pm
                        ON tm.system_team_id = pm.team_id
                       AND LOWER(TRIM(l.player_name)) =
                           LOWER(TRIM(pm.player_standard_name))
                    WHERE mm.system_match_id = ?
                      AND l.etl_insert_timestamp >=
                          CURRENT_TIMESTAMP - INTERVAL '30 minutes'
                    """,
                    [match_id],
                ).fetchone()

                if not row:
                    return None

                home_f = _parse_formation(str(row[0] or ""))
                away_f = _parse_formation(str(row[1] or ""))

                if home_f and away_f:
                    return home_f, away_f
                return None
        except Exception:
            return None


# ============================================================
# H2H 历史交锋
# ============================================================

class H2HEnhancer:
    """H2H 增强器：从 fact_match_context_summary 提取历史交锋数据。"""

    def __init__(self, database_path: str = "football_system.db") -> None:
        self._db = database_path

    def enhance(self, features: MatchFeatures, match_id: str) -> MatchFeatures:
        context = dict(features.context)
        try:
            row = self._query_h2h(match_id)
            if not row:
                return features

            h2h_home = row[0] or 0
            h2h_draw = row[1] or 0
            h2h_away = row[2] or 0
            total = h2h_home + h2h_draw + h2h_away

            if total == 0:
                return features

            h2h_home_rate = h2h_home / total
            h2h_away_rate = h2h_away / total

            context["h2h_total_matches"] = total
            context["h2h_home_wins"] = h2h_home
            context["h2h_draws"] = h2h_draw
            context["h2h_away_wins"] = h2h_away
            context["h2h_home_win_rate"] = round(h2h_home_rate, 3)
            context["h2h_away_win_rate"] = round(h2h_away_rate, 3)

            # 心理优势分：主队胜率 - 客队胜率，范围 -1 到 1
            psychological_edge = h2h_home_rate - h2h_away_rate
            context["h2h_psychological_edge"] = round(psychological_edge, 3)

            logger.info(
                "H2H: %s %dW-%dD-%dL vs %s (edge=%.2f)",
                features.home.name, h2h_home, h2h_draw, h2h_away,
                features.away.name, psychological_edge,
            )
        except Exception as exc:
            logger.warning("H2H enhancer failed: %s", exc)

        return replace(features, context=context)

    def _query_h2h(self, match_id: str) -> tuple | None:
        import duckdb

        try:
            with duckdb.connect(self._db) as conn:
                return conn.execute(
                    """SELECT h2h_home_wins, h2h_draws, h2h_away_wins
                       FROM core.fact_match_context_summary
                       WHERE match_id = ?""",
                    [match_id],
                ).fetchone()
        except Exception:
            return None


# ============================================================
# 休息天数
# ============================================================

class RestDaysEnhancer:
    """休息天数增强器：从 fact_match_schedule 提取休息天数。"""

    def __init__(self, database_path: str = "football_system.db") -> None:
        self._db = database_path

    def enhance(self, features: MatchFeatures, match_id: str) -> MatchFeatures:
        context = dict(features.context)
        try:
            row = self._query_rest_days(match_id)
            if not row:
                return features

            rest_home = row[0]
            rest_away = row[1]

            if rest_home is None and rest_away is None:
                return features

            rest_home = rest_home or 5
            rest_away = rest_away or 5

            context["rest_days_home"] = rest_home
            context["rest_days_away"] = rest_away
            context["rest_days_diff"] = rest_home - rest_away

            # 休息不足（< 3 天）的体能惩罚
            fatigue_penalty_home = max(0.0, (3.0 - rest_home) * 0.03)
            fatigue_penalty_away = max(0.0, (3.0 - rest_away) * 0.03)
            context["rest_fatigue_penalty_home"] = round(fatigue_penalty_home, 3)
            context["rest_fatigue_penalty_away"] = round(fatigue_penalty_away, 3)
        except Exception as exc:
            logger.warning("Rest days enhancer failed: %s", exc)

        return replace(features, context=context)

    def _query_rest_days(self, match_id: str) -> tuple | None:
        import duckdb

        try:
            with duckdb.connect(self._db) as conn:
                return conn.execute(
                    """SELECT rest_days_home, rest_days_away
                       FROM core.fact_match_schedule
                       WHERE match_id = ?""",
                    [match_id],
                ).fetchone()
        except Exception:
            return None


# ============================================================
# 赔率变动趋势
# ============================================================

class OddsTrendEnhancer:
    """赔率趋势增强器：从 fact_odds_capital_flow 分析赔率变动。

    限定单一可比序列：仅分析竞彩官方 SPF 赔率（odds_type='1X2'，
    source_provider='SportteryOfficialWeb'，bookmaker_name='SportteryOfficialWeb'）。
    官方竞彩趋势和第三方市场趋势分别计算，不混合。
    """

    def __init__(self, database_path: str = "football_system.db") -> None:
        self._db = database_path

    def enhance(self, features: MatchFeatures, match_id: str) -> MatchFeatures:
        context = dict(features.context)
        try:
            trend = self._query_odds_trend(match_id)
            if not trend:
                context["odds_trend_status"] = "insufficient_comparable_snapshots"
                return replace(features, context=context)

            context["odds_trend_home_direction"] = trend["home_direction"]
            context["odds_trend_home_change_pct"] = round(trend["home_change_pct"], 4)
            context["odds_trend_sharp_alignment"] = round(trend["sharp_alignment"], 3)
            context["odds_trend_snapshot_count"] = trend["snapshot_count"]
            context["odds_trend_status"] = "success"

            # 市场情绪：赔率下降 = 市场看好
            # sharp_alignment > 0 表示聪明钱与赔率变动方向一致
            market_sentiment = trend["sharp_alignment"] * (-trend["home_change_pct"])
            context["odds_trend_market_sentiment"] = round(market_sentiment, 4)

            logger.info(
                "Odds trend: home %s (%.1f%%), sharp_align=%.2f, snapshots=%d",
                trend["home_direction"], trend["home_change_pct"] * 100,
                trend["sharp_alignment"], trend["snapshot_count"],
            )
        except Exception as exc:
            logger.warning("Odds trend enhancer failed: %s", exc)
            context["odds_trend_status"] = "failed"

        return replace(features, context=context)

    def _query_odds_trend(self, match_id: str) -> dict[str, Any] | None:
        import duckdb

        try:
            with duckdb.connect(self._db) as conn:
                # 限定竞彩官方 SPF（1X2）赔率趋势，隔离玩法和供应商
                rows = conn.execute(
                    """
                    WITH ranked AS (
                        SELECT
                            home_odds, draw_odds, away_odds,
                            sharp_money_ratio,
                            ROW_NUMBER() OVER (ORDER BY snapshot_time ASC) AS rn_asc,
                            ROW_NUMBER() OVER (ORDER BY snapshot_time DESC) AS rn_desc
                        FROM core.fact_odds_capital_flow
                        WHERE match_id = ?
                          AND odds_type = '1X2'
                          AND source_provider = 'SportteryOfficialWeb'
                          AND bookmaker_name = 'SportteryOfficialWeb'
                    ),
                    first_snapshot AS (
                        SELECT home_odds AS first_home, draw_odds AS first_draw, away_odds AS first_away
                        FROM ranked WHERE rn_asc = 1
                    ),
                    last_snapshot AS (
                        SELECT home_odds AS last_home, draw_odds AS last_draw, away_odds AS last_away,
                               sharp_money_ratio
                        FROM ranked WHERE rn_desc = 1
                    ),
                    snapshot_count AS (
                        SELECT COUNT(*) AS cnt FROM ranked
                    )
                    SELECT f.first_home, f.first_draw, f.first_away,
                           l.last_home, l.last_draw, l.last_away,
                           l.sharp_money_ratio, s.cnt
                    FROM first_snapshot f, last_snapshot l, snapshot_count s
                    """,
                    [match_id],
                ).fetchone()

                if not rows or int(rows[7]) < 2:  # 至少需要 2 个同源同玩法快照
                    return None

                first_home = float(rows[0] or 1.0)
                last_home = float(rows[3] or 1.0)
                sharp_ratio = float(rows[6] or 0.0)

                change_pct = (last_home - first_home) / first_home if first_home > 0 else 0.0
                direction = "down" if change_pct < -0.02 else ("up" if change_pct > 0.02 else "stable")

                return {
                    "home_direction": direction,
                    "home_change_pct": change_pct,
                    "sharp_alignment": sharp_ratio,
                    "snapshot_count": int(rows[7]),
                }
        except Exception:
            return None


# ============================================================
# 积分榜/赛季状态
# ============================================================

class StandingsEnhancer:
    """积分榜增强器：读取比赛对应联赛和赛季的积分榜事实。

    积分榜作为赛季状态辅助，不直接替代近期状态。
    权重需回测验证后进入生产。
    """

    def __init__(self, database_path: str = "football_system.db") -> None:
        self._db = database_path

    def enhance(self, features: MatchFeatures, match_id: str) -> MatchFeatures:
        context = dict(features.context)
        try:
            row = self._query_standings(match_id)
            if not row:
                return features

            context["standings_home_rank"] = row[0]
            context["standings_away_rank"] = row[1]
            context["standings_home_points"] = row[2]
            context["standings_away_points"] = row[3]
            context["standings_home_goal_diff"] = row[4]
            context["standings_away_goal_diff"] = row[5]
            context["standings_source_provider"] = "core.fact_league_standings"
            context["standings_updated_at"] = (
                row[6].isoformat() if row[6] else None
            )

            # 积分榜差异作为赛季强度辅助信号
            if row[0] is not None and row[1] is not None:
                rank_diff = int(row[1]) - int(row[0])  # 正数 = 主队排名更高
                context["standings_rank_diff"] = rank_diff
                # 排名差越大，质量越高
                context["standings_quality"] = "high" if abs(rank_diff) >= 5 else "medium"
        except Exception as exc:
            logger.warning("Standings enhancer failed: %s", exc)

        return replace(features, context=context)

    def _query_standings(self, match_id: str) -> tuple | None:
        import duckdb

        try:
            with duckdb.connect(self._db) as conn:
                return conn.execute(
                    """
                    SELECT
                        h.rank_position,
                        a.rank_position,
                        h.points,
                        a.points,
                        h.goals_diff,
                        a.goals_diff,
                        GREATEST(h.updated_at, a.updated_at)
                    FROM core.fact_match_schedule ms
                    INNER JOIN core.fact_league_standings h
                        ON ms.system_league_id = h.system_league_id
                       AND ms.home_team_id = h.system_team_id
                       AND TRY_CAST(ms.season AS INTEGER) = h.season
                    INNER JOIN core.fact_league_standings a
                        ON ms.system_league_id = a.system_league_id
                       AND ms.away_team_id = a.system_team_id
                       AND TRY_CAST(ms.season AS INTEGER) = a.season
                    WHERE ms.match_id = ?
                    """,
                    [match_id],
                ).fetchone()
        except Exception:
            return None


# ============================================================
# 阵容确认状态
# ============================================================

class LineupConfirmationEnhancer:
    """仅将已完成实体映射且新鲜的阵容和伤停写入风险上下文。"""

    def __init__(self, database_path: str = "football_system.db") -> None:
        self._db = database_path

    def enhance(self, features: MatchFeatures, match_id: str) -> MatchFeatures:
        context = dict(features.context)
        try:
            row = self._query_confirmation(match_id)
            if not row:
                return features

            home_count = int(row[0] or 0)
            away_count = int(row[1] or 0)
            home_confirmed = home_count >= 11
            away_confirmed = away_count >= 11

            context["lineup_home_confirmed"] = home_confirmed
            context["lineup_away_confirmed"] = away_confirmed
            context["lineup_home_confirmed_count"] = home_count
            context["lineup_away_confirmed_count"] = away_count
            context["injury_home_confirmed_count"] = int(row[2] or 0)
            context["injury_away_confirmed_count"] = int(row[3] or 0)
            context["lineup_injury_source_provider"] = row[4]
            context["lineup_injury_updated_at"] = (
                row[5].isoformat() if row[5] else None
            )

            # 阵容未确认 → 阵型数据可信度降低
            if not home_confirmed and not away_confirmed:
                context["lineup_confidence"] = "low"
            elif home_confirmed and away_confirmed:
                context["lineup_confidence"] = "high"
            else:
                context["lineup_confidence"] = "partial"
        except Exception as exc:
            logger.warning("Lineup confirmation enhancer failed: %s", exc)

        return replace(features, context=context)

    def _query_confirmation(self, match_id: str) -> tuple | None:
        import duckdb

        try:
            with duckdb.connect(self._db) as conn:
                return conn.execute(
                    """
                    WITH valid_records AS (
                        SELECT
                            pm.system_player_id,
                            tm.system_team_id,
                            ms.home_team_id,
                            ms.away_team_id,
                            'lineup' AS record_type,
                            l.role,
                            l.source_provider,
                            l.etl_insert_timestamp
                        FROM staging.stg_lineups l
                        INNER JOIN core.dim_provider_match_mapping mm
                            ON l.provider_match_id = mm.provider_match_id
                           AND l.source_provider = mm.provider_name
                        INNER JOIN core.fact_match_schedule ms
                            ON mm.system_match_id = ms.match_id
                        INNER JOIN core.dim_provider_team_mapping tm
                            ON l.source_provider = tm.provider_name
                           AND LOWER(TRIM(l.team_name)) =
                               LOWER(TRIM(tm.provider_team_name))
                           AND tm.system_team_id IN
                               (ms.home_team_id, ms.away_team_id)
                        INNER JOIN core.dim_player_mapping pm
                            ON tm.system_team_id = pm.team_id
                           AND LOWER(TRIM(l.player_name)) =
                               LOWER(TRIM(pm.player_standard_name))
                        WHERE mm.system_match_id = ?
                          AND l.etl_insert_timestamp >=
                              CURRENT_TIMESTAMP - INTERVAL '30 minutes'

                        UNION ALL

                        SELECT
                            pm.system_player_id,
                            tm.system_team_id,
                            ms.home_team_id,
                            ms.away_team_id,
                            'injury' AS record_type,
                            NULL AS role,
                            i.source_provider,
                            i.etl_insert_timestamp
                        FROM staging.stg_injuries i
                        INNER JOIN core.dim_provider_match_mapping mm
                            ON i.provider_match_id = mm.provider_match_id
                           AND i.source_provider = mm.provider_name
                        INNER JOIN core.fact_match_schedule ms
                            ON mm.system_match_id = ms.match_id
                        INNER JOIN core.dim_provider_team_mapping tm
                            ON i.source_provider = tm.provider_name
                           AND LOWER(TRIM(i.team_name)) =
                               LOWER(TRIM(tm.provider_team_name))
                           AND tm.system_team_id IN
                               (ms.home_team_id, ms.away_team_id)
                        INNER JOIN core.dim_player_mapping pm
                            ON tm.system_team_id = pm.team_id
                           AND LOWER(TRIM(i.player_name)) =
                               LOWER(TRIM(pm.player_standard_name))
                        WHERE mm.system_match_id = ?
                          AND i.etl_insert_timestamp >=
                              CURRENT_TIMESTAMP - INTERVAL '30 minutes'
                    )
                    SELECT
                        COUNT(DISTINCT CASE
                            WHEN record_type = 'lineup'
                             AND role = 'starter'
                             AND system_team_id = home_team_id
                            THEN system_player_id END),
                        COUNT(DISTINCT CASE
                            WHEN record_type = 'lineup'
                             AND role = 'starter'
                             AND system_team_id = away_team_id
                            THEN system_player_id END),
                        COUNT(DISTINCT CASE
                            WHEN record_type = 'injury'
                             AND system_team_id = home_team_id
                            THEN system_player_id END),
                        COUNT(DISTINCT CASE
                            WHEN record_type = 'injury'
                             AND system_team_id = away_team_id
                            THEN system_player_id END),
                        STRING_AGG(DISTINCT source_provider, ','),
                        MAX(etl_insert_timestamp)
                    FROM valid_records
                    HAVING COUNT(*) > 0
                    """,
                    [match_id, match_id],
                ).fetchone()
        except Exception:
            return None


# ============================================================
# 汇总增强器
# ============================================================

class FeatureEnhancerPipeline:
    """特征增强管道：顺序执行所有增强器。

    集成在 pipeline.py 中，feature_builder.build() 之后、
    probability_engine.predict() 之前调用。
    """

    def __init__(self, database_path: str = "football_system.db") -> None:
        self._enhancers = [
            FormationEnhancer(database_path),
            H2HEnhancer(database_path),
            RestDaysEnhancer(database_path),
            OddsTrendEnhancer(database_path),
            LineupConfirmationEnhancer(database_path),
            StandingsEnhancer(database_path),
        ]

    def enhance(self, features: MatchFeatures, match_id: str) -> MatchFeatures:
        """依次执行所有增强器，单个失败不影响后续。"""
        for enhancer in self._enhancers:
            try:
                features = enhancer.enhance(features, match_id)
            except Exception as exc:
                logger.warning(
                    "%s failed: %s", type(enhancer).__name__, exc
                )
        return features
