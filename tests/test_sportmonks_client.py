from __future__ import annotations

import unittest
from datetime import date

from football_advisor.sportmonks_client import SportmonksClient


class SportmonksClientTests(unittest.TestCase):
    def test_mock_fetch_matches_by_date_returns_data(self):
        client = SportmonksClient(api_token="mock_token")

        result = client.fetch_matches_by_date(None, date(2026, 6, 2))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["match_count"], 1)

    def test_no_token_skips(self):
        client = SportmonksClient(api_token=None)

        result = client.fetch_matches_by_date(None, date(2026, 6, 2))

        self.assertEqual(result["status"], "skipped_no_token")

    def test_real_request_uses_date_endpoint(self):
        class RecordingClient(SportmonksClient):
            def __init__(self):
                super().__init__(api_token="token")
                self.paths = []

            def _http_get(self, path):
                self.paths.append(path)
                return {"data": []}

        client = RecordingClient()

        client.fetch_matches_by_date(None, date(2026, 6, 2))

        self.assertEqual(client.paths, ["/fixtures/date/2026-06-02"])


if __name__ == "__main__":
    unittest.main()
