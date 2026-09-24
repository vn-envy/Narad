"""Evidence for Workflow Paths: a stage is done only when the Mac can check it.

Every stage declares ``done_when``: structured conditions that must all hold
(``any`` groups alternatives). The engine checks them against evidence it
verifies itself, never against what a model wrote about its work:

  inputs_complete        every required intake detail has a value
  reported(fields)       the stage owner's ``done`` report carries these fields
  tool_succeeded(tools)  the server saw one of these tools return a success
                         status during a chat turn bound to this run
  artifact_exists(kind)  a generated file of that kind exists under the run
                         owner's own artifact folder
  records_saved(table)   rows the person confirmed exist in their own health.db
                         or finance.db, or in the path's application tracker
  task_done              a Kriya task of this profile finished
  approval_executed      an Anumati proposal of this profile was approved and ran
  user_confirmed         the person tapped "Done" on the Paths screen
  guided(event)          the Gurukul loop graded an answer or finished

Tool receipts are recorded by the avatar tool wrapper from the real function
responses of a turn the server bound to the run (``bind_turn``); a model can
name an id, but a review, task, proposal or file counts only once it is found
in the run owner's own stores. Another profile's id is simply not found.
"""

from __future__ import annotations

import contextvars
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from narad_config import ARTIFACTS_DIR

CONDITION_KINDS = frozenset({
    "inputs_complete", "reported", "tool_succeeded", "artifact_exists", "records_saved",
    "task_done", "approval_executed", "user_confirmed", "guided", "any",
})
STAGE_RESULT_STATUSES = ("done", "needs_input", "blocked", "in_progress")
RECORD_TABLES = frozenset({"lab_results", "transactions", "applications"})
# Tool statuses that mean the call did what it was asked. Anything else,
# an unknown or missing status included, is not a success.
SUCCESS_STATUSES = frozenset({
    "ok", "success", "set", "logged", "created", "stored", "saved", "sent", "done", "task_started",
})
# Conditions a model's work can satisfy without finishing the stage (a search
# ran, a file exists): they also need the owner's own "done" report.
_CLAIM_KINDS = frozenset({"reported", "tool_succeeded", "artifact_exists"})
_REVIEW_RE = re.compile(r"^rev_[a-f0-9]{16}$")
_TASK_RE = re.compile(r"^tsk_[0-9a-f]{16}$")
_PROPOSAL_RE = re.compile(r"^apr_[0-9a-f]{16}$")
_NOT_EVIDENCE_TOOLS = frozenset({"report_stage_result"})
_MAX_RECEIPTS = 80
_ARTIFACT_KINDS = {
    ".html": "webpage", ".htm": "webpage",
    ".docx": "document", ".pdf": "document", ".md": "document", ".txt": "document", ".pptx": "document",
    ".zip": "export",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
    ".mp4": "video",
}


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def _when(value: Any) -> datetime | None:
    """A stored time as aware UTC; naive values were written in local time."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.astimezone().astimezone(timezone.utc)


def artifact_kind(name: str) -> str:
    return _ARTIFACT_KINDS.get(Path(str(name or "")).suffix.lower(), "file")


def _empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return not value
    return False


# ── Plain words ───────────────────────────────────────────────────────────────

_TABLE_WORDS = {
    "lab_results": "lab value you confirmed",
    "transactions": "statement transaction",
    "applications": "role in your application tracker",
}


def describe(condition: dict[str, Any]) -> str:
    """One condition in plain words, as the Paths screen shows it."""
    if condition.get("label"):
        return str(condition["label"])
    kind = condition.get("kind")
    if kind == "any":
        return " or ".join(describe(item) for item in condition.get("of", []))
    if kind == "inputs_complete":
        return "Every required detail is filled in"
    if kind == "reported":
        return f"Narad reports {', '.join(str(f).replace('_', ' ') for f in condition.get('fields', []))}"
    if kind == "tool_succeeded":
        return f"{' or '.join(str(t).replace('_', ' ') for t in condition.get('tools', []))} ran successfully"
    if kind == "artifact_exists":
        return f"A {condition.get('artifact', 'file')} file was created"
    if kind == "records_saved":
        count = int(condition.get("min", 1) or 1)
        words = _TABLE_WORDS.get(str(condition.get("table")), "record")
        return f"At least {count} {words}{'' if count == 1 else 's'} saved"
    if kind == "task_done":
        return "A web task finished"
    if kind == "approval_executed":
        return "You approved this step on your phone"
    if kind == "user_confirmed":
        return "You mark this step done"
    if kind == "guided":
        return "You finished the guided lesson" if condition.get("event") == "complete" else "You answered a check"
    return str(kind or "condition")


def validate_done_when(conditions: Any) -> list[str]:
    """Problems with a pack's done_when (empty when it is well formed)."""
    problems: list[str] = []
    if not isinstance(conditions, list) or not conditions:
        return ["done_when must be a non-empty list"]
    for condition in conditions:
        kind = condition.get("kind") if isinstance(condition, dict) else None
        if kind not in CONDITION_KINDS:
            problems.append(f"unknown condition {kind!r}")
        elif kind == "any":
            problems.extend(validate_done_when(condition.get("of")))
        elif kind == "reported" and not condition.get("fields"):
            problems.append("reported needs fields")
        elif kind == "tool_succeeded" and not condition.get("tools"):
            problems.append("tool_succeeded needs tools")
        elif kind == "records_saved" and condition.get("table") not in RECORD_TABLES:
            problems.append(f"records_saved table {condition.get('table')!r}")
    return problems


