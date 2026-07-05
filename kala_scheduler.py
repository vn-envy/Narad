"""
Kala — Narad's in-process scheduler (M3.2).

One asyncio loop (started at server startup) ticks every
NARAD_SCHEDULER_INTERVAL seconds (default 60) and:

  1. Fires due medication reminders — parses the free-text `schedule`
     column of health.db medication_reminders ("once daily, 8am",
     "twice daily 8am and 9:30pm", "evening") into times-of-day and
     delivers each at most once per day via vahana.deliver().
     Missed slots earlier today (server was down) still fire once,
     annotated with their original time — never silently dropped.
  2. Consumes Swapna nightly — at NARAD_SWAPNA_HOUR (default 2) runs
     dream(apply=True) per user with episodes and delivers a digest.

Restart-safe: state (delivered keys per day, last swapna date) persists
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
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any

from narad_config import EPISODE_DIR, HEALTH_DB, SCHEDULER_STATE_PATH

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


def _fire_due_reminders(now: datetime, state: dict, *, user_id: str = "default") -> int:
    """Fire every (reminder, time) slot due today and not yet delivered."""
    from vahana import deliver

    today = now.strftime("%Y-%m-%d")
    delivered: dict[str, list[str]] = state.setdefault("delivered", {})
    # prune old days so the state file stays tiny
    for day in [d for d in delivered if d != today]:
        del delivered[day]
    done_today = set(delivered.setdefault(today, []))

    fired = 0
    for rem in _active_medication_reminders():
        for hhmm in parse_schedule_times(rem.get("schedule", "")):
            key = f"med:{rem['id']}:{hhmm}"
            if key in done_today:
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
                user_id=user_id,
                source="kala_scheduler.medication",
                priority="high",
                data={"reminder_id": rem["id"], "slot": hhmm},
            )
            done_today.add(key)
            fired += 1
    delivered[today] = sorted(done_today)
    return fired


# ── Nightly Swapna ────────────────────────────────────────────────────────────

def _swapna_hour() -> int:
    try:
        return int(os.environ.get("NARAD_SWAPNA_HOUR", "2")) % 24
    except ValueError:
        return 2


def _users_with_episodes() -> list[str]:
    try:
        return sorted(p.stem for p in EPISODE_DIR.glob("*.jsonl") if p.stat().st_size > 0)
    except Exception:
        return []


def _run_nightly_swapna(now: datetime, state: dict) -> int:
    """After NARAD_SWAPNA_HOUR, run one apply-cycle per user per day."""
    from vahana import deliver

    today = now.strftime("%Y-%m-%d")
    if now.hour < _swapna_hour() or state.get("last_swapna_date") == today:
        return 0

    ran = 0
    for user_id in _users_with_episodes():
        try:
            from swapna import dream
            result = dream(user_id=user_id, apply=True)
            if result.get("status") != "ok" or not result.get("inbox_id"):
                continue
            sug = result.get("suggestions", {})
            deliver(
                kind="swapna",
                title="Swapna nightly digest",
                body=(
                    f"Consolidated {result.get('source_episode_count', 0)} episode(s): "
                    f"{len(sug.get('facts', []))} fact(s), "
                    f"{len(sug.get('scenarios', []))} scenario(s), "
                    f"keywords: {', '.join(sug.get('candidate_keywords', [])[:6]) or '—'}. "
                    f"Review in the Swapna inbox."
                ),
                user_id=user_id,
                source="kala_scheduler.swapna",
                priority="low",
                data={"inbox_id": result.get("inbox_id")},
            )
            ran += 1
        except Exception as exc:
            log.warning("Kala: Swapna cycle failed for %s: %s", user_id, exc)
    state["last_swapna_date"] = today
    return ran


# ── Tick + loop ───────────────────────────────────────────────────────────────

def tick(now: datetime | None = None) -> dict:
    """One synchronous scheduler pass. Never raises."""
    now = now or datetime.now()
    state = _load_state()
    fired = swapna_ran = 0
    try:
        fired = _fire_due_reminders(now, state)
    except Exception as exc:
        log.warning("Kala: reminder pass failed: %s", exc)
    try:
        swapna_ran = _run_nightly_swapna(now, state)
    except Exception as exc:
        log.warning("Kala: swapna pass failed: %s", exc)
    state["last_tick"] = now.isoformat(timespec="seconds")
    _save_state(state)
    if fired or swapna_ran:
        log.info("Kala tick: %d reminder(s) fired, %d swapna cycle(s)", fired, swapna_ran)
    return {"fired": fired, "swapna_ran": swapna_ran, "ts": state["last_tick"]}


async def run_scheduler_loop() -> None:
    """Background loop for server startup. Cancellation-safe."""
    try:
        interval = max(10, int(os.environ.get("NARAD_SCHEDULER_INTERVAL", "60")))
    except ValueError:
        interval = 60
    log.info("Kala scheduler started (interval %ds, swapna hour %02d:00)",
             interval, _swapna_hour())
    while True:
        try:
            await asyncio.to_thread(tick)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # belt and braces — tick already guards
            log.warning("Kala tick crashed: %s", exc)
        await asyncio.sleep(interval)
