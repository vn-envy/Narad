"""Real, checkable evidence for Workflow Paths tests (no model, no network).

Each helper leaves behind what a stubbed tool really would: a receipt built from
its response, a document review confirmed into the profile's own health.db or
finance.db, a finished task in the profile's kriya.db, a file under the
profile's artifact folder. Tests therefore exercise the same verification the
engine runs in production, and a helper can never mark a stage done by itself.
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

_root = next(path for path in Path(__file__).resolve().parents if (path / "narad_paths.py").exists())
sys.path[:0] = [str(_root)]
import narad_paths  # noqa: F401

# isort: split
import document_review
import kriya.runtime as kriya_runtime
from kriya import store as kriya_store

import profile_context
import vahana
import workflow_engine
import workflow_evidence

ALL_CAPABILITIES = {
    name: True
    for name in (
        "planning", "learning", "health", "finance", "documents", "presentation",
        "filesystem", "sql", "tts", "search", "computer", "calendar", "email",
    )
}
THREAD = "thread-a"


class FakeKriya:
    """Stands in for the task runtime: a submitted task is stored (running) and
    finishes only when a test says so."""

    def __init__(self) -> None:
        self.submitted: list[str] = []

    def submit(self, *, profile_id: str, goal: str, start_url: str = "", done_when: str = "",
               surface: str = "browser", session_id: str | None = None, max_steps: int | None = None):
        task = kriya_store.create_task(profile_id=profile_id, goal=goal, start_url=start_url,
                                       surface="cloud_browser", session_id=session_id)
        kriya_store.update_task(task.task_id, profile_id=profile_id, status="running")
        self.submitted.append(task.task_id)
        return kriya_store.get_task(task.task_id, profile_id=profile_id)


def isolate(tmp_path: Path, monkeypatch: Any) -> SimpleNamespace:
    monkeypatch.setattr(workflow_engine, "WORKFLOW_DB", tmp_path / "workflows.db")
    monkeypatch.setattr(vahana, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr(profile_context, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(workflow_evidence, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setitem(sys.modules, "karma_log", SimpleNamespace(log_karma=lambda *args, **kwargs: None))
    monkeypatch.setattr(workflow_engine, "_capability_flags", lambda: dict(ALL_CAPABILITIES))
    runtime = FakeKriya()
    monkeypatch.setattr(kriya_runtime, "_RUNTIME", runtime)
    return SimpleNamespace(root=tmp_path, kriya=runtime)


# ── What stubbed tools leave behind ──────────────────────────────────────────


def receipt(tool: str, **response: Any) -> dict[str, Any]:
    response.setdefault("status", "ok")
    made = workflow_evidence.receipt_from_tool(tool, response)
    assert made is not None
    return made


def artifact_file(profile_id: str, name: str = "draft.docx", text: str = "draft") -> dict[str, str]:
    folder = Path(workflow_evidence.ARTIFACTS_DIR) / "runs" / profile_id / f"exec_{uuid.uuid4().hex[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    relative = path.relative_to(Path(workflow_evidence.ARTIFACTS_DIR)).as_posix()
    return {"path": str(path), "url": f"http://127.0.0.1:8000/media/{relative}"}


def document_receipt(profile_id: str, name: str = "draft.docx") -> dict[str, Any]:
    """create_document's real return shape: status, url and file_path."""
    made = artifact_file(profile_id, name)
    return receipt("create_document", url=made["url"], file_path=made["path"], message="Document generated.")


_LAB_ITEM = {
    "id": "i1", "kind": "lab", "label": "HbA1c", "value": "6.1", "unit": "%", "reference_range": "4.0-5.6",
    "flag": "high", "normalised_value": 6.1, "default_checked": True, "saveable": True, "confidence": 0.98,
}
_TXN_ITEM = {
    "id": "i1", "kind": "transaction", "label": "Grocery store", "value": "450.00", "unit": "INR",
    "reference_range": "", "flag": "", "normalised_value": 450.0, "default_checked": True, "saveable": True,
    "confidence": 0.97, "details": {"date": "2026-09-02", "type": "debit"},
}


def _review(profile_id: str, doc_type: str, item: dict[str, Any], document: dict[str, Any], *, save: bool) -> str:
    with profile_context.profile_scope(profile_id):
        review = document_review.create_review(
            doc_type=doc_type, ocr={"pages": [], "name": "photo.jpg"}, document=document,
            items=[dict(item)], rejected=[], model="stub", tier="local", escalation={},
        )
        if save:
            saved = document_review.save_review(review["review_id"], [{"id": item["id"], "action": "confirm"}])
            assert saved["status"] == "ok", saved
    return review["review_id"]


def lab_review(profile_id: str, *, save: bool = True) -> str:
    return _review(profile_id, "lab_report", _LAB_ITEM, {"test_date": "2026-09-01", "lab_name": "City Lab"}, save=save)


