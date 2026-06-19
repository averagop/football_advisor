import logging
import os
import secrets
import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .feature_builder import FeatureDataUnavailable
from .job_runner import MaintenanceRunner
from .models import AnalysisMode, MatchRequest
from .pipeline import PredictionPipeline
from .search_router import SearchRouter
from .text_fetcher import TextFetcher
from .backtest_engine import BacktestEngine, DuckDBBacktestMatchLoader


logger = logging.getLogger(__name__)


class PredictPayload(BaseModel):
    query: str = Field(..., min_length=2)
    mode: AnalysisMode = AnalysisMode.STANDARD
    match_id: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    kickoff_time: datetime | None = None
    market: str = "1x2"


class SearchPreviewPayload(BaseModel):
    query: str = Field(..., min_length=2)
    limit: int = Field(5, ge=1, le=20)


class TextFetchPreviewPayload(BaseModel):
    urls: list[str] = Field(..., min_length=1, max_length=10)


class BacktestPayload(BaseModel):
    limit: int = Field(50, ge=1, le=10000)
    match_id_prefix: str = Field("SQLITE_MATCH_", min_length=1)


class CalibrationMetricsSchema(BaseModel):
    brier_score: float
    log_loss: float
    accuracy: float
    sample_size: int


class BacktestResponse(BaseModel):
    status: str = "success"
    message: str | None = None
    metrics: CalibrationMetricsSchema | None = None
    bets: int
    roi: float
    max_drawdown: float | None = None
    average_odds: float | None = None
    staged_win_rate: float | None = None
    equity_curve: list[float]
    no_bet_samples: int | None = None
    no_bet_reasons: dict[str, int] | None = None


def create_app(
    pipeline: PredictionPipeline | None = None,
    search_router: SearchRouter | None = None,
    text_fetcher: TextFetcher | None = None,
    backtest_loader: DuckDBBacktestMatchLoader | None = None,
    backtest_engine: BacktestEngine | None = None,
):
    try:
        from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Response
        from fastapi.responses import JSONResponse
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "fastapi is not installed. Install project requirements first."
        ) from exc

    app = FastAPI(title="Football Betting Advisor", version="0.1.0")
    pipeline = pipeline or PredictionPipeline()
    search_router = search_router or SearchRouter()
    text_fetcher = text_fetcher or TextFetcher()
    _backtest_loader = backtest_loader
    _backtest_engine = backtest_engine
    _backtest_lock = threading.Lock()

    def _get_backtest_loader() -> DuckDBBacktestMatchLoader:
        nonlocal _backtest_loader
        if _backtest_loader is None:
            with _backtest_lock:
                if _backtest_loader is None:
                    _backtest_loader = DuckDBBacktestMatchLoader()
        return _backtest_loader

    def _get_backtest_engine() -> BacktestEngine:
        nonlocal _backtest_engine
        if _backtest_engine is None:
            with _backtest_lock:
                if _backtest_engine is None:
                    _backtest_engine = BacktestEngine()
        return _backtest_engine

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/predict")
    def predict(payload: PredictPayload, background_tasks: BackgroundTasks) -> Any:
        request = MatchRequest(
            query=payload.query,
            mode=payload.mode,
            match_id=payload.match_id,
            home_team=payload.home_team,
            away_team=payload.away_team,
            kickoff_time=payload.kickoff_time,
            market=payload.market,
        )
        try:
            report = pipeline.predict(request)
        except FeatureDataUnavailable:
            return JSONResponse(
                status_code=503,
                content={
                    "match_id": payload.match_id or "",
                    "status": "data_unavailable",
                    "reason": "Required prediction features are unavailable.",
                },
            )

        # 预测响应后，后台执行到期维护任务
        db_path = pipeline.config.duckdb.database_path
        background_tasks.add_task(_run_maintenance, db_path)

        return {"report": report}

    @app.post("/search/preview")
    def search_preview(payload: SearchPreviewPayload) -> dict[str, Any]:
        preview = search_router.preview(payload.query, limit=payload.limit)
        return asdict(preview)

    if os.environ.get("FOOTBALL_ENABLE_FETCH_PREVIEW", "").strip().lower() == "true":

        @app.post("/search/fetch-preview")
        def fetch_preview(
            payload: TextFetchPreviewPayload,
            x_football_admin_token: str | None = Header(default=None),
        ) -> dict[str, Any]:
            configured_token = os.environ.get(
                "FOOTBALL_FETCH_PREVIEW_ADMIN_TOKEN", ""
            )
            if not configured_token:
                raise HTTPException(
                    status_code=503,
                    detail="admin token is not configured",
                )
            if x_football_admin_token is None:
                raise HTTPException(
                    status_code=401,
                    detail="admin token is required",
                )
            if not secrets.compare_digest(
                x_football_admin_token,
                configured_token,
            ):
                raise HTTPException(status_code=403, detail="invalid admin token")

            documents = text_fetcher.fetch_many(payload.urls)
            return {
                "documents": [_as_response_dict(document) for document in documents]
            }

    @app.post("/backtest", response_model=BacktestResponse)
    def run_backtest(
        payload: BacktestPayload, response: Response = None
    ) -> BacktestResponse:
        try:
            loader = _get_backtest_loader()
            engine = _get_backtest_engine()
            matches = loader.load_walk_forward_matches(
                limit=payload.limit,
                match_id_prefix=payload.match_id_prefix,
            )
            if not matches:
                return BacktestResponse(
                    status="no_data",
                    message="No historical matches found for backtest.",
                    bets=0,
                    roi=0.0,
                    equity_curve=[],
                )
            result = engine.run(matches)
            result_dict = asdict(result)
            return BacktestResponse(status="success", **result_dict)
        except Exception as exc:
            logger.exception("Backtest request failed.")
            if response is not None:
                response.status_code = 500
            return BacktestResponse(
                status="error",
                message="Internal server error.",
                bets=0,
                roi=0.0,
                equity_curve=[],
            )

    return app


def _run_maintenance(db_path: str) -> None:
    """后台维护任务：在预测响应后执行。"""
    try:
        runner = MaintenanceRunner(database_path=db_path)
        results = runner.run_due()
        for r in results:
            if r.status == "failed":
                logger.warning("Maintenance job %s failed: %s", r.job_name, r.error)
    except Exception:
        logger.exception("Background maintenance failed.")


def _as_response_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    return dict(vars(value))


try:
    import fastapi

    app = create_app()
except ImportError:
    app = None
