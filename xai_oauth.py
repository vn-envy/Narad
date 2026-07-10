"""
xAI OAuth (Grok) — sign in with a SuperGrok / X Premium+ subscription.

Gives Narad a second full brain next to DeepSeek without a pasted API key.
The flow is standard OAuth 2.0 authorization-code + PKCE (S256) against
auth.x.ai, using the public Grok CLI client id. The resulting bearer token
works against the OpenAI-compatible https://api.x.ai/v1 endpoint, so the
whole routing stack stays untouched: we simply export it as XAI_API_KEY
(mirroring Kunji's apply_keys_to_env pattern) and LiteLLM's `xai/...`
models light up.

Wire-protocol notes (verified against the Grok CLI's public flow):
  * token exchange must include BOTH code_verifier AND the original
    code_challenge + code_challenge_method — xAI validates all three.
  * authorize URL carries plan=generic and a referrer tag.
  * refresh: grant_type=refresh_token + client_id + refresh_token.
  * all endpoints from discovery are pinned to HTTPS on x.ai / *.x.ai.

Token file: ~/.narad/config/xai_oauth.json (0600) — access_token,
refresh_token, expires_at. Never rendered back to the UI; status() exposes
booleans only. A real XAI_API_KEY env var (the .env escape hatch) wins.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlparse

from narad_config import CONFIG_DIR

_TOKEN_PATH = CONFIG_DIR / "xai_oauth.json"

CLIENT_ID = os.environ.get("NARAD_XAI_CLIENT_ID", "b1a00492-073a-47ea-816f-4c329264a828")
SCOPE = "openid profile email offline_access grok-cli:access api:access"
DISCOVERY_URL = "https://auth.x.ai/.well-known/openid-configuration"
_FALLBACK_AUTHORIZE = "https://auth.x.ai/oauth2/authorize"
_FALLBACK_TOKEN = "https://auth.x.ai/oauth2/token"
REFRESH_SKEW_SECONDS = 120

# In-flight logins: state → {verifier, challenge, redirect_uri, ts}
_PENDING: dict[str, dict[str, str | float]] = {}
_PENDING_TTL = 600.0


# ── Endpoint discovery (pinned to x.ai) ───────────────────────────────────────

def _pinned(url: str, fallback: str) -> str:
    """Only trust HTTPS endpoints on x.ai or a *.x.ai subdomain."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme == "https" and (host == "x.ai" or host.endswith(".x.ai")):
            return url
    except Exception:
        pass
    return fallback


def _endpoints() -> tuple[str, str]:
    """(authorize_url, token_url) — discovery with hard fallback."""
    try:
        import httpx

        resp = httpx.get(DISCOVERY_URL, timeout=10)
        if resp.status_code == 200:
            doc = resp.json()
            return (
                _pinned(doc.get("authorization_endpoint", ""), _FALLBACK_AUTHORIZE),
                _pinned(doc.get("token_endpoint", ""), _FALLBACK_TOKEN),
            )
    except Exception:
        pass
    return _FALLBACK_AUTHORIZE, _FALLBACK_TOKEN


# ── PKCE ──────────────────────────────────────────────────────────────────────

def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


# ── Token persistence ─────────────────────────────────────────────────────────

