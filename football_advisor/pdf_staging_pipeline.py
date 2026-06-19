"""PDF → staging → DuckDB 数据管线。

将 Wikipedia 导出的 PDF（裁判、名单、赛制）经过：
  PDF 文本抽取 → 分页证据 → 结构化候选 staging → 实体映射/交叉校验 → 合格后写 DuckDB core 或作为 No Bet 证据

3 个 PDF 都不能直接进正式预测事实链路。所有数据必须先落 staging，经过实体映射和交叉校验后才能进入 core。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .pdf_text_extractor import PDFTextExtractionResult, extract_pdf_text, summarize_pdf_text


# ============================================================================
# 数据结构
# ============================================================================


@dataclass(frozen=True)
class RefereeCandidate:
    source_pdf: str
    page_number: int
    raw_text: str
    referee_name: str | None = None
    country: str | None = None
    role: str | None = None


@dataclass(frozen=True)
class PlayerCandidate:
    source_pdf: str
    page_number: int
    raw_text: str
    player_name: str | None = None
    position: str | None = None
    birth_date: str | None = None
    caps: int | None = None
    goals: int | None = None
    club: str | None = None
    national_team: str | None = None


@dataclass(frozen=True)
class ScheduleCandidate:
    source_pdf: str
    page_number: int
    raw_text: str
    home_team_name: str | None = None
    away_team_name: str | None = None
    match_date: str | None = None
    venue: str | None = None


@dataclass(frozen=True)
class PDFStagingResult:
    referee_candidates: list[RefereeCandidate] = field(default_factory=list)
    player_candidates: list[PlayerCandidate] = field(default_factory=list)
    schedule_candidates: list[ScheduleCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_candidates(self) -> int:
        return (
            len(self.referee_candidates)
            + len(self.player_candidates)
            + len(self.schedule_candidates)
        )


@dataclass
class EntityMappingResult:
    referees_mapped: int = 0
    referees_unmapped: int = 0
    players_mapped: int = 0
    players_unmapped: int = 0
    schedules_mapped: int = 0
    schedules_unmapped: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class CrossValidationResult:
    referees_validated: int = 0
    referees_rejected: int = 0
    players_validated: int = 0
    players_rejected: int = 0
    schedules_validated: int = 0
    schedules_rejected: int = 0
    core_writes: int = 0
    no_bet_evidence: int = 0
    warnings: list[str] = field(default_factory=list)


# ============================================================================
# 已知实体映射（用于交叉校验）
# ============================================================================

# 世界杯 48 队标准名称 → system_team_id 映射
KNOWN_TEAM_MAP: dict[str, str] = {
    "algeria": "WC_TEAM_ALG",
    "argentina": "WC_TEAM_ARG",
    "australia": "WC_TEAM_AUS",
    "austria": "WC_TEAM_AUT",
    "belgium": "WC_TEAM_BEL",
    "bosnia and herzegovina": "WC_TEAM_BIH",
    "brazil": "WC_TEAM_BRA",
    "cape verde": "WC_TEAM_CPV",
    "canada": "WC_TEAM_CAN",
    "colombia": "WC_TEAM_COL",
    "croatia": "WC_TEAM_CRO",
    "curacao": "WC_TEAM_CUW",
    "czechia": "WC_TEAM_CZE",
    "ivory coast": "WC_TEAM_CIV",
    "côte d'ivoire": "WC_TEAM_CIV",
    "dr congo": "WC_TEAM_COD",
    "congo dr": "WC_TEAM_COD",
    "ecuador": "WC_TEAM_ECU",
    "egypt": "WC_TEAM_EGY",
    "england": "WC_TEAM_ENG",
    "france": "WC_TEAM_FRA",
    "germany": "WC_TEAM_GER",
    "ghana": "WC_TEAM_GHA",
    "haiti": "WC_TEAM_HAI",
    "iran": "WC_TEAM_IRN",
    "iraq": "WC_TEAM_IRQ",
    "japan": "WC_TEAM_JPN",
    "jordan": "WC_TEAM_JOR",
    "south korea": "WC_TEAM_KOR",
    "korea republic": "WC_TEAM_KOR",
    "mexico": "WC_TEAM_MEX",
    "morocco": "WC_TEAM_MAR",
    "netherlands": "WC_TEAM_NED",
    "new zealand": "WC_TEAM_NZL",
    "norway": "WC_TEAM_NOR",
    "panama": "WC_TEAM_PAN",
    "paraguay": "WC_TEAM_PAR",
    "portugal": "WC_TEAM_POR",
    "qatar": "WC_TEAM_QAT",
    "saudi arabia": "WC_TEAM_KSA",
    "scotland": "WC_TEAM_SCO",
    "senegal": "WC_TEAM_SEN",
    "south africa": "WC_TEAM_RSA",
    "spain": "WC_TEAM_ESP",
    "sweden": "WC_TEAM_SWE",
    "switzerland": "WC_TEAM_SUI",
    "tunisia": "WC_TEAM_TUN",
    "turkiye": "WC_TEAM_TUR",
    "türkiye": "WC_TEAM_TUR",
    "usa": "WC_TEAM_USA",
    "united states": "WC_TEAM_USA",
    "uruguay": "WC_TEAM_URU",
    "uzbekistan": "WC_TEAM_UZB",
}

# 已知国家名（用于裁判实体映射）
KNOWN_COUNTRIES: set[str] = {
    name.title() for name in KNOWN_TEAM_MAP
} | {
    "China", "Russia", "Italy", "Denmark", "Greece", "Poland", "Serbia",
    "Nigeria", "Cameroon", "Mali", "Zambia", "Guatemala", "Honduras",
    "El Salvador", "Costa Rica", "Chile", "Peru", "Venezuela", "Bolivia",
    "Romania", "Hungary", "Slovakia", "Slovenia", "Finland", "Iceland",
    "Wales", "Northern Ireland", "Ireland",
}

# 中文队名 → system_team_id 映射
CHINESE_TEAM_MAP: dict[str, str] = {
    "墨西哥": "WC_TEAM_MEX",
    "阿根廷": "WC_TEAM_ARG",
    "澳大利亚": "WC_TEAM_AUS",
    "奥地利": "WC_TEAM_AUT",
    "比利时": "WC_TEAM_BEL",
    "波黑": "WC_TEAM_BIH",
    "波斯尼亚和黑塞哥维那": "WC_TEAM_BIH",
    "巴西": "WC_TEAM_BRA",
    "佛得角": "WC_TEAM_CPV",
    "加拿大": "WC_TEAM_CAN",
    "哥伦比亚": "WC_TEAM_COL",
    "克罗地亚": "WC_TEAM_CRO",
    "库拉索": "WC_TEAM_CUW",
    "捷克": "WC_TEAM_CZE",
    "科特迪瓦": "WC_TEAM_CIV",
    "刚果民主共和国": "WC_TEAM_COD",
    "刚果（金）": "WC_TEAM_COD",
    "厄瓜多尔": "WC_TEAM_ECU",
    "埃及": "WC_TEAM_EGY",
    "英格兰": "WC_TEAM_ENG",
    "法国": "WC_TEAM_FRA",
    "德国": "WC_TEAM_GER",
    "加纳": "WC_TEAM_GHA",
    "海地": "WC_TEAM_HAI",
    "伊朗": "WC_TEAM_IRN",
    "伊拉克": "WC_TEAM_IRQ",
    "日本": "WC_TEAM_JPN",
    "约旦": "WC_TEAM_JOR",
    "韩国": "WC_TEAM_KOR",
    "摩洛哥": "WC_TEAM_MAR",
    "荷兰": "WC_TEAM_NED",
    "新西兰": "WC_TEAM_NZL",
    "挪威": "WC_TEAM_NOR",
    "巴拿马": "WC_TEAM_PAN",
    "巴拉圭": "WC_TEAM_PAR",
    "葡萄牙": "WC_TEAM_POR",
    "卡塔尔": "WC_TEAM_QAT",
    "沙特阿拉伯": "WC_TEAM_KSA",
    "沙特": "WC_TEAM_KSA",
    "苏格兰": "WC_TEAM_SCO",
    "塞内加尔": "WC_TEAM_SEN",
    "南非": "WC_TEAM_RSA",
    "西班牙": "WC_TEAM_ESP",
    "瑞典": "WC_TEAM_SWE",
    "瑞士": "WC_TEAM_SUI",
    "突尼斯": "WC_TEAM_TUN",
    "土耳其": "WC_TEAM_TUR",
    "美国": "WC_TEAM_USA",
    "乌拉圭": "WC_TEAM_URU",
    "乌兹别克斯坦": "WC_TEAM_UZB",
    "阿尔及利亚": "WC_TEAM_ALG",
}

# 中文位置映射
CHINESE_POSITION_MAP: dict[str, str] = {
    "门将": "GK",
    "門將": "GK",
    "后卫": "DF",
    "後衛": "DF",
    "中場": "MF",
    "中场": "MF",
    "前鋒": "FW",
    "前锋": "FW",
    "門卫": "GK",
    "門将": "GK",
}

# 中文国家名 → 英文国家名（用于裁判国家识别）
CHINESE_COUNTRY_MAP: dict[str, str] = {
    "中国": "China",
    "阿联酋": "United Arab Emirates",
    "卡達": "Qatar",
    "卡塔尔": "Qatar",
    "沙特阿拉伯": "Saudi Arabia",
    "澳大利亞": "Australia",
    "澳大利亚": "Australia",
    "約旦": "Jordan",
    "约旦": "Jordan",
    "乌兹别克斯坦": "Uzbekistan",
    "日本": "Japan",
    "索馬里": "Somalia",
    "喀麥隆": "Cameroon",
    "加蓬": "Gabon",
    "毛里塔尼亞": "Mauritania",
    "安哥拉": "Angola",
    "阿尔及利亚": "Algeria",
    "埃及": "Egypt",
    "摩洛哥": "Morocco",
    "塞内加尔": "Senegal",
    "突尼斯": "Tunisia",
    "尼日利亚": "Nigeria",
    "南非": "South Africa",
    "加纳": "Ghana",
    "科特迪瓦": "Ivory Coast",
    "刚果": "Congo",
    "英格兰": "England",
    "法国": "France",
    "德国": "Germany",
    "意大利": "Italy",
    "西班牙": "Spain",
    "荷兰": "Netherlands",
    "葡萄牙": "Portugal",
    "比利时": "Belgium",
    "瑞士": "Switzerland",
    "瑞典": "Sweden",
    "波兰": "Poland",
    "罗马尼亚": "Romania",
    "塞尔维亚": "Serbia",
    "克罗地亚": "Croatia",
    "捷克": "Czechia",
    "斯洛伐克": "Slovakia",
    "匈牙利": "Hungary",
    "俄罗斯": "Russia",
    "土耳其": "Turkiye",
    "希腊": "Greece",
    "丹麦": "Denmark",
    "挪威": "Norway",
    "芬兰": "Finland",
    "巴西": "Brazil",
    "阿根廷": "Argentina",
    "乌拉圭": "Uruguay",
    "智利": "Chile",
    "哥伦比亚": "Colombia",
    "秘鲁": "Peru",
    "委内瑞拉": "Venezuela",
    "巴拉圭": "Paraguay",
    "厄瓜多尔": "Ecuador",
    "玻利维亚": "Bolivia",
    "墨西哥": "Mexico",
    "美国": "United States",
    "加拿大": "Canada",
    "哥斯达黎加": "Costa Rica",
    "洪都拉斯": "Honduras",
    "巴拿马": "Panama",
    "危地马拉": "Guatemala",
    "萨尔瓦多": "El Salvador",
    "韩国": "South Korea",
    "伊朗": "Iran",
    "伊拉克": "Iraq",
    "新西兰": "New Zealand",
}


# ============================================================================
# PDF 结构化解析
# ============================================================================


def parse_referee_pdf(pdf_result: PDFTextExtractionResult) -> list[RefereeCandidate]:
    """从裁判 PDF 中提取裁判候选人。

    裁判 PDF 格式（中文 Wikipedia）：
    以"所属大洲"、"主裁判"、"助理裁判"等为表头，每行包含：
    奥马尔·阿里（阿联酋） 穆罕默德·哈马迪（阿联酋） ...
    其中括号内为国家名，括号前为裁判姓名。
    """
    candidates: list[RefereeCandidate] = []
    # 匹配 "姓名（国家）" 模式
    name_country_pattern = re.compile(r"([^\s（(]+)[（(]([^）)]+)[）)]")

    for page_idx, page_text in enumerate(pdf_result.pages, start=1):
        if not page_text.strip():
            continue
        # 在所有文本中查找 "姓名（国家）" 模式
        for match in name_country_pattern.finditer(page_text):
            name = match.group(1).strip()
            country_cn = match.group(2).strip()
            # 过滤掉明显不是人名的匹配（如数字、英文单词等）
            if len(name) < 2 or len(name) > 30:
                continue
            if re.search(r"[a-zA-Z]", name) and not re.search(r"[\u4e00-\u9fff]", name):
                continue
            # 将中文国家名转换为英文
            country_en = _resolve_chinese_country(country_cn)
            candidates.append(
                RefereeCandidate(
                    source_pdf=pdf_result.path,
                    page_number=page_idx,
                    raw_text=match.group(0)[:500],
                    referee_name=name,
                    country=country_en or country_cn,
                )
            )
    return candidates


def parse_squad_pdf(pdf_result: PDFTextExtractionResult) -> list[PlayerCandidate]:
    """从参赛名单 PDF 中提取球员候选人。

    参赛名单 PDF 格式（中文 Wikipedia，表格文字垂直拆分）：
    每队以"X组 队名"或"主教练"开头。
    表格单元格包含换行符，如 "1 ⻔\\n将 劳尔·兰赫尔 2000年2⽉25⽇（26\\n歲） 13 0\\n  ⽠达拉哈拉"

    处理策略：
    1. 先合并页内换行，还原表格行
    2. 按球员号码分割
    3. 解析每个球员的字段
    """
    candidates: list[PlayerCandidate] = []
    current_team: str | None = None

    for page_idx, page_text in enumerate(pdf_result.pages, start=1):
        if not page_text.strip():
            continue

        # 检测中文国家队名
        detected_team = _detect_chinese_team_header(page_text)
        if detected_team:
            current_team = detected_team

        # 先合并所有行，再按换行符分割
        # 表格单元格内的换行需要合并：将 "\n" 替换为 "" 来合并被拆分的单元格
        merged = _merge_table_newlines(page_text)

        # 按行分割（有些行可能被合并了）
        lines = merged.split("\n")

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # 检测新队名
            new_team = _detect_chinese_team_header(stripped)
            if new_team:
                current_team = new_team
                continue

            # 尝试解析中文球员行
            player = _parse_chinese_player_line(stripped, current_team)
            if player:
                candidates.append(
                    PlayerCandidate(
                        source_pdf=pdf_result.path,
                        page_number=page_idx,
                        raw_text=stripped[:500],
                        player_name=player.get("name"),
                        position=player.get("position"),
                        birth_date=player.get("birth_date"),
                        caps=player.get("caps"),
                        goals=player.get("goals"),
                        club=player.get("club"),
                        national_team=current_team,
                    )
                )
    return candidates


def _merge_table_newlines(text: str) -> str:
    """合并 Wikipedia PDF 表格中被拆分的单元格。

    表格单元格内的换行符需要合并。策略：
    - 位置字符延续（"将"、"卫"、"場"、"鋒"）→ 合并到上一行
    - 数字延续（年龄、出场等）→ 合并到上一行
    - 以空格开头（俱乐部名称延续）→ 合并到上一行
    - 年龄延续（"歲）"）→ 合并到上一行
    """
    lines = text.split("\n")
    if len(lines) <= 1:
        return text

    merged: list[str] = []
    for line in lines:
        stripped = line.strip() if line else ""
        if not stripped:
            merged.append(line)
            continue

        if merged and _is_table_continuation(line, merged[-1]):
            merged[-1] = merged[-1] + stripped
        else:
            merged.append(line)

    # 第二轮合并：合并以空格开头的行（俱乐部名称延续）
    merged2: list[str] = []
    for line in merged:
        if line.startswith("  ") and merged2:
            merged2[-1] = merged2[-1] + " " + line.strip()
        elif line.startswith(" ") and merged2:
            merged2[-1] = merged2[-1] + " " + line.strip()
        else:
            merged2.append(line)

    return "\n".join(merged2)


def _is_table_continuation(line: str, prev_line: str = "") -> bool:
    """判断该行是否是表格单元格的延续。

    表格单元格的延续行特征：
    - 以位置字符（将、卫、場、鋒、赛、球）开头
    - 以数字开头（年龄、出场、进球）
    - 以 "歲）" 开头
    - 以 "（队" 开头（队长标记延续）
    """
    stripped = line.strip()
    if not stripped:
        return False
    # 如果上一行看起来像表头，不合并
    if prev_line and _looks_like_header(prev_line):
        return False
    # 位置字符延续
    if stripped[0] in {"将", "卫", "場", "鋒", "赛", "球"}:
        return True
    # 数字延续（年龄、出场等）
    if re.match(r"^\d", stripped) and not re.match(r"^\d{1,2}\s", stripped):
        return True
    # 年龄延续
    if stripped.startswith("歲）") or stripped.startswith("歲)"):
        return True
    # 队长标记延续
    if stripped.startswith("（队"):
        return True
    return False


def _looks_like_header(line: str) -> bool:
    """判断是否看起来像表格表头。"""
    header_keywords = {"號碼", "位置", "球員", "出生", "出賽", "入球", "效力", "球會", "号码", "号码", "编号"}
    return any(kw in line for kw in header_keywords)


def parse_schedule_pdf(pdf_result: PDFTextExtractionResult) -> list[ScheduleCandidate]:
    """从赛程/赛制 PDF 中提取赛程候选人。

    赛程 PDF 格式（Wikipedia）：
    包含比赛日期、主客队、场馆等信息。
    小组赛格式：Date | Team A | Score | Team B | Venue
    """
    candidates: list[ScheduleCandidate] = []
    for page_idx, page_text in enumerate(pdf_result.pages, start=1):
        if not page_text.strip():
            continue
        # 尝试匹配比赛行
        matches = _parse_match_lines(page_text)
        for match in matches:
            candidates.append(
                ScheduleCandidate(
                    source_pdf=pdf_result.path,
                    page_number=page_idx,
                    raw_text=match.get("raw", "")[:500],
                    home_team_name=match.get("home"),
                    away_team_name=match.get("away"),
                    match_date=match.get("date"),
                    venue=match.get("venue"),
                )
            )
    return candidates


# ============================================================================
# 主管线：PDF → staging
# ============================================================================


def run_pdf_staging_pipeline(
    pdf_paths: list[str],
    connection: Any,
) -> PDFStagingResult:
    """执行 PDF → staging 管线。

    对每个 PDF：
    1. 文本抽取
    2. 主题识别
    3. 按主题解析结构化候选
    4. 写入 staging 表
    """
    all_referees: list[RefereeCandidate] = []
    all_players: list[PlayerCandidate] = []
    all_schedules: list[ScheduleCandidate] = []
    warnings: list[str] = []

    for pdf_path in pdf_paths:
        path = Path(pdf_path)
        if not path.exists():
            warnings.append(f"PDF 不存在: {pdf_path}")
            continue

        try:
            pdf_result = extract_pdf_text(path)
        except Exception as exc:
            warnings.append(f"PDF 解析失败 {pdf_path}: {exc}")
            continue

        summary = summarize_pdf_text(pdf_result.text, pdf_result.page_count)

        if "裁判" in summary.detected_topics:
            referees = parse_referee_pdf(pdf_result)
            _write_referee_candidates(connection, referees)
            all_referees.extend(referees)

        if "参赛名单" in summary.detected_topics:
            players = parse_squad_pdf(pdf_result)
            _write_player_candidates(connection, players)
            all_players.extend(players)

        if "赛程" in summary.detected_topics or "规则/赛事说明" in summary.detected_topics:
            schedules = parse_schedule_pdf(pdf_result)
            _write_schedule_candidates(connection, schedules)
            all_schedules.extend(schedules)

    return PDFStagingResult(
        referee_candidates=all_referees,
        player_candidates=all_players,
        schedule_candidates=all_schedules,
        warnings=warnings,
    )


# ============================================================================
# 实体映射
# ============================================================================


def run_entity_mapping(
    connection: Any,
    staging_result: PDFStagingResult,
) -> EntityMappingResult:
    """对 staging 中的候选数据进行实体映射。

    裁判 → dim_referee_profile
    球员 → dim_team_mapping + dim_player_mapping
    赛程 → fact_match_schedule
    """
    result = EntityMappingResult()

    # 映射裁判
    for ref in staging_result.referee_candidates:
        mapped = _map_referee_entity(connection, ref)
        if mapped:
            result.referees_mapped += 1
        else:
            result.referees_unmapped += 1

    # 映射球员
    for player in staging_result.player_candidates:
        mapped = _map_player_entity(connection, player)
        if mapped:
            result.players_mapped += 1
        else:
            result.players_unmapped += 1

    # 映射赛程
    for sched in staging_result.schedule_candidates:
        mapped = _map_schedule_entity(connection, sched)
        if mapped:
            result.schedules_mapped += 1
        else:
            result.schedules_unmapped += 1

    return result


# ============================================================================
# 交叉校验
# ============================================================================


def run_cross_validation(
    connection: Any,
    staging_result: PDFStagingResult,
) -> CrossValidationResult:
    """对实体映射后的候选进行交叉校验。

    校验规则：
    - 裁判姓名必须能匹配已知国家
    - 球员必须能映射到已知国家队
    - 赛程必须能和已有 fact_match_schedule 交叉验证
    - 只有通过校验的才能写入 core
    """
    result = CrossValidationResult()

    # 裁判交叉校验：姓名不为空 + 国家可识别
    for ref in staging_result.referee_candidates:
        if ref.referee_name and ref.country and _normalize_name(ref.country).title() in KNOWN_COUNTRIES:
            _write_referee_to_core(connection, ref)
            result.referees_validated += 1
            result.core_writes += 1
        else:
            result.referees_rejected += 1
            result.no_bet_evidence += 1

    # 球员交叉校验：姓名不为空 + 国家队可映射
    for player in staging_result.player_candidates:
        if player.player_name and player.national_team:
            team_id = _resolve_team_id(player.national_team)
            if team_id:
                _write_player_to_core(connection, player, team_id)
                result.players_validated += 1
                result.core_writes += 1
            else:
                result.players_rejected += 1
                result.no_bet_evidence += 1
        else:
            result.players_rejected += 1
            result.no_bet_evidence += 1

    # 赛程交叉校验：主客队都可映射 + 日期可解析
    for sched in staging_result.schedule_candidates:
        if sched.home_team_name and sched.away_team_name:
            home_id = _resolve_team_id(sched.home_team_name)
            away_id = _resolve_team_id(sched.away_team_name)
            if home_id and away_id:
                _write_schedule_to_core(connection, sched, home_id, away_id)
                result.schedules_validated += 1
                result.core_writes += 1
            else:
                result.schedules_rejected += 1
                result.no_bet_evidence += 1
        else:
            result.schedules_rejected += 1
            result.no_bet_evidence += 1

    return result


# ============================================================================
# 内部辅助函数
# ============================================================================


def _write_referee_candidates(connection: Any, candidates: list[RefereeCandidate]) -> None:
    if not candidates:
        return
    connection.executemany(
        """
        INSERT INTO staging.stg_referee_candidates (
            source_pdf, page_number, raw_text, referee_name, country, role
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (c.source_pdf, c.page_number, c.raw_text, c.referee_name, c.country, c.role)
            for c in candidates
        ],
    )


