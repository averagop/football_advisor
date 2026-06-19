"""
URL 安全验证器 (URL Safety Validator)

阻断所有可能访问内部网络、本地服务和敏感资源的 URL：
  - localhost / 环回地址
  - RFC1918 私网地址
  - 链路本地地址
  - IPv6 环回、私网、链路本地、IPv4 映射地址
  - DNS 解析到私网的域名
  - 公网 URL 重定向到私网地址
  - 缺少主机名、包含用户凭据、非 80/443 端口
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 明确禁止的 IP 地址
_BLOCKED_IPS = {
    ipaddress.IPv4Address("169.254.169.254"),  # AWS/Azure 元数据服务
}


def _is_private_ip(addr: str) -> bool:
    """检查 IP 地址是否为私网/环回/链路本地。"""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False

    if ip in _BLOCKED_IPS:
        return True
    if ip.is_loopback:
        return True
    if ip.is_private:
        return True
    if ip.is_link_local:
        return True
    # IPv4-mapped IPv6 (::ffff:x.x.x.x)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            ipv4 = ip.ipv4_mapped
            if ipv4.is_loopback or ipv4.is_private or ipv4.is_link_local:
                return True
            if ipv4 in _BLOCKED_IPS:
                return True
    return False


def validate_url(url: str) -> tuple[bool, str]:
    """验证 URL 是否安全。
    
    Returns:
        (is_safe, reason) — is_safe=True 表示可以访问，False 表示不安全。
    """
    if not url or not url.strip():
        return False, "empty_url"

    parsed = urlparse(url)
    if not parsed.scheme:
        return False, "missing_scheme"
    if parsed.scheme not in {"http", "https"}:
        return False, f"unsupported_scheme:{parsed.scheme}"

    if not parsed.hostname:
        return False, "missing_hostname"

    # 检查端口
    if parsed.port is not None and parsed.port not in {80, 443}:
        return False, f"blocked_port:{parsed.port}"

    # 检查用户名/密码
    if parsed.username or parsed.password:
        return False, "contains_credentials"

    # 检查 hostname 是否为 IP 地址
    try:
        ipaddress.ip_address(parsed.hostname)
        is_ip = True
    except ValueError:
        is_ip = False

    if is_ip:
        if _is_private_ip(parsed.hostname):
            return False, f"private_ip:{parsed.hostname}"
        return True, "ok"

    # 主机名：DNS 解析并检查。解析失败时拒绝请求。
    try:
        addrinfo = socket.getaddrinfo(parsed.hostname, parsed.port or 80)
    except socket.gaierror:
        return False, f"dns_resolution_failed:{parsed.hostname}"
    except Exception:
        return False, f"dns_error:{parsed.hostname}"

    for info in addrinfo:
        sockaddr = info[4]
        addr = sockaddr[0]
        if _is_private_ip(addr):
            return False, f"resolves_to_private:{parsed.hostname}->{addr}"

    return True, "ok"


class SafeHTTPRedirectHandler(urllib.request.HTTPRedirectHandler):
    """自定义重定向处理器：每次跳转前重新校验目标 URL。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe, reason = validate_url(newurl)
        if not safe:
            raise urllib.error.URLError(f"redirect blocked: {reason} ({newurl})")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class SafeHTTPHandler(urllib.request.HTTPHandler):
    """自定义 HTTP 处理器：在请求前校验 URL。"""

    def http_open(self, req):
        _validate_request(req.full_url)
        return super().http_open(req)


class SafeHTTPSHandler(urllib.request.HTTPSHandler):
    """自定义 HTTPS 处理器：在请求前校验 URL。"""

    def https_open(self, req):
        _validate_request(req.full_url)
        return super().https_open(req)


def _validate_request(url: str) -> None:
    safe, reason = validate_url(url)
    if not safe:
        raise urllib.error.URLError(f"request blocked: {reason}")


def build_safe_urlopen():
    """构建安全的 urlopen 函数，内置每跳重定向校验。"""
    opener = urllib.request.build_opener(
        SafeHTTPRedirectHandler(),
        SafeHTTPHandler(),
        SafeHTTPSHandler(),
    )
    return opener.open
