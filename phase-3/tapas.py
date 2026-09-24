"""
Tapas — Avatara's self-evolution layer.

After every session, Tapas:
  1. Scores the output with an independent judge model (0.0–1.0)
  2. Deduplicates against existing sutras (cosine similarity gate)
  3. Distills ONE transferable rule from the session (M4.4) and promotes it
     to sutras.jsonl — no rule extractable → no promotion (fail closed)
  4. Flags low-scoring sessions to weak_sessions.jsonl for prompt revision,
     and strikes any sutras that were injected into the failing run (M4.4
     demotion — outcome-based unlearning)

Sutra schema (one JSON per line in sutras.jsonl):
  {
    "id":          uuid,
    "ts":          ISO timestamp,
    "session_id":  str,
    "avatar":      str,
    "kind":        "rule" (M4.4; absent on legacy verbatim sutras),
    "rule":        str  — one distilled imperative rule (≤ ~240 chars),
    "query":       str  — kept for relevance ranking,
    "result":      str  — short evidence snippet (300 chars; legacy: 1500),
    "score":       float 0.0–1.0,
    "score_reason":str,
    "ttl_days":    int  (default 90 — sutras expire),
    "profile_id":  str  — whose session taught it (absent on older rows)
  }

Thresholds (tunable via env vars):
  TAPAS_PROMOTE_THRESHOLD   float, default 0.75  (score >= this → promote)
  TAPAS_FLAG_THRESHOLD      float, default 0.45  (score <  this → flag as weak)
  TAPAS_SIM_THRESHOLD       float, default 0.92  (cosine sim >= this → deduplicate)
  TAPAS_SUTRA_TTL_DAYS      int,   default 90

Judge model (independent from the avatar models):
  TAPAS_JUDGE_MODEL         str    model string for LiteLLM (default: deepseek/deepseek-flash)
  TAPAS_JUDGE_API_BASE      str    custom API base URL (e.g. for MiMo, local vLLM)
  TAPAS_JUDGE_API_KEY       str    API key if different from the default provider key

  Recommended: keep TAPAS_JUDGE_MODEL on a stable critique-capable model that your provider
  actually supports. DeepSeek V4.1 Flash is Narad's current default judge.
"""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from narad_config import SUTRA_DEMOTIONS_PATH as _DEMOTIONS_PATH
from narad_config import SUTRAS_PATH as _SUTRAS_PATH
from narad_config import WEAK_SESSIONS_PATH as _WEAK_PATH
from profile_context import current_profile_id, profile_data_path

PROMOTE_THRESHOLD = float(os.environ.get("TAPAS_PROMOTE_THRESHOLD", "0.80"))  # raised from 0.75
FLAG_THRESHOLD    = float(os.environ.get("TAPAS_FLAG_THRESHOLD",    "0.45"))
SIM_THRESHOLD     = float(os.environ.get("TAPAS_SIM_THRESHOLD",     "0.92"))
SUTRA_TTL_DAYS    = int(os.environ.get("TAPAS_SUTRA_TTL_DAYS",      "90"))

# Judge model — use the same current DeepSeek lane as Narad orchestration.
_JUDGE_MODEL    = os.environ.get("TAPAS_JUDGE_MODEL",    "deepseek/deepseek-flash")
_JUDGE_API_BASE = os.environ.get("TAPAS_JUDGE_API_BASE") or None
_JUDGE_API_KEY  = os.environ.get("TAPAS_JUDGE_API_KEY")  or None


