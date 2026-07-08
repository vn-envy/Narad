"""
Guru Engine — G1/G3 of the Gurukul track (see GURU-AND-ONBOARDING-PLAN.md).

Turns a learning topic into a syllabus: a small DAG of concept atoms, each
carrying a four-rung ELI5 ladder (analogy → plain → precise → formal), one
named misconception, and one check question. Tracks per-atom learner mastery
and grades check answers.

Everything degrades gracefully: with no provider key, syllabus generation and
grading fall back to deterministic templates/heuristics so the panel always
works (local-first honesty).

Syllabus schema (syllabus.json in the workspace dir):
  {
    "workspace_id": str,
    "topic": str,
    "generated_at": ISO ts,
    "generator": "llm" | "template",
    "atoms": [
      {
        "id": slug,
        "name": str,
        "prerequisites": [atom_id, ...],
        "eli5": str,          # 🧒 analogy from a five-year-old's world
        "plain": str,         # 📖 plain English, no jargon
        "precise": str,       # 🎯 correct terms, one paragraph
        "formal": str,        # 🎓 notation / formal definition
        "misconception": str, # the classic wrong belief, and the correction
        "check": {"q": str, "good_answer": str}
      }
    ]
  }

Learner state schema (learner_state.json in the workspace dir):
  { atom_id: {"status": "untaught"|"shaky"|"mastered",
              "attempts": int, "streak": int,
              "last_reviewed": ISO ts, "next_review": ISO ts} }

Env:
  GURU_MODEL          syllabus + artifact generation model (default deepseek/deepseek-v4-pro)
  GURU_GRADER_MODEL   answer grading model (default deepseek/deepseek-v4-flash)
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from narad_config import LEARNING_DIR

GURU_MODEL = os.environ.get("GURU_MODEL", "deepseek/deepseek-v4-pro")
GURU_GRADER_MODEL = os.environ.get("GURU_GRADER_MODEL", "deepseek/deepseek-v4-flash")

MAX_ATOMS = 12
_RUNGS = ("eli5", "plain", "precise", "formal")

# Review intervals in days, indexed by correct-answer streak (SM-2-lite).
_REVIEW_INTERVALS = (1, 3, 7, 21)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _slug(value: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return clean[:40] or "atom"


def _workspace_dir(user_id: str, workspace_id: str) -> Path:
    return LEARNING_DIR / user_id / workspace_id


def _syllabus_path(user_id: str, workspace_id: str) -> Path:
    return _workspace_dir(user_id, workspace_id) / "syllabus.json"


def _state_path(user_id: str, workspace_id: str) -> Path:
    return _workspace_dir(user_id, workspace_id) / "learner_state.json"


# ── LLM primitives (mirrors tapas.py's proven extraction pattern) ─────────────

def _strip_reasoning(text: str) -> str:
    text = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", text, flags=re.DOTALL)
    text = re.sub(r"<reasoning>.*?</reasoning>", "", text, flags=re.DOTALL)
    return text.strip()


def _extract_json(raw: str) -> dict:
    raw = _strip_reasoning(raw)
    if raw.startswith("```"):
        raw = raw.split("```")[1].strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()
    start, end = raw.find("{"), raw.rfind("}") + 1
    if start != -1 and end > start:
        raw = raw[start:end]
    return json.loads(raw)


def _record_cost(response: Any, source: str, model: str) -> None:
    try:
        usage = getattr(response, "usage", None)
        if not usage:
            return
        from cost_ledger import record
        record(
            source=source,
            model=model,
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        )
    except Exception:
        pass


def llm_json(
    prompt: str,
    *,
    model: str = GURU_MODEL,
    max_tokens: int = 3000,
    temperature: float = 0.4,
    source: str = "guru_engine",
    max_retries: int = 2,
) -> dict:
    """One JSON-returning LLM call with backoff. Raises on total failure."""
    import litellm

    delay = 1.0
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = litellm.completion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            _record_cost(response, source, model)
            return _extract_json(response.choices[0].message.content.strip())
        except Exception as error:  # transient API or parse error — retry once more
            last_error = error
            if attempt == max_retries:
                break
            time.sleep(delay)
            delay *= 2
    raise last_error or RuntimeError("llm_json failed")


# ── G1: syllabus generation ────────────────────────────────────────────────────

_SYLLABUS_PROMPT = """You are a master teacher decomposing a topic into atomic concepts.

TOPIC: {topic}

CONTEXT FROM THE LEARNER'S WORKSPACE (may be empty):
{packet}

Break the topic into 4-{max_atoms} concept atoms. Order them so prerequisites come
first. Every atom must be small enough to teach in one exchange.

