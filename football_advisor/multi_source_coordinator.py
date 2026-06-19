from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .config import SyncConfig


@dataclass(frozen=True)
class SourceResult:
    provider_name: str
    status: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    elapsed_ms: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class MultiSourceResult:
    results: list[SourceResult]
    started_at: datetime
    finished_at: datetime
    total_elapsed_ms: float

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.status == "success" and r.error is None)

    @property
    def failure_count(self) -> int:
        return sum(1 for r in self.results if r.error is not None)

    @property
    def skipped_count(self) -> int:
        return sum(1 for r in self.results if r.status.startswith("skipped"))

    @property
    def all_failed(self) -> bool:
        return self.success_count == 0 and self.failure_count > 0 and len(self.results) > 0

    @property
    def coverage_summary(self) -> dict[str, int]:
        coverage: dict[str, int] = {}
        for r in self.results:
            for field in r.data.get("covered_fields", []):
                coverage[field] = coverage.get(field, 0) + 1
        return coverage


class MultiSourceCoordinator:
    """Asyncio-based parallel dispatcher for multiple free data sources.

    所有免费数据源并行调用，无先后依赖。每个源独立执行，异常不影响其他源。
    架构同时适用于世界杯和五大联赛，不区分赛事类型。
    """

    def __init__(
        self,
        sync_config: SyncConfig,
        duckdb_path: str = "football_system.db",
    ) -> None:
        self._sync_config = sync_config
        self._duckdb_path = duckdb_path

    def _build_sync_fns(
        self,
        home_team: str | None,
        away_team: str | None,
        kickoff_time: datetime | None,
        exclude_providers: set[str] | None = None,
    ) -> list[Callable[[], SourceResult]]:
        """构建数据源同步调用函数列表，每个函数在线程中直接同步执行，可选排除指定 provider。"""
        exclude = exclude_providers or set()
        all_fns: list[tuple[str, Callable[[], SourceResult]]] = [
            ("SportteryOfficialWeb", lambda: self._fetch_sporttery(kickoff_time)),
            ("football-data.org", lambda: self._fetch_football_data(home_team, away_team, kickoff_time)),
            ("API-Football", lambda: self._fetch_api_football(home_team, away_team, kickoff_time)),
            ("TheSportsDB", lambda: self._fetch_thesportsdb(home_team, away_team, kickoff_time)),
            ("Sportmonks", lambda: self._fetch_sportmonks(home_team, away_team, kickoff_time)),
            ("iSports-API", lambda: self._fetch_isports(home_team, away_team, kickoff_time)),
            ("TheOddsAPI", lambda: self._fetch_the_odds_api(home_team, away_team, kickoff_time)),
            ("RapidAPI-OddsFeed", lambda: self._fetch_rapidapi_odds_feed(home_team, away_team, kickoff_time)),
        ]
        return [fn for name, fn in all_fns if name not in exclude]

    def fetch_all(
        self,
        home_team: str | None,
        away_team: str | None,
        kickoff_time: datetime | None = None,
        exclude_providers: set[str] | None = None,
    ) -> MultiSourceResult:
        """同步入口：Phase 0 日期滑窗 + Phase 1 并行 + Phase 2 缺口分析 + Phase 3 串行补漏。

        当指定日期找不到目标比赛时，自动向后滑动日期重试（最多 date_slide_max_days 天），
        直到找到赛程或赔率数据为止。
        """
        started_at = datetime.now(timezone.utc)
        base_time = kickoff_time or datetime.now(timezone.utc)
        max_slide = max(self._sync_config.date_slide_max_days, 0)
        exclude = exclude_providers or set()
        per_task_timeout = 60.0  # 单个数据源最大等待时间（秒），适配慢速 API

        results: list[SourceResult] = []
        search_date: datetime | None = None
        found_any = False
        for offset in range(max_slide + 1):
            search_date = base_time + timedelta(days=offset)
            sync_fns = self._build_sync_fns(home_team, away_team, search_date, exclude)
            day_results = self._gather_in_threads(sync_fns, per_task_timeout)

            for r in day_results:
                if r.error or r.status.startswith("skipped"):
                    continue
                data = r.data or {}
                has_schedule = (data.get("match_count", 0) > 0 or data.get("staged_count", 0) > 0) and r.status not in ("target_match_not_found", "match_not_found")
                has_odds = data.get("odds_count", 0) > 0 or data.get("merged_odds_count", 0) > 0
                has_team_data = data.get("mapped_team_stats_count", 0) > 0
                if has_schedule or has_odds or has_team_data:
                    found_any = True
                    break

            if offset > 0:
                for r in day_results:
                    if r.data is not None:
                        r.data["date_slide_offset_days"] = offset
                        r.data["date_slide_search_date"] = search_date.strftime("%Y-%m-%d")

            results.extend(day_results)

            if found_any:
                break
            for r in results:
                if r.status not in ("success",) and not r.status.startswith("skipped"):
                    r.data["date_slide_exhausted"] = True
                    r.data["date_slide_max_days"] = max_slide
                    r.data["date_slide_last_date"] = (
                        search_date.strftime("%Y-%m-%d") if search_date else "unknown"
                    )

        # === Phase 2 + 3: 关键数据缺口分析 + 串行补漏 ===
        gap_results = self._fill_critical_gaps(
            results,
            home_team,
            away_team,
            kickoff_time,
            excluded_providers=exclude,
        )
        if gap_results:
            results.extend(gap_results)

        finished_at = datetime.now(timezone.utc)
        return MultiSourceResult(
            results=results,
            started_at=started_at,
            finished_at=finished_at,
            total_elapsed_ms=(finished_at - started_at).total_seconds() * 1000,
        )

    def _fetch_sporttery(
        self,
        kickoff_time: datetime | None,
    ) -> SourceResult:
        provider = "SportteryOfficialWeb"
        start = datetime.now(timezone.utc)
        base_url = self._sync_config.sporttery_base_url
        if not base_url:
            return SourceResult(
                provider_name=provider,
                status="skipped_no_base_url",
                data={"covered_fields": []},
            )
        try:
            from .sporttery_client import SportteryClient
            import duckdb

            target_date = (
                kickoff_time.astimezone(timezone.utc).date()
                if kickoff_time and kickoff_time.tzinfo
                else kickoff_time.date()
                if kickoff_time
                else None
            )
            client = SportteryClient(base_url=base_url)
            with duckdb.connect(self._duckdb_path) as conn:
                res = client.fetch_football_odds(conn, target_date=target_date)
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            return SourceResult(
                provider_name=provider,
                status=res.get("status", "unknown"),
                data={
                    "covered_fields": ["sporttery_official_odds"],
                    "match_count": res.get("match_count", 0),
                    "odds_count": res.get("odds_count", 0),
                    "merged_match_count": res.get("merged_match_count", 0),
                    "merged_odds_count": res.get("merged_odds_count", 0),
                    "skipped_unmapped_count": res.get("skipped_unmapped_count", 0),
                },
                elapsed_ms=elapsed,
            )
        except Exception as e:
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            return SourceResult(
                provider_name=provider,
                status="failed",
                error=str(e),
                elapsed_ms=elapsed,
            )

    def _gather_in_threads(
        self,
        sync_fns: list[Callable[[], SourceResult]],
        per_task_timeout: float = 30.0,
    ) -> list[SourceResult]:
        """使用线程池并发执行同步 API 调用，每个任务有独立超时。"""
        results: list[SourceResult] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(sync_fns)) as executor:
            future_map = {executor.submit(fn): idx for idx, fn in enumerate(sync_fns)}
            # 使用 wait 而非 as_completed，避免永久阻塞
            done, not_done = concurrent.futures.wait(
                future_map, timeout=per_task_timeout + 5.0, return_when=concurrent.futures.ALL_COMPLETED
            )
            for future in done:
                try:
                    result = future.result(timeout=0)
                    results.append(result)
                except Exception as e:
                    results.append(
                        SourceResult(provider_name="unknown", status="failed", error=str(e))
                    )
            for future in not_done:
                future.cancel()
                results.append(
                    SourceResult(provider_name="unknown", status="failed", error=f"timeout after {per_task_timeout}s")
                )
        return results

    def _fetch_football_data(
        self,
        home_team: str | None,
        away_team: str | None,
        kickoff_time: datetime | None,
    ) -> SourceResult:
        provider = "football-data.org"
        start = datetime.now(timezone.utc)
        token = self._sync_config.football_data_api_token
        if not token:
            return SourceResult(
                provider_name=provider,
                status="skipped_no_token",
                data={"covered_fields": []},
            )
        try:
            from .football_data_client import FootballDataClient
            import duckdb

            client = FootballDataClient(
                api_token=token,
                base_url=self._sync_config.football_data_base_url,
            )
            ref_time = kickoff_time or datetime.now(timezone.utc)
            date_from = ref_time.strftime("%Y-%m-%d")
            date_to = ref_time.strftime("%Y-%m-%d")
            with duckdb.connect(self._duckdb_path) as conn:
                res = client.fetch_matches(conn, date_from, date_to)
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            return SourceResult(
                provider_name=provider,
                status=res.get("status", "unknown"),
                data={
                    "covered_fields": ["schedule", "score", "standings"],
                    "staged_count": res.get("staged_count", 0),
                    "merged_count": res.get("merged_count", 0),
                },
                elapsed_ms=elapsed,
            )
        except Exception as e:
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            return SourceResult(
                provider_name=provider,
                status="failed",
                error=str(e),
                elapsed_ms=elapsed,
            )

    def _fetch_api_football(
        self,
        home_team: str | None,
        away_team: str | None,
        kickoff_time: datetime | None,
    ) -> SourceResult:
        provider = "API-Football"
        start = datetime.now(timezone.utc)
        token = self._sync_config.api_football_token
        if not token:
            return SourceResult(
                provider_name=provider,
                status="skipped_no_token",
                data={"covered_fields": []},
            )
        try:
            from .api_football_client import ApiFootballClient
            import duckdb

            client = ApiFootballClient(
                api_token=token,
                base_url=self._sync_config.api_football_base_url,
            )
            ref_time = kickoff_time or datetime.now(timezone.utc)
            with duckdb.connect(self._duckdb_path) as conn:
                res = client.fetch_match_data(conn, ref_time, home_team, away_team)

                # Always attempt to fetch last N matches for both teams
                team_last_matches_res = _fetch_team_last_matches(
                    client, conn, ref_time, home_team, away_team
                )
                res["team_last_matches_home"] = team_last_matches_res.get(
                    "home", {}
                )
                res["team_last_matches_away"] = team_last_matches_res.get(
                    "away", {}
                )
                team_last_mapped = (
                    team_last_matches_res.get("home", {}).get(
                        "mapped_stats_count", 0
                    )
                    + team_last_matches_res.get("away", {}).get(
                        "mapped_stats_count", 0
                    )
                )

                if not _api_football_has_payload(res):
                    date_from, date_to = _recent_friendlies_window(ref_time)
                    recent_res = client.fetch_recent_friendlies(
                        conn,
                        ref_time,
                        home_team,
                        away_team,
                        date_from,
                        date_to,
                    )
                    target_status = res.get("status", "unknown")
                    res = {
                        **res,
                        **recent_res,
                        "target_status": target_status,
                        "recent_friendlies_date_from": date_from.isoformat(),
                        "recent_friendlies_date_to": date_to.isoformat(),
                    }
                    if recent_res.get("mapped_team_stats_count", 0) > 0:
                        res["status"] = "success"

                # If team last matches produced stats, mark as success
                if team_last_mapped > 0:
                    res["status"] = "success"
                    res["mapped_team_stats_count"] = (
                        res.get("mapped_team_stats_count", 0) + team_last_mapped
                    )
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            covered_fields = ["odds", "stats"]
            if res.get("mapped_team_stats_count", 0) > 0:
                covered_fields.append("recent_form")
            return SourceResult(
                provider_name=provider,
                status=res.get("status", "unknown"),
                data={
                    "covered_fields": covered_fields,
                    "odds_count": res.get("odds_count", 0),
                    "stats_count": res.get("stats_count", 0),
                    "merged_count": res.get("merged_count", 0),
                    "target_status": res.get("target_status"),
                    "friendly_count": res.get("friendly_count", 0),
                    "mapped_team_stats_count": res.get("mapped_team_stats_count", 0),
                    "recent_friendlies_date_from": res.get(
                        "recent_friendlies_date_from"
                    ),
                    "recent_friendlies_date_to": res.get("recent_friendlies_date_to"),
                },
                elapsed_ms=elapsed,
            )
        except Exception as e:
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            return SourceResult(
                provider_name=provider,
                status="failed",
                error=str(e),
                elapsed_ms=elapsed,
            )

    def _fetch_thesportsdb(
        self,
        home_team: str | None,
        away_team: str | None,
        kickoff_time: datetime | None,
    ) -> SourceResult:
        provider = "TheSportsDB"
        start = datetime.now(timezone.utc)
        token = self._sync_config.thesportsdb_api_token
        if not token:
            return SourceResult(
                provider_name=provider,
                status="skipped_no_token",
                data={"covered_fields": []},
            )
        try:
            from .thesportsdb_client import TheSportsDBClient
            import duckdb

            client = TheSportsDBClient(
                api_token=token,
                base_url=self._sync_config.thesportsdb_base_url,
            )
            ref_time = kickoff_time or datetime.now(timezone.utc)
            with duckdb.connect(self._duckdb_path) as conn:
                res = client.fetch_squad(conn, home_team, away_team, ref_time)
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            return SourceResult(
                provider_name=provider,
                status=res.get("status", "unknown"),
                data={
                    "covered_fields": ["squad", "history"],
                    "squad_count": res.get("squad_count", 0),
                    "history_count": res.get("history_count", 0),
                },
                elapsed_ms=elapsed,
            )
        except Exception as e:
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            return SourceResult(
                provider_name=provider,
                status="failed",
                error=str(e),
                elapsed_ms=elapsed,
            )

    def _fetch_sportmonks(
        self,
        home_team: str | None,
        away_team: str | None,
        kickoff_time: datetime | None,
    ) -> SourceResult:
        provider = "Sportmonks"
        start = datetime.now(timezone.utc)
        token = self._sync_config.sportmonks_api_token
        if not token:
            return SourceResult(
                provider_name=provider,
                status="skipped_no_token",
                data={"covered_fields": []},
            )
        try:
            from .sportmonks_client import SportmonksClient
            import duckdb

            client = SportmonksClient(
                api_token=token,
                base_url=self._sync_config.sportmonks_base_url,
            )
            ref_time = kickoff_time or datetime.now(timezone.utc)
            match_date = ref_time.astimezone(timezone.utc).date()
            with duckdb.connect(self._duckdb_path) as conn:
                res = client.fetch_matches_by_date(conn, match_date)
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            return SourceResult(
                provider_name=provider,
                status=res.get("status", "unknown"),
                data={
                    "covered_fields": ["schedule", "score"],
                    "match_count": res.get("match_count", 0),
                    "staged_count": res.get("staged_count", 0),
                    "date": match_date.isoformat(),
                },
                elapsed_ms=elapsed,
            )
        except Exception as e:
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            return SourceResult(
                provider_name=provider,
                status="failed",
                error=str(e),
                elapsed_ms=elapsed,
            )

    def _fetch_isports(
        self,
        home_team: str | None,
        away_team: str | None,
        kickoff_time: datetime | None,
    ) -> SourceResult:
        provider = "iSports-API"
        start = datetime.now(timezone.utc)
        token = self._sync_config.isports_api_token
        if not token:
            return SourceResult(
                provider_name=provider,
                status="skipped_no_token",
                data={"covered_fields": []},
            )
        try:
            from .isports_client import ISportsClient
            import duckdb

            client = ISportsClient(
                api_token=token,
                base_url=self._sync_config.isports_base_url,
            )
            ref_time = kickoff_time or datetime.now(timezone.utc)
            match_date = ref_time.astimezone(timezone.utc).date()
            with duckdb.connect(self._duckdb_path) as conn:
                res = client.fetch_matches_by_date(conn, match_date)
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            return SourceResult(
                provider_name=provider,
                status=res.get("status", "unknown"),
                data={
                    "covered_fields": ["schedule", "score"],
                    "match_count": res.get("match_count", 0),
                    "staged_count": res.get("staged_count", 0),
                    "date": match_date.isoformat(),
                },
                elapsed_ms=elapsed,
            )
        except Exception as e:
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            return SourceResult(
                provider_name=provider,
                status="failed",
                error=str(e),
                elapsed_ms=elapsed,
            )

    def _fetch_the_odds_api(
        self,
        home_team: str | None,
        away_team: str | None,
        kickoff_time: datetime | None,
    ) -> SourceResult:
        provider = "TheOddsAPI"
        start = datetime.now(timezone.utc)
        token = self._sync_config.the_odds_api_token
        if not token:
            return SourceResult(
                provider_name=provider,
                status="skipped_no_token",
                data={"covered_fields": []},
            )
        try:
            from .third_party_odds_client import ThirdPartyOddsClient
            import duckdb

            client = ThirdPartyOddsClient(
                the_odds_api_token=token,
                the_odds_api_base_url=self._sync_config.the_odds_api_base_url,
                the_odds_api_sport_keys=self._sync_config.the_odds_api_sport_keys,
            )
            with duckdb.connect(self._duckdb_path) as conn:
                res = client.fetch_the_odds_api_odds(
                    conn, home_team, away_team, kickoff_time
                )
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            covered_fields = ["odds_cross_check"]
            if res.get("capital_flow_fields"):
                covered_fields.append("capital_flow")
            return SourceResult(
                provider_name=provider,
                status=res.get("status", "unknown"),
                data={
                    "covered_fields": covered_fields,
                    "event_count": res.get("event_count", 0),
                    "matched_event_count": res.get("matched_event_count", 0),
                    "odds_count": res.get("odds_count", 0),
                    "merged_odds_count": res.get("merged_odds_count", 0),
                    "capital_flow_fields": res.get("capital_flow_fields", []),
                },
                elapsed_ms=elapsed,
            )
        except Exception as e:
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            return SourceResult(
                provider_name=provider,
                status="failed",
                error=str(e),
                elapsed_ms=elapsed,
            )

    def _fetch_rapidapi_odds_feed(
        self,
        home_team: str | None,
        away_team: str | None,
        kickoff_time: datetime | None,
    ) -> SourceResult:
        provider = "RapidAPI-OddsFeed"
        start = datetime.now(timezone.utc)
        token = self._sync_config.rapidapi_token
        if not token:
            return SourceResult(
                provider_name=provider,
                status="skipped_no_token",
                data={"covered_fields": []},
            )
        if (
            not self._sync_config.odds_feed_rapid_host
            or not self._sync_config.odds_feed_rapid_base_url
        ):
            return SourceResult(
                provider_name=provider,
                status="skipped_no_endpoint",
                data={"covered_fields": []},
            )
        try:
            from .third_party_odds_client import ThirdPartyOddsClient
            import duckdb

            client = ThirdPartyOddsClient(
                rapidapi_token=token,
                odds_feed_rapid_host=self._sync_config.odds_feed_rapid_host,
                odds_feed_rapid_base_url=self._sync_config.odds_feed_rapid_base_url,
            )
            with duckdb.connect(self._duckdb_path) as conn:
                res = client.fetch_rapidapi_odds_feed(
                    conn, home_team, away_team, kickoff_time
                )
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            covered_fields = ["odds_cross_check"]
            if res.get("capital_flow_fields"):
                covered_fields.append("capital_flow")
            return SourceResult(
                provider_name=provider,
                status=res.get("status", "unknown"),
                data={
                    "covered_fields": covered_fields,
                    "event_count": res.get("event_count", 0),
                    "matched_event_count": res.get("matched_event_count", 0),
                    "odds_count": res.get("odds_count", 0),
                    "merged_odds_count": res.get("merged_odds_count", 0),
                    "capital_flow_fields": res.get("capital_flow_fields", []),
                },
                elapsed_ms=elapsed,
            )
        except Exception as e:
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            return SourceResult(
                provider_name=provider,
                status="failed",
                error=str(e),
                elapsed_ms=elapsed,
            )

    # ---- Phase 2+3: 数据缺口分析 + 串行补漏 ----

    def _fill_critical_gaps(
        self,
        results: list[SourceResult],
        home_team: str | None,
        away_team: str | None,
        kickoff_time: datetime | None,
        excluded_providers: set[str] | None = None,
    ) -> list[SourceResult]:
        """分析 Phase 1 结果，对关键缺口尝试串行补漏。"""
        if not home_team or not away_team:
            return []

        spf_ok = self._has_spf_odds(results)
        rqspf_ok = self._has_rqspf_odds(results)
        schedule_ok = self._has_schedule(results)
        squad_ok = self._has_squad(results)
        h2h_ok = self._has_h2h(results)

        gaps: list[str] = []
        sporttery_handled_elsewhere = (
            "SportteryOfficialWeb" in (excluded_providers or set())
        )
        if not spf_ok and not sporttery_handled_elsewhere:
            gaps.append("SPF赔率缺失")
        if not rqspf_ok and not sporttery_handled_elsewhere:
            gaps.append("RQSPF赔率缺失")
        if not schedule_ok:
            gaps.append("赛程缺失")
        if not squad_ok:
            gaps.append("阵容缺失")

        if not gaps:
            # 比分/总进球/半全场 仅竞彩有，不存在串行补漏
            # 但需要标记竞彩未开售
            sporttery_missing = []
            if not self._has_correct_score(results):
                sporttery_missing.append("比分(CRS)")
            if not self._has_total_goals(results):
                sporttery_missing.append("总进球(TTG)")
            if not self._has_half_full(results):
                sporttery_missing.append("半全场(HAFU)")
            if sporttery_missing:
                sporttery_result = self._find_source_result(results, "SportteryOfficialWeb")
                if sporttery_result and sporttery_result.status == "success":
                    sporttery_result.data["lottery_unsold_playtypes"] = sporttery_missing
            return []

        new_results: list[SourceResult] = []
        for gap in gaps:
            fill_result = self._try_fill_gap(gap, home_team, away_team, kickoff_time)
            if fill_result:
                new_results.append(fill_result)

        return new_results

    def _try_fill_gap(
        self,
        gap: str,
        home_team: str,
        away_team: str,
        kickoff_time: datetime | None,
    ) -> SourceResult | None:
        """对单个数据缺口尝试串行补漏。按 DATA_FLOW_WEIGHT_DESIGN.md 优先级链。"""
        # 1. 赛程缺口
        if gap == "赛程缺失":
            # 优先 API-Football
            result = self._sync_call_fetch(
                lambda: self._fetch_api_football(home_team, away_team, kickoff_time)
            )
            if self._has_schedule([result]):
                return result
            # 再试 football-data
            result = self._sync_call_fetch(
                lambda: self._fetch_football_data(home_team, away_team, kickoff_time)
            )
            if self._has_schedule([result]):
                return result
            return None

        # 2. SPF / RQSPF 赔率缺口
        if gap.startswith("SPF") or gap.startswith("RQSPF"):
            # 优先 OddsFeed (1X2 + 亚盘)
            result = self._sync_call_fetch(
                lambda: self._fetch_rapidapi_odds_feed(home_team, away_team, kickoff_time)
            )
            if self._has_spf_odds([result]) or self._has_rqspf_odds([result]):
                return result
            # 再试 TheOddsAPI
            result = self._sync_call_fetch(
                lambda: self._fetch_the_odds_api(home_team, away_team, kickoff_time)
            )
            if self._has_spf_odds([result]) or self._has_rqspf_odds([result]):
                return result
            return None

        # 3. 阵容缺口
        if gap == "阵容缺失":
            # 优先 TheSportsDB
            result = self._sync_call_fetch(
                lambda: self._fetch_thesportsdb(home_team, away_team, kickoff_time)
            )
            if self._has_squad([result]):
                return result
            # 再试 API-Football
            result = self._sync_call_fetch(
                lambda: self._fetch_api_football(home_team, away_team, kickoff_time)
            )
            if self._has_squad([result]):
                return result
            return None

        return None

    @staticmethod
    def _sync_call_fetch(sync_fn: Callable[[], SourceResult], timeout: float = 30.0) -> SourceResult:
        """同步执行一个数据采集函数，返回 SourceResult。"""
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(sync_fn)
                return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return SourceResult(
                provider_name="gap_filler",
                status="failed",
                error=f"timeout after {timeout}s",
                data={},
                elapsed_ms=0,
            )
        except Exception:
            return SourceResult(
                provider_name="gap_filler",
                status="failed",
                data={},
                elapsed_ms=0,
            )

    # ---- 缺口检测辅助 ----

    @staticmethod
    def _find_source_result(results: list[SourceResult], name: str) -> SourceResult | None:
        return next((r for r in results if r.provider_name == name), None)

    @staticmethod
    def _has_official_sporttery_spf(results: list[SourceResult]) -> bool:
        """竞彩官方 SPF 赔率可用（仅 SportteryOfficialWeb）。"""
        for r in results:
            if r.provider_name != "SportteryOfficialWeb":
                continue
            data = r.data or {}
            if data.get("spf_odds_count", 0) > 0:
                return True
            if data.get("odds_count", 0) > 0:
                return True
        return False

    @staticmethod
    def _has_cross_check_1x2(results: list[SourceResult]) -> bool:
        """第三方 1X2 赔率可用（交叉校验）。"""
        for r in results:
            if r.provider_name == "SportteryOfficialWeb":
                continue
            data = r.data or {}
            if data.get("spf_odds_count", 0) > 0:
                return True
            if data.get("odds_count", 0) > 0:
                return True
        return False

    @staticmethod
    def _has_spf_odds(results: list[SourceResult]) -> bool:
        """兼容旧方法：任一来源的 SPF 赔率。新代码应使用 _has_official_sporttery_spf。"""
        return MultiSourceCoordinator._has_official_sporttery_spf(results)

    @staticmethod
    def _has_rqspf_odds(results: list[SourceResult]) -> bool:
        for r in results:
            if r.provider_name != "SportteryOfficialWeb":
                continue
            data = r.data or {}
            if data.get("rqspf_odds_count", 0) > 0:
                return True
        return False

    @staticmethod
    def _has_schedule(results: list[SourceResult]) -> bool:
        for r in results:
            data = r.data or {}
            if data.get("match_count", 0) > 0 or data.get("event_count", 0) > 0:
                return True
        return False

    @staticmethod
    def _has_squad(results: list[SourceResult]) -> bool:
        for r in results:
            data = r.data or {}
            if data.get("squad_count", 0) > 0 or data.get("player_count", 0) > 0:
                return True
        return False

    @staticmethod
    def _has_h2h(results: list[SourceResult]) -> bool:
        for r in results:
            data = r.data or {}
            if data.get("h2h_count", 0) > 0 or data.get("head_to_head_count", 0) > 0:
                return True
        return False

    @staticmethod
    def _has_correct_score(results: list[SourceResult]) -> bool:
        for r in results:
            data = r.data or {}
            if data.get("correct_score_count", 0) > 0:
                return True
        return False

    @staticmethod
    def _has_total_goals(results: list[SourceResult]) -> bool:
        for r in results:
            data = r.data or {}
            if data.get("total_goals_count", 0) > 0:
                return True
        return False

    @staticmethod
    def _has_half_full(results: list[SourceResult]) -> bool:
        for r in results:
            data = r.data or {}
            if data.get("half_full_count", 0) > 0:
                return True
        return False