def _write_player_candidates(connection: Any, candidates: list[PlayerCandidate]) -> None:
    if not candidates:
        return
    connection.executemany(
        """
        INSERT INTO staging.stg_player_candidates (
            source_pdf, page_number, raw_text, player_name, position,
            birth_date, caps, goals, club, national_team
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                c.source_pdf, c.page_number, c.raw_text,
                c.player_name, c.position, c.birth_date,
                c.caps, c.goals, c.club, c.national_team,
            )
            for c in candidates
        ],
    )


def _write_schedule_candidates(connection: Any, candidates: list[ScheduleCandidate]) -> None:
    if not candidates:
        return
    connection.executemany(
        """
        INSERT INTO staging.stg_schedule_candidates (
            source_pdf, page_number, raw_text,
            home_team_name, away_team_name, match_date, venue
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (c.source_pdf, c.page_number, c.raw_text,
             c.home_team_name, c.away_team_name, c.match_date, c.venue)
            for c in candidates
        ],
    )


def _map_referee_entity(connection: Any, candidate: RefereeCandidate) -> bool:
    """尝试将裁判候选人映射到 dim_referee_profile。"""
    if not candidate.referee_name:
        return False
    # 检查是否已存在
    row = connection.execute(
        "SELECT referee_id FROM core.dim_referee_profile WHERE lower(referee_name) = lower(?) LIMIT 1",
        [candidate.referee_name],
    ).fetchone()
    if row:
        connection.execute(
            "UPDATE staging.stg_referee_candidates SET entity_mapping_status = 'MAPPED' WHERE source_pdf = ? AND page_number = ? AND raw_text = ?",
            [candidate.source_pdf, candidate.page_number, candidate.raw_text],
        )
        return True
    return False