For EACH atom provide all of:
- "id": short-kebab-slug
- "name": concept name a syllabus would use
- "prerequisites": list of atom ids that must be understood first (empty for roots)
- "eli5": an analogy a five-year-old's world would supply (sandwiches, playgrounds,
  mail carriers...). 2-3 sentences. No jargon at all.
- "plain": plain-English explanation, no jargon, 2-4 sentences.
- "precise": correct terminology, one tight paragraph.
- "formal": the formal definition/notation if one exists, else the most rigorous
  one-paragraph statement.
- "misconception": the classic wrong belief about this atom AND the correction.
- "check": {{"q": one question that tests real understanding (not recall),
             "good_answer": what a correct answer must contain}}

Rules: the prerequisite graph must be acyclic. Use only ids you defined.
Respond with ONLY a JSON object: {{"atoms": [...]}}"""


def _validate_syllabus(data: dict) -> list[str]:
    """Return a list of problems; empty list means valid."""
    problems: list[str] = []
    atoms = data.get("atoms")
    if not isinstance(atoms, list) or not atoms:
        return ["atoms missing or empty"]
    if len(atoms) > MAX_ATOMS:
        problems.append(f"too many atoms ({len(atoms)} > {MAX_ATOMS})")
    ids: set[str] = set()
    for atom in atoms:
        if not isinstance(atom, dict):
            problems.append("non-object atom")
            continue
        atom_id = str(atom.get("id", "")).strip()
        if not atom_id or atom_id in ids:
            problems.append(f"missing/duplicate id: {atom_id!r}")
        ids.add(atom_id)
        for rung in _RUNGS:
            if not str(atom.get(rung, "")).strip():
                problems.append(f"{atom_id}: empty rung {rung}")
        check = atom.get("check") or {}
        if not str(check.get("q", "")).strip():
            problems.append(f"{atom_id}: missing check question")
    # prerequisite refs + acyclicity (Kahn)
    graph = {
        str(atom.get("id", "")): [str(p) for p in (atom.get("prerequisites") or [])]
        for atom in atoms
        if isinstance(atom, dict)
    }
    for atom_id, prereqs in graph.items():
        for prereq in prereqs:
            if prereq not in graph:
                problems.append(f"{atom_id}: unknown prerequisite {prereq!r}")
    # topo over "prereq before atom": indegree = number of (known) prerequisites
    indegree = {atom_id: len([p for p in prereqs if p in graph]) for atom_id, prereqs in graph.items()}
    queue = [atom_id for atom_id, degree in indegree.items() if degree == 0]
    visited = 0
    dependents: dict[str, list[str]] = {atom_id: [] for atom_id in graph}
    for atom_id, prereqs in graph.items():
        for prereq in prereqs:
            if prereq in dependents:
                dependents[prereq].append(atom_id)
    while queue:
        node = queue.pop()
        visited += 1
        for dependent in dependents[node]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)
    if visited != len(graph):
        problems.append("prerequisite graph has a cycle")
    return problems


def _fallback_syllabus(topic: str) -> dict:
    """Deterministic single-atom syllabus when no provider is available."""
    atom_id = _slug(topic)
    return {
        "atoms": [{
            "id": atom_id,
            "name": topic,
            "prerequisites": [],
            "eli5": f"Imagine explaining {topic} to a friend using only toys and snacks — that picture is where we will start.",
            "plain": f"{topic}, explained without jargon. Connect a cloud model or regenerate to unlock the full breakdown.",
            "precise": f"A precise treatment of {topic} will appear here once a model generates the syllabus.",
            "formal": f"Formal definition of {topic} pending model generation.",
            "misconception": f"A common mistake is assuming {topic} is one indivisible idea — it almost never is.",
            "check": {
                "q": f"In your own words, what is {topic} for?",
                "good_answer": f"Any answer that states the purpose of {topic} in plain language.",
            },
        }],
    }


def generate_syllabus(
    *,
    user_id: str,
    workspace_id: str,
    topic: str,
    force: bool = False,
) -> dict[str, Any]:
    """Generate (or return cached) syllabus for a workspace. Never raises."""
    path = _syllabus_path(user_id, workspace_id)
    if path.exists() and not force:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass  # regenerate over a corrupt file

    packet = ""
    try:
        from learning_workspace import build_workspace_packet
        packet = build_workspace_packet(user_id=user_id, workspace_id=workspace_id)
    except Exception:
        pass

    generator = "llm"
    data: dict | None = None
    prompt = _SYLLABUS_PROMPT.format(topic=topic, packet=packet or "(none)", max_atoms=MAX_ATOMS)
    for _ in range(2):  # one retry on schema failure
        try:
            candidate = llm_json(prompt, model=GURU_MODEL, source="guru_syllabus")
            if not _validate_syllabus(candidate):
                data = candidate
                break
        except Exception:
            break
    if data is None:
        data = _fallback_syllabus(topic)
        generator = "template"

    syllabus = {
        "workspace_id": workspace_id,
        "topic": topic,
        "generated_at": _now_iso(),
        "generator": generator,
        "atoms": data["atoms"],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(syllabus, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return syllabus


def load_syllabus(*, user_id: str, workspace_id: str) -> dict[str, Any] | None:
    path = _syllabus_path(user_id, workspace_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── G3: learner state + grading ───────────────────────────────────────────────

def load_learner_state(*, user_id: str, workspace_id: str) -> dict[str, Any]:
    path = _state_path(user_id, workspace_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_learner_state(user_id: str, workspace_id: str, state: dict[str, Any]) -> None:
    path = _state_path(user_id, workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def record_check_result(
    *,
    user_id: str,
    workspace_id: str,
    atom_id: str,
    correct: bool,
) -> dict[str, Any]:
    """Apply a mastery transition and return the atom's new state entry."""
    state = load_learner_state(user_id=user_id, workspace_id=workspace_id)
    entry = state.get(atom_id) or {"status": "untaught", "attempts": 0, "streak": 0}
    entry["attempts"] = int(entry.get("attempts", 0)) + 1
    if correct:
        entry["streak"] = int(entry.get("streak", 0)) + 1
        entry["status"] = "mastered"
    else:
        entry["streak"] = 0
        entry["status"] = "shaky"
    interval_days = _REVIEW_INTERVALS[min(int(entry["streak"]), len(_REVIEW_INTERVALS) - 1)]
    entry["last_reviewed"] = _now_iso()
    entry["next_review"] = (_now() + timedelta(days=interval_days)).isoformat()
    state[atom_id] = entry
    try:
        _save_learner_state(user_id, workspace_id, state)
    except Exception:
        pass
    return entry


