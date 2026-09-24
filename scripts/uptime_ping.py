#!/usr/bin/env python3
"""One uptime check for the family pilot; launchd runs it every 5 minutes (com.narad.uptime).

    .venv/bin/python scripts/uptime_ping.py           # check, append to NARAD_HOME/ops/uptime.jsonl
    .venv/bin/python scripts/uptime_ping.py --print   # and print the record

local   GET http://127.0.0.1:$NARAD_PORT/health must return Narad's own answer.
public  With NARAD_PUBLIC_URL set, GET <public url>/health through Cloudflare.
        Cloudflare Access sits in front, so an unauthenticated probe normally gets
        Access's redirect to its login page (or a 401/403) rather than /health.
        That counts as the tunnel being up, recorded as reached=access. Access
        answers at Cloudflare's edge, so it proves DNS and the Access app but not
        the tunnel itself; Narad's own /health answer (reached=health) proves the
        whole path. Get that with an Access Bypass policy on /health (README,
        Family access with Cloudflare Access, step 5) or a service token in
        NARAD_UPTIME_CF_CLIENT_ID / NARAD_UPTIME_CF_CLIENT_SECRET. A connection
        error, 502/504/52x, 530 or Cloudflare error 1033 means the tunnel is down.

status  up          local /health answered and the public path (if checked) was reached
        app_down    local /health failed: Narad itself is down
        tunnel_down Narad answered locally but the public path failed

With NARAD_UPTIME_PING_URL set (a healthchecks.io-style URL) an `up` check pings
it and anything else pings <url>/fail, with only the status as the body; the
service alerts you when pings stop, which also covers the Mac being off.

Standard library only. Nothing here reads or sends family data.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

LOCAL_TIMEOUT_S = 10
PUBLIC_TIMEOUT_S = 15
_USER_AGENT = "narad-uptime/1"
_TUNNEL_DOWN_HTTP = {502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527, 530}
_CF_ERROR = re.compile(rb"error code:?\s*(\d{4})|Error\s+(\d{4})", re.IGNORECASE)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep 3xx responses: an Access redirect is the answer we want to see."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _error_kind(exc: BaseException) -> str:
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(reason, socket.gaierror):
        return "dns"
    if isinstance(reason, ConnectionRefusedError):
        return "refused"
    if isinstance(reason, ssl.SSLError):
        return "tls"
    return "connection"


def fetch(url: str, *, timeout: float, headers: dict[str, str] | None = None,
          use_proxy: bool = True) -> dict[str, Any]:
    """GET without following redirects: {http, headers, body (first 4 KB), error, ms}."""
    handlers: list[Any] = [_NoRedirect()]
    if not use_proxy:
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, **(headers or {})})
    started = time.monotonic()
    result: dict[str, Any] = {"http": None, "headers": {}, "body": b"", "error": None}
    try:
        with opener.open(request, timeout=timeout) as response:
            result.update(http=response.status, body=response.read(4096),
                          headers={k.lower(): v for k, v in response.headers.items()})
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read(4096)
        except Exception:
            pass
        result.update(http=exc.code, body=body,
                      headers={k.lower(): v for k, v in (exc.headers or {}).items()})
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        result["error"] = _error_kind(exc)
    result["ms"] = int((time.monotonic() - started) * 1000)
    return result


def _is_narad_health(response: dict[str, Any]) -> bool:
    if response.get("http") != 200:
        return False
    try:
        payload = json.loads(response.get("body") or b"")
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("agent") == "Narad"


def classify_local(response: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": _is_narad_health(response),
        "http": response.get("http"),
        "ms": response.get("ms"),
        "error": response.get("error"),
    }


def classify_public(response: dict[str, Any]) -> dict[str, Any]:
    """reached = health (Narad answered), access (Cloudflare Access answered) or None."""
    status = response.get("http")
    headers = response.get("headers") or {}
    body = response.get("body") or b""
    reached = None
    match = _CF_ERROR.search(body)
    cf_error = (match.group(1) or match.group(2)).decode() if match else None
    location = str(headers.get("location") or "").lower()
    from_cloudflare = "cf-ray" in headers or str(headers.get("server") or "").lower() == "cloudflare"
    if _is_narad_health(response):
        reached = "health"
    elif status in (301, 302, 303, 307, 308) and (
        "cloudflareaccess.com" in location or "/cdn-cgi/access/" in location
    ):
        reached = "access"
    elif status in (401, 403) and cf_error is None and (
        from_cloudflare or b"cloudflareaccess" in body.lower() or b"cloudflare access" in body.lower()
    ):
        reached = "access"
    error = response.get("error")
    if reached is None and error is None:
        # 502/530/1033 and friends: Cloudflare could not reach cloudflared or the origin.
        error = "tunnel" if status in _TUNNEL_DOWN_HTTP or cf_error == "1033" else "unexpected_http"
    return {"reached": reached, "http": status, "ms": response.get("ms"), "error": error, "cf_error": cf_error}


def overall_status(local: dict[str, Any], public: dict[str, Any] | None) -> str:
    if not local.get("ok"):
        return "app_down"
    if public is None or public.get("reached"):
        return "up"
    return "tunnel_down"


def _access_headers() -> dict[str, str]:
    client_id = os.environ.get("NARAD_UPTIME_CF_CLIENT_ID", "").strip()
    secret = os.environ.get("NARAD_UPTIME_CF_CLIENT_SECRET", "").strip()
    if client_id and secret:
        return {"CF-Access-Client-Id": client_id, "CF-Access-Client-Secret": secret}
    return {}


def ping(url: str, status: str, detail: str) -> str:
    target = url.rstrip("/") + ("" if status == "up" else "/fail")
    body = f"{status} {detail}".strip().encode()[:200]
    request = urllib.request.Request(target, data=body, method="POST",
                                     headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read(64)
        return "sent"
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        return "error"


def check() -> dict[str, Any]:
    port = os.environ.get("NARAD_PORT", "8000").strip() or "8000"
    local = classify_local(fetch(f"http://127.0.0.1:{port}/health", timeout=LOCAL_TIMEOUT_S, use_proxy=False))
    public_url = os.environ.get("NARAD_PUBLIC_URL", "").strip().rstrip("/")
    public = None
    if public_url:
        public = classify_public(fetch(f"{public_url}/health", timeout=PUBLIC_TIMEOUT_S, headers=_access_headers()))
    status = overall_status(local, public)
    now = time.time()
    record: dict[str, Any] = {
        "t": round(now, 3),
        "ts": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
        "status": status,
        "local": local,
        "public": public,
        "ping": None,
    }
    ping_url = os.environ.get("NARAD_UPTIME_PING_URL", "").strip()
    if ping_url:
        detail = ""
        if status == "app_down":
            detail = f"local={local.get('http') or local.get('error')}"
        elif status == "tunnel_down" and public:
            detail = f"public={public.get('http') or public.get('error')}" + (
                f" cf={public['cf_error']}" if public.get("cf_error") else ""
            )
        record["ping"] = ping(ping_url, status, detail)
    return record


def append(record: dict[str, Any]) -> Path:
    home = Path(os.environ.get("NARAD_HOME") or Path.home() / ".narad").expanduser()
    path = home / "ops" / "uptime.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One Narad uptime check.")
    parser.add_argument("--print", action="store_true", help="print the record as JSON")
    args = parser.parse_args(argv)
    record = check()
    append(record)
    if args.print:
        print(json.dumps(record, indent=2))
    return 0 if record["status"] == "up" else 1


if __name__ == "__main__":
    sys.exit(main())
