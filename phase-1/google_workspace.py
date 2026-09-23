"""Local Google Workspace OAuth with incremental, per-service consent.

The OAuth client belongs to the Narad installation. User refresh tokens stay in
the OS keychain when available and fall back to an owner-only local file.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from narad_config import CONFIG_DIR
from profile_context import current_profile_id, profile_data_path, validate_profile_id

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
_TOKEN_PATH = CONFIG_DIR / "google_workspace_token.json"
_CLIENT_CONFIG_PATH = CONFIG_DIR / "google_oauth_client.json"
_SERVICE = "narad-google-workspace"
_CLIENT_SERVICE = "narad-google-oauth-client"
_PENDING: dict[str, dict[str, Any]] = {}

BASE_SCOPES = {"openid", "email", "profile"}
SERVICE_SCOPES: dict[str, dict[str, set[str]]] = {
    "gmail": {
        "read": {"https://www.googleapis.com/auth/gmail.readonly"},
        "write": {
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.modify",
        },
    },
    "calendar": {
        "read": {"https://www.googleapis.com/auth/calendar.events.readonly"},
        "write": {"https://www.googleapis.com/auth/calendar.events"},
    },
    "drive": {
        "read": {"https://www.googleapis.com/auth/drive.readonly"},
        # drive.file limits writes to files created/opened through Narad.
        "write": {
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/drive.file",
        },
    },
    "photos": {
        # Google removed whole-library readonly access in March 2025.
        "read": {"https://www.googleapis.com/auth/photospicker.mediaitems.readonly"},
        "write": {
            "https://www.googleapis.com/auth/photospicker.mediaitems.readonly",
            "https://www.googleapis.com/auth/photoslibrary.appendonly",
            "https://www.googleapis.com/auth/photoslibrary.edit.appcreateddata",
        },
    },
}


def _client_config() -> tuple[str, str]:
    env = (
        os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip(),
        os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip(),
    )
    if all(env):
        return env
    raw = ""
    try:
        import keyring

        raw = keyring.get_password(_CLIENT_SERVICE, "installation") or ""
    except Exception:
        pass
    if not raw:
        try:
            raw = _CLIENT_CONFIG_PATH.read_text(encoding="utf-8")
        except OSError:
            pass
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {}
    return str(payload.get("client_id") or "").strip(), str(payload.get("client_secret") or "").strip()


def configure_client(client_id: str, client_secret: str) -> dict[str, Any]:
    clean_id = str(client_id or "").strip()
    clean_secret = str(client_secret or "").strip()
    if len(clean_id) < 20 or not clean_id.endswith(".apps.googleusercontent.com"):
        raise ValueError("Enter a valid Google OAuth web client ID")
    if len(clean_secret) < 12:
        raise ValueError("Enter the Google OAuth client secret")
    raw = json.dumps({"client_id": clean_id, "client_secret": clean_secret})
    stored = False
    try:
        import keyring

        keyring.set_password(_CLIENT_SERVICE, "installation", raw)
        stored = keyring.get_password(_CLIENT_SERVICE, "installation") == raw
    except Exception:
        pass
    if not stored:
        _CLIENT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CLIENT_CONFIG_PATH.write_text(raw, encoding="utf-8")
        try:
            os.chmod(_CLIENT_CONFIG_PATH, 0o600)
        except OSError:
            pass
    return {"configured": True, "storage": "keychain" if stored else "owner_only_file"}


def _profile_id(user_id: str | None = None) -> str:
    return validate_profile_id(user_id or current_profile_id())


def _token_path(user_id: str | None = None) -> Path:
    return profile_data_path(
        "google_workspace_token.json",
        profile_id=_profile_id(user_id),
        legacy_default=_TOKEN_PATH,
    )


def _keyring_get(user_id: str | None = None) -> str | None:
    try:
        import keyring
        profile_id = _profile_id(user_id)
        value = keyring.get_password(_SERVICE, f"token:{profile_id}")
        if value is None and profile_id == "default":
            value = keyring.get_password(_SERVICE, "token")
        return value
    except Exception:
        return None


def _keyring_set(value: str, user_id: str | None = None) -> bool:
    try:
        import keyring
        account = f"token:{_profile_id(user_id)}"
        keyring.set_password(_SERVICE, account, value)
        return keyring.get_password(_SERVICE, account) == value
    except Exception:
        return False


def _load_token(user_id: str | None = None) -> dict[str, Any]:
    raw = _keyring_get(user_id)
    if raw:
        try:
            return json.loads(raw)
        except ValueError:
            pass
    try:
        return json.loads(_token_path(user_id).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_token(token: dict[str, Any], user_id: str | None = None) -> None:
    raw = json.dumps(token)
    path = _token_path(user_id)
    if _keyring_set(raw, user_id):
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    path.chmod(0o600)


def _delete_token(user_id: str | None = None) -> None:
    try:
        import keyring
        profile_id = _profile_id(user_id)
        keyring.delete_password(_SERVICE, f"token:{profile_id}")
        if profile_id == "default":
            try:
                keyring.delete_password(_SERVICE, "token")
            except Exception:
                pass
    except Exception:
        pass
    _token_path(user_id).unlink(missing_ok=True)


def _post_form(url: str, data: dict[str, str], timeout: float = 20.0) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw.decode()) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"Google OAuth returned HTTP {exc.code}: {detail[:300]}") from exc


def requested_scopes(services: list[str], access: str) -> set[str]:
    if access not in {"read", "write"}:
        raise ValueError("access must be read or write")
    unknown = set(services) - set(SERVICE_SCOPES)
    if unknown:
        raise ValueError(f"unknown Google services: {', '.join(sorted(unknown))}")
    scopes = set(BASE_SCOPES)
    for service in services:
        scopes.update(SERVICE_SCOPES[service][access])
    return scopes


def start_login(
    redirect_uri: str,
    services: list[str],
    access: str = "read",
    user_id: str | None = None,
) -> dict[str, Any]:
    profile_id = _profile_id(user_id)
    client_id, client_secret = _client_config()
    if not client_id or not client_secret:
        raise RuntimeError("Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET to enable Google")
    scopes = requested_scopes(services, access)
    existing = set(str(_load_token(profile_id).get("scope") or "").split())
    scopes.update(existing)
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    _PENDING[state] = {
        "verifier": verifier,
        "redirect_uri": redirect_uri,
        "services": services,
        "access": access,
        "user_id": profile_id,
        "created_at": time.time(),
    }
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(sorted(scopes)),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return {"authorize_url": f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}", "state": state}


def finish_login(code: str, state: str) -> dict[str, Any]:
    pending = _PENDING.pop(state, None)
    if not pending or time.time() - pending["created_at"] > 600:
        raise ValueError("Google sign-in state is invalid or expired")
    client_id, client_secret = _client_config()
    token = _post_form(TOKEN_URL, {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": pending["redirect_uri"],
        "grant_type": "authorization_code",
        "code_verifier": pending["verifier"],
    })
    user_id = _profile_id(str(pending.get("user_id") or "default"))
    previous = _load_token(user_id)
    if not token.get("refresh_token") and previous.get("refresh_token"):
        token["refresh_token"] = previous["refresh_token"]
    token["expires_at"] = time.time() + int(token.get("expires_in") or 3600) - 60
    token["connected_at"] = time.time()
    _save_token(token, user_id)
    return status(user_id)


def access_token(user_id: str | None = None) -> str:
    profile_id = _profile_id(user_id)
    token = _load_token(profile_id)
    if token.get("access_token") and float(token.get("expires_at") or 0) > time.time():
        return str(token["access_token"])
    refresh = str(token.get("refresh_token") or "")
    client_id, client_secret = _client_config()
    if not refresh or not client_id or not client_secret:
        raise RuntimeError("Google is not connected")
    refreshed = _post_form(TOKEN_URL, {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    })
    token.update(refreshed)
    token["refresh_token"] = refresh
    token["expires_at"] = time.time() + int(refreshed.get("expires_in") or 3600) - 60
    _save_token(token, profile_id)
    return str(token["access_token"])


def api_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {access_token(user_id)}", "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
    return json.loads(raw.decode()) if raw else {}


def api_raw_request(
    url: str, *, method: str = "POST", body: bytes = b"",
    content_type: str = "application/octet-stream", user_id: str | None = None,
) -> dict[str, Any]:
    """Call a Google API with a non-JSON body, used by file uploads."""
    request = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Bearer {access_token(user_id)}", "Content-Type": content_type},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
    return json.loads(raw.decode()) if raw else {}


def status(user_id: str | None = None) -> dict[str, Any]:
    profile_id = _profile_id(user_id)
    client_id, client_secret = _client_config()
    token = _load_token(profile_id)
    granted = set(str(token.get("scope") or "").split())
    services: dict[str, dict[str, bool]] = {}
    for name, modes in SERVICE_SCOPES.items():
        write_only = modes["write"] - modes["read"]
        services[name] = {
            "read": modes["read"] <= granted or modes["write"] <= granted,
            "write": bool(write_only) and write_only <= granted,
        }
    return {
        "configured": bool(client_id and client_secret),
        "connected": bool(token.get("refresh_token") or token.get("access_token")),
        "user_id": profile_id,
        "services": services,
        "photos_access": "picker-only for existing library; writes limited to app-created content",
        "reason": None if client_id and client_secret else "Google OAuth app credentials are not configured",
    }


def disconnect(user_id: str | None = None) -> bool:
    profile_id = _profile_id(user_id)
    token = _load_token(profile_id)
    value = str(token.get("refresh_token") or token.get("access_token") or "")
    existed = bool(value)
    if value:
        try:
            _post_form(REVOKE_URL, {"token": value}, timeout=10)
        except Exception:
            pass
    _delete_token(profile_id)
    return existed
