from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from football_advisor.config import SearchConfig
from football_advisor.search_router import SearchRouter


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class FakeUrlOpen:
    def __init__(self, response_payload: dict):
        self.response_payload = response_payload
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(
            SimpleNamespace(
                url=request.full_url,
                timeout=timeout,
                headers=dict(request.header_items()),
            )
        )
        return FakeResponse(self.response_payload)


class SearchRouterTests(unittest.TestCase):
    def test_preview_uses_searxng_without_ingesting_results(self):
        urlopen = FakeUrlOpen(
            {
                "results": [
                    {
                        "title": "Arsenal team news",
                        "url": "https://example.test/arsenal",
                        "content": "Lineup update before kickoff.",
                        "score": 0.84,
                    }
                ]
            }
        )

        router = SearchRouter(
            config=SearchConfig(
                searxng_base_url="http://localhost:8080", timeout_seconds=9
            ),
            urlopen=urlopen,
        )

        preview = router.preview("Arsenal Chelsea injuries", limit=3)

        self.assertEqual(preview.provider, "searxng")
        self.assertFalse(preview.ingested)
        self.assertEqual(preview.results[0].title, "Arsenal team news")
        self.assertEqual(preview.results[0].url, "https://example.test/arsenal")
        self.assertEqual(preview.results[0].snippet, "Lineup update before kickoff.")
        self.assertEqual(preview.results[0].score, 0.84)
        self.assertIn("/search?", urlopen.requests[0].url)
        self.assertIn("q=Arsenal+Chelsea+injuries", urlopen.requests[0].url)
        self.assertEqual(urlopen.requests[0].timeout, 9)

    def test_preview_reports_skipped_when_no_provider_is_configured(self):
        router = SearchRouter(config=SearchConfig())

        preview = router.preview("Arsenal Chelsea injuries", limit=3)

        self.assertEqual(preview.provider, "none")
        self.assertEqual(preview.status, "skipped_no_provider_configured")
        self.assertEqual(preview.results, [])
        self.assertFalse(preview.ingested)

    def test_preview_accepts_world_cup_readiness_query_without_ingesting(self):
        urlopen = FakeUrlOpen(
            {
                "results": [
                    {
                        "title": "USA squad update",
                        "url": "https://example.test/usa-world-cup-squad",
                        "content": "World Cup squad and injury update.",
                    }
                ]
            }
        )
        router = SearchRouter(
            config=SearchConfig(searxng_base_url="http://localhost:8080"),
            urlopen=urlopen,
        )

        preview = router.preview(
            "USA Mexico World Cup squad injury press conference", limit=5
        )

        self.assertEqual(preview.status, "ok")
        self.assertFalse(preview.ingested)
        self.assertIn("World+Cup+squad+injury+press+conference", urlopen.requests[0].url)

    def test_preview_uses_serper_without_google_cx(self):
        urlopen = FakeUrlOpen(
            {
                "organic": [
                    {
                        "title": "Mexico squad update",
                        "link": "https://example.test/mexico",
                        "snippet": "World Cup injury and lineup update.",
                    }
                ]
            }
        )
        router = SearchRouter(
            config=SearchConfig(serper_api_key="serper-key", timeout_seconds=11),
            urlopen=urlopen,
        )

        preview = router.preview("Mexico South Africa World Cup", limit=3)

        self.assertEqual(preview.provider, "serper")
        self.assertEqual(preview.status, "ok")
        self.assertEqual(preview.results[0].provider, "serper")
        self.assertEqual(preview.results[0].url, "https://example.test/mexico")
        self.assertEqual(urlopen.requests[0].url, "https://google.serper.dev/search")
        self.assertEqual(urlopen.requests[0].headers["X-api-key"], "serper-key")
        self.assertEqual(urlopen.requests[0].timeout, 11)


if __name__ == "__main__":
    unittest.main()
