"""
Persistent learning workspaces for Krishna + Matsya.

These workspaces keep ongoing teaching state outside repo docs and outside chat
history, while Smriti only receives distilled outcomes.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from narad_config import LEARNING_DIR

# Order matters for extract_learning_topic: longer prefixes first so the
# stripped topic doesn't keep filler words ("on", "me").
_TEACH_PATTERNS = (
    r"^teach me\b",
    r"^can you teach me\b",
    r"^can you teach\b",
    r"^i want to learn\b",
    r"^help me learn\b",
    r"^walk me through\b",
    r"^i'?m studying\b",
    r"^explain\b",
    r"^help me understand\b",
    r"^i don't understand\b",
    r"^quiz me on\b",
    r"^quiz me\b",
    r"^help me study\b",
    r"^what is\b",
    r"^how does\b",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(value: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return clean or "topic"


def is_learning_query(query: str) -> bool:
    q = (query or "").strip().lower()
    return any(re.search(pattern, q) for pattern in _TEACH_PATTERNS)


# Filler that survives prefix-stripping and makes ugly workspace titles:
# "teach me HOW virtual memory WORKS", "explain dot products TO ME".
_TOPIC_LEAD_RE = re.compile(
    r"^(?:about|on|the basics of|basics of|the fundamentals of|fundamentals of|"
    r"how does|how do|how|why does|why do|why|what|a|an|the)\s+",
    re.IGNORECASE,
)
_TOPIC_TRAIL_RE = re.compile(
    r"\s+(?:to me|for me|please|in detail|in simple terms|in simple words|"
    r"step by step|from scratch|from first principles|works?)[\s.,!?]*$",
    re.IGNORECASE,
)


def extract_learning_topic(query: str) -> str:
    """Best-effort clean topic from a teach query.

    "teach me how virtual memory works" -> "virtual memory"
    "explain dot products to me"        -> "dot products"
    "what is a dot product?"            -> "dot product"
    """
    text = (query or "").strip()
    lowered = text.lower()
    remainder = text
    for pattern in _TEACH_PATTERNS:
        match = re.search(pattern, lowered)
        if match:
            remainder = text[match.end():].strip(" .,:;?!-") or text
            break
    cleaned = remainder
    for _ in range(3):  # fillers stack: "about the basics of X"
        stripped = _TOPIC_LEAD_RE.sub("", cleaned).strip()
        if stripped == cleaned:
            break
        cleaned = stripped
    for _ in range(2):  # trailers stack: "X to me please"
        stripped = _TOPIC_TRAIL_RE.sub("", cleaned).strip(" .,:;?!-")
        if stripped == cleaned:
            break
        cleaned = stripped
    return cleaned or remainder or text


def topic_key(topic: str) -> str:
    return _slugify(topic)


def workspace_id_for_topic(topic: str) -> str:
    key = topic_key(topic)
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    return f"learn_{digest}"


def _user_dir(user_id: str) -> Path:
    path = LEARNING_DIR / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _workspace_dir(user_id: str, workspace_id: str) -> Path:
    return _user_dir(user_id) / workspace_id


def _workspace_meta_path(user_id: str, workspace_id: str) -> Path:
    return _workspace_dir(user_id, workspace_id) / "workspace.json"


def _mission_path(user_id: str, workspace_id: str) -> Path:
    return _workspace_dir(user_id, workspace_id) / "MISSION.md"


def _glossary_path(user_id: str, workspace_id: str) -> Path:
    return _workspace_dir(user_id, workspace_id) / "GLOSSARY.md"


def _resources_path(user_id: str, workspace_id: str) -> Path:
    return _workspace_dir(user_id, workspace_id) / "RESOURCES.md"


def _records_dir(user_id: str, workspace_id: str) -> Path:
    path = _workspace_dir(user_id, workspace_id) / "learning-records"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _artifacts_dir(user_id: str, workspace_id: str) -> Path:
    path = _workspace_dir(user_id, workspace_id) / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _artifact_path(user_id: str, workspace_id: str, artifact_id: str) -> Path:
    return _artifacts_dir(user_id, workspace_id) / f"{artifact_id}.json"


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _parse_glossary_lines(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"- \*\*(.+?)\*\*: (.+)", line.strip())
        if match:
            result[match.group(1).strip()] = match.group(2).strip()
    return result


def _render_glossary(topic: str, entries: dict[str, str]) -> str:
    lines = [
        f"# Glossary — {topic}",
        "",
        "Terms Krishna and Matsya normalize while teaching this topic.",
        "",
    ]
    if not entries:
        lines.append("- **Pending**: Terms will appear here as the teaching workspace evolves.")
    else:
        for term, definition in sorted(entries.items()):
            lines.append(f"- **{term}**: {definition}")
    lines.append("")
    return "\n".join(lines)


def _parse_resource_lines(text: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for line in text.splitlines():
        match = re.match(r"- \[(.+?)\]\((.+?)\)(?: — (.+))?", line.strip())
        if match:
            items.append({
                "title": match.group(1).strip(),
                "url": match.group(2).strip(),
                "note": (match.group(3) or "").strip(),
            })
    return items


def _render_resources(topic: str, items: list[dict[str, str]]) -> str:
    lines = [
        f"# Resources — {topic}",
        "",
        "Grounding collected by Matsya for this learning thread.",
        "",
    ]
    if not items:
        lines.append("- Pending — sources will appear here when Matsya grounds the topic.")
    else:
        for item in items:
            note = f" — {item['note']}" if item.get("note") else ""
            lines.append(f"- [{item['title']}]({item['url']}){note}")
    lines.append("")
    return "\n".join(lines)


def _record_filename(index: int, slug: str) -> str:
    return f"{index:04d}-{slug}.md"


def _load_workspace_meta(user_id: str, workspace_id: str) -> dict[str, Any] | None:
    path = _workspace_meta_path(user_id, workspace_id)
    if not path.exists():
        return None
    return _read_json(path, {})


def _load_record(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = path.stem
    summary = ""
    created_at = ""
    tags: list[str] = []
    session_id = None
    record_type = "lesson"
    for line in lines[:12]:
        if line.startswith("# "):
            title = line[2:].strip()
        elif line.startswith("- created_at:"):
            created_at = line.split(":", 1)[1].strip()
        elif line.startswith("- type:"):
            record_type = line.split(":", 1)[1].strip() or "lesson"
        elif line.startswith("- session_id:"):
            session_id = line.split(":", 1)[1].strip() or None
        elif line.startswith("- tags:"):
            raw = line.split(":", 1)[1].strip()
            tags = [item.strip() for item in raw.split(",") if item.strip()]
        elif line.startswith("## Summary"):
            summary_index = lines.index(line) + 1
            summary = lines[summary_index].strip() if summary_index < len(lines) else ""
            break
    return {
        "record_id": path.stem.split("-", 1)[0],
        "title": title,
        "summary": summary,
        "body": text,
        "created_at": created_at,
        "type": record_type,
        "session_id": session_id,
        "tags": tags,
        "path": str(path),
    }


def ensure_workspace(
    *,
    user_id: str,
    topic: str,
    mission: str = "",
    session_id: str | None = None,
) -> dict[str, Any]:
    workspace_id = workspace_id_for_topic(topic)
    root = _workspace_dir(user_id, workspace_id)
    root.mkdir(parents=True, exist_ok=True)
    _records_dir(user_id, workspace_id)

    meta = _load_workspace_meta(user_id, workspace_id) or {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "topic": topic.strip(),
        "topic_key": topic_key(topic),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "status": "active",
        "record_count": 0,
        "resource_count": 0,
        "glossary_term_count": 0,
        "artifact_count": 0,
        "latest_record_id": None,
        "latest_artifact_id": None,
        "last_session_id": session_id,
    }
    meta["topic"] = topic.strip() or meta["topic"]
    meta["updated_at"] = _now_iso()
    if session_id:
        meta["last_session_id"] = session_id
    _write_json(_workspace_meta_path(user_id, workspace_id), meta)

    mission_text = "\n".join([
        f"# Mission — {meta['topic']}",
        "",
        f"Workspace ID: `{workspace_id}`",
        f"Last session: `{session_id or meta.get('last_session_id') or 'n/a'}`",
        "",
        "## Learner Goal",
        mission.strip() or f"Understand {meta['topic']} clearly enough to explain and apply it.",
        "",
    ])
    _mission_path(user_id, workspace_id).write_text(mission_text, encoding="utf-8")
    _write_if_missing(_glossary_path(user_id, workspace_id), _render_glossary(meta["topic"], {}))
    _write_if_missing(_resources_path(user_id, workspace_id), _render_resources(meta["topic"], []))
    return load_workspace(user_id=user_id, workspace_id=workspace_id) or meta


def load_workspace(*, user_id: str, workspace_id: str) -> dict[str, Any] | None:
    meta = _load_workspace_meta(user_id, workspace_id)
    if not meta:
        return None
    mission = _mission_path(user_id, workspace_id).read_text(encoding="utf-8") if _mission_path(user_id, workspace_id).exists() else ""
    glossary = _glossary_path(user_id, workspace_id).read_text(encoding="utf-8") if _glossary_path(user_id, workspace_id).exists() else ""
    resources = _resources_path(user_id, workspace_id).read_text(encoding="utf-8") if _resources_path(user_id, workspace_id).exists() else ""
    records = list_records(user_id=user_id, workspace_id=workspace_id, limit=10)
    syllabus: dict[str, Any] | None = None
    learner_state: dict[str, Any] = {}
    try:
        from guru_engine import load_learner_state, load_syllabus
        syllabus = load_syllabus(user_id=user_id, workspace_id=workspace_id)
        learner_state = load_learner_state(user_id=user_id, workspace_id=workspace_id)
    except Exception:
        pass
    return {
        **meta,
        "mission": mission,
        "glossary": glossary,
        "resources": resources,
        "records": records,
        "artifacts": list_artifacts(user_id=user_id, workspace_id=workspace_id, limit=5),
        "syllabus": syllabus,
        "learner_state": learner_state,
    }


def list_workspaces(user_id: str) -> list[dict[str, Any]]:
    user_dir = _user_dir(user_id)
    items: list[dict[str, Any]] = []
    for child in user_dir.iterdir():
        if not child.is_dir():
            continue
        meta = _load_workspace_meta(user_id, child.name)
        if meta:
            items.append(meta)
    items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return items


def list_records(*, user_id: str, workspace_id: str, limit: int = 50) -> list[dict[str, Any]]:
    records_root = _records_dir(user_id, workspace_id)
    items = sorted(records_root.glob("*.md"), reverse=True)
    return [_load_record(path) for path in items[: max(limit, 1)]]


def _normalize_artifact_type(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"diagram", "concept_map", "concept map"}:
        return "concept_map"
    return "flashcards"


def _flashcard_doc(topic: str, focus: str = "") -> dict[str, Any]:
    prompt_focus = focus.strip() or topic
    cards = [
        {
            "id": f"card-{index + 1}",
            "front": question,
            "back": answer,
            "tags": [topic_key(topic)],
        }
        for index, (question, answer) in enumerate([
            (f"What is {prompt_focus}?", f"Explain {prompt_focus} in one precise, plain-English sentence."),
            (f"Why does {prompt_focus} matter?", f"State the main practical reason {prompt_focus} is important."),
            (f"What are the main moving parts in {prompt_focus}?", "Name the core components, steps, or ideas to track."),
            (f"Where would you actually use {prompt_focus}?", "Give one concrete real-world or interview-style use case."),
            (f"What is a common pitfall with {prompt_focus}?", "Name one misconception or failure mode to watch for."),
            (f"How would you explain {prompt_focus} quickly?", "Give a compact answer that would work in an interview or teaching context."),
        ])
    ]
    return {"cards": cards}


def _concept_map_doc(topic: str) -> dict[str, Any]:
    root_id = topic_key(topic) or "topic"
    nodes = [
        {"id": root_id, "label": topic, "note": f"The core topic being studied: {topic}."},
        {"id": "intuition", "label": "intuition", "note": f"What {topic} is trying to accomplish."},
        {"id": "mechanics", "label": "mechanics", "note": f"How {topic} works step by step."},
        {"id": "examples", "label": "examples", "note": f"Concrete cases where {topic} shows up."},
        {"id": "pitfalls", "label": "pitfalls", "note": f"Common mistakes or misconceptions around {topic}."},
        {"id": "compare", "label": "compare", "note": f"What {topic} is often confused with, and how to tell them apart."},
    ]
    edges = [
        {"source": root_id, "target": "intuition"},
        {"source": root_id, "target": "mechanics"},
        {"source": root_id, "target": "examples"},
        {"source": root_id, "target": "pitfalls"},
        {"source": root_id, "target": "compare"},
    ]
    return {"nodes": nodes, "edges": edges}


def _seed_artifact_doc(topic: str, artifact_type: str, instruction: str = "") -> dict[str, Any]:
    kind = _normalize_artifact_type(artifact_type)
    if kind == "concept_map":
        return _concept_map_doc(topic)
    return _flashcard_doc(topic, instruction)


def list_artifacts(*, user_id: str, workspace_id: str, limit: int = 20) -> list[dict[str, Any]]:
    artifacts_root = _artifacts_dir(user_id, workspace_id)
    items = sorted(artifacts_root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    results: list[dict[str, Any]] = []
    for path in items[: max(limit, 1)]:
        try:
            results.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return results


def load_artifact(
    *,
    user_id: str,
    artifact_id: str,
    workspace_id: str | None = None,
) -> dict[str, Any] | None:
    if workspace_id:
        path = _artifact_path(user_id, workspace_id, artifact_id)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    for workspace in list_workspaces(user_id):
        candidate = _artifact_path(user_id, workspace["workspace_id"], artifact_id)
        if not candidate.exists():
            continue
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def create_learning_artifact(
    *,
    user_id: str,
    workspace_id: str,
    topic: str,
    artifact_type: str,
    teaching_context: str = "",
    record_ids: list[str] | None = None,
) -> dict[str, Any]:
    workspace = load_workspace(user_id=user_id, workspace_id=workspace_id)
    if not workspace:
        raise FileNotFoundError(f"Unknown learning workspace: {workspace_id}")

    artifact_id = f"art_{uuid.uuid4().hex[:12]}"
    now = _now_iso()
    resolved_topic = topic.strip() or workspace["topic"]
    resolved_type = _normalize_artifact_type(artifact_type)
    # G2: LLM-generate real content; fall back to templates offline (local-first).
    generator = "llm"
    try:
        from guru_engine import generate_artifact_doc
        doc = generate_artifact_doc(
            topic=resolved_topic,
            artifact_type=resolved_type,
            teaching_context=teaching_context,
            packet=build_workspace_packet(user_id=user_id, workspace_id=workspace_id),
        )
    except Exception:
        doc = _seed_artifact_doc(resolved_topic, artifact_type, teaching_context)
        generator = "template"
    artifact = {
        "artifact_id": artifact_id,
        "workspace_id": workspace_id,
        "topic": resolved_topic,
        "artifact_type": resolved_type,
        "version": 1,
        "generator": generator,
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "record_ids": list(record_ids or []),
        "doc": doc,
    }
    _artifact_path(user_id, workspace_id, artifact_id).write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    meta_path = _workspace_meta_path(user_id, workspace_id)
    meta_payload = _read_json(meta_path, {})
    meta_payload["artifact_count"] = int(meta_payload.get("artifact_count", 0) or 0) + 1
    meta_payload["latest_artifact_id"] = artifact_id
    meta_payload["updated_at"] = now
    _write_json(meta_path, meta_payload)
    return artifact


def _artifact_history_dir(user_id: str, workspace_id: str) -> Path:
    path = _artifacts_dir(user_id, workspace_id) / "history"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _archive_artifact_version(user_id: str, workspace_id: str, artifact: dict[str, Any]) -> None:
    """G2: snapshot the current artifact version before revision (undo support)."""
    try:
        version = int(artifact.get("version", 1) or 1)
        path = _artifact_history_dir(user_id, workspace_id) / f"{artifact['artifact_id']}.v{version}.json"
        if not path.exists():
            path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def load_artifact_version(
    *,
    user_id: str,
    workspace_id: str,
    artifact_id: str,
    version: int,
) -> dict[str, Any] | None:
    path = _artifact_history_dir(user_id, workspace_id) / f"{artifact_id}.v{version}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _extract_focus_phrase(instruction: str) -> str:
    text = (instruction or "").strip()
    match = re.search(r"\b(?:about|for|on)\s+(.+?)(?:[.?!]|$)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r"\b(?:add|remove|delete)\s+(?:a|an|the)?\s*(?:node|card)\s+(.+?)(?:[.?!]|$)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text


def _append_flashcard(doc: dict[str, Any], topic: str, instruction: str) -> None:
    cards = list(doc.get("cards") or [])
    focus = _extract_focus_phrase(instruction) or topic
    card_id = f"card-{len(cards) + 1}"
    cards.append({
        "id": card_id,
        "front": f"What should you remember about {focus}?",
        "back": f"Explain {focus} in relation to {topic} with one crisp, practical takeaway.",
        "tags": [topic_key(topic), topic_key(focus)],
    })
    doc["cards"] = cards


def _remove_flashcard(doc: dict[str, Any], instruction: str) -> None:
    cards = list(doc.get("cards") or [])
    if not cards:
        return
    focus = _extract_focus_phrase(instruction).lower()
    if focus:
        filtered = [
            card for card in cards
            if focus not in str(card.get("front", "")).lower()
            and focus not in str(card.get("back", "")).lower()
        ]
        if len(filtered) != len(cards):
            doc["cards"] = filtered
            return
    doc["cards"] = cards[:-1]


def _append_concept_node(doc: dict[str, Any], topic: str, instruction: str) -> None:
    nodes = list(doc.get("nodes") or [])
    edges = list(doc.get("edges") or [])
    focus = _extract_focus_phrase(instruction) or "new concept"
    node_id = topic_key(focus)[:24] or f"node-{len(nodes) + 1}"
    if any(str(node.get("id")) == node_id for node in nodes):
        return
    root_id = str(nodes[0]["id"]) if nodes else "topic"
    nodes.append({
        "id": node_id,
        "label": focus,
        "note": f"How {focus} connects back to {topic}.",
    })
    edges.append({"source": root_id, "target": node_id})
    doc["nodes"] = nodes
    doc["edges"] = edges


def _remove_concept_node(doc: dict[str, Any], instruction: str) -> None:
    nodes = list(doc.get("nodes") or [])
    edges = list(doc.get("edges") or [])
    focus = _extract_focus_phrase(instruction).lower()
    if not focus or not nodes:
        return
    keep_ids = {
        str(node.get("id"))
        for node in nodes
        if focus not in str(node.get("label", "")).lower()
    }
    doc["nodes"] = [node for node in nodes if str(node.get("id")) in keep_ids]
    doc["edges"] = [
        edge for edge in edges
        if str(edge.get("source")) in keep_ids and str(edge.get("target")) in keep_ids
    ]


def update_learning_artifact(
    *,
    user_id: str,
    artifact_id: str,
    instruction: str,
    workspace_id: str | None = None,
    record_ids: list[str] | None = None,
) -> dict[str, Any]:
    artifact = load_artifact(user_id=user_id, artifact_id=artifact_id, workspace_id=workspace_id)
    if not artifact:
        raise FileNotFoundError(f"Unknown learning artifact: {artifact_id}")
    workspace_id = str(artifact["workspace_id"])
    doc = dict(artifact.get("doc") or {})
    artifact_type = _normalize_artifact_type(str(artifact.get("artifact_type", "flashcards")))
    # Keep the outgoing version for undo before touching the doc.
    _archive_artifact_version(user_id, workspace_id, artifact)
    # G2: LLM revision first; regex add/remove path remains the offline fallback.
    reviser = "llm"
    try:
        from guru_engine import revise_artifact_doc
        doc = revise_artifact_doc(
            doc=doc,
            topic=str(artifact.get("topic", "")),
            artifact_type=artifact_type,
            instruction=instruction,
        )
    except Exception:
        reviser = "template"
        lowered = (instruction or "").strip().lower()
        if artifact_type == "concept_map":
            if any(word in lowered for word in ("remove", "delete")):
                _remove_concept_node(doc, instruction)
            else:
                _append_concept_node(doc, str(artifact.get("topic", "")), instruction)
        else:
            if any(word in lowered for word in ("remove", "delete")):
                _remove_flashcard(doc, instruction)
            else:
                _append_flashcard(doc, str(artifact.get("topic", "")), instruction)
    artifact["doc"] = doc
    artifact["generator"] = reviser
    artifact["version"] = int(artifact.get("version", 1) or 1) + 1
    artifact["updated_at"] = _now_iso()
    if record_ids:
        merged = [*artifact.get("record_ids", []), *record_ids]
        artifact["record_ids"] = list(dict.fromkeys(str(item) for item in merged if item))
    _artifact_path(user_id, workspace_id, artifact_id).write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    meta_path = _workspace_meta_path(user_id, workspace_id)
    meta_payload = _read_json(meta_path, {})
    meta_payload["latest_artifact_id"] = artifact_id
    meta_payload["updated_at"] = artifact["updated_at"]
    _write_json(meta_path, meta_payload)
    return artifact


def append_learning_record(
    *,
    user_id: str,
    workspace_id: str,
    title: str,
    summary: str,
    body: str,
    record_type: str = "lesson",
    session_id: str | None = None,
    tags: list[str] | None = None,
    source: str = "krishna",
) -> dict[str, Any]:
    meta = load_workspace(user_id=user_id, workspace_id=workspace_id)
    if not meta:
        raise FileNotFoundError(f"Unknown learning workspace: {workspace_id}")
    records = list_records(user_id=user_id, workspace_id=workspace_id, limit=10_000)
    next_index = len(records) + 1
    record_id = f"{next_index:04d}"
    slug = _slugify(title)[:48]
    filename = _record_filename(next_index, slug)
    path = _records_dir(user_id, workspace_id) / filename
    payload = "\n".join([
        f"# {title}",
        "",
        f"- created_at: {_now_iso()}",
        f"- type: {record_type}",
        f"- source: {source}",
        f"- session_id: {session_id or ''}",
        f"- tags: {', '.join(tags or [])}",
        "",
        "## Summary",
        summary.strip(),
        "",
        "## Body",
        body.strip(),
        "",
    ])
    path.write_text(payload, encoding="utf-8")

    meta_path = _workspace_meta_path(user_id, workspace_id)
    meta_payload = _read_json(meta_path, {})
    meta_payload["record_count"] = int(meta_payload.get("record_count", 0) or 0) + 1
    meta_payload["updated_at"] = _now_iso()
    meta_payload["latest_record_id"] = record_id
    if session_id:
        meta_payload["last_session_id"] = session_id
    _write_json(meta_path, meta_payload)
    return _load_record(path)


def merge_resources(*, user_id: str, workspace_id: str, resources: list[dict[str, Any]]) -> dict[str, Any]:
    meta = load_workspace(user_id=user_id, workspace_id=workspace_id)
    if not meta:
        raise FileNotFoundError(f"Unknown learning workspace: {workspace_id}")
    current = _parse_resource_lines(meta["resources"])
    seen = {(item["url"], item["title"]) for item in current}
    for item in resources:
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title or not url:
            continue
        key = (url, title)
        if key in seen:
            continue
        seen.add(key)
        current.append({
            "title": title,
            "url": url,
            "note": str(item.get("snippet") or item.get("note") or item.get("source") or "").strip()[:220],
        })
    _resources_path(user_id, workspace_id).write_text(
        _render_resources(meta["topic"], current),
        encoding="utf-8",
    )
    meta_path = _workspace_meta_path(user_id, workspace_id)
    meta_payload = _read_json(meta_path, {})
    meta_payload["resource_count"] = len(current)
    meta_payload["updated_at"] = _now_iso()
    _write_json(meta_path, meta_payload)
    return load_workspace(user_id=user_id, workspace_id=workspace_id) or meta


def update_glossary_terms(*, user_id: str, workspace_id: str, entries: dict[str, str]) -> dict[str, Any]:
    meta = load_workspace(user_id=user_id, workspace_id=workspace_id)
    if not meta:
        raise FileNotFoundError(f"Unknown learning workspace: {workspace_id}")
    glossary = _parse_glossary_lines(meta["glossary"])
    for term, definition in entries.items():
        term_clean = term.strip()
        definition_clean = definition.strip()
        if not term_clean or not definition_clean:
            continue
        glossary[term_clean] = definition_clean
    _glossary_path(user_id, workspace_id).write_text(
        _render_glossary(meta["topic"], glossary),
        encoding="utf-8",
    )
    meta_path = _workspace_meta_path(user_id, workspace_id)
    meta_payload = _read_json(meta_path, {})
    meta_payload["glossary_term_count"] = len(glossary)
    meta_payload["updated_at"] = _now_iso()
    _write_json(meta_path, meta_payload)
    return load_workspace(user_id=user_id, workspace_id=workspace_id) or meta


def suggest_glossary_entries(topic: str, explanation: str) -> dict[str, str]:
    first_sentence = re.split(r"(?<=[.!?])\s+", explanation.strip(), maxsplit=1)[0].strip()
    entries: dict[str, str] = {}
    if topic.strip() and first_sentence:
        entries[topic.strip()] = first_sentence[:220]
    for match in re.findall(r"`([^`]{2,40})`", explanation):
        term = match.strip()
        if term and term not in entries:
            entries[term] = "Referenced during this teaching session."
    for match in re.findall(r"\b[A-Z]{2,8}\b", explanation):
        if match not in entries:
            entries[match] = "Acronym referenced during this teaching session."
    return entries


def _syllabus_packet_lines(user_id: str, workspace_id: str) -> list[str]:
    """SYLLABUS PROGRESS + CURRENT TEACHING ATOM sections for the packet (G6.1).

    Lazy guru_engine import; returns [] on any failure so the packet always builds.
    """
    try:
        from guru_engine import frontier_atom, load_learner_state, load_syllabus, mastery_summary
        syllabus = load_syllabus(user_id=user_id, workspace_id=workspace_id)
        if not syllabus:
            return []
        state = load_learner_state(user_id=user_id, workspace_id=workspace_id)
        counts = mastery_summary(syllabus, state)
        lines = [
            f"SYLLABUS PROGRESS: {counts['mastered']}/{counts['total']} atoms mastered"
            + (f", {counts['shaky']} shaky" if counts["shaky"] else ""),
            "",
        ]
        atom = frontier_atom(syllabus, state)
        if atom is None:
            lines.append("CURRENT TEACHING ATOM: (all atoms mastered — offer review or a harder angle)")
            lines.append("")
            return lines
        status = str((state.get(str(atom.get("id", ""))) or {}).get("status", "untaught"))
        check = atom.get("check") or {}
        lines.append(
            f"CURRENT TEACHING ATOM: {atom.get('name', atom.get('id', ''))} [{atom.get('id', '')}] (status: {status})"
        )
        # Omit empty fields — a dangling "- Analogy:" line reads as an
        # instruction to echo nothing and produces weird teaching output.
        eli5 = str(atom.get("eli5", "")).strip()
        plain = str(atom.get("plain", "")).strip()
        misconception = str(atom.get("misconception", "")).strip()
        question = str(check.get("q", "")).strip()
        if eli5:
            lines.append(f"- Analogy: {eli5[:300]}")
        if plain:
            lines.append(f"- Plain: {plain[:300]}")
        if misconception:
            lines.append(f"- Misconception to preempt: {misconception[:240]}")
        if question:
            lines.append(f"- Check question to ask: {question[:200]}")
        else:
            lines.append(
                "- Check question to ask: (none in the syllabus — compose ONE "
                "short question yourself that tests this exact idea)"
            )
        lines.append("")
        return lines
    except Exception:
        return []


def build_workspace_packet(*, user_id: str, workspace_id: str, max_records: int = 3, max_chars: int = 4500) -> str:
    workspace = load_workspace(user_id=user_id, workspace_id=workspace_id)
    if not workspace:
        return ""
    lines = [
        f"Learning workspace: {workspace['topic']} ({workspace_id})",
        "",
        "MISSION:",
        workspace["mission"][:800].strip(),
        "",
    ]
    lines += _syllabus_packet_lines(user_id, workspace_id)
    glossary = _parse_glossary_lines(workspace["glossary"])
    if glossary:
        lines.append("GLOSSARY:")
        for term, definition in list(glossary.items())[:8]:
            lines.append(f"- {term}: {definition}")
        lines.append("")
    resources = _parse_resource_lines(workspace["resources"])
    if resources:
        lines.append("RESOURCES:")
        for item in resources[:5]:
            note = f" — {item['note']}" if item.get("note") else ""
            lines.append(f"- {item['title']}: {item['url']}{note}")
        lines.append("")
    records = list_records(user_id=user_id, workspace_id=workspace_id, limit=max_records)
    if records:
        lines.append("RECENT LEARNING RECORDS:")
        for record in records:
            lines.append(f"- {record['title']}: {record['summary'][:180]}")
    packet = "\n".join(lines).strip()
    return packet[:max_chars]
