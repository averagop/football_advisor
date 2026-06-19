"""B-0: 种子数据初始化脚本 — 向 DuckDB 插入世界杯+五大联赛+国家队基础记录。

此脚本是阻塞性前置步骤，Provider Mapping CSV 导入依赖此脚本创建的 dim_league_mapping 和 dim_team_mapping 记录。
"""

import os
import sys
from datetime import datetime

import duckdb

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "football_system.db")

WORLD_CUP_LEAGUES = [
    ("WC_WORLD_CUP_2026", "FIFA World Cup 2026", "International"),
    ("INTL_FRIENDLY", "International Friendly", "International"),
]

TOP5_LEAGUES = [
    ("TOP5_EPL", "England Premier League", "England"),
    ("TOP5_LA_LIGA", "Spain La Liga", "Spain"),
    ("TOP5_BUNDESLIGA", "Germany Bundesliga", "Germany"),
    ("TOP5_SERIE_A", "Italy Serie A", "Italy"),
    ("TOP5_LIGUE_1", "France Ligue 1", "France"),
]

WORLD_CUP_TEAMS = [
    ("WC_TEAM_USA", "USA", "United States", 1780.0),
    ("WC_TEAM_MEX", "Mexico", "Mexico", 1720.0),
    ("WC_TEAM_CAN", "Canada", "Canada", 1650.0),
    ("WC_TEAM_ARG", "Argentina", "Argentina", 1900.0),
    ("WC_TEAM_BRA", "Brazil", "Brazil", 1880.0),
    ("WC_TEAM_FRA", "France", "France", 1860.0),
    ("WC_TEAM_ENG", "England", "England", 1840.0),
    ("WC_TEAM_ESP", "Spain", "Spain", 1830.0),
    ("WC_TEAM_GER", "Germany", "Germany", 1820.0),
    ("WC_TEAM_NED", "Netherlands", "Netherlands", 1800.0),
    ("WC_TEAM_POR", "Portugal", "Portugal", 1790.0),
    ("WC_TEAM_ITA", "Italy", "Italy", 1785.0),
    ("WC_TEAM_BEL", "Belgium", "Belgium", 1770.0),
    ("WC_TEAM_URU", "Uruguay", "Uruguay", 1760.0),
    ("WC_TEAM_CRO", "Croatia", "Croatia", 1755.0),
    ("WC_TEAM_COL", "Colombia", "Colombia", 1740.0),
    ("WC_TEAM_JPN", "Japan", "Japan", 1730.0),
    ("WC_TEAM_MAR", "Morocco", "Morocco", 1725.0),
    ("WC_TEAM_SEN", "Senegal", "Senegal", 1710.0),
    ("WC_TEAM_IRN", "Iran", "Iran", 1700.0),
    ("WC_TEAM_KOR", "South Korea", "South Korea", 1695.0),
    ("WC_TEAM_AUS", "Australia", "Australia", 1685.0),
    ("WC_TEAM_NZL", "New Zealand", "New Zealand", 1620.0),
    ("WC_TEAM_DEN", "Denmark", "Denmark", 1745.0),
    ("WC_TEAM_SUI", "Switzerland", "Switzerland", 1735.0),
    ("WC_TEAM_AUT", "Austria", "Austria", 1715.0),
    ("WC_TEAM_NGA", "Nigeria", "Nigeria", 1680.0),
    ("WC_TEAM_EGY", "Egypt", "Egypt", 1670.0),
    ("WC_TEAM_CIV", "Ivory Coast", "Ivory Coast", 1665.0),
    ("WC_TEAM_GHA", "Ghana", "Ghana", 1655.0),
    ("WC_TEAM_ECU", "Ecuador", "Ecuador", 1675.0),
    ("WC_TEAM_CHI", "Chile", "Chile", 1660.0),
    ("WC_TEAM_RSA", "South Africa", "South Africa", 1660.0),
    ("WC_TEAM_NOR", "Norway", "Norway", 1690.0),
    ("WC_TEAM_JOR", "Jordan", "Jordan", 1580.0),
    ("WC_TEAM_SLO", "Slovenia", "Slovenia", 1640.0),
    ("WC_TEAM_GRE", "Greece", "Greece", 1650.0),
]

