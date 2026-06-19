from __future__ import annotations

import json
import unittest

from openwebui_tools.football_advisor_tools import Tools


class FakeHTTPResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(
            {
                "status": "success",
                "bets": 1,
                "roi": 0.1,
                "staged_win_rate": 1.0,
                "max_drawdown": 0.0,
                "equity_curve": [0.1],
            }
        ).encode("utf-8")


class OpenWebUIToolsTests(unittest.TestCase):
    def test_run_backtest_clamps_limit_before_request(self):
        tool = Tools()
        captured = {}

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeHTTPResponse()

        original_urlopen = __import__(
            "openwebui_tools.football_advisor_tools", fromlist=["urllib"]
        ).urllib.request.urlopen
        __import__(
            "openwebui_tools.football_advisor_tools", fromlist=["urllib"]
        ).urllib.request.urlopen = fake_urlopen
        try:
            report = tool.run_backtest(limit=20000)
        finally:
            __import__(
                "openwebui_tools.football_advisor_tools", fromlist=["urllib"]
            ).urllib.request.urlopen = original_urlopen

        self.assertEqual(captured["payload"]["limit"], 10000)
        self.assertEqual(captured["timeout"], 60)
        self.assertIn("回测结果", report)

    def test_predict_match_sends_only_query_and_mode(self):
        tool = Tools()
        captured = {}

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout

            class FakeResponse:
                def read(self):
                    return json.dumps({"report": "Prediction success."}).encode("utf-8")
                def __enter__(self): return self
                def __exit__(self, *args): pass
            return FakeResponse()

        original_urlopen = __import__(
            "openwebui_tools.football_advisor_tools", fromlist=["urllib"]
        ).urllib.request.urlopen
        __import__(
            "openwebui_tools.football_advisor_tools", fromlist=["urllib"]
        ).urllib.request.urlopen = fake_urlopen
        try:
            report = tool.predict_match(query="Arsenal vs Chelsea", mode="standard")
        finally:
            __import__(
                "openwebui_tools.football_advisor_tools", fromlist=["urllib"]
            ).urllib.request.urlopen = original_urlopen

        self.assertIn("query", captured["payload"])
        self.assertIn("mode", captured["payload"])
        self.assertEqual(len(captured["payload"].keys()), 2)
        self.assertEqual(captured["payload"]["query"], "Arsenal vs Chelsea")
        self.assertEqual(captured["payload"]["mode"], "standard")
        self.assertEqual(report, "Prediction success.")

    def test_predict_match_keeps_world_cup_query_payload_minimal(self):
        tool = Tools()
        captured = {}

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))

            class FakeResponse:
                def read(self):
                    return json.dumps({"report": "World Cup report."}).encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

            return FakeResponse()

        original_urlopen = __import__(
            "openwebui_tools.football_advisor_tools", fromlist=["urllib"]
        ).urllib.request.urlopen
        __import__(
            "openwebui_tools.football_advisor_tools", fromlist=["urllib"]
        ).urllib.request.urlopen = fake_urlopen
        try:
            report = tool.predict_match(query="USA vs Mexico", mode="deep")
        finally:
            __import__(
                "openwebui_tools.football_advisor_tools", fromlist=["urllib"]
            ).urllib.request.urlopen = original_urlopen

        self.assertEqual(captured["payload"], {"query": "USA vs Mexico", "mode": "deep"})
        self.assertEqual(report, "World Cup report.")

    def test_predict_match_sends_optional_match_parameters_when_present(self):
        tool = Tools()
        captured = {}

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))

            class FakeResponse:
                def read(self):
                    return json.dumps({"report": "Detailed report."}).encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

            return FakeResponse()

        original_urlopen = __import__(
            "openwebui_tools.football_advisor_tools", fromlist=["urllib"]
        ).urllib.request.urlopen
        __import__(
            "openwebui_tools.football_advisor_tools", fromlist=["urllib"]
        ).urllib.request.urlopen = fake_urlopen
        try:
            report = tool.predict_match(
                query="Greece vs Italy",
                mode="standard",
                home_team="Greece",
                away_team="Italy",
                kickoff_time="2026-06-08T03:00:00Z",
                match_id="M_ST_2040143",
                market="1x2",
            )
        finally:
            __import__(
                "openwebui_tools.football_advisor_tools", fromlist=["urllib"]
            ).urllib.request.urlopen = original_urlopen

        self.assertEqual(
            captured["payload"],
            {
                "query": "Greece vs Italy",
                "mode": "standard",
                "home_team": "Greece",
                "away_team": "Italy",
                "kickoff_time": "2026-06-08T03:00:00Z",
                "match_id": "M_ST_2040143",
                "market": "1x2",
            },
        )
        self.assertEqual(report, "Detailed report.")


if __name__ == "__main__":
    unittest.main()
