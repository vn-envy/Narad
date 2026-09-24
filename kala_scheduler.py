"""
Kala — Narad's in-process scheduler (M3.2).

One asyncio loop (started at server startup) ticks every
NARAD_SCHEDULER_INTERVAL seconds (default 60) and:

  1. Fires due medication reminders — for every family profile, parses the
     free-text `schedule` column of that profile's own health.db
     medication_reminders ("once daily, 8am", "twice daily 8am and 9:30pm",
     "evening") into times-of-day and delivers each to that profile at most
     once per day via vahana.deliver().
     Missed slots earlier today (server was down) still fire once,
     annotated with their original time — never silently dropped.
  2. Delivers due Teach Anything reviews.
  3. Claims typed Workflow Path schedules and delivers restart-safe prompts.

Restart-safe: delivered keys and the last tick persist
in NARAD_HOME/scheduler_state.json. Everything is best-effort — a tick
never raises.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

from narad_config import HEALTH_DB, LEARNING_DIR, SCHEDULER_STATE_PATH

log = logging.getLogger("narad.kala")

_WORD_TIMES = {
    "morning": "08:00",
    "noon": "12:00",
    "afternoon": "14:00",
    "evening": "19:00",
    "night": "21:00",
    "bedtime": "22:00",
}
_TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.IGNORECASE)
_TIME_24_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


def parse_schedule_times(schedule: str) -> list[str]:
    """Free-text schedule → sorted unique "HH:MM" times-of-day.

    Falls back to 09:00 when nothing parseable is found, so every
    reminder fires at least once a day.
    """
    text = (schedule or "").lower()
    times: set[str] = set()
    for m in _TIME_RE.finditer(text):
        hour = int(m.group(1)) % 12
        if m.group(3).lower() == "pm":
            hour += 12
        times.add(f"{hour:02d}:{m.group(2) or '00'}")
    # 24h "HH:MM" only where am/pm didn't already claim the digits
    stripped = _TIME_RE.sub(" ", text)
    for m in _TIME_24_RE.finditer(stripped):
        times.add(f"{int(m.group(1)):02d}:{m.group(2)}")
    for word, t in _WORD_TIMES.items():
        if word in text:
            times.add(t)
    return sorted(times) if times else ["09:00"]


# ── State ─────────────────────────────────────────────────────────────────────

def _load_state() -> dict[str, Any]:
    try:
        return json.loads(SCHEDULER_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    try:
        tmp = SCHEDULER_STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(SCHEDULER_STATE_PATH)
    except Exception as exc:
        log.warning("Kala: could not persist state: %s", exc)


# ── Medication reminders ──────────────────────────────────────────────────────

def _active_medication_reminders(db_path: Path | None = None) -> list[dict]:
    path = db_path or HEALTH_DB
    if not Path(path).exists():
        return []
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, med_name, dose, schedule FROM medication_reminders WHERE active = 1"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("Kala: could not read medication reminders: %s", exc)
        return []


def _family_profile_ids() -> list[str]:
    """Registered family profiles; the single-user owner when there is no registry yet."""
    try:
        from family_profiles import FAMILY_PROFILES_PATH, list_profiles

        # Never bootstrap the registry from a background tick.
        profiles = list_profiles() if FAMILY_PROFILES_PATH.exists() else []
        profile_ids = [str(profile.get("user_id") or "") for profile in profiles]
    except Exception as exc:
        log.warning("Kala: could not list family profiles: %s", exc)
        profile_ids = []
    return [profile_id for profile_id in profile_ids if profile_id] or ["default"]


def _profile_health_db(profile_id: str) -> Path:
    """The profile's own health.db, scoped exactly as health_skill scopes it."""
    from profile_context import profile_data_path

    return profile_data_path("health.db", profile_id=profile_id, legacy_default=HEALTH_DB)


def _fire_due_reminders(now: datetime, state: dict) -> int:
    """Fire every profile's (reminder, time) slots due today and not yet delivered."""
    from vahana import deliver

    today = now.strftime("%Y-%m-%d")
    delivered: dict[str, list[str]] = state.setdefault("delivered", {})
    # prune old days so the state file stays tiny
    for day in [d for d in delivered if d != today]:
        del delivered[day]
    done_today = set(delivered.setdefault(today, []))

    fired = 0
    for profile_id in _family_profile_ids():
        try:
            reminders = _active_medication_reminders(_profile_health_db(profile_id))
        except Exception as exc:
            log.warning("Kala: skipped reminders for profile %s: %s", profile_id, exc)
            continue
        for rem in reminders:
            for hhmm in parse_schedule_times(rem.get("schedule", "")):
                # Reminder ids restart at 1 in every profile's database, so the
                # de-duplication key is profile-scoped. The owner's pre-family
                # keys still count, so an upgrade never repeats today's doses.
                key = f"med:{profile_id}:{rem['id']}:{hhmm}"
                legacy_key = f"med:{rem['id']}:{hhmm}" if profile_id == "default" else key
                if key in done_today or legacy_key in done_today:
                    continue
                hour, minute = map(int, hhmm.split(":"))
                fire_at = datetime.combine(now.date(), dtime(hour, minute))
                if fire_at > now:
                    continue
                late = (now - fire_at).total_seconds() > 15 * 60
                note = f" (scheduled {hhmm})" if late else ""
                deliver(
                    kind="reminder",
                    title=f"Medication: {rem['med_name']}",
                    body=f"Take {rem['med_name']} {rem['dose']} — {rem['schedule']}{note}",
                    user_id=profile_id,
                    source="kala_scheduler.medication",
                    priority="high",
                    data={"reminder_id": rem["id"], "slot": hhmm, "profile_id": profile_id},
                )
                done_today.add(key)
                fired += 1
    delivered[today] = sorted(done_today)
    return fired