def reported_fields(conditions: list[dict[str, Any]]) -> list[str]:
    """Every field a stage's reports can be asked for, in declaration order."""
    fields: list[str] = []
    for condition in conditions or []:
        if condition.get("kind") == "any":
            fields.extend(reported_fields(condition.get("of", [])))
        elif condition.get("kind") == "reported":
            fields.extend(str(item) for item in condition.get("fields", []))
    return list(dict.fromkeys(fields))


def _is_claim(condition: dict[str, Any]) -> bool:
    kind = condition.get("kind")
    return kind in _CLAIM_KINDS and not (kind == "artifact_exists" and condition.get("artifact") == "export")


# ── Tool receipts from a bound chat turn ──────────────────────────────────────


@dataclass
class TurnEvidence:
    """What one chat turn bound to a path produced, as the server saw it."""

    run_id: str
    user_id: str
    session_id: str
    receipts: list[dict[str, Any]] = field(default_factory=list)
    reported: bool = False


_turn: contextvars.ContextVar[TurnEvidence | None] = contextvars.ContextVar("narad_workflow_turn", default=None)


def bind_turn(run_id: str, *, user_id: str, session_id: str) -> contextvars.Token:
    return _turn.set(TurnEvidence(run_id=run_id, user_id=user_id, session_id=session_id))


def unbind_turn(token: contextvars.Token) -> None:
    try:
        _turn.reset(token)
    except ValueError:  # reset from another context: leave it to that context's end
        pass


def current_turn() -> TurnEvidence | None:
    return _turn.get()


def record_tool_result(tool: str, response: Any) -> None:
    """Called by the avatar tool wrapper for every tool response of a turn."""
    turn = _turn.get()
    if turn is None or tool in _NOT_EVIDENCE_TOOLS:
        return
    receipt = receipt_from_tool(tool, response)
    if receipt is not None and len(turn.receipts) < _MAX_RECEIPTS:
        turn.receipts.append(receipt)


def _artifact_entry(item: dict[str, Any]) -> dict[str, Any] | None:
    path = str(item.get("path") or item.get("file_path") or "")
    url = str(item.get("url") or "")
    if not path and "/media/" not in url:
        return None
    name = Path(path).name if path else url.rsplit("/", 1)[-1]
    return {
        "type": str(item.get("type") or "") or artifact_kind(name),
        "label": str(item.get("label") or name)[:120],
        "path": path,
        "url": url,
    }


