"""Debug the no_bet_data_quality_flag by checking each subquery condition."""
import sys
sys.path.insert(0, ".")
import duckdb
from football_advisor.db_schema import apply_schema

conn = duckdb.connect("football_system.db")
apply_schema(conn)

# Check the view_match_feature_base subquery directly
mf = conn.execute("""
SELECT match_id, missing_team_stats_flag, missing_context_flag,
       home_team_stats_quality_flag, away_team_stats_quality_flag,
       structured_updated_at
FROM core.view_match_feature_base
WHERE match_id = 'M_ST_2040143'
""").fetchone()
print("view_match_feature_base:")
print(f"  match_id: {mf[0]}")
print(f"  missing_team_stats_flag: {mf[1]}")
print(f"  missing_context_flag: {mf[2]}")
print(f"  home_quality: {mf[3]}")
print(f"  away_quality: {mf[4]}")
print(f"  structured_updated_at: {mf[5]}")

# Check the view_market_feature_base subquery
market = conn.execute("""
SELECT match_id, missing_1x2_odds_flag, market_updated_at
FROM core.view_market_feature_base
WHERE match_id = 'M_ST_2040143'
""").fetchone()
print("\nview_market_feature_base:")
print(f"  match_id: {market[0]}")
print(f"  missing_1x2_odds: {market[1]}")
print(f"  market_updated_at: {market[2]}")

# Check the news table
news = conn.execute("""
SELECT match_id, news_source_count, news_last_updated_at
FROM core.fact_news_signal_summary
WHERE match_id = 'M_ST_2040143'
""").fetchone()
print("\nnews_signal_summary:")
print(f"  match_id: {news[0]}")
print(f"  source_count: {news[1]}")
print(f"  last_updated: {news[2]}")

# Now evaluate each condition manually
print("\n--- Manual condition evaluation ---")
print(f"1. missing_team_stats: {mf[1]} -> {bool(mf[1])}")
print(f"2. home_quality in blacklist: {mf[3] in ('CSV_PRIOR_MATCHES_ONLY', 'SQLITE_PRIOR_MATCHES_ONLY', 'WORLD_CUP_LAST5_SNAPSHOT')}")
print(f"3. away_quality in blacklist: {mf[4] in ('CSV_PRIOR_MATCHES_ONLY', 'SQLITE_PRIOR_MATCHES_ONLY', 'WORLD_CUP_LAST5_SNAPSHOT')}")
print(f"4. missing_1x2_odds: {bool(market[1])}")
print(f"5. news.match_id IS NULL: {news[0] is None}")
print(f"6. news_source_count <= 0: {news[1] <= 0}")
print(f"7. NULL check: {news[2]}")

# Check if view is using outdated schema
# Let's try to manually evaluate the full CASE
result = conn.execute("""
SELECT 
    mf.missing_team_stats_flag as c1,
    COALESCE(mf.home_team_stats_quality_flag IN ('CSV_PRIOR_MATCHES_ONLY', 'SQLITE_PRIOR_MATCHES_ONLY', 'WORLD_CUP_LAST5_SNAPSHOT'), FALSE) as c2,
    COALESCE(mf.away_team_stats_quality_flag IN ('CSV_PRIOR_MATCHES_ONLY', 'SQLITE_PRIOR_MATCHES_ONLY', 'WORLD_CUP_LAST5_SNAPSHOT'), FALSE) as c3,
    market.missing_1x2_odds_flag as c4,
    news.match_id IS NULL as c5,
    COALESCE(news.news_source_count, 0) <= 0 as c6,
    date_diff('minute', COALESCE(news.news_last_updated_at, TIMESTAMP '1970-01-01'), CAST(CURRENT_TIMESTAMP AS TIMESTAMP)) > 30 as c7,
    date_diff('minute', COALESCE(LEAST(mf.structured_updated_at, market.market_updated_at), TIMESTAMP '1970-01-01'), CAST(CURRENT_TIMESTAMP AS TIMESTAMP)) > 30 as c8
FROM core.view_match_feature_base mf
LEFT JOIN core.view_market_feature_base market ON mf.match_id = market.match_id
LEFT JOIN core.fact_news_signal_summary news ON mf.match_id = news.match_id
WHERE mf.match_id = 'M_ST_2040143'
""").fetchone()
print(f"\nManual CASE evaluation:")
labels = ["c1: missing_team_stats", "c2: home_quality_blacklist", "c3: away_quality_blacklist",
          "c4: missing_1x2_odds", "c5: news_match_null", "c6: news_count_zero",
          "c7: news_age>30", "c8: data_age>30"]
for l, v in zip(labels, result):
    print(f"  {l}: {v}")

conn.close()