# ── Gurukul spaced-repetition reviews (G5) ────────────────────────────────────

def _review_hour() -> int:
    try:
        return int(os.environ.get("NARAD_REVIEW_HOUR", "9")) % 24
    except ValueError:
        return 9


def _users_with_learning_workspaces() -> list[str]:
    try:
        return sorted(p.name for p in LEARNING_DIR.iterdir() if p.is_dir())
    except Exception:
        return []


def _fire_due_reviews(now: datetime, state: dict) -> int:
    """One review digest per user per day: atoms due across their workspaces.

    State-keyed like medication reminders ("review_digest" → {user: date}),
    gated on NARAD_REVIEW_HOUR (default 09:00) so digests arrive at a humane
    hour. Tapping the inbox item opens Gurukul quiz mode (G5.2).
    """
    from vahana import deliver

    today = now.strftime("%Y-%m-%d")
    if now.hour < _review_hour():
        return 0
    digests: dict[str, str] = state.setdefault("review_digest", {})

    fired = 0
    for user_id in _users_with_learning_workspaces():
        if digests.get(user_id) == today:
            continue
        try:
            from guru_engine import due_reviews
            from learning_workspace import list_workspaces
            due_by_topic: list[tuple[str, str, int]] = []  # (topic, workspace_id, count)
            for meta in list_workspaces(user_id):
                workspace_id = str(meta.get("workspace_id", "")).strip()
                if not workspace_id:
                    continue
                due = due_reviews(user_id=user_id, workspace_id=workspace_id)
                if due:
                    topic = str(meta.get("topic", workspace_id))
                    due_by_topic.append((topic, workspace_id, len(due)))
            if not due_by_topic:
                digests[user_id] = today  # nothing due — don't rescan all day
                continue
            total = sum(count for _, _, count in due_by_topic)
            parts = ", ".join(
                f"{count} atom{'s' if count != 1 else ''} in {topic}"
                for topic, _, count in due_by_topic[:4]
            )
            deliver(
                kind="reminder",
                title=f"Gurukul review: {total} atom{'s' if total != 1 else ''} due",
                body=f"Due for review — {parts}. Open Gurukul to quiz yourself.",
                user_id=user_id,
                source="kala_scheduler.guru_review",
                priority="normal",
                data={
                    "review": [
                        {"workspace_id": ws, "topic": topic, "due_count": count}
                        for topic, ws, count in due_by_topic
                    ],
                },
            )
            digests[user_id] = today
            fired += 1
        except Exception as exc:
            log.warning("Kala: review digest failed for %s: %s", user_id, exc)
    return fired


# ── Tick + loop ───────────────────────────────────────────────────────────────

def tick(now: datetime | None = None) -> dict:
    """One synchronous scheduler pass. Never raises."""
    now = now or datetime.now()
    state = _load_state()
    fired = reviews_fired = workflow_fired = 0
    try:
        fired = _fire_due_reminders(now, state)
    except Exception as exc:
        log.warning("Kala: reminder pass failed: %s", exc)
    try:
        reviews_fired = _fire_due_reviews(now, state)
    except Exception as exc:
        log.warning("Kala: review pass failed: %s", exc)
    try:
        from workflow_engine import fire_due_workflow_schedules

        workflow_fired = int(fire_due_workflow_schedules(now).get("fired", 0))
    except Exception as exc:
        log.warning("Kala: workflow schedule pass failed: %s", exc)
    try:
        # Bill and circular reminders a person ticked on a document review.
        from document_review import fire_due_reminders

        fired += fire_due_reminders(now, _family_profile_ids())
    except Exception as exc:
        log.warning("Kala: document reminder pass failed: %s", exc)
    state["last_tick"] = now.isoformat(timespec="seconds")
    _save_state(state)
    if fired or reviews_fired or workflow_fired:
        log.info(
            "Kala tick: %d reminder(s), %d review digest(s), %d workflow trigger(s)",
            fired, reviews_fired, workflow_fired,
        )
    return {
        "fired": fired,
        "reviews_fired": reviews_fired,
        "workflow_fired": workflow_fired,
        "ts": state["last_tick"],
    }


async def run_scheduler_loop() -> None:
    """Background loop for server startup. Cancellation-safe."""
    try:
        interval = max(10, int(os.environ.get("NARAD_SCHEDULER_INTERVAL", "60")))
    except ValueError:
        interval = 60
    log.info("Kala scheduler started (interval %ds)", interval)
    while True:
        try:
            await asyncio.to_thread(tick)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # belt and braces — tick already guards
            log.warning("Kala tick crashed: %s", exc)
        await asyncio.sleep(interval)
