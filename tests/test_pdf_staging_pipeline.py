from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from football_advisor.pdf_staging_pipeline import (
    CHINESE_COUNTRY_MAP,
    CHINESE_TEAM_MAP,
    CrossValidationResult,
    EntityMappingResult,
    KNOWN_TEAM_MAP,
    PDFStagingResult,
    PlayerCandidate,
    RefereeCandidate,
    ScheduleCandidate,
    _convert_chinese_date,
    _detect_chinese_team_header,
    _normalize_name,
    _parse_chinese_player_line,
    _parse_match_lines,
    _resolve_chinese_country,
    _resolve_team_id,
    parse_referee_pdf,
    parse_schedule_pdf,
    parse_squad_pdf,
    run_cross_validation,
    run_entity_mapping,
    run_pdf_staging_pipeline,
)
from football_advisor.pdf_text_extractor import PDFTextExtractionResult


class PDFStagingPipelineTests(unittest.TestCase):
    """PDF staging 管线基础测试。"""

    # ========================================================================
    # 名称标准化
    # ========================================================================

    def test_normalize_name_removes_accents(self):
        self.assertEqual(_normalize_name("Côte d'Ivoire"), "Cote_d_Ivoire")
        self.assertEqual(_normalize_name("Türkiye"), "Turkiye")

    def test_normalize_name_preserves_chinese(self):
        self.assertEqual(_normalize_name("劳尔·兰赫尔"), "劳尔_兰赫尔")
        self.assertEqual(_normalize_name("墨西哥"), "墨西哥")

    def test_normalize_name_handles_whitespace(self):
        self.assertEqual(_normalize_name("  South Korea  "), "South_Korea")

    # ========================================================================
    # 球队 ID 映射
    # ========================================================================

    def test_resolve_team_id_exact_match(self):
        self.assertEqual(_resolve_team_id("Mexico"), "WC_TEAM_MEX")
        self.assertEqual(_resolve_team_id("Argentina"), "WC_TEAM_ARG")

    def test_resolve_team_id_case_insensitive(self):
        self.assertEqual(_resolve_team_id("mexico"), "WC_TEAM_MEX")
        self.assertEqual(_resolve_team_id("USA"), "WC_TEAM_USA")

    def test_resolve_team_id_unknown_returns_none(self):
        self.assertIsNone(_resolve_team_id("Mars Colony FC"))

    # ========================================================================
    # 球队标题检测
    # ========================================================================

    def test_detect_chinese_team_header_finds_known_team(self):
        result = _detect_chinese_team_header("A组 墨西哥")
        self.assertEqual(result, "墨西哥")

    def test_detect_chinese_team_header_returns_none_for_unknown(self):
        self.assertIsNone(_detect_chinese_team_header("Some Random Text"))

    # ========================================================================
    # 球员行解析（中文格式）
    # ========================================================================

    def test_parse_chinese_player_line_with_birth_date(self):
        result = _parse_chinese_player_line(
            "1 門将 劳尔·兰赫尔 2000年2月25日（26 歲） 13 0 瓜达拉哈拉",
            "墨西哥",
        )
        self.assertIsNotNone(result)
        if result:
            self.assertEqual(result["name"], "劳尔·兰赫尔")
            self.assertEqual(result["position"], "GK")
            self.assertEqual(result["birth_date"], "2000-02-25")
            self.assertEqual(result["caps"], 13)
            self.assertEqual(result["goals"], 0)
            self.assertEqual(result["club"], "瓜达拉哈拉")

    def test_parse_chinese_player_line_with_english_club(self):
        result = _parse_chinese_player_line(
            "2 后卫 豪尔赫·桑切斯 1997年12月10日（28歲） 58 3 PAOK",
            "墨西哥",
        )
        self.assertIsNotNone(result)
        if result:
            self.assertEqual(result["name"], "豪尔赫·桑切斯")
            self.assertEqual(result["position"], "DF")
            self.assertEqual(result["caps"], 58)
            self.assertEqual(result["goals"], 3)
            self.assertEqual(result["club"], "PAOK")

    def test_parse_chinese_player_line_returns_none_for_non_player(self):
        self.assertIsNone(_parse_chinese_player_line("主教练：哈维尔·阿吉雷", "墨西哥"))
        self.assertIsNone(_parse_chinese_player_line("", "巴西"))
        self.assertIsNone(_parse_chinese_player_line("A组 墨西哥", "墨西哥"))

    # ========================================================================
    # 中文日期转换
    # ========================================================================

    def test_convert_chinese_date(self):
        self.assertEqual(_convert_chinese_date("2000年2月25日"), "2000-02-25")
        self.assertEqual(_convert_chinese_date("1997年12月10日"), "1997-12-10")
        self.assertIsNone(_convert_chinese_date("not a date"))

    # ========================================================================
    # 中文国家名转换
    # ========================================================================

    def test_resolve_chinese_country(self):
        self.assertEqual(_resolve_chinese_country("阿联酋"), "United Arab Emirates")
        self.assertEqual(_resolve_chinese_country("日本"), "Japan")
        self.assertIsNone(_resolve_chinese_country("火星"))

    # ========================================================================
    # 比赛行解析
    # ========================================================================

    def test_parse_match_lines_with_date_format(self):
        matches = _parse_match_lines(
            "2026-06-11 19:00 Mexico vs South Africa Estadio Azteca"
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["home"], "Mexico")
        self.assertEqual(matches[0]["away"], "South Africa")

    def test_parse_match_lines_returns_empty_for_no_match(self):
        matches = _parse_match_lines("Some random text without a match")
        self.assertEqual(len(matches), 0)

    # ========================================================================
    # 裁判 PDF 解析
    # ========================================================================

    def test_parse_referee_pdf_generates_candidates(self):
        fake_result = PDFTextExtractionResult(
            path="referee.pdf",
            page_count=2,
            character_count=100,
            text="",
            pages=[
                "奥马尔·阿里（阿联酋） 穆罕默德·哈马迪（阿联酋）",
                "马宁（中国） 周飞（中国）",
            ],
        )
        candidates = parse_referee_pdf(fake_result)
        self.assertGreater(len(candidates), 0)
        for c in candidates:
            self.assertEqual(c.source_pdf, "referee.pdf")
            self.assertGreater(c.page_number, 0)
            self.assertTrue(c.raw_text)
            self.assertTrue(c.referee_name)
            self.assertTrue(c.country)

    def test_parse_referee_pdf_filters_header_lines(self):
        fake_result = PDFTextExtractionResult(
            path="referee.pdf",
            page_count=1,
            character_count=50,
            text="",
            pages=[
                "所属大洲 主裁判 助理裁判 执法比赛 第四官员\n"
                "奥马尔·阿里（阿联酋） 穆罕默德·哈马迪（阿联酋）",
            ],
        )
        candidates = parse_referee_pdf(fake_result)
        # 应该只提取姓名（国家）模式，不提取表头
        for c in candidates:
            self.assertTrue(c.referee_name)
            self.assertTrue(c.country)
            self.assertNotIn("所属", c.referee_name)

    # ========================================================================
    # 参赛名单 PDF 解析
    # ========================================================================

    def test_parse_squad_pdf_with_valid_player_lines(self):
        fake_result = PDFTextExtractionResult(
            path="squad.pdf",
            page_count=1,
            character_count=200,
            text="",
            pages=[
                "A组 墨西哥\n\n"
                "1 門将 劳尔·兰赫尔 2000年2月25日（26 歲） 13 0 瓜达拉哈拉\n"
                "2 后卫 豪尔赫·桑切斯 1997年12月10日（28歲） 58 3 PAOK",
            ],
        )
        candidates = parse_squad_pdf(fake_result)
        self.assertGreater(len(candidates), 0)
        for c in candidates:
            self.assertEqual(c.source_pdf, "squad.pdf")
            self.assertIn(c.national_team, ("墨西哥", None))

    def test_parse_squad_pdf_does_not_assign_next_team_players_to_previous_team(self):
        fake_result = PDFTextExtractionResult(
            path="squad.pdf",
            page_count=1,
            character_count=300,
            text="",
            pages=[
                "A组 墨西哥\n"
                "1 門将 劳尔·兰赫尔 2000年2月25日（26 歲） 13 0 瓜达拉哈拉\n"
                "南 非\n"
                "4 中場 Teboho Mokoena 1997年1月24日（29歲） 51 9 马梅洛迪日落",
            ],
        )

        candidates = parse_squad_pdf(fake_result)

        teams_by_player = {
            candidate.player_name: candidate.national_team for candidate in candidates
        }
        self.assertEqual(teams_by_player["劳尔·兰赫尔"], "墨西哥")
        self.assertEqual(teams_by_player["Teboho Mokoena"], "南非")
        self.assertNotEqual(teams_by_player["Teboho Mokoena"], "墨西哥")

    def test_parse_squad_pdf_preserves_raw_text(self):
        fake_result = PDFTextExtractionResult(
            path="squad.pdf",
            page_count=1,
            character_count=100,
            text="",
            pages=["A组 墨西哥\n\n1 門将 劳尔·兰赫尔 2000年2月25日（26 歲） 13 0 瓜达拉哈拉"],
        )
        candidates = parse_squad_pdf(fake_result)
        for c in candidates:
            self.assertTrue(c.raw_text)
            self.assertGreater(len(c.raw_text), 0)

    # ========================================================================
    # 赛程 PDF 解析
    # ========================================================================

    def test_parse_schedule_pdf_generates_candidates(self):
        fake_result = PDFTextExtractionResult(
            path="schedule.pdf",
            page_count=1,
            character_count=100,
            text="",
            pages=["2026-06-11 19:00 Mexico vs South Africa Estadio Azteca"],
        )
        candidates = parse_schedule_pdf(fake_result)
        for c in candidates:
            self.assertEqual(c.source_pdf, "schedule.pdf")
            self.assertTrue(c.raw_text)

    # ========================================================================
    # 主管线：PDF → staging
    # ========================================================================

    @patch("football_advisor.pdf_staging_pipeline.extract_pdf_text")
    @patch("football_advisor.pdf_staging_pipeline.summarize_pdf_text")
    def test_run_pipeline_writes_to_staging(self, mock_summarize, mock_extract):
        """验证主管线将候选数据写入 staging 表。"""
        from football_advisor.pdf_text_extractor import PDFTextSummary

        mock_extract.return_value = PDFTextExtractionResult(
            path="referee.pdf",
            page_count=2,
            character_count=100,
            text="",
            pages=[
                "AFC Referee1 (China) AR1 (Japan)",
                "CAF Referee2 (Nigeria) AR3 (Egypt)",
            ],
        )
        mock_summarize.return_value = PDFTextSummary(
            page_count=2,
            character_count=100,
            detected_topics=["裁判"],
            preview="",
        )

        conn = MagicMock()
        result = run_pdf_staging_pipeline(["referee.pdf"], conn)

        self.assertGreaterEqual(len(result.referee_candidates), 0)
        self.assertIsInstance(result, PDFStagingResult)

    # ========================================================================
    # 实体映射
    # ========================================================================

    def test_run_entity_mapping_validates_referees(self):
        """验证实体映射对裁判候选的处理。"""
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None

        staging_result = PDFStagingResult(
            referee_candidates=[
                RefereeCandidate(
                    source_pdf="referee.pdf",
                    page_number=1,
                    raw_text="AFC Referee1 (China)",
                    referee_name="Referee1",
                    country="China",
                )
            ]
        )

        result = run_entity_mapping(conn, staging_result)
        self.assertIsInstance(result, EntityMappingResult)
        # 未映射到现有 dim_referee_profile
        self.assertEqual(result.referees_mapped, 0)
        self.assertEqual(result.referees_unmapped, 1)

    # ========================================================================
    # 交叉校验
    # ========================================================================

    def test_run_cross_validation_rejects_invalid_referee(self):
        """验证交叉校验拒绝无效裁判（无姓名）。"""
        conn = MagicMock()

        staging_result = PDFStagingResult(
            referee_candidates=[
                RefereeCandidate(
                    source_pdf="referee.pdf",
                    page_number=1,
                    raw_text="some text",
                    referee_name=None,
                    country=None,
                )
            ]
        )

        result = run_cross_validation(conn, staging_result)
        self.assertEqual(result.referees_validated, 0)
        self.assertEqual(result.referees_rejected, 1)
        self.assertEqual(result.no_bet_evidence, 1)

    def test_run_cross_validation_validates_player_with_known_team(self):
        """验证交叉校验通过已知国家队的球员。"""
        conn = MagicMock()

        staging_result = PDFStagingResult(
            player_candidates=[
                PlayerCandidate(
                    source_pdf="squad.pdf",
                    page_number=1,
                    raw_text="1 門将 劳尔·兰赫尔 2000年2月25日 13 0 瓜达拉哈拉",
                    player_name="劳尔·兰赫尔",
                    position="GK",
                    national_team="墨西哥",
                )
            ]
        )

        result = run_cross_validation(conn, staging_result)
        # 墨西哥 is in CHINESE_TEAM_MAP
        self.assertEqual(result.players_validated, 1)
        self.assertEqual(result.players_rejected, 0)
        self.assertEqual(result.core_writes, 1)

    def test_run_cross_validation_rejects_player_with_unknown_team(self):
        """验证交叉校验拒绝未知国家队的球员。"""
        conn = MagicMock()

        staging_result = PDFStagingResult(
            player_candidates=[
                PlayerCandidate(
                    source_pdf="squad.pdf",
                    page_number=1,
                    raw_text="1 GK Player Name 30 5 0 Club",
                    player_name="Player Name",
                    position="GK",
                    national_team="Unknown Country",
                )
            ]
        )

        result = run_cross_validation(conn, staging_result)
        self.assertEqual(result.players_validated, 0)
        self.assertEqual(result.players_rejected, 1)

    def test_run_cross_validation_rejects_schedule_with_unmapped_teams(self):
        """验证交叉校验拒绝无法映射球队的赛程。"""
        conn = MagicMock()

        staging_result = PDFStagingResult(
            schedule_candidates=[
                ScheduleCandidate(
                    source_pdf="schedule.pdf",
                    page_number=1,
                    raw_text="match line",
                    home_team_name="Unknown Home",
                    away_team_name="Unknown Away",
                )
            ]
        )

        result = run_cross_validation(conn, staging_result)
        self.assertEqual(result.schedules_validated, 0)
        self.assertEqual(result.schedules_rejected, 1)

    # ========================================================================
    # No Bet 证据标记
    # ========================================================================

    def test_cross_validation_result_tracks_no_bet_evidence(self):
        """验证交叉校验结果正确追踪 No Bet 证据计数。"""
        result = CrossValidationResult(
            referees_validated=5,
            referees_rejected=3,
            players_validated=10,
            players_rejected=2,
            schedules_validated=0,
            schedules_rejected=4,
            core_writes=15,
            no_bet_evidence=9,
        )
        self.assertEqual(result.core_writes, 15)
        self.assertEqual(result.no_bet_evidence, 9)

    # ========================================================================
    # KNOWN_TEAM_MAP 完整性
    # ========================================================================

    def test_known_team_map_contains_core_teams(self):
        """验证已知球队映射包含核心世界杯球队。"""
        core_teams = [
            "argentina", "brazil", "england", "france", "germany",
            "mexico", "south africa", "spain", "portugal", "japan",
            "south korea", "usa", "netherlands", "uruguay", "croatia",
        ]
        for team in core_teams:
            self.assertIn(team, KNOWN_TEAM_MAP, f"缺少核心球队: {team}")


class ChineseMappingTests(unittest.TestCase):
    """中文映射表完整性测试。"""

    def test_resolve_team_id_chinese_name(self):
        self.assertEqual(_resolve_team_id("墨西哥"), "WC_TEAM_MEX")
        self.assertEqual(_resolve_team_id("巴西"), "WC_TEAM_BRA")

    def test_chinese_team_map_contains_core_teams(self):
        core_cn_teams = ["墨西哥", "巴西", "阿根廷", "英格兰", "法国", "德国", "西班牙", "葡萄牙", "日本", "韩国"]
        for team in core_cn_teams:
            self.assertIn(team, CHINESE_TEAM_MAP, f"缺少核心中文队名: {team}")

    def test_chinese_country_map_covers_main_countries(self):
        main_countries = ["日本", "韩国", "中国", "巴西", "阿根廷", "英格兰", "法国", "德国"]
        for country in main_countries:
            self.assertIn(country, CHINESE_COUNTRY_MAP, f"缺少中文国家名: {country}")


if __name__ == "__main__":
    unittest.main()
