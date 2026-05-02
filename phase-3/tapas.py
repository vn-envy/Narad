"""
Tapas — Avatara's self-evolution layer.

After every session, Tapas:
  1. Scores the output (Buddha as independent judge, 0.0–1.0)
  2. Deduplicates against existing sutras (cosine similarity gate)
  3. Promotes high-scoring outputs to sutras.jsonl
  4. Flags low-scoring sessions to weak_sessions.jsonl for prompt revision

Sutra schema (one JSON per line in sutras.jsonl):
  {
    "id":          uuid,
    "ts":          ISO timestamp,
    "session_id":  str,
    "avatar":      str,
    "query":       str,
    "result":      str (truncated to 800 chars),
    "score":       float 0.0–1.0,
    "score_reason":str,
    "ttl_days":    int  (default 90 — sutras expire)
  }

Thresholds (tunable via env vars):
  TAPAS_PROMOTE_THRESHOLD   float, default 0.75  (score >= this → promote)
  TAPAS_FLAG_THRESHOLD      float, default 0.45  (score <  this → flag as weak)
  TAPAS_SIM_THRESHOLD       float, default 0.92  (cosine sim >= this → deduplicate)
  TAPAS_SUTRA_TTL_DAYS      int,   default 90
"""

from __future__ import annotations

import json
import math
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BASE = Path(__file__).parent
_SUTRAS_PATH   = _BASE / "sutras.jsonl"
_WEAK_PATH     = _BASE / "weak_sessions.jsonl"

PROMOTE_THRESHOLD = float(os.environ.get("TAPAS_PROMOTE_THRESHOLD", "0.75"))
FLAG_THRESHOLD    = float(os.environ.get("TAPAS_FLAG_THRESHOLD",    "0.45"))
SIM_THRESHOLD     = float(os.environ.get("TAPAS_SIM_THRESHOLD",     "0.92"))
SUTRA_TTL_DAYS    = int(os.environ.get("TAPAS_SUTRA_TTL_DAYS",      "90"))


# ── Scoring (Buddha as judge) ─────────────────────────────────────────────────

_SCORE_PROMPT = """\
You are an impartial quality judge for an AI assistant called Avatara.

Score the following avatar response on a scale of 0.0 to 1.0.

Scoring rubric:
  1.0  — Complete, accurate, well-structured. Directly addresses the query.
  0.75 — Mostly complete. Minor gaps or slight verbosity.
  0.5  — Partially useful. Key information missing or significant hedging.
  0.25 — Superficial or evasive. Does not meaningfully address the query.
  0.0  — Wrong, harmful, or completely off-topic.

Query: {query}
Avatar: {avatar}
Response: {result}

Reply with ONLY a JSON object in this exact format:
{{"score": 0.82, "reason": "one sentence explaining the score"}}
"""


def score_session(query: str, avatar: str, result: str) -> tuple[float, str]:
    """Ask Buddha (DeepSeek V4 Pro) to score an avatar response. Returns (score, reason)."""
    try:
        import litellm
        prompt = _SCORE_PROMPT.format(
            query=query[:300],
            avatar=avatar,
            result=result[:600],
        )
        response = litellm.completion(
            model=os.environ.get("DS_PRO_MODEL", "deepseek/deepseek-v4-pro"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=80,
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1].strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()
        data = json.loads(raw)
        score = float(data.get("score", 0.5))
        reason = str(data.get("reason", ""))
        return max(0.0, min(1.0, score)), reason
    except Exception as exc:
        return 0.5, f"scoring unavailable: {exc}"


# ── Deduplication (cosine similarity) ────────────────────────────────────────

def _embed_local(text: str) -> list[float]:
    """Embed using OpenAI (same model as Smriti for consistency)."""
    import openai
    client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    resp = client.embeddings.create(model="text-embedding-3-small", input=text[:4000])
    return resp.data[0].embedding


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _is_duplicate(query: str, result: str) -> bool:
    """Return True if an existing sutra is too similar to this new candidate."""
    try:
        sutras = load_sutras()
        if not sutras:
            return False
        candidate_vec = _embed_local(f"{query} {result[:300]}")
        for s in sutras[-50:]:  # check recent 50 sutras only
            existing_text = f"{s.get('query','')} {s.get('result','')[:300]}"
            existing_vec = _embed_local(existing_text)
            if _cosine(candidate_vec, existing_vec) >= SIM_THRESHOLD:
                return True
        return False
    except Exception:
        return False


# ── Sutra storage ─────────────────────────────────────────────────────────────

def load_sutras(active_only: bool = True) -> list[dict]:
    """Load all sutras from disk, optionally filtering expired ones."""
    if not _SUTRAS_PATH.exists():
        return []
    now = datetime.now(timezone.utc)
    sutras = []
    for line in _SUTRAS_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            s = json.loads(line)
            if active_only:
                ts = datetime.fromisoformat(s["ts"])
                ttl = s.get("ttl_days", SUTRA_TTL_DAYS)
                age_days = (now - ts).days
                if age_days > ttl:
                    continue
            sutras.append(s)
        except Exception:
            continue
    return sutras


def _append(path: Path, record: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


# ── Main entry point ──────────────────────────────────────────────────────────

def process_session(
    session_id: str,
    query: str,
    avatar: str,
    result: str,
) -> dict:
    """
    Score a session and promote or flag it.
    Returns a dict with: score, reason, action (promoted|flagged|skipped)
    """
    score, reason = score_session(query, avatar, result)
    now = datetime.now(timezone.utc).isoformat()

    if score >= PROMOTE_THRESHOLD:
        if _is_duplicate(query, result):
            return {"score": score, "reason": reason, "action": "skipped_duplicate"}

        sutra = {
            "id":           str(uuid.uuid4()),
            "ts":           now,
            "session_id":   session_id,
            "avatar":       avatar,
            "query":        query[:400],
            "result":       result[:800],
            "score":        score,
            "score_reason": reason,
            "ttl_days":     SUTRA_TTL_DAYS,
        }
        _append(_SUTRAS_PATH, sutra)
        return {"score": score, "reason": reason, "action": "promoted"}

    elif score < FLAG_THRESHOLD:
        weak = {
            "ts":         now,
            "session_id": session_id,
            "avatar":     avatar,
            "query":      query[:400],
            "result":     result[:400],
            "score":      score,
            "reason":     reason,
        }
        _append(_WEAK_PATH, weak)
        return {"score": score, "reason": reason, "action": "flagged"}

    return {"score": score, "reason": reason, "action": "none"}


def sutra_summary() -> dict:
    """Quick stats on the sutra bank."""
    sutras = load_sutras()
    by_avatar: dict[str, int] = {}
    for s in sutras:
        by_avatar[s.get("avatar", "unknown")] = by_avatar.get(s.get("avatar", "unknown"), 0) + 1
    return {
        "total_active_sutras": len(sutras),
        "by_avatar": by_avatar,
        "path": str(_SUTRAS_PATH),
    }
