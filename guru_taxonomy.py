"""
Guru taxonomy (E1) — offline syllabus ground truth from the vendored
Marble Skill Taxonomy (github.com/withmarbleapp/os-taxonomy, v1):
1,590 micro-topics + 3,221 prerequisite edges under data/taxonomy/
(ODbL 1.0 database + CC BY-SA 4.0 content — see data/taxonomy/NOTICE).

`build_syllabus_atoms(topic)` matches the topic against taxonomy names
(exact → token-subset → fuzzy), walks the prerequisite closure, and
returns a guru_engine-shaped atom list — keyless, deterministic, no LLM.
Rungs are derived honestly from the data (description → plain language,
evidence → precise/formal criteria); `assessmentPrompt` seeds the check
question ({{name}} placeholder rewritten to second person; Krishna
rephrases naturally in chat). Returns None on any miss so the caller
falls through to the LLM/template path. Never raises.
"""
from __future__ import annotations

import difflib
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

TAXONOMY_DIR = Path(os.environ.get(
    "NARAD_TAXONOMY_DIR",
    str(Path(__file__).resolve().parent / "data" / "taxonomy"),
))

_FUZZY_CUTOFF = 0.85  # conservative: a wrong syllabus is worse than no match


# ── Dataset ───────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _dataset() -> tuple[dict[str, dict], dict[str, list[dict]]] | None:
    """(topics_by_id, incoming_prereq_edges_by_topic_id) or None if not vendored."""
    try:
        topics_raw = json.loads((TAXONOMY_DIR / "topics.json").read_text(encoding="utf-8"))
        deps_raw = json.loads((TAXONOMY_DIR / "dependencies.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    topics = {
        str(t["id"]): t
        for t in (topics_raw.get("topics") or [])
        if isinstance(t, dict) and t.get("id") and str(t.get("name", "")).strip()
    }
    if not topics:
        return None
    prereq_edges: dict[str, list[dict]] = {}
    for edge in deps_raw.get("dependencies") or []:
        if not isinstance(edge, dict):
            continue
        topic_id = str(edge.get("topicId", ""))
        prereq_id = str(edge.get("prerequisiteId", ""))
        if topic_id in topics and prereq_id in topics:
            prereq_edges.setdefault(topic_id, []).append(edge)
    return topics, prereq_edges


def taxonomy_available() -> bool:
    return _dataset() is not None


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split())


@lru_cache(maxsize=1)
def _names_index() -> dict[str, str]:
    """normalized name → topic id (first wins on collision)."""
    data = _dataset()
    if not data:
        return {}
    index: dict[str, str] = {}
    for topic_id, topic in data[0].items():
        key = _norm(str(topic.get("name", "")))
        if key and key not in index:
            index[key] = topic_id
    return index


def find_topic(topic: str) -> dict | None:
    """Match a free-text topic to a taxonomy micro-topic, or None."""
    data = _dataset()
    if not data:
        return None
    topics, _ = data
    query = _norm(topic)
    if not query:
        return None
    index = _names_index()
    if query in index:
        return topics[index[query]]
    query_tokens = set(query.split())
    subset = [name for name in index if query_tokens <= set(name.split())]
    if subset:
        return topics[index[min(subset, key=len)]]
    close = difflib.get_close_matches(query, list(index), n=1, cutoff=_FUZZY_CUTOFF)
    return topics[index[close[0]]] if close else None


# ── Syllabus construction ─────────────────────────────────────────────────────

def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug[:48] or "atom"


_PRONOUN_SWAPS = (
    (r"\bthey\b", "you"), (r"\bThey\b", "You"),
    (r"\bthem\b", "you"), (r"\btheir\b", "your"), (r"\bTheir\b", "Your"),
    (r"\bthemselves\b", "yourself"),
)
_IRREGULAR_VERBS = ((" you is ", " you are "), (" you was ", " you were "),
                    (" you has ", " you have "), (" you does ", " you do "))
_NO_STRIP = {"always", "sometimes", "perhaps", "yes", "this", "its", "as", "is", "was", "has", "does"}


def _second_person(prompt: str) -> str:
    """assessmentPrompt is parent-directed about the child; rewrite both the
    {{name}} placeholder and the child-referring third-person pronouns to
    second person, then patch the resulting verb agreement."""
    text = (prompt or "").replace("{{name}}'s", "your").replace("{{name}}", "you")
    text = re.sub(r"\{\{.*?\}\}", "you", text)
    for pattern, repl in _PRONOUN_SWAPS:
        text = re.sub(pattern, repl, text)
    for old, new in _IRREGULAR_VERBS:
        text = text.replace(old, new)

    def _de_conjugate(match: re.Match) -> str:
        word = match.group(1)
        if word.lower() in _NO_STRIP or word.endswith(("ss", "us", "is")):
            return match.group(0)
        return f"you {word[:-1]}"

    return re.sub(r"\byou ([a-z]{3,}s)\b", _de_conjugate, text).strip()