def statement_review(profile_id: str, *, save: bool = True) -> str:
    return _review(profile_id, "bank_statement", _TXN_ITEM, {"account": "Savings"}, save=save)


def kriya_task(profile_id: str, *, status: str = "done", answer: str = "Three fares found.") -> str:
    task = kriya_store.create_task(profile_id=profile_id, goal="Search fares from Delhi to Goa")
    if status != "queued":
        kriya_store.update_task(
            task.task_id, profile_id=profile_id, status=status,
            result={"summary": "Finished." if status == "done" else "Stopped.", "answer": answer},
            finished_ts=time.time(),
        )
    return task.task_id


def approve(run_id: str, user_id: str) -> workflow_engine.WorkflowRun:
    with patch("dharma.gate_action", return_value=SimpleNamespace(allowed=True, reasons=[])):
        return workflow_engine.approve_pending_stage(run_id, approved_by=user_id)


def report(run: workflow_engine.WorkflowRun, status: str = "done", *, summary: str = "", fields: dict | None = None,
           receipts: list[dict] | None = None, evidence_ids: list[str] | None = None,
           questions: list[str] | None = None, session_id: str | None = None) -> dict[str, Any]:
    return workflow_engine.submit_stage_result(
        run.run_id, user_id=run.user_id, session_id=session_id or run.session_id or THREAD, status=status,
        summary=summary or f"Stage {run.current_stage_id} result.", fields=fields or {},
        receipts=receipts or [], evidence_ids=evidence_ids or [], questions=questions or [],
    )


# ── Walking a stage with the evidence its done_when asks for ─────────────────


def _first_real(conditions: list[dict[str, Any]]) -> dict[str, Any]:
    """From an ``any`` group, prefer evidence over the person's word."""
    return next((item for item in conditions if item["kind"] != "user_confirmed"), conditions[0])


def satisfy(run: workflow_engine.WorkflowRun) -> workflow_engine.WorkflowRun:
    """Produce real evidence for the current stage's done_when and report it done."""
    pack = workflow_engine.get_pack(run.workflow_id)
    stage = workflow_engine._stage(pack, run.current_stage_id)
    assert stage is not None, "no open stage"
    user = run.user_id
    if stage.get("requires_confirmation") and not workflow_engine._approved_for(run, stage):
        if (run.state.get("confirmation") or {}).get("status") != "pending":
            verdict = report(run, summary=f"Preview: {stage['title']} exactly as it will happen.")
            assert verdict["status"] == "approval_requested", verdict
        run = approve(run.run_id, user)
    receipts: list[dict[str, Any]] = []
    fields: dict[str, Any] = {}
    confirm = False
    conditions = [_first_real(item["of"]) if item["kind"] == "any" else item for item in stage["done_when"]]
    for condition in conditions:
        kind = condition["kind"]
        if kind == "reported":
            fields.update({name: f"{name.replace('_', ' ')} for the test" for name in condition["fields"]})
        elif kind == "tool_succeeded":
            receipts.append(receipt(condition["tools"][0]))
        elif kind == "artifact_exists" and condition["artifact"] == "export":
            return workflow_engine.export_run(run.run_id, user_id=user)
        elif kind == "artifact_exists":
            receipts.append(document_receipt(user))
        elif kind == "records_saved" and condition["table"] == "lab_results":
            receipts.append(receipt("extract_fields", review_id=lab_review(user)))
        elif kind == "records_saved" and condition["table"] == "transactions":
            receipts.append(receipt("extract_fields", review_id=statement_review(user)))
        elif kind == "records_saved" and condition["table"] == "applications":
            workflow_engine.upsert_application(
                run.run_id, user_id=user, company="Example Co", role="Product lead",
                status="applied" if condition.get("status") == "applied" else "shortlisted",
            )
        elif kind == "task_done":
            receipts.append(receipt("start_task", status="task_started", task_id=kriya_task(user)))
        elif kind == "guided":
            return workflow_engine.record_guided_progress(run.run_id, user_id=user, event=condition["event"])
        elif kind == "user_confirmed":
            confirm = True
    if confirm and not fields and not receipts:
        return workflow_engine.confirm_stage(run.run_id, user_id=user)
    verdict = report(run, fields=fields, receipts=receipts)
    assert verdict["status"] == "completed", verdict
    return workflow_engine.get_workflow_run(run.run_id)


def walk(run: workflow_engine.WorkflowRun, until: str | None = None) -> workflow_engine.WorkflowRun:
    """Satisfy stages until the run reaches ``until`` (or completes)."""
    for _ in range(20):
        if run.status == "completed" or run.current_stage_id == until:
            return run
        run = satisfy(run)
    raise AssertionError("the path did not finish")
