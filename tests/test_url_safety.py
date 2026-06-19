from __future__ import annotations

import socket
import unittest
import urllib.error
from unittest.mock import patch
from urllib.request import Request

from football_advisor.url_safety import (
    SafeHTTPHandler,
    SafeHTTPRedirectHandler,
    SafeHTTPSHandler,
    build_safe_urlopen,
    validate_url,
)


class UrlSafetyTests(unittest.TestCase):
    def test_dns_resolution_failure_is_rejected(self):
        with patch("socket.getaddrinfo", side_effect=socket.gaierror()):
            safe, reason = validate_url("https://unresolved.example")

        self.assertFalse(safe)
        self.assertEqual(reason, "dns_resolution_failed:unresolved.example")

    def test_non_standard_port_remains_blocked(self):
        safe, reason = validate_url("https://example.com:8443/news")

        self.assertFalse(safe)
        self.assertEqual(reason, "blocked_port:8443")

    def test_http_handlers_revalidate_before_connecting(self):
        for handler, method, url in (
            (SafeHTTPHandler(), "http_open", "http://example.com/news"),
            (SafeHTTPSHandler(), "https_open", "https://example.com/news"),
        ):
            with self.subTest(method=method):
                with patch(
                    "football_advisor.url_safety.validate_url",
                    return_value=(False, "private_ip:127.0.0.1"),
                ):
                    with self.assertRaises(urllib.error.URLError):
                        getattr(handler, method)(Request(url))

    def test_safe_urlopen_registers_redirect_http_and_https_handlers(self):
        opener_open = build_safe_urlopen()
        handler_types = {type(handler) for handler in opener_open.__self__.handlers}

        self.assertIn(SafeHTTPRedirectHandler, handler_types)
        self.assertIn(SafeHTTPHandler, handler_types)
        self.assertIn(SafeHTTPSHandler, handler_types)


if __name__ == "__main__":
    unittest.main()
