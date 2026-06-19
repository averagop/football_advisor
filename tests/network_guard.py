from __future__ import annotations

import http.client
import os
import socket
import urllib.request
from typing import Any


_INSTALLED = False


def _blocked_network_call(*args: Any, **kwargs: Any) -> None:
    raise RuntimeError("单元测试禁止访问真实网络。请使用 mock、fixture 或 live/smoke 测试。")


def install_unit_test_network_guard() -> None:
    """阻断单元测试中的常见真实网络入口。"""
    global _INSTALLED
    if _INSTALLED:
        return
    if os.environ.get("FOOTBALL_UNIT_TEST_NETWORK_BLOCK", "1") == "0":
        return

    urllib.request.urlopen = _blocked_network_call
    socket.create_connection = _blocked_network_call

    class BlockedHTTPConnection(http.client.HTTPConnection):
        def connect(self) -> None:
            _blocked_network_call()

    class BlockedHTTPSConnection(http.client.HTTPSConnection):
        def connect(self) -> None:
            _blocked_network_call()

    http.client.HTTPConnection = BlockedHTTPConnection
    http.client.HTTPSConnection = BlockedHTTPSConnection

    try:
        import requests
    except Exception:
        pass
    else:
        requests.sessions.Session.request = _blocked_network_call

    _INSTALLED = True