def _fetch_team_last_matches(
    client: Any,
    connection: Any,
    ref_time: datetime,
    home_team: str | None,
    away_team: str | None,
) -> dict[str, dict[str, Any]]:
    """Fetch last 10 matches for home and away team from API-Football."""
    result: dict[str, dict[str, Any]] = {}
    try:
        rows = connection.execute(
            """
            SELECT m.provider_team_id, m.system_team_id, t.team_standard_name
            FROM core.dim_provider_team_mapping m
            JOIN core.dim_team_mapping t ON m.system_team_id = t.system_team_id
            WHERE m.provider_name = ?
              AND lower(t.team_standard_name) IN (lower(?), lower(?))
            """,
            [
                getattr(client, "PROVIDER_NAME", "API-Football"),
                home_team or "",
                away_team or "",
            ],
        ).fetchall()
    except Exception:
        return result

    for row in rows:
        provider_team_id, system_team_id, _ = row
        side = None
        if home_team and _team_name_match(row[2], home_team):
            side = "home"
        elif away_team and _team_name_match(row[2], away_team):
            side = "away"
        if side:
            try:
                target_match_id = client._find_target_match_id(
                    connection, ref_time, home_team, away_team
                )
            except Exception:
                continue
            if not target_match_id:
                continue
            try:
                res = client.fetch_team_last_matches(
                    connection,
                    target_match_id,
                    str(provider_team_id),
                    str(system_team_id),
                    10,
                )
                result[side] = res
            except Exception:
                continue
    return result


def _team_name_match(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def _api_football_has_payload(result: dict[str, Any]) -> bool:
    for field in (
        "odds_count",
        "stats_count",
        "merged_count",
        "injury_count",
        "lineup_count",
    ):
        value = result.get(field, 0)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return True
    return False


def _recent_friendlies_window(ref_time: datetime) -> tuple[Any, Any]:
    target_date = (
        ref_time.astimezone(timezone.utc).date()
        if ref_time.tzinfo
        else ref_time.date()
    )
    today = datetime.now(timezone.utc).date()
    date_to = min(target_date, today)
    return date_to - timedelta(days=30), date_to
