import os
import sys
import unittest
from dataclasses import dataclass
from types import SimpleNamespace

from football_advisor.calibration_engine import CalibrationMetrics
from football_advisor.backtest_engine import BacktestResult


@dataclass(frozen=True)
class FakeSearchItem:
    title: str
    url: str
    snippet: str
    provider: str
    score: float | None = None


@dataclass(frozen=True)
class FakeSearchPreview:
    query: str
    provider: str
    status: str
    ingested: bool
    results: list[FakeSearchItem]


class FakeSearchRouter:
    def preview(self, query: str, limit: int = 5):
        return FakeSearchPreview(
            query=query,
            provider="searxng",
            status="ok",
            ingested=False,
            results=[
                FakeSearchItem(
                    title="Arsenal team news",
                    url="https://example.test/arsenal",
                    snippet="Lineup update.",
                    provider="searxng",
                    score=0.7,
                )
            ],
        )


class FakeTextFetcher:
    def fetch_many(self, urls):
        return [
            SimpleNamespace(
                url=urls[0],
                title="Arsenal injury update",
                text="Gabriel trained before kickoff.",
                content_hash="a" * 64,
                status="ok",
                ingested=False,
                error=None,
            )
        ]


class FakeBacktestLoader:
    def load_walk_forward_matches(self, limit: int, match_id_prefix: str):
        self.limit = limit
        self.match_id_prefix = match_id_prefix
        return ["match1", "match2"]


class FakeBacktestEngine:
    def run(self, matches):
        self.matches = matches
        return BacktestResult(
            metrics=CalibrationMetrics(brier_score=0.1, log_loss=0.2, accuracy=0.6, sample_size=2),
            bets=2,
            roi=0.15,
            max_drawdown=0.05,
            average_odds=2.0,
            staged_win_rate=0.5,
            equity_curve=[1.0, 1.15],
            no_bet_samples=0,
            no_bet_reasons={},
        )


class FakeFastAPI:
    def __init__(self, title: str, version: str):
        self.title = title
        self.version = version
        self.routes = {}

    def get(self, path: str, **kwargs):
        def decorator(func):
            self.routes[("GET", path)] = func
            return func

        return decorator

    def post(self, path: str, **kwargs):
        def decorator(func):
            self.routes[("POST", path)] = func
            return func

        return decorator


class FakeBackgroundTasks:
    """模拟 fastapi 的 BackgroundTasks。"""

    def __init__(self):
        self.tasks = []

    def add_task(self, func, *args, **kwargs):
        self.tasks.append((func, args, kwargs))


class FakeResponse:
    def __init__(self):
        self.status_code = 200


