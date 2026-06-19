from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

CURRENT_SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS staging;

CREATE TABLE IF NOT EXISTS core.schema_version (
    version INTEGER NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    migration_name VARCHAR NOT NULL,
    PRIMARY KEY (version),
    CHECK (version >= 1)
);

CREATE TABLE IF NOT EXISTS core.dim_league_mapping (
    system_league_id VARCHAR PRIMARY KEY,
    league_standard_name VARCHAR NOT NULL,
    country VARCHAR,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS core.dim_team_mapping (
    system_team_id VARCHAR PRIMARY KEY,
    team_standard_name VARCHAR NOT NULL,
    country VARCHAR,
    key_wikidata VARCHAR,
    key_opta VARCHAR,
    key_sofascore INT,
    key_understat VARCHAR,
    key_api_football INT,
    elo_rating_base DOUBLE DEFAULT 1500.0,
    stadium_name VARCHAR,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS core.dim_provider_league_mapping (
    provider_name VARCHAR NOT NULL,
    provider_league_id VARCHAR NOT NULL,
    system_league_id VARCHAR NOT NULL,
    provider_league_name VARCHAR,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (provider_name, provider_league_id)
);

CREATE TABLE IF NOT EXISTS core.dim_provider_team_mapping (
    provider_name VARCHAR NOT NULL,
    provider_team_id VARCHAR NOT NULL,
    system_team_id VARCHAR NOT NULL,
    provider_team_name VARCHAR,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (provider_name, provider_team_id)
);

CREATE TABLE IF NOT EXISTS core.dim_provider_match_mapping (
    provider_name VARCHAR NOT NULL,
    provider_match_id VARCHAR NOT NULL,
    system_match_id VARCHAR NOT NULL,
    resolution_method VARCHAR NOT NULL,
    resolution_confidence DOUBLE NOT NULL,
    verified_at TIMESTAMP NOT NULL,
    PRIMARY KEY (provider_name, provider_match_id)
);

CREATE TABLE IF NOT EXISTS core.quarantine_unmapped_records (
    quarantine_id BIGINT,
    provider_name VARCHAR NOT NULL,
    provider_match_id VARCHAR,
    provider_league_id VARCHAR,
    provider_home_team_id VARCHAR,
    provider_away_team_id VARCHAR,
    match_time TIMESTAMP,
    error_reason VARCHAR NOT NULL,
    component VARCHAR NOT NULL,
    quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP,
    resolution_notes VARCHAR
);

CREATE TABLE IF NOT EXISTS core.dim_player_mapping (
    system_player_id VARCHAR PRIMARY KEY,
    player_standard_name VARCHAR NOT NULL,
    team_id VARCHAR,
    primary_position VARCHAR,
    key_wikidata VARCHAR,
    key_opta VARCHAR,
    key_sofascore INT,
    key_understat INT,
    key_fbref VARCHAR,
    key_api_football INT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS core.dim_referee_profile (
    referee_id VARCHAR PRIMARY KEY,
    referee_name VARCHAR NOT NULL,
    avg_fouls_called DOUBLE,
    penalty_rate DOUBLE,
    red_card_rate DOUBLE,
    home_bias_factor DOUBLE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS core.fact_match_schedule (
    match_id VARCHAR PRIMARY KEY,
    season VARCHAR,
    system_league_id VARCHAR NOT NULL,
    match_time TIMESTAMP NOT NULL,
    home_team_id VARCHAR NOT NULL,
    away_team_id VARCHAR NOT NULL,
    referee_id VARCHAR,
    status VARCHAR DEFAULT 'PRE-MATCH',
    weather_condition VARCHAR,
    temperature DOUBLE,
    is_neutral_venue BOOLEAN DEFAULT FALSE,
    rest_days_home INT,
    rest_days_away INT,
    home_score INT,
    away_score INT,
    source_provider VARCHAR,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    etl_insert_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    _change_request_type VARCHAR DEFAULT 'INSERT'
);

CREATE TABLE IF NOT EXISTS core.fact_team_rolling_stats (
    match_id VARCHAR NOT NULL,
    team_id VARCHAR NOT NULL,
    record_date TIMESTAMP NOT NULL,
    source_provider VARCHAR NOT NULL,
    rolling_goals_for DOUBLE,
    rolling_goals_against DOUBLE,
    rolling_xg_for DOUBLE,
    rolling_npxg_for DOUBLE,
    rolling_xg_against DOUBLE,
    rolling_psxg_diff DOUBLE,
    ppda_intensity DOUBLE,
    transition_threat_xt DOUBLE,
    recent_points_per_match DOUBLE,
    attack_strength DOUBLE,
    defense_strength DOUBLE,
    motivation_coefficient DOUBLE,
    key_missing_weight DOUBLE,
    data_quality_flag VARCHAR,
    etl_insert_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (match_id, team_id, record_date, source_provider)
);

CREATE TABLE IF NOT EXISTS core.fact_odds_capital_flow (
    match_id VARCHAR NOT NULL,
    snapshot_time TIMESTAMP NOT NULL,
    time_to_kickoff INT,
    odds_type VARCHAR NOT NULL,
    bookmaker_name VARCHAR NOT NULL,
    handicap_line DOUBLE,
    home_odds DOUBLE,
    draw_odds DOUBLE,
    away_odds DOUBLE,
    implied_home_probability DOUBLE,
    implied_draw_probability DOUBLE,
    implied_away_probability DOUBLE,
    sharp_money_ratio DOUBLE,
    retail_ticket_ratio DOUBLE,
    matched_volume DOUBLE,
    line_movement_vol DOUBLE,
    source_provider VARCHAR,
    etl_insert_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (match_id, snapshot_time, odds_type, bookmaker_name)
);

CREATE TABLE IF NOT EXISTS core.fact_match_context_summary (
    match_id VARCHAR PRIMARY KEY,
    h2h_home_wins INT,
    h2h_draws INT,
    h2h_away_wins INT,
    home_recent_wins INT,
    home_recent_draws INT,
    home_recent_losses INT,
    away_recent_wins INT,
    away_recent_draws INT,
    away_recent_losses INT,
    home_home_points_per_match DOUBLE,
    away_away_points_per_match DOUBLE,
    home_key_absences INT,
    away_key_absences INT,
    home_lineup_confirmed BOOLEAN,
    away_lineup_confirmed BOOLEAN,
    injury_data_updated_at TIMESTAMP,
    source_provider VARCHAR,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS core.fact_news_signal_summary (
    match_id VARCHAR PRIMARY KEY,
    news_sentiment_score DOUBLE,
    news_risk_flag BOOLEAN,
    news_source_count INT,
    news_last_updated_at TIMESTAMP,
    source_provider VARCHAR,
    generated_by VARCHAR,
    source_text_hash VARCHAR,
    confidence DOUBLE,
    requires_cross_check BOOLEAN,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS core.fact_match_events_unified (
    event_id VARCHAR PRIMARY KEY,
    match_id VARCHAR NOT NULL,
    team_id VARCHAR NOT NULL,
    player_id VARCHAR,
    timestamp_ms INT NOT NULL,
    period INT NOT NULL,
    event_type VARCHAR NOT NULL,
    start_x DOUBLE,
    start_y DOUBLE,
    end_x DOUBLE,
    end_y DOUBLE,
    is_successful BOOLEAN,
    event_value DOUBLE,
    source_provider VARCHAR NOT NULL,
    etl_insert_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS staging.stg_match_schedule (
    provider_match_id VARCHAR,
    provider_league_id VARCHAR,
    provider_home_team_id VARCHAR,
    provider_away_team_id VARCHAR,
    match_time TIMESTAMP,
    status VARCHAR,
    home_score INT,
    away_score INT,
    source_provider VARCHAR,
    etl_insert_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS staging.stg_team_stats (
    provider_match_id VARCHAR,
    provider_team_id VARCHAR,
    goals_for DOUBLE,
    goals_against DOUBLE,
    xg_for DOUBLE,
    xg_against DOUBLE,
    source_provider VARCHAR,
    etl_insert_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS staging.stg_odds (
    provider_match_id VARCHAR,
    odds_type VARCHAR,
    bookmaker_name VARCHAR,
    snapshot_time TIMESTAMP,
    handicap_line DOUBLE,
    home_odds DOUBLE,
    draw_odds DOUBLE,
    away_odds DOUBLE,
    source_provider VARCHAR,
    etl_insert_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 竞彩特殊赔率: 比分/总进球/半全场 (非三路赔率结构)
CREATE TABLE IF NOT EXISTS staging.stg_sporttery_odds_detail (
    provider_match_id VARCHAR,
    odds_type VARCHAR,          -- 'CORRECT_SCORE', 'TOTAL_GOALS', 'HALF_FULL'
    score_key VARCHAR,          -- '1:0', '3', '胜胜' 等
    odds_value DOUBLE,
    snapshot_time TIMESTAMP,
    source_provider VARCHAR,
    etl_insert_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS core.fact_sporttery_odds_detail (
    match_id VARCHAR NOT NULL,
    odds_type VARCHAR NOT NULL,
    outcome_key VARCHAR NOT NULL,
    odds_value DOUBLE NOT NULL,
    snapshot_time TIMESTAMP NOT NULL,
    source_provider VARCHAR NOT NULL,
    PRIMARY KEY (match_id, odds_type, outcome_key, snapshot_time)
);

CREATE TABLE IF NOT EXISTS core.fact_sporttery_market_status (
    match_id VARCHAR NOT NULL,
    market_type VARCHAR NOT NULL,
    sale_status VARCHAR NOT NULL CHECK (
        sale_status IN ('OPEN', 'NOT_ON_SALE', 'MAPPING_MISSING', 'SYNC_FAILED')
    ),
    snapshot_time TIMESTAMP NOT NULL,
    source_provider VARCHAR NOT NULL,
    PRIMARY KEY (match_id, market_type, snapshot_time)
);

CREATE TABLE IF NOT EXISTS core.fact_target_sync_state (
    match_id VARCHAR NOT NULL,
    component VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    last_attempt_at TIMESTAMP,
    last_success_at TIMESTAMP,
    source_updated_at TIMESTAMP,
    status VARCHAR NOT NULL,
    row_count INT DEFAULT 0,
    empty_confirmed BOOLEAN DEFAULT FALSE,
    error_code VARCHAR,
    PRIMARY KEY (match_id, component, provider)
);

CREATE TABLE IF NOT EXISTS staging.stg_player_squad (
    team_name VARCHAR,
    player_name VARCHAR,
    position VARCHAR,
    thumbnail_url VARCHAR,
    source_provider VARCHAR,
    etl_insert_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS staging.stg_injuries (
    provider_match_id VARCHAR,
    player_name VARCHAR,
    team_name VARCHAR,
    injury_type VARCHAR,
    reason VARCHAR,
    source_provider VARCHAR,
    etl_insert_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS staging.stg_lineups (
    provider_match_id VARCHAR,
    player_name VARCHAR,
    team_name VARCHAR,
    role VARCHAR,
    formation VARCHAR,
    source_provider VARCHAR,
    etl_insert_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- PDF 候选 staging 表：不经任何清洗直接落库，保留原始证据
CREATE TABLE IF NOT EXISTS staging.stg_referee_candidates (
    source_pdf VARCHAR NOT NULL,
    page_number INT NOT NULL,
    raw_text VARCHAR NOT NULL,
    referee_name VARCHAR,
    country VARCHAR,
    role VARCHAR,
    entity_mapping_status VARCHAR DEFAULT 'PENDING',
    cross_validation_status VARCHAR DEFAULT 'PENDING',
    quality_flag VARCHAR DEFAULT 'PDF_WIKIPEDIA_UNVERIFIED',
    etl_insert_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS staging.stg_player_candidates (
    source_pdf VARCHAR NOT NULL,
    page_number INT NOT NULL,
    raw_text VARCHAR NOT NULL,
    player_name VARCHAR,
    position VARCHAR,
    birth_date VARCHAR,
    caps INT,
    goals INT,
    club VARCHAR,
    national_team VARCHAR,
    system_team_id VARCHAR,
    system_player_id VARCHAR,
    entity_mapping_status VARCHAR DEFAULT 'PENDING',
    cross_validation_status VARCHAR DEFAULT 'PENDING',
    quality_flag VARCHAR DEFAULT 'PDF_WIKIPEDIA_UNVERIFIED',
    etl_insert_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS staging.stg_schedule_candidates (
    source_pdf VARCHAR NOT NULL,
    page_number INT NOT NULL,
    raw_text VARCHAR NOT NULL,
    home_team_name VARCHAR,
    away_team_name VARCHAR,
    match_date VARCHAR,
    venue VARCHAR,
    match_id VARCHAR,
    entity_mapping_status VARCHAR DEFAULT 'PENDING',
    cross_validation_status VARCHAR DEFAULT 'PENDING',
    quality_flag VARCHAR DEFAULT 'PDF_WIKIPEDIA_UNVERIFIED',
    etl_insert_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS core.fact_team_elo_history (
    team_id VARCHAR NOT NULL,
    match_id VARCHAR NOT NULL,
    record_date TIMESTAMP NOT NULL,
    elo_rating_before DOUBLE,
    elo_rating_after DOUBLE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (team_id, match_id)
);

CREATE INDEX IF NOT EXISTS idx_schedule_time_teams
    ON core.fact_match_schedule (match_time, home_team_id, away_team_id);
CREATE INDEX IF NOT EXISTS idx_rolling_lookup
    ON core.fact_team_rolling_stats (match_id, team_id, record_date);
CREATE INDEX IF NOT EXISTS idx_odds_lookup
    ON core.fact_odds_capital_flow (match_id, odds_type, snapshot_time);

CREATE OR REPLACE VIEW core.view_match_feature_base AS
WITH ranked_team_stats AS (
    SELECT
        r.*,
        ROW_NUMBER() OVER (
            PARTITION BY r.match_id, r.team_id
            ORDER BY r.record_date DESC, r.etl_insert_timestamp DESC
        ) AS rn
    FROM core.fact_team_rolling_stats r
    JOIN core.fact_match_schedule s ON r.match_id = s.match_id
    WHERE r.record_date <= s.match_time - INTERVAL 30 MINUTE
),
ranked_team_elo AS (
    SELECT
        e.team_id,
        s.match_id,
        e.elo_rating_after,
        ROW_NUMBER() OVER (
            PARTITION BY s.match_id, e.team_id
            ORDER BY e.record_date DESC, e.updated_at DESC
        ) AS rn
    FROM core.fact_team_elo_history e
    JOIN core.fact_match_schedule s ON e.team_id IN (s.home_team_id, s.away_team_id)
    WHERE e.record_date < s.match_time
),
home_stats AS (
    SELECT r.*
    FROM ranked_team_stats r
    JOIN core.fact_match_schedule s ON r.match_id = s.match_id AND r.team_id = s.home_team_id
    WHERE r.rn = 1
),
away_stats AS (
    SELECT r.*
    FROM ranked_team_stats r
    JOIN core.fact_match_schedule s ON r.match_id = s.match_id AND r.team_id = s.away_team_id
    WHERE r.rn = 1
),
home_elo_hist AS (
    SELECT e.*
    FROM ranked_team_elo e
    JOIN core.fact_match_schedule s ON e.match_id = s.match_id AND e.team_id = s.home_team_id
    WHERE e.rn = 1
),
away_elo_hist AS (
    SELECT e.*
    FROM ranked_team_elo e
    JOIN core.fact_match_schedule s ON e.match_id = s.match_id AND e.team_id = s.away_team_id
    WHERE e.rn = 1
)
SELECT
    s.match_id,
    s.is_neutral_venue,
    s.match_time,
    s.system_league_id,
    l.league_standard_name,
    s.status,
    s.home_team_id AS home_team_id,
    s.away_team_id AS away_team_id,
    COALESCE(home.team_standard_name, s.home_team_id) AS home_team_name,
    COALESCE(away.team_standard_name, s.away_team_id) AS away_team_name,
    COALESCE(heh.elo_rating_after, home.elo_rating_base, 1500.0) AS home_elo,
    COALESCE(aeh.elo_rating_after, away.elo_rating_base, 1500.0) AS away_elo,
    COALESCE(h.attack_strength, 1.0) AS home_attack_strength,
    COALESCE(a.attack_strength, 1.0) AS away_attack_strength,
    COALESCE(h.defense_strength, 1.0) AS home_defense_strength,
    COALESCE(a.defense_strength, 1.0) AS away_defense_strength,
    COALESCE(h.recent_points_per_match, 1.4) AS home_recent_points_per_match,
    COALESCE(a.recent_points_per_match, 1.4) AS away_recent_points_per_match,
    COALESCE(h.key_missing_weight, 0.0) AS home_injury_penalty,
    COALESCE(a.key_missing_weight, 0.0) AS away_injury_penalty,
    COALESCE(h.rolling_xg_for, h.rolling_goals_for, 1.25) AS home_rolling_xg_for,
    COALESCE(a.rolling_xg_for, a.rolling_goals_for, 1.10) AS away_rolling_xg_for,
    COALESCE(h.rolling_xg_against, h.rolling_goals_against, 1.10) AS home_rolling_xg_against,
    COALESCE(a.rolling_xg_against, a.rolling_goals_against, 1.15) AS away_rolling_xg_against,
    COALESCE(h.ppda_intensity, 11.5) AS home_ppda,
    COALESCE(a.ppda_intensity, 11.5) AS away_ppda,
    COALESCE(h.motivation_coefficient, 0.5) AS home_motivation,
    COALESCE(a.motivation_coefficient, 0.5) AS away_motivation,
    COALESCE(s.rest_days_home - s.rest_days_away, 0) AS rest_days_diff,
    COALESCE(s.weather_condition, 'UNKNOWN') AS weather_condition,
    COALESCE(s.temperature, 15.0) AS temperature,
    COALESCE(ctx.h2h_home_wins, 0) AS h2h_home_wins,
    COALESCE(ctx.h2h_draws, 0) AS h2h_draws,
    COALESCE(ctx.h2h_away_wins, 0) AS h2h_away_wins,
    COALESCE(ctx.home_home_points_per_match, h.recent_points_per_match, 1.4) AS home_home_points_per_match,
    COALESCE(ctx.away_away_points_per_match, a.recent_points_per_match, 1.4) AS away_away_points_per_match,
    COALESCE(ctx.home_key_absences, 0) AS home_key_absences,
    COALESCE(ctx.away_key_absences, 0) AS away_key_absences,
    COALESCE(ref.referee_name, 'TBA') AS referee_name,
    COALESCE(ref.red_card_rate, 0.05) AS ref_red_card_probability,
    COALESCE(ref.home_bias_factor, 0.0) AS ref_home_bias_factor,
    COALESCE(
        LEAST(
            GREATEST(
                COALESCE(s.etl_insert_timestamp, TIMESTAMP '1970-01-01'),
                COALESCE(s.updated_at, TIMESTAMP '1970-01-01')
            ),
            COALESCE(h.etl_insert_timestamp, h.record_date),
            COALESCE(a.etl_insert_timestamp, a.record_date),
            ctx.updated_at
        ),
        TIMESTAMP '1970-01-01'
    ) AS structured_updated_at,
    CONCAT_WS(
        ',',
        s.source_provider,
        h.source_provider,
        a.source_provider,
        ctx.source_provider
    ) AS structured_sources,
    h.match_id IS NULL OR a.match_id IS NULL AS missing_team_stats_flag,
    ctx.match_id IS NULL AS missing_context_flag,
    h.data_quality_flag AS home_team_stats_quality_flag,
    a.data_quality_flag AS away_team_stats_quality_flag
FROM core.fact_match_schedule s
LEFT JOIN core.dim_league_mapping l ON s.system_league_id = l.system_league_id
LEFT JOIN core.dim_team_mapping home ON s.home_team_id = home.system_team_id
LEFT JOIN core.dim_team_mapping away ON s.away_team_id = away.system_team_id
LEFT JOIN home_stats h ON s.match_id = h.match_id
LEFT JOIN away_stats a ON s.match_id = a.match_id
LEFT JOIN home_elo_hist heh ON s.match_id = heh.match_id
LEFT JOIN away_elo_hist aeh ON s.match_id = aeh.match_id
LEFT JOIN core.fact_match_context_summary ctx ON s.match_id = ctx.match_id
LEFT JOIN core.dim_referee_profile ref ON s.referee_id = ref.referee_id;

CREATE OR REPLACE VIEW core.view_market_feature_base AS
WITH odds_ranked AS (
    SELECT
        o.*,
        ROW_NUMBER() OVER (
            PARTITION BY o.match_id, o.odds_type
            ORDER BY o.snapshot_time DESC, o.etl_insert_timestamp DESC
        ) AS latest_rn,
        ROW_NUMBER() OVER (
            PARTITION BY o.match_id, o.odds_type
            ORDER BY o.snapshot_time ASC, o.etl_insert_timestamp ASC
        ) AS opening_rn
    FROM core.fact_odds_capital_flow o
),
-- 竞彩官方 SPF/RQSPF 赔率（独立排序，不与其他 provider 混合）
sporttery_ranked AS (
    SELECT
        o.*,
        ROW_NUMBER() OVER (
            PARTITION BY o.match_id, o.odds_type
            ORDER BY o.snapshot_time DESC, o.etl_insert_timestamp DESC
        ) AS latest_rn,
        ROW_NUMBER() OVER (
            PARTITION BY o.match_id, o.odds_type
            ORDER BY o.snapshot_time ASC, o.etl_insert_timestamp ASC
        ) AS opening_rn
    FROM core.fact_odds_capital_flow o
    WHERE o.source_provider = 'SportteryOfficialWeb'
),
latest_1x2 AS (
    SELECT * FROM sporttery_ranked
    WHERE odds_type = '1X2' AND latest_rn = 1
),
opening_1x2 AS (
    SELECT * FROM sporttery_ranked
    WHERE odds_type = '1X2' AND opening_rn = 1
),
latest_rqspf AS (
    SELECT * FROM sporttery_ranked
    WHERE odds_type = 'SPORTTERY_RQSPF' AND latest_rn = 1
),
-- 第三方 1X2 交叉校验（独立排序，仅非竞彩 provider）
cross_ranked_1x2 AS (
    SELECT
        o.*,
        ROW_NUMBER() OVER (
            PARTITION BY o.match_id, o.odds_type
            ORDER BY o.snapshot_time DESC, o.etl_insert_timestamp DESC
        ) AS latest_rn
    FROM core.fact_odds_capital_flow o
    WHERE o.source_provider != 'SportteryOfficialWeb'
      AND o.odds_type = '1X2'
),
cross_check_1x2 AS (
    SELECT * FROM cross_ranked_1x2 WHERE latest_rn = 1
),
latest_totals AS (
    SELECT * FROM odds_ranked WHERE odds_type = 'TOTAL_GOALS' AND latest_rn = 1
),
latest_handicap AS (
    SELECT * FROM odds_ranked WHERE odds_type = 'ASIAN_HANDICAP' AND latest_rn = 1
)
SELECT
    s.match_id,
    s.is_neutral_venue,
    l.home_odds AS latest_home_odds,
    l.draw_odds AS latest_draw_odds,
    l.away_odds AS latest_away_odds,
    rq.handicap_line AS rqspf_handicap_line,
    rq.home_odds AS latest_rqspf_home_odds,
    rq.draw_odds AS latest_rqspf_draw_odds,
    rq.away_odds AS latest_rqspf_away_odds,
    l.implied_home_probability,
    l.implied_draw_probability,
    l.implied_away_probability,
    o.home_odds AS opening_home_odds,
    o.draw_odds AS opening_draw_odds,
    o.away_odds AS opening_away_odds,
    l.home_odds - o.home_odds AS home_odds_movement,
    l.draw_odds - o.draw_odds AS draw_odds_movement,
    l.away_odds - o.away_odds AS away_odds_movement,
    -- 第三方交叉校验赔率（仅进入风险上下文，不进入竞彩价值计算）
    cc.home_odds AS cross_check_home_odds,
    cc.draw_odds AS cross_check_draw_odds,
    cc.away_odds AS cross_check_away_odds,
    cc.source_provider AS cross_check_source_provider,
    cc.snapshot_time AS cross_check_updated_at,
    COALESCE(t.handicap_line, 2.5) AS totals_line,
    h.handicap_line AS asian_handicap_line,
    COALESCE(l.sharp_money_ratio, h.sharp_money_ratio) AS sharp_money_ratio,
    COALESCE(l.retail_ticket_ratio, h.retail_ticket_ratio) AS retail_ticket_ratio,
    COALESCE(l.matched_volume, h.matched_volume) AS matched_volume,
    COALESCE(l.line_movement_vol, h.line_movement_vol, 0.0) AS line_movement_vol,
    l.bookmaker_name AS latest_1x2_bookmaker,
    CASE
        WHEN l.snapshot_time IS NULL
             AND t.snapshot_time IS NULL
             AND h.snapshot_time IS NULL
             AND rq.snapshot_time IS NULL
        THEN NULL
        ELSE GREATEST(
            COALESCE(l.etl_insert_timestamp, l.snapshot_time, TIMESTAMP '1970-01-01'),
            COALESCE(t.etl_insert_timestamp, t.snapshot_time, TIMESTAMP '1970-01-01'),
            COALESCE(h.etl_insert_timestamp, h.snapshot_time, TIMESTAMP '1970-01-01'),
            COALESCE(rq.etl_insert_timestamp, rq.snapshot_time, TIMESTAMP '1970-01-01')
        )
    END AS market_updated_at,
    l.match_id IS NULL AS missing_1x2_odds_flag,
    t.match_id IS NULL AS missing_totals_odds_flag,
    h.match_id IS NULL AS missing_handicap_odds_flag,
    rq.match_id IS NULL AS missing_rqspf_odds_flag
FROM core.fact_match_schedule s
LEFT JOIN latest_1x2 l ON s.match_id = l.match_id
LEFT JOIN opening_1x2 o ON s.match_id = o.match_id
LEFT JOIN cross_check_1x2 cc ON s.match_id = cc.match_id
LEFT JOIN latest_totals t ON s.match_id = t.match_id
LEFT JOIN latest_handicap h ON s.match_id = h.match_id
LEFT JOIN latest_rqspf rq ON s.match_id = rq.match_id;

CREATE OR REPLACE VIEW core.view_llm_match_prediction_base AS
SELECT
    mf.match_id,
    mf.is_neutral_venue,
    mf.match_time,
    mf.system_league_id,
    mf.league_standard_name,
    mf.status,
    mf.home_team_id,
    mf.away_team_id,
    mf.home_team_name,
    mf.away_team_name,
    mf.home_elo,
    mf.away_elo,
    mf.home_attack_strength,
    mf.away_attack_strength,
    mf.home_defense_strength,
    mf.away_defense_strength,
    mf.home_recent_points_per_match,
    mf.away_recent_points_per_match,
    mf.home_injury_penalty,
    mf.away_injury_penalty,
    mf.home_rolling_xg_for,
    mf.away_rolling_xg_for,
    mf.home_rolling_xg_against,
    mf.away_rolling_xg_against,
    mf.home_ppda,
    mf.away_ppda,
    mf.home_motivation,
    mf.away_motivation,
    mf.rest_days_diff,
    mf.weather_condition,
    mf.temperature,
    mf.h2h_home_wins,
    mf.h2h_draws,
    mf.h2h_away_wins,
    mf.home_home_points_per_match,
    mf.away_away_points_per_match,
    mf.home_key_absences,
    mf.away_key_absences,
    mf.home_team_stats_quality_flag,
    mf.away_team_stats_quality_flag,
    mf.referee_name,
    mf.ref_red_card_probability,
    mf.ref_home_bias_factor,
    market.latest_home_odds,
    market.latest_draw_odds,
    market.latest_away_odds,
    market.rqspf_handicap_line,
    market.latest_rqspf_home_odds,
    market.latest_rqspf_draw_odds,
    market.latest_rqspf_away_odds,
    market.implied_home_probability,
    market.implied_draw_probability,
    market.implied_away_probability,
    market.opening_home_odds,
    market.opening_draw_odds,
    market.opening_away_odds,
    market.home_odds_movement,
    market.draw_odds_movement,
    market.away_odds_movement,
    market.totals_line,
    market.asian_handicap_line,
    market.sharp_money_ratio,
    market.retail_ticket_ratio,
    market.matched_volume,
    market.line_movement_vol,
    COALESCE(news.news_sentiment_score, 0.0) AS news_sentiment_score,
    COALESCE(news.news_risk_flag, FALSE) AS news_risk_flag,
    COALESCE(news.news_source_count, 0) AS news_source_count,
    news.news_last_updated_at,
    COALESCE(LEAST(mf.structured_updated_at, market.market_updated_at), TIMESTAMP '1970-01-01') AS data_updated_at,
    date_diff(
        'minute',
        COALESCE(LEAST(mf.structured_updated_at, market.market_updated_at), TIMESTAMP '1970-01-01'),
        CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
    ) AS critical_data_age_minutes,
    CONCAT_WS(
        ',',
        mf.structured_sources,
        market.latest_1x2_bookmaker,
        news.source_provider
    ) AS data_sources,
    mf.missing_team_stats_flag,
    mf.missing_context_flag,
    COALESCE(mf.home_team_stats_quality_flag IN (
        'CSV_PRIOR_MATCHES_ONLY',
        'SQLITE_PRIOR_MATCHES_ONLY',
        'WORLD_CUP_LAST5_SNAPSHOT'
    ), FALSE)
        OR COALESCE(mf.away_team_stats_quality_flag IN (
            'CSV_PRIOR_MATCHES_ONLY',
            'SQLITE_PRIOR_MATCHES_ONLY',
            'WORLD_CUP_LAST5_SNAPSHOT'
        ), FALSE)
        AS derived_team_stats_quality_flag,
    market.missing_1x2_odds_flag,
    market.missing_totals_odds_flag,
    market.missing_handicap_odds_flag,
    market.missing_rqspf_odds_flag,
    news.match_id IS NULL OR COALESCE(news.news_source_count, 0) <= 0 AS missing_news_signal_flag,
    CASE
        WHEN COALESCE(market.retail_ticket_ratio, 0.0) >= 0.85
             AND COALESCE(market.sharp_money_ratio, 0.0) < 0.25
             AND COALESCE(market.line_movement_vol, 0.0) < 0.05
        THEN TRUE
        ELSE FALSE
    END AS anti_consensus_trap_flag,
    CASE
        WHEN COALESCE(mf.ref_red_card_probability, 0.0) >= 0.15 THEN TRUE
        ELSE FALSE
    END AS high_variance_referee_warning,
    CASE
        WHEN mf.missing_team_stats_flag
             OR COALESCE(mf.home_team_stats_quality_flag IN (
                 'CSV_PRIOR_MATCHES_ONLY',
                 'SQLITE_PRIOR_MATCHES_ONLY',
                 'WORLD_CUP_LAST5_SNAPSHOT'
             ), FALSE)
             OR COALESCE(mf.away_team_stats_quality_flag IN (
                 'CSV_PRIOR_MATCHES_ONLY',
                 'SQLITE_PRIOR_MATCHES_ONLY',
                 'WORLD_CUP_LAST5_SNAPSHOT'
             ), FALSE)
             OR market.missing_1x2_odds_flag
             OR news.match_id IS NULL
             OR COALESCE(news.news_source_count, 0) <= 0
             OR date_diff(
                 'minute',
                 COALESCE(news.news_last_updated_at, TIMESTAMP '1970-01-01'),
                 CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
             ) > 30
             OR date_diff(
                 'minute',
                 COALESCE(LEAST(mf.structured_updated_at, market.market_updated_at), TIMESTAMP '1970-01-01'),
                 CAST(CURRENT_TIMESTAMP AS TIMESTAMP)
             ) > 30
        THEN TRUE
        ELSE FALSE
    END AS no_bet_data_quality_flag
FROM core.view_match_feature_base mf
LEFT JOIN core.view_market_feature_base market ON mf.match_id = market.match_id
LEFT JOIN core.fact_news_signal_summary news ON mf.match_id = news.match_id;

CREATE TABLE IF NOT EXISTS core.dim_collection_state (
    collection_type VARCHAR PRIMARY KEY,
    last_collected_date DATE NOT NULL,
    last_success_at TIMESTAMP,
    cursor_value VARCHAR,
    status VARCHAR DEFAULT 'IDLE',
    failure_count INT DEFAULT 0,
    last_error VARCHAR,
    next_due_at TIMESTAMP,
    started_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS staging.stg_odds_snapshot (
    snapshot_id VARCHAR PRIMARY KEY,
    match_id VARCHAR NOT NULL,
    provider_name VARCHAR NOT NULL,
    snapshot_date DATE NOT NULL,
    market_type VARCHAR NOT NULL DEFAULT '1X2',
    home_odds DOUBLE,
    draw_odds DOUBLE,
    away_odds DOUBLE,
    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(match_id, provider_name, snapshot_date, market_type)
);

CREATE TABLE IF NOT EXISTS core.fact_league_standings (
    standing_id VARCHAR PRIMARY KEY,
    system_league_id VARCHAR NOT NULL,
    system_team_id VARCHAR NOT NULL,
    season INTEGER NOT NULL,
    rank_position INTEGER,
    played INTEGER,
    wins INTEGER,
    draws INTEGER,
    loses INTEGER,
    goals_for INTEGER,
    goals_against INTEGER,
    goals_diff INTEGER,
    points INTEGER,
    form VARCHAR,
    description VARCHAR,
    home_played INTEGER,
    home_wins INTEGER,
    home_draws INTEGER,
    home_loses INTEGER,
    home_goals_for INTEGER,
    home_goals_against INTEGER,
    away_played INTEGER,
    away_wins INTEGER,
    away_draws INTEGER,
    away_loses INTEGER,
    away_goals_for INTEGER,
    away_goals_against INTEGER,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(system_league_id, system_team_id, season)
);

-- ============================================================
-- 预测日志 + 投注账本（验证与反馈系统）
-- ============================================================

CREATE TABLE IF NOT EXISTS core.fact_prediction_log (
    prediction_id VARCHAR PRIMARY KEY,
    match_id VARCHAR NOT NULL,
    prediction_time TIMESTAMP NOT NULL,
    home_team VARCHAR,
    away_team VARCHAR,
    home_team_id VARCHAR,
    away_team_id VARCHAR,
    kickoff_time TIMESTAMP,
    market_type VARCHAR NOT NULL DEFAULT 'SPF',
    predicted_outcome VARCHAR,
    model_predicted_outcome VARCHAR,
    model_predicted_probability DOUBLE,
    value_candidate_outcome VARCHAR,
    predicted_probability DOUBLE,
    model_home_prob DOUBLE,
    model_draw_prob DOUBLE,
    model_away_prob DOUBLE,
    odds_at_prediction DOUBLE,
    expected_value DOUBLE,
    expected_home_goals DOUBLE,
    expected_away_goals DOUBLE,
    policy_recommendation VARCHAR,
    policy_reasons TEXT,
    confidence DOUBLE,
    risk_level VARCHAR,
    data_quality_flag BOOLEAN,
    feature_snapshot_json TEXT,
    diff_summary TEXT,
    version INT DEFAULT 1,
    status VARCHAR DEFAULT 'PENDING',
    verified_at TIMESTAMP,
    actual_home_score INT,
    actual_away_score INT,
    actual_outcome VARCHAR,
    actual_odds_result VARCHAR,
    is_verified BOOLEAN DEFAULT FALSE,
    is_correct BOOLEAN,
    profit_loss_unit DOUBLE,
    verified_by VARCHAR DEFAULT 'auto',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_prediction_log_match
    ON core.fact_prediction_log (match_id, prediction_time);
CREATE INDEX IF NOT EXISTS idx_prediction_log_status
    ON core.fact_prediction_log (status, prediction_time);

CREATE TABLE IF NOT EXISTS core.fact_bet_ledger (
    bet_id VARCHAR PRIMARY KEY,
    prediction_id VARCHAR,
    match_id VARCHAR NOT NULL,
    home_team VARCHAR,
    away_team VARCHAR,
    bet_time TIMESTAMP NOT NULL,
    stake_amount DOUBLE NOT NULL,
    market_type VARCHAR NOT NULL DEFAULT 'SPF',
    outcome_bet VARCHAR NOT NULL,
    odds_at_bet DOUBLE NOT NULL,
    predicted_prob DOUBLE,
    expected_value DOUBLE,
    result VARCHAR DEFAULT 'PENDING',
    actual_return DOUBLE DEFAULT 0.0,
    net_profit DOUBLE DEFAULT 0.0,
    verified_at TIMESTAMP,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (prediction_id) REFERENCES core.fact_prediction_log(prediction_id)
);

CREATE INDEX IF NOT EXISTS idx_bet_ledger_match
    ON core.fact_bet_ledger (match_id, bet_time);
CREATE INDEX IF NOT EXISTS idx_bet_ledger_result
    ON core.fact_bet_ledger (result, bet_time);

-- 投注绩效汇总视图
CREATE OR REPLACE VIEW core.view_bet_performance AS
SELECT
    COUNT(*) AS total_bets,
    SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) AS wins,
    SUM(CASE WHEN result = 'LOSS' THEN 1 ELSE 0 END) AS losses,
    SUM(CASE WHEN result = 'PENDING' THEN 1 ELSE 0 END) AS pending,
    ROUND(SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0), 4) AS win_rate,
    ROUND(SUM(stake_amount), 2) AS total_staked,
    ROUND(SUM(actual_return), 2) AS total_returned,
    ROUND(SUM(net_profit), 2) AS total_profit,
    ROUND(SUM(net_profit) / NULLIF(SUM(stake_amount), 0), 4) AS roi,
    ROUND(AVG(odds_at_bet), 2) AS avg_odds,
    ROUND(AVG(predicted_prob), 4) AS avg_predicted_prob,
    ROUND(SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) * 1.0
        / NULLIF(SUM(CASE WHEN result IN ('WIN', 'LOSS') THEN 1 ELSE 0 END), 0), 4) AS settled_win_rate
FROM core.fact_bet_ledger;

-- 预测质量汇总视图（按市场类型）
CREATE OR REPLACE VIEW core.view_prediction_quality AS
SELECT
    market_type,
    COUNT(*) AS total_predictions,
    SUM(CASE WHEN is_verified THEN 1 ELSE 0 END) AS verified_count,
    SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) AS correct_count,
    ROUND(SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) * 1.0
        / NULLIF(SUM(CASE WHEN is_verified THEN 1 ELSE 0 END), 0), 4) AS accuracy,
    ROUND(AVG(CASE WHEN is_verified THEN predicted_probability END), 4) AS avg_confidence,
    ROUND(AVG(CASE WHEN is_verified THEN ABS(predicted_probability - 0.333) END), 4) AS avg_confidence_spread,
    ROUND(AVG(CASE WHEN is_verified AND is_correct THEN predicted_probability END), 4) AS avg_win_confidence,
    ROUND(AVG(CASE WHEN is_verified AND NOT is_correct THEN predicted_probability END), 4) AS avg_loss_confidence
FROM core.fact_prediction_log
GROUP BY market_type;
"""


def initialize_database(database_path: str = "football_system.db") -> None:
    try:
        import duckdb  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "duckdb is not installed. Install project requirements first."
        ) from exc

    _backup_existing_database(database_path)
    with duckdb.connect(database_path) as conn:
        apply_schema(conn)
        _migrate_new_columns(conn)
        _record_schema_version(conn)


def migration_backup_path(database_path: str) -> str:
    return f"{database_path}.before-migration.bak"


def _backup_existing_database(database_path: str) -> None:
    if database_path == ":memory:":
        return
    source = Path(database_path)
    if not source.exists() or source.stat().st_size == 0:
        return
    try:
        shutil.copy2(source, migration_backup_path(database_path))
    except OSError:
        return


def _record_schema_version(conn: Any) -> None:
    """记录当前 schema 版本号，跳过已存在的版本。"""
    existing = conn.execute(
        "SELECT MAX(version) FROM core.schema_version"
    ).fetchone()
    if existing[0] is None or existing[0] < CURRENT_SCHEMA_VERSION:
        conn.execute(
            "INSERT OR IGNORE INTO core.schema_version (version, migration_name) "
            "VALUES (?, ?)",
            [CURRENT_SCHEMA_VERSION, f"v{CURRENT_SCHEMA_VERSION}_base"],
        )


def _migrate_new_columns(conn: Any) -> None:
    """为已有表添加新增列（Alter Table 迁移）。先检查列是否存在再执行，ALTER TABLE 失败时带表名和列名重新抛出。"""
    migrations = [
        ("core.fact_prediction_log", "ADD diff_summary VARCHAR"),
        ("core.fact_prediction_log", "ADD version INT DEFAULT 1"),
        # 批次C: 添加 home_team_id / away_team_id 用于按标准 ID 连接核验
        ("core.fact_prediction_log", "ADD home_team_id VARCHAR"),
        ("core.fact_prediction_log", "ADD away_team_id VARCHAR"),
        # 任务2: 预测日志语义分离
        ("core.fact_prediction_log", "ADD model_predicted_outcome VARCHAR"),
        ("core.fact_prediction_log", "ADD model_predicted_probability DOUBLE"),
        ("core.fact_prediction_log", "ADD value_candidate_outcome VARCHAR"),
        # 任务4: 中立场语义
        ("core.fact_match_schedule", "ADD is_neutral_venue BOOLEAN DEFAULT FALSE"),
        # 批次B: dim_collection_state 扩展
        ("core.dim_collection_state", "ADD last_success_at TIMESTAMP"),
        ("core.dim_collection_state", "ADD cursor_value VARCHAR"),
        ("core.dim_collection_state", "ADD status VARCHAR DEFAULT 'IDLE'"),
        ("core.dim_collection_state", "ADD failure_count INT DEFAULT 0"),
        ("core.dim_collection_state", "ADD last_error VARCHAR"),
        ("core.dim_collection_state", "ADD next_due_at TIMESTAMP"),
        ("core.dim_collection_state", "ADD started_at TIMESTAMP"),
    ]
    for table, alteration in migrations:
        col_name = alteration.split()[1]  # ADD <col_name> ...
        try:
            existing = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema || '.' || table_name = ? AND column_name = ?",
                [table, col_name],
            ).fetchone()
        except Exception as exc:
            raise RuntimeError(
                f"Migration inspection failed: table={table}, column={col_name}"
            ) from exc

        if existing is None:
            try:
                conn.execute(f"ALTER TABLE {table} {alteration}")
            except Exception:
                # ALTER TABLE 失败时带上下文重新抛出，不静默吞错
                raise RuntimeError(
                    f"Migration failed: ALTER TABLE {table} {alteration}"
                )


def apply_schema(connection: Any) -> None:
    _migrate_view_dependencies(connection)
    connection.execute(SCHEMA_SQL)


def _migrate_view_dependencies(connection: Any) -> None:
    """在创建视图前补齐已有表中被视图直接引用的新增列。"""
    table_count, column_count = connection.execute(
        """
        SELECT
            COUNT(DISTINCT table_name),
            COUNT(*) FILTER (WHERE column_name = 'is_neutral_venue')
        FROM information_schema.columns
        WHERE table_schema = 'core'
          AND table_name = 'fact_match_schedule'
        """
    ).fetchone()
    if table_count and not column_count:
        connection.execute(
            "ALTER TABLE core.fact_match_schedule "
            "ADD is_neutral_venue BOOLEAN DEFAULT FALSE"
        )