def _map_player_entity(connection: Any, candidate: PlayerCandidate) -> bool:
    """尝试将球员候选人映射到 dim_player_mapping。"""
    if not candidate.player_name:
        return False
    row = connection.execute(
        "SELECT system_player_id FROM core.dim_player_mapping WHERE lower(player_standard_name) = lower(?) LIMIT 1",
        [candidate.player_name],
    ).fetchone()
    if row:
        connection.execute(
            "UPDATE staging.stg_player_candidates SET entity_mapping_status = 'MAPPED', system_player_id = ? WHERE source_pdf = ? AND page_number = ? AND raw_text = ?",
            [str(row[0]), candidate.source_pdf, candidate.page_number, candidate.raw_text],
        )
        return True
    return False


def _map_schedule_entity(connection: Any, candidate: ScheduleCandidate) -> bool:
    """尝试将赛程候选人映射到 fact_match_schedule。"""
    if not candidate.home_team_name or not candidate.away_team_name:
        return False
    home_id = _resolve_team_id(candidate.home_team_name)
    away_id = _resolve_team_id(candidate.away_team_name)
    if not home_id or not away_id:
        return False
    row = connection.execute(
        """
        SELECT match_id FROM core.fact_match_schedule
        WHERE home_team_id = ? AND away_team_id = ?
        LIMIT 1
        """,
        [home_id, away_id],
    ).fetchone()
    if row:
        connection.execute(
            "UPDATE staging.stg_schedule_candidates SET entity_mapping_status = 'MAPPED', match_id = ? WHERE source_pdf = ? AND page_number = ? AND raw_text = ?",
            [str(row[0]), candidate.source_pdf, candidate.page_number, candidate.raw_text],
        )
        return True
    return False


