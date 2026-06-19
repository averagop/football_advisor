from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import duckdb

from football_advisor.db_schema import apply_schema
from scripts import smoke_test_worldcup_readiness


class SmokeTestWorldCupReadinessTests(unittest.TestCase):
    def test_generic_news_query_uses_explicit_league_context(self):
        query = smoke_test_worldcup_readiness._build_match_news_query(
            "Greece",
            "Italy",
            "International Friendly",
        )

        self.assertNotIn("World Cup", query)
        self.assertIn("International Friendly", query)
        self.assertIn("injury", query)

    def test_world_cup_news_query_keeps_world_cup_context(self):
        query = smoke_test_worldcup_readiness._build_match_news_query(
            "Mexico",
            "South Africa",
            "World Cup",
        )

        self.assertIn("World Cup", query)

    def test_mapping_gate_requires_sporttery_official_web(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mapping_gate.duckdb"
            with duckdb.connect(str(db_path)) as conn:
                apply_schema(conn)
                conn.execute(
                    """
                    INSERT INTO core.dim_team_mapping
                        (system_team_id, team_standard_name)
                    VALUES ('HOME', 'Home Team'), ('AWAY', 'Away Team')
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO core.dim_provider_team_mapping
                        (provider_name, provider_team_id, system_team_id)
                    VALUES ('API-Football', ?, ?)
                    """,
                    [["10", "HOME"], ["20", "AWAY"]],
                )

                missing = (
                    smoke_test_worldcup_readiness._teams_missing_provider_mapping(
                        conn, ["HOME", "AWAY"]
                    )
                )

                self.assertEqual(missing, ["Away Team", "Home Team"])

    def test_readiness_supports_generic_league_and_match_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "match.duckdb"
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "FOOTBALL_DATA_API_TOKEN=fake-football-data",
                        "API_FOOTBALL_TOKEN=fake-api-football",
                        "THESPORTSDB_API_TOKEN=fake-thesportsdb",
                        "FOOTBALL_SEARXNG_BASE_URL=http://search.example.test",
                        "FOOTBALL_EXTERNAL_LLM_BASE_URL=https://llm.example.test/v1",
                        "FOOTBALL_EXTERNAL_LLM_API_KEY=fake-llm-key",
                    ]
                ),
                encoding="utf-8",
            )
            with duckdb.connect(str(db_path)) as conn:
                apply_schema(conn)
                _seed_generic_match(conn)

            sync_result = SimpleNamespace(
                success_count=1,
                failure_count=0,
                skipped_count=0,
                total_elapsed_ms=1.0,
                results=[
                    SimpleNamespace(
                        provider_name="API-Football",
                        status="success",
                        data={"odds_count": 1, "stats_count": 1, "merged_count": 1},
                        error=None,
                    )
                ],
            )
            preview_result = SimpleNamespace(
                provider="searxng",
                status="ok",
                results=[SimpleNamespace(url="https://example.test/friendly")],
                errors=[],
            )
            fetched_document = SimpleNamespace(
                url="https://example.test/friendly",
                title="Greece Italy friendly squad update",
                text="Greece and Italy injury lineup press conference tactical news.",
                status="ok",
            )
            output = io.StringIO()

            with patch(
                "football_advisor.multi_source_coordinator.MultiSourceCoordinator.fetch_all",
                return_value=sync_result,
            ) as fetch_mock, patch(
                "football_advisor.search_router.SearchRouter.preview",
                return_value=preview_result,
            ), patch(
                "football_advisor.text_fetcher.TextFetcher.fetch_many",
                return_value=[fetched_document],
            ), patch(
                "football_advisor.news_ingestion.NewsIngester.ingest_news",
                return_value={
                    "duckdb_status": "success",
                    "chromadb_status": "success",
                    "source_count": 1,
                    "ingested_chunks": 1,
                },
            ), contextlib.redirect_stdout(output):
                exit_code = smoke_test_worldcup_readiness.main(
                    [
                        "--home-team",
                        "Greece",
                        "--away-team",
                        "Italy",
                        "--date",
                        "2026-06-07",
                        "--match-id",
                        "M_TEST",
                        "--league",
                        "International Friendly",
                        "--db-path",
                        str(db_path),
                        "--env-path",
                        str(env_path),
                    ]
                )

            text = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertEqual(
                fetch_mock.call_args.args[2].date().isoformat(), "2026-06-08"
            )
            self.assertIn("统一赛前预测可用性门禁: 通过", text)
            self.assertIn("联赛上下文: International Friendly", text)

    def test_readiness_reports_missing_tokens_and_search_without_faking_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "worldcup.duckdb"
            missing_env_path = Path(temp_dir) / ".env.missing"
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                exit_code = smoke_test_worldcup_readiness.main(
                    [
                        "--home-team",
                        "USA",
                        "--away-team",
                        "Mexico",
                        "--date",
                        "2026-06-09",
                        "--db-path",
                        str(db_path),
                        "--env-path",
                        str(missing_env_path),
                    ]
                )

            text = output.getvalue()
            self.assertEqual(exit_code, 1)
            self.assertIn("结构化源 token 配置完整", text)
            self.assertIn("搜索 provider 配置可用", text)
            self.assertIn("外部 LLM 配置完整", text)
            self.assertIn("API-Football", text)
            self.assertNotIn("<YOUR_API_TOKEN>", text)

            with duckdb.connect(str(db_path)) as conn:
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM core.dim_team_mapping WHERE system_team_id IN ('WC_TEAM_USA', 'WC_TEAM_MEX')"
                    ).fetchone()[0],
                    2,
                )
                self.assertGreaterEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM core.dim_provider_team_mapping WHERE system_team_id IN ('WC_TEAM_USA', 'WC_TEAM_MEX')"
                    ).fetchone()[0],
                    2,
                )


    def test_readiness_fails_when_target_match_id_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "worldcup.duckdb"
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "FOOTBALL_DATA_API_TOKEN=fake-football-data",
                        "API_FOOTBALL_TOKEN=fake-api-football",
                        "THESPORTSDB_API_TOKEN=fake-thesportsdb",
                        "FOOTBALL_SEARXNG_BASE_URL=http://search.example.test",
                        "FOOTBALL_EXTERNAL_LLM_BASE_URL=https://llm.example.test/v1",
                        "FOOTBALL_EXTERNAL_LLM_API_KEY=fake-llm-key",
                    ]
                ),
                encoding="utf-8",
            )
            output = io.StringIO()

            sync_result = SimpleNamespace(
                success_count=1,
                failure_count=0,
                skipped_count=0,
                total_elapsed_ms=1.0,
                results=[
                    SimpleNamespace(
                        provider_name="API-Football",
                        status="success",
                        data={"odds_count": 1, "stats_count": 1, "merged_count": 1},
                        error=None,
                    )
                ],
            )
            preview_result = SimpleNamespace(
                provider="searxng",
                status="ok",
                results=[SimpleNamespace(url="https://example.test/world-cup-news")],
                errors=[],
            )
            fetched_document = SimpleNamespace(
                url="https://example.test/world-cup-news",
                title="USA Mexico World Cup squad update",
                text="World Cup squad injury lineup press conference tactical news.",
                status="ok",
            )

            with patch(
                "football_advisor.multi_source_coordinator.MultiSourceCoordinator.fetch_all",
                return_value=sync_result,
            ) as fetch_mock, patch(
                "football_advisor.search_router.SearchRouter.preview",
                return_value=preview_result,
            ), patch(
                "football_advisor.text_fetcher.TextFetcher.fetch_many",
                return_value=[fetched_document],
            ), contextlib.redirect_stdout(output):
                exit_code = smoke_test_worldcup_readiness.main(
                    [
                        "--home-team",
                        "USA",
                        "--away-team",
                        "Mexico",
                        "--date",
                        "2026-06-09",
                        "--db-path",
                        str(db_path),
                        "--env-path",
                        str(env_path),
                    ]
                )

            text = output.getvalue()
            self.assertEqual(exit_code, 1)
            self.assertIn("目标比赛 match_id 存在", text)
            self.assertIn("core.fact_match_schedule 未命中目标主客队", text)
            self.assertIn("统一赛前预测可用性门禁: 未通过", text)

    def test_readiness_passes_for_seeded_world_cup_opening_fixture(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "worldcup.duckdb"
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "FOOTBALL_DATA_API_TOKEN=fake-football-data",
                        "API_FOOTBALL_TOKEN=fake-api-football",
                        "THESPORTSDB_API_TOKEN=fake-thesportsdb",
                        "FOOTBALL_SEARXNG_BASE_URL=http://search.example.test",
                        "FOOTBALL_EXTERNAL_LLM_BASE_URL=https://llm.example.test/v1",
                        "FOOTBALL_EXTERNAL_LLM_API_KEY=fake-llm-key",
                    ]
                ),
                encoding="utf-8",
            )
            output = io.StringIO()

            sync_result = SimpleNamespace(
                success_count=1,
                failure_count=0,
                skipped_count=0,
                total_elapsed_ms=1.0,
                results=[
                    SimpleNamespace(
                        provider_name="API-Football",
                        status="success",
                        data={"odds_count": 1, "stats_count": 1, "merged_count": 1},
                        error=None,
                    )
                ],
            )
            preview_result = SimpleNamespace(
                provider="searxng",
                status="ok",
                results=[SimpleNamespace(url="https://example.test/opening-match")],
                errors=[],
            )
            fetched_document = SimpleNamespace(
                url="https://example.test/opening-match",
                title="Mexico South Africa World Cup opening match squad update",
                text="Mexico and South Africa World Cup opening match squad injury lineup press conference tactical news.",
                status="ok",
            )

            with patch(
                "football_advisor.multi_source_coordinator.MultiSourceCoordinator.fetch_all",
                return_value=sync_result,
            ) as fetch_mock, patch(
                "football_advisor.search_router.SearchRouter.preview",
                return_value=preview_result,
            ), patch(
                "football_advisor.text_fetcher.TextFetcher.fetch_many",
                return_value=[fetched_document],
            ), patch(
                "football_advisor.news_ingestion.NewsIngester.ingest_news",
                return_value={
                    "duckdb_status": "success",
                    "chromadb_status": "success",
                    "source_count": 1,
                    "ingested_chunks": 1,
                    "sentiment": 0.0,
                    "risk": False,
                },
            ), contextlib.redirect_stdout(output):
                exit_code = smoke_test_worldcup_readiness.main(
                    [
                        "--home-team",
                        "Mexico",
                        "--away-team",
                        "South Africa",
                        "--date",
                        "2026-06-09",
                        "--db-path",
                        str(db_path),
                        "--env-path",
                        str(env_path),
                    ]
                )

            text = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertEqual(fetch_mock.call_args.args[2].date().isoformat(), "2026-06-11")
            self.assertIn("目标比赛 match_id 存在", text)
            self.assertIn("统一赛前预测可用性门禁: 通过", text)

    def test_readiness_fails_when_pdf_player_is_written_to_multiple_teams(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "worldcup.duckdb"
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "FOOTBALL_DATA_API_TOKEN=fake-football-data",
                        "API_FOOTBALL_TOKEN=fake-api-football",
                        "THESPORTSDB_API_TOKEN=fake-thesportsdb",
                        "FOOTBALL_SEARXNG_BASE_URL=http://search.example.test",
                        "FOOTBALL_EXTERNAL_LLM_BASE_URL=https://llm.example.test/v1",
                        "FOOTBALL_EXTERNAL_LLM_API_KEY=fake-llm-key",
                    ]
                ),
                encoding="utf-8",
            )
            with duckdb.connect(str(db_path)) as conn:
                apply_schema(conn)
                conn.execute(
                    """
                    INSERT INTO core.dim_player_mapping (
                        system_player_id, player_standard_name, team_id, primary_position
                    ) VALUES
                        ('PDF_WC_TEAM_MEX_TEBOHO_MOKOENA', 'Teboho Mokoena', 'WC_TEAM_MEX', 'MF'),
                        ('PDF_WC_TEAM_RSA_TEBOHO_MOKOENA', 'Teboho Mokoena', 'WC_TEAM_RSA', 'MF')
                    """
                )

            output = io.StringIO()
            sync_result = SimpleNamespace(
                success_count=1,
                failure_count=0,
                skipped_count=0,
                total_elapsed_ms=1.0,
                results=[
                    SimpleNamespace(
                        provider_name="API-Football",
                        status="success",
                        data={"odds_count": 1, "stats_count": 1, "merged_count": 1},
                        error=None,
                    )
                ],
            )
            preview_result = SimpleNamespace(
                provider="searxng",
                status="ok",
                results=[SimpleNamespace(url="https://example.test/opening-match")],
                errors=[],
            )
            fetched_document = SimpleNamespace(
                url="https://example.test/opening-match",
                title="Mexico South Africa World Cup opening match squad update",
                text="Mexico and South Africa World Cup squad injury lineup press conference tactical news.",
                status="ok",
            )

            with patch(
                "football_advisor.multi_source_coordinator.MultiSourceCoordinator.fetch_all",
                return_value=sync_result,
            ), patch(
                "football_advisor.search_router.SearchRouter.preview",
                return_value=preview_result,
            ), patch(
                "football_advisor.text_fetcher.TextFetcher.fetch_many",
                return_value=[fetched_document],
            ), patch(
                "football_advisor.news_ingestion.NewsIngester.ingest_news",
                return_value={
                    "duckdb_status": "success",
                    "chromadb_status": "success",
                    "source_count": 1,
                    "ingested_chunks": 1,
                    "sentiment": 0.0,
                    "risk": False,
                },
            ), contextlib.redirect_stdout(output):
                exit_code = smoke_test_worldcup_readiness.main(
                    [
                        "--home-team",
                        "Mexico",
                        "--away-team",
                        "South Africa",
                        "--date",
                        "2026-06-09",
                        "--db-path",
                        str(db_path),
                        "--env-path",
                        str(env_path),
                    ]
                )

            text = output.getvalue()
            self.assertEqual(exit_code, 1)
            self.assertIn("PDF 球员跨队污染检查", text)
            self.assertIn("Teboho Mokoena", text)

    def test_readiness_rejects_empty_structured_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "worldcup.duckdb"
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "FOOTBALL_DATA_API_TOKEN=fake-football-data",
                        "API_FOOTBALL_TOKEN=fake-api-football",
                        "THESPORTSDB_API_TOKEN=fake-thesportsdb",
                        "FOOTBALL_SEARXNG_BASE_URL=http://search.example.test",
                        "FOOTBALL_EXTERNAL_LLM_BASE_URL=https://llm.example.test/v1",
                        "FOOTBALL_EXTERNAL_LLM_API_KEY=fake-llm-key",
                    ]
                ),
                encoding="utf-8",
            )
            output = io.StringIO()

            sync_result = SimpleNamespace(
                success_count=2,
                failure_count=0,
                skipped_count=0,
                total_elapsed_ms=1.0,
                results=[
                    SimpleNamespace(
                        provider_name="API-Football",
                        status="success",
                        data={"odds_count": 0, "stats_count": 0, "merged_count": 0},
                        error=None,
                    ),
                    SimpleNamespace(
                        provider_name="football-data.org",
                        status="success",
                        data={"staged_count": 0, "merged_count": 0},
                        error=None,
                    ),
                ],
            )
            preview_result = SimpleNamespace(
                provider="searxng",
                status="ok",
                results=[SimpleNamespace(url="https://example.test/opening-match")],
                errors=[],
            )
            fetched_document = SimpleNamespace(
                url="https://example.test/opening-match",
                title="Mexico South Africa World Cup opening match squad update",
                text="Mexico and South Africa World Cup squad injury lineup press conference tactical news.",
                status="ok",
            )

            with patch(
                "football_advisor.multi_source_coordinator.MultiSourceCoordinator.fetch_all",
                return_value=sync_result,
            ), patch(
                "football_advisor.search_router.SearchRouter.preview",
                return_value=preview_result,
            ), patch(
                "football_advisor.text_fetcher.TextFetcher.fetch_many",
                return_value=[fetched_document],
            ), patch(
                "football_advisor.news_ingestion.NewsIngester.ingest_news",
                return_value={
                    "duckdb_status": "success",
                    "chromadb_status": "success",
                    "source_count": 1,
                    "ingested_chunks": 1,
                    "sentiment": 0.0,
                    "risk": False,
                },
            ), contextlib.redirect_stdout(output):
                exit_code = smoke_test_worldcup_readiness.main(
                    [
                        "--home-team",
                        "Mexico",
                        "--away-team",
                        "South Africa",
                        "--date",
                        "2026-06-09",
                        "--db-path",
                        str(db_path),
                        "--env-path",
                        str(env_path),
                    ]
                )

            text = output.getvalue()
            self.assertEqual(exit_code, 1)
            self.assertIn("至少一个结构化源返回可用数据", text)

    def test_structured_payload_accepts_recent_friendly_stats(self):
        results = [
            SimpleNamespace(
                provider_name="API-Football",
                status="success",
                data={"friendly_count": 1, "mapped_team_stats_count": 1},
                error=None,
            )
        ]

        self.assertTrue(smoke_test_worldcup_readiness._has_structured_payload(results))

    def test_readiness_accepts_serper_key_without_google_cx(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "worldcup.duckdb"
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "FOOTBALL_SERPER_API_KEY=fake-serper-key",
                        "FOOTBALL_EXTERNAL_LLM_BASE_URL=https://llm.example.test/v1",
                        "FOOTBALL_EXTERNAL_LLM_API_KEY=fake-llm-key",
                    ]
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            preview_result = SimpleNamespace(
                provider="serper",
                status="provider_error",
                results=[],
                errors=["fake search error"],
            )

            with patch(
                "football_advisor.search_router.SearchRouter.preview",
                return_value=preview_result,
            ) as preview_mock, contextlib.redirect_stdout(output):
                exit_code = smoke_test_worldcup_readiness.main(
                    [
                        "--home-team",
                        "Mexico",
                        "--away-team",
                        "South Africa",
                        "--date",
                        "2026-06-09",
                        "--db-path",
                        str(db_path),
                        "--env-path",
                        str(env_path),
                    ]
                )

            text = output.getvalue()
            self.assertEqual(exit_code, 1)
            self.assertEqual(preview_mock.call_count, 1)
            self.assertNotIn(
                "配置 FOOTBALL_SEARXNG_BASE_URL 或 FOOTBALL_SERPER_API_KEY",
                text,
            )

    def test_readiness_uses_local_schedule_and_stats_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "worldcup.duckdb"
            env_path = temp_path / ".env"
            schedule_csv = temp_path / "world-cup-2026-schedule.csv"
            stats_csv = temp_path / "world_cup_2026_teams_last_5_matches_data.csv"
            env_path.write_text(
                "\n".join(
                    [
                        "FOOTBALL_DATA_API_TOKEN=fake-football-data",
                        "API_FOOTBALL_TOKEN=fake-api-football",
                        "THESPORTSDB_API_TOKEN=fake-thesportsdb",
                        "FOOTBALL_SEARXNG_BASE_URL=http://search.example.test",
                        "FOOTBALL_EXTERNAL_LLM_BASE_URL=https://llm.example.test/v1",
                        "FOOTBALL_EXTERNAL_LLM_API_KEY=fake-llm-key",
                    ]
                ),
                encoding="utf-8",
            )
            schedule_csv.write_text(
                "\n".join(
                    [
                        "match_number,stage,group,date,time_et,time_local,team_a,team_b,venue,city,country,status,source",
                        "2,Group Stage,A,2026-06-11,22:00,20:00,Korea Republic,Czechia,Estadio Akron,Guadalajara,Mexico,confirmed_group_fixture,source",
                    ]
                ),
                encoding="utf-8",
            )
            stats_csv.write_text(
                "\n".join(
                    [
                        "rank,team,group,rating,avg_squad_age,avg_trans_value,W,D,L,GF,GA,GD,W%",
                        "24,South Korea,A,1594.35,27.7,5.16,3,0,2,8,5,3,60.0",
                        "36,Czechia,A,1471.24,27.1,5.41,3,2,0,13,5,8,60.0",
                    ]
                ),
                encoding="utf-8",
            )
            output = io.StringIO()

            sync_result = SimpleNamespace(
                success_count=1,
                failure_count=0,
                skipped_count=0,
                total_elapsed_ms=1.0,
                results=[
                    SimpleNamespace(
                        provider_name="API-Football",
                        status="success",
                        data={"odds_count": 1, "stats_count": 1, "merged_count": 1},
                        error=None,
                    )
                ],
            )
            preview_result = SimpleNamespace(
                provider="searxng",
                status="ok",
                results=[SimpleNamespace(url="https://example.test/korea-czechia")],
                errors=[],
            )
            fetched_document = SimpleNamespace(
                url="https://example.test/korea-czechia",
                title="South Korea Czechia World Cup squad update",
                text="South Korea and Czechia World Cup squad injury lineup press conference tactical news.",
                status="ok",
            )

            with patch(
                "football_advisor.multi_source_coordinator.MultiSourceCoordinator.fetch_all",
                return_value=sync_result,
            ) as fetch_mock, patch(
                "football_advisor.search_router.SearchRouter.preview",
                return_value=preview_result,
            ), patch(
                "football_advisor.text_fetcher.TextFetcher.fetch_many",
                return_value=[fetched_document],
            ), patch(
                "football_advisor.news_ingestion.NewsIngester.ingest_news",
                return_value={
                    "duckdb_status": "success",
                    "chromadb_status": "success",
                    "source_count": 1,
                    "ingested_chunks": 1,
                    "sentiment": 0.0,
                    "risk": False,
                },
            ), contextlib.redirect_stdout(output):
                exit_code = smoke_test_worldcup_readiness.main(
                    [
                        "--home-team",
                        "South Korea",
                        "--away-team",
                        "Czechia",
                        "--date",
                        "2026-06-09",
                        "--db-path",
                        str(db_path),
                        "--env-path",
                        str(env_path),
                        "--schedule-csv",
                        str(schedule_csv),
                        "--team-stats-csv",
                        str(stats_csv),
                    ]
                )

            text = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertEqual(fetch_mock.call_args.args[2].date().isoformat(), "2026-06-12")
            self.assertIn("本地世界杯基础资料导入", text)
            self.assertIn("目标比赛 match_id 存在", text)
            self.assertIn("统一赛前预测可用性门禁: 通过", text)

    def test_readiness_falls_back_to_serper_when_searxng_fetch_has_no_valid_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "worldcup.duckdb"
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "FOOTBALL_DATA_API_TOKEN=fake-football-data",
                        "API_FOOTBALL_TOKEN=fake-api-football",
                        "THESPORTSDB_API_TOKEN=fake-thesportsdb",
                        "FOOTBALL_SEARXNG_BASE_URL=http://search.example.test",
                        "FOOTBALL_SERPER_API_KEY=fake-serper-key",
                        "FOOTBALL_EXTERNAL_LLM_BASE_URL=https://llm.example.test/v1",
                        "FOOTBALL_EXTERNAL_LLM_API_KEY=fake-llm-key",
                    ]
                ),
                encoding="utf-8",
            )
            output = io.StringIO()

            sync_result = SimpleNamespace(
                success_count=1,
                failure_count=0,
                skipped_count=0,
                total_elapsed_ms=1.0,
                results=[
                    SimpleNamespace(
                        provider_name="API-Football",
                        status="success",
                        data={"odds_count": 1, "stats_count": 1, "merged_count": 1},
                        error=None,
                    )
                ],
            )
            searxng_preview = SimpleNamespace(
                provider="searxng",
                status="ok",
                results=[SimpleNamespace(url="https://example.test/blocked")],
                errors=[],
            )
            serper_preview = SimpleNamespace(
                provider="serper",
                status="ok",
                results=[SimpleNamespace(url="https://example.test/serper-news")],
                errors=[],
            )
            invalid_document = SimpleNamespace(
                url="https://example.test/blocked",
                title="",
                text="",
                status="fetch_error",
            )
            valid_document = SimpleNamespace(
                url="https://example.test/serper-news",
                title="Mexico South Africa World Cup news",
                text="Mexico and South Africa World Cup opening match squad injury lineup press conference tactical news.",
                status="ok",
            )

            with patch(
                "football_advisor.multi_source_coordinator.MultiSourceCoordinator.fetch_all",
                return_value=sync_result,
            ), patch(
                "football_advisor.search_router.SearchRouter.preview",
                side_effect=[searxng_preview, serper_preview],
            ) as preview_mock, patch(
                "football_advisor.text_fetcher.TextFetcher.fetch_many",
                side_effect=[[invalid_document], [valid_document]],
            ), patch(
                "football_advisor.news_ingestion.NewsIngester.ingest_news",
                return_value={
                    "duckdb_status": "success",
                    "chromadb_status": "success",
                    "source_count": 1,
                    "ingested_chunks": 1,
                    "sentiment": 0.0,
                    "risk": False,
                },
            ), contextlib.redirect_stdout(output):
                exit_code = smoke_test_worldcup_readiness.main(
                    [
                        "--home-team",
                        "Mexico",
                        "--away-team",
                        "South Africa",
                        "--date",
                        "2026-06-09",
                        "--db-path",
                        str(db_path),
                        "--env-path",
                        str(env_path),
                    ]
                )

            text = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertEqual(preview_mock.call_count, 2)
            self.assertIn("SearXNG 正文无有效文本，改用 Serper 搜索备用", text)

    def test_load_env_accepts_utf8_sig_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\ufeffFOOTBALL_SEARXNG_BASE_URL=http://search.example.test\n",
                encoding="utf-8",
            )

            env = smoke_test_worldcup_readiness._load_env(env_path)

        self.assertEqual(
            env.get("FOOTBALL_SEARXNG_BASE_URL"),
            "http://search.example.test",
        )