def receipt_from_tool(tool: str, response: Any) -> dict[str, Any] | None:
    """A compact, checkable record of one tool response (None if it has no status)."""
    if not isinstance(response, dict):
        return None
    status = str(response.get("status") or "").strip().lower()
    if not status:
        return None
    refs: dict[str, Any] = {}
    review_id = str(response.get("review_id") or "")
    if _REVIEW_RE.fullmatch(review_id):
        refs["review_id"] = review_id
    task = response.get("task") if isinstance(response.get("task"), dict) else {}
    task_id = str(response.get("task_id") or task.get("id") or "")
    if _TASK_RE.fullmatch(task_id):
        refs["task_id"] = task_id
    approval = response.get("approval") if isinstance(response.get("approval"), dict) else {}
    proposal_id = str(response.get("proposal_id") or approval.get("id") or "")
    if _PROPOSAL_RE.fullmatch(proposal_id):
        refs["proposal_id"] = proposal_id
    artifacts = [
        entry for item in (response.get("artifacts") or []) if isinstance(item, dict)
        if (entry := _artifact_entry(item))
    ]
    if response.get("file_path") and (entry := _artifact_entry(response)):
        artifacts.append(entry)
    if artifacts:
        refs["artifacts"] = artifacts[:10]
    for key in ("imported", "duplicates", "count", "record_id"):
        value = response.get(key)
        if isinstance(value, (int, str)) and not isinstance(value, bool) and str(value).strip():
            refs[key] = value
    return {
        "receipt_id": f"rct_{uuid.uuid4().hex[:12]}",
        "tool": str(tool),
        "status": status,
        "ok": status in SUCCESS_STATUSES and not response.get("error"),
        "refs": refs,
        "summary": " ".join(str(response.get("summary") or response.get("message") or "").split())[:200],
        "at": _iso(),
    }


# ── Verification in the run owner's own stores ────────────────────────────────


def _media_path(url: str) -> Path | None:
    marker = "/media/"
    if marker not in url:
        return None
    relative = unquote(url.split(marker, 1)[1].split("?", 1)[0].split("#", 1)[0])
    return Path(ARTIFACTS_DIR) / relative


def verify_artifact(item: dict[str, Any], profile_id: str) -> dict[str, Any] | None:
    """A generated file that exists under the profile's own runs/ folder."""
    raw = str(item.get("path") or "")
    path = Path(raw) if raw else _media_path(str(item.get("url") or ""))
    if path is None:
        return None
    root = (Path(ARTIFACTS_DIR) / "runs" / profile_id).resolve()
    try:
        resolved = path.expanduser().resolve()
        relative = resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    if not resolved.is_file():
        return None
    return {
        "kind": "artifact",
        "id": f"runs/{profile_id}/{relative.as_posix()}",
        "type": artifact_kind(resolved.name),
        "label": str(item.get("label") or resolved.name)[:120],
        "url": f"/media/runs/{profile_id}/{relative.as_posix()}",
        "status": "saved",
        "created_at": _iso(datetime.fromtimestamp(resolved.stat().st_mtime, timezone.utc)),
    }


_DOC_WORDS = {
    "lab_report": "Lab report", "bank_statement": "Bank statement", "prescription": "Prescription",
    "school_circular": "School circular", "bill": "Bill", "generic": "Document",
}


def verify_review(review_id: str, profile_id: str) -> dict[str, Any] | None:
    """A document review in the profile's own folder, with what its save stored."""
    try:
        import document_review
    except ImportError:
        return None
    review = document_review.load_review(review_id, profile_id)
    if not review or str(review.get("profile_id") or profile_id) != profile_id:
        return None
    rows: dict[str, int] = {}
    for row in (review.get("saved") or {}).get("rows", []):
        if row.get("status") in {"stored", "created", "duplicate"}:
            rows[str(row.get("table"))] = rows.get(str(row.get("table")), 0) + 1
    return {
        "kind": "review",
        "id": review_id,
        "status": str(review.get("status") or ""),
        "label": f"{_DOC_WORDS.get(str(review.get('doc_type')), 'Document')} review",
        "url": f"/?review={review_id}",
        "created_at": str(review.get("created_at") or ""),
        "rows": rows,
        "txn_ids": [
            str(row.get("row_id")) for row in (review.get("saved") or {}).get("rows", [])
            if row.get("table") == "transactions" and row.get("row_id")
        ],
    }


def verify_task(task_id: str, profile_id: str) -> dict[str, Any] | None:
    try:
        from kriya import store
    except ImportError:
        return None
    try:
        task = store.get_task(task_id, profile_id=profile_id)
    except (KeyError, ValueError):
        return None
    result = task.result or {}
    return {
        "kind": "task",
        "id": task.task_id,
        "status": task.status,
        "label": task.goal[:120],
        "url": f"/?task={task.task_id}",
        "created_at": _iso(datetime.fromtimestamp(task.created_ts, timezone.utc)),
        "answer": " ".join(str(result.get("answer") or result.get("summary") or "").split())[:600],
    }


