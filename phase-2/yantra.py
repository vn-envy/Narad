"""
Yantra — Avatara's observability layer (v2).

Writes a structured JSONL trace for every session: routing decisions,
per-avatar latency, token costs, phase transitions, and errors.
No external service required — plain files in ~/.narad/sessions/.

Trace schema (one JSON object per line):
  {
    "ts":              ISO timestamp,
    "session_id":      str,
    "user_id":         str,
    "event":           "session_start" | "avatar_start" | "avatar_done"
                       | "session_done" | "phase_transition"
                       | "routing_decision" | "error" | <custom>,
    "avatar":          str | null,
    "task":            str | null,        # truncated to 200 chars
    "result_len":      int | null,        # chars in result
    "result_digest":   str | null,        # first 100 chars of result (avatar_done only)
    "usage":           dict | null,       # {prompt_tokens, completion_tokens, total} (avatar_done)
    "trajectory":      dict | null,       # Trajectory.to_dict() — all tool calls (avatar_done only)
    "latency_ms":      int | null,        # wall-clock ms for this avatar
    "total_ms":        int | null,        # session_done only
    "phase":           str | null,        # phase_transition only
    "avatars_invoked": list | null,       # routing_decision only
    "discipline":      str | null,
    "degraded_capabilities": list | null,
    "error":           str | null,
    "error_type":      "tool_not_found" | "import_failed" | "timeout" | "model_error"
                       | "json_parse" | "event_loop" | "notion_sync" | null,
  }

Usage:
  tracer = Tracer(session_id, user_id)
  tracer.session_start(query)
  with tracer.avatar_span("Parashurama", task) as span:
      # inside the run loop, call span.record_usage(usage_metadata) each LLM event
      result = await run_avatar(...)
      span.finish(result, trajectory=traj)   # traj is a Trajectory instance or None
  tracer.log_event("phase_transition", avatar="Parashurama", phase="assess")
  tracer.log_event("routing_decision", avatars_invoked=["Parashurama"], query_preview="...")
  tracer.session_done()
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generator

from narad_config import TRACE_DIR as _TRACE_DIR

if TYPE_CHECKING:
    from yantra_models import Trajectory


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "not found" in msg and "tool" in msg:
        return "tool_not_found"
    if "importerror" in msg or "modulenotfounderror" in msg or "cannot import" in msg:
        return "import_failed"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "json" in msg and ("parse" in msg or "decode" in msg or "unterminated" in msg):
        return "json_parse"
    if "event loop" in msg or "asyncio" in msg:
        return "event_loop"
    if "notion" in msg:
        return "notion_sync"
    return "model_error"


def _write(path: Path, record: dict) -> None:
    try:
        with path.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


class _TokenMeter:
    """Accumulates token usage across all LLM events within one avatar run.

    Adapted from IBM/AssetOpsBench (Apache 2.0).
    Call record_usage() for every event that carries usage_metadata.
    Fixes the prior = (not +=) bug where only the last event's tokens were kept.
    """
    __slots__ = ("prompt", "completion")

    def __init__(self) -> None:
        self.prompt = 0
        self.completion = 0

    def record(self, usage_metadata: Any) -> None:
        """Accept a google.genai UsageMetadata object or any object with token count attrs."""
        if usage_metadata is None:
            return
        self.prompt     += getattr(usage_metadata, "prompt_token_count",     0) or 0
        self.completion += getattr(usage_metadata, "candidates_token_count", 0) or 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion


class _AvatarSpan:
    def __init__(
        self,
        tracer: "Tracer",
        avatar: str,
        task: str,
        discipline: str | None = None,
        degraded_capabilities: list[str] | None = None,
    ) -> None:
        self._tracer = tracer
        self._avatar = avatar
        self._task = task
        self._discipline = discipline
        self._degraded_capabilities = degraded_capabilities or []
        self._start = time.monotonic()
        self.meter = _TokenMeter()

    def record_usage(self, usage_metadata: Any) -> None:
        """Call once per LLM event that carries usage_metadata to accumulate tokens."""
        self.meter.record(usage_metadata)

    def finish(
        self,
        result: str = "",
        trajectory: "Trajectory | None" = None,
    ) -> None:
        latency_ms = int((time.monotonic() - self._start) * 1000)
        digest = result[:100].strip() if result else ""
        extra: dict[str, Any] = {}
        if digest:
            extra["result_digest"] = digest
        if self.meter.total > 0:
            extra["usage"] = {
                "prompt_tokens":     self.meter.prompt,
                "completion_tokens": self.meter.completion,
                "total_tokens":      self.meter.total,
            }
        if trajectory is not None:
            extra["trajectory"] = trajectory.to_dict()
        self._tracer._write_event(
            event="avatar_done",
            avatar=self._avatar,
            task=self._task,
            discipline=self._discipline,
            result_len=len(result),
            latency_ms=latency_ms,
            degraded_capabilities=self._degraded_capabilities or None,
            **extra,
        )


class Tracer:
    def __init__(self, session_id: str, user_id: str) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self._session_start = time.monotonic()
        self._path = _TRACE_DIR / f"{session_id}.jsonl"

    def _write_event(self, event: str, **kwargs: Any) -> None:
        record: dict[str, Any] = {
            "ts":         _now(),
            "session_id": self.session_id,
            "user_id":    self.user_id,
            "event":      event,
        }
        # Standard fields
        for key in ("avatar", "task", "result_len", "result_digest", "usage",
                    "trajectory", "latency_ms", "total_ms", "phase",
                    "discipline", "degraded_capabilities",
                    "avatars_invoked", "error", "error_type"):
            val = kwargs.get(key)
            if val is not None:
                record[key] = val
        # Truncate task to 200 chars
        if "task" in record and isinstance(record["task"], str):
            record["task"] = record["task"][:200] or None
        _write(self._path, record)

    def log_event(self, event: str, **kwargs: Any) -> None:
        """Emit an arbitrary named event to the trace JSONL."""
        self._write_event(event, **kwargs)

    def session_start(self, query: str) -> None:
        self._write_event(event="session_start", task=query)

    @contextmanager
    def avatar_span(
        self,
        avatar: str,
        task: str,
        discipline: str | None = None,
        degraded_capabilities: list[str] | None = None,
    ) -> Generator[_AvatarSpan, None, None]:
        self._write_event(
            event="avatar_start",
            avatar=avatar,
            task=task,
            discipline=discipline,
            degraded_capabilities=degraded_capabilities or None,
        )
        span = _AvatarSpan(self, avatar, task, discipline, degraded_capabilities)
        try:
            yield span
        except Exception as exc:
            self._write_event(event="error", avatar=avatar, error=str(exc), error_type=_classify_error(exc))
            raise

    def session_done(self) -> None:
        total_ms = int((time.monotonic() - self._session_start) * 1000)
        self._write_event(event="session_done", total_ms=total_ms)

    @staticmethod
    def load(session_id: str) -> list[dict]:
        """Load all events for a session."""
        path = _TRACE_DIR / f"{session_id}.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    @staticmethod
    def summary(session_id: str) -> dict:
        """Return a summary dict: avatars called, latencies, token costs, total time."""
        events = Tracer.load(session_id)
        done_events = [e for e in events if e.get("event") == "avatar_done"]
        session_done = next((e for e in events if e.get("event") == "session_done"), {})
        phase_events = [e for e in events if e.get("event") == "phase_transition"]
        total_prompt = sum(e.get("usage", {}).get("prompt_tokens", 0) for e in done_events)
        total_completion = sum(e.get("usage", {}).get("completion_tokens", 0) for e in done_events)
        return {
            "session_id":        session_id,
            "avatars":           [e["avatar"] for e in done_events],
            "latencies_ms":      {e["avatar"]: e.get("latency_ms") for e in done_events},
            "total_ms":          session_done.get("total_ms"),
            "total_prompt_tokens":     total_prompt,
            "total_completion_tokens": total_completion,
            "phase_transitions": [
                {"avatar": e.get("avatar"), "phase": e.get("phase")} for e in phase_events
            ],
        }
