"""Local family profile registry with PIN-derived authentication sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from narad_config import FAMILY_PROFILES_PATH, PROFILE_SESSION_SECRET_PATH, PROFILES_DIR
from profile_context import profile_root, validate_profile_id

SCHEMA_VERSION = 1
MAX_PROFILES = 12
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30
INVITE_TTL_SECONDS = 60 * 60 * 72
_LOCK = threading.RLock()
_COLORS = ("sindoor", "matsya", "rama", "krishna", "parashurama", "nila", "gulab", "tulsi")
# Unambiguous when read aloud or typed on a phone: no 0/O, 1/I/L, or U.
_INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"


class InviteError(ValueError):
    """Raised when a profile invite code is missing, expired, or already used."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, Any]:
    try:
        payload = json.loads(FAMILY_PROFILES_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(payload: dict[str, Any]) -> None:
    FAMILY_PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = FAMILY_PROFILES_PATH.with_name(f".{FAMILY_PROFILES_PATH.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(FAMILY_PROFILES_PATH)


def _clean_name(display_name: str) -> str:
    value = " ".join(str(display_name or "").strip().split())[:48]
    if not value:
        raise ValueError("display name is required")
    return value


def _slug(display_name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-") or "member"
    return value[:36]


def _profile_id_for(display_name: str, existing: set[str]) -> str:
    base = _slug(display_name)
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base[:40]}-{suffix}"
        suffix += 1
    return validate_profile_id(candidate)


def _hash_pin(pin: str, salt: bytes | None = None) -> tuple[str, str]:
    if not re.fullmatch(r"\d{4,8}", pin or ""):
        raise ValueError("PIN must contain 4 to 8 digits")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 240_000)
    return salt.hex(), digest.hex()


def _verify_pin(pin: str, profile: dict[str, Any]) -> bool:
    expected = str(profile.get("pin_hash") or "")
    salt_hex = str(profile.get("pin_salt") or "")
    if not expected or not salt_hex:
        return False
    try:
        _, actual = _hash_pin(pin, bytes.fromhex(salt_hex))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _session_epoch(profile: dict[str, Any]) -> int:
    try:
        return int(profile.get("session_epoch") or 0)
    except (TypeError, ValueError):
        return 0


def _invite_digest(code: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]", "", str(code or "").upper())
    return hashlib.sha256(normalized.encode()).hexdigest()


def _live_invites(payload: dict[str, Any]) -> dict[str, Any]:
    now = int(time.time())
    invites = payload.get("invites")
    return {
        digest: invite
        for digest, invite in (invites.items() if isinstance(invites, dict) else [])
        if isinstance(invite, dict) and int(invite.get("expires_at") or 0) > now
    }


def _public(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": str(profile.get("user_id", "")),
        "display_name": str(profile.get("display_name", "")),
        "initial": str(profile.get("display_name", "?")).strip()[:1].upper() or "?",
        "color": str(profile.get("color", "sindoor")),
        "has_pin": bool(profile.get("pin_hash")),
        "is_owner": bool(profile.get("is_owner")),
        "created_at": profile.get("created_at"),
        "last_active_at": profile.get("last_active_at"),
    }


def _bootstrap_default(payload: dict[str, Any]) -> dict[str, Any]:
    profiles = payload.get("profiles")
    if isinstance(profiles, dict) and profiles:
        return payload
    display_name = "You"
    try:
        from onboarding import build_onboarding_status

        display_name = build_onboarding_status("default").get("display_name") or display_name
    except Exception:
        pass
    now = _now()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "profiles": {
            "default": {
                "user_id": "default",
                "display_name": display_name,
                "color": "sindoor",
                "pin_salt": "",
                "pin_hash": "",
                "is_owner": True,
                "created_at": now,
                "updated_at": now,
                "last_active_at": None,
            }
        },
    }
    _write(payload)
    profile_root("default")
    return payload


def list_profiles() -> list[dict[str, Any]]:
    with _LOCK:
        payload = _bootstrap_default(_load())
        profiles = payload.get("profiles") or {}
        rows = [_public(value) for value in profiles.values() if isinstance(value, dict)]
    return sorted(rows, key=lambda row: (not row["is_owner"], row["display_name"].lower()))


def get_profile(user_id: str) -> dict[str, Any] | None:
    safe_id = validate_profile_id(user_id)
    with _LOCK:
        profiles = _bootstrap_default(_load()).get("profiles") or {}
        profile = profiles.get(safe_id)
        return _public(profile) if isinstance(profile, dict) else None


def create_profile(
    display_name: str,
    pin: str,
    color: str = "",
    *,
    invite_code: str | None = None,
) -> dict[str, Any]:
    """Create a family profile; ``invite_code`` is consumed in the same write."""
    clean_name = _clean_name(display_name)
    salt, pin_hash = _hash_pin(pin)
    with _LOCK:
        payload = _bootstrap_default(_load())
        profiles = payload.get("profiles") or {}
        if len(profiles) >= MAX_PROFILES:
            raise ValueError(f"This Narad pilot supports up to {MAX_PROFILES} profiles")
        if invite_code is not None:
            invites = _live_invites(payload)
            if invites.pop(_invite_digest(invite_code), None) is None:
                raise InviteError("That invite code is invalid, expired, or already used")
            payload["invites"] = invites
        user_id = _profile_id_for(clean_name, set(profiles))
        now = _now()
        profile = {
            "user_id": user_id,
            "display_name": clean_name,
            "color": color if color in _COLORS else _COLORS[len(profiles) % len(_COLORS)],
            "pin_salt": salt,
            "pin_hash": pin_hash,
            "is_owner": False,
            "created_at": now,
            "updated_at": now,
            "last_active_at": None,
        }
        profiles[user_id] = profile
        payload["profiles"] = profiles
        payload["schema_version"] = SCHEMA_VERSION
        _write(payload)
    profile_root(user_id)
    return _public(profile)


