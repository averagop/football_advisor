from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class DuckDBConfig:
    database_path: str = "football_system.db"


@dataclass(frozen=True)
class ChromaConfig:
    persist_directory: str = "chroma_store"
    collection_name: str = "football_news"
    max_distance: float = 0.7
    embedding_model_name: str = "bge-m3"
    embedding_model_version: str = "bge-m3"
    ollama_base_url: str = "http://localhost:11434/api/embeddings"


@dataclass(frozen=True)
class LLMConfig:
    external_base_url: str | None = None
    external_api_key: str | None = None
    external_model: str = "gpt-4.1-mini"
    backup_base_url: str = "http://localhost:11434/v1"
    backup_model: str = "qwen2.5:7b"
    retries: int = 2
    timeout_seconds: int = 45


@dataclass(frozen=True)
class SearchConfig:
    searxng_base_url: str | None = None
    serper_api_key: str | None = None
    google_api_key: str | None = None
    google_cx: str | None = None
    timeout_seconds: int = 15


@dataclass(frozen=True)
class SyncConfig:
    structured_source: str = "structured_provider_placeholder"
    news_source: str = "news_provider_placeholder"
    sporttery_base_url: str | None = "https://www.sporttery.cn"
    football_data_api_token: str | None = None
    football_data_base_url: str = "https://api.football-data.org/v4"
    api_football_token: str | None = None
    api_football_base_url: str = "https://v3.football.api-sports.io"
    thesportsdb_api_token: str | None = None
    thesportsdb_base_url: str = "https://www.thesportsdb.com/api/v1/json"
    sportmonks_api_token: str | None = None
    sportmonks_base_url: str = "https://api.sportmonks.com/v3/football"
    isports_api_token: str | None = None
    isports_base_url: str = "https://api.isportsapi.com"
    the_odds_api_token: str | None = None
    the_odds_api_base_url: str = "https://api.the-odds-api.com/v4"
    the_odds_api_sport_keys: tuple[str, ...] = ("soccer_fifa_world_cup",)
    rapidapi_token: str | None = None
    odds_feed_rapid_host: str | None = None
    odds_feed_rapid_base_url: str | None = None
    date_slide_max_days: int = 7
    post_match_collect_enabled: bool = True
    post_match_collect_leagues: tuple[str, ...] = (
        "TOP5_EPL", "TOP5_LA_LIGA", "TOP5_SERIE_A",
        "TOP5_BUNDESLIGA", "TOP5_LIGUE_1", "WC_WORLD_CUP_2026",
    )
    pre_match_lookahead_days: int = 3  # API-Football 免费层仅支持 ±3 天
    pre_match_collect_enabled: bool = True
    pre_match_collect_leagues: tuple[str, ...] = (
        "TOP5_EPL", "TOP5_LA_LIGA", "TOP5_SERIE_A",
        "TOP5_BUNDESLIGA", "TOP5_LIGUE_1", "WC_WORLD_CUP_2026",
    )


@dataclass(frozen=True)
class FeatureBuilderConfig:
    feature_view_or_query: str = "core.view_llm_match_prediction_base"


@dataclass(frozen=True)
class ProbabilityConfig:
    poisson_weight: float = 0.72
    model_version: str = "baseline-uncalibrated"
    calibrated: bool = False
    enable_enhanced_boosts: bool = False