def _seed_generic_match(connection):
    match_time = datetime(2026, 6, 8, 3, 0, tzinfo=timezone.utc)
    connection.execute(
        """
        INSERT INTO core.dim_league_mapping
            (system_league_id, league_standard_name, country)
        VALUES ('INTL_FRIENDLY', 'International Friendly', 'International')
        """
    )
    connection.execute(
        """
        INSERT INTO core.dim_team_mapping
            (system_team_id, team_standard_name, country)
        VALUES
            ('WC_TEAM_GRE', 'Greece', 'Greece'),
            ('WC_TEAM_ITA', 'Italy', 'Italy')
        """
    )
    connection.executemany(
        """
        INSERT INTO core.dim_provider_team_mapping
            (provider_name, provider_team_id, system_team_id, provider_team_name)
        VALUES (?, ?, ?, ?)
        """,
        [
            ["API-Football", "1117", "WC_TEAM_GRE", "Greece"],
            ["API-Football", "768", "WC_TEAM_ITA", "Italy"],
            ["SportteryOfficialWeb", "361", "WC_TEAM_GRE", "希腊"],
            ["SportteryOfficialWeb", "400", "WC_TEAM_ITA", "意大利"],
        ],
    )
    connection.execute(
        """
        INSERT INTO core.fact_match_schedule
            (match_id, season, system_league_id, match_time,
             home_team_id, away_team_id, status, source_provider, updated_at)
        VALUES ('M_TEST', '2026', 'INTL_FRIENDLY', ?, 'WC_TEAM_GRE',
                'WC_TEAM_ITA', 'PRE-MATCH', 'TEST', ?)
        """,
        [match_time, match_time],
    )


if __name__ == "__main__":
    unittest.main()
