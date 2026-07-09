"""
Phase 5 — Sutra Engine

Reads sutras promoted by Tapas and manages their lifecycle:
  pending  → within COOLDOWN_HOURS of promotion (visible in UI, not yet injected)
  active   → post-cooldown, not reverted, not demoted (injected into avatar runs)
  demoted  → accumulated SUTRA_DEMOTE_STRIKES outcome strikes since the last
             user accept (M4.4 — never injected; user re-accept reactivates)
  reverted → user explicitly rejected (never injected)

Key functions:
  get_active_sutras(avatar)       → list[dict] for prompt injection
  get_all_sutras()                → all sutras with computed status
  accept_sutra(id)                → skip cooldown / clear demotion, activate now
  revert_sutra(id)                → mark as reverted forever
  format_for_injection(sutras)    → prompt block string (rule-aware, M4.4)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from narad_config import SUTRA_DEMOTIONS_PATH as _DEMOTIONS_PATH
from narad_config import SUTRA_OVERRIDES_PATH as _OVERRIDES_PATH
from narad_config import SUTRAS_PATH as _SUTRAS_PATH

COOLDOWN_HOURS = int(__import__("os").environ.get("SUTRA_COOLDOWN_HOURS", "24"))
MAX_ACTIVE_PER_AVATAR = int(__import__("os").environ.get("SUTRA_MAX_ACTIVE", "5"))
DEMOTE_STRIKES = int(__import__("os").environ.get("SUTRA_DEMOTE_STRIKES", "2"))


def sutras_enabled() -> bool:
    """M4.3: global sutra kill switch — NARAD_SUTRAS=off|0|false disables injection.

    Read per call (not at import) so the A/B eval can flip arms in-process.
    Default: enabled.
    """
    import os
    return os.environ.get("NARAD_SUTRAS", "on").strip().lower() not in ("off", "0", "false")

SutraStatus = Literal["pending", "active", "demoted", "reverted"]


# ── Override store (accept / revert) ─────────────────────────────────────────

def _load_overrides() -> dict[str, str]:
    """Returns {sutra_id: "accepted"|"reverted"} from the override log."""
    if not _OVERRIDES_PATH.exists():
        return {}
    out: dict[str, str] = {}
    for line in _OVERRIDES_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            out[rec["sutra_id"]] = rec["action"]
        except Exception:
            continue
    return out


def _load_last_accept_ts() -> dict[str, str]:
    """{sutra_id: ISO ts of the most recent 'accepted' override}. M4.4: strikes
    older than the last accept don't count — re-accepting clears the slate."""
    if not _OVERRIDES_PATH.exists():
        return {}
    out: dict[str, str] = {}
    for line in _OVERRIDES_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            if rec.get("action") == "accepted":
                out[rec["sutra_id"]] = rec.get("ts", "")
        except Exception:
            continue
    return out


# ── Demotion strikes (M4.4) ───────────────────────────────────────────────────

def _load_strikes() -> dict[str, list[str]]:
    """{sutra_id: [strike ISO timestamps]} from Tapas' demotion log."""
    if not _DEMOTIONS_PATH.exists():
        return {}
    out: dict[str, list[str]] = {}
    for line in _DEMOTIONS_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            sid = rec.get("sutra_id", "")
            if sid:
                out.setdefault(sid, []).append(rec.get("ts", ""))
        except Exception:
            continue
    return out


def _strikes_since_accept(sutra_id: str, strikes: dict[str, list[str]],
                          accept_ts: dict[str, str]) -> int:
    """Strikes newer than the sutra's last user accept (all strikes if never accepted)."""
    stamps = strikes.get(sutra_id, [])
    if not stamps:
        return 0
    floor = accept_ts.get(sutra_id, "")
    if not floor:
        return len(stamps)
    return sum(1 for ts in stamps if ts > floor)


def _write_override(sutra_id: str, action: Literal["accepted", "reverted"]) -> None:
    _OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "sutra_id": sutra_id,
        "action":   action,
        "ts":       datetime.now(timezone.utc).isoformat(),
    }
    with _OVERRIDES_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")


# ── Status resolution ─────────────────────────────────────────────────────────