def verify_proposal(proposal_id: str, profile_id: str) -> dict[str, Any] | None:
    import anumati

    try:
        proposal = anumati.get(proposal_id, profile_id=profile_id)
    except (anumati.ApprovalError, KeyError, ValueError):
        return None
    return {
        "kind": "approval",
        "id": proposal.proposal_id,
        "status": proposal.status,
        "surface": proposal.surface,
        "target": proposal.target,
        "cycle": (proposal.args or {}).get("cycle") if isinstance(proposal.args, dict) else None,
        "label": proposal.summary[:120],
        "url": f"/?approval={proposal.proposal_id}",
        "created_at": _iso(datetime.fromtimestamp(proposal.created_ts, timezone.utc)),
    }


def _lab_rows(review_id: str, profile_id: str) -> int:
    """Lab values in the profile's own health.db that came from this review."""
    try:
        import health_skill

        from profile_context import profile_scope
    except ImportError:
        return 0
    with profile_scope(profile_id):
        conn = health_skill._get_conn()
        try:
            row = conn.execute("SELECT COUNT(*) FROM lab_results WHERE source_review_id = ?", (review_id,)).fetchone()
        finally:
            conn.close()
    return int(row[0] or 0)


def _ledger_rows(txn_ids: list[str], profile_id: str) -> int:
    """How many of these transaction ids are in the profile's own finance.db."""
    if not txn_ids:
        return 0
    try:
        import finance_skill

        from profile_context import profile_scope
    except ImportError:
        return 0
    with profile_scope(profile_id):
        conn = finance_skill._db()
        try:
            marks = ",".join("?" for _ in txn_ids)
            row = conn.execute(f"SELECT COUNT(*) FROM transactions WHERE id IN ({marks})", txn_ids).fetchone()
        finally:
            conn.close()
    return int(row[0] or 0)


def _ledger_total(profile_id: str) -> int:
    try:
        import finance_skill

        from profile_context import profile_scope
    except ImportError:
        return 0
    with profile_scope(profile_id):
        conn = finance_skill._db()
        try:
            row = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()
        finally:
            conn.close()
    return int(row[0] or 0)


# ── The check ─────────────────────────────────────────────────────────────────


@dataclass
class StageContext:
    """What the engine knows about the stage being checked."""

    profile_id: str
    run_id: str
    stage_id: str
    cycle: int
    run_created_at: str
    missing_inputs: list[str] = field(default_factory=list)
    stage_proposal_id: str = ""
    record_count: Callable[[str, str], int] = lambda _kind, _status: 0


def classify_id(value: str) -> str:
    """What a model-named evidence id looks like ('' if nothing checkable)."""
    text = str(value or "").strip()
    if _REVIEW_RE.fullmatch(text):
        return "review"
    if _TASK_RE.fullmatch(text):
        return "task"
    if _PROPOSAL_RE.fullmatch(text):
        return "approval"
    if "/media/" in text or text.startswith(("runs/", "/")):
        return "artifact"
    return ""


def _after_run_start(ref: dict[str, Any], ctx: StageContext) -> bool:
    """A named id counts only for work done since this run began."""
    made, started = _when(ref.get("created_at")), _when(ctx.run_created_at)
    if not made or not started:
        return False
    if made.microsecond == 0:  # stored to the second (reviews): compare to the second
        started = started.replace(microsecond=0)
    return made >= started


