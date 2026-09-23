"""
Vahana — Narad's single delivery channel (M3.1).

Every fired event (reminder, Swapna digest, Andon escalation, cron) flows
through ONE function: deliver(). It fans out to:
  1. Chat inbox   — append-only jsonl at NARAD_HOME/inbox/<user>.jsonl,
                    surfaced by GET /inbox and rendered as inbox turns.
  2. ntfy push    — best-effort POST to the profile's own private topic
                    (env-gated; silently skipped when unconfigured, never blocks).
  3. Karma ledger — one mutation row per delivery for provenance.

Env:
  NTFY_URL    — ntfy server base, e.g. https://ntfy.sh (no trailing slash needed)
  NTFY_TOPIC  — topic prefix; both must be set for push to activate. Nothing is
                ever published to this shared topic itself: each profile pushes
                to "<NTFY_TOPIC>-<profile>-<random suffix>", persisted in
                profiles/<profile>/ntfy.json (set "topic" there to override).
                Run `python vahana.py` to print each profile's subscription.
  NTFY_TOKEN  — optional bearer token for private servers

No external deps — urllib only, 5s timeout, all failures logged not raised.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from narad_config import INBOX_DIR

log = logging.getLogger("narad.vahana")

_VALID_KINDS = {"reminder", "swapna", "andon", "cron", "system", "triage"}
_NTFY_PRIORITY = {"urgent": "5", "high": "4", "default": "3", "low": "2"}
_NTFY_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TOPIC_LOCK = threading.Lock()


def _safe_slug(user_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", user_id or "default") or "default"


def _inbox_path(user_id: str) -> Path:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    return INBOX_DIR / f"{_safe_slug(user_id)}.jsonl"


# ── ntfy push (env-gated, best-effort) ────────────────────────────────────────

def ntfy_configured() -> bool:
    return bool(os.environ.get("NTFY_URL") and os.environ.get("NTFY_TOPIC"))


def _profile_id(user_id: str) -> str | None:
    from profile_context import validate_profile_id

    if not user_id:
        return None  # validate_profile_id would map this to the owner
    try:
        return validate_profile_id(user_id)
    except ValueError:
        return None


def ntfy_topic(user_id: str) -> str | None:
    """Return the profile's private ntfy topic, creating it on first use.

    Family members share one Mac, so a shared topic would put every profile's
    reminders on every subscribed phone. Each profile instead gets an
    unguessable topic of its own; None means push is off for this profile.
    """
    if not ntfy_configured():
        return None
    profile_id = _profile_id(user_id)
    if not profile_id:
        return None
    from profile_context import profile_data_path

    shared = os.environ["NTFY_TOPIC"]
    path = profile_data_path("ntfy.json", profile_id=profile_id)
    with _TOPIC_LOCK:
        try:
            topic = str(json.loads(path.read_text(encoding="utf-8")).get("topic") or "")
        except (OSError, ValueError, AttributeError):
            topic = ""
        if not topic:
            topic = f"{_safe_slug(shared)[:24]}-{profile_id[:16]}-{secrets.token_hex(10)}"
            path.write_text(json.dumps({"topic": topic}, indent=2), encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    if topic == shared or not _NTFY_TOPIC_RE.fullmatch(topic):
        log.warning("Vahana: ntfy topic for %s is shared or invalid — push skipped", profile_id)
        return None
    return topic


def _push_ntfy(event: dict) -> bool:
    """POST the event to its profile's ntfy topic. Returns True on 2xx. Never raises."""
    if not ntfy_configured():
        return False
    try:
        topic = ntfy_topic(event.get("profile_id") or "")
    except Exception as exc:
        log.warning("Vahana: ntfy topic unavailable (%s) — inbox copy is safe", exc)
        return False
    if not topic:
        return False
    url = os.environ["NTFY_URL"].rstrip("/") + "/" + topic
    body = (event.get("body") or "")[:2000].encode("utf-8")
    headers = {
        "Title": (event.get("title") or "Narad")[:120],
        "Priority": _NTFY_PRIORITY.get(event.get("priority", "default"), "3"),
        "Tags": event.get("kind", "system"),
    }
    token = os.environ.get("NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:
        log.warning("Vahana: ntfy push failed (%s) — inbox copy is safe", exc)
        return False


# ── The one delivery function ─────────────────────────────────────────────────

def deliver(
    *,
    kind: str,
    title: str,
    body: str,
    user_id: str = "default",
    source: str = "",
    priority: str = "default",
    data: dict[str, Any] | None = None,
) -> dict:
    """Deliver one event: inbox jsonl (always) + ntfy push (env-gated) + karma.

    Args:
        kind:     reminder | swapna | andon | cron | system | triage
        title:    Short headline shown in inbox and as push title.
        body:     Full text of the event.
        user_id:  Inbox owner.
        source:   Module/function that fired this (provenance).
        priority: urgent | high | default | low (maps to ntfy priority).
        data:     Optional structured payload (ids, session refs).
    Returns:
        {"status": "ok", "event_id", "pushed": bool}
    """
    if kind not in _VALID_KINDS:
        kind = "system"
    event = {
        "id": f"vahana-{uuid.uuid4().hex[:12]}",
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "title": (title or "").strip()[:200],
        "body": (body or "").strip()[:4000],
        "user_id": user_id,
        "profile_id": _profile_id(user_id),
        "source": source,
        "priority": priority if priority in _NTFY_PRIORITY else "default",
        "read": False,
        "data": data or {},
    }

    # 1. Inbox — the durable copy; failure here is a real error.
    with open(_inbox_path(user_id), "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

    # 2. ntfy — best-effort.
    pushed = _push_ntfy(event)
    if not pushed and not ntfy_configured():
        log.info("Vahana: NTFY_URL/NTFY_TOPIC unset — delivered to inbox only (%s)", event["id"])

    # 3. Karma provenance — best-effort, in the recipient's own ledger (the
    #    scheduler fires outside any request, so it has no profile context).
    try:
        from karma_log import log_karma

        from profile_context import profile_scope
        with profile_scope(event["profile_id"] or "default"):
            log_karma(
                "vahana_delivered", event["id"], "Narad",
                f"{kind}: {event['title'][:80]}",
                entity_type="delivery",
                metadata={"pushed": pushed, "source": source, "user_id": user_id},
            )
    except Exception:
        pass

    return {"status": "ok", "event_id": event["id"], "pushed": pushed}


# ── Inbox reads / mark-read ───────────────────────────────────────────────────

def load_inbox(
    user_id: str = "default",
    *,
    limit: int = 50,
    unread_only: bool = False,
    kind: str | None = None,
) -> list[dict]:
    """Return newest-first inbox events."""
    path = _inbox_path(user_id)
    if not path.exists():
        return []
    events: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("op") == "mark_read":
            for eid in row.get("ids", []):
                if eid in events:
                    events[eid]["read"] = True
            continue
        if row.get("id"):
            events[row["id"]] = row
    rows = list(reversed(list(events.values())))
    if unread_only:
        rows = [r for r in rows if not r.get("read")]
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    return rows[:limit]


def mark_read(user_id: str = "default", ids: list[str] | None = None) -> dict:
    """Mark events read. ids=None marks everything currently unread."""
    if ids is None:
        ids = [r["id"] for r in load_inbox(user_id, limit=1000, unread_only=True)]
    if ids:
        marker = {
            "op": "mark_read",
            "ts": datetime.now(timezone.utc).isoformat(),
            "ids": ids,
        }
        with open(_inbox_path(user_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(marker) + "\n")
    return {"status": "ok", "marked": len(ids)}


def unread_count(user_id: str = "default") -> int:
    return len(load_inbox(user_id, limit=1000, unread_only=True))


if __name__ == "__main__":
    # Pilot setup: subscribe each family member's phone to their own topic only.
    from dotenv import load_dotenv

    from family_profiles import list_profiles

    load_dotenv(Path(__file__).parent / ".env")
    if not ntfy_configured():
        raise SystemExit("Set NTFY_URL and NTFY_TOPIC (e.g. in .env) to enable push.")
    for profile in list_profiles():
        topic = ntfy_topic(profile["user_id"])
        print(f"{profile['display_name']} ({profile['user_id']}): {os.environ['NTFY_URL'].rstrip('/')}/{topic}")
