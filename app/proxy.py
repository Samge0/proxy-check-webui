"""隧道出口探测：requests 直连目标端点，trust_env=False 保证出口=隧道出口而非本机系统代理。"""
from __future__ import annotations

import re
import time
from urllib.parse import quote_plus

import requests

# 兜底 IP 源：主端点失败时按序尝试；全部厂商共用同一列表，保证对比口径一致
FALLBACK_ENDPOINTS = ["https://api.ipify.org"]

_HEADERS = {"User-Agent": "curl/8.11.1"}

_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def build_proxy_url(vendor: dict) -> str:
    raw = (vendor.get("raw_url") or "").strip()
    if raw:
        return _normalize_raw(raw)
    scheme = "socks5h" if vendor.get("protocol") == "socks5" else "http"
    host = (vendor.get("host") or "").strip()
    port = vendor.get("port") or ""
    username = quote_plus(vendor.get("username") or "")
    password = quote_plus(vendor.get("password") or "")
    if username:
        return f"{scheme}://{username}:{password}@{host}:{port}"
    return f"{scheme}://{host}:{port}"


def _normalize_raw(raw: str) -> str:
    """完整代理串兼容三形态：完整 URL / host:port:user:pass / host:port。"""
    if "://" in raw:
        return raw
    parts = raw.split(":")
    if len(parts) == 4:
        host, port, user, pwd = parts
        return f"http://{quote_plus(user)}:{quote_plus(pwd)}@{host}:{port}"
    if len(parts) == 2:
        return f"http://{raw}"
    return raw


def probe_ip(vendor: dict, target_url: str, timeout: int, fallbacks: list[str] | None = None) -> dict:
    """经厂商隧道请求 IP 回显端点。返回 {ok, ip, latency_ms, error, endpoint}。"""
    proxy_url = build_proxy_url(vendor)
    proxies = {"http": proxy_url, "https": proxy_url}
    endpoints = [target_url] + list(fallbacks or [])
    latency_ms = 0
    error = ""
    for url in endpoints:
        started = time.monotonic()
        try:
            # 每次探测必须全新连接：会话复用会让「按 TCP 连接绑定出口」的隧道
            # 反复命中同一 IP，低估换 IP 率（2026-11-25 实测巨量/KDL 均有此现象）
            with requests.Session() as session:
                session.trust_env = False
                resp = session.get(url, proxies=proxies, headers=_HEADERS, timeout=timeout)
            latency_ms = int((time.monotonic() - started) * 1000)
            found = _IPV4_RE.search(resp.text)
            if resp.status_code == 200 and found:
                payload = {
                    "ok": 1,
                    "ip": found.group(0),
                    "latency_ms": latency_ms,
                    "error": "",
                    "endpoint": url,
                }
                return payload
            if resp.status_code != 200:
                error = f"HTTP {resp.status_code} body={resp.text[:60]!r}"
            else:
                error = f"响应无IPv4: {resp.text[:60]!r}"
        except requests.RequestException as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            error = f"{type(exc).__name__}: {str(exc)[:150]}"
    payload = {
        "ok": 0,
        "ip": "",
        "latency_ms": latency_ms,
        "error": error,
        "endpoint": endpoints[0],
    }
    return payload
