"""Lazy, profile-scoped Artemis Android adapter.

Artemis (google/artemis, Apache-2.0) drives an Android phone over local ADB
with its own agent; its admin API listens on loopback. Narad talks to that API
only through this module. Every endpoint below was read from the Artemis
sources (apps/admin_console/routers/*.py and packages/artemis-client at
371aa6d), not guessed:

  GET  /api/status                      scheduler state (queue, active_tasks)
  GET  /api/devices                     connected phones
  POST /api/run                         admit a task (``session_id`` makes it idempotent)
  GET  /api/sessions/{id}               one task's row (status, goal, device)
  POST /api/stop {"session_id"}         cancel one queued or running task
  GET  /api/sessions/{id}/steps         its steps: summary, screenshots, foreground app
  GET  /api/images/{name}               a step screenshot (JPEG)
  GET  /api/sessions/{id}/checks        the Checker's ledger and ``run_outcome``

Phone work runs as a Kriya task (``kriya.phone``): Kriya holds the handle,
polls, shows the steps and the latest screenshot, cancels on the server and
maps the verified-mode result. ``phone_use`` is a thin wrapper that starts
such a task and waits briefly. Artemis reports nothing about FLAG_SECURE, so
Narad never forwards a screenshot to any model and shows none while a
banking or UPI app is in front (see ``kriya.phone``).
"""

from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import quote, urlparse

import requests
from interaction_targets import resolve_interaction_target

from profile_context import current_profile_id, validate_profile_id
from tool_result import envelope, ui_panel

_TERMINAL = frozenset({"completed", "success", "failed", "cancelled", "canceled", "rejected"})
_SUCCESS = frozenset({"completed", "success"})
# Endpoints a server answered 404/405 for: not asked again in this process.
_MISSING: set[str] = set()


class ArtemisAdapterError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _base_url() -> str:
    return os.environ.get("NARAD_ARTEMIS_URL", "").strip().rstrip("/")


def _validate_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ArtemisAdapterError("NARAD_ARTEMIS_URL must be an absolute HTTP(S) URL")
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    remote_allowed = os.environ.get("NARAD_ALLOW_REMOTE_ARTEMIS", "0").lower() in {
        "1", "true", "yes", "on",
    }
    if not loopback and not remote_allowed:
        raise ArtemisAdapterError(
            "Remote Artemis is disabled; keep it on loopback or explicitly enable an authenticated proxy"
        )
    if not loopback and (parsed.scheme != "https" or not os.environ.get("NARAD_ARTEMIS_TOKEN", "")):
        raise ArtemisAdapterError("Remote Artemis requires HTTPS and NARAD_ARTEMIS_TOKEN")
    return value


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "Narad/ArtemisAdapter"}
    token = os.environ.get("NARAD_ARTEMIS_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _send(method: str, path: str, *, payload: dict[str, Any] | None, timeout_s: float) -> requests.Response:
    base_url = _validate_base_url(_base_url())
    try:
        response = requests.request(
            method,
            f"{base_url}{path}",
            json=payload,
            headers=_headers(),
            timeout=max(1.0, timeout_s),
        )
    except requests.RequestException as exc:
        raise ArtemisAdapterError(f"Could not reach Artemis: {exc}") from exc
    if response.status_code >= 400:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        if isinstance(detail, dict):
            detail = detail.get("detail") or detail.get("error") or detail.get("message") or detail
        raise ArtemisAdapterError(
            f"Artemis HTTP {response.status_code}: {str(detail)[:800]}",
            status_code=response.status_code,
        )
    return response


def _request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout_s: float = 10,
) -> Any:
    response = _send(method, path, payload=payload, timeout_s=timeout_s)
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise ArtemisAdapterError("Artemis returned invalid JSON") from exc


def _optional(kind: str, method: str, path: str, **kwargs: Any) -> Any:
    """An endpoint a server may not have: None (and not asked again) on 404/405."""
    if kind in _MISSING:
        return None
    try:
        return _request(method, path, **kwargs)
    except ArtemisAdapterError as exc:
        if exc.status_code in {404, 405}:
            _MISSING.add(kind)
            return None
        raise


