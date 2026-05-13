"""
Phase 6 — Sankalpa Engine

Sankalpa (संकल्प) — vow, resolve, intention.

Where Smriti stores facts (what the user is), Sankalpa models style:
how the user works with this system — tone, output format, domain context,
recurring workflows. These become per-avatar prompt addenda injected before
every avatar run, personalising responses without the user having to repeat
themselves.

Pipeline:
  1. observe_session()       — called after every avatar run, lightweight
  2. _extract_via_llm()      — fires every EXTRACT_EVERY sessions per (user, avatar)
  3. get_active_sankalpas()  — inject into avatar context (post-cooldown only)
  4. accept_sankalpa()       — bypass cooldown, active immediately
  5. revert_sankalpa()       — permanently suppress, logged to karma

Sankalpa schema (one JSON per line in sankalpas.jsonl):
  {
    "id":           uuid,
    "ts":           ISO timestamp,
    "user_id":      str,
    "avatar":       str | "__global__",
    "pattern_type": "style" | "preference" | "domain" | "workflow",
    "content":      str  (one actionable sentence — injected verbatim),
    "evidence":     str  (session quote that triggered this),
    "confidence":   float 0.0–1.0,
    "source_count": int  (sessions that contributed),
    "ttl_days":     int  (default 180)
  }

Thresholds (tunable via env vars):
  SANKALPA_EXTRACT_EVERY   int,   default 5
  SANKALPA_MIN_CONFIDENCE  float, default 0.65
  SANKALPA_COOLDOWN_HOURS  int,   default 24
  SANKALPA_TTL_DAYS        int,   default 180
  SANKALPA_MAX_PER_AVATAR  int,   default 8
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal

import sys as _sys_nc
_sys_nc.path.insert(0, str(Path(__file__).parent.parent))
from narad_config import (
    SANKALPAS_PATH as _SANKALPAS_PATH,
    SANKALPA_OVERRIDES_PATH as _OVERRIDES_PATH,
    SANKALPA_SESSION_LOG_PATH as _SESSION_LOG_PATH,
)

EXTRACT_EVERY  = int(os.environ.get("SANKALPA_EXTRACT_EVERY",   "5"))
MIN_CONFIDENCE = float(os.environ.get("SANKALPA_MIN_CONFIDENCE", "0.65"))
COOLDOWN_HOURS = int(os.environ.get("SANKALPA_COOLDOWN_HOURS",   "24"))
TTL_DAYS       = int(os.environ.get("SANKALPA_TTL_DAYS",         "180"))
MAX_PER_AVATAR = int(os.environ.get("SANKALPA_MAX_PER_AVATAR",   "8"))


# ── Session logging ────────────────────────────────────────────────────────────

def _log_session(user_id: str, avatar: str, task: str, result: str) -> None:
    record = {
        "ts":      datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "avatar":  avatar,
        "task":    task[:400],
        "result":  result[:600],
    }
    _SESSION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _SESSION_LOG_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")


def _load_recent_sessions(user_id: str, avatar: str, n: int = 20) -> list[dict]:
    if not _SESSION_LOG_PATH.exists():
        return []
    entries = []
    for line in _SESSION_LOG_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            if r.get("user_id") == user_id and r.get("avatar") == avatar:
                entries.append(r)
        except Exception:
            continue
    return entries[-n:]


def _session_count(user_id: str, avatar: str) -> int:
    return len(_load_recent_sessions(user_id, avatar, n=100_000))


# ── LLM extraction ─────────────────────────────────────────────────────────────

_EXTRACT_PROMPT = """\
You are analysing how a specific user works with an AI avatar called {avatar}.

Below are the last {count} task/response pairs for this user with {avatar}:

{sessions_text}

Patterns already captured for this user (do not duplicate):
{existing_text}

Identify up to 3 DISTINCT, ACTIONABLE style patterns about how this user works.

Focus on:
- Output format preferences (length, structure, bullets vs prose)
- Tone preferences (formal/casual, terse/detailed)
- Domain context they consistently reference
- Recurring workflows or task types

Each pattern must be:
- A single sentence, usable as a direct prompt instruction
- Specific and observable — not vague ("user likes quality responses")
- Supported by at least 2 of the sessions above

