"""Guided-mode engine — generic step-by-step teaching sessions (G7).

Powers `/teach` in chat today; `/interview` (or anything mastery-loop-shaped)
later is one more entry in MODES, not new machinery.

The loop is a deterministic server-side state machine, not freeform chat:

    start → [present atom → quiz → grade → advance] × N → complete

Each *step* the client receives is one atom's presentation:

    {atom, narration, artifact_html, quiz, progress}

- narration    — what Krishna speaks aloud (TTS) and shows as text
- artifact_html — self-contained animated SVG/CSS card, rendered in a
                  sandboxed iframe (scripts are stripped server-side AND
                  blocked by the iframe sandbox — CSS animations only)
- quiz         — {"type": "mcq", question, options, correct answered
                  server-side} or {"type": "free", question} (the atom's own
                  check question, graded by guru_engine's grader)

Everything reuses guru_engine: syllabus generation (taxonomy → LLM →
template), mastery state (SM-2-lite), frontier selection, free-answer
grading. Sessions persist to the learning workspace
(LEARNING_DIR/<user>/<workspace>/guided_session.json) so "/teach me
pointers" next week resumes where it left off, and the Gurukul tab sees
the same mastery state. Degrades keyless like the rest of the engine:
template narration from the atom's rungs, template artifact, heuristic
grading.
"""

from __future__ import annotations

import json
import re
import secrets
from html import escape
from pathlib import Path
from typing import Any

from guru_engine import (
    GURU_MODEL,
    _now_iso,
    _workspace_dir,
    frontier_atom,
    generate_syllabus,
    grade_check_answer,
    llm_json,
    load_learner_state,
    load_syllabus,
    mastery_summary,
    record_check_result,
)
from learning_workspace import ensure_workspace, workspace_id_for_topic

# ── Mode registry ──────────────────────────────────────────────────────────────
# A mode is data, not code. Adding /interview later = one more dict here.

MODES: dict[str, dict[str, Any]] = {
    "teach": {
        "avatar": "Krishna",
        "label": "Guru mode",
        "persona": (
            "You are Krishna in guru mode — a patient master teacher. Warm, "
            "vivid, never condescending. You teach ONE atomic concept at a "
            "time with a concrete analogy first."
        ),
        "narration_goal": (
            "Teach this one concept so a motivated beginner truly gets it: "
            "open with the analogy, then the plain-English idea, then name "
            "the classic misconception and correct it."
        ),
        "quiz_policy": "mixed",  # presenter picks mcq (recall) or free (understanding)
        "completion": "That's the full syllabus — everything mastered. 🎉",
    },
}

_SESSION_FILE = "guided_session.json"


def _session_path(user_id: str, workspace_id: str) -> Path:
    return _workspace_dir(user_id, workspace_id) / _SESSION_FILE


# ── HTML sanitising (defense in depth; iframe sandbox is the real wall) ───────