_GRADE_PROMPT = """You are grading a learner's answer to a check question.

CONCEPT: {name}
QUESTION: {question}
WHAT A CORRECT ANSWER MUST CONTAIN: {good_answer}
LEARNER'S ANSWER: {answer}

Grade generously on understanding, strictly on correctness. A paraphrase that
shows the idea is correct. Respond with ONLY JSON:
{{"correct": true|false,
  "feedback": "one warm sentence — what they got right or where it slipped",
  "remediation": "if wrong: re-explain with a DIFFERENT analogy than before, 2-3 sentences; if right: empty string"}}"""


def _heuristic_grade(good_answer: str, answer: str) -> dict[str, Any]:
    """Keyword-overlap fallback grading when no provider is available."""
    words = {w for w in re.findall(r"[a-z]{4,}", (good_answer or "").lower())}
    given = {w for w in re.findall(r"[a-z]{4,}", (answer or "").lower())}
    overlap = len(words & given) / max(len(words), 1)
    correct = overlap >= 0.3 and len(answer.strip()) >= 15
    return {
        "correct": correct,
        "feedback": "That covers the key idea." if correct
        else "That's missing some of the key idea — let's look at it another way.",
        "remediation": "" if correct
        else "Re-read the plain-English rung, then try describing it with your own example.",
        "grader": "heuristic",
    }


def grade_check_answer(
    *,
    user_id: str,
    workspace_id: str,
    atom_id: str,
    answer: str,
) -> dict[str, Any]:
    """Grade an answer, update mastery state, return grade + new state. Never raises."""
    syllabus = load_syllabus(user_id=user_id, workspace_id=workspace_id) or {}
    atom = next(
        (a for a in syllabus.get("atoms", []) if str(a.get("id")) == atom_id),
        None,
    )
    if atom is None:
        return {"correct": False, "feedback": "Unknown concept atom.", "remediation": "", "state": None}

    check = atom.get("check") or {}
    try:
        grade = llm_json(
            _GRADE_PROMPT.format(
                name=atom.get("name", atom_id),
                question=str(check.get("q", ""))[:400],
                good_answer=str(check.get("good_answer", ""))[:400],
                answer=(answer or "")[:800],
            ),
            model=GURU_GRADER_MODEL,
            max_tokens=400,
            temperature=0.2,
            source="guru_grader",
        )
        grade = {
            "correct": bool(grade.get("correct", False)),
            "feedback": str(grade.get("feedback", "")),
            "remediation": str(grade.get("remediation", "")),
            "grader": "llm",
        }
    except Exception:
        grade = _heuristic_grade(str(check.get("good_answer", "")), answer or "")

    entry = record_check_result(
        user_id=user_id,
        workspace_id=workspace_id,
        atom_id=atom_id,
        correct=grade["correct"],
    )
    grade["state"] = entry
    return grade


