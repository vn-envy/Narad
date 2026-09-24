"""
Vahana — Narad's single delivery channel (M3.1).

Every fired event (reminder, approval, Swapna digest, Andon escalation, cron)
flows through ONE function: deliver(). It fans out to:
  1. Chat inbox   — append-only jsonl at NARAD_HOME/inbox/<user>.jsonl,
                    surfaced by GET /inbox and the Activity screen. This copy
                    is the source of truth; pushes only point at it.
  2. Web Push     — every phone the profile subscribed (vahana_push.py),
                    self-hosted VAPID, end-to-end encrypted, sent off-thread.
  3. ntfy push    — fallback only when the profile has no Web Push device:
                    best-effort POST to the profile's own private topic
                    (env-gated; silently skipped when unconfigured).
  4. Care circle  — shared kinds are copied to each carer's inbox and phones,
                    title and one-line summary only (care_circle.py).
  5. Karma ledger — one mutation row per delivery for provenance.

Each person's notification preferences (profiles/<id>/notification_prefs.json)
shape the pushes, never the inbox copy:
  lock_screen_details   off by default: the push says only "Narad has a
                        reminder for you"; the full text waits in the app.
  quiet_hours           22:00-07:00 by default: only urgent pushes (and the
                        person's own on-time medicine reminders, unless they
                        turn that off); everything else waits silently.

Env:
  NTFY_URL    — ntfy server base, e.g. https://ntfy.sh (no trailing slash needed)
  NTFY_TOPIC  — topic prefix; both must be set for push to activate. Nothing is
                ever published to this shared topic itself: each profile pushes
                to "<NTFY_TOPIC>-<profile>-<random suffix>", persisted in
                profiles/<profile>/ntfy.json (set "topic" there to override).
                Run `python vahana.py` to print each profile's subscription.
  NTFY_TOKEN  — optional bearer token for private servers

Network sends run on vahana_push's worker pool (5-10 s timeouts); failures are
logged, never raised, and never block the event loop or the inbox write.
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

import profile_context
from narad_config import INBOX_DIR

log = logging.getLogger("narad.vahana")

_VALID_KINDS = {
    "reminder", "swapna", "andon", "cron", "system", "triage",
    # Care-circle shareable kinds (care_circle.SHAREABLE_KINDS).
    "medicine_reminder", "health_alert", "task_done", "approval_request",
    # Anumati approvals and anything else that waits on the person.
    "approval_result", "question",
}
_NTFY_PRIORITY = {"urgent": "5", "high": "4", "default": "3", "low": "2"}
_URGENCY = {"urgent": "high", "high": "high", "default": "normal", "low": "low"}
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


def _push_ntfy(event: dict, topic: str, title: str, body: str, tags: str) -> bool:
    """POST one rendered notification to a profile's ntfy topic. Blocking; never raises."""
    url = os.environ["NTFY_URL"].rstrip("/") + "/" + topic
    headers = {
        "Title": (title or "Narad")[:120],
        "Priority": _NTFY_PRIORITY.get(event.get("priority", "default"), "3"),
        "Tags": tags,
    }
    token = os.environ.get("NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(url, data=(body or "")[:2000].encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:
        log.warning("Vahana: ntfy push failed (%s) — inbox copy is safe", exc)
        return False


def _queue_ntfy(event: dict, title: str, body: str, tags: str) -> bool:
    """Queue an ntfy push to the event's profile topic. True when one was queued."""
    try:
        topic = ntfy_topic(event.get("profile_id") or "")
    except Exception as exc:
        log.warning("Vahana: ntfy topic unavailable (%s) — inbox copy is safe", exc)
        return False
    if not topic:
        return False
    import vahana_push

    vahana_push.submit(_push_ntfy, event, topic, title, body, tags)
    return True


def drain_pushes(timeout: float = 15.0) -> None:
    """Wait for queued pushes to finish (tests, clean shutdown)."""
    import vahana_push

    vahana_push.drain(timeout)


# ── Notification preferences (per profile) ────────────────────────────────────

PREFS_FILE = "notification_prefs.json"
DEFAULT_PREFERENCES: dict[str, Any] = {
    "lock_screen_details": False,
    "quiet_hours": {"enabled": True, "start": "22:00", "end": "07:00"},
    "medicine_in_quiet_hours": True,
    "timezone": None,
}
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_PREFS_LOCK = threading.Lock()

# What a push says when the person keeps details off their lock screen. The
# full text waits in the app's Activity screen.
_GENERIC_TEXT = {
    "approval_request": ("Narad needs your OK", "Open Narad to review it."),
    "question": ("Narad has a question for you", "Open Narad to answer it."),
    "medicine_reminder": ("Narad has a reminder for you", "Open Narad to see it."),
    "health_alert": ("Narad has a reminder for you", "Open Narad to see it."),
    "reminder": ("Narad has a reminder for you", "Open Narad to see it."),
    "task_done": ("Narad finished something for you", "Open Narad to see it."),
}
_GENERIC_DEFAULT = ("Narad has an update for you", "Open Narad to see it.")
_GENERIC_SHARED = ("Narad has a family update", "Open Narad to see it.")


def _prefs_path(profile_id: str) -> Path:
    return Path(profile_context.PROFILES_DIR) / profile_context.validate_profile_id(profile_id) / PREFS_FILE


def _valid_timezone(name: Any) -> str | None:
    text = str(name or "").strip()
    if not text or len(text) > 64:
        return None
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(text)
    except Exception:
        return None
    return text


def _normalise_preferences(raw: Any) -> dict[str, Any]:
    prefs = json.loads(json.dumps(DEFAULT_PREFERENCES))
    if not isinstance(raw, dict):
        return prefs
    for key in ("lock_screen_details", "medicine_in_quiet_hours"):
        if isinstance(raw.get(key), bool):
            prefs[key] = raw[key]
    quiet = raw.get("quiet_hours") if isinstance(raw.get("quiet_hours"), dict) else {}
    if isinstance(quiet.get("enabled"), bool):
        prefs["quiet_hours"]["enabled"] = quiet["enabled"]
    for key in ("start", "end"):
        if _HHMM_RE.fullmatch(str(quiet.get(key) or "")):
            prefs["quiet_hours"][key] = quiet[key]
    prefs["timezone"] = _valid_timezone(raw.get("timezone"))
    if raw.get("updated_at"):
        prefs["updated_at"] = str(raw["updated_at"])
    return prefs


def load_preferences(profile_id: str) -> dict[str, Any]:
    """The profile's notification preferences, defaults filled in."""
    try:
        raw = json.loads(_prefs_path(profile_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return _normalise_preferences(raw)


def update_preferences(profile_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Merge validated changes into the profile's preferences; ValueError on bad input."""
    changes = {key: value for key, value in dict(changes or {}).items() if value is not None}
    quiet = changes.pop("quiet_hours", None) or {}
    if not isinstance(quiet, dict):
        raise ValueError("quiet_hours must be an object")
    for key in ("start", "end"):
        if quiet.get(key) is not None and not _HHMM_RE.fullmatch(str(quiet[key])):
            raise ValueError("Quiet hours use HH:MM, for example 22:00")
    if changes.get("timezone") and not _valid_timezone(changes["timezone"]):
        raise ValueError("Unknown time zone")
    with _PREFS_LOCK:
        current = load_preferences(profile_id)
        merged = {
            **current,
            **changes,
            "quiet_hours": {**current["quiet_hours"], **{k: v for k, v in quiet.items() if v is not None}},
        }
        prefs = _normalise_preferences(merged)
        prefs["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        path = profile_context.profile_root(profile_id) / PREFS_FILE
        temporary = path.with_name(f".{PREFS_FILE}.{uuid.uuid4().hex[:8]}.tmp")
        temporary.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
        temporary.replace(path)
    return prefs


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def in_quiet_hours(prefs: dict[str, Any], now: datetime | None = None) -> bool:
    """True inside the person's quiet hours, in their time zone (else the Mac's)."""
    quiet = prefs.get("quiet_hours") or {}
    if not quiet.get("enabled"):
        return False
    moment = now or _clock()
    zone = _valid_timezone(prefs.get("timezone"))
    if zone:
        from zoneinfo import ZoneInfo

        local = moment.astimezone(ZoneInfo(zone))
    else:
        local = moment.astimezone()
    start_h, start_m = map(int, str(quiet.get("start", "22:00")).split(":"))
    end_h, end_m = map(int, str(quiet.get("end", "07:00")).split(":"))
    start, end, current = start_h * 60 + start_m, end_h * 60 + end_m, local.hour * 60 + local.minute
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _held_by_quiet_hours(event: dict, prefs: dict[str, Any]) -> bool:
    if event.get("priority") == "urgent" or not in_quiet_hours(prefs):
        return False
    # The person chose this time for their own medicine. A late copy (the Mac
    # was asleep at the time) or someone else's shared reminder still waits.
    own_on_time_medicine = (
        event.get("kind") == "medicine_reminder"
        and not event.get("shared_from")
        and not (event.get("data") or {}).get("late")
        and prefs.get("medicine_in_quiet_hours", True)
    )
    return not own_on_time_medicine


# ── Push rendering ────────────────────────────────────────────────────────────

_TAG_RE = re.compile(r"[^A-Za-z0-9_.:-]+")


def _event_url(event: dict) -> str:
    """A same-origin deep link: the caller's own url, else the Activity item.

    A carer's copy always opens their own Activity item, never the subject's link."""
    url = "" if event.get("shared_from") else str((event.get("data") or {}).get("url") or "")
    if url.startswith("/") and not url.startswith("//") and "\\" not in url and len(url) <= 300:
        return url
    return f"/?activity={event['id']}"


def _event_tag(event: dict) -> str:
    data = {} if event.get("shared_from") else (event.get("data") or {})
    if data.get("proposal_id"):
        # A request and its result share a tag, so the result replaces it.
        tag = f"approval-{data['proposal_id']}"
    elif event.get("kind") == "medicine_reminder" and data.get("reminder_id") is not None:
        tag = f"med-{data['reminder_id']}-{data.get('slot', '')}"
    else:
        tag = str(event.get("id") or "narad")
    return _TAG_RE.sub("-", tag)[:64]


def push_payload(event: dict, prefs: dict[str, Any]) -> dict[str, Any]:
    """The small push payload: title, body, url, tag, kind, id (plus the unread badge).

    Without lock-screen details, title and body are generic per kind; the full
    text stays in the inbox inside the app."""
    if prefs.get("lock_screen_details"):
        title, body = event.get("title") or "Narad", event.get("body") or ""
    elif event.get("shared_from"):
        title, body = _GENERIC_SHARED
    else:
        title, body = _GENERIC_TEXT.get(event.get("kind", ""), _GENERIC_DEFAULT)
    body = " ".join(str(body).split())
    return {
        "title": str(title)[:80],
        "body": body if len(body) <= 180 else body[:179].rstrip() + "…",
        "url": _event_url(event),
        "tag": _event_tag(event),
        "kind": event.get("kind", "system"),
        "id": event["id"],
        "unread": unread_count(event["user_id"]),
    }


def _notify(event: dict) -> dict[str, Any]:
    """Push one inbox event to its profile's phones: Web Push, else ntfy.

    Never raises; the inbox copy is already written."""
    profile_id = event.get("profile_id")
    if not profile_id:
        return {"channel": None, "queued": False}
    try:
        prefs = load_preferences(profile_id)
        if _held_by_quiet_hours(event, prefs):
            return {"channel": None, "queued": False, "held": "quiet_hours"}
        payload = push_payload(event, prefs)
        import vahana_push

        devices = vahana_push.push_to_profile(
            profile_id,
            payload,
            urgency=_URGENCY.get(event.get("priority", "default"), "normal"),
        )
        if devices:
            return {"channel": "web_push", "queued": True, "devices": devices}
        if ntfy_configured():
            # ntfy prints tags on the notification, so the kind shows only with details on.
            tags = event.get("kind", "system") if prefs.get("lock_screen_details") else "narad"
            return {"channel": "ntfy", "queued": _queue_ntfy(event, payload["title"], payload["body"], tags)}
    except Exception as exc:
        log.warning("Vahana: push for %s skipped (%s) — inbox copy is safe", event.get("id"), exc)
    return {"channel": None, "queued": False}


def _append_inbox(event: dict) -> None:
    with open(_inbox_path(event["user_id"]), "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _share_with_carers(event: dict) -> list[str]:
    """Copy a shared kind to each carer: inbox and phones, title and summary only."""
    subject = event.get("profile_id")
    if not subject or event.get("shared_from"):
        return []
    try:
        import care_circle

        carers = care_circle.carers_for(subject, event["kind"])
        if not carers:
            return []
        name = care_circle.display_name(subject)
        summary = care_circle.shared_summary(event, name)
    except Exception as exc:
        log.warning("Vahana: care circle for %s unavailable (%s)", subject, exc)
        return []
    shared = []
    for carer in carers:
        copy = {
            "id": f"vahana-{uuid.uuid4().hex[:12]}",
            "ts": event["ts"],
            "kind": event["kind"],
            "title": event["title"],
            "body": summary,
            "user_id": carer,
            "profile_id": carer,
            "source": "care_circle",
            "priority": event["priority"],
            "read": False,
            "shared_from": subject,
            "shared_from_name": name,
            "data": {"shared_from": subject, "shared_from_name": name},
        }
        try:
            _append_inbox(copy)
        except OSError as exc:
            log.warning("Vahana: could not share %s with %s (%s)", event["id"], carer, exc)
            continue
        _notify(copy)
        shared.append(carer)
    return shared


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
    summary: str | None = None,
) -> dict:
    """Deliver one event: inbox jsonl (always), then phones, carers and karma.

    Args:
        kind:     reminder | medicine_reminder | health_alert | task_done |
                  approval_request | approval_result | question | swapna |
                  andon | cron | system | triage (anything else → system)
        title:    Short headline for the inbox and, with lock-screen details
                  on, the push title.
        body:     Full text of the event (inbox; the lock screen only if allowed).
        user_id:  Inbox owner (a profile id).
        source:   Module/function that fired this (provenance).
        priority: urgent | high | default | low. Only urgent breaks quiet hours.
        data:     Optional structured payload (ids, session refs). data["url"]
                  is the push's same-origin deep link, e.g. "/?approval=<id>";
                  data["late"] marks a reminder that fired after its time.
        summary:  Optional one line for carers; defaults to the body's first line.
    Returns:
        {"status": "ok", "event_id", "pushed": bool, "push": {...}, "shared_with": [...]}
        "pushed" means a push was queued for at least one phone.
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
    if summary:
        event["summary"] = " ".join(str(summary).split())[:200]

    # 1. Inbox — the durable copy; failure here is a real error.
    _append_inbox(event)

    # 2. Phones — best-effort and off-thread; preferences decide what shows.
    push = _notify(event)
    if not push["queued"] and not push.get("held"):
        log.info("Vahana: no phone to push to — %s is in the inbox only", event["id"])

    # 3. Care circle — carers given this kind get a short copy.
    shared_with = _share_with_carers(event)

    # 4. Karma provenance — best-effort, in the recipient's own ledger (the
    #    scheduler fires outside any request, so it has no profile context).
    try:
        from karma_log import log_karma

        from profile_context import profile_scope
        with profile_scope(event["profile_id"] or "default"):
            log_karma(
                "vahana_delivered", event["id"], "Narad",
                f"{kind}: {event['title'][:80]}",
                entity_type="delivery",
                metadata={
                    "pushed": push["queued"], "channel": push.get("channel"), "held": push.get("held"),
                    "shared_with": shared_with, "source": source, "user_id": user_id,
                },
            )
    except Exception:
        pass

    return {
        "status": "ok",
        "event_id": event["id"],
        "pushed": bool(push["queued"]),
        "push": push,
        "shared_with": shared_with,
    }


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
