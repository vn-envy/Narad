"""The persistent cua-driver MCP session and Kriya's desktop surface.

A stub MCP server (evals/pariksha/cua_driver_stub.py, a tiny Python script
speaking MCP over stdio like ``cua-driver mcp``) stands in for the driver:
one process for many calls, the tool self-check, restart after a crash,
telemetry off, scroll arguments, and a desktop task end to end where every
input step waits for the owner's approval and the driver's Effect contract
and ``verify_state`` decide what the step line says.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]
import narad_paths  # noqa: F401

# isort: split
import computer_use_skill
import cua_session
import interaction_targets
from kriya import runtime as kriya_runtime
from kriya import store
from kriya.desktop import DesktopSurface
from kriya.operator import ScriptedOperator
from kriya.runtime import TaskRuntime

import anumati
import conversation_memory
import family_profiles
import profile_context
import vahana

STUB = _r / "evals" / "pariksha" / "cua_driver_stub.py"
_ALLOWED = SimpleNamespace(allowed=True, reasons=[])


@pytest.fixture
def driver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    log = tmp_path / "cua.log"
    monkeypatch.setenv("CUA_STUB_LOG", str(log))
    monkeypatch.setenv("CUA_DRIVER_RS_TELEMETRY_ENABLED", "true")  # the session must override the host's
    session = cua_session.CuaMcpSession(sys.executable, args=(str(STUB), "mcp"), call_timeout_s=10)
    yield SimpleNamespace(session=session, log=log)
    session.close()


def _log(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _calls(path: Path, tool: str | None = None) -> list[dict[str, Any]]:
    return [row for row in _log(path) if "tool" in row and (tool is None or row["tool"] == tool)]


# ── The session ──────────────────────────────────────────────────────────────


def test_one_process_serves_every_call_with_telemetry_off(driver) -> None:
    session = driver.session
    for _ in range(3):
        cua_session.tool_payload(session.call("list_windows", {"on_screen_only": True}))
    effect = cua_session.tool_payload(session.call(
        "click", {"pid": 4242, "window_id": 7, "element_token": "tok-2", "delivery_mode": "background"}))
    started = [row for row in _log(driver.log) if row.get("started")]
    assert len(started) == 1 and started[0]["argv"] == ["mcp"]
    assert started[0]["telemetry"] == "false" and started[0]["telemetry_compat"] == "false"
    assert effect["effect"] == "confirmed" and effect["delivery"]["mode"] == "background"
    status = session.status()
    assert status["running"] and status["self_check"]["ok"] and status["server"]["version"] == "0.28.4-stub"


def test_the_self_check_refuses_a_driver_whose_tools_changed(driver, monkeypatch) -> None:
    monkeypatch.setenv("CUA_STUB_TOOLS", "missing")
    with pytest.raises(cua_session.CuaContractMismatch, match="verify_state"):
        driver.session.start()
    assert driver.session.status()["self_check"]["missing"] == ["verify_state"]
    assert not _calls(driver.log)  # nothing was sent to the desktop
    monkeypatch.setenv("CUA_STUB_TOOLS", "old_scroll")
    old = cua_session.CuaMcpSession(sys.executable, args=(str(STUB), "mcp"))
    try:
        with pytest.raises(cua_session.CuaContractMismatch) as caught:
            old.call("list_windows", {})
        assert old.check["arguments"] == {"scroll": ["direction", "amount", "by"]}
        assert "scroll lacks direction, amount, by" in str(caught.value)
    finally:
        old.close()


def test_calls_outside_the_table_are_refused_before_sending(driver) -> None:
    with pytest.raises(cua_session.CuaSessionError, match="not in Narad's cua-driver tool table"):
        driver.session.call("clipboard_read", {})
    with pytest.raises(cua_session.CuaSessionError, match="does not take delta_y"):
        driver.session.call("scroll", {"x": 1, "y": 2, "delta_y": 240})
    assert not _calls(driver.log)


def test_a_crashed_driver_is_restarted_and_the_call_is_never_repeated(driver, monkeypatch) -> None:
    monkeypatch.setenv("CUA_STUB_CRASH_ON", "click")
    session = driver.session
    click = {"pid": 4242, "window_id": 7, "element_token": "tok-2", "delivery_mode": "background"}
    with pytest.raises(cua_session.CuaSessionError, match="stopped"):
        session.call("click", click)
    # The next call starts a fresh process; the click that died is not resent by itself.
    assert cua_session.tool_payload(session.call("list_windows", {}))["windows"]
    assert len([row for row in _log(driver.log) if row.get("started")]) == 2
    assert [row["tool"] for row in _calls(driver.log)] == ["click", "list_windows"]


def test_tool_errors_are_reported_not_hidden(driver) -> None:
    with pytest.raises(cua_session.CuaToolError, match="No application named Missing"):
        driver.session.call("launch_app", {"name": "Missing"})
    assert driver.session.status()["running"]  # a tool error is not a transport failure


def test_legacy_desktop_batches_run_over_the_session_with_direction_and_amount(driver, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cua_session, "session", lambda binary=None: driver.session)
    actions = [{"action": "scroll", "x": 100, "y": 200, "delta_y": 360}, {"action": "click", "x": 5, "y": 6}]
    results, shot = computer_use_skill._execute_cua_actions("/unused", actions, tmp_path, "desktop_t")
    assert [row["status"] for row in results] == ["ok", "ok"]
    scroll = _calls(driver.log, "scroll")[0]["arguments"]
    assert {key: scroll[key] for key in ("direction", "amount", "by", "delivery_mode")} == {
        "direction": "down", "amount": 3, "by": "line", "delivery_mode": "foreground"}
    assert "delta_y" not in scroll and scroll["target"] == {"kind": "desktop", "display_id": "primary"}
    assert shot is not None and shot.read_bytes().startswith(b"\x89PNG")
    assert len([row for row in _log(driver.log) if row.get("started")]) == 1


def test_effects_map_to_batch_statuses() -> None:
    assert computer_use_skill._effect_status("confirmed") == "ok"
    assert computer_use_skill._effect_status("refused") == "error"
    for effect in ("partial", "unverifiable", "suspected_noop"):
        assert computer_use_skill._effect_status(effect) == "unverified"


# ── Desktop tasks in Kriya ───────────────────────────────────────────────────


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setattr(profile_context, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(conversation_memory, "THREAD_DIR", tmp_path / "threads")
    monkeypatch.setattr(conversation_memory, "WORKING_MEMORY_DIR", tmp_path / "working")
    monkeypatch.setattr(vahana, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr("dharma.gate_action", lambda *args, **kwargs: _ALLOWED)
    monkeypatch.setattr(kriya_runtime, "_deliver", lambda **kwargs: None)
    monkeypatch.setattr(computer_use_skill, "_COMPUTER_ARTIFACTS_DIR", tmp_path / "computer-use")
    monkeypatch.setattr(family_profiles, "get_profile",
                        lambda user_id: {"user_id": user_id, "is_owner": user_id == "default"})
    monkeypatch.setattr(interaction_targets, "resolve_interaction_target",
                        lambda kind, requested="", *, profile_id=None: {"target_id": "target_host"}
                        if kind == "cua" and profile_id == "default" else None)
    readiness = {"enabled": True, "selected_provider": "cua", "reason": None,
                 "adapters": {"cua": {"ready": True, "reason": None}}}
    monkeypatch.setattr(computer_use_skill, "_desktop_driver_status", lambda: readiness)
    return SimpleNamespace(root=tmp_path)


def _notes_script(context: Any) -> dict[str, Any]:
    history = " ".join(context.history)
    text = context.observation.text
    note = next(ref for ref, node in context.observation.refs.items() if node.name == "Note")
    save = next(ref for ref, node in context.observation.refs.items() if node.name == "Save")
    if "Saved" in text:
        return {"action": "done", "summary": "Added milk to the shopping list.", "answer": "Saved"}
    if "Type" not in history:
        return {"note": "Adding milk", "actions": [{"action": "type", "ref": note, "value": "Milk"}]}
    return {"note": "Saving", "actions": [{"action": "click", "ref": save, "expect": {"text_appears": "Saved"}}]}


def _desktop_runtime(driver) -> TaskRuntime:
    return TaskRuntime(
        operator_factory=lambda task: ScriptedOperator(_notes_script),
        surface_factory=lambda task: DesktopSurface(task_id=task.task_id, profile_id=task.profile_id,
                                                    goal=task.goal, driver=driver.session),
        poll_s=0.02,
    )


def test_desktop_tasks_are_owner_only(home) -> None:
    runtime = TaskRuntime(poll_s=0.02)
    with pytest.raises(PermissionError, match="owner"):
        runtime.submit(profile_id="asha", goal="Add milk to the shopping list", surface="desktop")


def test_a_desktop_task_asks_before_every_input_and_verifies_with_the_driver(home, driver) -> None:
    runtime = _desktop_runtime(driver)
    task = runtime.submit(profile_id="default", goal="Add milk to the shopping list in Notes", surface="desktop")
    assert task.surface == "desktop"
    decided: list[str] = []
    for expected in ("type_text", "click"):  # typing, then the click on Save: each waits for its own OK
        deadline = time.monotonic() + 15
        waiting = store.get_task(task.task_id, profile_id="default")
        while (waiting.status != "waiting_approval" or waiting.proposal_id in decided) \
                and time.monotonic() < deadline:
            time.sleep(0.02)
            waiting = store.get_task(task.task_id, profile_id="default")
        proposal = anumati.get(waiting.proposal_id, profile_id="default")
        assert proposal.surface == "task" and proposal.risk_class == "desktop_input"
        assert proposal.preview["desktop"] and proposal.preview["page_title"].startswith("The Mac: Notes")
        assert proposal.summary.startswith("On the Mac (Notes — Shopping list)")
        assert not _calls(driver.log, expected)  # nothing typed or clicked before the OK
        decided.append(proposal.proposal_id)
        anumati.approve(proposal.proposal_id, profile_id="default", decided_by="default")
    done = runtime.wait(task.task_id, profile_id="default", statuses={"done", "failed"}, timeout_s=15)
    assert done.status == "done", done.result
    [typed] = _calls(driver.log, "type_text")
    assert typed["arguments"] == {"pid": 4242, "window_id": 7, "element_token": "tok-1", "text": "Milk",
                                  "delivery_mode": "background"}
    [click] = _calls(driver.log, "click")
    assert click["arguments"]["element_token"] == "tok-2" and click["arguments"]["delivery_mode"] == "background"
    [verify] = _calls(driver.log, "verify_state")
    assert verify["arguments"]["expect"] == [{"element": {"selector": {"label_contains": "Saved"}, "exists": True}}]
    lines = [event["summary"] for event in store.list_events(task.task_id, profile_id="default")
             if event["kind"] == "step"]
    assert any("driver: confirmed" in line for line in lines)
    assert any("verify_state: satisfied" in line for line in lines)
    assert len([row for row in _log(driver.log) if row.get("started")]) == 1  # one session for the whole task


def test_a_refused_desktop_step_is_reported_and_never_retried(home, driver, monkeypatch) -> None:
    monkeypatch.setenv("CUA_STUB_EFFECT", "refused")
    runtime = _desktop_runtime(driver)
    task = runtime.submit(profile_id="default", goal="Add milk to the shopping list in Notes", surface="desktop")
    waiting = runtime.wait(task.task_id, profile_id="default", statuses={"waiting_approval"}, timeout_s=15)
    anumati.approve(waiting.proposal_id, profile_id="default", decided_by="default")

    def step_lines() -> list[str]:
        return [event["summary"] for event in store.list_events(task.task_id, profile_id="default")
                if event["kind"] == "step"]

    deadline = time.monotonic() + 15
    while not step_lines() and time.monotonic() < deadline:
        time.sleep(0.02)
    runtime.cancel(task.task_id, profile_id="default")
    runtime.wait(task.task_id, profile_id="default", statuses={"cancelled"}, timeout_s=15)
    assert len(_calls(driver.log, "type_text")) == 1  # the approved step ran once and was not retried
    assert any("refused" in line and "permission_required" in line for line in step_lines())


def test_desktop_frames_are_pngs_from_memory(home, driver) -> None:
    surface = DesktopSurface(task_id="tsk_0123456789abcdef", profile_id="default", goal="x", driver=driver.session)
    surface.open("")
    assert surface.url == "desktop://window/4242/7"
    frame = surface.frame()
    assert frame is not None and frame.startswith(b"\x89PNG")
    assert not list(Path(home.root).rglob("*.png"))  # a live frame is never written to disk
    with pytest.raises(PermissionError):
        surface.takeover("click", x=0.5, y=0.5)
    observation = surface.observe()
    refs = {node.name: ref for ref, node in observation.refs.items()}
    again = {node.name: ref for ref, node in surface.observe().refs.items()}
    assert refs == again  # the same controls keep their refs across snapshots
    assert "[w1] window" not in observation.text and "Notes — Shopping list" in observation.text