def create_invite(created_by: str) -> dict[str, Any]:
    """Mint a single-use join code. Only its hash is stored; the code is shown once."""
    raw = "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(8))
    code = f"{raw[:4]}-{raw[4:]}"
    expires_at = int(time.time()) + INVITE_TTL_SECONDS
    with _LOCK:
        payload = _bootstrap_default(_load())
        invites = _live_invites(payload)
        if len(invites) >= MAX_PROFILES:
            raise ValueError("Too many open invites; wait for one to be used or expire")
        invites[_invite_digest(code)] = {
            "created_by": validate_profile_id(created_by),
            "created_at": _now(),
            "expires_at": expires_at,
        }
        payload["invites"] = invites
        _write(payload)
    return {"code": code, "expires_at": expires_at}


def _secret() -> bytes:
    try:
        existing = PROFILE_SESSION_SECRET_PATH.read_bytes()
        if len(existing) >= 32:
            return existing
    except OSError:
        pass
    secret = secrets.token_bytes(32)
    PROFILE_SESSION_SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_SESSION_SECRET_PATH.write_bytes(secret)
    try:
        os.chmod(PROFILE_SESSION_SECRET_PATH, 0o600)
    except OSError:
        pass
    return secret


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_session(user_id: str, pin: str) -> dict[str, Any]:
    safe_id = validate_profile_id(user_id)
    with _LOCK:
        payload = _bootstrap_default(_load())
        profiles = payload.get("profiles") or {}
        profile = profiles.get(safe_id)
        if not isinstance(profile, dict) or not _verify_pin(pin, profile):
            raise ValueError("Profile or PIN is incorrect")
        profile["last_active_at"] = _now()
        profile["updated_at"] = _now()
        _write(payload)
        public = _public(profile)
        epoch = _session_epoch(profile)
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    body = _b64encode(json.dumps({
        "sub": safe_id,
        "exp": expires_at,
        "epoch": epoch,
        "nonce": secrets.token_hex(8),
    }, separators=(",", ":")).encode())
    signature = _b64encode(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return {"profile": public, "token": f"{body}.{signature}", "expires_at": expires_at}


def verify_session(token: str) -> dict[str, Any] | None:
    try:
        body, signature = token.split(".", 1)
        expected = _b64encode(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(_b64decode(body))
        if int(payload.get("exp") or 0) <= int(time.time()) or not payload.get("sub"):
            return None
        safe_id = validate_profile_id(str(payload["sub"]))
        with _LOCK:
            profile = (_bootstrap_default(_load()).get("profiles") or {}).get(safe_id)
            # A PIN change or "sign out everywhere" bumps the epoch, so every
            # token minted before it stops verifying.
            if not isinstance(profile, dict) or int(payload.get("epoch") or 0) != _session_epoch(profile):
                return None
            return _public(profile)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def revoke_sessions(user_id: str) -> int:
    """Sign a profile out on every device; returns the new session epoch."""
    safe_id = validate_profile_id(user_id)
    with _LOCK:
        payload = _bootstrap_default(_load())
        profile = (payload.get("profiles") or {}).get(safe_id)
        if not isinstance(profile, dict):
            raise ValueError("Unknown profile")
        profile["session_epoch"] = _session_epoch(profile) + 1
        profile["updated_at"] = _now()
        _write(payload)
        return profile["session_epoch"]


def update_profile(user_id: str, *, display_name: str | None = None, color: str | None = None, pin: str | None = None) -> dict[str, Any]:
    safe_id = validate_profile_id(user_id)
    with _LOCK:
        payload = _bootstrap_default(_load())
        profiles = payload.get("profiles") or {}
        profile = profiles.get(safe_id)
        if not isinstance(profile, dict):
            raise ValueError("Unknown profile")
        if display_name is not None:
            profile["display_name"] = _clean_name(display_name)
        if color is not None:
            if color not in _COLORS:
                raise ValueError("Unknown profile color")
            profile["color"] = color
        if pin is not None:
            profile["pin_salt"], profile["pin_hash"] = _hash_pin(pin)
            profile["session_epoch"] = _session_epoch(profile) + 1
        profile["updated_at"] = _now()
        _write(payload)
        return _public(profile)


def bootstrap_owner_pin(user_id: str, pin: str) -> dict[str, Any]:
    """Secure a migrated single-user owner profile exactly once."""
    safe_id = validate_profile_id(user_id)
    with _LOCK:
        payload = _bootstrap_default(_load())
        profiles = payload.get("profiles") or {}
        profile = profiles.get(safe_id)
        if not isinstance(profile, dict) or not profile.get("is_owner"):
            raise ValueError("Only the migrated owner profile can be secured here")
        if profile.get("pin_hash"):
            raise ValueError("This owner profile is already secured")
        profile["pin_salt"], profile["pin_hash"] = _hash_pin(pin)
        profile["updated_at"] = _now()
        _write(payload)
    return issue_session(safe_id, pin)


def profiles_root() -> Path:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    return PROFILES_DIR
