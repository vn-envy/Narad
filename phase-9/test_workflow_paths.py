"""Workflow Paths v2: a stage finishes only on evidence the Mac can check.

Each path runs end to end on stubbed tools (real receipts from their return
shapes, reviews confirmed into the profile's own health.db / finance.db, tasks
in the profile's own kriya.db, files in its own artifact folder). The
no-false-completion cases try every shortcut a model or a stale client could
take: free text, a failed tool, another profile's ids, an unconfirmed review,
an old task, a claimed file that is not there.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

_root = next(path for path in Path(__file__).resolve().parents if (path / "narad_paths.py").exists())
sys.path[:0] = [str(_root)]
import path_fixtures as paths

import narad_paths  # noqa: F401

# isort: split
import document_review
import health_skill

import kala_scheduler
import profile_context
import vahana
import workflow_engine
import workflow_evidence
import workflow_tools
from workflow_packs import list_packs

_INPUTS = {
    "career": {"target_role": "Product lead", "locations": "Remote India", "experience": "Eight years in SaaS"},
    "health": {"goal": "More energy", "baseline": "Desk job, late dinners", "dietary_context": "Vegetarian",
               "activity_limits": "Knee pain; walking is fine"},
    "travel": {"origin": "Delhi", "destination": "Goa", "dates": "10-14 December", "travelers": "2 adults",
               "budget": "INR 60,000"},
    "teach": {"topic": "SQL window functions", "outcome": "Use them at work", "current_level": "New"},
    "finance": {"currency": "INR", "goal": "Six-month emergency fund", "monthly_context": "Salary 90k, rent 25k"},
    "documents": {"deliverable": "Presentation", "objective": "Decide the school trip budget",
                  "audience": "Parents' committee"},
}


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    return paths.isolate(tmp_path, monkeypatch)


def _start(workflow_id: str, user_id: str = "asha", **inputs) -> workflow_engine.WorkflowRun:
    return workflow_engine.start_workflow_run(workflow_id, user_id=user_id, inputs={**_INPUTS[workflow_id], **inputs})


def _stage(run: workflow_engine.WorkflowRun) -> str:
    return str(workflow_engine.get_workflow_run(run.run_id).current_stage_id)


# ── Every stage declares a checkable finish line ─────────────────────────────


def test_every_stage_declares_a_valid_done_when_in_plain_words() -> None:
    for pack in list_packs():
        assert pack["version"] == 2
        for stage in pack["stages"]:
            assert workflow_evidence.validate_done_when(stage["done_when"]) == [], (pack["id"], stage["id"])
            for condition in stage["done_when"]:
                assert workflow_evidence.describe(condition)[:1].isupper()
        # Files come from the phone as attachments: no intake field is a Mac path.
        assert not [field for field in pack["intake"] if "path" in field["key"]], pack["id"]
        assert all(field["ask"] for field in pack["intake"] if field["required"])


def test_stage_tools_are_registered_on_their_owner() -> None:
    import avatar_agents

    registered = {
        agent.name: {tool.name for tool in agent.tools}
        for agent in (avatar_agents.matsya, avatar_agents.rama, avatar_agents.krishna, avatar_agents.parashurama)
    }
    for pack in list_packs():
        for stage in pack["stages"]:
            assert set(stage["tools"]) <= registered[stage["owner"]], (pack["id"], stage["id"])
    assert all("report_stage_result" in tools for tools in registered.values())
    assert {"track_application", "extract_fields"} <= registered["Rama"]


# ── No false completions ─────────────────────────────────────────────────────


def test_free_text_and_a_bare_done_report_never_complete_a_stage() -> None:
    run = _start("career")
    run = workflow_engine.record_chat_stage_result(
        run.run_id, user_id="asha", session_id=paths.THREAD,
        response_text="Done! I searched everywhere and found five great roles for you.",
    )
    assert run.current_stage_id == "market_scan"
    verdict = paths.report(run, summary="I searched and found roles.")  # no fields, no search receipt
    assert verdict["status"] == "not_done"
    assert any("live job search" in item for item in verdict["missing"])
    assert _stage(run) == "market_scan"
    with pytest.raises(workflow_engine.StageNotDone):
        workflow_engine.complete_current_stage(run.run_id, summary="All done")


def test_a_failed_tool_is_not_evidence() -> None:
    run = _start("career")
    failed = paths.receipt("exa_search", status="error", summary="Exa is not configured.", error="unconfigured")
    verdict = paths.report(run, fields={"roles": ["Product lead at Example Co"]}, receipts=[failed])
    assert verdict["status"] == "not_done"
    assert any("returned error" in item for item in verdict["missing"])
    # "unconfigured" or a missing status are not successes either.
    assert workflow_evidence.receipt_from_tool("exa_search", {"status": "unconfigured", "summary": "x"})["ok"] is False
    assert workflow_evidence.receipt_from_tool("exa_search", {"results": []}) is None
    assert _stage(run) == "market_scan"


def test_someone_elses_ids_prove_nothing() -> None:
    run = _start("health")
    other_review = paths.lab_review("ravi")  # saved, but in Ravi's own profile
    other_task = paths.kriya_task("ravi")
    other_file = paths.artifact_file("ravi", "plan.docx")
    verdict = paths.report(run, evidence_ids=[other_review, other_task, other_file["url"]])
    assert verdict["status"] == "not_done"
    assert _stage(run) == "labs"
    check = workflow_engine._check(workflow_engine.get_workflow_run(run.run_id), workflow_engine.get_pack("health"),
                                   workflow_engine._stage(workflow_engine.get_pack("health"), "labs"))
    assert check["refs"] == []  # none of them resolved in Asha's own stores

    trip = _start("travel", user_id="asha")
    trip = paths.walk(trip, until="itinerary")
    # A file path outside the artifact folder, or a made-up one, is not a file she made.
    verdict = paths.report(trip, evidence_ids=["/etc/hosts", "runs/asha/nothing/here.docx", other_file["path"]])
    assert verdict["status"] == "not_done"


def test_an_unconfirmed_review_does_not_save_anything_and_does_not_finish_the_stage() -> None:
    run = _start("health")
    pending = paths.lab_review("asha", save=False)
    verdict = paths.report(run, receipts=[paths.receipt("extract_fields", review_id=pending)])
    assert verdict["status"] == "not_done"
    payload = workflow_engine.workflow_run_payload(workflow_engine.get_workflow_run(run.run_id))
    assert payload["next_action"]["kind"] == "review"
    assert payload["next_action"]["url"] == f"/?review={pending}"
    assert payload["evidence"][0]["status"] == "pending"

    # The person confirms on the review screen: the stage settles on its own.
    with profile_context.profile_scope("asha"):
        document_review.save_review(pending, [{"id": "i1", "action": "confirm"}], document={"test_date": "2026-09-01"})
    assert workflow_engine.settle_run(run.run_id).current_stage_id == "safety"


def test_a_task_from_before_the_run_or_still_running_is_not_done() -> None:
    old_task = paths.kriya_task("asha")  # finished before this trip existed
    run = _start("travel")
    running = paths.kriya_task("asha", status="running")
    verdict = paths.report(run, fields={"options": ["Fly 10 Dec"]}, evidence_ids=[old_task],
                           receipts=[paths.receipt("start_task", status="task_started", task_id=running)])
    assert verdict["status"] == "not_done"
    payload = workflow_engine.workflow_run_payload(workflow_engine.get_workflow_run(run.run_id))
    assert payload["next_action"]["kind"] == "wait"
    assert old_task not in [ref["id"] for ref in payload["evidence"]]


def test_the_persons_done_only_finishes_stages_that_accept_it() -> None:
    run = _start("career")
    with pytest.raises(workflow_engine.StageNotDone):
        workflow_engine.confirm_stage(run.run_id, user_id="asha")  # a search is not the person's word
    assert _stage(run) == "market_scan"
    run = paths.walk(run, until="prepare")
    run = workflow_engine.confirm_stage(run.run_id, user_id="asha", note="I feel ready")
    assert run.current_stage_id == "review"


def test_needs_input_asks_at_most_two_questions_and_blocked_says_why() -> None:
    run = _start("finance")
    verdict = paths.report(run, "needs_input", questions=["Which bank?", "Which month?", "Anything else?"])
    assert verdict["status"] == "recorded"
    payload = workflow_engine.workflow_run_payload(workflow_engine.get_workflow_run(run.run_id))
    assert payload["status"] == "waiting_for_user"
    assert payload["next_action"]["kind"] == "answer"
    assert payload["next_action"]["questions"] == ["Which bank?", "Which month?"]

    paths.report(run, "blocked", summary="The statement is password protected.")
    payload = workflow_engine.workflow_run_payload(workflow_engine.get_workflow_run(run.run_id))
    assert payload["next_action"]["kind"] == "blocked"
    assert "password protected" in payload["next_action"]["detail"]


def test_only_optional_stages_can_be_skipped_and_skipped_is_not_done() -> None:
    run = _start("health")
    run = workflow_engine.skip_stage(run.run_id, user_id="asha")
    assert run.current_stage_id == "safety"
    stages = {item["id"]: item for item in workflow_engine.workflow_run_payload(run)["stages"]}
    assert stages["labs"]["status"] == "skipped"
    with pytest.raises(ValueError, match="cannot be skipped"):
        workflow_engine.skip_stage(run.run_id, user_id="asha")


# ── Each path end to end on stubbed tools ────────────────────────────────────


def test_health_path_lab_report_to_reminders_that_fire_for_the_right_person(monkeypatch) -> None:
    run = _start("health")
    rid = paths.lab_review("asha", save=False)
    paths.report(run, "in_progress", receipts=[paths.receipt("extract_fields", review_id=rid)])
    with profile_context.profile_scope("asha"):
        document_review.save_review(rid, [{"id": "i1", "action": "confirm"}], document={"test_date": "2026-09-01"})
    run = workflow_engine.settle_run(run.run_id)
    assert run.current_stage_id == "safety"
    labs = run.state["stage_outputs"]["labs"]
    assert labs["evidence"][0]["id"] == rid and labs["done_when"][0]["met"] is True

    with profile_context.profile_scope("asha"):
        trend = health_skill.get_lab_results("HbA1c")
    assert trend["count"] == 1
    verdict = paths.report(run, fields={"boundaries": "HbA1c above the printed range; see a doctor"},
                           receipts=[paths.receipt("get_lab_results", **trend)])
    assert verdict["status"] == "completed"
    run = paths.satisfy(workflow_engine.get_workflow_run(run.run_id))  # weekly plan
    assert run.current_stage_id == "reminders"

    with profile_context.profile_scope("asha"):
        reminder = health_skill.set_medication_reminder("Metformin", "500mg", "once daily, 8am")
    assert paths.report(run, receipts=[paths.receipt("set_medication_reminder", **reminder)])["status"] == "completed"
    # The reminder lives in Asha's own health.db and Kala sends it to her alone.
    monkeypatch.setattr(kala_scheduler, "_family_profile_ids", lambda: ["asha", "ravi"])
    assert kala_scheduler._fire_due_reminders(datetime(2026, 9, 24, 8, 5), {}) == 1
    assert vahana.load_inbox("asha")[0]["kind"] == "medicine_reminder"
    assert vahana.load_inbox("ravi") == []

    run = paths.walk(workflow_engine.get_workflow_run(run.run_id))
    assert run.status == "completed"
    assert vahana.load_inbox("asha")[0]["kind"] == "task_done"


def test_finance_path_statement_csv_then_budget_and_goal(tmp_path: Path) -> None:
    import finance_skill

    run = _start("finance")
    csv = tmp_path / "hdfc_statement.csv"
    csv.write_text("Date,Narration,Debit Amount,Credit Amount,Closing Balance\n"
                   "02/09/2026,GROCERY STORE,450.00,,10000.00\n03/09/2026,PHARMACY,120.00,,9880.00\n")
    with profile_context.profile_scope("asha"):
        imported = finance_skill.import_csv(str(csv))
    assert imported["imported"] == 2
    # Records saved is an outcome: the import alone settles the stage.
    run = workflow_engine.record_chat_stage_result(
        run.run_id, user_id="asha", session_id=paths.THREAD, receipts=[paths.receipt("import_csv", **imported)],
    )
    assert run.current_stage_id == "analyze"
    assert run.state["stage_outputs"]["ingest"]["done_when"][0]["detail"] == "2 saved."

    run = paths.satisfy(run)  # analyze
    run = paths.satisfy(run)  # scenarios
    assert run.current_stage_id == "plan"
    with profile_context.profile_scope("asha"):
        goal = finance_skill.add_goal("Emergency fund", 540000, "2027-06-30")
    verdict = paths.report(run, fields={"plan": "Save 30k a month"}, receipts=[paths.receipt("add_goal", **goal)])
    assert verdict["status"] == "completed"
    assert paths.walk(workflow_engine.get_workflow_run(run.run_id)).status == "completed"


def test_finance_statement_photo_is_saved_only_after_the_person_confirms() -> None:
    run = _start("finance")
    rid = paths.statement_review("asha", save=False)
    run = workflow_engine.record_chat_stage_result(
        run.run_id, user_id="asha", session_id=paths.THREAD, receipts=[paths.receipt("extract_fields", review_id=rid)],
    )
    assert run.current_stage_id == "ingest"
    with profile_context.profile_scope("asha"):
        document_review.save_review(rid, [{"id": "i1", "action": "confirm"}])
    assert workflow_engine.settle_active_runs(user_id="asha") == 1
    assert _stage(run) == "analyze"


def test_travel_path_live_search_task_booking_approval_and_trip_pack(isolated) -> None:
    run = _start("travel", price_watch=True)
    task_id = paths.kriya_task("asha", status="running")
    verdict = paths.report(run, fields={"options": [{"fare": 8200, "url": "https://example.com/f1"}]},
                           receipts=[paths.receipt("start_task", status="task_started", task_id=task_id)])
    assert verdict["status"] == "not_done"  # the search is still running
    from kriya import store

    store.update_task(task_id, profile_id="asha", status="done", result={"answer": "Three fares", "summary": "Done"})
    run = workflow_engine.settle_run(run.run_id)
    assert run.current_stage_id == "compare"
    assert run.state["stage_outputs"]["research"]["evidence"][0]["url"] == f"/?task={task_id}"

    run = paths.walk(run, until="booking")
    verdict = paths.report(run, summary="Book IndiGo 10 Dec, 2 adults, INR 16,400 total.")
    assert verdict["status"] == "approval_requested"
    run = workflow_engine.get_workflow_run(run.run_id)
    assert run.status == "waiting_confirmation"
    assert workflow_engine.workflow_run_payload(run)["next_action"]["url"].startswith("/?approval=apr_")
    run = paths.approve(run.run_id, "asha")
    booked = paths.kriya_task("asha")
    assert paths.report(run, receipts=[paths.receipt("start_task", status="task_started", task_id=booked)])["status"] == "completed"
    run = workflow_engine.get_workflow_run(run.run_id)
    assert run.current_stage_id == "trip_ready"
    verdict = paths.report(run, receipts=[paths.document_receipt("asha", "trip-pack.html")])
    assert verdict["status"] == "completed"
    run = paths.walk(workflow_engine.get_workflow_run(run.run_id))
    assert run.status == "completed"
    assert [item["stage_id"] for item in run.state["versions"]] == ["itinerary", "trip_ready"]


def test_career_path_tracker_and_read_only_gmail_reply_watch(monkeypatch) -> None:
    run = _start("career")
    run = paths.satisfy(run)  # market scan
    assert run.current_stage_id == "shortlist"
    token = workflow_evidence.bind_turn(run.run_id, user_id="asha", session_id=paths.THREAD)
    try:
        added = workflow_tools.track_application("Example Co", "Product lead", link="https://example.com/jobs/1")
        workflow_tools.track_application("Sample Labs", "Product manager")
    finally:
        workflow_evidence.unbind_turn(token)
    assert added["status"] == "ok"
    assert paths.report(run, fields={"ranking": "Example Co first"})["status"] == "completed"
    run = paths.walk(workflow_engine.get_workflow_run(run.run_id), until="track")
    # Shortlisted is not applied: the tracker must show the application went in.
    assert paths.report(run)["status"] == "not_done"
    workflow_engine.upsert_application(run.run_id, user_id="asha", company="Example Co", role="Product lead",
                                       status="applied", date="2026-09-20")
    assert workflow_engine.settle_run(run.run_id).current_stage_id == "prepare"

    import google_workspace_skill

    calls = []

    def _search(query, max_results=20):
        calls.append(query)
        return {"status": "ok", "messages": [
            {"id": "m1", "from": "Talent <jobs@example.com>", "subject": "Example Co: next steps", "date": "Mon"},
            {"id": "m2", "from": "Newsletter <news@example.org>", "subject": "Weekly digest", "date": "Mon"},
        ]}

    monkeypatch.setattr(google_workspace_skill, "search_google_mail", _search)
    schedule = next(item for item in workflow_engine.list_workflow_schedules(run.run_id)
                    if item.payload["template_id"] == "reply_watch")
    import sqlite3

    for day, expected in ((1, "found"), (2, "nothing_new")):
        due = datetime(2026, 9, 24 + day, 13, 30).astimezone()
        with sqlite3.connect(str(workflow_engine.WORKFLOW_DB)) as con:
            con.execute("UPDATE workflow_schedules SET next_run_at=? WHERE schedule_id=?",
                        (due.isoformat(), schedule.schedule_id))
        with patch("vahana.deliver", return_value={"status": "ok"}) as deliver:
            workflow_engine.fire_due_workflow_schedules(due)
        finding = workflow_engine.workflow_run_payload(workflow_engine.get_workflow_run(run.run_id))["findings"][0]
        assert finding["status"] == expected
        assert (deliver.call_count == 1) is (expected == "found")
    assert '"Example Co"' in calls[0] and "-from:me" in calls[0]
    assert finding["kind"] == "reply_check"
    after = workflow_engine.get_workflow_run(run.run_id)
    assert after.current_stage_id == "prepare"  # a watch never moves the run
    tracker = workflow_engine.workflow_run_payload(after)["applications"]
    assert next(item for item in tracker if item["company"] == "Example Co")["reply_seen"]["subject"].startswith("Example")


def test_teach_path_guided_loop_still_completes_every_stage() -> None:
    run = _start("teach")
    run = workflow_engine.record_guided_progress(run.run_id, user_id="asha", event="answer", details={"correct": True})
    assert run.current_stage_id == "lesson"
    # An answer mid-lesson is progress, not the end of the lesson.
    run = workflow_engine.record_guided_progress(run.run_id, user_id="asha", event="answer")
    assert run.current_stage_id == "lesson"
    for _ in range(4):
        run = workflow_engine.record_guided_progress(run.run_id, user_id="asha", event="complete")
    assert run.status == "completed"


def test_documents_path_versions_and_export() -> None:
    run = _start("documents")
    run = paths.walk(run, until="create")
    first = paths.document_receipt("asha", "deck-v1.html")
    assert paths.report(run, receipts=[first])["status"] == "completed"
    run = workflow_engine.record_workflow_feedback(run.run_id, "revision_requested")
    run = paths.walk(run, until="create")
    second = paths.document_receipt("asha", "deck-v2.html")
    assert paths.report(run, receipts=[second])["status"] == "completed"
    run = paths.walk(workflow_engine.get_workflow_run(run.run_id), until="export")
    assert [item["version"] for item in run.state["versions"]] == [1, 2]
    assert workflow_engine.workflow_run_payload(run)["next_action"]["kind"] == "export"
    run = workflow_engine.export_run(run.run_id, user_id="asha")
    assert run.status == "completed"
    exported = run.state["stage_outputs"]["export"]["evidence"][0]
    assert exported["type"] == "export" and exported["url"].startswith("/media/runs/asha/")
    import zipfile

    bundle = Path(workflow_evidence.ARTIFACTS_DIR) / exported["id"]
    with zipfile.ZipFile(bundle) as archive:
        assert sorted(archive.namelist()) == ["deck-v2.html", "provenance.md"]
        assert "Version 2" in archive.read("provenance.md").decode()


# ── Mobile-first intake ──────────────────────────────────────────────────────


def test_a_chat_start_opens_on_intake_prefilled_and_asks_two_questions_at_a_time() -> None:
    vahana.update_preferences("asha", {"timezone": "Asia/Kolkata"})
    earlier = _start("travel", travelers="2 adults and a child")
    workflow_engine.set_workflow_status(earlier.run_id, "cancelled")
    finished = workflow_engine.start_workflow_run("travel", user_id="asha", inputs={
        **_INPUTS["travel"], "travelers": "Me and my sister", "origin": "Jaipur",
    })
    run = workflow_engine.start_workflow_run("travel", user_id="asha", inputs={"destination": "Kerala"},
                                             session_id="thread-kerala", partial=True)
    assert finished.run_id != run.run_id
    assert run.current_stage_id == "intake" and run.status == "waiting_for_user"
    assert run.session_id == "thread-kerala"
    # Carried from her last (not cancelled) trip and her phone's time zone.
    assert run.inputs["origin"] == "Jaipur" and run.inputs["travelers"] == "Me and my sister"
    assert run.inputs["timezone"] == "Asia/Kolkata"
    payload = workflow_engine.workflow_run_payload(run)
    assert [item["key"] for item in payload["intake_questions"]] == ["dates", "budget"]
    assert payload["next_action"]["kind"] == "answer" and len(payload["next_action"]["questions"]) == 2
    assert workflow_engine.list_workflow_schedules(run.run_id) == []  # the rhythm waits for the details

    context = workflow_engine.build_workflow_context(run.run_id, user_id="asha", session_id="thread-kerala")
    assert "dates: Which dates" in context and "never ask for a file path" in context

    verdict = paths.report(run, "needs_input", fields={"dates": "20-24 January"}, questions=["What is the budget?"])
    assert verdict["status"] == "recorded"
    run = workflow_engine.update_workflow_inputs(run.run_id, {"budget": "INR 80,000"})
    assert run.current_stage_id == "research"
    assert run.status == "active"


def test_intake_reopened_by_feedback_needs_a_fresh_look() -> None:
    run = _start("finance")
    run = workflow_engine.record_workflow_feedback(run.run_id, "goal_changed")
    run = workflow_engine.settle_run(run.run_id)
    assert run.current_stage_id == "intake"  # old answers do not re-close it
    run = workflow_engine.update_workflow_inputs(run.run_id, {"goal": "Pay off the car loan first"})
    assert run.current_stage_id == "ingest"


# ── The avatar tool ──────────────────────────────────────────────────────────


def test_report_stage_result_works_only_inside_a_bound_turn() -> None:
    outside = workflow_tools.report_stage_result("done", "All finished")
    assert outside["status"] == "error" and outside["error"] == "no_bound_path"

    run = _start("career")
    token = workflow_evidence.bind_turn(run.run_id, user_id="asha", session_id=paths.THREAD)
    try:
        workflow_evidence.record_tool_result("exa_search", {"status": "ok", "summary": "5 results"})
        workflow_evidence.record_tool_result("report_stage_result", {"status": "completed", "summary": "x"})
        turn = workflow_evidence.current_turn()
        assert [item["tool"] for item in turn.receipts] == ["exa_search"]
        result = workflow_tools.report_stage_result("done", "Five roles", fields={"roles": ["A", "B"]})
        assert turn.reported is True
    finally:
        workflow_evidence.unbind_turn(token)
    assert result["status"] == "completed"
    assert result["next_stage"]["id"] == "shortlist"
    assert "Next: Ranked shortlist" in result["summary"]
