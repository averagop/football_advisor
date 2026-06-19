from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .config import FeatureBuilderConfig
from .models import MatchFeatures, MatchRequest, TeamFeatures
from .query_tools import DuckDBStatsQueryTool
from .time_utils import as_utc


class FeatureDataUnavailable(RuntimeError):
    pass


class DuckDBFeatureBuilder:
    def __init__(
        self,
        query_tool: DuckDBStatsQueryTool | None = None,
        config: FeatureBuilderConfig | None = None,
    ) -> None:
        self.query_tool = query_tool or DuckDBStatsQueryTool()
        self.config = config or FeatureBuilderConfig()

    def build(
        self, request: MatchRequest, sync_results: list[Any] | None = None
    ) -> MatchFeatures:
        sql, params = self._build_feature_sql_and_params(request)
        try:
            result = self.query_tool.query_match_stats(sql, params)
        except Exception as exc:
            raise FeatureDataUnavailable(str(exc)) from exc

        if not result.rows:
            raise FeatureDataUnavailable("No DuckDB feature row matched the request.")

        row = result.rows[0]
        odds_1x2 = self._odds_1x2(row)
        odds_rqspf = self._odds_rqspf(row)
        detail_odds = self._sporttery_detail_odds(str(row.get("match_id") or ""))

        return MatchFeatures(
            home=TeamFeatures(
                name=str(row.get("home_team_name") or request.home_team or "Home Team"),
                elo=self._float(row, "home_elo", 1500.0),
                attack_strength=self._float(row, "home_attack_strength", 1.0),
                defense_strength=self._float(row, "home_defense_strength", 1.0),
                recent_points_per_match=self._float(
                    row, "home_recent_points_per_match", 1.4
                ),
                home_advantage=self._home_advantage(row),
                injury_penalty=self._float(row, "home_injury_penalty", 0.0),
            ),
            away=TeamFeatures(
                name=str(row.get("away_team_name") or request.away_team or "Away Team"),
                elo=self._float(row, "away_elo", 1500.0),
                attack_strength=self._float(row, "away_attack_strength", 1.0),
                defense_strength=self._float(row, "away_defense_strength", 1.0),
                recent_points_per_match=self._float(
                    row, "away_recent_points_per_match", 1.4
                ),
                injury_penalty=self._float(row, "away_injury_penalty", 0.0),
            ),
            league_avg_home_goals=self._float(row, "league_avg_home_goals", 1.45),
            league_avg_away_goals=self._float(row, "league_avg_away_goals", 1.15),
            odds_1x2=odds_1x2,
            odds_rqspf=odds_rqspf,
            odds_correct_score=detail_odds["CORRECT_SCORE"],
            odds_total_goals=detail_odds["TOTAL_GOALS"],
            odds_half_full=detail_odds["HALF_FULL"],
            rqspf_handicap_line=self._optional_float(row, "rqspf_handicap_line"),
            totals_line=self._float(row, "totals_line", 2.5),
            capital_flow_volume=self._float(row, "matched_volume", 0.0),
            capital_flow_sharp_ratio=self._float(row, "sharp_money_ratio", 0.0),
            updated_at=self._datetime(row.get("data_updated_at")),
            sources=self._sources(row, sync_results or []),
            context=self._context(row, request),
        )

    def _build_feature_sql_and_params(
        self, request: MatchRequest
    ) -> tuple[str, list[Any]]:
        base_query = self.config.feature_view_or_query.strip()
        
        # If the configured base looks like a full SQL statement, wrap it as a subquery
        if base_query.lower().startswith("select "):
            base_from = f"({base_query}) AS base_query"
        else:
            base_from = base_query

        if request.match_id:
            sql = f"SELECT * FROM {base_from} WHERE match_id = ? LIMIT 1"
            params = [request.match_id]
        elif request.home_team and request.away_team:
            sql = (
                f"SELECT * FROM {base_from} "
                "WHERE lower(home_team_name) = lower(?) AND lower(away_team_name) = lower(?) "
                "ORDER BY COALESCE(data_updated_at, TIMESTAMP '1970-01-01') DESC LIMIT 1"
            )
            params = [request.home_team, request.away_team]
        else:
            raise FeatureDataUnavailable(
                "FeatureBuilder requires match_id or both teams."
            )
        return sql, params

    def _sporttery_detail_odds(self, match_id: str) -> dict[str, dict[str, float]]:
        odds: dict[str, dict[str, float]] = {
            "CORRECT_SCORE": {},
            "TOTAL_GOALS": {},
            "HALF_FULL": {},
        }
        if not match_id:
            return odds
        result = self.query_tool.query_match_stats(
            """
            SELECT odds_type, outcome_key, odds_value
            FROM (
                SELECT odds_type, outcome_key, odds_value,
                       ROW_NUMBER() OVER (
                           PARTITION BY odds_type, outcome_key
                           ORDER BY snapshot_time DESC
                       ) AS row_number
                FROM core.fact_sporttery_odds_detail
                WHERE match_id = ?
                  AND source_provider = 'SportteryOfficialWeb'
            ) ranked
            WHERE row_number = 1
            """,
            [match_id],
        )
        for row in result.rows:
            odds_type = str(row.get("odds_type") or "")
            score_key = str(row.get("outcome_key") or "")
            odds_value = row.get("odds_value")
            if odds_type in odds and score_key and odds_value is not None:
                value = float(odds_value)
                if value > 1.0:
                    odds[odds_type][score_key] = value
        return odds

    @staticmethod
    def _sql_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    @staticmethod
    def _float(row: dict[str, Any], key: str, default: float) -> float:
        value = row.get(key)
        if value is None:
            return default
        return float(value)

    @staticmethod
    def _optional_float(row: dict[str, Any], key: str) -> float | None:
        value = row.get(key)
        if value is None:
            return None
        return float(value)

    @staticmethod
    def _datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            return as_utc(value)
        return datetime.now(timezone.utc)

    @staticmethod
    def _odds_1x2(row: dict[str, Any]) -> dict[str, float]:
        mapping = {
            "home": row.get("latest_home_odds"),
            "draw": row.get("latest_draw_odds"),
            "away": row.get("latest_away_odds"),
        }
        return {
            outcome: float(value)
            for outcome, value in mapping.items()
            if value is not None and float(value) > 1.0
        }

    @staticmethod
    def _odds_rqspf(row: dict[str, Any]) -> dict[str, float]:
        mapping = {
            "home": row.get("latest_rqspf_home_odds"),
            "draw": row.get("latest_rqspf_draw_odds"),
            "away": row.get("latest_rqspf_away_odds"),
        }
        return {
            outcome: float(value)
            for outcome, value in mapping.items()
            if value is not None and float(value) > 1.0
        }

    @staticmethod
    def _home_advantage(row: dict[str, Any]) -> float:
        # 中立场：主场优势强制归零
        is_neutral = row.get("is_neutral_venue")
        if is_neutral is True or str(is_neutral).lower() in ("true", "1", "yes"):
            return 0.0
        home_ppm = row.get("home_home_points_per_match")
        away_ppm = row.get("away_away_points_per_match")
        if home_ppm is None or away_ppm is None:
            return 0.12
        diff = float(home_ppm) - float(away_ppm)
        return max(0.0, min(0.22, 0.10 + diff * 0.03))

    @staticmethod
    def _sources(row: dict[str, Any], sync_results: list[Any]) -> list[str]:
        sources: list[str] = []
        raw_sources = str(row.get("data_sources") or "")
        for source in raw_sources.split(","):
            cleaned = source.strip()
            if cleaned and cleaned not in sources:
                sources.append(cleaned)
        for result in sync_results:
            source = getattr(result, "source", "")
            if source and source not in sources:
                sources.append(source)
        return sources

    @staticmethod
    def _context(row: dict[str, Any], request: MatchRequest) -> dict[str, Any]:
        consumed_keys = {
            "home_team_name",
            "home_elo",
            "home_attack_strength",
            "home_defense_strength",
            "home_recent_points_per_match",
            "home_home_points_per_match",
            "home_injury_penalty",
            "away_team_name",
            "away_elo",
            "away_attack_strength",
            "away_defense_strength",
            "away_recent_points_per_match",
            "away_away_points_per_match",
            "away_injury_penalty",
            "league_avg_home_goals",
            "league_avg_away_goals",
            "latest_home_odds",
            "latest_draw_odds",
            "latest_away_odds",
            "latest_rqspf_home_odds",
            "latest_rqspf_draw_odds",
            "latest_rqspf_away_odds",
            "rqspf_handicap_line",
            "totals_line",
            "matched_volume",
            "sharp_money_ratio",
            "data_updated_at",
            "data_sources",
            "is_neutral_venue",
        }
        
        context = {
            key: value
            for key, value in row.items()
            if key not in consumed_keys and value is not None
        }
        
        context["mode"] = request.mode.value
        context["feature_source"] = "duckdb_view"
        return context
