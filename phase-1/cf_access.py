"""Cloudflare Access JWT verification: defense in depth behind the tunnel.

Cloudflare Access signs every request it lets through with an RS256 JWT in the
Cf-Access-Jwt-Assertion header (and the CF_Authorization cookie). Checking it
at the origin means a tunnel hostname added without an Access application, or
a policy edited by mistake, still cannot reach Narad.

Enabled only when both NARAD_CF_ACCESS_TEAM_DOMAIN ("myteam.cloudflareaccess.com")
and NARAD_CF_ACCESS_AUD (the application's Audience tag; comma-separated for
several) are set. Signing keys come from https://<team>/cdn-cgi/access/certs,
cached in-process for an hour and refetched early (throttled) on an unknown kid.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

import jwt
from jwt.algorithms import RSAAlgorithm

_log = logging.getLogger("narad.cf_access")

_JWKS_TTL_S = 3600.0
_REFETCH_MIN_S = 10.0  # unknown-kid refetches; bounds a flood of made-up kids
_FETCH_TIMEOUT_S = 5.0
_LEEWAY_S = 60
_HEADER = "cf-access-jwt-assertion"
_COOKIE = "CF_Authorization"


@dataclass(frozen=True)
class AccessConfig:
    team: str
    audiences: tuple[str, ...]

    @property
    def issuer(self) -> str:
        return f"https://{self.team}"

    @property
    def certs_url(self) -> str:
        return f"{self.issuer}/cdn-cgi/access/certs"


def load_config(env: Mapping[str, str] = os.environ) -> AccessConfig | None:
    """The Access application to enforce, or None when either setting is unset."""
    team = env.get("NARAD_CF_ACCESS_TEAM_DOMAIN", "").strip().lower()
    team = team.removeprefix("https://").removeprefix("http://").strip("/")
    audiences = tuple(
        aud.strip() for aud in env.get("NARAD_CF_ACCESS_AUD", "").split(",") if aud.strip()
    )
    return AccessConfig(team, audiences) if team and audiences else None


def fetch_jwks(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "narad-cf-access"})
    with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT_S) as response:
        return json.loads(response.read(1_000_000))


def _parse_jwks(data: dict) -> dict[str, Any]:
    keys: dict[str, Any] = {}
    for jwk in data.get("keys") or []:
        if isinstance(jwk, dict) and jwk.get("kty") == "RSA" and isinstance(jwk.get("kid"), str):
            try:
                keys[jwk["kid"]] = RSAAlgorithm.from_jwk(jwk)
            except Exception:
                continue
    return keys


class _KeyCache:
    """kid → public key for one certs URL. Reads never touch the network."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (url, keys, fetched_at, attempted_at), replaced whole so reads need no lock.
        self._state: tuple[str, dict[str, Any], float, float] = ("", {}, -1e18, -1e18)

    def lookup(self, url: str, kid: str) -> tuple[Any | None, bool]:
        """(key or None, whether a fetch is due) from memory only."""
        cached_url, keys, fetched_at, attempted_at = self._state
        if cached_url != url:
            return None, True
        now = time.monotonic()
        key = keys.get(kid)
        if key is not None and now - fetched_at < _JWKS_TTL_S:
            return key, False
        return key, now - attempted_at >= _REFETCH_MIN_S

    def refresh(self, url: str) -> None:
        """Blocking fetch; run off the event loop. Concurrent callers share one fetch."""
        with self._lock:
            cached_url, keys, fetched_at, attempted_at = self._state
            if cached_url != url:
                keys, fetched_at, attempted_at = {}, -1e18, -1e18
            now = time.monotonic()
            if now - attempted_at < _REFETCH_MIN_S:
                return
            try:
                keys, fetched_at = _parse_jwks(fetch_jwks(url)), now
            except Exception as exc:  # keep serving the last good keys
                _log.warning("Cloudflare Access keys unavailable from %s: %s", url, exc)
            self._state = (url, keys, fetched_at, now)


_cache = _KeyCache()


async def verify_request(
    config: AccessConfig, headers: Mapping[str, str], cookies: Mapping[str, str]
) -> dict:
    """Claims of the request's valid Access JWT; raises jwt.InvalidTokenError."""
    token = headers.get(_HEADER) or cookies.get(_COOKIE) or ""
    if not token:
        raise jwt.InvalidTokenError("no Access token")
    kid = jwt.get_unverified_header(token).get("kid")
    if not isinstance(kid, str) or not kid:
        raise jwt.InvalidTokenError("Access token has no key id")
    key, fetch_due = _cache.lookup(config.certs_url, kid)
    if fetch_due:
        await asyncio.to_thread(_cache.refresh, config.certs_url)
        key = _cache.lookup(config.certs_url, kid)[0]
    if key is None:
        raise jwt.InvalidTokenError(f"unknown Access signing key {kid!r}")
    return jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        audience=list(config.audiences),
        # A list means exact membership; a bare string was a substring match
        # in PyJWT 2.10.0 (CVE-2024-53861).
        issuer=[config.issuer],
        leeway=_LEEWAY_S,
        options={"require": ["exp", "iss", "aud"]},
    )
