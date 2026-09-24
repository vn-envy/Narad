"""Pariksha browser fixtures in real Chromium: the Kriya loop end to end.

The fixture sites (evals/pariksha/browser_fixtures.py) run on loopback with
server-side oracles; a scripted operator answers with deterministic JSON
actions, so no model is called. Skipped cleanly when no Chromium is found
(set NARAD_CHROMIUM_EXECUTABLE to use a pinned build).

Run with -s to see the scorecard.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]
import narad_paths  # noqa: F401

# isort: split
import computer_use_skill
from kriya import runtime as kriya_runtime
from kriya import store
from kriya.browser import BrowserSurface
from kriya.operator import ScriptedOperator
from kriya.perception import MAX_OBSERVATION_CHARS
from kriya.runtime import TaskRuntime

import anumati
import conversation_memory
import profile_context
import vahana
from profile_context import profile_scope


def _load_fixtures():
    spec = importlib.util.spec_from_file_location(
        "pariksha_browser_fixtures", _r / "evals" / "pariksha" / "browser_fixtures.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fixtures = _load_fixtures()
CHROMIUM = fixtures.find_chromium()
pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="no Chromium for Playwright on this machine")
_ALLOWED = SimpleNamespace(allowed=True, reasons=[])
SCORECARD: list[dict] = []


@pytest.fixture(scope="module")
def site():
    with pytest.MonkeyPatch.context() as patch:
        if CHROMIUM:
            patch.setenv("NARAD_CHROMIUM_EXECUTABLE", CHROMIUM)
        # The fixture sites live on loopback, which the URL policy refuses unless named.
        patch.setenv("NARAD_BROWSER_PRIVATE_HOSTS", "127.0.0.1,localhost")
        running = fixtures.FixtureSite().start()
        yield running
        running.stop()
        computer_use_skill.shutdown_computer_use()
    if SCORECARD:
        print("\nPariksha browser scorecard (scripted operator, real Chromium)")
        for row in SCORECARD:
            print(
                f"  {row['task']:<16} {'PASS' if row['success'] else 'FAIL'} steps={row['steps']:<3} "
                f"s/step={row['seconds_per_step']:<5} obs_max={row['max_observation_chars']} chars "
                f"(~{row['max_observation_tokens']} tokens) approvals={row['approvals_asked']}/"
                f"{row['approvals_needed']} help={row['help_asked']} retries={row['retries']}"
            )


@pytest.fixture
def home(tmp_path, monkeypatch, site):
    monkeypatch.setattr(profile_context, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(conversation_memory, "THREAD_DIR", tmp_path / "threads")
    monkeypatch.setattr(conversation_memory, "WORKING_MEMORY_DIR", tmp_path / "working")
    monkeypatch.setattr(vahana, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr(computer_use_skill, "_COMPUTER_ARTIFACTS_DIR", tmp_path / "computer-use")
    monkeypatch.setattr("dharma.gate_action", lambda *args, **kwargs: _ALLOWED)
    delivered: list[dict] = []
    monkeypatch.setattr(kriya_runtime, "_deliver", lambda **kwargs: delivered.append(kwargs))
    site.oracle.__init__()  # a fresh oracle per test
    return SimpleNamespace(root=tmp_path, delivered=delivered)


def _runtime(script, **kwargs) -> TaskRuntime:
    return TaskRuntime(operator_factory=lambda task: ScriptedOperator(script, **kwargs), poll_s=0.05)


@pytest.mark.parametrize("fixture", fixtures.TASKS, ids=[item.name for item in fixtures.TASKS])
def test_fixture_task(fixture, site, home) -> None:
    runtime = _runtime(fixture.script)
    with profile_scope("asha"):
        row = fixtures.run_fixture_task(runtime, site, fixture, profile_id="asha", timeout_s=90)
    SCORECARD.append(row)
    assert row["success"], row
    assert row["approvals_asked"] == row["approvals_needed"], row
    assert row["help_asked"] == fixture.help_needed, row
    assert 0 < row["max_observation_chars"] <= MAX_OBSERVATION_CHARS


def test_booking_stops_at_the_payment_step_until_approved(site, home) -> None:
    runtime = _runtime(fixtures.flight_script)
    flight = next(item for item in fixtures.TASKS if item.name == "flight")
    task = runtime.submit(profile_id="asha", goal=flight.goal, start_url=site.url("/flights"))
    waiting = runtime.wait(task.task_id, profile_id="asha", statuses={"waiting_approval", "failed"}, timeout_s=60)
    assert waiting.status == "waiting_approval", waiting.detail
    # Searched and filled the passenger details; nothing is paid yet.
    assert len(site.oracle.held) == 1 and site.oracle.payments == []
    proposal = anumati.get(waiting.proposal_id, profile_id="asha")
    assert proposal.risk_class == "pay" and 'click "Pay ₹5,420"' in proposal.summary
    assert proposal.args["page_url"].startswith(site.url("/flights/pay"))
    shots = list((home.root / "computer-use" / "asha" / f"task_{task.task_id}").glob("approval-*.png"))
    assert shots and shots[0].stat().st_size > 1000
    frame = runtime.frame(task.task_id, profile_id="asha")
    assert frame and frame[:2] == b"\xff\xd8"
    time.sleep(0.3)
    assert site.oracle.payments == []  # waiting really waits

    anumati.approve(waiting.proposal_id, profile_id="asha", decided_by="asha")
    done = runtime.wait(task.task_id, profile_id="asha", statuses={"done", "failed", "cancelled"}, timeout_s=60)
    assert done.status == "done", done.detail
    assert len(site.oracle.payments) == 1 and done.result["answer"] == "PNR X7K9QZ"
    assert anumati.get(waiting.proposal_id, profile_id="asha").status == "executed"
    assert home.delivered[-1]["kind"] == "task_done"
    assert runtime.frame(task.task_id, profile_id="asha") is None  # no live view once it is over


def test_injection_page_forces_approval_on_the_first_write(site, home) -> None:
    runtime = _runtime(fixtures.deals_subscribe_script)
    task = runtime.submit(profile_id="asha", goal="Sign up for deal alerts", start_url=site.url("/deals"))
    waiting = runtime.wait(task.task_id, profile_id="asha", statuses={"waiting_approval", "failed", "done"},
                           timeout_s=60)
    proposal = anumati.get(waiting.proposal_id, profile_id="asha")
    assert proposal.risk_class == "injection"
    assert proposal.args["actions"][0]["action"] == "fill"  # even typing into the field waits
    assert proposal.preview["warning"]
    anumati.reject(proposal.proposal_id, profile_id="asha", decided_by="asha")
    stopped = runtime.wait(task.task_id, profile_id="asha", statuses={"cancelled", "failed", "done"}, timeout_s=30)
    assert stopped.status == "cancelled" and site.oracle.subscriptions == []


def test_cancel_stops_within_one_step(site, home) -> None:
    runtime = _runtime(fixtures.slow_script, delay_s=0.3)
    task = runtime.submit(profile_id="asha", goal="Read through the pages", start_url=site.url("/slow/1"))
    deadline = time.monotonic() + 30
    while store.get_task(task.task_id, profile_id="asha").step < 3 and time.monotonic() < deadline:
        time.sleep(0.05)
    started = time.monotonic()
    runtime.cancel(task.task_id, profile_id="asha")
    visits = len(site.oracle.slow_visits)
    stopped = runtime.wait(task.task_id, profile_id="asha", statuses={"cancelled"}, timeout_s=20)
    assert time.monotonic() - started < 5
    assert len(site.oracle.slow_visits) <= visits + 1
    assert stopped.result["reason"] == "cancelled"


def test_dense_page_observation_stays_under_budget(site, home) -> None:
    with profile_scope("asha"):
        surface = BrowserSurface(task_id="tsk_00000000000000d1", profile_id="asha", goal="Look around")
        try:
            surface.open(site.url("/dense"))
            observation = surface.observe()
        finally:
            surface.close()
    assert observation.chars <= MAX_OBSERVATION_CHARS and observation.approx_tokens <= 2000
    assert "more items on screen not shown" in observation.text
    assert observation.find("link", "Product 1 deluxe edition")
    SCORECARD.append({
        "task": "dense_page", "success": True, "steps": 0, "seconds_per_step": 0,
        "max_observation_chars": observation.chars, "max_observation_tokens": observation.approx_tokens,
        "approvals_asked": 0, "approvals_needed": 0, "help_asked": 0, "retries": 0,
    })


def test_sign_in_through_takeover_never_stores_the_password(site, home) -> None:
    runtime = _runtime(fixtures.rewards_script)
    rewards = next(item for item in fixtures.TASKS if item.name == "rewards")
    with profile_scope("asha"):
        row = fixtures.run_fixture_task(runtime, site, rewards, profile_id="asha", timeout_s=60)
    assert row["success"] and row["answer"] == "Points balance: 1,240", row
    assert home.delivered[0]["kind"] == "question"
    assert b"letmein-123" not in (home.root / "profiles" / "asha" / "kriya.db").read_bytes()
    trace = home.root / "computer-use" / "asha" / f"task_{row['task_id']}" / "trace.jsonl"
    assert "letmein-123" not in trace.read_text(encoding="utf-8")
    summaries = [event["summary"] for event in store.list_events(row["task_id"], profile_id="asha")]
    assert "You typed on the page" in summaries and "You tapped Continue" in summaries


def test_private_and_malformed_start_addresses_are_refused(site, home) -> None:
    runtime = _runtime(fixtures.slow_script)
    for url in ("http://10.1.2.3/admin", "file:///etc/passwd", "https://user:pw@example.com/"):
        with pytest.raises(ValueError):
            runtime.submit(profile_id="asha", goal="Open this page", start_url=url)
    assert os.environ["NARAD_BROWSER_PRIVATE_HOSTS"] == "127.0.0.1,localhost"