WORLD_CUP_FIXTURES = [
    (
        "WC2026_M001",
        "2026",
        "WC_WORLD_CUP_2026",
        datetime(2026, 6, 11, 19, 0, 0),
        "WC_TEAM_MEX",
        "WC_TEAM_RSA",
        "FIFA_OFFICIAL_SCHEDULE_SEED",
    ),
]

TOP5_TEAMS = [
    ("TOP5_ARS", "Arsenal", "England", "TOP5_EPL", None),
    ("TOP5_CHE", "Chelsea", "England", "TOP5_EPL", None),
    ("TOP5_MCI", "Manchester City", "England", "TOP5_EPL", None),
    ("TOP5_LIV", "Liverpool", "England", "TOP5_EPL", None),
    ("TOP5_MUN", "Manchester United", "England", "TOP5_EPL", None),
    ("TOP5_TOT", "Tottenham Hotspur", "England", "TOP5_EPL", None),
    ("TOP5_NEW", "Newcastle United", "England", "TOP5_EPL", None),
    ("TOP5_AVL", "Aston Villa", "England", "TOP5_EPL", None),
    ("TOP5_BHA", "Brighton", "England", "TOP5_EPL", None),
    ("TOP5_WHU", "West Ham United", "England", "TOP5_EPL", None),
    ("TOP5_BAR", "Barcelona", "Spain", "TOP5_LA_LIGA", None),
    ("TOP5_RMA", "Real Madrid", "Spain", "TOP5_LA_LIGA", None),
    ("TOP5_ATM", "Atletico Madrid", "Spain", "TOP5_LA_LIGA", None),
    ("TOP5_SEV", "Sevilla", "Spain", "TOP5_LA_LIGA", None),
    ("TOP5_VAL", "Valencia", "Spain", "TOP5_LA_LIGA", None),
    ("TOP5_ATH", "Athletic Bilbao", "Spain", "TOP5_LA_LIGA", None),
    ("TOP5_RSO", "Real Sociedad", "Spain", "TOP5_LA_LIGA", None),
    ("TOP5_VIL", "Villarreal", "Spain", "TOP5_LA_LIGA", None),
    ("TOP5_BAY", "Bayern Munich", "Germany", "TOP5_BUNDESLIGA", None),
    ("TOP5_BVB", "Borussia Dortmund", "Germany", "TOP5_BUNDESLIGA", None),
    ("TOP5_LEV", "Bayer Leverkusen", "Germany", "TOP5_BUNDESLIGA", None),
    ("TOP5_LEI", "RB Leipzig", "Germany", "TOP5_BUNDESLIGA", None),
    ("TOP5_FRA", "Eintracht Frankfurt", "Germany", "TOP5_BUNDESLIGA", None),
    ("TOP5_STU", "VfB Stuttgart", "Germany", "TOP5_BUNDESLIGA", None),
    ("TOP5_JUV", "Juventus", "Italy", "TOP5_SERIE_A", None),
    ("TOP5_INT", "Inter Milan", "Italy", "TOP5_SERIE_A", None),
    ("TOP5_MIL", "AC Milan", "Italy", "TOP5_SERIE_A", None),
    ("TOP5_NAP", "Napoli", "Italy", "TOP5_SERIE_A", None),
    ("TOP5_ROM", "AS Roma", "Italy", "TOP5_SERIE_A", None),
    ("TOP5_ATA", "Atalanta", "Italy", "TOP5_SERIE_A", None),
    ("TOP5_LAZ", "Lazio", "Italy", "TOP5_SERIE_A", None),
    ("TOP5_PSG", "Paris Saint-Germain", "France", "TOP5_LIGUE_1", None),
    ("TOP5_MON", "AS Monaco", "France", "TOP5_LIGUE_1", None),
    ("TOP5_MAR", "Olympique Marseille", "France", "TOP5_LIGUE_1", None),
    ("TOP5_LYO", "Olympique Lyon", "France", "TOP5_LIGUE_1", None),
    ("TOP5_LIL", "LOSC Lille", "France", "TOP5_LIGUE_1", None),
    ("TOP5_REN", "Stade Rennais", "France", "TOP5_LIGUE_1", None),
    ("TOP5_NIC", "OGC Nice", "France", "TOP5_LIGUE_1", None),
]


