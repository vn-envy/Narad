"""Anumati: hash-bound approvals that a person gives on their phone.

Offline throughout: no network, no LLM, no Chromium, no real Gmail. The
isolated browser runs against a scripted page; email "sends" land on a mock.
The root conftest gives every test its own approval store and records the
Vahana notifications instead of delivering them.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split
import artemis_adapter
import avatar_agents
import computer_use_skill
import email_skill
import google_workspace
import interaction_targets
import model_config
import server
import yantra
from fastapi.testclient import TestClient
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

import anumati
import conversation_memory
import cost_ledger
import family_profiles
import profile_context
import risk_policy
import smriti_core
import vahana
import workflow_engine
from profile_context import profile_scope

_ALLOWED = SimpleNamespace(allowed=True, reasons=[])


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Profiles, threads and the inbox under tmp; Gmail and Dharma stubbed."""
    monkeypatch.setattr(profile_context, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(conversation_memory, "THREAD_DIR", tmp_path / "threads")
    monkeypatch.setattr(conversation_memory, "WORKING_MEMORY_DIR", tmp_path / "working")
    monkeypatch.setattr(vahana, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr(email_skill, "_gmail_write_ready", lambda: True)
    monkeypatch.setattr("dharma.gate_action", lambda *args, **kwargs: _ALLOWED)
    return tmp_path


@pytest.fixture
def gmail(monkeypatch: pytest.MonkeyPatch) -> Mock:
    sent = Mock(return_value={"id": "msg-1"})
    monkeypatch.setattr(google_workspace, "api_request", sent)
    return sent


def _karma(home: Path, profile_id: str) -> list[dict]:
    path = home / "profiles" / profile_id / "karma_mutations.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _email(**overrides: Any) -> dict:
    kwargs = {"to": "ravi@example.com", "subject": "Dinner on Friday", "body": "Shall we meet at 8?"}
    return {**kwargs, **overrides}


# ── The model can no longer approve its own side effects ────────────────────


def test_send_email_waits_for_the_person_and_runs_once_when_approved(home, gmail, anumati_home) -> None:
    with profile_scope("asha"):
        waiting = email_skill.send_email(**_email(), dry_run=False)
        again = email_skill.send_email(**_email(), dry_run=False)

    assert waiting["status"] == "needs_approval"
    assert waiting["requires_confirmation"] is True
    gmail.assert_not_called()
    proposal = waiting["approval"]
    assert again["proposal_id"] == proposal["id"]  # the identical request reuses the pending card
    assert proposal["summary"] == 'Send an email to ravi@example.com: "Dinner on Friday"'
    assert proposal["preview"]["kind"] == "email" and proposal["preview"]["body"] == "Shall we meet at 8?"
    assert proposal["risk_class"] == "send" and proposal["editable"] is True
    assert re.fullmatch(r"[0-9a-f]{64}", proposal["args_hash"])

    # Notified once, with the deep link the app opens.
    requests = [item for item in anumati_home.notifications if item["kind"] == "approval_request"]
    assert len(requests) == 1
    assert requests[0]["user_id"] == "asha" and requests[0]["priority"] == "high"
    assert requests[0]["data"] == {
        "proposal_id": proposal["id"],
        "url": f"/?approval={proposal['id']}",
        "surface": "email",
        "risk_class": "send",
        "expires_at": proposal["expires_at"],
    }

    anumati.approve(proposal["id"], profile_id="asha", decided_by="asha", device="Pixel 8")
    done = anumati.execute_approved(proposal["id"], profile_id="asha")
    assert done.status == "executed" and done.result["status"] == "ok"
    assert done.decided_device == "Pixel 8"
    gmail.assert_called_once()

    # Single use: running it again, or the model re-sending it, sends nothing more.
    assert anumati.execute_approved(proposal["id"], profile_id="asha").status == "executed"
    with profile_scope("asha"):
        repeat = email_skill.send_email(**_email(), dry_run=False)
    assert repeat["status"] == "already_done"
    gmail.assert_called_once()
    results = [item for item in anumati_home.notifications if item["kind"] == "approval_result"]
    assert [item["data"]["status"] for item in results] == ["executed"]
    # The Activity inbox pairs a result with its request by proposal_id.
    assert results[0]["data"]["proposal_id"] == proposal["id"]
    assert results[0]["data"]["expires_at"] == proposal["expires_at"]


def test_http_writes_wait_for_the_person_and_reads_do_not(home, monkeypatch: pytest.MonkeyPatch) -> None:
    import http_skill

    sent: list[tuple] = []

    def _send(method, url, headers, body_bytes, timeout_s):
        sent.append((method, url, dict(headers), body_bytes))
        return {"status": "ok", "status_code": 200, "message": "HTTP 200"}

    monkeypatch.setattr(http_skill, "_send", _send)
    monkeypatch.setattr(http_skill, "_check_url", lambda url: None)
    hook = "https://hooks.example.com/services/T1"
    with profile_scope("asha"):
        read = http_skill.http_request("GET", "https://api.example.com/status")
        waiting = http_skill.http_request(
            "POST", hook, headers={"Authorization": "Bearer secret-token"}, body={"text": "Dinner at 8"},
        )
    assert read["status"] == "ok" and sent[0][0] == "GET"
    assert waiting["status"] == "needs_approval" and len(sent) == 1

    proposal = waiting["approval"]
    assert proposal["summary"] == 'POST to hooks.example.com: {"text": "Dinner at 8"}'
    # The phone never sees the API key; the store keeps it because it is what runs.
    assert "secret-token" not in json.dumps(proposal)
    assert proposal["preview"]["headers"]["Authorization"] == "••••"

    anumati.approve(proposal["id"], profile_id="asha", decided_by="asha", device="Pixel 8")
    done = anumati.execute_approved(proposal["id"], profile_id="asha")
    assert done.status == "executed"
    assert sent[-1] == ("POST", hook, {"Authorization": "Bearer secret-token",
                                        "Content-Type": "application/json"}, b'{"text": "Dinner at 8"}')
    with profile_scope("asha"):
        again = http_skill.http_request(
            "POST", hook, headers={"Authorization": "Bearer secret-token"}, body={"text": "Dinner at 8"},
        )
    assert again["status"] == "already_done" and len(sent) == 2


def test_a_changed_argument_is_a_different_proposal(home, gmail) -> None:
    with profile_scope("asha"):
        first = email_skill.send_email(**_email(), dry_run=False)
    anumati.approve(first["proposal_id"], profile_id="asha", decided_by="asha")
    with profile_scope("asha"):
        # Approved but not yet consumed: a different body cannot use it...
        changed = email_skill.send_email(**_email(body="Shall we meet at 9?"), dry_run=False)
        gmail.assert_not_called()
        # ...the exact email can, once.
        exact = email_skill.send_email(**_email(), dry_run=False)

    assert changed["status"] == "needs_approval"
    assert changed["proposal_id"] != first["proposal_id"]
    assert changed["approval"]["args_hash"] != first["approval"]["args_hash"]
    assert exact["status"] == "ok"
    gmail.assert_called_once()
    assert anumati.get(first["proposal_id"], profile_id="asha").status == "executed"
    assert anumati.get(changed["proposal_id"], profile_id="asha").status == "pending"


def test_expired_proposals_are_refused(home, gmail, monkeypatch: pytest.MonkeyPatch) -> None:
    with profile_scope("asha"):
        waiting = email_skill.send_email(**_email(), dry_run=False)
    later = time.time() + 16 * 60
    monkeypatch.setattr(anumati, "_now", lambda: later)

    with pytest.raises(anumati.ProposalClosed, match="expired"):
        anumati.approve(waiting["proposal_id"], profile_id="asha", decided_by="asha")
    assert anumati.get(waiting["proposal_id"], profile_id="asha").status == "expired"
    assert anumati.execute_approved(waiting["proposal_id"], profile_id="asha").status == "expired"
    gmail.assert_not_called()
    assert "approval_expired" in [row["action"] for row in _karma(home, "asha")]


def test_an_approval_that_expires_before_use_cannot_be_consumed(home, gmail, monkeypatch) -> None:
    with profile_scope("asha"):
        waiting = email_skill.send_email(**_email(), dry_run=False)
    anumati.approve(waiting["proposal_id"], profile_id="asha", decided_by="asha")
    later = time.time() + 16 * 60
    monkeypatch.setattr(anumati, "_now", lambda: later)
    with profile_scope("asha"):
        retry = email_skill.send_email(**_email(), dry_run=False)
    assert retry["status"] == "needs_approval" and retry["proposal_id"] != waiting["proposal_id"]
    gmail.assert_not_called()


def test_an_approval_is_consumed_exactly_once_under_concurrency(home) -> None:
    spec = {"surface": "email", "action": "send", "target": "to: ravi@example.com", "args": {"n": 1}}
    proposal, _ = anumati.propose(**spec, summary="Send", risk_class="send", profile_id="asha")
    anumati.approve(proposal.proposal_id, profile_id="asha", decided_by="asha")
    taken: list[Any] = []
    barrier = threading.Barrier(6)

    def _take() -> None:
        barrier.wait()
        taken.append(anumati.consume(**spec, profile_id="asha"))

    threads = [threading.Thread(target=_take) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len([item for item in taken if item is not None]) == 1


def test_the_hash_covers_surface_action_target_and_canonical_args() -> None:
    base = anumati.compute_hash("email", "send", "to: a@example.com", {"b": 1, "a": [1, 2]})
    assert base == anumati.compute_hash("email", "send", "to: a@example.com", {"a": [1, 2], "b": 1})
    for changed in (
        ("browser", "send", "to: a@example.com", {"b": 1, "a": [1, 2]}),
        ("email", "pay", "to: a@example.com", {"b": 1, "a": [1, 2]}),
        ("email", "send", "to: b@example.com", {"b": 1, "a": [1, 2]}),
        ("email", "send", "to: a@example.com", {"b": 1, "a": [2, 1]}),
    ):
        assert anumati.compute_hash(*changed) != base


# ── Karma, the chat thread, and the email edit flow ──────────────────────────


def test_karma_records_every_verdict_with_the_hash(home, gmail) -> None:
    with profile_scope("asha"):
        sent = email_skill.send_email(**_email(), dry_run=False)
        declined = email_skill.send_email(**_email(subject="Lunch"), dry_run=False)

    anumati.approve(sent["proposal_id"], profile_id="asha", decided_by="asha")
    anumati.execute_approved(sent["proposal_id"], profile_id="asha")
    anumati.reject(declined["proposal_id"], profile_id="asha", decided_by="asha", reason="Not today")

    rows = [row for row in _karma(home, "asha") if row.get("entity_type") == "approval"]
    by_proposal: dict[str, list[str]] = {}
    for row in rows:
        by_proposal.setdefault(row["sutra_id"], []).append(row["action"])
        assert row["metadata"]["args_hash"] == anumati.get(row["sutra_id"], profile_id="asha").args_hash
    assert by_proposal[sent["proposal_id"]] == [
        "approval_requested", "approval_approved", "approval_consumed", "approval_executed",
    ]
    assert by_proposal[declined["proposal_id"]] == ["approval_requested", "approval_rejected"]
    verdicts = {row["action"]: row["metadata"].get("verdict") for row in rows}
    assert verdicts["approval_approved"] == "approved" and verdicts["approval_rejected"] == "rejected"


def test_the_originating_thread_gets_a_note(home, gmail) -> None:
    conversation_memory.append_turn(user_id="asha", session_id="thread-1", role="user", text="Email Ravi")
    proposal, _ = anumati.propose(
        surface="email",
        action="send",
        target="to: ravi@example.com",
        args=email_skill._email_args("ravi@example.com", "Dinner", "At 8?", "", ""),
        summary='Send an email to ravi@example.com: "Dinner"',
        risk_class="send",
        profile_id="asha",
        session_id="thread-1",
    )
    anumati.approve(proposal.proposal_id, profile_id="asha", decided_by="asha")
    anumati.execute_approved(proposal.proposal_id, profile_id="asha")

    note = conversation_memory.load_thread("asha", "thread-1")[-1]
    assert note["role"] == "assistant"
    assert note["text"].startswith("[Approval] Approved and done: Send an email to ravi@example.com")
    assert note["metadata"] == {"kind": "approval_result", "proposal_id": proposal.proposal_id, "status": "executed"}


def test_editing_an_email_supersedes_it_with_a_new_hash(home, gmail) -> None:
    with profile_scope("asha"):
        waiting = email_skill.send_email(**_email(), dry_run=False, html_body="<p>Shall we meet at 8?</p>")
    edited = anumati.edit(
        waiting["proposal_id"],
        {"to": "ravi@example.com, meera@example.com", "body": "Shall we meet at 9 instead?"},
        profile_id="asha",
        decided_by="asha",
    )
    old = anumati.get(waiting["proposal_id"], profile_id="asha")

    assert old.status == "edited" and old.superseded_by == edited.proposal_id
    assert edited.supersedes == old.proposal_id and edited.status == "pending"
    assert edited.args_hash != old.args_hash
    assert edited.args["to"] == ["ravi@example.com", "meera@example.com"]
    assert edited.args["html_body"] == ""  # the rendered HTML no longer matched the body
    assert edited.summary == 'Send an email to ravi@example.com, meera@example.com: "Dinner on Friday"'
    with pytest.raises(anumati.ProposalClosed):
        anumati.approve(old.proposal_id, profile_id="asha", decided_by="asha")
    with pytest.raises(ValueError, match="Invalid email"):
        anumati.edit(edited.proposal_id, {"to": "not-an-address"}, profile_id="asha", decided_by="asha")

    anumati.approve(edited.proposal_id, profile_id="asha", decided_by="asha")
    assert anumati.execute_approved(edited.proposal_id, profile_id="asha").status == "executed"
    gmail.assert_called_once()
    raw = gmail.call_args.kwargs["payload"]["raw"]
    import base64
    from email import message_from_bytes

    message = message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    assert message["To"] == "ravi@example.com, meera@example.com"
    assert "9 instead" in message.get_payload()[0].get_payload()


# ── The isolated browser: benign steps run, commits wait ─────────────────────


class _Locator:
    def __init__(self, page: "_Page", ref: str | None) -> None:
        self.page, self.ref = page, ref

    @property
    def first(self) -> "_Locator":
        return self

    async def count(self) -> int:
        return int(self.ref in self.page.elements)

    async def evaluate(self, *_args: Any) -> dict:
        return dict(self.page.elements[self.ref])

    async def click(self, **_kwargs: Any) -> None:
        self.page.clicks.append(self.page.elements[self.ref]["text"])

    async def fill(self, value: str) -> None:
        self.page.filled[self.ref] = value

    async def press(self, key: str) -> None:
        self.page.pressed.append((self.ref, key))


class _Page:
    """A checkout page: a search box, a cookie banner, sign-in, a name field, Pay."""

    def __init__(self, context: SimpleNamespace, url: str) -> None:
        self.context, self.url = context, url
        self.clicks: list[str] = []
        self.filled: dict[str, str] = {}
        self.pressed: list[tuple[str, str]] = []
        self.elements = {
            "e1": {"tag": "input", "type": "search", "text": "", "placeholder": "Search products"},
            "e2": {"tag": "button", "type": "button", "text": "Accept all", "context": "cookie-banner We use cookies"},
            "e3": {"tag": "a", "type": "", "text": "Sign in"},
            "e4": {"tag": "input", "type": "text", "text": "", "label": "Full name"},
            "e5": {"tag": "button", "type": "submit", "text": "Pay ₹6,000"},
            "e6": {"tag": "button", "type": "button", "text": "Confirm", "context": "Your privacy choices: cookies"},
        }
        context.pages.append(self)

    def locator(self, selector: str) -> _Locator:
        match = re.search(r'data-narad-ref="([^"]+)"', selector)
        return _Locator(self, match.group(1) if match else None)

    async def evaluate(self, script: str, *_args: Any) -> Any:
        if "maxElements" in script:
            return {"text": "Checkout", "interactive": [], "fields": []}
        if "activeElement" in script:
            return None
        return "Checkout. Total ₹6,000."

    async def title(self) -> str:
        return "Checkout"

    async def screenshot(self, path: str, **_kwargs: Any) -> None:
        Path(path).write_bytes(b"png")

    async def wait_for_timeout(self, *_args: Any) -> None:
        return None

    @property
    def mouse(self) -> SimpleNamespace:
        async def wheel(*_args: Any) -> None:
            self.scrolled = True

        return SimpleNamespace(wheel=wheel)


@pytest.fixture
def shop(home: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    manager = computer_use_skill.BrowserSessionManager()
    monkeypatch.setattr(manager, "_call", lambda coroutine, timeout_s=0: asyncio.run(coroutine))
    context = SimpleNamespace(pages=[])
    page = _Page(context, "https://shop.example.com/checkout")
    run_dir = home / "browser"
    run_dir.mkdir()
    session = computer_use_skill._BrowserSession(
        session_id="browser_shop", owner_profile_id="asha", context=context, page=page,
        run_dir=run_dir, task="Buy groceries", start_url="",
    )
    manager._sessions[session.session_id] = session
    monkeypatch.setattr(manager, "open", lambda **_kwargs: (session, False))
    monkeypatch.setattr(computer_use_skill, "_BROWSER_MANAGER", manager)
    monkeypatch.setattr(
        computer_use_skill.socket, "getaddrinfo", lambda *_a, **_k: [(2, 1, 6, "", ("93.184.216.34", 0))]
    )
    monkeypatch.setattr(computer_use_skill, "_browser_decision_hint", lambda *_args: None)
    return SimpleNamespace(page=page, session=session)


def _browse(actions: list[dict], **kwargs: Any) -> dict:
    with profile_scope("asha"):
        return computer_use_skill.computer_use(
            "Buy groceries", session_id="browser_shop", actions=actions, dry_run=False, **kwargs
        )


def test_benign_browser_steps_need_no_approval(shop) -> None:
    result = _browse([
        {"action": "fill", "ref": "e1", "value": "basmati rice"},
        {"action": "press", "ref": "e1", "key": "Enter"},  # Enter in a search box
        {"action": "click", "ref": "e2"},  # cookie banner "Accept all"
        {"action": "click", "ref": "e6"},  # "Confirm" inside the consent dialog
        {"action": "click", "ref": "e3"},  # open the sign-in page
        {"action": "fill", "ref": "e4", "value": "Asha Sharma"},  # reversible input
        {"action": "scroll", "delta_y": 400},
    ])

    assert result["status"] == "ok", result["summary"]
    assert shop.page.clicks == ["Accept all", "Confirm", "Sign in"]
    assert shop.page.pressed == [("e1", "Enter")]
    assert anumati.list_proposals(profile_id="asha") == []


def test_the_models_confirmed_flag_no_longer_clicks_pay(shop, anumati_home) -> None:
    waiting = _browse(
        [{"action": "fill", "ref": "e4", "value": "Asha Sharma"}, {"action": "click", "ref": "e5"}],
        confirmed=True,
    )

    assert waiting["status"] == "needs_approval"
    assert shop.page.clicks == []
    assert shop.page.filled == {"e4": "Asha Sharma"}  # the benign prefix ran; the preview shows it
    proposal = waiting["approval"]
    assert proposal["surface"] == "browser" and proposal["risk_class"] == "pay"
    assert proposal["summary"] == 'On shop.example.com: click "Pay ₹6,000"'
    assert proposal["args"]["actions"] == [{"action": "click", "ref": "e5"}]
    assert proposal["args"]["page_url"] == "https://shop.example.com/checkout"
    assert proposal["preview"]["page_title"] == "Checkout"

    anumati.approve(proposal["id"], profile_id="asha", decided_by="asha")
    done = anumati.execute_approved(proposal["id"], profile_id="asha")
    assert done.status == "executed", done.result
    assert shop.page.clicks == ["Pay ₹6,000"]

    # Exactly once: neither the executor nor a model retry pays twice.
    anumati.execute_approved(proposal["id"], profile_id="asha")
    repeat = _browse([{"action": "click", "ref": "e5"}], confirmed=True)
    assert repeat["status"] == "already_done"
    assert shop.page.clicks == ["Pay ₹6,000"]


def test_a_browser_approval_binds_the_exact_batch(shop) -> None:
    waiting = _browse([{"action": "click", "ref": "e5"}])
    anumati.approve(waiting["proposal_id"], profile_id="asha", decided_by="asha")

    widened = _browse([{"action": "click", "ref": "e5"}, {"action": "click", "ref": "e2"}])
    assert widened["status"] == "needs_approval" and widened["proposal_id"] != waiting["proposal_id"]
    assert shop.page.clicks == []

    exact = _browse([{"action": "click", "ref": "e5"}])
    assert exact["status"] == "ok"
    assert shop.page.clicks == ["Pay ₹6,000"]
    assert anumati.get(waiting["proposal_id"], profile_id="asha").status == "executed"


def test_an_approved_browser_action_fails_cleanly_if_the_page_changed(shop) -> None:
    waiting = _browse([{"action": "click", "ref": "e5"}])
    shop.page.url = "https://shop.example.com/cart"
    anumati.approve(waiting["proposal_id"], profile_id="asha", decided_by="asha")
    done = anumati.execute_approved(waiting["proposal_id"], profile_id="asha")

    assert done.status == "failed"
    assert "page changed" in done.result["summary"]
    assert shop.page.clicks == []


def test_an_approved_browser_action_fails_if_its_button_changed(shop) -> None:
    waiting = _browse([{"action": "click", "ref": "e5"}])
    shop.page.elements["e5"]["text"] = "Pay ₹60,000"
    anumati.approve(waiting["proposal_id"], profile_id="asha", decided_by="asha")
    done = anumati.execute_approved(waiting["proposal_id"], profile_id="asha")

    assert done.status == "failed" and "no longer on the page" in done.result["summary"]
    assert shop.page.clicks == []


def test_phone_tasks_with_side_effects_wait_for_approval(home, monkeypatch) -> None:
    grant = {"external_id": "emulator-5554", "label": "Asha's phone"}
    dispatched = Mock(side_effect=artemis_adapter.ArtemisAdapterError("phone offline"))
    monkeypatch.setattr(artemis_adapter, "resolve_interaction_target", lambda *_a, **_k: grant)
    monkeypatch.setattr(artemis_adapter, "artemis_status", lambda **_k: {"ready": True, "available": True})
    monkeypatch.setattr(artemis_adapter, "_request", dispatched)
    with profile_scope("asha"):
        read = artemis_adapter.phone_use("Open YouTube and play the news", dry_run=False)
        waiting = artemis_adapter.phone_use(
            "Pay the milk bill on PhonePe", mode="verified", dry_run=False, confirmed=True
        )

    assert read["status"] == "error"  # read-oriented: dispatched straight away (the phone is "offline")
    assert waiting["status"] == "needs_approval"
    dispatched.assert_called_once()  # only the read task reached Artemis
    assert waiting["approval"]["summary"] == "On Asha's phone: Pay the milk bill on PhonePe"
    assert waiting["approval"]["args"]["mode"] == "verified"


def test_desktop_input_always_waits_even_with_confirmed(home, monkeypatch) -> None:
    readiness = {"available": True, "reason": None, "selected_provider": "pyautogui", "adapters": {}}
    executed = Mock(return_value=([{"action": "type", "status": "ok"}], None))
    monkeypatch.setattr(computer_use_skill, "_desktop_driver_status", lambda: readiness)
    monkeypatch.setattr(computer_use_skill, "_execute_pyautogui_actions", executed)
    monkeypatch.setattr(interaction_targets, "resolve_interaction_target", lambda *_a, **_k: {"target_id": "t1"})
    with profile_scope("asha"):
        waiting = computer_use_skill.computer_use(
            "Type a note", environment="desktop", actions=[{"action": "type", "text": "hi"}],
            dry_run=False, confirmed=True,
        )
    assert waiting["status"] == "needs_approval" and waiting["approval"]["risk_class"] == "desktop_input"
    assert waiting["approval"]["summary"] == 'On the Narad host desktop: type "hi"'
    executed.assert_not_called()


def test_typed_values_are_masked_outside_the_store(home) -> None:
    actions = [{"action": "fill", "ref": "e3", "value": "482913"}, {"action": "click", "ref": "e4"}]
    proposal, _ = anumati.propose(
        surface="browser", action="sensitive_input", target="browser_x @ https://bank.example.com/otp",
        args={"session_id": "browser_x", "page_url": "https://bank.example.com/otp", "actions": actions},
        summary='On bank.example.com: enter "••••" in "OTP"; click "Verify"', risk_class="sensitive_input",
        profile_id="asha",
    )
    assert proposal.to_payload()["args"]["actions"][0]["value"] == "••••"
    assert anumati.get(proposal.proposal_id, profile_id="asha").args["actions"][0]["value"] == "482913"


# ── Risk policy v2 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("label, category", [
    ("Pay ₹6,000", "pay"),
    ("Place order", "pay"),
    ("Continue to pay ₹6,000", "pay"),
    ("भुगतान करें", "pay"),
    ("Pay karo", "pay"),
    ("Book now", "book"),
    ("book karo", "book"),
    ("Buy now", "pay"),
    ("Apply now", "apply"),
    ("Submit application", "apply"),
    ("Send", "send"),
    ("भेजें", "send"),
    ("bhejo", "send"),
    ("Post", "post"),
    ("Delete", "delete"),
    ("Cancel subscription", "delete"),
    ("Save changes", "account"),
    ("Sign up", "account"),
    ("Adopt and Sign", "sign"),
    ("Submit", "submit"),
    ("Confirm", "submit"),
])
def test_commit_labels_need_approval(label: str, category: str) -> None:
    verdict = risk_policy.classify_label(label)
    assert verdict is not None and verdict.needs_approval and verdict.category == category


@pytest.mark.parametrize("label, context, category", [
    ("Search", "", "search"),
    ("खोजें", "", "search"),
    ("Search karo", "", "search"),
    ("Apply filters", "", "filter"),
    ("Sort by price", "", "filter"),
    ("Next", "", "step"),
    ("Proceed to checkout", "", "step"),
    # Opening the payment page is a step; paying on it is the commit.
    ("Continue to payment", "", "step"),
    ("Add to cart", "", "cart"),
    ("Page 3", "", "pagination"),
    ("Load more", "", "pagination"),
    ("Accept all", "", "consent"),
    ("Reject all", "", "consent"),
    ("Confirm my choices", "", "consent"),
    ("Confirm", "We use cookies to improve Narad", "consent"),
    ("Sign in", "", "auth"),
    ("Sign in with Google", "", "auth"),
    ("Cancel", "", "dismiss"),
])
def test_benign_labels_need_no_approval(label: str, context: str, category: str) -> None:
    verdict = risk_policy.classify_label(label, context=context)
    assert verdict is not None and not verdict.needs_approval and verdict.category == category


def test_continuing_to_pay_an_amount_is_the_payment() -> None:
    assert risk_policy.classify_label("Continue to pay ₹6,000").category == "pay"
    assert risk_policy.classify_label("Continue to payment").needs_approval is False


def test_a_consent_banner_never_hides_a_payment() -> None:
    assert risk_policy.classify_label("Pay now", context="cookie consent").category == "pay"
    assert risk_policy.classify_label("Confirm", context="It is necessary to confirm payment").needs_approval


@pytest.mark.parametrize("action, element, needs", [
    ({"action": "press", "key": "Enter"}, {"type": "search"}, False),
    ({"action": "press", "key": "Enter", "target": {"label": "Search"}}, None, False),
    ({"action": "press", "key": "Enter", "target": {"label": "Write a message"}}, None, True),
    ({"action": "press", "key": "Enter"}, None, True),
    ({"action": "press", "key": "Tab"}, None, False),
    ({"action": "fill", "target": {"label": "PIN code"}, "value": "411001"}, None, False),
    ({"action": "fill", "target": {"label": "UPI PIN"}, "value": "1234"}, None, True),
    ({"action": "fill", "target": {"label": "Password"}, "value": "x"}, None, True),
    ({"action": "fill", "ref": "e3"}, {"type": "password"}, True),
    ({"action": "click", "ref": "e9"}, {"type": "submit", "text": "→"}, True),
    ({"action": "click", "ref": "e9"}, {"type": "submit", "text": "→", "in_search_form": True}, False),
    ({"action": "click", "ref": "e9"}, {"tag": "button", "type": "button", "text": ""}, True),  # icon only
    ({"action": "click", "target": {"role": "button", "name": "Menu"}}, {"tag": "button", "text": ""}, False),
    ({"action": "click", "ref": "e9"}, {"tag": "a", "type": "", "text": ""}, False),
    ({"action": "click", "x": 10, "y": 20}, None, True),
    ({"action": "upload", "path": "/tmp/x.pdf"}, None, True),
    ({"action": "navigate", "url": "https://example.com"}, None, False),
    ({"action": "download", "ref": "e2"}, None, False),
])
def test_browser_actions_are_classified(action: dict, element: dict | None, needs: bool) -> None:
    assert risk_policy.classify_browser_action(action, element).needs_approval is needs


def test_instruction_like_pages_make_every_non_read_step_wait() -> None:
    fill = {"action": "fill", "target": {"label": "Name"}, "value": "Asha"}
    assert not risk_policy.classify_browser_action(fill).needs_approval
    assert risk_policy.classify_browser_action(fill, injection=True).category == "injection"
    assert not risk_policy.classify_browser_action({"action": "scroll"}, injection=True).needs_approval


@pytest.mark.parametrize("task, needs", [
    ("Read my latest WhatsApp from Mom", False),
    ("Open YouTube and play the news", False),
    ("Send Ravi a message that I'm late", True),
    ("पापा को मैसेज भेजो", True),
    ("Open Paytm and check the balance", True),
    ("Place an order for milk", True),
])
def test_phone_tasks_are_classified(task: str, needs: bool) -> None:
    assert risk_policy.classify_task(task).needs_approval is needs


# ── Notifications ────────────────────────────────────────────────────────────


def test_vahana_keeps_the_approval_kinds(home) -> None:
    result = vahana.deliver(
        user_id="asha", kind="approval_request", title="Needs your OK: Send", body="Send an email",
        data={"proposal_id": "apr_0123456789abcdef", "url": "/?approval=apr_0123456789abcdef"},
        priority="high",
    )
    [event] = vahana.load_inbox("asha")
    assert result["status"] == "ok"
    assert event["kind"] == "approval_request" and event["priority"] == "high"
    assert event["data"]["url"] == "/?approval=apr_0123456789abcdef"


# ── Deciding from the phone: the HTTP API ────────────────────────────────────


@pytest.fixture
def family(home: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setattr(server, "_AUTH_MODE", "strict")
    monkeypatch.setattr(server, "_login_failures", {})
    monkeypatch.setattr(family_profiles, "FAMILY_PROFILES_PATH", home / "family_profiles.json")
    monkeypatch.setattr(family_profiles, "PROFILE_SESSION_SECRET_PATH", home / "profile_secret")
    family_profiles.update_profile("default", pin="8642")
    family_profiles.create_profile("Asha", "2468")
    family_profiles.create_profile("Bob", "1357")
    client = TestClient(server.app)

    def headers(user_id: str, pin: str) -> dict[str, str]:
        response = client.post("/profiles/login", json={"user_id": user_id, "pin": pin})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['token']}", "User-Agent": "Narad Android"}

    return SimpleNamespace(client=client, asha=headers("asha", "2468"), bob=headers("bob", "1357"))


def test_another_profile_gets_404_and_cannot_decide(family, gmail) -> None:
    with profile_scope("asha"):
        waiting = email_skill.send_email(**_email(), dry_run=False)
    proposal_id = waiting["proposal_id"]
    client = family.client

    for method, path in (
        ("get", f"/approvals/{proposal_id}"),
        ("post", f"/approvals/{proposal_id}/approve"),
        ("post", f"/approvals/{proposal_id}/reject"),
    ):
        assert getattr(client, method)(path, headers=family.bob).status_code == 404, path
    edited = client.post(f"/approvals/{proposal_id}/edit", json={"body": "Hi"}, headers=family.bob)
    assert edited.status_code == 404
    assert client.get("/approvals", headers=family.bob).json() == {"approvals": []}
    assert client.get(f"/approvals/{proposal_id}", headers={"User-Agent": "x"}).status_code == 401
    assert client.get(f"/approvals/{proposal_id}?user_id=asha", headers=family.bob).status_code == 403
    gmail.assert_not_called()

    pending = client.get("/approvals?status=pending", headers=family.asha).json()["approvals"]
    assert [item["id"] for item in pending] == [proposal_id]
    approved = client.post(f"/approvals/{proposal_id}/approve", headers=family.asha)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "executed"
    assert approved.json()["decided_by"] == "asha" and approved.json()["decided_device"] == "Narad Android"
    gmail.assert_called_once()
    again = client.post(f"/approvals/{proposal_id}/approve", headers=family.asha)
    assert again.status_code == 409
    gmail.assert_called_once()


def test_reject_and_edit_over_http(family, gmail) -> None:
    with profile_scope("asha"):
        first = email_skill.send_email(**_email(), dry_run=False)
        second = email_skill.send_email(**_email(subject="Lunch"), dry_run=False)
    client = family.client

    rejected = client.post(
        f"/approvals/{first['proposal_id']}/reject", json={"reason": "Wrong day"}, headers=family.asha
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected" and rejected.json()["decision_reason"] == "Wrong day"

    edited = client.post(
        f"/approvals/{second['proposal_id']}/edit",
        json={"subject": "Lunch on Saturday", "body": "1 pm at the usual place?"},
        headers=family.asha,
    )
    assert edited.status_code == 200, edited.text
    new = edited.json()
    assert new["id"] != second["proposal_id"] and new["supersedes"] == second["proposal_id"]
    assert new["preview"]["subject"] == "Lunch on Saturday" and new["status"] == "pending"
    assert client.get(f"/approvals/{second['proposal_id']}", headers=family.asha).json()["status"] == "edited"
    bad = client.post(f"/approvals/{new['id']}/edit", json={"to": "nobody"}, headers=family.asha)
    assert bad.status_code == 422

    approved = client.post(f"/approvals/{new['id']}/approve", headers=family.asha)
    assert approved.json()["status"] == "executed"
    gmail.assert_called_once()


def test_a_path_step_is_approved_through_its_proposal(family, monkeypatch) -> None:
    monkeypatch.setattr(workflow_engine, "WORKFLOW_DB", profile_context.PROFILES_DIR.parent / "workflows.db")
    monkeypatch.setattr(workflow_engine, "_capability_flags", lambda: {"planning": True, "search": True, "computer": True})
    run = workflow_engine.start_workflow_run(
        "career", user_id="asha",
        inputs={"target_role": "Analyst", "locations": "Pune", "experience": "Five years", "timezone": "Asia/Kolkata"},
    )
    for _stage in ("market_scan", "shortlist", "tailor"):
        run = workflow_engine.complete_current_stage(run.run_id, summary="done")
    run = workflow_engine.request_stage_confirmation(run.run_id, summary="Submit application to Example Co")
    proposal_id = run.state["confirmation"]["proposal_id"]

    card = family.client.get(f"/approvals/{proposal_id}", headers=family.asha).json()
    assert card["surface"] == "workflow" and card["preview"]["stage_title"] == "Application review"
    assert family.client.post(f"/approvals/{proposal_id}/approve", headers=family.bob).status_code == 404
    approved = family.client.post(f"/approvals/{proposal_id}/approve", headers=family.asha)
    assert approved.json()["status"] == "executed", approved.json()
    run = workflow_engine.get_workflow_run(run.run_id)
    assert run.status == "active" and run.state["confirmation"]["status"] == "approved"


# ── The chat stream shows the card ───────────────────────────────────────────


class _Scripted(BaseLlm):
    reply: Any = None

    async def generate_content_async(self, llm_request, stream: bool = False):
        text, calls = self.reply(llm_request)
        parts = [types.Part(text=text)] if text else []
        parts += [types.Part(function_call=types.FunctionCall(name=name, args=args)) for name, args in calls]
        yield LlmResponse(content=types.Content(role="model", parts=parts))


def _after_tool(text: str, calls: list) -> Any:
    def reply(llm_request):
        last = llm_request.contents[-1]
        if any(part.function_response for part in last.parts or []):
            return text, []
        return "", calls

    return reply


def test_the_chat_stream_carries_an_approval_card(home, gmail, monkeypatch) -> None:
    async def _nothing(*_args, **_kwargs):
        return None

    async def _no_recall(*_args, **_kwargs):
        return {"context": ""}

    monkeypatch.setenv("NARAD_SUPERVISOR_RECALL_BUDGET", "0")
    monkeypatch.setenv("NARAD_JEV_ROUTE_MODE", "off")
    monkeypatch.setenv("NARAD_LEARNING_FREEZE", "1")
    monkeypatch.setenv("NARAD_PREROUTER", "off")
    monkeypatch.setattr(yantra, "_TRACE_DIR", home / "traces")
    monkeypatch.setattr(avatar_agents, "_avatar_session_cache", {})
    monkeypatch.setattr(avatar_agents, "_phase_state", {})
    monkeypatch.setattr(avatar_agents, "_avatar_runtime_status", _nothing)
    monkeypatch.setattr(smriti_core, "recall_context", _no_recall)
    monkeypatch.setattr(smriti_core, "capture_episode", lambda **_kwargs: None)
    monkeypatch.setattr(model_config, "get_avatar_model", lambda name: f"scripted-{name.lower()}")
    monkeypatch.setattr(cost_ledger, "record", lambda **_kwargs: {"cost_usd": 0.0})
    for name, stub in {
        "_load_working_state": lambda *_a, **_k: None,
        "_load_thread": lambda *_a, **_k: [],
        "_append_thread_turn": lambda **_k: None,
        "_save_working_state": lambda **_k: None,
        "_summarize_thread": lambda **_k: "",
        "_record_harness_session_state": lambda **_k: None,
        "_build_attachment_bundle": lambda *_a, **_k: {
            "context": "", "attachments": [], "durable_refs": [], "missing": [], "urls": [], "image_data_uris": [],
        },
    }.items():
        monkeypatch.setattr(server, name, stub)

    email = {"to": "ravi@example.com", "subject": "Dinner", "body": "At 8?", "dry_run": False}
    krishna = LlmAgent(
        name="Krishna",
        description="Krishna, a scripted test avatar.",
        model=_Scripted(model="scripted-krishna", reply=_after_tool("It is waiting for your OK.", [("send_email", email)])),
        instruction="You are Krishna.",
        tools=avatar_agents._offload_sync_tools([avatar_agents.FunctionTool(email_skill.send_email)]),
    )
    narad = LlmAgent(
        name="Narad",
        model=_Scripted(model="scripted-narad", reply=_after_tool("", [("invoke_krishna", {"task": "Email Ravi"})])),
        instruction="Route every request.",
        tools=[avatar_agents._make_avatar_tool(krishna, user_id="asha")],
    )
    runner = Runner(agent=narad, app_name="avatara", session_service=InMemorySessionService())
    monkeypatch.setattr(server, "_get_runner_for_user", lambda *_a, **_k: runner)
    session_id = f"thread-{uuid.uuid4().hex[:8]}"

    async def scenario() -> list[dict]:
        queue: asyncio.Queue = asyncio.Queue()
        await server._run_agent_task(server.ChatRequest(query="Email Ravi", user_id="asha"), session_id, queue)
        events = []
        while not queue.empty():
            item = queue.get_nowait()
            if item is not None:
                events.append(json.loads(item))
        return events

    with profile_scope("asha"):
        events = asyncio.run(scenario())

    cards = [event["data"] for event in events if event["type"] == "approval_requested"]
    assert len(cards) == 1, [event["type"] for event in events]
    assert cards[0]["surface"] == "email" and cards[0]["status"] == "pending"
    assert cards[0]["summary"] == 'Send an email to ravi@example.com: "Dinner"'
    assert cards[0]["session_id"] == session_id  # the thread its result will be noted in
    assert not [event for event in events if event["type"] == "tool_ui"]
    gmail.assert_not_called()