def due_reviews(*, user_id: str, workspace_id: str) -> list[str]:
    """Atom ids whose next_review is in the past (for G5 scheduler integration)."""
    state = load_learner_state(user_id=user_id, workspace_id=workspace_id)
    now = _now_iso()
    return [
        atom_id
        for atom_id, entry in state.items()
        if entry.get("next_review") and str(entry["next_review"]) <= now
    ]


# ── G2 support: LLM artifact generation / revision ────────────────────────────

_ARTIFACT_GEN_PROMPT = """You are creating a {kind} study artifact for the topic below.

TOPIC: {topic}
FOCUS/INSTRUCTION: {context}
WORKSPACE CONTEXT (may be empty):
{packet}

{shape}

Content must be real and correct — actual answers on card backs, actual concept
relationships. Never write placeholder or instruction-to-self text.
Respond with ONLY the JSON object."""

_FLASHCARD_SHAPE = """Produce 6-10 flashcards. JSON shape:
{"cards": [{"id": "card-1", "front": "a real question", "back": "the real, complete answer (1-3 sentences)", "tags": ["kebab-tag"]}]}"""

_CONCEPT_MAP_SHAPE = """Produce a concept map with 6-12 nodes. JSON shape:
{"nodes": [{"id": "kebab-id", "label": "short label", "note": "one-sentence explanation"}],
 "edges": [{"source": "id", "target": "id", "label": "relationship (e.g. 'enables', 'is a kind of')"}]}"""

_ARTIFACT_REVISE_PROMPT = """You maintain a {kind} study artifact. Apply the instruction and
return the COMPLETE revised document (not a diff) in the exact same JSON shape.

TOPIC: {topic}
INSTRUCTION: {instruction}

CURRENT DOCUMENT:
{doc}

Keep everything not touched by the instruction. Content must be real and correct.
Respond with ONLY the JSON object."""


def valid_artifact_doc(doc: Any, artifact_type: str) -> bool:
    if not isinstance(doc, dict):
        return False
    if artifact_type == "concept_map":
        nodes, edges = doc.get("nodes"), doc.get("edges")
        if not isinstance(nodes, list) or not nodes or not isinstance(edges, list):
            return False
        ids = {str(n.get("id")) for n in nodes if isinstance(n, dict) and n.get("id")}
        return len(ids) == len(nodes) and all(
            isinstance(e, dict) and str(e.get("source")) in ids and str(e.get("target")) in ids
            for e in edges
        )
    cards = doc.get("cards")
    return (
        isinstance(cards, list)
        and len(cards) > 0
        and all(
            isinstance(c, dict) and str(c.get("front", "")).strip() and str(c.get("back", "")).strip()
            for c in cards
        )
    )


def generate_artifact_doc(
    *,
    topic: str,
    artifact_type: str,
    teaching_context: str = "",
    packet: str = "",
) -> dict[str, Any]:
    """LLM-generate an artifact doc. Raises on failure (caller falls back to template)."""
    kind = "concept map" if artifact_type == "concept_map" else "flashcard deck"
    shape = _CONCEPT_MAP_SHAPE if artifact_type == "concept_map" else _FLASHCARD_SHAPE
    doc = llm_json(
        _ARTIFACT_GEN_PROMPT.format(
            kind=kind,
            topic=topic,
            context=teaching_context or "(general coverage of the topic)",
            packet=packet or "(none)",
            shape=shape,
        ),
        model=GURU_MODEL,
        source="guru_artifact",
    )
    if not valid_artifact_doc(doc, artifact_type):
        raise ValueError("generated artifact failed shape validation")
    return doc


def revise_artifact_doc(
    *,
    doc: dict[str, Any],
    topic: str,
    artifact_type: str,
    instruction: str,
) -> dict[str, Any]:
    """LLM-revise an artifact doc. Raises on failure (caller falls back to regex path)."""
    kind = "concept map" if artifact_type == "concept_map" else "flashcard deck"
    revised = llm_json(
        _ARTIFACT_REVISE_PROMPT.format(
            kind=kind,
            topic=topic,
            instruction=(instruction or "")[:500],
            doc=json.dumps(doc, ensure_ascii=False)[:6000],
        ),
        model=GURU_MODEL,
        source="guru_artifact",
    )
    if not valid_artifact_doc(revised, artifact_type):
        raise ValueError("revised artifact failed shape validation")
    return revised