def seed_database():
    if not os.path.exists(DB_PATH):
        print(f"数据库文件不存在: {DB_PATH}")
        sys.exit(1)

    conn = duckdb.connect(DB_PATH)

    league_inserted = 0
    team_inserted = 0

    for league_id, name, country in WORLD_CUP_LEAGUES + TOP5_LEAGUES:
        existing = conn.execute(
            "SELECT 1 FROM core.dim_league_mapping WHERE system_league_id = ?",
            [league_id],
        ).fetchone()
        if existing:
            continue
        conn.execute(
            "INSERT INTO core.dim_league_mapping (system_league_id, league_standard_name, country) VALUES (?, ?, ?)",
            [league_id, name, country],
        )
        league_inserted += 1

    print(f"联赛: 插入 {league_inserted} 条")

    for team_id, name, country, elo in WORLD_CUP_TEAMS:
        existing = conn.execute(
            "SELECT 1 FROM core.dim_team_mapping WHERE system_team_id = ?",
            [team_id],
        ).fetchone()
        if existing:
            continue
        conn.execute(
            "INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name, country, elo_rating_base) VALUES (?, ?, ?, ?)",
            [team_id, name, country, elo],
        )
        team_inserted += 1

    for team_id, name, country, _league_id, _elo in TOP5_TEAMS:
        existing = conn.execute(
            "SELECT 1 FROM core.dim_team_mapping WHERE system_team_id = ?",
            [team_id],
        ).fetchone()
        if existing:
            continue
        conn.execute(
            "INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name, country, elo_rating_base) VALUES (?, ?, ?, ?)",
            [team_id, name, country, 1500.0],
        )
        team_inserted += 1

    print(f"球队: 插入 {team_inserted} 条")

    fixture_inserted = 0
    for (
        match_id,
        season,
        league_id,
        match_time,
        home_team_id,
        away_team_id,
        source_provider,
    ) in WORLD_CUP_FIXTURES:
        existing = conn.execute(
            "SELECT 1 FROM core.fact_match_schedule WHERE match_id = ?",
            [match_id],
        ).fetchone()
        if existing:
            continue
        conn.execute(
            """
            INSERT INTO core.fact_match_schedule (
                match_id,
                season,
                system_league_id,
                match_time,
                home_team_id,
                away_team_id,
                status,
                source_provider
            ) VALUES (?, ?, ?, ?, ?, ?, 'PRE-MATCH', ?)
            """,
            [
                match_id,
                season,
                league_id,
                match_time,
                home_team_id,
                away_team_id,
                source_provider,
            ],
        )
        fixture_inserted += 1

    print(f"赛程: 插入 {fixture_inserted} 条")

    total_leagues = conn.execute("SELECT COUNT(*) FROM core.dim_league_mapping").fetchone()[0]
    total_teams = conn.execute("SELECT COUNT(*) FROM core.dim_team_mapping").fetchone()[0]
    total_fixtures = conn.execute("SELECT COUNT(*) FROM core.fact_match_schedule").fetchone()[0]
    print(f"总计: 联赛 {total_leagues} 条, 球队 {total_teams} 条, 赛程 {total_fixtures} 条")

    conn.close()
    print("B-0 种子数据初始化完成")


if __name__ == "__main__":
    seed_database()