def verified_refs(evidence: dict[str, Any], ctx: StageContext) -> dict[str, list[dict[str, Any]]]:
    """Every review, task, approval and file in the stage's evidence that checks out."""
    found: dict[str, dict[str, dict[str, Any]]] = {"review": {}, "task": {}, "approval": {}, "artifact": {}}

    def add(kind: str, ref: dict[str, Any] | None, *, named: bool) -> None:
        if ref is None or ref["id"] in found[kind]:
            return
        if named and not _after_run_start(ref, ctx):
            return
        found[kind][ref["id"]] = ref

    for receipt in evidence.get("receipts") or []:
        refs = receipt.get("refs") or {}
        if refs.get("review_id"):
            add("review", verify_review(refs["review_id"], ctx.profile_id), named=False)
        if refs.get("task_id"):
            add("task", verify_task(refs["task_id"], ctx.profile_id), named=False)
        if refs.get("proposal_id"):
            add("approval", verify_proposal(refs["proposal_id"], ctx.profile_id), named=False)
        if receipt.get("ok"):
            for item in refs.get("artifacts") or []:
                add("artifact", verify_artifact(item, ctx.profile_id), named=False)
    for value in evidence.get("claimed") or []:
        kind = classify_id(value)
        if kind == "review":
            add("review", verify_review(value, ctx.profile_id), named=True)
        elif kind == "task":
            add("task", verify_task(value, ctx.profile_id), named=True)
        elif kind == "approval":
            add("approval", verify_proposal(value, ctx.profile_id), named=True)
        elif kind == "artifact":
            item = {"url": value} if "/media/" in value else {"path": str(Path(ARTIFACTS_DIR) / value)
                                                               if value.startswith("runs/") else value}
            add("artifact", verify_artifact(item, ctx.profile_id), named=True)
    if ctx.stage_proposal_id:
        add("approval", verify_proposal(ctx.stage_proposal_id, ctx.profile_id), named=False)
    return {kind: list(items.values()) for kind, items in found.items()}


def _records(condition: dict[str, Any], evidence: dict[str, Any], refs: dict, ctx: StageContext) -> tuple[int, str]:
    table = str(condition.get("table"))
    if table == "applications":
        count = ctx.record_count("application", str(condition.get("status") or ""))
        return count, "" if count else "Nothing is in the application tracker yet."
    if table == "lab_results":
        count = sum(_lab_rows(ref["id"], ctx.profile_id) for ref in refs["review"] if ref["status"] == "saved")
        pending = [ref for ref in refs["review"] if ref["status"] == "pending"]
        if count:
            return count, ""
        return 0, ("Confirm the values on the review screen." if pending
                   else "Send a lab report photo or PDF in this chat.")
    if table == "transactions":
        count = sum(
            _ledger_rows(ref.get("txn_ids", []), ctx.profile_id)
            for ref in refs["review"] if ref["status"] == "saved"
        )
        imported = sum(
            int(receipt["refs"].get("imported", 0) or 0) + int(receipt["refs"].get("duplicates", 0) or 0)
            for receipt in evidence.get("receipts") or []
            if receipt.get("tool") == "import_csv" and receipt.get("ok")
        )
        if imported:
            count += min(imported, _ledger_total(ctx.profile_id))
        pending = [ref for ref in refs["review"] if ref["status"] == "pending"]
        if count:
            return count, ""
        return 0, ("Confirm the statement lines on the review screen." if pending
                   else "Send your bank statement (CSV, PDF or a photo) in this chat.")
    return 0, "Unknown record table."