def artemis_status(*, include_devices: bool = False) -> dict[str, Any]:
    configured = bool(_base_url())
    if not configured:
        return {
            "available": False,
            "ready": False,
            "configured": False,
            "reason": "Set NARAD_ARTEMIS_URL after starting the optional Artemis sidecar",
            "devices": [],
        }
    try:
        status = _request("GET", "/api/status", timeout_s=2)
        devices: list[dict[str, Any]] = []
        if include_devices:
            device_payload = _request("GET", "/api/devices", timeout_s=5)
            raw = device_payload.get("devices", []) if isinstance(device_payload, dict) else device_payload
            devices = raw if isinstance(raw, list) else []
        return {
            "available": True,
            "ready": True,
            "configured": True,
            "url": _base_url(),
            "reason": None,
            "status": status,
            "devices": devices,
        }
    except ArtemisAdapterError as exc:
        return {
            "available": True,
            "ready": False,
            "configured": True,
            "url": _base_url(),
            "reason": str(exc),
            "devices": [],
        }


# ── The task API (used by kriya.phone) ───────────────────────────────────────


def task_payload(
    *,
    task: str,
    task_id: str,
    device_id: str,
    mode: str,
    app_scope: str,
    verification_level: str,
) -> dict[str, Any]:
    """The /api/run body: ``session_id`` is Narad's handle and makes a retry idempotent."""
    payload: dict[str, Any] = {
        "goal": task,
        "profile": "flash" if mode == "fast" else "pro",
        "session_id": task_id,
        "ingress": "narad",
        "device_serial": device_id,
    }
    if app_scope:
        payload["locked_app_package"] = app_scope
    if mode == "verified":
        payload["verification_level"] = verification_level
    return payload


def start_session(payload: dict[str, Any]) -> dict[str, Any]:
    """Admit a task; returns the admitted task row (raises when Artemis refuses)."""
    admission = _request("POST", "/api/run", payload=payload, timeout_s=30)
    if not isinstance(admission, dict):
        raise ArtemisAdapterError("Artemis did not admit the task")
    tasks = admission.get("tasks")
    if str(admission.get("status") or "").lower() == "rejected" or not tasks or not isinstance(tasks[0], dict):
        raise ArtemisAdapterError(str(admission.get("error") or "Artemis did not admit the task"))
    return dict(tasks[0])


def get_session(session_id: str) -> dict[str, Any] | None:
    """The task's row, or its live queue entry, or None when Artemis has neither."""
    try:
        row = _request("GET", f"/api/sessions/{quote(session_id)}", timeout_s=15)
        if isinstance(row, dict):
            return row
    except ArtemisAdapterError as exc:
        if exc.status_code != 404:
            raise
    return live_task(session_id)


def live_task(session_id: str) -> dict[str, Any] | None:
    """The task in /api/status (queued or holding a device), else None."""
    status = _request("GET", "/api/status", timeout_s=10)
    if not isinstance(status, dict):
        return None
    for key, state in (("active_tasks", "running"), ("queue", "queued")):
        for item in status.get(key) or []:
            if isinstance(item, dict) and session_id in {str(item.get("session_id") or ""),
                                                          str(item.get("task_id") or "")}:
                return {"status": state, **item}
    if str(status.get("session_id") or "") == session_id and status.get("status") in {"running", "paused"}:
        return {"status": "running", "session_id": session_id, "goal": status.get("goal")}
    return None


def stop_session(session_id: str) -> bool:
    """Ask Artemis to stop one task (queued or running). True when it did."""
    answer = _optional("stop", "POST", "/api/stop", payload={"session_id": session_id}, timeout_s=15)
    return isinstance(answer, dict) and str(answer.get("status") or "").lower() == "stopped"


