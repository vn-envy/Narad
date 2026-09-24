"""
pilot_metrics — private, local pilot metrics: counts and outcomes, never text.

Per profile, under NARAD_HOME/profiles/<id>/:
  metrics/turns.jsonl     one line per chat turn (TurnRecorder, hooked in /chat)
  metrics/feedback.jsonl  thumbs up/down from POST /feedback
  metrics/voice.jsonl     one line per voice-in (STT) or voice-out (TTS) request
  consent.json            the consent version this person accepted, and when

Every record written here passes one allowlist: numbers, booleans, and short
single-token strings (ids, enum values, timestamps). Prompt text, reply text,
tool arguments and previews never reach these files; phase-1/test_pilot_metrics.py
asserts it. Host uptime, backups and the weekly scorecard live in
pilot_scorecard.py. Exact definitions: docs/PILOT_CONSENT_AND_METRICS.md.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

log = logging.getLogger("narad.pilot_metrics")

# Bump when docs/PILOT_CONSENT_AND_METRICS.md changes what is stored or shared;
# everyone is asked to accept the new version.
CONSENT_VERSION = "2026-09-24"

OUTCOMES = ("answered", "error", "stopped")
RATINGS = ("up", "down")
FEEDBACK_REASONS = (
    "wrong", "incomplete", "not_what_i_asked", "too_slow", "unsafe",
    "unneeded_approval", "language", "other",
)
AVATARS = ("narad", "matsya", "rama", "krishna", "parashurama")

# Event types that carry answer text: streamed deltas (text_reset drops the
# routing chatter streamed so far) and the complete final synthesis. Only their
# length is ever counted.
_DELTA_EVENTS = frozenset({"text_delta", "token", "message_delta"})
_TEXT_EVENTS = _DELTA_EVENTS | {"narad_synthesis", "text"}
_STOP_EVENTS = frozenset({"stopped", "cancelled", "run_stopped"})

# Approval accounting (until Anumati emits explicit approval events). A tool
# result asking for confirmation is an approval *requested*; it was *needed*
# when the tool commits something (send, book, upload, delete, schedule).
# computer_use / phone_use gate per action, so their approvals stay unclassified.
COMMIT_TOOLS = frozenset({
    "send_email", "create_event", "upload_google_drive", "browser_upload_and_submit",
    "move_to_trash", "organize_by_type", "schedule_cron", "remove_cron_job",
})
UNCLASSIFIED_APPROVAL_TOOLS = frozenset({"computer_use", "phone_use"})
_APPROVAL_PREVIEW = re.compile(
    r"'status':\s*'(?:preview|confirmation_required)'|'requires_confirmation':\s*True"
)

# Egress ledger sources that belong to a chat turn; background learners
# (tapas, sankalpa, guru, scheduler) are reported separately.
_TURN_EGRESS_SOURCES = frozenset({"agent", "memory"})

_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+-]{0,63}$")
_TYPE_PREFIX = re.compile(r'^\{\s*"type"\s*:\s*"([A-Za-z_]+)"')
_write_lock = threading.Lock()


# ── Paths and safe writes ─────────────────────────────────────────────────────

def _profile_dir(profile_id: str | None = None) -> Path:
    from profile_context import profile_root

    return profile_root(profile_id)


def metrics_dir(profile_id: str | None = None) -> Path:
    return _profile_dir(profile_id) / "metrics"


def _token(value: Any, default: str | None = None) -> str | None:
    text = str(value) if value is not None else ""
    return text if _TOKEN.fullmatch(text) else default


def _clean(value: Any) -> Any:
    """The allowlist every record passes: no free text survives it."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _token(value, "invalid")
    if isinstance(value, dict):
        return {_token(k, "invalid"): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return "invalid"


def _append(path: Path, record: dict[str, Any]) -> None:
    line = json.dumps(_clean(record), separators=(",", ":"), sort_keys=True)
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def iso_local(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def read_jsonl(path: Path, *, since: float | None = None) -> list[dict[str, Any]]:
    """Rows of a metrics/ops ledger, oldest first; rows before `since` (epoch) dropped."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if since is not None and float(row.get("t") or _epoch(row.get("ts")) or 0) < since:
            continue
        rows.append(row)
    return rows


def _epoch(value: Any) -> float | None:
    if not value:
        return None
    for fmt in (None, "%Y-%m-%dT%H:%M:%S%z"):
        try:
            parsed = datetime.fromisoformat(str(value)) if fmt is None else datetime.strptime(str(value), fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed.timestamp()
    return None


# ── Per-turn recording ────────────────────────────────────────────────────────

def _tool_name(value: Any) -> str | None:
    return _token(str(value or "").lstrip("_").lower())


class TurnRecorder:
    """Watches one chat turn's SSE payloads and writes one count-only record.

    It reads event types and the few structural fields it needs (avatar name,
    tool name, confirmation status, workflow ids) and the length of answer
    text. It never keeps text.
    """

    def __init__(
        self,
        *,
        profile_id: str,
        session_id: str,
        workflow_run_id: str | None = None,
        attachments: int = 0,
        images: int = 0,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.turn_id = uuid.uuid4().hex[:16]
        self.profile_id = profile_id
        self.session_id = session_id
        self._clock = clock
        self._start = clock()
        self.started_at = wall()
        self.first_event_ms: int | None = None
        self.first_text_ms: int | None = None
        self.done_ms: int | None = None
        self.delta_chars = 0
        self.synthesis_chars = 0
        self.avatars: list[str] = []
        self.avatar_calls = 0
        self.tool_calls = 0
        self.tools: dict[str, int] = {}
        self.approvals = {"requested": 0, "needed": 0, "unclassified": 0}
        self.andon_alerts = 0
        self.error_events = 0
        self.stop_event = False
        self.workflow: dict[str, Any] | None = (
            {"run_id": workflow_run_id} if workflow_run_id else None
        )
        self.inputs = {"attachments": int(attachments), "images": int(images)}
        self.written: dict[str, Any] | None = None

    def _elapsed_ms(self) -> int:
        return max(0, round((self._clock() - self._start) * 1000))

    def observe(self, item: Any) -> Any:
        """Account for one queued payload; returns it (``done`` gains ``turn_id``)."""
        if not isinstance(item, str):
            return item
        match = _TYPE_PREFIX.match(item)
        if not match:
            return item
        kind = match.group(1)
        if kind == "ping":
            return item
        elapsed = self._elapsed_ms()
        if self.first_event_ms is None:
            self.first_event_ms = elapsed
        if kind == "text_reset":
            self.delta_chars = 0
            return item
        if kind in _TEXT_EVENTS or kind in {"avatar_start", "step_event", "workflow_updated", "done"}:
            try:
                payload = json.loads(item)
            except ValueError:
                return item
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            if kind in _TEXT_EVENTS:
                text = data.get("text") or data.get("delta") or ""
                if isinstance(text, str) and text.strip():
                    if kind in _DELTA_EVENTS:
                        self.delta_chars += len(text)
                    else:
                        self.synthesis_chars += len(text)
                    if self.first_text_ms is None:
                        self.first_text_ms = elapsed
            elif kind == "avatar_start":
                self.avatar_calls += 1
                avatar = str(data.get("avatar") or "").lower()
                avatar = avatar if avatar in AVATARS else "other"
                if avatar not in self.avatars:
                    self.avatars.append(avatar)
            elif kind == "step_event":
                self._observe_step(data)
            elif kind == "workflow_updated":
                run = data.get("run") if isinstance(data.get("run"), dict) else {}
                self.workflow = {
                    "run_id": _token(data.get("workflow_run_id")),
                    "workflow_id": _token(data.get("workflow_id")),
                    "stage": _token(run.get("current_stage_id")),
                    "status": _token(run.get("status")),
                }
            elif kind == "done":
                self.done_ms = elapsed
                data["turn_id"] = self.turn_id
                payload["data"] = data
                return json.dumps(payload)
        elif kind in ("andon", "andon_alert"):
            self.andon_alerts += 1
        elif kind == "error":
            self.error_events += 1
        elif kind in _STOP_EVENTS:
            self.stop_event = True
        return item

    def _observe_step(self, data: dict[str, Any]) -> None:
        step = data.get("kind")
        tool = _tool_name(data.get("tool"))
        if step == "tool_call" and tool:
            self.tool_calls += 1
            self.tools[tool] = self.tools.get(tool, 0) + 1
        elif step == "tool_result" and tool:
            # The preview is str(result)[:150]; "status" leads every envelope,
            # so a confirmation stop is visible in it.
            preview = data.get("preview")
            if isinstance(preview, str) and _APPROVAL_PREVIEW.search(preview):
                self._count_approval(tool)

    def _count_approval(self, tool: str) -> None:
        self.approvals["requested"] += 1
        if tool in COMMIT_TOOLS:
            self.approvals["needed"] += 1
        elif tool in UNCLASSIFIED_APPROVAL_TOOLS:
            self.approvals["unclassified"] += 1

    @property
    def reply_chars(self) -> int:
        """Length of the answer, whether it streamed, arrived whole, or both."""
        return max(self.delta_chars, self.synthesis_chars)

    def outcome(self, *, cancelled: bool = False, crashed: bool = False) -> tuple[str, str]:
        if cancelled or self.stop_event:
            return "stopped", "cancelled" if cancelled else "stop_event"
        if crashed or self.error_events:
            return "error", "exception"
        if self.done_ms is None:
            return "error", "no_done"
        if self.reply_chars <= 0:
            return "error", "empty"
        return "answered", ""

    def finish(self, *, cancelled: bool = False, crashed: bool = False) -> dict[str, Any] | None:
        """Write this turn's record once; later calls are no-ops."""
        if self.written is not None:
            return None
        done_ms = self.done_ms if self.done_ms is not None else self._elapsed_ms()
        ended_at = self.started_at + done_ms / 1000
        outcome, reason = self.outcome(cancelled=cancelled, crashed=crashed)
        egress = turn_egress(self.profile_id, self.started_at, ended_at)
        record = {
            "v": 1,
            "t": round(ended_at, 3),
            "ts": iso_local(ended_at),
            "started": iso_local(self.started_at),
            "turn_id": self.turn_id,
            "session_id": _token(self.session_id, "invalid"),
            "outcome": outcome,
            "reason": reason,
            "latency_ms": {
                "first_event": self.first_event_ms,
                "first_text": self.first_text_ms,
                "done": done_ms,
            },
            "avatars": self.avatars,
            "avatar_calls": self.avatar_calls,
            "tool_calls": self.tool_calls,
            "tools": self.tools,
            "approvals": self.approvals,
            "cloud_llm_calls": egress.pop("cloud_llm_calls"),
            "egress": egress,
            "andon_alerts": self.andon_alerts,
            "workflow": self.workflow,
            "inputs": self.inputs,
            "reply_chars": self.reply_chars,
        }
        self.written = _clean(record)
        _append(metrics_dir(self.profile_id) / "turns.jsonl", record)
        return self.written


class TurnQueue(asyncio.Queue):
    """The chat task's SSE queue, with every payload shown to a TurnRecorder.

    asyncio.Queue.put() ends in put_nowait(), so one override sees every event
    whichever way it is queued. A recorder failure never touches the stream.
    """

    def __init__(self, recorder: TurnRecorder) -> None:
        super().__init__()
        self.recorder = recorder

    def put_nowait(self, item: Any) -> None:
        try:
            item = self.recorder.observe(item)
        except Exception as exc:  # metrics must never break a turn
            log.debug("pilot metrics observe failed: %s", exc)
        super().put_nowait(item)

    def watch(self, task: asyncio.Future) -> None:
        """Write the turn record when the chat task ends, however it ends."""
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Future) -> None:
        try:
            cancelled = task.cancelled()
            crashed = (not cancelled) and task.exception() is not None
            self.recorder.finish(cancelled=cancelled, crashed=crashed)
        except Exception as exc:
            log.warning("pilot metrics turn record failed: %s", exc)


def turn_egress(profile_id: str, started: float, ended: float) -> dict[str, int]:
    """Cloud calls this profile made while the turn ran, by trust tier.

    Read from the tail of the profile's privacy egress ledger. Approximate when
    one profile runs two turns at once; background learners are excluded.
    """
    counts = {"trusted": 0, "redact": 0, "blocked": 0, "cloud_llm_calls": 0}
    path = _profile_dir(profile_id) / "privacy" / "egress.jsonl"
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 256 * 1024))
            tail = handle.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return counts
    for line in reversed(tail):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        stamp = _epoch(row.get("ts"))
        if stamp is None:
            continue
        if stamp < started - 1:
            break
        if stamp > ended + 1 or row.get("source") not in _TURN_EGRESS_SOURCES:
            continue
        if row.get("blocked"):
            counts["blocked"] += 1
            continue
        tier = row.get("tier")
        if tier in ("trusted", "redact"):
            counts[tier] += 1
            if row.get("source") == "agent":
                counts["cloud_llm_calls"] += 1
    return counts


