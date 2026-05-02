"""
Yantra — Avatara's observability layer.

Writes a structured JSONL trace for every session: routing decisions,
per-avatar latency, token estimates, and errors. No external service
required — plain files in yantra_traces/.

Trace schema (one JSON object per line):
  {
    "ts":         ISO timestamp,
    "session_id": str,
    "user_id":    str,
    "event":      "session_start" | "avatar_start" | "avatar_done" | "session_done" | "error",
    "avatar":     str | null,
    "task":       str | null,       # truncated to 200 chars
    "result_len": int | null,       # chars in result
    "latency_ms": int | null,       # wall-clock ms for this avatar
    "total_ms":   int | null,       # session_done only
    "error":      str | null,
  }

Usage:
  tracer = Tracer(session_id, user_id)
  tracer.session_start(query)
  with tracer.avatar_span("Narasimha", task) as span:
      result = await run_avatar(...)
      span.finish(result)
  tracer.session_done()
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

_TRACE_DIR = Path(__file__).parent / "yantra_traces"
_TRACE_DIR.mkdir(exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, record: dict) -> None:
    try:
        with path.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


class _AvatarSpan:
    def __init__(self, tracer: "Tracer", avatar: str, task: str) -> None:
        self._tracer = tracer
        self._avatar = avatar
        self._task = task
        self._start = time.monotonic()

    def finish(self, result: str = "") -> None:
        latency_ms = int((time.monotonic() - self._start) * 1000)
        self._tracer._write_event(
            event="avatar_done",
            avatar=self._avatar,
            task=self._task,
            result_len=len(result),
            latency_ms=latency_ms,
        )


class Tracer:
    def __init__(self, session_id: str, user_id: str) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self._session_start = time.monotonic()
        self._path = _TRACE_DIR / f"{session_id}.jsonl"

    def _write_event(self, event: str, **kwargs: Any) -> None:
        record = {
            "ts":         _now(),
            "session_id": self.session_id,
            "user_id":    self.user_id,
            "event":      event,
            "avatar":     kwargs.get("avatar"),
            "task":       (kwargs.get("task") or "")[:200] or None,
            "result_len": kwargs.get("result_len"),
            "latency_ms": kwargs.get("latency_ms"),
            "total_ms":   kwargs.get("total_ms"),
            "error":      kwargs.get("error"),
        }
        _write(self._path, {k: v for k, v in record.items() if v is not None})

    def session_start(self, query: str) -> None:
        self._write_event(event="session_start", task=query)

    @contextmanager
    def avatar_span(self, avatar: str, task: str) -> Generator[_AvatarSpan, None, None]:
        self._write_event(event="avatar_start", avatar=avatar, task=task)
        span = _AvatarSpan(self, avatar, task)
        try:
            yield span
        except Exception as exc:
            self._write_event(event="error", avatar=avatar, error=str(exc))
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
        """Return a summary dict: avatars called, latencies, total time."""
        events = Tracer.load(session_id)
        done_events = [e for e in events if e.get("event") == "avatar_done"]
        session_done = next((e for e in events if e.get("event") == "session_done"), {})
        return {
            "session_id":   session_id,
            "avatars":      [e["avatar"] for e in done_events],
            "latencies_ms": {e["avatar"]: e.get("latency_ms") for e in done_events},
            "total_ms":     session_done.get("total_ms"),
        }
