"""
Tapas — Avatara's self-evolution layer.

After every session, Tapas:
  1. Scores the output with an independent judge model (0.0–1.0)
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

Judge model (independent from the avatar models):
  TAPAS_JUDGE_MODEL         str    model string for LiteLLM (default: deepseek/deepseek-v4-pro)
  TAPAS_JUDGE_API_BASE      str    custom API base URL (e.g. for MiMo, local vLLM)
  TAPAS_JUDGE_API_KEY       str    API key if different from the default provider key

  Recommended: keep TAPAS_JUDGE_MODEL on a stable critique-capable model that your provider
  actually supports. For the current DeepSeek endpoint, v4-pro is the safest default.
"""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sys as _sys_nc
_sys_nc.path.insert(0, str(Path(__file__).parent.parent))
from narad_config import SUTRAS_PATH as _SUTRAS_PATH, WEAK_SESSIONS_PATH as _WEAK_PATH

PROMOTE_THRESHOLD = float(os.environ.get("TAPAS_PROMOTE_THRESHOLD", "0.80"))  # raised from 0.75
FLAG_THRESHOLD    = float(os.environ.get("TAPAS_FLAG_THRESHOLD",    "0.45"))
SIM_THRESHOLD     = float(os.environ.get("TAPAS_SIM_THRESHOLD",     "0.92"))
SUTRA_TTL_DAYS    = int(os.environ.get("TAPAS_SUTRA_TTL_DAYS",      "90"))

# Judge model — default to a provider-supported DeepSeek model unless explicitly overridden.
_JUDGE_MODEL    = os.environ.get("TAPAS_JUDGE_MODEL",    "deepseek/deepseek-v4-pro")
_JUDGE_API_BASE = os.environ.get("TAPAS_JUDGE_API_BASE") or None
_JUDGE_API_KEY  = os.environ.get("TAPAS_JUDGE_API_KEY")  or None


# ── Scoring (independent judge) ───────────────────────────────────────────────

_SCORE_PROMPT_BASE = """\
You are an impartial quality judge for an AI assistant called Avatara.
Score the response below on four dimensions, then compute a weighted final score.

Dimensions (each 0–10):
  A. Correctness   — is the information accurate and complete? (weight 0.35)
  B. Specificity   — concrete details, code, numbers vs. vague prose? (weight 0.30)
  C. Actionability — can the user immediately act on this without follow-up? (weight 0.25)
  D. Conciseness   — is it appropriately concise without padding? (weight 0.10)

{avatar_rubric}

Query: {query}
Avatar: {avatar}
Response: {result}

Step 1 — Score each dimension A/B/C/D as an integer 0–10.
Step 2 — Compute: final = (A*0.35 + B*0.30 + C*0.25 + D*0.10) / 10.0
Step 3 — Round final to two decimal places.
Step 4 — Evaluate two boolean gates:
  E. hallucination_free — Does the response avoid fabricated facts, invented citations,
     incorrect API/function names, or made-up statistics?
     true = no hallucinations detected. false = BLOCKS promotion regardless of other scores.
  F. sequence_correct — For phase-gated skills (teach, presentation_create, video_create,
     email_send, file_cleanup, symptom_check): did the response respect the mandatory phase
     order and stopping points? true if no phase was skipped or collapsed.
     Not applicable for single-turn responses — mark true. false = apply -0.20 score penalty.

Return ONLY a single valid JSON object with exactly four keys:
  "score"             — the computed float (must be between 0.00 and 1.00)
  "reason"            — one sentence naming the dominant strength or weakness
  "hallucination_free" — boolean (true/false)
  "sequence_correct"  — boolean (true/false)

No markdown fences, no prose outside the JSON.
"""