class FakeHTTPException(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _fake_fastapi_module():
    return SimpleNamespace(
        FastAPI=FakeFastAPI,
        Response=FakeResponse,
        BackgroundTasks=FakeBackgroundTasks,
        Header=lambda default=None: default,
        HTTPException=FakeHTTPException,
    )


class ApiTests(unittest.TestCase):
    def test_search_preview_endpoint_returns_router_results(self):
        original = sys.modules.get("fastapi")
        sys.modules["fastapi"] = _fake_fastapi_module()
        try:
            from football_advisor.api import SearchPreviewPayload, create_app

            app = create_app(search_router=FakeSearchRouter())
            handler = app.routes[("POST", "/search/preview")]
            payload = SearchPreviewPayload(query="Arsenal Chelsea", limit=4)
            response = handler(payload)
        finally:
            if original is None:
                sys.modules.pop("fastapi", None)
            else:
                sys.modules["fastapi"] = original

        self.assertEqual(response["provider"], "searxng")
        self.assertFalse(response["ingested"])
        self.assertEqual(response["results"][0]["title"], "Arsenal team news")

    def test_text_fetch_preview_endpoint_returns_cleaned_documents(self):
        original = sys.modules.get("fastapi")
        sys.modules["fastapi"] = _fake_fastapi_module()
        original_env = os.environ.get("FOOTBALL_ENABLE_FETCH_PREVIEW")
        original_token = os.environ.get("FOOTBALL_FETCH_PREVIEW_ADMIN_TOKEN")
        os.environ["FOOTBALL_ENABLE_FETCH_PREVIEW"] = "true"
        os.environ["FOOTBALL_FETCH_PREVIEW_ADMIN_TOKEN"] = "test-admin-token"
        try:
            from football_advisor.api import TextFetchPreviewPayload, create_app

            app = create_app(
                search_router=FakeSearchRouter(), text_fetcher=FakeTextFetcher()
            )
            handler = app.routes[("POST", "/search/fetch-preview")]
            payload = TextFetchPreviewPayload(urls=["https://example.test/arsenal"])
            response = handler(payload, "test-admin-token")
        finally:
            if original is None:
                sys.modules.pop("fastapi", None)
            else:
                sys.modules["fastapi"] = original
            if original_env is None:
                os.environ.pop("FOOTBALL_ENABLE_FETCH_PREVIEW", None)
            else:
                os.environ["FOOTBALL_ENABLE_FETCH_PREVIEW"] = original_env
            if original_token is None:
                os.environ.pop("FOOTBALL_FETCH_PREVIEW_ADMIN_TOKEN", None)
            else:
                os.environ["FOOTBALL_FETCH_PREVIEW_ADMIN_TOKEN"] = original_token

        self.assertEqual(response["documents"][0]["title"], "Arsenal injury update")
        self.assertFalse(response["documents"][0]["ingested"])

    def test_backtest_endpoint_returns_result(self):
        original = sys.modules.get("fastapi")
        sys.modules["fastapi"] = _fake_fastapi_module()
        try:
            from football_advisor.api import BacktestPayload, create_app

            loader = FakeBacktestLoader()
            engine = FakeBacktestEngine()
            app = create_app(backtest_loader=loader, backtest_engine=engine)
            handler = app.routes[("POST", "/backtest")]
            payload = BacktestPayload(limit=10, match_id_prefix="TEST_")
            response = handler(payload)
        finally:
            if original is None:
                sys.modules.pop("fastapi", None)
            else:
                sys.modules["fastapi"] = original

        self.assertEqual(loader.limit, 10)
        self.assertEqual(loader.match_id_prefix, "TEST_")
        self.assertEqual(engine.matches, ["match1", "match2"])
        self.assertEqual(response.status, "success")
        self.assertEqual(response.bets, 2)
        self.assertEqual(response.roi, 0.15)
        self.assertEqual(response.equity_curve, [1.0, 1.15])

    def test_backtest_endpoint_empty_returns_no_data(self):
        original = sys.modules.get("fastapi")
        sys.modules["fastapi"] = _fake_fastapi_module()
        try:
            from football_advisor.api import BacktestPayload, create_app

            class EmptyLoader:
                def load_walk_forward_matches(self, limit, match_id_prefix):
                    return []

            app = create_app(backtest_loader=EmptyLoader(), backtest_engine=FakeBacktestEngine())
            handler = app.routes[("POST", "/backtest")]
            payload = BacktestPayload(limit=5)
            response = handler(payload)
        finally:
            if original is None:
                sys.modules.pop("fastapi", None)
            else:
                sys.modules["fastapi"] = original

        self.assertEqual(response.status, "no_data")
        self.assertEqual(response.bets, 0)
        self.assertEqual(response.equity_curve, [])

    def test_backtest_endpoint_error_returns_error_status(self):
        original = sys.modules.get("fastapi")
        sys.modules["fastapi"] = _fake_fastapi_module()
        try:
            from football_advisor.api import BacktestPayload, create_app

            class BrokenLoader:
                def load_walk_forward_matches(self, limit, match_id_prefix):
                    raise RuntimeError("DuckDB file not found")

            app = create_app(backtest_loader=BrokenLoader(), backtest_engine=FakeBacktestEngine())
            handler = app.routes[("POST", "/backtest")]
            payload = BacktestPayload(limit=5)
            response_meta = FakeResponse()
            with self.assertLogs("football_advisor.api", level="ERROR"):
                response = handler(payload, response_meta)
        finally:
            if original is None:
                sys.modules.pop("fastapi", None)
            else:
                sys.modules["fastapi"] = original

        self.assertEqual(response.status, "error")
        self.assertEqual(response.message, "Internal server error.")
        self.assertEqual(response_meta.status_code, 500)
        self.assertEqual(response.bets, 0)
        self.assertEqual(response.equity_curve, [])


if __name__ == "__main__":
    unittest.main()