@dataclass(frozen=True)
class TapasScore:
    score: float
    reason: str
    hallucination_free: bool
    sequence_correct: bool
    provider: str
    model: str
    confidence: float
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    fallback_reason: str | None = None
    dimensions: dict[str, float] | None = None

    def metadata(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("score", "reason", "hallucination_free", "sequence_correct"):
            payload.pop(key, None)
        return payload


_SCORE_METADATA: ContextVar[dict[str, Any]] = ContextVar(
    "tapas_score_metadata", default={}
)


def score_metadata() -> dict[str, Any]:
    return dict(_SCORE_METADATA.get())


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
Independent run evidence: {evidence}

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


def _record_judge_cost(response: Any, source: str) -> None:
    """M4.1: judge/critique calls hit the cost ledger — learning has a price tag.

    Best-effort; never lets ledger problems affect scoring.
    """
    try:
        usage = getattr(response, "usage", None)
        if not usage:
            return
        from cost_ledger import record
        record(
            source=source,
            model=_JUDGE_MODEL,
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        )
    except Exception:
        pass


def _score_session_llm(
    query: str,
    avatar: str,
    result: str,
    evidence: dict[str, Any] | None = None,
) -> TapasScore:
    """Existing deliberative judge path, retained as ambiguity fallback."""
    started = time.perf_counter()
    input_tokens = 0
    output_tokens = 0
    estimated_cost = 0.0
    try:
        import privacy_gateway as litellm_gateway
        avatar_rubric = _AVATAR_RUBRIC.get(avatar, "")
        prompt = _SCORE_PROMPT_BASE.format(
            avatar_rubric=avatar_rubric,
            query=query[:600],
            avatar=avatar,
            result=result[:1200],
            evidence=json.dumps(evidence or {"available": False}, ensure_ascii=False)[:2400],
        )
        kwargs: dict = dict(
            model=_JUDGE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1200,
            response_format={"type": "json_object"},
        )
        # Tapas needs a short structured verdict, not a long hidden chain of
        # thought. DeepSeek otherwise can consume the entire output budget in
        # reasoning_content and return an empty JSON response.
        if _JUDGE_MODEL.lower().startswith("deepseek/"):
            kwargs["reasoning_effort"] = "none"
        if _JUDGE_API_BASE:
            kwargs["api_base"] = _JUDGE_API_BASE
        if _JUDGE_API_KEY:
            kwargs["api_key"] = _JUDGE_API_KEY

        response = _litellm_with_retry(litellm_gateway, {**kwargs, "narad_source": "tapas"})
        _record_judge_cost(response, "tapas_judge")
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        try:
            from cost_ledger import estimate_cost

            estimated_cost, _ = estimate_cost(_JUDGE_MODEL, input_tokens, output_tokens)
        except Exception:
            estimated_cost = 0.0
        message = response.choices[0].message
        raw = str(
            getattr(message, "content", None)
            or getattr(message, "reasoning_content", None)
            or ""
        ).strip()
        data              = _extract_judge_json(raw)
        required = {"score", "reason", "hallucination_free", "sequence_correct"}
        if not required.issubset(data):
            missing = ", ".join(sorted(required - set(data)))
            raise ValueError(f"judge response missing required fields: {missing}")
        if not isinstance(data["hallucination_free"], bool) or not isinstance(data["sequence_correct"], bool):
            raise ValueError("judge response gates must be booleans")
        score             = float(data.get("score", 0.5))
        reason            = str(data.get("reason", ""))
        hallucination_free = bool(data.get("hallucination_free", True))
        sequence_correct   = bool(data.get("sequence_correct", True))

        # sequence_correct=False → -0.20 penalty on the raw score
        if not sequence_correct:
            score = max(0.0, score - 0.20)
            reason = f"[sequence violation -0.20] {reason}"
        return TapasScore(
            score=max(0.0, min(1.0, score)),
            reason=reason,
            hallucination_free=hallucination_free,
            sequence_correct=sequence_correct,
            provider="llm",
            model=_JUDGE_MODEL,
            confidence=0.5,
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated_cost,
        )
    except Exception as exc:
        return TapasScore(
            score=0.5,
            reason=f"scoring unavailable: {exc}",
            hallucination_free=True,
            sequence_correct=True,
            provider="llm",
            model=_JUDGE_MODEL,
            confidence=0.0,
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated_cost,
            fallback_reason=str(exc)[:300],
        )


_JEV_SCORE_LEVELS = [
    "1/10 - absent, wrong, or actively harmful",
    "2/10 - almost entirely wrong or unusable",
    "3/10 - major failures dominate",
    "4/10 - materially deficient",
    "5/10 - mixed or minimally acceptable",
    "6/10 - useful but has clear gaps",
    "7/10 - solid and fit for purpose",
    "8/10 - strong with only minor gaps",
    "9/10 - excellent and unusually complete",
    "10/10 - fully satisfies the stated dimension",
]


def _score_session_jev(
    query: str,
    avatar: str,
    result: str,
    evidence: dict[str, Any] | None = None,
) -> TapasScore:
    """Use Jev for narrow judgments; Narad code owns the final arithmetic."""
    from decision_engine import DecisionQuestion, evaluate_decision

    state = {
        "query": query[:600],
        "avatar": avatar,
        "response": result[:1200],
        "independent_run_evidence": evidence or {"available": False},
        "avatar_rubric": _AVATAR_RUBRIC.get(avatar, ""),
        "scoring_policy": {
            "correctness": "Judge only against supplied material and widely established facts; unsupported specifics are a defect.",
            "specificity": "Prefer concrete relevant details over vague prose.",
            "actionability": "The user should be able to make progress without avoidable follow-up.",
            "conciseness": "Use no more detail than the task needs; do not punish necessary completeness.",
        },
    }
    questions = {
        name: DecisionQuestion(
            "score",
            instruction,
            _JEV_SCORE_LEVELS,
        )
        for name, instruction in {
            "correctness": "Score the response's factual and task-level correctness from 0 to 10.",
            "specificity": "Score how concrete and specifically useful the response is from 0 to 10.",
            "actionability": "Score how readily the user can act on the response from 0 to 10.",
            "conciseness": "Score whether the response is appropriately concise for the task from 0 to 10.",
        }.items()
    }
    questions.update({
        "unsupported_claims": DecisionQuestion(
            "noul",
            "Does the response contain a fabricated, contradicted, or unsupported factual claim, citation, API, statistic, or completion claim?",
            {
                "true": "At least one material claim lacks support or conflicts with supplied state",
                "false": "No material unsupported claim is detectable from supplied state",
            },
        ),
        "sequence_violation": DecisionQuestion(
            "noul",
            "For a phase-gated workflow, did the response skip a mandatory phase or stopping point? Answer false when no phased workflow applies.",
        ),
    })
    decision = evaluate_decision(
        "tapas_score_v1",
        state,
        questions,
        allow_sensitive=False,
    )
    if not decision.available:
        return TapasScore(
            score=0.5,
            reason=f"Jev scoring unavailable: {decision.error or decision.status}",
            hallucination_free=True,
            sequence_correct=True,
            provider="jev",
            model=decision.model,
            confidence=0.0,
            latency_ms=decision.latency_ms,
            fallback_reason=decision.error or decision.status,
        )

    dimensions = {
        name: (max(0.0, min(9.0, float(decision.answers[name].value))) + 1.0) / 10.0
        for name in ("correctness", "specificity", "actionability", "conciseness")
    }
    score = (
        dimensions["correctness"] * 0.35
        + dimensions["specificity"] * 0.30
        + dimensions["actionability"] * 0.25
        + dimensions["conciseness"] * 0.10
    )
    unsupported = decision.answers["unsupported_claims"]
    sequence = decision.answers["sequence_violation"]
    unsupported_probability = float(unsupported.probabilities.get("true", 0.5))
    sequence_probability = float(sequence.probabilities.get("true", 0.5))
    # Hard gates need stronger evidence than the primitive's ordinary 0.5
    # decision boundary. Ambiguous gates lower promotion confidence instead.
    hallucination_free = unsupported_probability < 0.75
    sequence_correct = sequence_probability < 0.75
    if not sequence_correct:
        score = max(0.0, score - 0.20)

    strongest = max(dimensions, key=dimensions.get)
    weakest = min(dimensions, key=dimensions.get)
    notes = [f"strongest: {strongest} {dimensions[strongest]:.2f}; weakest: {weakest} {dimensions[weakest]:.2f}"]
    if not hallucination_free:
        notes.insert(0, "unsupported claim detected")
    if not sequence_correct:
        notes.insert(0, "sequence violation -0.20")
    dimension_confidence = sum(
        decision.answers[name].confidence
        for name in ("correctness", "specificity", "actionability", "conciseness")
    ) / 4.0
    confidence = dimension_confidence
    if score >= PROMOTE_THRESHOLD:
        confidence = min(confidence, unsupported.confidence, sequence.confidence)
    return TapasScore(
        score=round(max(0.0, min(1.0, score)), 4),
        reason="; ".join(notes),
        hallucination_free=hallucination_free,
        sequence_correct=sequence_correct,
        provider="jev",
        model=decision.model,
        confidence=confidence,
        latency_ms=decision.latency_ms,
        input_tokens=decision.input_tokens,
        output_tokens=decision.output_tokens,
        estimated_cost_usd=decision.estimated_cost_usd,
        dimensions=dimensions,
    )


def score_session_detailed(
    query: str,
    avatar: str,
    result: str,
    *,
    provider: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> TapasScore:
    """Score with Jev when confident, otherwise retain the deliberative judge."""
    selected = (provider or os.environ.get("TAPAS_JUDGE_PROVIDER", "auto")).strip().lower()
    if selected not in {"auto", "jev", "llm"}:
        selected = "auto"
    if selected in {"auto", "jev"}:
        jev = _score_session_jev(query, avatar, result, evidence)
        if selected == "jev":
            return jev
        try:
            minimum_confidence = float(os.environ.get("TAPAS_JEV_MIN_CONFIDENCE", "0.45"))
        except ValueError:
            minimum_confidence = 0.45
        if jev.confidence >= minimum_confidence and not jev.reason.startswith("Jev scoring unavailable:"):
            return jev
        llm = _score_session_llm(query, avatar, result, evidence)
        return TapasScore(
            **{
                **asdict(llm),
                "fallback_reason": (
                    jev.fallback_reason
                    or f"Jev confidence {jev.confidence:.2f} below {minimum_confidence:.2f}"
                ),
            }
        )
    return _score_session_llm(query, avatar, result, evidence)


def score_session(
    query: str,
    avatar: str,
    result: str,
    *,
    evidence: dict[str, Any] | None = None,
) -> tuple[float, str, bool, bool]:
    """Compatibility wrapper returning Tapas's established four-value contract."""
    scored = score_session_detailed(query, avatar, result, evidence=evidence)
    _SCORE_METADATA.set(scored.metadata())
    return scored.score, scored.reason, scored.hallucination_free, scored.sequence_correct


# ── Rule distillation (M4.4) ─────────────────────────────────────────────────

_DISTILL_PROMPT = """\
You are a knowledge distiller for an AI assistant's behavior bank.
A session scored highly and is about to be saved as a learned pattern.
Do NOT save the response verbatim. Extract ONE transferable rule instead.

Avatar: {avatar}
Query: {query}
Response (excerpt): {result}

A good rule is:
- imperative ("For HDFC bank CSVs, map the narration column to description")
- transferable to similar future tasks, not a restatement of this one answer
- self-contained (no "as shown above", no references to this session)
- at most 240 characters

If the response is a one-off answer with no reusable technique, convention,
or mapping behind it, there is no rule — say so.

Return ONLY JSON: {{"rule": "<the rule>" }} or {{"rule": null}} if none exists.
No markdown fences, no prose outside the JSON.
"""

_RULE_MAX_CHARS = 240


def _distill_rule(query: str, avatar: str, result: str) -> tuple[str | None, str]:
    """Distill one transferable rule from a promotable session.

    Returns (rule, note). rule=None → nothing promotable (fail closed):
    either the judge said no rule exists, or the distill call failed —
    a verbatim replay is never written as a fallback.
    """
    try:
        import privacy_gateway as litellm_gateway
        prompt = _DISTILL_PROMPT.format(
            avatar=avatar,
            query=query[:600],
            result=result[:1200],
        )
        kwargs: dict = dict(
            model=_JUDGE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=300,
        )
        if _JUDGE_API_BASE:
            kwargs["api_base"] = _JUDGE_API_BASE
        if _JUDGE_API_KEY:
            kwargs["api_key"] = _JUDGE_API_KEY
        response = _litellm_with_retry(litellm_gateway, {**kwargs, "narad_source": "tapas"})
        _record_judge_cost(response, "tapas_distill")
        raw = response.choices[0].message.content.strip()
        data = _extract_judge_json(raw)
        rule = data.get("rule")
        if not rule or not str(rule).strip():
            return None, "no transferable rule in session"
        rule = " ".join(str(rule).split())[:_RULE_MAX_CHARS]
        if len(rule) < 12:  # degenerate output ("ok", "yes", …)
            return None, "distilled rule too short to be a rule"
        return rule, ""
    except Exception as exc:
        return None, f"distillation unavailable: {exc}"


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

    Returns (passed: bool, concerns: str). Fails CLOSED (M4.4): if the
    critique call errors, the candidate is NOT promoted — an unreviewed
    pattern must never enter the behavior bank. The session can re-earn
    promotion on a future run when the judge is reachable.
    """
    try:
        import privacy_gateway as litellm_gateway
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
        response = _litellm_with_retry(litellm_gateway, {**kwargs, "narad_source": "tapas"})
        _record_judge_cost(response, "tapas_critique")
        raw  = response.choices[0].message.content.strip()
        data = _extract_judge_json(raw)
        return bool(data.get("pass", True)), str(data.get("concerns", ""))
    except Exception as exc:
        return False, f"critique unavailable — failing closed: {exc}"


# ── Deduplication (cosine similarity) ────────────────────────────────────────

def _embed_local(text: str) -> list[float]:
    """Embed using OpenAI (same model as Smriti for consistency)."""
    return _batch_embed([text])[0]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _batch_embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts in a single API call."""
    import privacy_gateway
    resp = privacy_gateway.embedding(
        narad_source="tapas",
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

def _sutras_path() -> Path:
    return profile_data_path("sutras.jsonl", legacy_default=_SUTRAS_PATH)


def _weak_path() -> Path:
    return profile_data_path("weak_sessions.jsonl", legacy_default=_WEAK_PATH)


def _demotions_path() -> Path:
    return profile_data_path("sutra_demotions.jsonl", legacy_default=_DEMOTIONS_PATH)

def load_sutras(active_only: bool = True) -> list[dict]:
    """Load all sutras from disk, optionally filtering expired ones."""
    path = _sutras_path()
    if not path.exists():
        return []
    now = datetime.now(timezone.utc)
    sutras = []
    for line in path.read_text().splitlines():
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


# ── Demotion strikes (M4.4) ───────────────────────────────────────────────────

def _strike_sutras(applied_sutra_ids: list[str], session_id: str, avatar: str,
                   score: float, reason: str) -> int:
    """Record one demotion strike per sutra that was injected into a failing run.

    sutra_engine counts strikes (since the last user re-accept) and demotes at
    SUTRA_DEMOTE_STRIKES. Best-effort; returns the number of strikes written.
    """
    written = 0
    ts = datetime.now(timezone.utc).isoformat()
    for sutra_id in applied_sutra_ids:
        if not sutra_id:
            continue
        try:
            path = _demotions_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            _append(path, {
                "sutra_id":   sutra_id,
                "ts":         ts,
                "session_id": session_id,
                "avatar":     avatar,
                "score":      score,
                "reason":     reason[:200],
            })
            written += 1
            try:
                from karma_log import log_karma
                log_karma("demotion_strike", sutra_id, avatar,
                          f"Injected into failing session (score {score:.2f})",
                          triggered_by=session_id, tapas_score=score)
            except Exception:
                pass
        except Exception:
            continue
    return written


# ── Main entry point ──────────────────────────────────────────────────────────

def _load_run_evidence(session_id: str, avatar: str) -> dict[str, Any]:
    """Build a bounded fact packet from Yantra instead of trusting prose claims."""
    try:
        from yantra import Tracer

        events = Tracer.load(session_id)
    except Exception:
        events = []
    if not events:
        return {"available": False}
    done = next(
        (
            event
            for event in reversed(events)
            if event.get("event") == "avatar_done" and event.get("avatar") == avatar
        ),
        {},
    )
    trajectory = done.get("trajectory") if isinstance(done.get("trajectory"), dict) else {}
    tool_calls: list[dict[str, Any]] = []
    for turn in trajectory.get("turns", []) if isinstance(trajectory.get("turns"), list) else []:
        if not isinstance(turn, dict):
            continue
        for call in turn.get("tool_calls", []) if isinstance(turn.get("tool_calls"), list) else []:
            if not isinstance(call, dict):
                continue
            tool_calls.append({
                "tool": str(call.get("tool") or "")[:100],
                "result": str(call.get("result_preview") or "")[:300],
                "error": str(call.get("error") or "")[:300] or None,
                "latency_ms": int(call.get("latency_ms") or 0),
            })
    errors = [
        {
            "type": event.get("error_type"),
            "error": str(event.get("error") or "")[:300],
        }
        for event in events
        if event.get("event") == "error" and event.get("avatar") in {None, avatar}
    ]
    return {
        "available": bool(done or tool_calls or errors),
        "avatar_done": bool(done),
        "latency_ms": int(done.get("latency_ms") or 0),
        "usage": done.get("usage") if isinstance(done.get("usage"), dict) else {},
        "tool_calls": tool_calls[:30],
        "tool_failures": sum(bool(call.get("error")) for call in tool_calls),
        "errors": errors[:10],
        "phase_transitions": [
            str(event.get("phase"))[:100]
            for event in events
            if event.get("event") == "phase_transition" and event.get("avatar") == avatar
        ][:20],
    }

def process_session(
    session_id: str,
    query: str,
    avatar: str,
    result: str,
    applied_sutra_ids: list[str] | None = None,
) -> dict:
    """
    Score a session and promote or flag it.
    applied_sutra_ids: sutras that were injected into this run — struck for
    demotion if the session scores below FLAG_THRESHOLD (M4.4).
    Returns a dict with: score, reason, action (promoted|flagged|skipped…)
    """
    _SCORE_METADATA.set({})
    evidence = _load_run_evidence(session_id, avatar)
    score, reason, hallucination_free, sequence_correct = score_session(
        query,
        avatar,
        result,
        evidence=evidence,
    )
    scoring = score_metadata()

    def finish(payload: dict[str, Any]) -> dict[str, Any]:
        if scoring:
            payload["scoring"] = scoring
        return payload

    now = datetime.now(timezone.utc).isoformat()

    if reason.startswith("scoring unavailable:"):
        try:
            from karma_log import log_karma
            log_karma("tapas_skipped", "n/a", avatar, reason[:200],
                      triggered_by=session_id, tapas_score=None)
        except Exception:
            pass
        return finish({"score": score, "reason": reason, "action": "tapas_skipped"})

    # Hallucination hard gate — blocks regardless of other scores (P3-1)
    if not hallucination_free:
        try:
            from karma_log import log_karma
            log_karma("blocked_hallucination", "n/a", avatar,
                      f"Hallucination detected: {reason[:120]}",
                      triggered_by=session_id, tapas_score=score,
                      hallucination_free=False)
        except Exception:
            pass
        # M4.4: sutras injected into a hallucinating run take a strike —
        # this is the exact poisoning loop demotion exists to break.
        struck = _strike_sutras(applied_sutra_ids or [], session_id, avatar, 0.0, reason)
        out = {"score": 0.0, "reason": reason, "action": "blocked_hallucination"}
        if struck:
            out["sutras_struck"] = struck
        return finish(out)

    if score >= PROMOTE_THRESHOLD:
        if _is_duplicate(query, result):
            return finish({"score": score, "reason": reason, "action": "skipped_duplicate"})

        # M4.4: distill ONE transferable rule — no rule, no promotion (fail closed).
        rule, distill_note = _distill_rule(query, avatar, result)
        if rule is None:
            try:
                from karma_log import log_karma
                log_karma("skipped_no_rule", "n/a", avatar, distill_note[:160],
                          triggered_by=session_id, tapas_score=score)
            except Exception:
                pass
            return finish({"score": score, "reason": reason, "action": "skipped_no_rule",
                           "distill_note": distill_note})

        # Jnana pass: Constitutional AI self-critique on the distilled rule.
        # Fails closed — a critique error blocks promotion.
        critique_passed, concerns = _cai_critique(avatar, query, rule)
        if not critique_passed:
            try:
                from karma_log import log_karma
                log_karma("blocked_critique", "n/a", avatar, f"CAI blocked: {concerns[:120]}",
                          triggered_by=session_id, tapas_score=score, critique_passed=False)
            except Exception:
                pass
            return finish({"score": score, "reason": reason, "action": "blocked_by_critique",
                           "concerns": concerns})

        sutra = {
            "id":           str(uuid.uuid4()),
            "ts":           now,
            "session_id":   session_id,
            "avatar":       avatar,
            "kind":         "rule",
            "rule":         rule,
            "query":        query[:600],          # kept for relevance ranking
            "result":       result[:300],         # short evidence, not replay material
            "score":        score,
            "score_reason": reason,
            "ttl_days":     SUTRA_TTL_DAYS,
            "profile_id":   current_profile_id(),
        }
        _append(_sutras_path(), sutra)
        try:
            from karma_log import log_karma
            log_karma("promoted", sutra["id"], avatar, rule[:120],
                      triggered_by=session_id, tapas_score=score, critique_passed=True)
        except Exception:
            pass
        return finish({"score": score, "reason": reason, "action": "promoted", "rule": rule})

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
        _append(_weak_path(), weak)
        # M4.4: the sutras that steered this failing run take a strike each.
        struck = _strike_sutras(applied_sutra_ids or [], session_id, avatar, score, reason)
        out = {"score": score, "reason": reason, "action": "flagged"}
        if struck:
            out["sutras_struck"] = struck
        return finish(out)

    return finish({"score": score, "reason": reason, "action": "none"})


def sutra_summary() -> dict:
    """Quick stats on the sutra bank."""
    sutras = load_sutras()
    by_avatar: dict[str, int] = {}
    for s in sutras:
        by_avatar[s.get("avatar", "unknown")] = by_avatar.get(s.get("avatar", "unknown"), 0) + 1
    return {
        "total_active_sutras": len(sutras),
        "by_avatar": by_avatar,
        "path": str(_sutras_path()),
    }