def _write_referee_to_core(connection: Any, candidate: RefereeCandidate) -> None:
    """将通过校验的裁判写入 dim_referee_profile。"""
    referee_id = f"REF_{_normalize_name(candidate.referee_name or 'UNKNOWN').upper().replace(' ', '_')[:40]}"
    connection.execute(
        """
        INSERT INTO core.dim_referee_profile (
            referee_id, referee_name, updated_at
        ) VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (referee_id) DO UPDATE SET
            referee_name = excluded.referee_name,
            updated_at = excluded.updated_at
        """,
        [referee_id, candidate.referee_name],
    )
    connection.execute(
        "UPDATE staging.stg_referee_candidates SET cross_validation_status = 'VALIDATED', quality_flag = 'PDF_VALIDATED' WHERE source_pdf = ? AND page_number = ? AND raw_text = ?",
        [candidate.source_pdf, candidate.page_number, candidate.raw_text],
    )


def _write_player_to_core(connection: Any, candidate: PlayerCandidate, team_id: str) -> None:
    """将通过校验的球员写入 dim_player_mapping。"""
    player_id = f"PDF_{team_id}_{_normalize_name(candidate.player_name or 'UNKNOWN').upper().replace(' ', '_')[:50]}"
    connection.execute(
        """
        INSERT INTO core.dim_player_mapping (
            system_player_id, player_standard_name, team_id, primary_position, updated_at
        ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (system_player_id) DO UPDATE SET
            player_standard_name = excluded.player_standard_name,
            team_id = excluded.team_id,
            primary_position = COALESCE(excluded.primary_position, core.dim_player_mapping.primary_position),
            updated_at = excluded.updated_at
        """,
        [player_id, candidate.player_name, team_id, candidate.position],
    )
    connection.execute(
        "UPDATE staging.stg_player_candidates SET cross_validation_status = 'VALIDATED', system_team_id = ?, system_player_id = ?, quality_flag = 'PDF_VALIDATED' WHERE source_pdf = ? AND page_number = ? AND raw_text = ?",
        [team_id, player_id, candidate.source_pdf, candidate.page_number, candidate.raw_text],
    )


