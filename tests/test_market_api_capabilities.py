from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import check_market_api_capabilities


class MarketApiCapabilitiesTests(unittest.TestCase):
    def test_load_env_reads_utf8_sig_and_redacts_values(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", delete=False) as temp:
            temp.write("THE_ODDS_API_TOKEN=secret\n")
            temp.write("ODDS_FEED_RAPID_BASE_URL=https://example.test\n")
            env_path = temp.name

        env = check_market_api_capabilities.load_env(Path(env_path))

        self.assertEqual(env["THE_ODDS_API_TOKEN"], "secret")
        self.assertEqual(
            check_market_api_capabilities.redacted_status(env, "THE_ODDS_API_TOKEN"),
            "已配置",
        )

    def test_classify_payload_distinguishes_odds_and_capital_flow(self):
        payload = {
            "events": [
                {
                    "home_team": "Greece",
                    "away_team": "Italy",
                    "bookmakers": [
                        {
                            "markets": [
                                {
                                    "key": "h2h",
                                    "outcomes": [
                                        {"name": "Greece", "price": 2.2},
                                        {"name": "Draw", "price": 3.1},
                                        {"name": "Italy", "price": 3.0},
                                    ],
                                }
                            ]
                        }
                    ],
                    "totalMatched": 10000,
                }
            ]
        }

        result = check_market_api_capabilities.classify_payload(payload)

        self.assertTrue(result["odds_available"])
        self.assertEqual(result["capital_flow_fields"], ["totalMatched"])

    def test_the_odds_api_probe_skips_invalid_sport_key(self):
        class FakeClient:
            def __init__(self, **kwargs):
                pass

            def _candidate_the_odds_sport_keys(self):
                return ("bad_sport", "soccer_good")

            def _the_odds_api_url(self, sport_key):
                return f"https://example.test/{sport_key}"

            def _http_json(self, url):
                if url.endswith("/bad_sport"):
                    raise RuntimeError("404")
                return [
                    {
                        "home_team": "Greece",
                        "away_team": "Italy",
                        "bookmakers": [
                            {
                                "markets": [
                                    {
                                        "key": "h2h",
                                        "outcomes": [
                                            {"name": "Greece", "price": 2.2},
                                            {"name": "Draw", "price": 3.1},
                                            {"name": "Italy", "price": 3.0},
                                        ],
                                    }
                                ]
                            }
                        ],
                    }
                ]

        sync_config = SimpleNamespace(
            the_odds_api_token="token",
            the_odds_api_base_url="https://example.test",
            the_odds_api_sport_keys=("bad_sport",),
        )

        with patch(
            "scripts.check_market_api_capabilities.ThirdPartyOddsClient",
            FakeClient,
        ):
            result = check_market_api_capabilities.check_the_odds_api(sync_config)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sport_key"], "soccer_good")
        self.assertTrue(result["odds_available"])


if __name__ == "__main__":
    unittest.main()