def _compute_status(
    sutra: dict,
    overrides: dict[str, str],
    strikes: dict[str, list[str]] | None = None,
    accept_ts: dict[str, str] | None = None,
) -> SutraStatus:
    sid = sutra.get("id", "")
    override = overrides.get(sid)
    if override == "reverted":
        return "reverted"
    # M4.4: outcome strikes since the last accept demote — even a user-accepted
    # sutra that keeps steering runs into failures gets pulled from injection.
    if strikes and _strikes_since_accept(sid, strikes, accept_ts or {}) >= DEMOTE_STRIKES:
        return "demoted"
    if override == "accepted":
        return "active"

    ts = datetime.fromisoformat(sutra["ts"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - ts
    return "active" if age >= timedelta(hours=COOLDOWN_HOURS) else "pending"


def _cooldown_remaining(sutra: dict) -> str:
    """Human-readable time left in cooldown, e.g. '18h 32m'."""
    ts = datetime.fromisoformat(sutra["ts"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    expires = ts + timedelta(hours=COOLDOWN_HOURS)
    remaining = expires - datetime.now(timezone.utc)
    if remaining.total_seconds() <= 0:
        return "0m"
    h = int(remaining.total_seconds() // 3600)
    m = int((remaining.total_seconds() % 3600) // 60)
    return f"{h}h {m}m" if h else f"{m}m"


# ── Public API ────────────────────────────────────────────────────────────────

def get_all_sutras() -> list[dict]:
    """All non-expired sutras with computed status and cooldown info."""
    if not _SUTRAS_PATH.exists():
        return []
    overrides = _load_overrides()
    strikes = _load_strikes()
    accept_ts = _load_last_accept_ts()
    now = datetime.now(timezone.utc)
    result = []
    for line in _SUTRAS_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            s = json.loads(line)
            # Skip TTL-expired sutras
            ts = datetime.fromisoformat(s["ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ttl_days = s.get("ttl_days", 90)
            if (now - ts).days > ttl_days:
                continue
            status = _compute_status(s, overrides, strikes, accept_ts)
            s["status"] = status
            s["cooldown_remaining"] = _cooldown_remaining(s) if status == "pending" else None
            if status == "demoted":
                s["strike_count"] = _strikes_since_accept(s.get("id", ""), strikes, accept_ts)
            result.append(s)
        except Exception:
            continue
    # Newest first
    result.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return result


def _keyword_overlap(text_a: str, text_b: str) -> float:
    """Fraction of text_a words that appear in text_b. Fast, no API calls."""
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())
    if not words_a:
        return 0.0
    # Strip common stop words to improve signal
    _STOP = {"the","a","an","in","on","of","to","for","and","or","is","it","be",
             "this","that","with","as","at","by","from","was","are","have","has"}
    words_a -= _STOP
    words_b -= _STOP
    if not words_a:
        return 0.0
    return len(words_a & words_b) / len(words_a)


def get_active_sutras(avatar: str, task: str = "") -> list[dict]:
    """Active sutras for a given avatar, ranked by combined relevance + quality.

    When task is provided, sutras are ranked by:
      0.6 × tapas_score  +  0.4 × keyword_overlap(task, sutra.query)
    This surfaces patterns actually relevant to the current query rather than
    always returning the globally highest-scored ones.

    Returns [] when the NARAD_SUTRAS kill switch is off (M4.3) — this is the
    single gate every injection consumer flows through.
    """
    if not sutras_enabled():
        return []
    all_s = get_all_sutras()
    active = [
        s for s in all_s
        if s.get("avatar") == avatar and s["status"] == "active"
    ]
    if not active:
        return []

    if task:
        def _rank(s: dict) -> float:
            overlap = _keyword_overlap(task, s.get("query", ""))
            return s.get("score", 0.0) * 0.6 + overlap * 0.4
        active.sort(key=_rank, reverse=True)
    else:
        active.sort(key=lambda x: x.get("score", 0.0), reverse=True)

    return active[:MAX_ACTIVE_PER_AVATAR]


def get_pending_sutras(avatar: str | None = None) -> list[dict]:
    """Sutras still in the 24h cooldown window."""
    all_s = get_all_sutras()
    pending = [s for s in all_s if s["status"] == "pending"]
    if avatar:
        pending = [s for s in pending if s.get("avatar") == avatar]
    return pending


def accept_sutra(sutra_id: str) -> bool:
    """Bypass the cooldown — sutra becomes active immediately. Returns success.

    M4.4: also reactivates a demoted sutra — strikes recorded before this
    accept no longer count toward demotion (fresh slate; new strikes do).
    """
    all_s = get_all_sutras()
    ids = {s["id"] for s in all_s}
    if sutra_id not in ids:
        return False
    _write_override(sutra_id, "accepted")
    # Emit karma event
    try:
        from karma_log import log_karma
        sutra = next(s for s in all_s if s["id"] == sutra_id)
        log_karma("accepted", sutra_id, sutra.get("avatar", ""), sutra.get("query", "")[:120])
    except Exception:
        pass
    return True


def revert_sutra(sutra_id: str) -> bool:
    """Permanently revert a sutra — it will never be injected. Returns success."""
    all_s = get_all_sutras()
    ids = {s["id"] for s in all_s}
    if sutra_id not in ids:
        return False
    _write_override(sutra_id, "reverted")
    try:
        from karma_log import log_karma
        sutra = next(s for s in all_s if s["id"] == sutra_id)
        log_karma("reverted", sutra_id, sutra.get("avatar", ""), sutra.get("query", "")[:120])
    except Exception:
        pass
    return True


import re as _re_inject

_INJECTION_BLOCKLIST = [
    r"(?i)ignore\s+(all\s+)?previous\s+instructions?",
    r"(?i)\[INST\]",
    r"(?i)system\s*:",
    r"(?i)jailbreak",
    r"(?i)forget\s+(your|all)\s+(instructions?|rules?)",
    r"(?i)act\s+as\s+(if\s+)?you\s+(are|were)\s+(?!a\s+\w+\s+expert)",  # allow "act as a Python expert"
    r"(?i)DAN\s+mode",
    r"(?i)developer\s+mode",
]


def _sanitize_sutra(text: str) -> str | None:
    """Return None if sutra text contains prompt-injection patterns; text otherwise."""
    for pattern in _INJECTION_BLOCKLIST:
        if _re_inject.search(pattern, text):
            try:
                from karma_log import log_karma
                log_karma("blocked_injection", "sanitize", "system",
                          f"Injection pattern blocked: {text[:80]}")
            except Exception:
                pass
            return None
    return text


def format_for_injection(sutras: list[dict]) -> str:
    """Format active sutras as a prompt block for injection into avatar context.

    M4.4: distilled sutras (kind="rule") render as one imperative rule line —
    no response replay. Legacy verbatim sutras keep the query/response form
    until they expire (90-day TTL flushes them naturally).

    Sanitizes each sutra against prompt-injection patterns before inclusion.
    Sutras containing injection signals are silently dropped and logged to karma.
    """
    if not sutras:
        return ""
    lines = ["[LEARNED RULES — ranked by relevance to your current task]"]
    count = 0
    for s in sutras:
        score = s.get("score", 0.0)
        if s.get("kind") == "rule" and s.get("rule"):
            rule = " ".join(str(s["rule"]).split())[:300].strip()
            if _sanitize_sutra(rule) is None:
                continue  # injection pattern detected — skip this sutra
            count += 1
            lines.append(f"\n{count}. (confidence {score:.2f}) {rule}")
        else:
            query_summary  = s.get("query",  "")[:250].strip()
            result_snippet = s.get("result", "")[:500].strip()
            combined = f"{query_summary} {result_snippet}"
            if _sanitize_sutra(combined) is None:
                continue  # injection pattern detected — skip this sutra
            count += 1
            lines.append(f"\n{count}. Query (score {score:.2f}): {query_summary}")
            lines.append(f"   Response: {result_snippet}")
    if count == 0:
        return ""
    lines.append(
        "\n[Apply these rules where relevant. Adapt to the current task — do not copy verbatim.]"
    )
    return "\n".join(lines)