def _closure_ids(seed_id: str, prereq_edges: dict[str, list[dict]], max_atoms: int) -> list[str]:
    """Seed + prerequisite ancestors, breadth-first (hard edges first), capped."""
    ordered = [seed_id]
    seen = {seed_id}
    frontier = [seed_id]
    while frontier and len(ordered) < max_atoms:
        next_frontier: list[str] = []
        for topic_id in frontier:
            edges = sorted(
                prereq_edges.get(topic_id, []),
                key=lambda e: (0 if e.get("strength") == "hard" else 1, str(e.get("prerequisiteId"))),
            )
            for edge in edges:
                prereq_id = str(edge.get("prerequisiteId"))
                if prereq_id not in seen:
                    seen.add(prereq_id)
                    ordered.append(prereq_id)
                    next_frontier.append(prereq_id)
                if len(ordered) >= max_atoms:
                    break
            if len(ordered) >= max_atoms:
                break
        frontier = next_frontier
    return ordered[:max_atoms]


def _topo_order(kept: list[str], topics: dict[str, dict], prereq_edges: dict[str, list[dict]]) -> list[str]:
    """Prerequisites before dependents; deterministic (age, name) tie-break."""
    kept_set = set(kept)
    prereqs_of = {
        tid: sorted({
            str(e.get("prerequisiteId")) for e in prereq_edges.get(tid, [])
            if str(e.get("prerequisiteId")) in kept_set
        })
        for tid in kept
    }
    def sort_key(tid: str) -> tuple:
        topic = topics[tid]
        return (int(topic.get("ageRangeStart") or 0), str(topic.get("name", "")))
    remaining = set(kept)
    ordered: list[str] = []
    while remaining:
        ready = sorted(
            (tid for tid in remaining if not (set(prereqs_of[tid]) & remaining)),
            key=sort_key,
        )
        if not ready:  # cycle guard (upstream is a DAG; belt and braces)
            ordered.extend(sorted(remaining, key=sort_key))
            break
        for tid in ready:
            ordered.append(tid)
            remaining.discard(tid)
    return ordered


def _atom(topic: dict, atom_id: str, prereq_atom_ids: list[str], incoming_reason: str) -> dict[str, Any]:
    name = str(topic.get("name", "")).strip()
    desc = str(topic.get("description", "")).strip()
    evidence = [str(e).strip() for e in (topic.get("evidence") or []) if str(e).strip()]
    subject = str(topic.get("subject", "")).strip()
    domain = str(topic.get("domain", "")).strip()
    ages = f"ages {topic.get('ageRangeStart', '?')}–{topic.get('ageRangeEnd', '?')}"
    check_q = _second_person(str(topic.get("assessmentPrompt", ""))) or f"In your own words, what is {name} for?"
    return {
        "id": atom_id,
        "name": name,
        "prerequisites": prereq_atom_ids,
        "eli5": desc or f"{name}, in everyday terms.",
        "plain": (f"{name} — mastery looks like: {evidence[0]}" if evidence else desc or name),
        "precise": ("You can: " + "; ".join(evidence[:4])) if evidence else (desc or name),
        "formal": f"Curriculum-grade criteria ({subject} · {domain}, {ages}): "
                  + ("; ".join(evidence) if evidence else desc or name),
        "misconception": (
            f"Skipping the groundwork — {incoming_reason}" if incoming_reason
            else f"Assuming {name} is one indivisible idea — it builds on the prerequisites above."
        ),
        "check": {
            "q": check_q,
            "good_answer": evidence[0] if evidence else f"Any answer that captures: {(desc or name)[:160]}",
        },
    }


def build_syllabus_atoms(topic: str, *, max_atoms: int = 12) -> dict[str, Any] | None:
    """Taxonomy-derived syllabus payload {"atoms": [...], ...} or None on miss."""
    try:
        data = _dataset()
        if not data:
            return None
        topics, prereq_edges = data
        seed = find_topic(topic)
        if not seed:
            return None
        seed_id = str(seed["id"])
        kept = _closure_ids(seed_id, prereq_edges, max(1, max_atoms))
        ordered = _topo_order(kept, topics, prereq_edges)
        kept_set = set(ordered)

        atom_ids: dict[str, str] = {}
        used: set[str] = set()
        for tid in ordered:
            base = _slug(str(topics[tid].get("name", tid)))
            candidate, n = base, 2
            while candidate in used:
                candidate, n = f"{base}-{n}", n + 1
            used.add(candidate)
            atom_ids[tid] = candidate

        atoms: list[dict] = []
        for tid in ordered:
            edges = prereq_edges.get(tid, [])
            prereq_atom_ids = sorted({
                atom_ids[str(e.get("prerequisiteId"))]
                for e in edges if str(e.get("prerequisiteId")) in kept_set
            })
            reason = next(
                (str(e.get("reason", "")).strip() for e in edges
                 if str(e.get("prerequisiteId")) in kept_set and str(e.get("reason", "")).strip()),
                "",
            )
            atoms.append(_atom(topics[tid], atom_ids[tid], prereq_atom_ids, reason))
        if not atoms:
            return None
        return {
            "atoms": atoms,
            "matched_topic": str(seed.get("name", "")),
            "source": "Marble Skill Taxonomy v1 (os-taxonomy, ODbL 1.0 / CC BY-SA 4.0)",
        }
    except Exception:
        return None