def _write_schedule_to_core(
    connection: Any,
    candidate: ScheduleCandidate,
    home_id: str,
    away_id: str,
) -> None:
    """将通过校验的赛程交叉验证结果记录到 staging（不直接覆盖 fact_match_schedule）。"""
    connection.execute(
        "UPDATE staging.stg_schedule_candidates SET cross_validation_status = 'VALIDATED', match_id = ?, quality_flag = 'PDF_CROSS_VALIDATED' WHERE source_pdf = ? AND page_number = ? AND raw_text = ?",
        [f"PDF_XVAL_{home_id}_{away_id}", candidate.source_pdf, candidate.page_number, candidate.raw_text],
    )


# ============================================================================
# 中文 Wikipedia 解析辅助函数
# ============================================================================


def _resolve_chinese_country(country_cn: str) -> str | None:
    """将中文国家名转换为英文国家名。"""
    if country_cn in CHINESE_COUNTRY_MAP:
        return CHINESE_COUNTRY_MAP[country_cn]
    # 也检查英文名
    if country_cn in KNOWN_TEAM_MAP:
        return country_cn.title()
    return None


def _detect_chinese_team_header(text: str) -> str | None:
    """检测中文文本中的国家队名称。

    格式示例：
    - "A组 墨西哥" → "墨西哥"
    - "墨西哥国家足球队" → "墨西哥"
    - "主教练：哈维尔·阿吉雷 墨西哥" → "墨西哥"

    会先对文本做 Radical 字符标准化。
    """
    normalized = _normalize_pdf_radical_chars(text)
    compact_normalized = re.sub(r"\s+", "", normalized)
    for team_cn in sorted(CHINESE_TEAM_MAP, key=len, reverse=True):
        if team_cn in normalized or team_cn in compact_normalized:
            return team_cn
    # 也检查英文队名
    for team_en in KNOWN_TEAM_MAP:
        if re.search(rf"\b{re.escape(team_en)}\b", normalized, re.IGNORECASE):
            return team_en.title()
    return None


