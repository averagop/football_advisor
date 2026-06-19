from __future__ import annotations

import unittest

from scripts import walkthrough_e2e


class WalkthroughE2ETests(unittest.TestCase):
    def test_cases_match_requested_four_fixtures(self):
        self.assertEqual(
            walkthrough_e2e.CASES,
            (
                ("WC2026_M010", "Germany", "Curacao"),
                ("WC2026_M011", "Netherlands", "Japan"),
                ("WC2026_M009", "Ivory Coast", "Ecuador"),
                ("WC2026_M012", "Sweden", "Tunisia"),
            ),
        )

    def test_special_markets_use_detail_odds_table(self):
        for market in ("CRS", "TTG", "HAFU"):
            self.assertEqual(
                walkthrough_e2e.MARKET_ODDS[market][0],
                "core.fact_sporttery_odds_detail",
            )

    def test_production_ready_requires_full_readiness(self):
        audit = {"M1": {"passed": True}}
        evidence = {
            "cases": {
                "M1": {
                    "runs": [
                        {
                            "completed": True,
                            "readiness_ready": True,
                            "critical_cache_reused": True,
                            "no_bet": False,
                        }
                    ]
                }
            }
        }

        self.assertEqual(
            walkthrough_e2e._determine_conclusion(
                [], audit, evidence, [1.0]
            ),
            "PRODUCTION_READY",
        )

    def test_internal_pipeline_failure_is_failed(self):
        self.assertEqual(
            walkthrough_e2e._determine_conclusion(
                ["M1: RuntimeError"], {}, {"cases": {}}, []
            ),
            "FAILED",
        )


if __name__ == "__main__":
    unittest.main()
