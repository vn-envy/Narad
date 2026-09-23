"""Profile-scoped grants for high-trust browser and mobile connectors."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from profile_context import current_profile_id, profile_data_path, validate_profile_id

_LOCK = threading.RLock()
_KINDS = frozenset({"browser_skill", "artemis", "cua"})
_EXTERNAL_ID = re.compile(r"^[^\x00-\x1f\x7f]{1,160}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(profile_id: str):
    return profile_data_path("interaction_targets.json", profile_id=profile_id)


def _load(profile_id: str) -> dict[str, Any]:
    try:
        payload = json.loads(_path(profile_id).read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except (OSError, json.JSONDecodeError):
        pass
    return {"schema_version": 1, "targets": []}


def _write(profile_id: str, payload: dict[str, Any]) -> None:
    path = _path(profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)


def _clean_kind(kind: str) -> str:
    value = str(kind or "").strip().lower()
    if value not in _KINDS:
        raise ValueError(f"connector kind must be one of: {', '.join(sorted(_KINDS))}")
    return value


def _clean_external_id(external_id: str) -> str:
    value = str(external_id or "").strip()
    if not _EXTERNAL_ID.fullmatch(value):
        raise ValueError("connector target id is missing or contains unsupported characters")
    return value


def list_interaction_targets(
    *, profile_id: str | None = None, kind: str | None = None
) -> list[dict[str, Any]]:
    owner = validate_profile_id(profile_id or current_profile_id())
    requested_kind = _clean_kind(kind) if kind else None
    with _LOCK:
        rows = _load(owner).get("targets") or []
        return [
            dict(row)
            for row in rows
            if isinstance(row, dict)
            and bool(row.get("enabled", True))
            and (requested_kind is None or row.get("kind") == requested_kind)
        ]


def register_interaction_target(
    kind: str,
    external_id: str,
    *,
    label: str = "",
    profile_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    owner = validate_profile_id(profile_id or current_profile_id())
    target_kind = _clean_kind(kind)
    target_external_id = _clean_external_id(external_id)
    clean_label = " ".join(str(label or "").split())[:80] or target_external_id
    safe_metadata = {
        str(key)[:60]: value
        for key, value in (metadata or {}).items()
        if isinstance(value, (str, int, float, bool)) or value is None
    }
    with _LOCK:
        payload = _load(owner)
        rows = [row for row in payload.get("targets", []) if isinstance(row, dict)]
        existing = next(
            (
                row
                for row in rows
                if row.get("kind") == target_kind
                and row.get("external_id") == target_external_id
            ),
            None,
        )
        now = _now()
        if existing is None:
            existing = {
                "target_id": f"target_{uuid.uuid4().hex[:12]}",
                "kind": target_kind,
                "external_id": target_external_id,
                "created_at": now,
            }
            rows.append(existing)
        existing.update({
            "label": clean_label,
            "enabled": True,
            "updated_at": now,
            "metadata": safe_metadata,
        })
        payload.update({"schema_version": 1, "targets": rows})
        _write(owner, payload)
        return dict(existing)


def revoke_interaction_target(target_id: str, *, profile_id: str | None = None) -> bool:
    owner = validate_profile_id(profile_id or current_profile_id())
    wanted = str(target_id or "").strip()
    with _LOCK:
        payload = _load(owner)
        rows = [row for row in payload.get("targets", []) if isinstance(row, dict)]
        retained = [row for row in rows if row.get("target_id") != wanted]
        if len(retained) == len(rows):
            return False
        payload["targets"] = retained
        _write(owner, payload)
        return True


def resolve_interaction_target(
    kind: str,
    requested: str = "",
    *,
    profile_id: str | None = None,
) -> dict[str, Any] | None:
    """Resolve only a target granted to the active profile.

    ``requested`` may be Narad's opaque target id or the connector's external id.
    An omitted target resolves only when exactly one grant exists.
    """
    rows = list_interaction_targets(profile_id=profile_id, kind=kind)
    wanted = str(requested or "").strip()
    if wanted:
        return next(
            (
                row
                for row in rows
                if wanted in {str(row.get("target_id") or ""), str(row.get("external_id") or "")}
            ),
            None,
        )
    return rows[0] if len(rows) == 1 else None
