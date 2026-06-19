from __future__ import annotations

import socket
import unittest
from unittest.mock import patch

from football_advisor.text_fetcher import TextFetcher


class FakeResponse:
    def __init__(self, body: str, content_type: str = "text/html; charset=utf-8"):
        self.body = body.encode("utf-8")
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.body


class FakeUrlOpen:
    def __init__(self, responses: dict[str, FakeResponse]):
        self.responses = responses
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request.full_url, timeout))
        return self.responses[request.full_url]


class TextFetcherTests(unittest.TestCase):
    def setUp(self):
        self.dns_patch = patch(
            "football_advisor.url_safety.socket.getaddrinfo",
            return_value=[
                (2, 1, 6, "", ("93.184.216.34", 443)),
            ],
        )
        self.dns_patch.start()

    def tearDown(self):
        self.dns_patch.stop()

    def test_fetches_and_cleans_article_text_without_ingesting(self):
        html = """
        <html>
          <head><title>Arsenal injury update</title><script>ignore()</script></head>
          <body>
            <nav>Menu text</nav>
            <article>
              <h1>Arsenal injury update</h1>
              <p>Gabriel trained before kickoff.</p>
              <p>Arteta said the squad will be assessed.</p>
            </article>
          </body>
        </html>
        """
        urlopen = FakeUrlOpen({"https://example.test/news": FakeResponse(html)})

        result = TextFetcher(
            urlopen=urlopen, timeout_seconds=7, min_text_length=0, require_title=True
        ).fetch_one("https://example.test/news")

        self.assertEqual(result.url, "https://example.test/news")
        self.assertEqual(result.title, "Arsenal injury update")
        self.assertIn("Gabriel trained before kickoff.", result.text)
        self.assertIn("Arteta said the squad will be assessed.", result.text)
        self.assertNotIn("ignore()", result.text)
        self.assertNotIn("Menu text", result.text)
        self.assertFalse(result.ingested)
        self.assertEqual(len(result.content_hash), 64)
        self.assertEqual(urlopen.requests, [("https://example.test/news", 7)])

    def test_fetch_many_deduplicates_by_cleaned_content_hash(self):
        html = "<html><body><article><p>Same article text.</p></article></body></html>"
        urlopen = FakeUrlOpen(
            {
                "https://example.test/a": FakeResponse(html),
                "https://example.test/b": FakeResponse(html),
            }
        )

        results = TextFetcher(
            urlopen=urlopen, min_text_length=0, require_title=False
        ).fetch_many(["https://example.test/a", "https://example.test/b"])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://example.test/a")

    def test_rejects_non_http_urls(self):
        result = TextFetcher().fetch_one("file:///etc/passwd")

        self.assertEqual(result.status, "unsupported_url_scheme")
        self.assertFalse(result.ingested)
        self.assertEqual(result.text, "")

    def test_dns_failure_stops_before_urlopen(self):
        urlopen = FakeUrlOpen({})
        with patch(
            "football_advisor.url_safety.socket.getaddrinfo",
            side_effect=socket.gaierror(),
        ):
            result = TextFetcher(urlopen=urlopen).fetch_one(
                "https://unresolved.example/news"
            )

        self.assertEqual(
            result.status,
            "unsafe_url:dns_resolution_failed:unresolved.example",
        )
        self.assertEqual(urlopen.requests, [])

    def test_rejects_missing_title(self):
        html = "<html><body><p>Some text</p></body></html>"
        urlopen = FakeUrlOpen({"https://example.test/notitle": FakeResponse(html)})
        result = TextFetcher(
            urlopen=urlopen, require_title=True, min_text_length=0
        ).fetch_one("https://example.test/notitle")
        self.assertEqual(result.status, "missing_title")
        self.assertEqual(result.title, "")

    def test_rejects_short_text(self):
        html = "<html><head><title>Title</title></head><body><p>Short</p></body></html>"
        urlopen = FakeUrlOpen({"https://example.test/short": FakeResponse(html)})
        result = TextFetcher(
            urlopen=urlopen, require_title=True, min_text_length=150
        ).fetch_one("https://example.test/short")
        self.assertEqual(result.status, "text_too_short")
        self.assertEqual(result.text, "")

    def test_fetches_world_cup_squad_and_injury_article(self):
        html = """
        <html>
          <head><title>USA World Cup squad and injury update</title></head>
          <body>
            <header>Navigation</header>
            <article>
              <h1>USA World Cup squad and injury update</h1>
              <p>The final squad has been confirmed before the World Cup opener.</p>
              <p>The coach said one midfielder trained separately and will be assessed.</p>
              <p>The press conference also covered tactical changes and likely starters.</p>
            </article>
          </body>
        </html>
        """
        urlopen = FakeUrlOpen({"https://example.test/world-cup": FakeResponse(html)})

        result = TextFetcher(
            urlopen=urlopen, timeout_seconds=5, min_text_length=80
        ).fetch_one("https://example.test/world-cup")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.title, "USA World Cup squad and injury update")
        self.assertIn("final squad has been confirmed", result.text)
        self.assertIn("press conference", result.text)
        self.assertEqual(len(result.content_hash), 64)


if __name__ == "__main__":
    unittest.main()
