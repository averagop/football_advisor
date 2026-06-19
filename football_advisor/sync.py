from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import AdvisorConfig, SyncConfig, load_config
from .models import MatchRequest
from .news_ingestion import NewsIngester
from .search_router import SearchRouter
from .text_fetcher import TextFetcher
from .exchange_client import ExchangeClient
from .pre_match_collector import PreMatchCollector
from .post_match_collector import PostMatchCollector
from .rolling_stats_refresher import RollingStatsRefresher

logger = logging.getLogger(__name__)


def _log_sync_event(step: str, match_id: str, status: str, details: str = "") -> None:
    logger.info("sync_news|step=%s|match_id=%s|status=%s|%s", step, match_id, status, details)


@dataclass(frozen=True)
class SyncResult:
    source: str
    updated_at: datetime
    status: str
    details: dict[str, str] = field(default_factory=dict)


class DataSyncCoordinator:
    """Boundary for real data providers; V1 keeps sync explicit and auditable."""

    def __init__(
        self,
        config: SyncConfig | None = None,
        search_router: SearchRouter | None = None,
        text_fetcher: TextFetcher | None = None,
        news_ingester: NewsIngester | None = None,
        exchange_client: ExchangeClient | None = None,
        parent_config: AdvisorConfig | None = None,
    ) -> None:
        self.parent_config = parent_config or load_config()
        self.config = config or self.parent_config.sync
        self.search_router = search_router or SearchRouter(
            config=self.parent_config.search
        )
        self.text_fetcher = text_fetcher or TextFetcher()
        self.news_ingester = news_ingester or NewsIngester(config=self.parent_config)
        self.exchange_client = exchange_client

    def sync_structured_data(
        self,
        request: MatchRequest,
        exclude_providers: set[str] | None = None,
    ) -> SyncResult:
        from .multi_source_coordinator import MultiSourceCoordinator
        from .cross_validator import CrossValidator

        try:
            import duckdb
        except ModuleNotFoundError:
            return SyncResult(
                source=self.config.structured_source,
                updated_at=datetime.now(timezone.utc),
                status="skipped_no_duckdb",
            )

        coordinator = MultiSourceCoordinator(
            sync_config=self.config,
            duckdb_path=self.parent_config.duckdb.database_path,
        )
        multi_result = coordinator.fetch_all(
            home_team=request.home_team,
            away_team=request.away_team,
            kickoff_time=request.kickoff_time,
            exclude_providers=exclude_providers,
        )

        validator = CrossValidator()
        cross_result = validator.validate(multi_result)

        if multi_result.all_failed:
            return SyncResult(
                source="multi_source",
                updated_at=datetime.now(timezone.utc),
                status="all_sources_failed",
                details={
                    "query": request.query,
                    "source_count": str(len(multi_result.results)),
                    "success_count": str(multi_result.success_count),
                    "failure_count": str(multi_result.failure_count),
                    "cross_check_passed": str(not cross_result.cross_check_failed),
                    "coverage_flag": cross_result.multi_source_coverage_flag,
                },
            )

        if multi_result.success_count == 0:
            return SyncResult(
                source="multi_source",
                updated_at=datetime.now(timezone.utc),
                status="no_source_coverage",
                details={
                    "query": request.query,
                    "source_count": str(len(multi_result.results)),
                    "success_count": str(multi_result.success_count),
                    "failure_count": str(multi_result.failure_count),
                    "skipped_count": str(multi_result.skipped_count),
                    "coverage_flag": cross_result.multi_source_coverage_flag,
                    "cross_check_passed": str(not cross_result.cross_check_failed),
                },
            )

        if cross_result.cross_check_failed:
            return SyncResult(
                source="multi_source",
                updated_at=datetime.now(timezone.utc),
                status="cross_check_failed",
                details={
                    "query": request.query,
                    "score_consensus": cross_result.score_consensus,
                    "odds_deviation_sources": ", ".join(cross_result.odds_deviation_sources),
                    "invalid_range_sources": ", ".join(cross_result.invalid_range_sources),
                    "duplicate_record_sources": ", ".join(cross_result.duplicate_record_sources),
                    "anomaly_sources": ", ".join(cross_result.anomaly_sources),
                    "lineup_uncertain_count": str(cross_result.lineup_uncertain_count),
                    "injury_unconfirmed_count": str(cross_result.injury_unconfirmed_count),
                    "coverage_flag": cross_result.multi_source_coverage_flag,
                    "total_elapsed_ms": str(multi_result.total_elapsed_ms),
                },
            )

        return SyncResult(
            source="multi_source",
            updated_at=datetime.now(timezone.utc),
            status="success",
            details={
                "query": request.query,
                "source_count": str(len(multi_result.results)),
                "success_count": str(multi_result.success_count),
                "skipped_count": str(multi_result.skipped_count),
                "coverage_flag": cross_result.multi_source_coverage_flag,
                "cross_check_passed": str(not cross_result.cross_check_failed),
                "total_elapsed_ms": str(multi_result.total_elapsed_ms),
            },
        )

    def sync_capital_flow_data(self, request: MatchRequest) -> SyncResult:
        if self.exchange_client is None:
            return SyncResult(
                source="exchange_provider",
                updated_at=datetime.now(timezone.utc),
                status="skipped_no_provider_configured",
                details={"query": request.query},
            )

        try:
            import duckdb
        except ModuleNotFoundError:
            return SyncResult(
                source="exchange_provider",
                updated_at=datetime.now(timezone.utc),
                status="skipped_no_duckdb",
            )
            
        try:
            with duckdb.connect(self.parent_config.duckdb.database_path) as conn:
                res = self.exchange_client.fetch_capital_flow(conn, request.query)
            return SyncResult(
                source=res.get("source", "exchange_provider"),
                updated_at=datetime.now(timezone.utc),
                status=res.get("status", "unknown"),
                details={"query": request.query, "match_id": res.get("match_id", "")},
            )
        except Exception as e:
            return SyncResult(
                source="exchange_provider",
                updated_at=datetime.now(timezone.utc),
                status="error",
                details={"error": str(e)},
            )

    def sync_news_data(self, request: MatchRequest) -> SyncResult:
        # 1. 寻找匹配的 match_id
        match_id = request.match_id
        if not match_id:
            match_id = self._find_match_id(request)
            if not match_id:
                return SyncResult(
                    source=self.config.news_source,
                    updated_at=datetime.now(timezone.utc),
                    status="failed_no_match_id",
                    details={"reason": "Cannot resolve match_id for the request query"},
                )

        # 2. 构造搜索查询词
        search_query = self._build_news_search_query(request)

        # 3. 搜索预览
        preview_res = self.search_router.preview(search_query, limit=5)
        if preview_res.status != "ok" or not preview_res.results:
            _log_sync_event(
                "sync_news_data",
                match_id,
                "empty_search",
                details=f"status={preview_res.status} results={len(preview_res.results)} errors={preview_res.errors}",
            )
            return SyncResult(
                source=self.config.news_source,
                updated_at=datetime.now(timezone.utc),
                status="empty_search",
                details={
                    "query": search_query,
                    "reason": "Search returned no results",
                    "errors": ",".join(preview_res.errors),
                },
            )

        # 4. 网页抓取正文
        urls = [item.url for item in preview_res.results]
        _log_sync_event("sync_news_data", match_id, "fetching", details=f"fetching {len(urls)} urls")
        fetched_docs = self.text_fetcher.fetch_many(urls)
        ok_docs = [d for d in fetched_docs if d.status == "ok"]
        _log_sync_event("sync_news_data", match_id, "fetched", details=f"fetched={len(fetched_docs)} ok={len(ok_docs)}")

        if not ok_docs:
            return SyncResult(
                source=self.config.news_source,
                updated_at=datetime.now(timezone.utc),
                status="empty_fetch",
                details={
                    "query": search_query,
                    "match_id": match_id,
                    "fetched_count": str(len(fetched_docs)),
                    "reason": "All fetched documents failed or were empty",
                },
            )

        # 5. ChromaDB 入库与 DuckDB 结构化沉淀
        kickoff_date = ""
        if request.kickoff_time is not None:
            kickoff_date = request.kickoff_time.strftime("%Y-%m-%d")
        ingest_res = self.news_ingester.ingest_news(
            match_id,
            fetched_docs,
            home_team_id=request.home_team or "",
            away_team_id=request.away_team or "",
            kickoff_date=kickoff_date,
        )

        # 6. 返回结果
        status = "success"
        if ingest_res.get("status") == "empty_ingestion":
            status = "empty_ingestion"
        elif _is_error_status(
            str(ingest_res.get("duckdb_status", ""))
        ) or _is_error_status(str(ingest_res.get("chromadb_status", ""))):
            status = "failed_news_ingestion"

        details = {
            "query": search_query,
            "match_id": match_id,
            "ingested_chunks": str(ingest_res.get("ingested_chunks", 0)),
            "chromadb_status": ingest_res.get("chromadb_status", "skipped"),
            "duckdb_status": ingest_res.get("duckdb_status", "skipped"),
            "sentiment": f"{ingest_res.get('sentiment', 0.0):.2f}",
            "risk": str(ingest_res.get("risk", False)),
            "source_count": str(ingest_res.get("source_count", 0)),
        }

        return SyncResult(
            source=self.config.news_source,
            updated_at=datetime.now(timezone.utc),
            status=status,
            details=details,
        )

    def _build_news_search_query(self, request: MatchRequest) -> str:
        if request.home_team and request.away_team:
            competition_context = (
                "World Cup " if self._request_uses_world_cup_context(request) else ""
            )
            return (
                f"{request.home_team} vs {request.away_team} "
                f"{competition_context}squad injury suspension lineup press conference "
                "training tactical news"
            )
        return request.query

    def _request_uses_world_cup_context(self, request: MatchRequest) -> bool:
        if "world cup" in request.query.casefold():
            return True
        if not request.match_id:
            return False
        try:
            import duckdb

            with duckdb.connect(self.parent_config.duckdb.database_path, read_only=True) as conn:
                row = conn.execute(
                    """
                    SELECT l.league_standard_name
                    FROM core.fact_match_schedule s
                    JOIN core.dim_league_mapping l
                      ON s.system_league_id = l.system_league_id
                    WHERE s.match_id = ?
                    LIMIT 1
                    """,
                    [request.match_id],
                ).fetchone()
        except Exception:
            return False
        return bool(row and "world cup" in str(row[0]).casefold())

    def sync_before_prediction(self, request: MatchRequest) -> Any:
        """委托给 TargetMatchSyncService 进行目标比赛关键数据同步。

        保留旧方法（sync_pre_match_results / sync_post_match_results）供维护任务调用。
        """
        from .target_sync import TargetMatchSyncService

        target_sync = TargetMatchSyncService(
            parent_config=self.parent_config,
            sync_coordinator=self,
        )
        return target_sync.sync_for_prediction(request)

    def sync_pre_match_results(self) -> SyncResult:
        """赛前预采集：拉取未来 N 天 TOP5+WC 的赛程、赔率快照、阵容。"""
        if not self.parent_config.sync.api_football_token:
            return SyncResult(
                source="pre_match_collector",
                updated_at=datetime.now(timezone.utc),
                status="skipped_no_token",
                details={"reason": "API_FOOTBALL_TOKEN 未配置"},
            )

        collector = PreMatchCollector(
            api_token=self.parent_config.sync.api_football_token,
            base_url=self.parent_config.sync.api_football_base_url,
            duckdb_path=self.parent_config.duckdb.database_path,
            enabled=self.parent_config.sync.pre_match_collect_enabled,
            lookahead_days=self.parent_config.sync.pre_match_lookahead_days,
            target_leagues=self.parent_config.sync.pre_match_collect_leagues,
        )
        result = collector.collect_if_needed()

        details = {
            "status": result.status,
            "days_collected": str(result.days_collected),
            "matches_inserted": str(result.matches_inserted),
            "odds_snapshots": str(result.odds_snapshots),
            "lineups_collected": str(result.lineups_collected),
            "recent_form_matches": str(result.recent_form_matches),
            "h2h_matches": str(result.h2h_matches),
            "team_stats_collected": str(result.team_stats_collected),
            "standings_updates": str(result.standings_updates),
            "injuries_collected": str(result.injuries_collected),
        }
        if result.error:
            details["error"] = result.error

        return SyncResult(
            source="pre_match_collector",
            updated_at=datetime.now(timezone.utc),
            status=result.status,
            details=details,
        )

    def sync_post_match_results(self) -> SyncResult:
        """赛后采集：拉取遗漏的 TOP5+世界杯完赛数据。"""
        if not self.parent_config.sync.api_football_token:
            return SyncResult(
                source="post_match_collector",
                updated_at=datetime.now(timezone.utc),
                status="skipped_no_token",
                details={"reason": "API_FOOTBALL_TOKEN 未配置"},
            )

        collector = PostMatchCollector(
            api_token=self.parent_config.sync.api_football_token,
            base_url=self.parent_config.sync.api_football_base_url,
            duckdb_path=self.parent_config.duckdb.database_path,
            enabled=self.parent_config.sync.post_match_collect_enabled,
            target_leagues=self.parent_config.sync.post_match_collect_leagues,
        )
        result = collector.collect_if_needed()

        details = {
            "status": result.status,
            "collected_days": str(result.collected_days),
            "matches_updated": str(result.matches_updated),
            "matches_inserted": str(result.matches_inserted),
            "stats_inserted": str(result.stats_inserted),
            "elo_updated": str(result.elo_updated),
        }
        if result.error:
            details["error"] = result.error

        sync_status = result.status
        if result.status == "success":
            # 赛后采集成功后，刷新受影响球队的滚动统计
            if result.matches_updated > 0 or result.matches_inserted > 0:
                try:
                    refresher = RollingStatsRefresher(
                        duckdb_path=self.parent_config.duckdb.database_path,
                    )
                    refresh_result = refresher.refresh_teams(
                        list(result.affected_team_ids)
                    )
                    details["rolling_stats"] = str(refresh_result)
                    if refresh_result.get("status") == "error":
                        sync_status = "error"
                except Exception as exc:
                    logger.exception("Post-match rolling stats refresh failed.")
                    details["rolling_stats_error"] = str(exc)
                    sync_status = "error"

        return SyncResult(
            source="post_match_collector",
            updated_at=datetime.now(timezone.utc),
            status=sync_status,
            details=details,
        )

    def _find_match_id(self, request: MatchRequest) -> str | None:
        if not request.home_team or not request.away_team:
            return None
        try:
            import duckdb
        except ModuleNotFoundError:
            return None

        if request.kickoff_time is not None:
            sql = """
                SELECT s.match_id
                FROM core.fact_match_schedule s
                JOIN core.dim_team_mapping h ON s.home_team_id = h.system_team_id
                JOIN core.dim_team_mapping a ON s.away_team_id = a.system_team_id
                WHERE h.team_standard_name = ?
                  AND a.team_standard_name = ?
                  AND s.status = 'PRE-MATCH'
                  AND abs(date_diff('minute', s.match_time, ?)) <= 720
                ORDER BY abs(date_diff('minute', s.match_time, ?)) ASC
                LIMIT 1
            """
            try:
                with duckdb.connect(self.parent_config.duckdb.database_path) as conn:
                    res = conn.execute(
                        sql,
                        [
                            request.home_team,
                            request.away_team,
                            request.kickoff_time,
                            request.kickoff_time,
                        ],
                    ).fetchone()
                    if res:
                        return str(res[0])
            except Exception:
                pass
            return None

        sql = """
            SELECT s.match_id
            FROM core.fact_match_schedule s
            JOIN core.dim_team_mapping h ON s.home_team_id = h.system_team_id
            JOIN core.dim_team_mapping a ON s.away_team_id = a.system_team_id
            WHERE h.team_standard_name = ? AND a.team_standard_name = ?
              AND s.status = 'PRE-MATCH'
              AND s.match_time >= CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
            ORDER BY s.match_time ASC
            LIMIT 2
        """
        try:
            with duckdb.connect(self.parent_config.duckdb.database_path) as conn:
                rows = conn.execute(
                    sql, [request.home_team, request.away_team]
                ).fetchall()
                if len(rows) == 1:
                    return str(rows[0][0])
        except Exception:
            pass
        return None


def _is_error_status(status: str) -> bool:
    return status.startswith("error:") or status.startswith("failed")