Reply with ONLY a JSON array. Return [] if no strong patterns are visible:
[
  {{
    "pattern_type": "style" | "preference" | "domain" | "workflow",
    "content": "one sentence starting with: User prefers... / User works in... / When asking {avatar} for...",
    "confidence": 0.0-1.0,
    "evidence": "brief quote from one of the sessions above (max 80 chars)"
  }}
]"""


def _extract_via_llm(user_id: str, avatar: str) -> list[dict]:
    try:
        import litellm
        sessions = _load_recent_sessions(user_id, avatar, n=EXTRACT_EVERY * 2)
        if len(sessions) < 3:
            return []

        sample = sessions[-EXTRACT_EVERY:]
        sessions_text = "\n\n".join(
            f"Session {i+1}:\n  Task: {s['task']}\n  Response snippet: {s['result'][:300]}"
            for i, s in enumerate(sample)
        )

        existing = load_sankalpas(user_id=user_id, avatar=avatar, active_only=False)
        existing_text = "\n".join(f"- {s['content']}" for s in existing) or "(none yet)"

        prompt = _EXTRACT_PROMPT.format(
            avatar=avatar,
            count=len(sample),
            sessions_text=sessions_text,
            existing_text=existing_text,
        )

        response = litellm.completion(
            model=os.environ.get("DS_CHAT_MODEL", "deepseek/deepseek-chat"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.15,
            max_tokens=400,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()
        return json.loads(raw)
    except Exception:
        return []


# ── Deduplication ─────────────────────────────────────────────────────────────

def _jaccard(a: str, b: str) -> float:
    wa, wb = set(a.lower().split()), set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


# ── Storage ────────────────────────────────────────────────────────────────────

def _append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def load_sankalpas(
    user_id: str | None = None,
    avatar: str | None = None,
    active_only: bool = True,
) -> list[dict]:
    """Load sankalpas, optionally filtered, skipping TTL-expired entries."""
    if not _SANKALPAS_PATH.exists():
        return []
    now = datetime.now(timezone.utc)
    results = []
    for line in _SANKALPAS_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            s = json.loads(line)
            if user_id and s.get("user_id") != user_id:
                continue
            if avatar and s.get("avatar") not in (avatar, "__global__"):
                continue
            if active_only:
                ts = datetime.fromisoformat(s["ts"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if (now - ts).days > s.get("ttl_days", TTL_DAYS):
                    continue
            results.append(s)
        except Exception:
            continue
    return results


# ── Override store ─────────────────────────────────────────────────────────────

def _load_overrides() -> dict[str, str]:
    if not _OVERRIDES_PATH.exists():
        return {}
    out: dict[str, str] = {}
    for line in _OVERRIDES_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            out[rec["sankalpa_id"]] = rec["action"]
        except Exception:
            continue
    return out


def _write_override(sankalpa_id: str, action: Literal["accepted", "reverted"]) -> None:
    _append(_OVERRIDES_PATH, {
        "sankalpa_id": sankalpa_id,
        "action":      action,
        "ts":          datetime.now(timezone.utc).isoformat(),
    })


# ── Status resolution ─────────────────────────────────────────────────────────

def _compute_status(s: dict, overrides: dict[str, str]) -> str:
    override = overrides.get(s.get("id", ""))
    if override == "reverted":
        return "reverted"
    if override == "accepted":
        return "active"
    ts = datetime.fromisoformat(s["ts"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - ts
    return "active" if age >= timedelta(hours=COOLDOWN_HOURS) else "pending"


def _cooldown_remaining(s: dict) -> str | None:
    ts = datetime.fromisoformat(s["ts"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    expires = ts + timedelta(hours=COOLDOWN_HOURS)
    remaining = expires - datetime.now(timezone.utc)
    if remaining.total_seconds() <= 0:
        return None
    h = int(remaining.total_seconds() // 3600)
    m = int((remaining.total_seconds() % 3600) // 60)
    return f"{h}h {m}m" if h else f"{m}m"


# ── Public API ─────────────────────────────────────────────────────────────────

def get_all_sankalpas(user_id: str) -> list[dict]:
    """All non-expired sankalpas for a user, with computed status."""
    sankalpas = load_sankalpas(user_id=user_id, active_only=True)
    overrides = _load_overrides()
    result = []
    for s in sankalpas:
        s["status"]             = _compute_status(s, overrides)
        s["cooldown_remaining"] = _cooldown_remaining(s) if s["status"] == "pending" else None
        result.append(s)
    result.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return result


def get_active_sankalpas(user_id: str, avatar: str) -> list[dict]:
    """Active (post-cooldown) sankalpas for injection. Includes __global__ ones."""
    all_s = get_all_sankalpas(user_id)
    return [
        s for s in all_s
        if s["status"] == "active" and s.get("avatar") in (avatar, "__global__")
    ][:MAX_PER_AVATAR]


def format_for_injection(sankalpas: list[dict]) -> str:
    if not sankalpas:
        return ""
    lines = ["[USER STYLE — how this specific user works with you]"]
    for s in sankalpas:
        lines.append(f"- {s['content']}")
    lines.append("[Adapt to these patterns. Do not reference or repeat them explicitly.]")
    return "\n".join(lines)


def accept_sankalpa(sankalpa_id: str, user_id: str) -> bool:
    """Bypass cooldown — active immediately. Returns success."""
    all_s = get_all_sankalpas(user_id)
    if not any(s["id"] == sankalpa_id for s in all_s):
        return False
    _write_override(sankalpa_id, "accepted")
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent.parent / "phase-5"))
        from karma_log import log_karma
        s = next(x for x in all_s if x["id"] == sankalpa_id)
        log_karma("sankalpa_accepted", sankalpa_id, s.get("avatar", ""), s.get("content", "")[:120])
    except Exception:
        pass
    return True


def revert_sankalpa(sankalpa_id: str, user_id: str) -> bool:
    """Permanently suppress a sankalpa. Returns success."""
    all_s = get_all_sankalpas(user_id)
    if not any(s["id"] == sankalpa_id for s in all_s):
        return False
    _write_override(sankalpa_id, "reverted")
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent.parent / "phase-5"))
        from karma_log import log_karma
        s = next(x for x in all_s if x["id"] == sankalpa_id)
        log_karma("sankalpa_reverted", sankalpa_id, s.get("avatar", ""), s.get("content", "")[:120])
    except Exception:
        pass
    return True


def observe_session(user_id: str, avatar: str, task: str, result: str) -> None:
    """
    Called after every avatar run. Logs the session; triggers LLM extraction
    every EXTRACT_EVERY sessions. Fire-and-forget — never raises.
    """
    try:
        _log_session(user_id, avatar, task, result)
        count = _session_count(user_id, avatar)

        if count % EXTRACT_EVERY != 0:
            return

        patterns = _extract_via_llm(user_id, avatar)
        if not patterns:
            return

        existing = load_sankalpas(user_id=user_id, avatar=avatar, active_only=False)
        existing_contents = [s["content"].lower() for s in existing]

        for p in patterns:
            content    = str(p.get("content", "")).strip()
            confidence = float(p.get("confidence", 0.0))
            if not content or confidence < MIN_CONFIDENCE:
                continue
            if any(_jaccard(content.lower(), ec) > 0.6 for ec in existing_contents):
                continue

            sankalpa = {
                "id":           str(uuid.uuid4()),
                "ts":           datetime.now(timezone.utc).isoformat(),
                "user_id":      user_id,
                "avatar":       avatar,
                "pattern_type": p.get("pattern_type", "preference"),
                "content":      content[:300],
                "evidence":     str(p.get("evidence", ""))[:200],
                "confidence":   max(0.0, min(1.0, confidence)),
                "source_count": count,
                "ttl_days":     TTL_DAYS,
            }
            _append(_SANKALPAS_PATH, sankalpa)
            existing_contents.append(content.lower())

            try:
                import sys as _sys
                _sys.path.insert(0, str(Path(__file__).parent.parent / "phase-5"))
                from karma_log import log_karma
                log_karma("sankalpa_proposed", sankalpa["id"], avatar, content[:120])
            except Exception:
                pass
    except Exception:
        pass


def sankalpa_summary(user_id: str) -> dict:
    all_s = get_all_sankalpas(user_id)
    by_avatar: dict[str, int] = {}
    for s in all_s:
        a = s.get("avatar", "unknown")
        by_avatar[a] = by_avatar.get(a, 0) + 1
    return {
        "total":    len(all_s),
        "by_avatar": by_avatar,
        "path":     str(_SANKALPAS_PATH),
    }