def session_steps(session_id: str) -> list[dict[str, Any]] | None:
    """The task's recorded steps, oldest first (None: the server has no step API)."""
    steps = _optional("steps", "GET", f"/api/sessions/{quote(session_id)}/steps", timeout_s=15)
    if steps is None:
        return None
    return [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []


def session_checks(session_id: str) -> dict[str, Any] | None:
    """The Checker's verdicts and ``run_outcome`` (None: the server has no checks API)."""
    checks = _optional("checks", "GET", f"/api/sessions/{quote(session_id)}/checks", timeout_s=15)
    return checks if isinstance(checks, dict) else None


def session_image(image_name: str) -> bytes | None:
    """One step screenshot as JPEG bytes, kept in memory by the caller."""
    name = str(image_name or "")
    if not name or "/" in name or ".." in name or "images" in _MISSING:
        return None
    try:
        response = _send("GET", f"/api/images/{quote(name)}", payload=None, timeout_s=10)
    except ArtemisAdapterError as exc:
        if exc.status_code == 405:
            _MISSING.add("images")
        return None
    kind = response.headers.get("content-type", "")
    return response.content if response.content and kind.startswith("image/") else None


def is_terminal(status: str) -> bool:
    return str(status or "").lower() in _TERMINAL


def is_success(status: str) -> bool:
    return str(status or "").lower() in _SUCCESS


def resolve_device(owner: str, requested: str) -> tuple[str, dict[str, Any] | None]:
    grant = resolve_interaction_target("artemis", requested, profile_id=owner)
    if grant:
        return str(grant["external_id"]), grant
    implicit = os.environ.get("NARAD_ARTEMIS_DEVICE", "").strip()
    if owner == "default" and implicit and (not requested or requested == implicit):
        return implicit, None
    if requested:
        raise ArtemisAdapterError("This Android device is not granted to the active Narad profile")
    raise ArtemisAdapterError("Connect and grant an Android device to this Narad profile first")


_resolve_device = resolve_device  # the name earlier callers used


# ── phone_use: the avatar tool ───────────────────────────────────────────────


def phone_use(
    task: str,
    device_id: str = "",
    mode: str = "fast",
    app_scope: str = "",
    verification_level: str = "final",
    dry_run: bool = True,
    confirmed: bool = False,
    timeout_s: int = 600,
) -> dict[str, Any]:
    """Preview or start a task on a profile-bound Android phone via Artemis.

    With dry_run=False the task runs as a background Kriya task: the person
    sees a task card with the phone's steps and screen and a Stop button. A
    task that sends, pays, books, buys, deletes, posts, calls, installs, or
    touches a bank, wallet, UPI app, password or OTP waits for their approval
    on that card first and always runs in verified mode; a banking or UPI app
    must also be allowed for that task on the card. ``mode='fast'`` is for
    deterministic, read-oriented tasks only. ``confirmed`` is accepted for
    compatibility and approves nothing. ``timeout_s`` bounds the whole task.
    """
    owner = validate_profile_id(current_profile_id())
    clean_task = " ".join(str(task or "").split())
    if not clean_task:
        return envelope(status="error", summary="task is required", error="invalid_phone_task")
    mode = str(mode or "fast").strip().lower()
    if mode not in {"fast", "verified"}:
        return envelope(status="error", summary="mode must be 'fast' or 'verified'", error="invalid_phone_mode")
    verification_level = str(verification_level or "final").strip().lower()
    if verification_level not in {"off", "final", "checkpoints", "strict"}:
        return envelope(
            status="error",
            summary="verification_level must be off, final, checkpoints, or strict",
            error="invalid_verification_level",
        )
    try:
        resolved_device, grant = resolve_device(owner, device_id)
    except ArtemisAdapterError as exc:
        return envelope(
            status="unavailable",
            summary=str(exc),
            error="android_target_unavailable",
            requires_confirmation=False,
            readiness=artemis_status(include_devices=False),
        )
    from risk_policy import classify_phone_task

    admission = classify_phone_task(clean_task, app_scope)
    high_risk = admission.needs_approval
    preview = {
        "task": clean_task,
        "device_id": resolved_device,
        "mode": mode,
        "app_scope": app_scope or None,
        "verification_level": verification_level if mode == "verified" else None,
        "high_risk": high_risk,
        "reason": admission.verdict.reason,
        "banking_apps": [app["name"] for app in admission.apps],
    }
    if dry_run:  # a preview never touches the device
        label = str((grant or {}).get("label") or resolved_device)
        safety = "Read-oriented task; it runs as a task you can watch and stop."
        if high_risk:
            safety = "Waits for your approval in the Narad app, then runs in verified mode."
        if admission.apps:
            names = ", ".join(app["name"] for app in admission.apps)
            safety += f" {names} is a banking or UPI app: you must allow it for this task on the card."
        return envelope(
            status="preview",
            summary=f"Prepared an Android {mode} task; nothing was executed.",
            ui=ui_panel(
                title="Android task preview",
                summary=clean_task,
                sections=[{"title": "Device", "body": label}, {"title": "Safety", "body": safety}],
                tone="computer-use",
            ),
            provenance={"engine": "artemis", "profile_id": owner},
            requires_confirmation=high_risk,
            planned_task=preview,
            readiness=artemis_status(include_devices=False),
        )
    if high_risk and mode != "verified":
        return envelope(
            status="blocked",
            summary="High-risk Android tasks must use mode='verified'.",
            error="verified_mode_required",
            planned_task=preview,
        )
    from kriya.runtime import runtime
    from kriya.tool import _origin_session_id

    try:
        started = runtime().submit(
            profile_id=owner,
            goal=clean_task,
            surface="phone",
            session_id=_origin_session_id(),
            options={
                "device": resolved_device,
                "app_scope": app_scope,
                "mode": mode,
                "verification_level": verification_level,
                "timeout_s": timeout_s,
            },
        )
    except (ValueError, PermissionError) as exc:
        return envelope(status="error", summary=str(exc), error="phone_task_not_started", planned_task=preview)
    return _wait_briefly(started)


def _wait_briefly(started: Any) -> dict[str, Any]:
    """Wait a few seconds: a quick read finishes here, anything else keeps its card."""
    from kriya import store

    wait_s = float(os.environ.get("NARAD_PHONE_USE_WAIT_S", "20") or 20)
    deadline = time.monotonic() + max(0.0, wait_s)
    task = started
    while time.monotonic() < deadline:
        task = store.get_task(started.task_id, profile_id=started.profile_id)
        if task.status in {"done", "failed", "cancelled", "waiting_approval"}:
            break
        time.sleep(0.25)
    payload = task.to_payload()
    provenance = {"engine": "artemis", "task_id": task.task_id, "profile_id": task.profile_id}
    if task.status in {"done", "failed", "cancelled"}:
        result = task.result or {}
        answer = str(result.get("answer") or "")
        return envelope(
            status="ok" if task.status == "done" else "error",
            summary=" ".join(filter(None, (str(result.get("summary") or task.detail), answer)))[:4_000],
            provenance=provenance,
            requires_confirmation=False,
            task=payload,
            task_id=task.task_id,
            verification=result.get("verification"),
        )
    waiting = task.status == "waiting_approval"
    summary = (
        f"The phone task is waiting for the person's OK on its task card: {task.detail}. Nothing has run on the "
        "phone yet. Tell them in one sentence; do not start it again."
        if waiting else
        "The phone task is running on the phone; the person can watch its steps and stop it on the task card. "
        "Tell them in one sentence; do not start it again."
    )
    return envelope(
        status="task_started",
        summary=summary,
        provenance=provenance,
        requires_confirmation=waiting,
        task=payload,
        task_id=task.task_id,
    )


def _execute_phone_proposal(proposal: Any) -> dict[str, Any]:
    """Phone approvals are decided on their Kriya task now; an older ``phone``
    proposal approved after the upgrade runs nothing."""
    return {
        "status": "error",
        "summary": "Phone tasks now run as tasks with their own approval card; ask Narad again.",
    }


def _register_approvals() -> None:
    import anumati

    anumati.register_executor("phone", _execute_phone_proposal)


_register_approvals()
