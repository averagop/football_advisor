import unittest

from scripts import smoke_test_provider_apis


class SmokeTestProviderApisTest(unittest.TestCase):
    def test_sporttery_missing_base_url_does_not_configure_request(self):
        result = smoke_test_provider_apis._check_sporttery({})

        self.assertEqual("SportteryOfficialWeb", result["provider"])
        self.assertFalse(result["configured"])
        self.assertEqual("missing_base_url", result["status"])
        self.assertEqual(
            "gateway/uniform/football/getMatchCalculatorV1.qry?channel=c&poolCode=hhad,had",
            result["endpoint"],
        )

    def test_sporttery_configured_uses_official_match_calculator_api(self):
        captured = {}

        def fake_get_json_count(provider, url, selector, endpoint, headers=None):
            captured.update(
                provider=provider,
                url=url,
                selector=selector,
                endpoint=endpoint,
                headers=headers,
            )
            return {
                "provider": provider,
                "configured": True,
                "status": "ok",
                "http_status": 200,
                "count": 1,
                "endpoint": endpoint,
            }

        original = smoke_test_provider_apis._get_json_count
        smoke_test_provider_apis._get_json_count = fake_get_json_count
        try:
            result = smoke_test_provider_apis._check_sporttery(
                {"FOOTBALL_SPORTTERY_BASE_URL": "https://www.sporttery.cn/"}
            )
        finally:
            smoke_test_provider_apis._get_json_count = original

        self.assertEqual("ok", result["status"])
        self.assertEqual(
            "https://webapi.sporttery.cn/gateway/uniform/football/"
            "getMatchCalculatorV1.qry?channel=c&poolCode=hhad,had",
            captured["url"],
        )
        self.assertEqual("value.matchInfoList", captured["selector"])
        self.assertEqual(
            "gateway/uniform/football/getMatchCalculatorV1.qry?channel=c&poolCode=hhad,had",
            captured["endpoint"],
        )

    def test_football_data_missing_token_does_not_configure_request(self):
        result = smoke_test_provider_apis._check_football_data({}, "2026-06-02")

        self.assertEqual("football-data.org", result["provider"])
        self.assertFalse(result["configured"])
        self.assertEqual("missing_token", result["status"])
        self.assertEqual("competitions/", result["endpoint"])

    def test_api_football_missing_token_does_not_configure_request(self):
        result = smoke_test_provider_apis._check_api_football({}, "2026-06-02")

        self.assertEqual("API-Football", result["provider"])
        self.assertFalse(result["configured"])
        self.assertEqual("missing_token", result["status"])
        self.assertEqual("fixtures?date=2026-06-02", result["endpoint"])

    def test_header_prefixed_token_is_rejected_before_request(self):
        result = smoke_test_provider_apis._check_football_data(
            {"FOOTBALL_DATA_API_TOKEN": "X-Auth-Token: secret-token"},
            "2026-06-02",
        )

        self.assertEqual("football-data.org", result["provider"])
        self.assertTrue(result["configured"])
        self.assertEqual("invalid_token_format", result["status"])
        self.assertEqual("n/a", result["http_status"])

    def test_authorized_headers_do_not_leak_into_endpoint(self):
        captured = {}

        def fake_get_json_count(provider, url, selector, endpoint, headers=None):
            captured.update(
                provider=provider,
                url=url,
                selector=selector,
                endpoint=endpoint,
                headers=headers,
            )
            return {
                "provider": provider,
                "configured": True,
                "status": "ok",
                "http_status": 200,
                "count": 0,
                "endpoint": endpoint,
            }

        original = smoke_test_provider_apis._get_json_count
        smoke_test_provider_apis._get_json_count = fake_get_json_count
        try:
            result = smoke_test_provider_apis._check_api_football(
                {"API_FOOTBALL_TOKEN": "secret-token"},
                "2026-06-02",
            )
        finally:
            smoke_test_provider_apis._get_json_count = original

        self.assertEqual("ok", result["status"])
        self.assertEqual({"x-apisports-key": "secret-token"}, captured["headers"])
        self.assertNotIn("secret-token", captured["endpoint"])


if __name__ == "__main__":
    unittest.main()