def start_turn(
    *,
    profile_id: str,
    session_id: str,
    workflow_run_id: str | None = None,
    attachments: int = 0,
    images: int = 0,
) -> TurnQueue:
    return TurnQueue(TurnRecorder(
        profile_id=profile_id,
        session_id=session_id,
        workflow_run_id=workflow_run_id,
        attachments=attachments,
        images=images,
    ))


# ── Feedback, voice, consent ──────────────────────────────────────────────────

def record_feedback(
    profile_id: str,
    *,
    session_id: str,
    rating: str,
    turn_id: str | None = None,
    message_index: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Append a thumbs up/down. Raises ValueError on anything but ids and enums."""
    if not _token(session_id):
        raise ValueError("session_id is not a valid id")
    if rating not in RATINGS:
        raise ValueError("rating must be 'up' or 'down'")
    if turn_id is not None and not re.fullmatch(r"[0-9a-f]{8,32}", str(turn_id)):
        raise ValueError("turn_id is not a valid id")
    if message_index is not None:
        if isinstance(message_index, bool) or not isinstance(message_index, int) or not 0 <= message_index < 100_000:
            raise ValueError("message_index must be a non-negative integer")
    if turn_id is None and message_index is None:
        raise ValueError("turn_id or message_index is required")
    if reason is not None and reason not in FEEDBACK_REASONS:
        raise ValueError(f"reason must be one of: {', '.join(FEEDBACK_REASONS)}")
    now = time.time()
    record = {
        "v": 1,
        "t": round(now, 3),
        "ts": iso_local(now),
        "session_id": session_id,
        "turn_id": turn_id,
        "message_index": message_index,
        "rating": rating,
        "reason": reason,
    }
    _append(metrics_dir(profile_id) / "feedback.jsonl", record)
    return _clean(record)


def record_voice(
    kind: str,
    *,
    engine: str | None,
    ok: bool,
    profile_id: str | None = None,
    audio_bytes: int | None = None,
    chars: int | None = None,
) -> None:
    """One voice-in (stt) or voice-out (tts) request. Never raises."""
    try:
        if profile_id is None:
            from profile_context import current_profile_id

            profile_id = current_profile_id()
        now = time.time()
        _append(metrics_dir(profile_id) / "voice.jsonl", {
            "v": 1,
            "t": round(now, 3),
            "ts": iso_local(now),
            "kind": kind if kind in ("stt", "tts") else "other",
            "engine": _token(engine, "unknown") if engine else None,
            "ok": bool(ok),
            "audio_bytes": audio_bytes,
            "chars": chars,
        })
    except Exception as exc:
        log.debug("voice metric not recorded: %s", exc)


def consent_status(profile_id: str) -> dict[str, Any]:
    path = _profile_dir(profile_id) / "consent.json"
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    stored = stored if isinstance(stored, dict) else {}
    accepted = bool(stored.get("accepted")) and stored.get("version") == CONSENT_VERSION
    return {
        "profile": profile_id,
        "current_version": CONSENT_VERSION,
        "version": stored.get("version"),
        "accepted": bool(stored.get("accepted")),
        "decided_at": stored.get("decided_at"),
        "needs_consent": not accepted,
        "document": "docs/PILOT_CONSENT_AND_METRICS.md",
    }


def record_consent(profile_id: str, *, version: str, accepted: bool) -> dict[str, Any]:
    """Store the person's decision on the current consent sheet (0600)."""
    if version != CONSENT_VERSION:
        raise ValueError(f"The current consent version is {CONSENT_VERSION}")
    path = _profile_dir(profile_id) / "consent.json"
    now = time.time()
    payload = {"version": version, "accepted": bool(accepted), "decided_at": iso_local(now)}
    history = []
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
        history = list(previous.get("history") or [])[-19:]
        history.append({k: previous.get(k) for k in ("version", "accepted", "decided_at")})
    except (OSError, ValueError, AttributeError):
        pass
    payload["history"] = history
    tmp = path.with_name(f".{path.name}.tmp")
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(_clean(payload), indent=2, sort_keys=True), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)
    return consent_status(profile_id)


