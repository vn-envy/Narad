"""Durable first-run state and readiness for Narad's UI onboarding.

The file is shared with the tier engine, so every write merges with the current
document instead of replacing it. Provider secrets remain in Kunji/keyring;
this module stores only profile and completion metadata.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any

from narad_config import ONBOARDING_PATH

SCHEMA_VERSION = 1
_LOCK = threading.RLock()
_SAFE_USER_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_MODEL_PROVIDERS = {"anthropic", "deepseek", "google", "openai"}
_SEARCH_PROVIDERS = {"exa", "search"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validated_user_id(user_id: str) -> str:
    value = (user_id or "default").strip()
    if not _SAFE_USER_ID.fullmatch(value):
        raise ValueError("user_id must contain only letters, numbers, dots, dashes, or underscores")
    return value


def _load_document() -> dict[str, Any]:
    try:
        payload = json.loads(ONBOARDING_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_document(payload: dict[str, Any]) -> None:
    ONBOARDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = ONBOARDING_PATH.with_name(f".{ONBOARDING_PATH.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(ONBOARDING_PATH)


def _profile_from(payload: dict[str, Any], user_id: str) -> dict[str, Any]:
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict):
        return {}
    profile = profiles.get(user_id)
    return profile if isinstance(profile, dict) else {}


def save_onboarding_state(
    user_id: str = "default",
    *,
    display_name: str | None = None,
    completed: bool | None = None,
    skipped: bool | None = None,
) -> dict[str, Any]:
    """Merge a user's onboarding state while preserving unrelated settings."""
    safe_user_id = _validated_user_id(user_id)
    with _LOCK:
        payload = _load_document()
        profiles = payload.get("profiles")
        if not isinstance(profiles, dict):
            profiles = {}
        profile = dict(_profile_from(payload, safe_user_id))

        if display_name is not None:
            clean_name = " ".join(str(display_name).strip().split())[:80]
            profile["display_name"] = clean_name
        if completed is not None:
            profile["completed"] = bool(completed)
            profile["completed_at"] = _now() if completed else None
        if skipped is not None:
            profile["skipped"] = bool(skipped)

        profile["updated_at"] = _now()
        profiles[safe_user_id] = profile
        payload["schema_version"] = SCHEMA_VERSION
        payload["profiles"] = profiles
        _write_document(payload)
    return build_onboarding_status(safe_user_id)


def _connection_readiness(user_id: str = "default") -> dict[str, Any]:
    try:
        from kunji import list_connections

        connections = list_connections()
    except Exception:
        connections = []

    connected = {
        str(item.get("provider", ""))
        for item in connections
        if isinstance(item, dict) and item.get("connected")
    }
    model_connections = sorted(connected & _MODEL_PROVIDERS)
    search_connections = sorted(connected & _SEARCH_PROVIDERS)

    subscription_connections: list[str] = []
    try:
        from subscription_providers import subscriptions_payload

        subscription_connections = sorted(
            str(item.get("provider", ""))
            for item in subscriptions_payload()
            if isinstance(item, dict) and item.get("available")
        )
    except Exception:
        pass

    local_model: dict[str, Any] = {
        "ready": False,
        "runtime_installed": False,
        "reachable": False,
        "reason": "Local runtime unavailable",
    }
    try:
        from local_model_runtime import local_runtime_status

        local_model = local_runtime_status()
    except Exception:
        pass
    local_ready = bool(local_model.get("ready"))

    google_workspace: dict[str, Any] = {
        "configured": False,
        "connected": False,
        "services": {},
        "reason": "Google Workspace status unavailable",
    }
    try:
        from google_workspace import status as google_status

        google_workspace = google_status(user_id)
        try:
            from family_profiles import get_profile

            google_workspace["can_configure"] = bool((get_profile(user_id) or {}).get("is_owner"))
        except Exception:
            google_workspace["can_configure"] = user_id == "default"
    except Exception:
        pass

    jev: dict[str, Any] = {"available": False, "configured": False, "reason": "Jev unavailable"}
    try:
        from decision_engine import jev_status

        jev = jev_status()
    except Exception:
        pass

    phone: dict[str, Any] = {"available": False, "ready": False, "devices": []}
    desktop: dict[str, Any] = {"available": False, "enabled": False, "adapters": {}}
    grants: list[dict[str, Any]] = []
    try:
        from artemis_adapter import artemis_status
        from computer_use_skill import browser_runtime_status
        from interaction_targets import list_interaction_targets

        phone = artemis_status(include_devices=True)
        desktop = browser_runtime_status().get("desktop", desktop)
        grants = list_interaction_targets(profile_id=user_id)
    except Exception:
        pass

    return {
        "model_ready": bool(model_connections or subscription_connections or local_ready),
        "research_ready": bool(search_connections),
        "connected_model_providers": model_connections,
        "connected_search_providers": search_connections,
        "connected_subscriptions": subscription_connections,
        "local_model_ready": local_ready,
        "local_model": local_model,
        "google_workspace": google_workspace,
        "jev": jev,
        "phone": phone,
        "desktop": desktop,
        "interaction_grants": grants,
    }


def build_onboarding_status(user_id: str = "default") -> dict[str, Any]:
    safe_user_id = _validated_user_id(user_id)
    with _LOCK:
        payload = _load_document()
        profile = _profile_from(payload, safe_user_id)
    readiness = _connection_readiness(safe_user_id)
    completed = bool(profile.get("completed"))
    return {
        "schema_version": SCHEMA_VERSION,
        "user_id": safe_user_id,
        "completed": completed,
        "needs_onboarding": not completed,
        "skipped": bool(profile.get("skipped")),
        "display_name": str(profile.get("display_name", "")),
        "completed_at": profile.get("completed_at"),
        "updated_at": profile.get("updated_at"),
        "readiness": readiness,
    }
