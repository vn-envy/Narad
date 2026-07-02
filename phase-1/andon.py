"""
Jaagruti — Andon pull-cord for Narad.

Fires when an avatar produces a result that is empty, too slow, connection-exhausted,
or contains tool errors. On fire:
  1. Appends an event to ~/.narad/config/andon_log.jsonl
  2. Emits andon_alert SSE event immediately
  3. Invokes Parashurama in ANDON_DIAGNOSTIC mode (fire-and-forget coroutine)
  4. Emits andon_diagnosis SSE event with Parashurama's structured JSON

AndonGate is a pure function — no side effects, safe to call anywhere.
All I/O lives in log_andon() and _run_andon_diagnostic().
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from narad_config import CONFIG_DIR

ANDON_LOG_PATH: Path = CONFIG_DIR / "andon_log.jsonl"

# ── Thresholds (env-overridable) ──────────────────────────────────────────────
ANDON_MIN_LENGTH  = int(os.environ.get("ANDON_MIN_LENGTH",  "80"))
ANDON_LATENCY_MS  = int(os.environ.get("ANDON_LATENCY_MS",  "120000"))  # 2 min


class AndonGate:
    """Pure quality gate — no side effects, no I/O."""

    def check(
        self,
        result_text: str,
        latency_ms: int,
        retries_exhausted: bool,
        tool_error: bool,
    ) -> tuple[bool, str]:
        """Return (should_fire, reason). Call after every avatar span."""
        stripped = result_text.strip()
        # Strip CURRENT_PHASE markers before length check
        for marker in ("CURRENT_PHASE:", "DONE", "[CONTINUING SKILL]"):
            stripped = stripped.replace(marker, "")
        stripped = stripped.strip()

        if retries_exhausted:
            return True, "CONNECTION"
        if tool_error:
            return True, "TOOL_ERROR"
        if len(stripped) < ANDON_MIN_LENGTH:
            return True, "EMPTY_RESULT"
        if latency_ms > ANDON_LATENCY_MS:
            return True, "TIMEOUT"
        return False, ""


def log_andon(
    avatar: str,
    trigger: str,
    session_id: str,
    task_preview: str,
    result_preview: str,
) -> None:
    """Append one andon event to ~/.narad/config/andon_log.jsonl."""
    event = {
        "id":             str(uuid.uuid4()),
        "ts":             datetime.now(timezone.utc).isoformat(),
        "avatar":         avatar,
        "trigger":        trigger,
        "session_id":     session_id,
        "task_preview":   task_preview[:200],
        "result_preview": result_preview[:200],
    }
    with open(ANDON_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")

    # Notion sync hook (fire-and-forget)
    try:
        import os as _os
        if _os.environ.get("NOTION_API_TOKEN"):
            import asyncio as _ao
            from notion_sync import NotionSync as _NS  # type: ignore
            _ns = _NS()
            _ao.get_event_loop().call_soon(lambda _e=event:
                _ao.ensure_future(_ns.push_andon(
                    _e["id"], _e["avatar"], _e["trigger"], _e["session_id"],
                    _e["task_preview"], _e.get("result_preview", ""), _e["ts"]
                )))
    except Exception:
        pass


def load_andon_log(limit: int = 50) -> list[dict]:
    """Return the last `limit` andon events, newest first."""
    if not ANDON_LOG_PATH.exists():
        return []
    lines = ANDON_LOG_PATH.read_text(encoding="utf-8").splitlines()
    events = []
    for line in lines:
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return list(reversed(events[-limit:]))


def andon_stats(days: int = 7) -> dict:
    """Return counts by avatar and failure class for the last N days."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    events = [e for e in load_andon_log(limit=500) if e.get("ts", "") >= cutoff]

    by_avatar: dict[str, int] = {}
    by_class:  dict[str, int] = {}
    for e in events:
        by_avatar[e["avatar"]]  = by_avatar.get(e["avatar"], 0)  + 1
        by_class[e["trigger"]]  = by_class.get(e["trigger"], 0)  + 1

    return {
        "period_days":  days,
        "total":        len(events),
        "by_avatar":    by_avatar,
        "by_class":     by_class,
    }


async def _run_andon_diagnostic(
    avatar_name: str,
    task: str,
    result_text: str,
    trigger: str,
    queue,           # asyncio.Queue | None — SSE queue
    session_id: str,
    user_id: str,
) -> None:
    """
    Fire-and-forget coroutine — invokes Parashurama in ANDON_DIAGNOSTIC mode.
    Emits andon_diagnosis SSE event with Parashurama's structured output.
    """
    import asyncio
    import logging
    _log = logging.getLogger("narad.andon")

    diagnostic_task = (
        f"ANDON DIAGNOSTIC — avatar {avatar_name} triggered Jaagruti.\n"
        f"Trigger: {trigger}\n"
        f"Original task (preview): {task[:300]}\n"
        f"Avatar result (preview): {result_text[:300] if result_text else '(empty)'}\n\n"
        "Run the ANDON_DIAGNOSTIC skill: 5-step structured diagnosis."
    )

    try:
        # Import Parashurama agent and run it
        from avatar_agents import parashurama  # noqa: PLC0415
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types as genai_types

        svc = InMemorySessionService()
        diag_sid = str(__import__("uuid").uuid4())
        app_name = "andon_diagnostic"
        await svc.create_session(app_name=app_name, user_id="narad", session_id=diag_sid)
        runner = Runner(agent=parashurama, app_name=app_name, session_service=svc)
        msg = genai_types.Content(
            role="user", parts=[genai_types.Part(text=diagnostic_task)]
        )
        diagnosis_text = ""
        async for event in runner.run_async(
            user_id="narad", session_id=diag_sid, new_message=msg
        ):
            if event.is_final_response() and event.content and event.content.parts:
                diagnosis_text = "".join(p.text or "" for p in event.content.parts)

        if queue is not None and diagnosis_text:
            await queue.put(json.dumps({
                "type": "andon_diagnosis",
                "data": {
                    "avatar":    avatar_name,
                    "trigger":   trigger,
                    "diagnosis": diagnosis_text[:800],
                },
            }))
        _log.info("Andon diagnostic complete for %s (%s)", avatar_name, trigger)
    except Exception as exc:
        _log.warning("Andon diagnostic failed: %s", exc)
        if queue is not None:
            try:
                await queue.put(json.dumps({
                    "type": "andon_diagnosis",
                    "data": {
                        "avatar":    avatar_name,
                        "trigger":   trigger,
                        "diagnosis": f"Diagnostic unavailable: {str(exc)[:200]}",
                    },
                }))
            except Exception:
                pass