def _parse_chinese_player_line(line: str, national_team: str | None) -> dict[str, Any] | None:
    """解析中文球员行。

    格式示例（PDF 可能使用 Radical 字符）：
    - "1 ⻔ 将 劳尔·兰赫尔 2000年2⽉25⽇（26 歲） 13 0 ⽠达拉哈拉"
    - "2 后 卫 豪尔赫·桑切斯 1997年12⽉10⽇（28歲） 58 3 PAOK"

    返回: {"name": ..., "position": ..., "birth_date": ..., "caps": ..., "goals": ..., "club": ...}
    """
    stripped = line.strip()
    if len(stripped) < 5:
        return None

    # 必须由数字号码开头
    if not re.match(r"^\d{1,2}\s", stripped):
        return None

    # 预处理：修复 Radical 字符和空格
    normalized = _normalize_pdf_radical_chars(stripped)

    # 尝试匹配中文球员行格式
    # 模式: 号码 位置 姓名 出生日期（年龄） 出场 进球 俱乐部
    match = re.match(
        r"^(\d{1,2})\s+"
        r"([\u4e00-\u9fff]{2,3})\s+"
        r"(.+?)\s+"
        r"(\d{4}年\d{1,2}月\d{1,2}日)[^0-9]*\d+[^0-9]*\s+"
        r"(\d+)\s+"
        r"(\d+)\s+"
        r"(.+)$",
        normalized,
    )
    if match:
        pos_cn = match.group(2)
        position = CHINESE_POSITION_MAP.get(pos_cn, pos_cn)
        birth_date = _convert_chinese_date(match.group(4))
        return {
            "name": match.group(3).strip(),
            "position": position,
            "birth_date": birth_date,
            "caps": int(match.group(5)),
            "goals": int(match.group(6)),
            "club": match.group(7).strip(),
        }

    return None