_SCRIPT_RE = re.compile(r"<\s*/?\s*(script|iframe|object|embed|link|meta|base|form)\b[^>]*>", re.IGNORECASE)
_EVENT_ATTR_RE = re.compile(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_EXTERNAL_URL_RE = re.compile(r"(src|href|xlink:href)\s*=\s*([\"'])\s*(https?:|//|javascript:)[^\"']*\2", re.IGNORECASE)


def sanitize_artifact_html(html: str) -> str:
    """Strip scripts, event handlers and external fetches from artifact HTML."""
    html = _SCRIPT_RE.sub("", html or "")
    html = _EVENT_ATTR_RE.sub("", html)
    html = _EXTERNAL_URL_RE.sub(r'\1=\2\2', html)
    return html.strip()


def _fallback_artifact_html(atom: dict[str, Any]) -> str:
    """Keyless artifact: the atom's analogy on an animated card."""
    name = escape(str(atom.get("name", "Concept")))
    eli5 = escape(str(atom.get("eli5", "")))
    plain = escape(str(atom.get("plain", "")))
    return f"""<div style="font-family: Georgia, serif; padding: 20px 24px; color: #2b2115;">
<style>
@keyframes guru-rise {{ from {{ opacity: 0; transform: translateY(14px); }} to {{ opacity: 1; transform: none; }} }}
@keyframes guru-underline {{ from {{ width: 0; }} to {{ width: 64px; }} }}
.guru-card h2 {{ margin: 0 0 4px; font-size: 1.25rem; animation: guru-rise 0.7s ease-out both; }}
.guru-card .rule {{ height: 3px; background: #d97b29; border-radius: 2px; animation: guru-underline 1s 0.3s ease-out both; }}
.guru-card p {{ line-height: 1.55; animation: guru-rise 0.7s ease-out both; }}
.guru-card p.eli5 {{ animation-delay: 0.45s; font-style: italic; }}
.guru-card p.plain {{ animation-delay: 0.9s; }}
</style>
<div class="guru-card">
<h2>{name}</h2>
<div class="rule"></div>
<p class="eli5">{eli5}</p>
<p class="plain">{plain}</p>
</div>
</div>"""


# ── Presenter (one LLM call per atom, cached in the session) ───────────────────

_PRESENT_PROMPT = """{persona}

CONCEPT ATOM (from a prerequisite-ordered syllabus on "{topic}"):
- name: {name}
- analogy (eli5): {eli5}
- plain: {plain}
- precise: {precise}
- misconception: {misconception}
- check question: {check_q}
- what a correct answer must contain: {good_answer}

{narration_goal}

Produce ONLY a JSON object with exactly these keys:

1. "narration": 4-7 sentences of spoken teaching (it will be read aloud).
   Conversational, no markdown, no headings, no lists. Build on the analogy.

2. "artifact_html": a self-contained animated visual explaining THIS concept.
   Hard rules: inline <style> + HTML/SVG only. Animate with CSS keyframes
   (no <script>, no event handlers, no external URLs, no fonts/images).
   Aim for one clear visual metaphor with 2-4 animated elements, not a wall
   of text. Max ~120 lines. Use warm parchment-friendly colours
   (#d97b29 accents, #2b2115 text) on a transparent background.

3. "quiz": pick the format that best tests THIS atom:
   - Recall/terminology atom → {{"type": "mcq", "question": "...",
     "options": ["...", "...", "...", "..."], "correct_index": 0-3,
     "why": "one sentence on why the right option is right"}}
     Make distractors plausible — one should embody the misconception.
   - Understanding/why atom → {{"type": "free"}} (the learner answers the
     check question above in their own words; do not restate it).

Respond with ONLY the JSON object."""


def _fallback_narration(atom: dict[str, Any]) -> str:
    parts = [
        str(atom.get("eli5", "")).strip(),
        str(atom.get("plain", "")).strip(),
    ]
    mis = str(atom.get("misconception", "")).strip()
    if mis:
        parts.append(f"One thing people often get wrong: {mis}")
    return " ".join(p for p in parts if p)


def _validate_mcq(quiz: Any) -> dict[str, Any] | None:
    if not isinstance(quiz, dict) or quiz.get("type") != "mcq":
        return None
    question = str(quiz.get("question", "")).strip()
    options = quiz.get("options")
    idx = quiz.get("correct_index")
    if not question or not isinstance(options, list) or not isinstance(idx, int):
        return None
    options = [str(o).strip() for o in options if str(o).strip()]
    if len(options) < 3 or not (0 <= idx < len(options)):
        return None
    return {
        "type": "mcq",
        "question": question,
        "options": options,
        "correct_index": idx,
        "why": str(quiz.get("why", "")).strip(),
    }


def _present_atom(mode_cfg: dict[str, Any], topic: str, atom: dict[str, Any]) -> dict[str, Any]:
    """Build one atom's presentation: narration + artifact + quiz. Never raises."""
    check = atom.get("check") or {}
    narration = ""
    artifact_html = ""
    quiz: dict[str, Any] | None = None
    presenter = "template"

    try:
        data = llm_json(
            _PRESENT_PROMPT.format(
                persona=mode_cfg["persona"],
                topic=topic,
                name=atom.get("name", ""),
                eli5=str(atom.get("eli5", ""))[:500],
                plain=str(atom.get("plain", ""))[:500],
                precise=str(atom.get("precise", ""))[:500],
                misconception=str(atom.get("misconception", ""))[:400],
                check_q=str(check.get("q", ""))[:300],
                good_answer=str(check.get("good_answer", ""))[:300],
                narration_goal=mode_cfg["narration_goal"],
            ),
            model=GURU_MODEL,
            max_tokens=3500,
            temperature=0.5,
            source="guided_presenter",
        )
        narration = str(data.get("narration", "")).strip()
        artifact_html = sanitize_artifact_html(str(data.get("artifact_html", "")))
        quiz = _validate_mcq(data.get("quiz"))
        if quiz is None and isinstance(data.get("quiz"), dict) and data["quiz"].get("type") == "free":
            quiz = {"type": "free"}
        if narration:
            presenter = "llm"
    except Exception:
        pass

    if not narration:
        narration = _fallback_narration(atom)
    if not artifact_html:
        artifact_html = _fallback_artifact_html(atom)
    if quiz is None:
        quiz = {"type": "free"}
    if quiz["type"] == "free":
        # Free answers are always graded against the atom's own check question.
        quiz["question"] = str(check.get("q", f"In your own words, explain {atom.get('name', 'this concept')}."))

    return {
        "atom_id": str(atom.get("id", "")),
        "name": str(atom.get("name", "")),
        "narration": narration,
        "artifact_html": artifact_html,
        "quiz": quiz,
        "presenter": presenter,
    }


# ── Session store ──────────────────────────────────────────────────────────────

def _load_session(user_id: str, workspace_id: str) -> dict[str, Any] | None:
    path = _session_path(user_id, workspace_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_session(session: dict[str, Any]) -> None:
    session["updated_at"] = _now_iso()
    path = _session_path(session["user_id"], session["workspace_id"])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _client_quiz(quiz: dict[str, Any]) -> dict[str, Any]:
    """The quiz as sent to the browser — never leaks the MCQ answer."""
    safe = {k: v for k, v in quiz.items() if k not in ("correct_index", "why")}
    return safe


def _progress(session: dict[str, Any]) -> dict[str, Any]:
    syllabus = load_syllabus(user_id=session["user_id"], workspace_id=session["workspace_id"])
    state = load_learner_state(user_id=session["user_id"], workspace_id=session["workspace_id"])
    counts = mastery_summary(syllabus, state)
    return {**counts, "topic": session.get("topic", ""), "mode": session.get("mode", "teach")}


def _step_payload(session: dict[str, Any], presentation: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "atom",
        "atom_id": presentation["atom_id"],
        "name": presentation["name"],
        "narration": presentation["narration"],
        "artifact_html": presentation["artifact_html"],
        "quiz": _client_quiz(presentation["quiz"]),
        "progress": _progress(session),
        "avatar": MODES.get(session.get("mode", "teach"), MODES["teach"])["avatar"],
    }


def _completion_payload(session: dict[str, Any]) -> dict[str, Any]:
    mode_cfg = MODES.get(session.get("mode", "teach"), MODES["teach"])
    return {
        "kind": "complete",
        "message": mode_cfg["completion"],
        "progress": _progress(session),
        "avatar": mode_cfg["avatar"],
    }


def _current_step(session: dict[str, Any]) -> dict[str, Any]:
    """Present the session's frontier atom (cached), or completion."""
    user_id, workspace_id = session["user_id"], session["workspace_id"]
    syllabus = load_syllabus(user_id=user_id, workspace_id=workspace_id)
    state = load_learner_state(user_id=user_id, workspace_id=workspace_id)
    skipped = set(session.get("skipped", []))
    atom = frontier_atom(syllabus, state)
    if atom is not None and str(atom.get("id", "")) in skipped:
        # Walk past explicitly skipped atoms without marking them mastered.
        atoms = (syllabus or {}).get("atoms") or []
        atom = next(
            (
                a for a in atoms
                if str(a.get("id", "")) not in skipped
                and str((state.get(str(a.get("id", ""))) or {}).get("status", "untaught")) != "mastered"
            ),
            None,
        )
    if atom is None:
        session["status"] = "completed"
        _save_session(session)
        return _completion_payload(session)

    atom_id = str(atom.get("id", ""))
    session["current_atom_id"] = atom_id
    cache = session.setdefault("presented", {})
    if atom_id not in cache:
        mode_cfg = MODES.get(session.get("mode", "teach"), MODES["teach"])
        cache[atom_id] = _present_atom(mode_cfg, session.get("topic", ""), atom)
    _save_session(session)
    return _step_payload(session, cache[atom_id])


# ── Public API ─────────────────────────────────────────────────────────────────

def start_session(*, user_id: str, mode: str, topic: str) -> dict[str, Any]:
    """Start or resume a guided session. Returns {session_meta, step}."""
    if mode not in MODES:
        raise ValueError(f"unknown guided mode '{mode}' — available: {', '.join(sorted(MODES))}")
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("topic is required — try '/teach me virtual memory'")

    workspace = ensure_workspace(user_id=user_id, topic=topic)
    workspace_id = workspace["workspace_id"]
    generate_syllabus(user_id=user_id, workspace_id=workspace_id, topic=topic)

    session = _load_session(user_id, workspace_id)
    resumed = bool(session) and session.get("mode") == mode
    if not resumed:
        session = {
            "session_id": f"guided_{secrets.token_hex(6)}",
            "mode": mode,
            "user_id": user_id,
            "workspace_id": workspace_id,
            "topic": topic,
            "status": "active",
            "created_at": _now_iso(),
            "presented": {},
            "skipped": [],
            "history": [],
        }
    else:
        session["status"] = "active"
        session["skipped"] = []  # a fresh sit-down retries previously skipped atoms

    step = _current_step(session)
    return {"session": session_meta(session), "step": step, "resumed": resumed}


def session_meta(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": session.get("session_id"),
        "mode": session.get("mode"),
        "workspace_id": session.get("workspace_id"),
        "topic": session.get("topic"),
        "status": session.get("status"),
        "current_atom_id": session.get("current_atom_id"),
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
    }


def get_session(*, user_id: str, workspace_id: str) -> dict[str, Any] | None:
    session = _load_session(user_id, workspace_id)
    if session is None:
        return None
    return {"session": session_meta(session), "progress": _progress(session)}


def find_session_for_topic(*, user_id: str, topic: str) -> dict[str, Any] | None:
    return get_session(user_id=user_id, workspace_id=workspace_id_for_topic(topic))


def submit_answer(
    *,
    user_id: str,
    workspace_id: str,
    answer: str = "",
    choice_index: int | None = None,
) -> dict[str, Any]:
    """Grade the current atom's quiz. Advances to the next step when correct."""
    session = _load_session(user_id, workspace_id)
    if session is None or session.get("status") not in ("active",):
        raise ValueError("no active guided session here — start one with /teach me <topic>")
    atom_id = str(session.get("current_atom_id", ""))
    presentation = (session.get("presented") or {}).get(atom_id)
    if not atom_id or not presentation:
        raise ValueError("session has no current atom — call start again")

    quiz = presentation.get("quiz") or {"type": "free"}
    if quiz.get("type") == "mcq":
        if choice_index is None:
            raise ValueError("this quiz is multiple choice — send choice_index")
        correct = int(choice_index) == int(quiz.get("correct_index", -1))
        options = quiz.get("options") or []
        right = options[quiz["correct_index"]] if 0 <= int(quiz.get("correct_index", -1)) < len(options) else ""
        grade = {
            "correct": correct,
            "feedback": (quiz.get("why") or f"Right — {right}.") if correct
            else f"Not quite — the answer is: {right}. {quiz.get('why', '')}".strip(),
            "remediation": "",  # the reveal above is the remediation for MCQ
            "grader": "local",
        }
        grade["state"] = record_check_result(
            user_id=user_id, workspace_id=workspace_id, atom_id=atom_id, correct=correct,
        )
    else:
        grade = grade_check_answer(
            user_id=user_id, workspace_id=workspace_id, atom_id=atom_id, answer=answer,
        )

    session.setdefault("history", []).append(
        {"atom_id": atom_id, "correct": bool(grade.get("correct")), "at": _now_iso()}
    )

    result: dict[str, Any] = {
        "grade": {k: grade.get(k) for k in ("correct", "feedback", "remediation", "grader")},
        "atom_id": atom_id,
        "advanced": False,
    }
    if grade.get("correct"):
        result["advanced"] = True
        result["step"] = _current_step(session)  # recomputes frontier → next atom
    else:
        _save_session(session)
        result["progress"] = _progress(session)
    return result


def skip_atom(*, user_id: str, workspace_id: str) -> dict[str, Any]:
    """Move past the current atom without mastering it (revisited next session)."""
    session = _load_session(user_id, workspace_id)
    if session is None or session.get("status") != "active":
        raise ValueError("no active guided session here")
    atom_id = str(session.get("current_atom_id", ""))
    if atom_id:
        skipped = session.setdefault("skipped", [])
        if atom_id not in skipped:
            skipped.append(atom_id)
    return {"skipped": atom_id, "step": _current_step(session)}


def exit_session(*, user_id: str, workspace_id: str) -> dict[str, Any]:
    """End the sit-down; mastery persists, so /teach later resumes."""
    session = _load_session(user_id, workspace_id)
    if session is None:
        raise ValueError("no guided session here")
    if session.get("status") == "active":
        session["status"] = "exited"
        _save_session(session)
    progress = _progress(session)
    return {
        "session": session_meta(session),
        "progress": progress,
        "message": (
            f"Paused — {progress['mastered']}/{progress['total']} atoms mastered. "
            f"Say /teach me {session.get('topic', 'this topic')} anytime to continue."
        ),
    }