# ── Aggregation ───────────────────────────────────────────────────────────────

def percentile(values: Iterable[float], pct: float) -> int | None:
    ordered = sorted(v for v in values if v is not None)
    if not ordered:
        return None
    rank = max(0, min(len(ordered) - 1, int(round(pct / 100 * len(ordered) + 0.5)) - 1))
    return int(ordered[rank])


def ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _latency(turn: dict[str, Any], key: str) -> float | None:
    value = (turn.get("latency_ms") or {}).get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _feedback_by_turn(turns: list[dict[str, Any]], feedback: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Latest rating per turn_id; a message_index names the session's n-th turn."""
    by_session: dict[str, list[str]] = {}
    for turn in turns:
        by_session.setdefault(str(turn.get("session_id")), []).append(str(turn.get("turn_id")))
    ratings: dict[str, dict[str, Any]] = {}
    for row in feedback:
        turn_id = row.get("turn_id")
        if not turn_id and row.get("message_index") is not None:
            ordered = by_session.get(str(row.get("session_id")), [])
            index = int(row["message_index"])
            turn_id = ordered[index] if 0 <= index < len(ordered) else None
        if turn_id:
            ratings[str(turn_id)] = row
    return ratings


def _abandoned_sessions(turns: list[dict[str, Any]], now: float, quiet_s: float = 1800) -> tuple[int, int]:
    last: dict[str, dict[str, Any]] = {}
    for turn in turns:
        key = str(turn.get("session_id"))
        if key not in last or float(turn.get("t") or 0) >= float(last[key].get("t") or 0):
            last[key] = turn
    abandoned = sum(
        1 for turn in last.values()
        if turn.get("outcome") in ("error", "stopped") and now - float(turn.get("t") or 0) >= quiet_s
    )
    return len(last), abandoned


def egress_summary(profile_id: str, *, since: float) -> dict[str, Any]:
    """Every cloud call in the profile's egress ledger since `since`, by tier and source."""
    rows = [
        row for row in read_jsonl(_profile_dir(profile_id) / "privacy" / "egress.jsonl")
        if (_epoch(row.get("ts")) or 0) >= since
    ]
    by_tier: dict[str, int] = {}
    by_source: dict[str, int] = {}
    blocked: dict[str, int] = {}
    rules_only = violations = 0
    for row in rows:
        source = _token(str(row.get("source") or "unknown").replace(" ", "_"), "other")
        if row.get("blocked"):
            reason = str(row["blocked"]).split(":", 1)[0]
            blocked[_token(reason, "other")] = blocked.get(_token(reason, "other"), 0) + 1
            continue
        tier = str(row.get("tier") or "unknown")
        by_tier[tier] = by_tier.get(tier, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1
        if tier == "redact" and row.get("detector") == "rules":
            rules_only += 1
        if tier == "blocked":
            violations += 1
    return {
        "calls": sum(by_tier.values()),
        "by_tier": by_tier,
        "by_source": by_source,
        "blocked": blocked,
        "redact_rules_only": rules_only,
        "blocked_tier_sent": violations,
    }


def profile_summary(profile_id: str, *, days: int = 7, now: float | None = None,
                    until: float | None = None) -> dict[str, Any]:
    """One person's aggregates over [until - days, until) (until defaults to now)."""
    now = time.time() if now is None else now
    end = now if until is None else until
    since = end - days * 86400
    folder = metrics_dir(profile_id)
    turns = [r for r in read_jsonl(folder / "turns.jsonl", since=since) if float(r.get("t") or 0) < end]
    feedback = [r for r in read_jsonl(folder / "feedback.jsonl", since=since) if float(r.get("t") or 0) < end]
    voice = [r for r in read_jsonl(folder / "voice.jsonl", since=since) if float(r.get("t") or 0) < end]
    ratings = _feedback_by_turn(turns, feedback)

    outcomes = {name: 0 for name in OUTCOMES}
    for turn in turns:
        if turn.get("outcome") in outcomes:
            outcomes[turn["outcome"]] += 1
    down = {turn_id for turn_id, row in ratings.items() if row.get("rating") == "down"}
    successes = sum(
        1 for turn in turns
        if turn.get("outcome") == "answered" and str(turn.get("turn_id")) not in down
    )
    answered = [turn for turn in turns if turn.get("outcome") == "answered"]
    approvals = {"requested": 0, "needed": 0, "unclassified": 0}
    for turn in turns:
        for key in approvals:
            approvals[key] += int((turn.get("approvals") or {}).get(key) or 0)
    approvals["unneeded"] = max(0, approvals["requested"] - approvals["needed"] - approvals["unclassified"])
    sessions, abandoned = _abandoned_sessions(turns, end)
    reasons: dict[str, int] = {}
    for row in ratings.values():
        if row.get("reason"):
            reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
    ups = sum(1 for row in ratings.values() if row.get("rating") == "up")
    voice_counts = {"stt": 0, "tts": 0, "failed": 0}
    engines: dict[str, int] = {}
    for row in voice:
        if row.get("kind") in ("stt", "tts"):
            voice_counts[row["kind"]] += 1
        if not row.get("ok"):
            voice_counts["failed"] += 1
        if row.get("engine"):
            engines[row["engine"]] = engines.get(row["engine"], 0) + 1
    return {
        "profile": profile_id,
        "window": {"days": days, "since": iso_local(since), "until": iso_local(end)},
        "turns": len(turns),
        "outcomes": outcomes,
        "task_success": {"successes": successes, "rate": ratio(successes, len(turns))},
        "time_to_done_ms": {
            "p50": percentile((_latency(t, "done") for t in answered), 50),
            "p90": percentile((_latency(t, "done") for t in answered), 90),
        },
        "first_text_ms": {
            "p50": percentile((_latency(t, "first_text") for t in answered), 50),
            "p90": percentile((_latency(t, "first_text") for t in answered), 90),
            # Turns that used no tools: the Stage B "fast on phones" figure.
            "p50_no_tools": percentile(
                (_latency(t, "first_text") for t in answered if not t.get("tool_calls")), 50
            ),
        },
        "approvals": approvals,
        "abandonment": {"sessions": sessions, "abandoned": abandoned, "rate": ratio(abandoned, sessions)},
        "feedback": {
            "up": ups,
            "down": len(down),
            "rated_turns": len(ratings),
            "coverage": ratio(len(ratings), len(turns)),
            "reasons": reasons,
        },
        "voice": {**voice_counts, "engines": engines, "stt_per_turn": ratio(voice_counts["stt"], len(turns))},
        "turn_egress": {
            "turns_all_local": sum(
                1 for t in turns
                if not any((t.get("egress") or {}).get(k) for k in ("trusted", "redact"))
            ),
            "trusted": sum(int((t.get("egress") or {}).get("trusted") or 0) for t in turns),
            "redact": sum(int((t.get("egress") or {}).get("redact") or 0) for t in turns),
            "blocked": sum(int((t.get("egress") or {}).get("blocked") or 0) for t in turns),
        },
        "egress": egress_summary(profile_id, since=since),
        "workflow_turns": sum(1 for t in turns if t.get("workflow")),
    }


def known_profiles() -> list[str]:
    try:
        from family_profiles import list_profiles

        return [str(row["user_id"]) for row in list_profiles()]
    except Exception:
        return ["default"]


def owner_view(summary: dict[str, Any]) -> dict[str, Any]:
    """What the owner may see of one person's summary.

    Cloud calls by *source* stay private to the person: a source such as a
    medication-reminder job would reveal what the person uses Narad for.
    """
    view = dict(summary)
    view["egress"] = {k: v for k, v in summary["egress"].items() if k != "by_source"}
    return view


def household_summary(*, days: int = 7, now: float | None = None) -> dict[str, Any]:
    """Owner view: per-profile aggregates plus consent state. Counts only."""
    now = time.time() if now is None else now
    profiles = {}
    for profile_id in known_profiles():
        summary = owner_view(profile_summary(profile_id, days=days, now=now))
        summary["consent"] = consent_status(profile_id)
        profiles[profile_id] = summary
    return {"window_days": days, "profiles": profiles}
