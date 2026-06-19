from __future__ import annotations

import unittest
from datetime import datetime, timezone

from scripts import smoke_test_multi_source_sync


class SmokeTestMultiSourceSyncTests(unittest.TestCase):
    def test_delta_requires_current_run_inserted_rows(self):
        before = {"staging.stg_match_schedule": 4}
        after_same = {"staging.stg_match_schedule": 4}
        after_new = {"staging.stg_match_schedule": 5}

        self.assertFalse(
            smoke_test_multi_source_sync._has_new_rows(
                before,
                after_same,
                "staging.stg_match_schedule",
            )
        )
        self.assertTrue(
            smoke_test_multi_source_sync._has_new_rows(
                before,
                after_new,
                "staging.stg_match_schedule",
            )
        )

    def test_core_table_change_accepts_update_without_count_delta(self):
        before_counts = {"core.fact_match_schedule": 10}
        after_counts = {"core.fact_match_schedule": 10}
        before_timestamps = {
            "core.fact_match_schedule": datetime(2026, 6, 1, tzinfo=timezone.utc)
        }
        after_timestamps = {
            "core.fact_match_schedule": datetime(2026, 6, 2, tzinfo=timezone.utc)
        }

        self.assertTrue(
            smoke_test_multi_source_sync._table_changed(
                before_counts,
                after_counts,
                before_timestamps,
                after_timestamps,
                "core.fact_match_schedule",
            )
        )

    def test_api_football_missing_odds_is_warning_not_pass(self):
        self.assertEqual(
            smoke_test_multi_source_sync._api_football_odds_check_state(0),
            "warn",
        )
        self.assertEqual(
            smoke_test_multi_source_sync._api_football_odds_check_state(2),
            "pass",
        )


if __name__ == "__main__":
    unittest.main()