def _normalize_pdf_radical_chars(text: str) -> str:
    """将 PDF 提取的 Radical/Kangxi 字符标准化为常规 CJK 字符。

    使用预建映射表进行 Radical → CJK 转换。
    同时修复位置字符之间多余的空格（如 "⻔ 将" → "門将"）。
    """
    # 预建 Radical → CJK 映射表（从 Unicode 字符名称推断）
    _RADICAL_TO_CJK = _get_radical_cjk_map()

    result = "".join(_RADICAL_TO_CJK.get(c, c) for c in text)

    # 修复已知位置名称中的空格（如 "門 将" → "門将", "后 卫" → "后卫"）
    position_fixes = {
        "門 将": "門将",
        "后 卫": "后卫",
        "中 場": "中場",
        "前 鋒": "前鋒",
        "门 将": "门将",
        "中 场": "中场",
        "前 锋": "前锋",
    }
    for bad, good in position_fixes.items():
        result = result.replace(bad, good)

    return result


def _get_radical_cjk_map() -> dict[str, str]:
    """返回 Radical/Kangxi 字符 → 常规 CJK 字符的映射表。

    从 Unicode 字符名称推断：
    Kangxi Radical "KANGXI RADICAL SUN" (U+2F47) → "日" (U+65E5)
    CJK Radical "CJK RADICAL SIMPLIFIED HORN" (U+2EC6) → "角" (U+89D2)
    """
    # 手动构建的映射表（覆盖 PDF 中出现的 Radical 字符）
    # Kangxi Radicals (U+2F00-U+2FDF)
    kangxi_radical_mapping = {
        0x2F00: 0x4E00,  # 一
        0x2F06: 0x4E8C,  # 二
        0x2F08: 0x4EBA,  # 人
        0x2F0A: 0x5165,  # 入
        0x2F0B: 0x516B,  # 八
        0x2F12: 0x529B,  # 力
        0x2F17: 0x5341,  # 十
        0x2F18: 0x535C,  # 卜
        0x2F1D: 0x53E3,  # 口
        0x2F1F: 0x571F,  # 土
        0x2F20: 0x58EB,  # 士
        0x2F24: 0x5927,  # 大
        0x2F26: 0x5B50,  # 子
        0x2F29: 0x5C0F,  # 小
        0x2F2D: 0x5C71,  # 山
        0x2F2F: 0x5DE5,  # 工
        0x2F32: 0x5E72,  # 干
        0x2F3C: 0x5FC3,  # 心
        0x2F3D: 0x6208,  # 戈
        0x2F40: 0x652F,  # 支
        0x2F42: 0x6587,  # 文
        0x2F43: 0x6597,  # 斗
        0x2F45: 0x65B9,  # 方
        0x2F47: 0x65E5,  # 日
        0x2F49: 0x6708,  # 月
        0x2F4A: 0x6728,  # 木
        0x2F50: 0x6BD4,  # 比
        0x2F51: 0x6BDB,  # 毛
        0x2F54: 0x6C34,  # 水
        0x2F55: 0x706B,  # 火
        0x2F5B: 0x7259,  # 牙
        0x2F5C: 0x725B,  # 牛
        0x2F60: 0x74DC,  # 瓜
        0x2F61: 0x74E6,  # 瓦
        0x2F63: 0x751F,  # 生
        0x2F65: 0x7530,  # 田
        0x2F69: 0x767D,  # 白
        0x2F6A: 0x76AE,  # 皮
        0x2F6F: 0x77F3,  # 石
        0x2F72: 0x79BE,  # 禾
        0x2F74: 0x7ACB,  # 立
        0x2F76: 0x7C73,  # 米
        0x2F7F: 0x8033,  # 耳
        0x2F82: 0x81E3,  # 臣
        0x2F83: 0x81EA,  # 自
        0x2F84: 0x81F3,  # 至
        0x2F88: 0x8207,  # 舟
        0x2F95: 0x8C37,  # 谷
        0x2F99: 0x8C9D,  # 贝
        0x2F9C: 0x8DB3,  # 足
        0x2F9E: 0x8ECA,  # 车
        0x2F9F: 0x8F9B,  # 辛
        0x2FA5: 0x91CC,  # 里
        0x2FA6: 0x91D1,  # 金
        0x2FA8: 0x9580,  # 門
        0x2FAC: 0x96E8,  # 雨
        0x2FAE: 0x975E,  # 非
        0x2FB0: 0x9769,  # 革
        0x2FB1: 0x97CB,  # 韦
        0x2FB6: 0x98DE,  # 飞
        0x2FB8: 0x9996,  # 首
        0x2FBA: 0x99AC,  # 馬
        0x2FBC: 0x9AD8,  # 高
        0x2FC5: 0x9E7F,  # 鹿
        0x2FC6: 0x9EA6,  # 麦
        0x2FC8: 0x9EC3,  # 黄
        0x2FCA: 0x9ED1,  # 黑
        0x2FD1: 0x9F50,  # 齊
    }
    # CJK Radicals Supplement (U+2E80-U+2EFF)
    cjk_radical_mapping = {
        0x2EA0: 0x6C11,  # 民
        0x2EC6: 0x89D2,  # 角
        0x2EC9: 0x8D1D,  # 贝
        0x2ED1: 0x957F,  # 长
        0x2ED3: 0x957F,  # 长
        0x2ED4: 0x9580,  # 門
        0x2ED9: 0x97E6,  # 韦
        0x2EE2: 0x9A6C,  # 马
        0x2EE8: 0x9EA6,  # 麦
        0x2EEC: 0x9F50,  # 齐
    }

    mapping: dict[str, str] = {}
    for cp, cjk_cp in kangxi_radical_mapping.items():
        mapping[chr(cp)] = chr(cjk_cp)
    for cp, cjk_cp in cjk_radical_mapping.items():
        mapping[chr(cp)] = chr(cjk_cp)

    return mapping