def _load_tokens() -> dict[str, Any]:
    try:
        data = json.loads(_TOKEN_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_tokens(tokens: dict[str, Any]) -> None:
    _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_PATH.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
    try:
        os.chmod(_TOKEN_PATH, 0o600)
    except OSError:
        pass


def _store_token_response(payload: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    previous = previous or {}
    tokens = {
        "access_token": payload.get("access_token", ""),
        # xAI may rotate or omit the refresh token on refresh — keep the old one.
        "refresh_token": payload.get("refresh_token") or previous.get("refresh_token", ""),
        "expires_at": time.time() + float(payload.get("expires_in", 3600)),
        "ts": time.time(),
    }
    _save_tokens(tokens)
    return tokens


# ── Login flow ────────────────────────────────────────────────────────────────

def start_login(redirect_uri: str) -> dict[str, str]:
    """Build the authorize URL. Returns {authorize_url, state}."""
    now = time.time()
    for state in [s for s, p in _PENDING.items() if now - float(p["ts"]) > _PENDING_TTL]:
        _PENDING.pop(state, None)

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    _PENDING[state] = {
        "verifier": verifier,
        "challenge": challenge,
        "redirect_uri": redirect_uri,
        "ts": now,
    }
    authorize_url, _ = _endpoints()
    # Param set mirrors the Hermes Agent loopback flow exactly — auth.x.ai
    # only auto-redirects to the loopback for this recognized referrer;
    # anything else falls back to the "copy this code" page.
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "nonce": secrets.token_urlsafe(16),
        "plan": "generic",
        "referrer": "hermes-agent",
    }
    return {"authorize_url": f"{authorize_url}?{urlencode(params)}", "state": state}


def finish_login(code: str, state: str) -> dict[str, Any]:
    """Exchange the authorization code. Raises ValueError/RuntimeError on failure."""
    pending = _PENDING.pop(state, None)
    if pending is None:
        raise ValueError("unknown or expired OAuth state — restart the sign-in")

    import httpx

    _, token_url = _endpoints()
    resp = httpx.post(
        token_url,
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": pending["redirect_uri"],
            # xAI quirk: the exchange validates verifier AND challenge together.
            "code_verifier": pending["verifier"],
            "code_challenge": pending["challenge"],
            "code_challenge_method": "S256",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"xAI token exchange failed: HTTP {resp.status_code} {resp.text[:200]}")
    payload = resp.json()
    if not payload.get("access_token"):
        raise RuntimeError("xAI token exchange returned no access_token")
    tokens = _store_token_response(payload)
    apply_to_env(force=True)
    return {"signed_in": True, "expires_at": tokens["expires_at"]}


def finish_login_input(raw: str) -> dict[str, Any]:
    """Finish sign-in from whatever the user pasted.

    xAI sometimes shows a "copy this code" page instead of redirecting to the
    loopback (it only auto-redirects for recognized referrers). The user may
    paste either the bare code from that page or the full callback URL.
      * full URL → extract code + state from the query string.
      * bare code → use the most recent pending login's state (Narad is
        single-user local, so "the newest login attempt" is unambiguous).
    """
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("nothing pasted — copy the code xAI showed you")

    code, state = raw, ""
    if "://" in raw or raw.startswith("localhost") or "?" in raw:
        try:
            from urllib.parse import parse_qs

            query = parse_qs(urlparse(raw).query)
            url_code = (query.get("code") or [""])[0]
            if url_code:
                code = url_code
                state = (query.get("state") or [""])[0]
        except Exception:
            pass  # fall through: treat the paste as a bare code

    if not state:
        if not _PENDING:
            raise ValueError("no sign-in in progress — click Sign in with Grok first")
        state = max(_PENDING, key=lambda s: float(_PENDING[s]["ts"]))
    return finish_login(code, state)


def _refresh(tokens: dict[str, Any]) -> dict[str, Any] | None:
    refresh_token = tokens.get("refresh_token", "")
    if not refresh_token:
        return None
    try:
        import httpx

        _, token_url = _endpoints()
        resp = httpx.post(
            token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        payload = resp.json()
        if not payload.get("access_token"):
            return None
        return _store_token_response(payload, previous=tokens)
    except Exception:
        return None


# ── Runtime access ────────────────────────────────────────────────────────────

def get_access_token() -> str | None:
    """Valid access token or None. Auto-refreshes near expiry and exports env."""
    tokens = _load_tokens()
    if not tokens.get("access_token"):
        return None
    if time.time() >= float(tokens.get("expires_at", 0)) - REFRESH_SKEW_SECONDS:
        tokens = _refresh(tokens)
        if not tokens:
            return None
    token = tokens["access_token"]
    if not os.environ.get("NARAD_XAI_ENV_LOCKED"):
        os.environ["XAI_API_KEY"] = token
    return token


def apply_to_env(*, force: bool = False) -> bool:
    """Export the OAuth token as XAI_API_KEY unless a real env var is set.

    Mirrors kunji.apply_keys_to_env: the .env escape hatch always wins —
    except right after an interactive sign-in (force=True).
    """
    if not force and os.environ.get("XAI_API_KEY", "").strip():
        return False
    tokens = _load_tokens()
    if not tokens.get("access_token"):
        return False
    token = get_access_token() if time.time() >= float(tokens.get("expires_at", 0)) - REFRESH_SKEW_SECONDS else tokens["access_token"]
    if not token:
        return False
    os.environ["XAI_API_KEY"] = token
    return True


def signed_in() -> bool:
    return bool(_load_tokens().get("access_token"))


def status() -> dict[str, Any]:
    tokens = _load_tokens()
    has = bool(tokens.get("access_token"))
    expires_at = float(tokens.get("expires_at", 0))
    return {
        "signed_in": has,
        "expires_at": expires_at if has else None,
        "expired": has and time.time() >= expires_at,
        "has_refresh": bool(tokens.get("refresh_token")),
    }


def disconnect() -> bool:
    """Remove tokens + env export. True if a session existed."""
    existed = signed_in()
    try:
        _TOKEN_PATH.unlink(missing_ok=True)
    except OSError:
        pass
    os.environ.pop("XAI_API_KEY", None)
    return existed
