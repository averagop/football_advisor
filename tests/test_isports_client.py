from __future__ import annotations

import unittest
import urllib.error
from datetime import date
from unittest.mock import patch

from football_advisor.isports_client import ISportsClient


class ISportsClientTests(unittest.TestCase):
    def test_transport_failure_is_not_reported_as_no_matches(self):
        client = ISportsClient(api_token="secret-token")

        with patch(
            "football_advisor.isports_client.urllib.request.urlopen",
            side_effect=urllib.error.URLError("certificate verify failed"),
        ):
            result = client.fetch_matches_by_date(None, date(2026, 6, 2))

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "transport_error")

    def test_plain_http_is_rejected_before_token_is_sent(self):
        client = ISportsClient(
            api_token="secret-token",
            base_url="http://api.isportsapi.com",
        )

        with patch("football_advisor.isports_client.urllib.request.urlopen") as urlopen:
            result = client.fetch_matches_by_date(None, date(2026, 6, 2))

        self.assertEqual(result["status"], "skipped_insecure_transport")
        urlopen.assert_not_called()

    def test_mock_fetch_matches_by_date_returns_data(self):
        client = ISportsClient(api_token="mock_token")

        result = client.fetch_matches_by_date(None, date(2026, 6, 2))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["match_count"], 1)

    def test_no_token_skips(self):
        client = ISportsClient(api_token=None)

        result = client.fetch_matches_by_date(None, date(2026, 6, 2))

        self.assertEqual(result["status"], "skipped_no_token")

    def test_real_request_uses_schedule_date_endpoint(self):
        class RecordingClient(ISportsClient):
            def __init__(self):
                super().__init__(api_token="token")
                self.paths = []

            def _http_get(self, path):
                self.paths.append(path)
                return {"data": []}

        client = RecordingClient()

        client.fetch_matches_by_date(None, date(2026, 6, 2))

        self.assertEqual(
            client.paths,
            ["/sport/football/schedule?date=2026-06-02"],
        )


if __name__ == "__main__":
    unittest.main()