def _convert_chinese_date(date_str: str) -> str | None:
    """将中文日期格式转换为 ISO 格式。

    "2000年2月25日" → "2000-02-25"
    """
    match = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", date_str)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return None


def _normalize_name(name: str) -> str:
    """标准化名称：去重音、去多余空格，保留中文等非 ASCII 字符。"""
    import unicodedata
    # NFKD 分解：将带重音的字符分解为基础字符 + 组合标记
    normalized = unicodedata.normalize("NFKD", name.strip())
    # 移除组合标记（\u0300-\u036f：Combining Diacritical Marks）
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    # 替换各种引号和点为空格
    for ch in "\u2019\u2018'\u00b7\u2022":
        normalized = normalized.replace(ch, " ")
    # 移除其他特殊字符，保留字母、数字、中文、连字符、下划线
    result = re.sub(
        r"[^\w\u4e00-\u9fff\u2e80-\u2eff\u2f00-\u2fdf\u3000-\u303f\uff00-\uffef\-]",
        " ",
        normalized,
        flags=re.UNICODE,
    )
    # 合并空格为下划线
    return re.sub(r"\s+", "_", result).strip("_")[:50]


def _resolve_team_id(name: str) -> str | None:
    """将球队名称映射到 system_team_id。

    支持中文名和英文名。
    """
    # 先检查中文名
    if name in CHINESE_TEAM_MAP:
        return CHINESE_TEAM_MAP[name]
    # 再检查英文名（大小写不敏感）
    normalized = _normalize_name(name).lower()
    if normalized in KNOWN_TEAM_MAP:
        return KNOWN_TEAM_MAP[normalized]
    # 模糊匹配
    for key, team_id in KNOWN_TEAM_MAP.items():
        if key in normalized or normalized in key:
            return team_id
    return None


def _parse_match_lines(text: str) -> list[dict[str, str]]:
    """从文本中解析比赛行。

    格式示例：
    - "2026-06-11 19:00 Mexico vs South Africa Estadio Azteca"
    - "2026-06-11 19:00 Mexico vs South Africa"
    """
    matches: list[dict[str, str]] = []
    lines = text.split("\n")

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # 先按 "vs" 分割
        vs_match = re.match(
            r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(.+?)\s+vs\s+(.+)",
            stripped,
            re.IGNORECASE,
        )
        if not vs_match:
            continue

        date_str = vs_match.group(1)
        time_str = vs_match.group(2)
        home_team = vs_match.group(3).strip()
        away_and_venue = vs_match.group(4).strip()

        # 从 away_and_venue 中分离客队和场馆
        # 尝试匹配已知球队名
        away_team = away_and_venue
        venue = ""
        for team_name in sorted(KNOWN_TEAM_MAP, key=len, reverse=True):
            pattern = re.compile(rf"^{re.escape(team_name)}\s+(.+)", re.IGNORECASE)
            m = pattern.match(away_and_venue)
            if m:
                away_team = team_name.title()
                venue = m.group(1).strip()
                break

        matches.append({
            "raw": stripped,
            "date": date_str,
            "home": home_team,
            "away": away_team,
            "venue": venue,
        })

    return matches