_AVATAR_RUBRIC = {
    "Parashurama": (
        "Avatar-specific rubric for Parashurama (engineering, debugging, automation):\n"
        "  Code completeness matters most — working runnable code beats pseudocode.\n"
        "  Reward concrete root-cause diagnosis before a fix when debugging.\n"
        "  Penalise heavily for: skeletons with TODO placeholders, missing imports,\n"
        "  security vulnerabilities, unsafe shell guidance, or unhandled edge cases.\n"
        "  Reward: correct language/runtime version, explicit automation boundaries,\n"
        "  and inline comments only where non-obvious."
    ),
    "Matsya": (
        "Avatar-specific rubric for Matsya (retrieval, documents, analysis):\n"
        "  Specific sourced facts and faithful document extraction score higher than vague summaries.\n"
        "  Reward exact quotes or clearly flagged inferences when summarizing documents.\n"
        "  Penalise for: fabricated sources, stale information stated as current,\n"
        "  or analysis presented as direct evidence without support."
    ),
    "Rama": (
        "Avatar-specific rubric for Rama (planning, calendar, finance, health logging):\n"
        "  Numbered, executable steps score higher than aspirational prose.\n"
        "  Reward clear time sequencing, safe previews before side effects, and realistic scheduling.\n"
        "  For finance or health support, penalise overconfident advice and missing caveats.\n"
        "  Penalise for: vague steps like 'think about X', missing done-criteria,\n"
        "  plans with more than 15 steps that aren't grouped into phases."
    ),
    "Krishna": (
        "Avatar-specific rubric for Krishna — apply the correct mode based on the query:\n"
        "\n"
        "GURU MODE (query contains: explain, quiz, study, flashcard, help me understand,\n"
        "  I don't understand, what is X [conceptual], teach me, make flashcards, curriculum):\n"
        "  +0.30 if the response ends with a question back to the student\n"
        "  +0.20 if a specific misconception is named and corrected\n"
        "  +0.10 if a concept is cited by name (not just described)\n"
        "  -0.40 if the response directly gives away the answer the student should work out\n"
        "  -0.20 if the response exceeds 150 words (guru mode must stay concise)\n"
        "  -0.20 if the response does not pose any question to the student\n"
        "  Max score: cap at 0.5 if the direct answer was given without Socratic progression.\n"
        "\n"
        "COMMUNICATION MODE (emails, posts, memos, announcements — default when not guru):\n"
        "  Complete, send-ready drafts score highest.\n"
        "  Penalise for: [PLACEHOLDER] text, skeleton templates, wrong tone for audience.\n"
        "  Reward: active voice, appropriate format for the medium (email/Slack/LinkedIn).\n"
        "\n"
        "MEDIA / WELLNESS MODE (multimedia generation, learner support, gentle triage):\n"
        "  Reward outputs that stay supportive, bounded, and medium-appropriate.\n"
        "  Penalise for unsafe reassurance, overclaiming expertise, or ignoring the requested format."
    ),
}


def _strip_reasoning(text: str) -> str:
    """Strip chain-of-thought blocks emitted by reasoning models before the JSON."""
    import re
    # Remove <think>…</think>, <thinking>…</thinking>, <reasoning>…</reasoning>
    text = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", text, flags=re.DOTALL)
    text = re.sub(r"<reasoning>.*?</reasoning>", "", text, flags=re.DOTALL)
    return text.strip()


def _extract_judge_json(raw: str) -> dict:
    """Strip reasoning, fences, and prose then parse the judge's JSON response.

    Deduplicates the identical extraction pattern previously in score_session()
    and _cai_critique(). Raises json.JSONDecodeError if no valid object found.
    Adapted from IBM/AssetOpsBench (Apache 2.0).
    """
    raw = _strip_reasoning(raw)
    # strip ```json ... ``` or ``` ... ``` fences
    if raw.startswith("```"):
        raw = raw.split("```")[1].strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()
    # extract first { ... } block — handles prose before/after the JSON
    brace_start = raw.find("{")
    brace_end   = raw.rfind("}") + 1
    if brace_start != -1 and brace_end > brace_start:
        raw = raw[brace_start:brace_end]
    return json.loads(raw)


def _litellm_with_retry(litellm_module: Any, kwargs: dict, max_retries: int = 2) -> Any:
    """Call litellm.completion with exponential backoff on transient errors.

    Raises the final exception if all retries are exhausted.
    """
    delay = 1.0
    for attempt in range(max_retries + 1):
        try:
            return litellm_module.completion(**kwargs)
        except Exception:
            if attempt == max_retries:
                raise
            time.sleep(delay)
            delay *= 2


def score_session(query: str, avatar: str, result: str) -> tuple[float, str, bool, bool]:
    """Score an avatar response using the configured judge model.

    Returns (score, reason, hallucination_free, sequence_correct).
    hallucination_free=False blocks promotion (hard zero).
    sequence_correct=False applies a -0.20 score penalty.
    """
    try:
        import litellm
        avatar_rubric = _AVATAR_RUBRIC.get(avatar, "")
        prompt = _SCORE_PROMPT_BASE.format(
            avatar_rubric=avatar_rubric,
            query=query[:600],
            avatar=avatar,
            result=result[:1200],
        )
        kwargs: dict = dict(
            model=_JUDGE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=500,
        )
        if _JUDGE_API_BASE:
            kwargs["api_base"] = _JUDGE_API_BASE
        if _JUDGE_API_KEY:
            kwargs["api_key"] = _JUDGE_API_KEY

        response = _litellm_with_retry(litellm, kwargs)
        raw = response.choices[0].message.content.strip()
        data              = _extract_judge_json(raw)
        score             = float(data.get("score", 0.5))
        reason            = str(data.get("reason", ""))
        hallucination_free = bool(data.get("hallucination_free", True))
        sequence_correct   = bool(data.get("sequence_correct", True))

        # sequence_correct=False → -0.20 penalty on the raw score
        if not sequence_correct:
            score = max(0.0, score - 0.20)
            reason = f"[sequence violation -0.20] {reason}"

        return max(0.0, min(1.0, score)), reason, hallucination_free, sequence_correct
    except Exception as exc:
        return 0.5, f"scoring unavailable: {exc}", True, True


# ── Constitutional AI self-critique (jnana pass) ─────────────────────────────

