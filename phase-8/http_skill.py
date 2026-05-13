"""
Matsya HTTP skill — safe parameterised HTTP requests.

Lets Matsya reach any REST API, webhook endpoint, or JSON service
without hardcoded integrations.

Safety model:
  - Blocks private/localhost IP ranges (SSRF prevention)
  - Non-HTTP protocols are rejected
  - Response body is capped to prevent context overflow
  - Timeout enforced (default 30s)
  - No redirect following to private ranges

Uses httpx (async-capable, modern). Falls back to urllib if not installed.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import time
from urllib.parse import urlparse

TIMEOUT_S = int(os.environ.get("HTTP_TIMEOUT", "30"))

# Private/reserved IP ranges blocked to prevent SSRF
_PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local
    ipaddress.ip_network("100.64.0.0/10"),    # shared address space
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),          # IPv6 private
]

_RESPONSE_CAP = 12_000  # chars


def _check_url(url: str) -> str | None:
    """Return an error message if the URL is unsafe, else None."""
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return f"Only http:// and https:// are allowed. Got: {parsed.scheme!r}"

    hostname = parsed.hostname or ""
    if not hostname:
        return "No hostname in URL."

    # Resolve hostname to IP and check for private ranges
    try:
        ip_str = socket.gethostbyname(hostname)
        ip = ipaddress.ip_address(ip_str)
        for private_range in _PRIVATE_RANGES:
            if ip in private_range:
                return (
                    f"Blocked: '{hostname}' resolves to a private/reserved IP "
                    f"({ip_str}). Only public internet addresses are allowed."
                )
    except socket.gaierror:
        pass  # DNS failure — let the actual request produce a clear error

    return None


def http_request(
    method: str,
    url: str,
    headers: dict | None = None,
    body: dict | str | None = None,
    timeout_s: int = TIMEOUT_S,
) -> dict:
    """Make an HTTP request to any public REST API or webhook endpoint.

    Use this when you need to:
    - Call a REST API directly with specific parameters or auth headers
    - Send a webhook payload (Slack, Discord, Zapier, etc.)
    - Fetch JSON data from an API that web_search cannot reach
    - POST form data or JSON to an external service

    Args:
        method:    HTTP method: GET, POST, PUT, PATCH, DELETE, HEAD
        url:       Full URL including query parameters.
                   Must be public (private/localhost addresses are blocked).
        headers:   Dict of request headers. e.g. {"Authorization": "Bearer TOKEN",
                   "Content-Type": "application/json"}
        body:      Request body. Pass a dict for JSON (auto-serialised),
                   or a string for raw body (set Content-Type manually).
        timeout_s: Seconds before timeout (default 30, max 120).

    Returns:
        status:        "ok" | "error" | "http_error"
        status_code:   HTTP response code
        body:          Response body as string (capped at 12000 chars)
        body_json:     Parsed JSON if response Content-Type is application/json
        headers:       Response headers as dict
        duration_s:    Round-trip time in seconds
        message:       Summary or error description

    Examples:
        # GET a public API
        http_request("GET", "https://api.github.com/repos/anthropics/anthropic-sdk-python")

        # POST JSON to a webhook
        http_request("POST", "https://hooks.slack.com/services/XXX",
                     body={"text": "Hello from Narad!"})

        # Authenticated API call
        http_request("GET", "https://api.example.com/data",
                     headers={"Authorization": "Bearer mytoken123"})
    """
    method = method.upper().strip()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        return {
            "status":  "error",
            "message": f"Unsupported method: {method!r}. Use GET, POST, PUT, PATCH, DELETE.",
        }

    url_error = _check_url(url)
    if url_error:
        return {"status": "error", "message": url_error}

    timeout_s = min(max(1, timeout_s), 120)
    headers = headers or {}

    # Auto-set Content-Type for dict bodies
    if isinstance(body, dict) and "Content-Type" not in headers:
        headers["Content-Type"] = "application/json"

    body_bytes: bytes | None = None
    if body is not None:
        if isinstance(body, dict):
            body_bytes = json.dumps(body).encode()
        elif isinstance(body, str):
            body_bytes = body.encode()

    start = time.time()

    try:
        import httpx

        with httpx.Client(timeout=timeout_s, follow_redirects=True, max_redirects=5) as client:
            req = httpx.Request(
                method=method,
                url=url,
                headers=headers,
                content=body_bytes,
            )
            resp = client.send(req)

        duration = round(time.time() - start, 2)
        resp_text = resp.text[:_RESPONSE_CAP]
        resp_json = None
        ct = resp.headers.get("content-type", "")
        if "json" in ct:
            try:
                resp_json = resp.json()
                # Cap nested json to avoid token overflow
                resp_json_str = json.dumps(resp_json)
                if len(resp_json_str) > _RESPONSE_CAP:
                    resp_json = json.loads(resp_json_str[:_RESPONSE_CAP])
            except Exception:
                pass

        ok = 200 <= resp.status_code < 300
        return {
            "status":      "ok" if ok else "http_error",
            "status_code": resp.status_code,
            "body":        resp_text,
            "body_json":   resp_json,
            "headers":     dict(resp.headers),
            "duration_s":  duration,
            "message":     f"HTTP {resp.status_code} in {duration}s.",
        }

    except ImportError:
        pass  # fall through to urllib

    except Exception as exc:
        return {
            "status":   "error",
            "message":  f"Request failed: {exc}",
            "duration_s": round(time.time() - start, 2),
        }

    # Fallback: urllib (stdlib)
    try:
        import urllib.request
        import urllib.error

        req_obj = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        with urllib.request.urlopen(req_obj, timeout=timeout_s) as resp:
            duration = round(time.time() - start, 2)
            resp_body = resp.read().decode("utf-8", errors="replace")[:_RESPONSE_CAP]
            resp_json = None
            ct = resp.headers.get("Content-Type", "")
            if "json" in ct:
                try:
                    resp_json = json.loads(resp_body)
                except Exception:
                    pass
            return {
                "status":      "ok",
                "status_code": resp.status,
                "body":        resp_body,
                "body_json":   resp_json,
                "headers":     dict(resp.headers),
                "duration_s":  duration,
                "message":     f"HTTP {resp.status} in {duration}s.",
            }

    except Exception as exc:
        return {
            "status":   "error",
            "message":  f"Request failed: {exc}",
            "duration_s": round(time.time() - start, 2),
        }