def _evaluate(condition: dict[str, Any], evidence: dict[str, Any], refs: dict, ctx: StageContext) -> dict[str, Any]:
    kind = condition.get("kind")
    met, detail, used = False, "", []
    # Whether satisfying this relied on the model's own work (a report, a tool
    # run, a file); such a stage also needs its owner's "done" report.
    claim = _is_claim(condition)
    if kind == "any":
        parts = [_evaluate(item, evidence, refs, ctx) for item in condition.get("of", [])]
        met = any(part["met"] for part in parts)
        detail = next((part["detail"] for part in parts if part["met"]), "") or next(
            (part["detail"] for part in parts if part["detail"]), "")
        used = [ref for part in parts if part["met"] for ref in part["evidence"]]
        met_parts = [part for part in parts if part["met"]]
        claim = all(part["claim"] for part in met_parts) if met_parts else any(part["claim"] for part in parts)
    elif kind == "inputs_complete":
        met = not ctx.missing_inputs
        detail = "" if met else f"Still needed: {', '.join(ctx.missing_inputs)}."
    elif kind == "reported":
        report = evidence.get("report") or {}
        fields = report.get("fields") or {}
        missing = [name for name in condition.get("fields", []) if _empty(fields.get(name))]
        met = report.get("status") == "done" and not missing
        if not met:
            detail = (f"The stage result is missing: {', '.join(missing)}." if missing
                      else "The stage owner has not reported this stage done.")
    elif kind == "tool_succeeded":
        tools = set(condition.get("tools", []))
        mine = [receipt for receipt in evidence.get("receipts") or [] if receipt.get("tool") in tools]
        succeeded = [receipt for receipt in mine if receipt.get("ok")]
        met = len(succeeded) >= int(condition.get("min", 1) or 1)
        failed = [receipt for receipt in mine if not receipt.get("ok")]
        if not met:
            detail = (f"{failed[-1]['tool']} returned {failed[-1]['status']}." if failed
                      else "It has not run in this stage yet.")
        used = [{"kind": "tool", "id": r["receipt_id"], "label": r["tool"], "status": r["status"]} for r in succeeded]
    elif kind == "artifact_exists":
        wanted = str(condition.get("artifact") or "any")
        allowed = {"document": {"document", "webpage"}}.get(wanted, {wanted})
        used = [ref for ref in refs["artifact"] if wanted == "any" or ref["type"] in allowed]
        met = bool(used)
        detail = "" if met else "No such file has been created for this stage yet."
    elif kind == "records_saved":
        count, detail = _records(condition, evidence, refs, ctx)
        met = count >= int(condition.get("min", 1) or 1)
        if met:
            detail = f"{count} saved."
            table = str(condition.get("table"))
            used = [ref for ref in refs["review"] if ref["status"] == "saved" and ref["rows"].get(table)]
    elif kind == "task_done":
        done = [ref for ref in refs["task"] if ref["status"] == "done"]
        met = bool(done)
        used = done
        if not met:
            running = [ref for ref in refs["task"] if ref["status"] not in {"done", "failed", "cancelled"}]
            failed = [ref for ref in refs["task"] if ref["status"] in {"failed", "cancelled"}]
            detail = ("The task is still running." if running
                      else f"The task {failed[-1]['status']}." if failed else "No task has run for this stage yet.")
    elif kind == "approval_executed":
        surface = str(condition.get("surface") or "workflow")
        if surface == "workflow":
            candidates = [
                ref for ref in refs["approval"]
                if ref["id"] == ctx.stage_proposal_id and ref["target"] == f"{ctx.run_id}:{ctx.stage_id}"
            ]
        else:
            candidates = [ref for ref in refs["approval"] if ref["surface"] == surface]
        used = [ref for ref in candidates if ref["status"] == "executed"]
        met = bool(used)
        if not met:
            detail = ("Waiting for your approval." if any(ref["status"] in {"pending", "approved"} for ref in candidates)
                      else "No approval has been asked for yet.")
    elif kind == "user_confirmed":
        confirmed = evidence.get("user_confirmed") or {}
        met = bool(confirmed.get("at"))
        if met:
            used = [{"kind": "confirmation", "id": "user", "label": "You confirmed", "created_at": confirmed["at"]}]
    elif kind == "guided":
        wanted = {"answer": {"answer", "skip", "complete"}, "complete": {"complete"}}.get(
            str(condition.get("event")), {str(condition.get("event"))})
        events = [item for item in evidence.get("guided") or [] if item.get("event") in wanted]
        met = bool(events)
        detail = "" if met else "Waiting for the guided lesson."
    else:
        detail = f"Unknown condition {kind!r}."
    return {"kind": kind, "text": describe(condition), "met": met, "detail": detail, "evidence": used, "claim": claim}


def check_done_when(conditions: list[dict[str, Any]], evidence: dict[str, Any], ctx: StageContext) -> dict[str, Any]:
    """Whether a stage's done_when holds, condition by condition, with what proves it.

    ``needs_report`` is true when a condition was (or can only be) met by the
    model's own work; the engine then also wants the owner's "done" report. A
    stage met only by outcomes (saved records, a finished task, an approval,
    the person's tap, the guided loop) settles on its own.
    """
    refs = verified_refs(evidence or {}, ctx)
    results = [_evaluate(condition, evidence or {}, refs, ctx) for condition in conditions or []]
    return {
        "met": bool(results) and all(item["met"] for item in results),
        "needs_report": any(item["claim"] for item in results),
        "conditions": results,
        "missing": [item["text"] for item in results if not item["met"]],
        "refs": [ref for kind in ("review", "task", "approval", "artifact") for ref in refs[kind]],
    }