_CRITIQUE_PROMPT = """\
You are a safety reviewer for an AI assistant's behavior bank.
A learned pattern is about to be added to the system. Review it.

Avatar: {avatar}
Pattern source (task summary): {task}
Pattern to be added: {result}

Answer these three questions with yes/no and one sentence each:
1. Could this pattern cause harm if applied to a vulnerable user (e.g. someone in crisis, a child, someone with no domain expertise)?
2. Does this pattern respect user autonomy — not manipulative, not paternalistic, not biased toward a specific agenda?
3. Is this pattern specific enough to be consistently applied without over-generalizing?

Return JSON only: {{"pass": true/false, "concerns": "brief explanation or empty string"}}
"""


def _cai_critique(avatar: str, task: str, result: str) -> tuple[bool, str]:
    """Run Constitutional AI self-critique on a candidate sutra.

    Returns (passed: bool, concerns: str). Defaults to (True, '') on error
    so scoring failures don't block all promotions.
    """
    try:
        import litellm
        prompt = _CRITIQUE_PROMPT.format(
            avatar=avatar,
            task=task[:300],
            result=result[:600],
        )
        kwargs: dict = dict(
            model=_JUDGE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=200,
        )
        if _JUDGE_API_BASE:
            kwargs["api_base"] = _JUDGE_API_BASE
        if _JUDGE_API_KEY:
            kwargs["api_key"] = _JUDGE_API_KEY
        response = _litellm_with_retry(litellm, kwargs)
        raw  = response.choices[0].message.content.strip()
        data = _extract_judge_json(raw)
        return bool(data.get("pass", True)), str(data.get("concerns", ""))
    except Exception:
        return True, ""  # fail-open: don't block promotions on critique errors


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


def _batch_embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts in a single API call."""
    import litellm
    resp = litellm.embedding(
        model=os.environ.get("EMBED_MODEL", "text-embedding-3-small"),
        input=[t[:4000] for t in texts],
    )
    return [item["embedding"] for item in resp["data"]]


def _is_duplicate(query: str, result: str) -> bool:
    """Return True if an existing sutra is too similar to the new candidate.

    Uses a single batched embedding call instead of N serial calls, cutting
    deduplication cost by ~50× for a 50-sutra bank.
    """
    try:
        sutras = load_sutras()
        if not sutras:
            return False
        recent = sutras[-50:]
        candidate_text = f"{query} {result[:400]}"
        existing_texts = [f"{s.get('query','')} {s.get('result','')[:400]}" for s in recent]

        all_vecs = _batch_embed([candidate_text] + existing_texts)
        candidate_vec = all_vecs[0]
        for existing_vec in all_vecs[1:]:
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
    score, reason, hallucination_free, sequence_correct = score_session(query, avatar, result)
    now = datetime.now(timezone.utc).isoformat()

    if reason.startswith("scoring unavailable:"):
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).parent.parent / "phase-5"))
            from karma_log import log_karma
            log_karma("tapas_skipped", "n/a", avatar, reason[:200],
                      triggered_by=session_id, tapas_score=None)
        except Exception:
            pass
        return {"score": score, "reason": reason, "action": "tapas_skipped"}

    # Hallucination hard gate — blocks regardless of other scores (P3-1)
    if not hallucination_free:
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).parent.parent / "phase-5"))
            from karma_log import log_karma
            log_karma("blocked_hallucination", "n/a", avatar,
                      f"Hallucination detected: {reason[:120]}",
                      triggered_by=session_id, tapas_score=score,
                      hallucination_free=False)
        except Exception:
            pass
        return {"score": 0.0, "reason": reason, "action": "blocked_hallucination"}

    if score >= PROMOTE_THRESHOLD:
        if _is_duplicate(query, result):
            return {"score": score, "reason": reason, "action": "skipped_duplicate"}

        # Jnana pass: Constitutional AI self-critique before promotion
        critique_passed, concerns = _cai_critique(avatar, query, result)
        if not critique_passed:
            try:
                import sys as _sys
                _sys.path.insert(0, str(Path(__file__).parent.parent / "phase-5"))
                from karma_log import log_karma
                log_karma("blocked_critique", "n/a", avatar, f"CAI blocked: {concerns[:120]}",
                          triggered_by=session_id, tapas_score=score, critique_passed=False)
            except Exception:
                pass
            return {"score": score, "reason": reason, "action": "blocked_by_critique",
                    "concerns": concerns}

        sutra = {
            "id":           str(uuid.uuid4()),
            "ts":           now,
            "session_id":   session_id,
            "avatar":       avatar,
            "query":        query[:600],
            "result":       result[:1500],
            "score":        score,
            "score_reason": reason,
            "ttl_days":     SUTRA_TTL_DAYS,
        }
        _append(_SUTRAS_PATH, sutra)
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).parent.parent / "phase-5"))
            from karma_log import log_karma
            log_karma("promoted", sutra["id"], avatar, query[:120],
                      triggered_by=session_id, tapas_score=score, critique_passed=True)
        except Exception:
            pass
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