@dataclass(frozen=True)
class AdvisorConfig:
    duckdb: DuckDBConfig = field(default_factory=DuckDBConfig)
    chroma: ChromaConfig = field(default_factory=ChromaConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    feature_builder: FeatureBuilderConfig = field(default_factory=FeatureBuilderConfig)
    probability: ProbabilityConfig = field(default_factory=ProbabilityConfig)


def load_config(environ: Mapping[str, str] | None = None) -> AdvisorConfig:
    env = environ if environ is not None else os.environ
    return AdvisorConfig(
        duckdb=DuckDBConfig(
            database_path=_string(env, "FOOTBALL_DUCKDB_PATH", "football_system.db"),
        ),
        chroma=ChromaConfig(
            persist_directory=_string(
                env, "FOOTBALL_CHROMA_PERSIST_DIRECTORY", "chroma_store"
            ),
            collection_name=_string(env, "FOOTBALL_CHROMA_COLLECTION", "football_news"),
            max_distance=_float(env, "FOOTBALL_CHROMA_MAX_DISTANCE", 0.7),
            embedding_model_name=_string(
                env, "FOOTBALL_CHROMA_EMBEDDING_MODEL", "bge-m3"
            ),
            embedding_model_version=_string(
                env,
                "FOOTBALL_CHROMA_EMBEDDING_MODEL_VERSION",
                _string(env, "FOOTBALL_CHROMA_EMBEDDING_MODEL", "bge-m3"),
            ),
            ollama_base_url=_string(
                env,
                "FOOTBALL_CHROMA_OLLAMA_BASE_URL",
                "http://localhost:11434/api/embeddings",
            ),
        ),
        llm=LLMConfig(
            external_base_url=_optional_string(env, "FOOTBALL_EXTERNAL_LLM_BASE_URL"),
            external_api_key=_optional_string(env, "FOOTBALL_EXTERNAL_LLM_API_KEY"),
            external_model=_string(env, "FOOTBALL_EXTERNAL_LLM_MODEL", "gpt-4.1-mini"),
            backup_base_url=_string(
                env, "FOOTBALL_BACKUP_LLM_BASE_URL", "http://localhost:11434/v1"
            ),
            backup_model=_string(env, "FOOTBALL_BACKUP_LLM_MODEL", "qwen2.5:7b"),
            retries=_int(env, "FOOTBALL_LLM_RETRIES", 2),
            timeout_seconds=_int(env, "FOOTBALL_LLM_TIMEOUT_SECONDS", 45),
        ),
        search=SearchConfig(
            searxng_base_url=_optional_string(env, "FOOTBALL_SEARXNG_BASE_URL"),
            serper_api_key=(
                _optional_string(env, "FOOTBALL_SERPER_API_KEY")
                or _optional_string(env, "FOOTBALL_GOOGLE_SEARCH_API_KEY")
            ),
            google_api_key=_optional_string(env, "FOOTBALL_GOOGLE_SEARCH_API_KEY"),
            google_cx=_optional_string(env, "FOOTBALL_GOOGLE_SEARCH_CX"),
            timeout_seconds=_int(env, "FOOTBALL_SEARCH_TIMEOUT_SECONDS", 15),
        ),
        sync=SyncConfig(
            structured_source=_string(
                env,
                "FOOTBALL_STRUCTURED_SYNC_SOURCE",
                "structured_provider_placeholder",
            ),
            news_source=_string(
                env, "FOOTBALL_NEWS_SYNC_SOURCE", "news_provider_placeholder"
            ),
            sporttery_base_url=_string(
                env, "FOOTBALL_SPORTTERY_BASE_URL", "https://www.sporttery.cn"
            ),
            football_data_api_token=_optional_string(
                env, "FOOTBALL_DATA_API_TOKEN"
            ),
            football_data_base_url=_string(
                env, "FOOTBALL_DATA_BASE_URL", "https://api.football-data.org/v4"
            ),
            api_football_token=_optional_string(
                env, "API_FOOTBALL_TOKEN"
            ),
            api_football_base_url=_string(
                env, "API_FOOTBALL_BASE_URL", "https://v3.football.api-sports.io"
            ),
            thesportsdb_api_token=_optional_string(
                env, "THESPORTSDB_API_TOKEN"
            ),
            thesportsdb_base_url=_string(
                env,
                "THESPORTSDB_BASE_URL",
                "https://www.thesportsdb.com/api/v1/json",
            ),
            sportmonks_api_token=_optional_string(
                env, "SPORTMONKS_API_TOKEN"
            ),
            sportmonks_base_url=_string(
                env,
                "SPORTMONKS_BASE_URL",
                "https://api.sportmonks.com/v3/football",
            ),
            isports_api_token=_optional_string(
                env, "ISPORTS_API_TOKEN"
            ),
            isports_base_url=_string(
                env, "ISPORTS_BASE_URL", "https://api.isportsapi.com"
            ),
            the_odds_api_token=_optional_string(env, "THE_ODDS_API_TOKEN"),
            the_odds_api_base_url=_string(
                env, "THE_ODDS_API_BASE_URL", "https://api.the-odds-api.com/v4"
            ),
            the_odds_api_sport_keys=_csv_tuple(
                env,
                "THE_ODDS_API_SPORT_KEYS",
                ("soccer_international_friendlies",),
            ),
            rapidapi_token=_optional_string(env, "RAPIDAPI_TOKEN"),
            odds_feed_rapid_host=_optional_string(env, "ODDS_FEED_RAPID_HOST"),
            odds_feed_rapid_base_url=_optional_string(
                env, "ODDS_FEED_RAPID_BASE_URL"
            ),
            date_slide_max_days=_int(env, "FOOTBALL_DATE_SLIDE_MAX_DAYS", 7),
            post_match_collect_enabled=_bool(
                env, "FOOTBALL_POST_MATCH_COLLECT_ENABLED", True
            ),
            post_match_collect_leagues=_csv_tuple(
                env,
                "FOOTBALL_POST_MATCH_COLLECT_LEAGUES",
                (
                    "TOP5_EPL", "TOP5_LA_LIGA", "TOP5_SERIE_A",
                    "TOP5_BUNDESLIGA", "TOP5_LIGUE_1", "WC_WORLD_CUP_2026",
                ),
            ),
            pre_match_lookahead_days=_int(
                env, "FOOTBALL_PRE_MATCH_LOOKAHEAD_DAYS", 3
            ),
            pre_match_collect_enabled=_bool(
                env, "FOOTBALL_PRE_MATCH_COLLECT_ENABLED", True
            ),
            pre_match_collect_leagues=_csv_tuple(
                env,
                "FOOTBALL_PRE_MATCH_COLLECT_LEAGUES",
                (
                    "TOP5_EPL", "TOP5_LA_LIGA", "TOP5_SERIE_A",
                    "TOP5_BUNDESLIGA", "TOP5_LIGUE_1", "WC_WORLD_CUP_2026",
                ),
            ),
        ),
        feature_builder=FeatureBuilderConfig(
            feature_view_or_query=_string(
                env,
                "FOOTBALL_FEATURE_VIEW_OR_QUERY",
                "core.view_llm_match_prediction_base",
            )
        ),
        probability=ProbabilityConfig(
            poisson_weight=_float(env, "FOOTBALL_PROBABILITY_POISSON_WEIGHT", 0.72),
            model_version=_string(
                env,
                "FOOTBALL_PROBABILITY_MODEL_VERSION",
                "baseline-uncalibrated",
            ),
            calibrated=_bool(env, "FOOTBALL_PROBABILITY_CALIBRATED", False),
            enable_enhanced_boosts=_bool(
                env,
                "FOOTBALL_PROBABILITY_ENABLE_ENHANCED_BOOSTS",
                False,
            ),
        ),
    )


def _optional_string(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key)
    if value is None or not value.strip():
        return None
    return value.strip()


def _string(env: Mapping[str, str], key: str, default: str) -> str:
    return _optional_string(env, key) or default


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    value = _optional_string(env, key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer.") from exc


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    value = _optional_string(env, key)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number.") from exc


def _csv_tuple(
    env: Mapping[str, str], key: str, default: tuple[str, ...]
) -> tuple[str, ...]:
    value = _optional_string(env, key)
    if value is None:
        return default
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    return items or default


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    value = _optional_string(env, key)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes"):
        return True
    if normalized in ("0", "false", "no"):
        return False
    raise ValueError(f"{key} must be a boolean.")
