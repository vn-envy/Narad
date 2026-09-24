"""Phone tasks in Kriya: Android work that Artemis runs, admitted, approved and
followed by Narad.

Artemis is a stub HTTP server here that speaks the admin API the adapter uses
(the routes of google/artemis apps/admin_console at 371aa6d): /api/status,
/api/run, /api/sessions/{id}, /api/stop, /api/sessions/{id}/steps,
/api/images/{name} and /api/sessions/{id}/checks. A session reveals one step
per poll and then ends as its script says, so the whole path runs for real:
local admission, the approval before dispatch, async progress, frames, cancel,
restart reconciliation, orphans, the banking/UPI denylist and the mapping of
Artemis's verified-mode result.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]
import narad_paths  # noqa: F401

# isort: split
import artemis_adapter
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from kriya import phone as kriya_phone
from kriya import runtime as kriya_runtime
from kriya import store
from kriya.api import build_tasks_router
from kriya.runtime import TaskRuntime

import anumati
import conversation_memory
import profile_context
import risk_policy
import vahana
from profile_context import profile_scope

_ALLOWED = SimpleNamespace(allowed=True, reasons=[])
PHONE_A = {"external_id": "serial-a", "label": "Phone A", "target_id": "target_a", "kind": "artemis"}
PHONE_B = {"external_id": "serial-b", "label": "Phone B", "target_id": "target_b", "kind": "artemis"}


class FakeArtemis:
    """The Artemis admin API with scripted sessions."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sessions: dict[str, dict[str, Any]] = {}
        self.runs: list[dict[str, Any]] = []
        self.stops: list[str] = []
        self.images: list[str] = []
        self.script: dict[str, Any] = {"steps": ["Opened the app", "Read the screen"], "status": "completed"}
        self.locked = False
        self.orphan = False  # rows say running, but nothing holds the phone
        self.forget = False  # no row and no queue entry at all
        self.model = {"provider": "google", "id": "gemini-3.7-flash"}  # what Artemis's agent sends screens to
        self.hold = threading.Event()  # set: sessions stop advancing
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def count(self, path: str) -> int:
        return sum(1 for run in self.runs if run.get("_path") == path)

    def _session_row(self, session_id: str) -> dict[str, Any] | None:
        session = self.sessions.get(session_id)
        if session is None or self.forget:
            return None
        if session["status"] == "queued":
            session["status"] = "running"  # admitted: the first look finds it queued, then running
            return None
        if session["status"] == "running" and not self.hold.is_set():
            if session["revealed"] < len(session["steps"]):
                session["revealed"] += 1
            else:
                session["status"] = session["final"]
        return {"session_id": session_id, "initial_goal": session["goal"], "status": session["status"],
                "device_info": json.dumps({"device_serial": session["device"]}), "pid": 4242,
                **({"output": session["output"]} if session["status"] == "completed" and session.get("output")
                   else {})}

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, code: int, body: Any, kind: str = "application/json") -> None:
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                with fake.lock:
                    if path == "/api/status":
                        queue = [{"session_id": sid, "goal": s["goal"]} for sid, s in fake.sessions.items()
                                 if s["status"] == "queued" and not fake.forget]
                        active = [{"session_id": sid, "device_id": s["device"]} for sid, s in fake.sessions.items()
                                  if s["status"] == "running" and not fake.orphan and not fake.forget]
                        return self._send(200, {"status": "running" if active else "idle", "queue": queue,
                                                "active_tasks": active, "model_info": fake.model})
                    if path.startswith("/api/images/"):
                        name = path.rsplit("/", 1)[1]
                        fake.images.append(name)
                        return self._send(200, b"\xff\xd8" + name.encode(), "image/jpeg")
                    parts = path.strip("/").split("/")
                    if len(parts) >= 3 and parts[:2] == ["api", "sessions"]:
                        session = fake.sessions.get(parts[2])
                        if len(parts) == 3:
                            row = fake._session_row(parts[2])
                            return self._send(200, row) if row else self._send(404, {"detail": "not found"})
                        if session is None:
                            return self._send(404, {"detail": "not found"})
                        if parts[3] == "steps":
                            steps = []
                            for index, step in enumerate(session["steps"][: session["revealed"]]):
                                text, app = step if isinstance(step, tuple) else (step, "com.android.settings")
                                steps.append({"step_number": index + 1, "summary": text,
                                              "pre_image_name": f"pre{index}", "post_image_name": f"img{index}",
                                              "extra_metadata": {"foreground_app": app}})
                            return self._send(200, steps)
                        if parts[3] == "checks":
                            return self._send(200, {"records": [], "streams": [], "run_outcome": session["outcome"]})
                return self._send(404, {"detail": "no route"})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                with fake.lock:
                    if self.path == "/api/run":
                        fake.runs.append({**body, "_path": "/api/run"})
                        if fake.locked:
                            return self._send(409, {"detail": "Android device serial-a is locked."})
                        sid = body["session_id"]
                        if sid not in fake.sessions:
                            fake.sessions[sid] = {
                                "goal": body["goal"], "device": body.get("device_serial"), "status": "queued",
                                "steps": list(fake.script["steps"]), "revealed": 0,
                                "final": fake.script["status"], "outcome": fake.script.get("outcome"),
                                "output": fake.script.get("output"),
                            }
                        return self._send(200, {"status": "queued", "tasks": [{"session_id": sid, "status": "queued"}],
                                                "enqueued_count": 1})
                    if self.path == "/api/stop":
                        sid = str(body.get("session_id") or "")
                        fake.stops.append(sid)
                        session = fake.sessions.get(sid)
                        if session and session["status"] in {"queued", "running"}:
                            session["status"] = "cancelled"
                            return self._send(200, {"status": "stopped", "session_id": sid})
                        return self._send(200, {"status": "no_running_task"})
                return self._send(404, {"detail": "no route"})

        return Handler


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setattr(profile_context, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(conversation_memory, "THREAD_DIR", tmp_path / "threads")
    monkeypatch.setattr(conversation_memory, "WORKING_MEMORY_DIR", tmp_path / "working")
    monkeypatch.setattr(vahana, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr("dharma.gate_action", lambda *args, **kwargs: _ALLOWED)
    monkeypatch.setattr(kriya_runtime, "_deliver", lambda **kwargs: None)
    monkeypatch.setattr(kriya_phone, "POLL_S", 0.02)
    monkeypatch.setattr(kriya_phone, "STEPS_EVERY_S", 0.0)
    monkeypatch.setattr(kriya_phone, "LIVE_CHECK_EVERY_S", 0.0)
    monkeypatch.setattr(kriya_phone, "STOP_WAIT_S", 2.0)
    monkeypatch.setattr(risk_policy, "_denylist_file", lambda: tmp_path / "config" / "phone_app_denylist.json")
    artemis = FakeArtemis()
    monkeypatch.setenv("NARAD_ARTEMIS_URL", artemis.url)
    grants = {"asha": [PHONE_A, PHONE_B], "bob": []}

    def resolve(kind: str, requested: str = "", *, profile_id: str | None = None):
        rows = grants.get(str(profile_id), [])
        if requested:
            return next((row for row in rows if requested in {row["external_id"], row["target_id"]}), None)
        return rows[0] if len(rows) == 1 else None

    monkeypatch.setattr(artemis_adapter, "resolve_interaction_target", resolve)
    artemis_adapter._MISSING.clear()
    yield SimpleNamespace(root=tmp_path, artemis=artemis, grants=grants)
    artemis.close()


def _runtime() -> TaskRuntime:
    return TaskRuntime(poll_s=0.02, workers=4)


def _submit(runtime: TaskRuntime, goal: str, device: str = "serial-a", **options: Any) -> store.Task:
    return runtime.submit(profile_id="asha", goal=goal, surface="phone", options={"device": device, **options})


def _wait(runtime: TaskRuntime, task: store.Task, *statuses: str, timeout_s: float = 10) -> store.Task:
    return runtime.wait(task.task_id, profile_id="asha", statuses=set(statuses), timeout_s=timeout_s)


def _events(task: store.Task) -> list[dict[str, Any]]:
    return store.list_events(task.task_id, profile_id="asha")


def _api() -> TestClient:
    app = FastAPI()

    def identity(request: Request, claimed: str | None) -> str:
        return request.headers["x-test-profile"]

    app.include_router(build_tasks_router(identity))
    return TestClient(app)


# ── Admission, locally ───────────────────────────────────────────────────────


@pytest.mark.parametrize("task, needs, apps", [
    ("Read my latest WhatsApp from Mom", False, []),
    ("Open YouTube and play the news", False, []),
    ("paise bhejo bhaiya ko", True, []),
    ("recharge karo 299 wala plan", True, []),
    ("Jio ka recharge kar do", True, []),
    ("दूध वाले को भुगतान करो", True, []),
    ("पापा को मैसेज भेज दो", True, []),
    ("order karo the atta", True, []),
    ("cancel kar do the cab", True, []),
    ("send the OTP to me", True, []),
    ("Open PhonePe and check the balance", True, ["PhonePe"]),
    ("Check my HDFC balance", True, ["HDFC Bank"]),
    ("Open the settings app", False, []),
])
def test_phone_admission_is_local_and_speaks_hinglish(task: str, needs: bool, apps: list[str], home) -> None:
    admission = risk_policy.classify_phone_task(task)
    assert admission.needs_approval is needs, admission.verdict
    assert [app["name"] for app in admission.apps] == apps


def test_the_denylist_can_only_tighten(home) -> None:
    config = home.root / "config" / "phone_app_denylist.json"
    config.parent.mkdir(parents=True)
    # The owner adds a bank; an entry trying to drop PhonePe cannot remove it.
    config.write_text(json.dumps({"apps": [
        {"package": "com.example.familybank", "names": ["Family Bank"]},
        {"package": "com.phonepe.app", "names": []},
        {"package": "not a package", "names": ["x"]},
    ]}))
    listed = risk_policy.phone_app_denylist()
    assert "com.example.familybank" in listed and listed["com.phonepe.app"][0] == "PhonePe"
    assert "not a package" not in listed
    read = risk_policy.classify_phone_task("Open Family Bank and read the last statement")
    assert read.needs_approval and read.apps[0]["package"] == "com.example.familybank"
    scoped = risk_policy.classify_phone_task("Look at the home screen", app_scope="net.one97.paytm")
    assert scoped.needs_approval and scoped.apps[0]["name"] == "Paytm"
    assert risk_policy.denylisted_package("com.whatsapp") is None


def test_a_phone_task_needs_a_granted_phone_artemis_and_verified_mode(home, monkeypatch) -> None:
    runtime = _runtime()
    with pytest.raises(ValueError, match="grant"):
        runtime.submit(profile_id="bob", goal="Open the settings app", surface="phone")
    with pytest.raises(ValueError, match="verified"):
        _submit(runtime, "Send Ravi a message that I am late", mode="fast")
    with pytest.raises(ValueError, match="cannot be off"):
        _submit(runtime, "Send Ravi a message that I am late", verification_level="off")
    monkeypatch.delenv("NARAD_ARTEMIS_URL")
    with pytest.raises(ValueError, match="not set up"):
        _submit(runtime, "Open the settings app")
    assert home.artemis.runs == []


# ── Running, watching, finishing ─────────────────────────────────────────────


def test_a_read_task_runs_async_with_steps_and_a_live_frame(home) -> None:
    home.artemis.script = {"steps": ["Opened Settings", "Opened Wi-Fi", "Read the network name"],
                           "status": "completed", "output": "Connected to HomeNet"}
    home.artemis.hold.set()
    runtime = _runtime()
    task = _submit(runtime, "Tell me which Wi-Fi the phone is on")
    assert task.status == "queued" and task.to_payload()["device"] == "Phone A"
    deadline = time.monotonic() + 5
    while not home.artemis.sessions and time.monotonic() < deadline:
        time.sleep(0.02)
    [run] = home.artemis.runs
    assert run["profile"] == "flash" and "verification_level" not in run  # fast mode, no approval
    assert run["device_serial"] == "serial-a" and run["ingress"] == "narad"
    assert store.get_task(task.task_id, profile_id="asha").status == "running"
    home.artemis.hold.clear()
    done = _wait(runtime, task, "done")
    assert done.result["answer"] == "Connected to HomeNet"
    assert done.result["verification"] == {"status": "not_requested"}
    lines = [event["summary"] for event in _events(done) if event["kind"] == "step"]
    assert lines == ["Opened Settings", "Opened Wi-Fi", "Read the network name"]
    assert "Sent to Phone A (fast mode)" in [event["summary"] for event in _events(done)]
    assert done.envelope["phone"]["artemis_session"] in home.artemis.sessions


def test_the_live_frame_is_the_latest_screenshot_in_memory_and_no_store(home, monkeypatch) -> None:
    home.artemis.script = {"steps": ["Opened Settings", "Opened Display"], "status": "completed"}
    runtime = _runtime()
    monkeypatch.setattr(kriya_runtime, "_RUNTIME", runtime)
    client = _api()
    task = _submit(runtime, "Open the display settings")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with home.artemis.lock:
            revealed = [session["revealed"] for session in home.artemis.sessions.values()]
            if revealed and revealed[0] >= 2:
                home.artemis.hold.set()  # keep the task running on its second screen
                break
        time.sleep(0.005)
    steps_deadline = time.monotonic() + 5
    while (len([e for e in _events(task) if e["kind"] == "step"]) < 2
           and time.monotonic() < steps_deadline):
        time.sleep(0.02)
    frame = client.get(f"/tasks/{task.task_id}/frame", headers={"x-test-profile": "asha"})
    assert frame.status_code == 200 and frame.content == b"\xff\xd8img1"
    assert frame.headers["content-type"] == "image/jpeg" and "no-store" in frame.headers["cache-control"]
    assert client.get(f"/tasks/{task.task_id}/frame", headers={"x-test-profile": "bob"}).status_code == 404
    tap = client.post(f"/tasks/{task.task_id}/takeover", json={"kind": "click", "x": 0.5, "y": 0.5},
                      headers={"x-test-profile": "asha"})
    assert tap.status_code == 409  # the phone is controlled on the phone
    home.artemis.hold.clear()
    _wait(runtime, task, "done")
    assert not list((home.root / "profiles").rglob("img1*"))  # frames are never written to disk


def test_a_consequential_task_waits_for_approval_before_dispatch(home) -> None:
    home.artemis.script = {
        "steps": ["Opened WhatsApp", "Typed the message", "Sent it"], "status": "completed",
        "outcome": {"task_status": "completed", "tests": {"passed": 2, "failed": 0, "inconclusive": 0,
                                                           "unchecked": 0}},
    }
    runtime = _runtime()
    task = _submit(runtime, "Send Ravi a message on WhatsApp that I am late")
    waiting = _wait(runtime, task, "waiting_approval")
    time.sleep(0.2)
    assert home.artemis.runs == []  # nothing reached the phone yet
    proposal = anumati.get(waiting.proposal_id, profile_id="asha")
    assert proposal.surface == "task" and proposal.preview["kind"] == "phone"
    assert proposal.summary == "On Phone A: Send Ravi a message on WhatsApp that I am late"
    assert proposal.args["mode"] == "verified" and proposal.args["verification_level"] == "final"
    anumati.approve(waiting.proposal_id, profile_id="asha", decided_by="asha")
    done = _wait(runtime, task, "done")
    [run] = home.artemis.runs
    assert run["profile"] == "pro" and run["verification_level"] == "final"
    assert run["goal"] == "Send Ravi a message on WhatsApp that I am late"
    assert done.result["verification"]["status"] == "verified"
    assert "Artemis checked the result" in done.result["summary"]
    finished = anumati.get(waiting.proposal_id, profile_id="asha")
    assert finished.status == "executed" and finished.result["task_id"] == task.task_id


def test_rejecting_stops_before_the_phone_is_touched(home) -> None:
    runtime = _runtime()
    task = _submit(runtime, "Book a cab to the station")
    waiting = _wait(runtime, task, "waiting_approval")
    anumati.reject(waiting.proposal_id, profile_id="asha", decided_by="asha", reason="not now")
    stopped = _wait(runtime, task, "cancelled")
    assert stopped.result["reason"] == "rejected" and "not now" in stopped.result["summary"]
    assert home.artemis.runs == []


@pytest.mark.parametrize("outcome, status, words", [
    ({"task_status": "completed", "tests": {"passed": 1, "failed": 1, "failed_items": [{"text": "Message shows as sent"}]}},
     "failed", "Message shows as sent"),
    ({"task_status": "partial", "tests": {"passed": 1}}, "failed", "only part of it"),
    ({"task_status": "blocked", "tests": {}, "last_findings": ["The app asked for a PIN"]}, "failed", "PIN"),
    ({"task_status": "completed", "tests": {"passed": 0, "inconclusive": 2}}, "done", "could not confirm"),
    (None, "done", "no checks"),
])
def test_artemis_verified_result_decides_the_outcome(home, outcome, status, words) -> None:
    home.artemis.script = {"steps": ["Did it"], "status": "completed", "outcome": outcome}
    runtime = _runtime()
    task = _submit(runtime, "Reply to the school group that we will come")
    anumati.approve(_wait(runtime, task, "waiting_approval").proposal_id, profile_id="asha", decided_by="asha")
    ended = _wait(runtime, task, "done", "failed")
    assert ended.status == status, ended.result
    assert words in ended.result["summary"]
    assert ended.result["verification"]["status"] in {"failed", "partial", "blocked", "unverified"}


def test_phone_screens_go_only_to_a_local_or_trusted_model(home) -> None:
    runtime = _runtime()
    done = _wait(runtime, _submit(runtime, "Open the camera app"), "done")
    ledger = home.root / "profiles" / "asha" / "privacy" / "egress.jsonl"
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert [(row["source"], row["tier"], row["blocked"]) for row in rows] == [("kriya_phone", "trusted", "")]
    assert done.status == "done"
    home.artemis.model = {"provider": "ollama", "id": "gemma4:e4b"}  # local: no ledger line needed
    _wait(runtime, _submit(runtime, "Open the camera app"), "done")
    for model in ({"provider": "someprovider", "id": "some-vlm"}, {}):
        home.artemis.model = model
        refused = _wait(runtime, _submit(runtime, "Open the camera app"), "failed")
        assert refused.result["reason"] == "screen_model", refused.result
    assert len(home.artemis.runs) == 2  # the refused ones never reached the phone
    assert json.loads(ledger.read_text().splitlines()[-1])["blocked"] == "raw_content"


def test_a_failed_artemis_task_and_a_locked_phone_fail_the_task(home) -> None:
    home.artemis.script = {"steps": ["Tried"], "status": "failed"}
    runtime = _runtime()
    failed = _wait(runtime, _submit(runtime, "Open the camera app"), "failed")
    assert "did not finish" in failed.result["summary"]
    home.artemis.locked = True
    locked = _wait(runtime, _submit(runtime, "Open the camera app"), "failed")
    assert "Unlock it" in locked.result["summary"]


# ── Stop, restart, orphans ───────────────────────────────────────────────────


def test_cancel_stops_the_task_on_artemis(home) -> None:
    home.artemis.hold.set()
    runtime = _runtime()
    task = _submit(runtime, "Scroll through the photos")
    deadline = time.monotonic() + 5
    while not home.artemis.sessions and time.monotonic() < deadline:
        time.sleep(0.02)
    session_id = next(iter(home.artemis.sessions))
    runtime.cancel(task.task_id, profile_id="asha")
    stopped = _wait(runtime, task, "cancelled")
    assert home.artemis.stops == [session_id]
    assert home.artemis.sessions[session_id]["status"] == "cancelled"
    assert "Artemis stopped the task" in stopped.result["summary"]


def test_a_restart_reattaches_and_never_dispatches_twice(home) -> None:
    home.artemis.hold.set()
    first = _runtime()
    task = _submit(first, "Read the latest SMS")
    deadline = time.monotonic() + 5
    while not home.artemis.sessions and time.monotonic() < deadline:
        time.sleep(0.02)
    first.suspend()  # the server stops; the phone keeps working
    assert store.get_task(task.task_id, profile_id="asha").status == "running"
    home.artemis.hold.clear()
    second = _runtime()
    assert second.resume_all() == 1
    done = _wait(second, task, "done")
    assert len(home.artemis.runs) == 1  # followed again, not sent again
    assert "resumed" in [event["kind"] for event in _events(done)]


def test_cancel_after_a_restart_still_stops_the_phone(home) -> None:
    home.artemis.hold.set()
    first = _runtime()
    task = _submit(first, "Read the latest SMS")
    deadline = time.monotonic() + 5
    while not home.artemis.sessions and time.monotonic() < deadline:
        time.sleep(0.02)
    first.suspend()
    session_id = store.get_task(task.task_id, profile_id="asha").envelope["phone"]["artemis_session"]
    second = _runtime()  # restarted, not picked up yet
    stopped = second.cancel(task.task_id, profile_id="asha")
    assert stopped.status == "cancelled"
    assert home.artemis.stops == [session_id]  # no orphan left running on Artemis


def test_orphaned_artemis_tasks_are_stopped_and_failed(home, monkeypatch) -> None:
    monkeypatch.setattr(kriya_phone, "ORPHAN_AFTER_S", 0.3)
    home.artemis.hold.set()
    home.artemis.orphan = True  # the row says running; nothing holds the phone
    runtime = _runtime()
    task = _submit(runtime, "Read the latest SMS")
    failed = _wait(runtime, task, "failed")
    assert failed.result["reason"] == "orphaned"
    assert home.artemis.stops == [failed.envelope["phone"]["artemis_session"]]
    home.artemis.orphan, home.artemis.forget = False, True  # Artemis lost it entirely
    gone = _wait(runtime, _submit(runtime, "Read the latest SMS again"), "failed")
    assert "no record" in gone.result["summary"]


def test_one_task_per_phone_and_phones_run_side_by_side(home) -> None:
    home.artemis.hold.set()
    runtime = _runtime()
    first = _submit(runtime, "Read the latest SMS")
    second = _submit(runtime, "Open the calendar app")
    other = _submit(runtime, "Open the clock app", device="serial-b")
    deadline = time.monotonic() + 5
    while len(home.artemis.sessions) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.2)
    devices = sorted(session["device"] for session in home.artemis.sessions.values())
    assert devices == ["serial-a", "serial-b"]  # phone A's second task waits its turn
    assert store.get_task(second.task_id, profile_id="asha").status == "queued"
    assert runtime._lock_key(first) == "phone:serial-a" != runtime._lock_key(other)
    home.artemis.hold.clear()
    for task in (first, second, other):
        _wait(runtime, task, "done")
    assert len(home.artemis.runs) == 3


# ── Banking and UPI apps ─────────────────────────────────────────────────────


def test_a_banking_app_must_be_allowed_for_the_task_on_the_card(home, monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(kriya_runtime, "_RUNTIME", runtime)
    client = _api()
    asha = {"x-test-profile": "asha"}

    # Approving without allowing PhonePe: refused, nothing sent to the phone.
    task = _submit(runtime, "Pay the milk bill on PhonePe")
    waiting = _wait(runtime, task, "waiting_approval")
    proposal = anumati.get(waiting.proposal_id, profile_id="asha")
    assert proposal.preview["blocked_apps"] == [{"package": "com.phonepe.app", "name": "PhonePe"}]
    assert proposal.preview["open_in_task"] and "PhonePe" in proposal.preview["warning"]
    anumati.approve(waiting.proposal_id, profile_id="asha", decided_by="asha")
    refused = _wait(runtime, task, "failed")
    assert refused.result["reason"] == "app_not_allowed" and home.artemis.runs == []

    # Allowed on the task screen: a new proposal (new hash) that names it, then approved.
    home.artemis.script = {"steps": [("Opened PhonePe", "com.phonepe.app"), "Paid"], "status": "completed",
                           "outcome": {"task_status": "completed", "tests": {"passed": 1}}}
    task = _submit(runtime, "Pay the milk bill on PhonePe")
    waiting = _wait(runtime, task, "waiting_approval")
    path = f"/tasks/{task.task_id}/allow-app"
    assert client.post(path, json={"package": "com.whatsapp"}, headers=asha).status_code == 422
    assert client.post(path, json={"package": "com.phonepe.app"}, headers={"x-test-profile": "bob"}).status_code == 404
    allowed = client.post(path, json={"package": "com.phonepe.app"}, headers=asha)
    assert allowed.status_code == 200, allowed.text
    body = allowed.json()
    new = anumati.get(body["proposal_id"], profile_id="asha")
    assert new.proposal_id != waiting.proposal_id and new.args_hash != proposal.args_hash
    assert new.args["allowed_apps"] == ["com.phonepe.app"] and new.preview["blocked_apps"] == []
    assert new.summary.endswith("(allows PhonePe for this task)")
    assert anumati.get(waiting.proposal_id, profile_id="asha").status == "edited"
    assert body["approval"]["id"] == new.proposal_id
    anumati.approve(new.proposal_id, profile_id="asha", decided_by="asha")
    done = _wait(runtime, task, "done")
    assert len(home.artemis.runs) == 1
    assert "You allowed PhonePe for this task" in [event["summary"] for event in _events(done)]
    assert home.artemis.runs[0]["goal"] == "Pay the milk bill on PhonePe"


def test_a_banking_app_on_screen_stops_a_task_that_did_not_allow_it_and_shows_no_frame(home) -> None:
    home.artemis.script = {"steps": ["Opened the home screen", ("Opened GPay", "com.google.android.apps.nbu.paisa.user"),
                                     "Went on"], "status": "completed"}
    runtime = _runtime()
    task = _submit(runtime, "Tidy up the home screen")
    failed = _wait(runtime, task, "failed")
    assert failed.result["reason"] == "app_not_allowed" and "Google Pay" in failed.result["summary"]
    assert home.artemis.stops == [failed.envelope["phone"]["artemis_session"]]
    surface = kriya_phone.PhoneSurface()
    surface.show("img9", "net.one97.paytm")
    assert surface.frame() is None and "img9" not in home.artemis.images  # nothing fetched or shown


def test_push_for_an_app_allowance_opens_the_task_screen(home, anumati_home) -> None:
    runtime = _runtime()
    task = _submit(runtime, "Check my Paytm balance")
    _wait(runtime, task, "waiting_approval")
    [request] = [item for item in anumati_home.notifications if item["kind"] == "approval_request"]
    assert request["data"]["url"] == f"/?task={task.task_id}"
    runtime.cancel(task.task_id, profile_id="asha")
    _wait(runtime, task, "cancelled")


def test_phone_use_is_a_thin_wrapper_over_a_phone_task(home, monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(kriya_runtime, "_RUNTIME", runtime)
    monkeypatch.setenv("NARAD_PHONE_USE_WAIT_S", "10")
    home.artemis.script = {"steps": ["Opened the clock"], "status": "completed", "output": "07:30 alarm is on"}
    with profile_scope("asha"):
        preview = artemis_adapter.phone_use("Check my morning alarm", device_id="serial-a")
        assert preview["status"] == "preview" and home.artemis.runs == []  # a preview never dispatches
        done = artemis_adapter.phone_use("Check my morning alarm", device_id="serial-a", dry_run=False)
        waiting = artemis_adapter.phone_use("Transfer 500 rupees to Ravi on Paytm", device_id="serial-a",
                                            mode="verified", dry_run=False)
    assert done["status"] == "ok" and "07:30 alarm is on" in done["summary"]
    assert waiting["status"] == "task_started" and waiting["task"]["status"] == "waiting_approval"
    assert waiting["task"]["device"] == "Phone A"
    assert len(home.artemis.runs) == 1
    runtime.cancel(waiting["task_id"], profile_id="asha")
    runtime.wait(waiting["task_id"], profile_id="asha", statuses={"cancelled"}, timeout_s=5)


def test_verification_mapping_table() -> None:
    fast = kriya_phone.verification_result({}, None, "fast")
    assert fast == {"status": "not_requested"}
    ok = kriya_phone.verification_result({}, {"run_outcome": {"task_status": "completed",
                                                              "tests": {"passed": 3}}}, "verified")
    assert ok["status"] == "verified" and ok["tests"]["passed"] == 3
    mixed = kriya_phone.verification_result({}, {"run_outcome": {"task_status": "completed",
                                                                 "tests": {"passed": 1, "unchecked": 1}}}, "verified")
    assert mixed["status"] == "unverified"
