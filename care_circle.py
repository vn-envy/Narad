"""
Care circles — family members looking after each other.

A person (the subject) can share chosen notification kinds with another family
member (the carer): a parent's medicine reminders, a missed dose, a finished
task, or that an approval is waiting. Rules:

  - Only the subject grants or revokes, from their own profile. Nobody else,
    the owner included, can share someone's notifications for them.
  - The grant lives in the subject's own folder: profiles/<subject>/care_circle.json.
  - A carer sees who shared what with them and can leave the circle.
  - A carer's copy carries only the notification's title and a one-line
    summary, never its data or links. An approval request reaches a carer as
    an FYI they cannot act on; only the subject decides it.

vahana.deliver() does the fan-out; this module owns the grants.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import profile_context

log = logging.getLogger("narad.care_circle")

CIRCLE_FILE = "care_circle.json"
SHAREABLE_KINDS: dict[str, str] = {
    "medicine_reminder": "Medicine reminders",
    "health_alert": "Missed doses and health alerts",
    "task_done": "Finished tasks",
    "approval_request": "Approvals waiting (FYI only)",
}
SUMMARY_LIMIT = 160
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _profile_id(value: Any) -> str:
    """A validated profile id; empty is an error here, never the owner."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("A family member is required")
    return profile_context.validate_profile_id(text)


def _circle_path(subject: str) -> Path:
    return Path(profile_context.PROFILES_DIR) / profile_context.validate_profile_id(subject) / CIRCLE_FILE


def _read(subject: str) -> list[dict[str, Any]]:
    try:
        rows = json.loads(_circle_path(subject).read_text(encoding="utf-8")).get("grants")
    except (OSError, ValueError, AttributeError):
        return []
    grants = []
    for row in rows if isinstance(rows, list) else []:
        try:
            carer = _profile_id(row.get("carer"))
        except (ValueError, AttributeError):
            continue
        kinds = [kind for kind in row.get("kinds") or [] if kind in SHAREABLE_KINDS]
        if carer != subject and kinds:
            grants.append({"carer": carer, "kinds": kinds, "granted_at": row.get("granted_at")})
    return grants


def _write(subject: str, grants: list[dict[str, Any]]) -> None:
    path = profile_context.profile_root(subject) / CIRCLE_FILE
    temporary = path.with_name(f".{CIRCLE_FILE}.{uuid.uuid4().hex[:8]}.tmp")
    temporary.write_text(json.dumps({"grants": grants}, indent=2), encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)


def display_name(profile_id: str) -> str:
    try:
        from family_profiles import registered_profile

        profile = registered_profile(profile_id)
    except Exception:
        profile = None
    return str((profile or {}).get("display_name") or profile_id)


def _audit(subject: str, action: str, detail: str, metadata: dict[str, Any]) -> None:
    try:
        from karma_log import log_karma

        with profile_context.profile_scope(subject):
            log_karma(action, f"care-circle-{subject}", "Narad", detail, entity_type="care_circle", metadata=metadata)
    except Exception:
        pass


def load_circle(subject: str) -> list[dict[str, Any]]:
    """The subject's grants: [{carer, kinds, granted_at}]."""
    return _read(_profile_id(subject))


def set_circle(subject: str, grants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace the subject's grants. The caller must already be the subject.

    Carers must be registered family members other than the subject; unknown
    kinds are refused rather than dropped so a typo never silently shares less
    (or more) than the person chose."""
    from family_profiles import registered_profile

    subject = _profile_id(subject)
    wanted: dict[str, list[str]] = {}
    for grant in grants:
        carer = _profile_id((grant or {}).get("carer"))
        if carer == subject:
            raise ValueError("You cannot share notifications with yourself")
        if registered_profile(carer) is None:
            raise ValueError(f"Unknown family member: {carer}")
        kinds = list(dict.fromkeys(str(kind) for kind in (grant or {}).get("kinds") or []))
        unknown = [kind for kind in kinds if kind not in SHAREABLE_KINDS]
        if unknown:
            raise ValueError(f"These notifications cannot be shared: {', '.join(unknown)}")
        if kinds:
            wanted[carer] = [kind for kind in SHAREABLE_KINDS if kind in kinds]
    with _LOCK:
        before = {row["carer"]: row for row in _read(subject)}
        now = _now()
        result = [
            {
                "carer": carer,
                "kinds": kinds,
                "granted_at": before[carer]["granted_at"] if carer in before and before[carer]["kinds"] == kinds else now,
            }
            for carer, kinds in sorted(wanted.items())
        ]
        _write(subject, result)
    changes = {
        carer: {"before": before.get(carer, {}).get("kinds", []), "after": wanted.get(carer, [])}
        for carer in sorted(set(before) | set(wanted))
        if before.get(carer, {}).get("kinds", []) != wanted.get(carer, [])
    }
    if changes:
        _audit(subject, "care_circle_updated", f"shared notifications changed for {len(changes)} carer(s)", changes)
    return result


def carers_for(subject: str, kind: str) -> list[str]:
    """Carers the subject shares this kind with."""
    if kind not in SHAREABLE_KINDS:
        return []
    return [row["carer"] for row in load_circle(subject) if kind in row["kinds"]]


def shared_with(carer: str) -> list[dict[str, Any]]:
    """Who shares what with this carer: [{subject, subject_name, kinds, granted_at}]."""
    carer = _profile_id(carer)
    root = Path(profile_context.PROFILES_DIR)
    rows = []
    for path in sorted(root.glob(f"*/{CIRCLE_FILE}")) if root.exists() else []:
        subject = path.parent.name
        try:
            grants = _read(subject)
        except ValueError:
            continue
        for grant in grants:
            if grant["carer"] == carer:
                rows.append({
                    "subject": subject,
                    "subject_name": display_name(subject),
                    "kinds": grant["kinds"],
                    "granted_at": grant["granted_at"],
                })
    return rows


def leave(carer: str, subject: str) -> bool:
    """The carer steps out of the subject's circle. Returns False if they were not in it."""
    carer = _profile_id(carer)
    subject = _profile_id(subject)
    with _LOCK:
        grants = _read(subject)
        kept = [row for row in grants if row["carer"] != carer]
        if len(kept) == len(grants):
            return False
        _write(subject, kept)
    _audit(subject, "care_circle_left", f"{carer} left the care circle", {"carer": carer})
    return True


def shared_summary(event: dict[str, Any], subject_name: str) -> str:
    """The one line a carer's copy may carry."""
    if event.get("kind") == "approval_request":
        return f"{subject_name} has something waiting for their OK. Only {subject_name} can decide it."
    summary = str(event.get("summary") or "").strip()
    if not summary:
        summary = next((line.strip() for line in str(event.get("body") or "").splitlines() if line.strip()), "")
    if len(summary) > SUMMARY_LIMIT:
        summary = summary[: SUMMARY_LIMIT - 1].rstrip() + "…"
    return summary
