"""Lazy, profile-scoped Artemis Android task adapter."""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import requests
from interaction_targets import operation_lock, resolve_interaction_target

from narad_config import ARTIFACTS_DIR
from profile_context import current_profile_id, validate_profile_id
from risk_policy import COMMIT, Verdict, classify_task
from tool_result import artifact, envelope, ui_panel

_TERMINAL = frozenset({"completed", "success", "failed", "cancelled", "canceled", "rejected"})
_SUCCESS = frozenset({"completed", "success"})


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


def _request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout_s: float = 10,
) -> Any:
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
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise ArtemisAdapterError("Artemis returned invalid JSON") from exc


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


def _resolve_device(owner: str, requested: str) -> tuple[str, dict[str, Any] | None]:
    grant = resolve_interaction_target("artemis", requested, profile_id=owner)
    if grant:
        return str(grant["external_id"]), grant
    implicit = os.environ.get("NARAD_ARTEMIS_DEVICE", "").strip()
    if owner == "default" and implicit and (not requested or requested == implicit):
        return implicit, None
    if requested:
        raise ArtemisAdapterError("This Android device is not granted to the active Narad profile")
    raise ArtemisAdapterError("Connect and grant an Android device to this Narad profile first")


def _task_payload(
    *,
    task: str,
    task_id: str,
    device_id: str,
    mode: str,
    app_scope: str,
    verification_level: str,
) -> dict[str, Any]:
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


def _android_admission_hint(task: str, mode: str, app_scope: str) -> tuple[dict[str, Any] | None, bool]:
    decision_mode = os.environ.get("NARAD_JEV_PHONE_MODE", "shadow").strip().lower()
    if decision_mode == "off":
        return None, False
    try:
        from decision_contracts import android_admission_v1, compact_decision
        from decision_engine import jev_status

        if not jev_status().get("available"):
            return None, False
        result = android_admission_v1({
            "task": task[:1600],
            "requested_mode": mode,
            "app_scope": app_scope[:300] if app_scope else None,
        })
        hint = compact_decision(result)
        hint["mode"] = decision_mode
        risk = result.answers.get("risk")
        human = result.answers.get("needs_human")
        stricter_gate = bool(
            decision_mode == "active"
            and (
                risk is not None
                and risk.confidence >= 0.80
                and risk.value in {"external_side_effect", "sensitive"}
                or human is not None
                and human.confidence >= 0.80
                and bool(human.value)
            )
        )
        return hint, stricter_gate
    except Exception as exc:
        return {
            "decision_id": "android_admission_v1",
            "status": "error",
            "mode": decision_mode,
            "error": f"{type(exc).__name__}: {exc}"[:300],
        }, False


def _android_verification_hint(task: str, result: dict[str, Any]) -> dict[str, Any] | None:
    decision_mode = os.environ.get("NARAD_JEV_PHONE_MODE", "shadow").strip().lower()
    if decision_mode == "off":
        return None
    try:
        from decision_contracts import android_verify_v1, compact_decision
        from decision_engine import jev_status

        if not jev_status().get("available"):
            return None
        decision = android_verify_v1({"task": task[:1600], "artemis_result": result})
        hint = compact_decision(decision)
        hint["mode"] = decision_mode
        return hint
    except Exception as exc:
        return {
            "decision_id": "android_verify_v1",
            "status": "error",
            "mode": decision_mode,
            "error": f"{type(exc).__name__}: {exc}"[:300],
        }


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
    """Preview or execute a task on a profile-bound Android device via Artemis.

    Use ``mode='fast'`` only for deterministic, read-oriented tasks. Use
    ``mode='verified'`` for multi-app work, diagnostics, or anything requiring
    checkpoints. A task that sends, pays, books, buys, deletes, posts, calls,
    installs, or touches a bank, wallet, UPI app, password or OTP returns
    status "needs_approval" with dry_run=False: the person approves this exact
    task on an approval card and Narad dispatches it then. High-risk work is
    never dispatched through fast mode. ``confirmed`` is accepted for
    compatibility and approves nothing.
    """
    arguments = dict(
        task=task, device_id=device_id, mode=mode, app_scope=app_scope,
        verification_level=verification_level, dry_run=dry_run,
        confirmed=confirmed, timeout_s=timeout_s,
    )
    if dry_run:  # a preview never touches the device
        return _phone_use(**arguments)
    try:
        device_key, _ = _resolve_device(validate_profile_id(current_profile_id()), device_id)
    except ArtemisAdapterError:
        device_key = device_id
    # Parallel tool calls must not interleave two tasks' taps on one phone.
    with operation_lock(f"android:{device_key}"):
        return _phone_use(**arguments)


def _phone_use(
    *,
    task: str,
    device_id: str,
    mode: str,
    app_scope: str,
    verification_level: str,
    dry_run: bool,
    confirmed: bool,
    timeout_s: int,
) -> dict[str, Any]:
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
        resolved_device, grant = _resolve_device(owner, device_id)
    except ArtemisAdapterError as exc:
        return envelope(
            status="unavailable",
            summary=str(exc),
            error="android_target_unavailable",
            requires_confirmation=False,
            readiness=artemis_status(include_devices=False),
        )
    readiness = artemis_status(include_devices=False)
    verdict = classify_task(clean_task)
    decision_hint, stricter_jev_gate = _android_admission_hint(clean_task, mode, app_scope)
    if stricter_jev_gate and not verdict.needs_approval:
        verdict = Verdict(COMMIT, "sensitive", "The admission check judged this task consequential")
    high_risk = verdict.needs_approval
    preview = {
        "task": clean_task,
        "device_id": resolved_device,
        "mode": mode,
        "app_scope": app_scope or None,
        "verification_level": verification_level if mode == "verified" else None,
        "high_risk": high_risk,
        "decision_hint": decision_hint,
    }
    if dry_run:
        return envelope(
            status="preview",
            summary=f"Prepared an Android {mode} task; nothing was executed.",
            ui=ui_panel(
                title="Android task preview",
                summary=clean_task,
                sections=[
                    {"title": "Device", "body": str((grant or {}).get("label") or resolved_device)},
                    {"title": "Safety", "body": "Waits for approval in the Narad app." if high_risk else "Read-oriented task; execution is still observable and stoppable."},
                ],
                tone="computer-use",
            ),
            provenance={"engine": "artemis", "profile_id": owner, "decision_hint": decision_hint},
            requires_confirmation=high_risk,
            planned_task=preview,
            readiness=readiness,
        )
    if not readiness.get("ready"):
        return envelope(
            status="unavailable",
            summary=str(readiness.get("reason") or "Artemis is unavailable"),
            error="artemis_unavailable",
            readiness=readiness,
        )
    if high_risk and mode != "verified":
        return envelope(
            status="blocked",
            summary="High-risk Android tasks must use mode='verified'.",
            error="verified_mode_required",
            planned_task=preview,
        )
    consumed = None
    if high_risk:
        # `confirmed` approves nothing: the person approves this exact task.
        import anumati

        gate = anumati.require(
            **_phone_approval_spec(
                clean_task, resolved_device, grant, mode, app_scope, verification_level, verdict
            ),
            profile_id=owner,
        )
        if gate.status == "needs_approval":
            return anumati.needs_approval_result(
                gate.proposal,
                planned_task=preview,
                provenance={"engine": "artemis", "profile_id": owner, "decision_hint": decision_hint},
            )
        if gate.status == "already_executed":
            return anumati.already_executed_result(gate.proposal, planned_task=preview)
        consumed = gate.proposal
    result = _dispatch_phone_task(
        owner=owner,
        task=clean_task,
        device_id=resolved_device,
        grant=grant,
        mode=mode,
        app_scope=app_scope,
        verification_level=verification_level,
        timeout_s=timeout_s,
        high_risk=high_risk,
        decision_hint=decision_hint,
    )
    if consumed is not None:
        import anumati

        anumati.record_result(consumed.proposal_id, result, profile_id=owner)
    return result


def _phone_approval_spec(
    task: str,
    device_id: str,
    grant: dict[str, Any] | None,
    mode: str,
    app_scope: str,
    verification_level: str,
    verdict: Verdict,
) -> dict[str, Any]:
    """The hash-bound part of a phone task: the exact goal, device and mode."""
    label = str((grant or {}).get("label") or device_id)
    return {
        "surface": "phone",
        "action": verdict.category,
        "target": f"{label} ({device_id})",
        "args": {
            "task": task,
            "device_id": device_id,
            "mode": mode,
            "app_scope": app_scope or "",
            "verification_level": verification_level,
        },
        "summary": f"On {label}: {task}",
        "risk_class": verdict.category,
        "preview": {
            "kind": "phone",
            "device": label,
            "mode": mode,
            "app_scope": app_scope or None,
            "reason": verdict.reason,
        },
    }


def _execute_phone_proposal(proposal: Any) -> dict[str, Any]:
    """Dispatch an approved phone task, if the device is still granted and reachable."""
    owner = proposal.profile_id
    args = proposal.args
    with operation_lock(f"android:{args['device_id']}"):
        try:
            resolved_device, grant = _resolve_device(owner, str(args["device_id"]))
        except ArtemisAdapterError as exc:
            return {"status": "error", "summary": str(exc)}
        readiness = artemis_status(include_devices=False)
        if not readiness.get("ready"):
            return {"status": "unavailable", "summary": str(readiness.get("reason") or "Artemis is unavailable")}
        return _dispatch_phone_task(
            owner=owner,
            task=str(args["task"]),
            device_id=resolved_device,
            grant=grant,
            mode=str(args["mode"]),
            app_scope=str(args.get("app_scope") or ""),
            verification_level=str(args["verification_level"]),
            timeout_s=600,
            high_risk=True,
            decision_hint=None,
        )


def _dispatch_phone_task(
    *,
    owner: str,
    task: str,
    device_id: str,
    grant: dict[str, Any] | None,
    mode: str,
    app_scope: str,
    verification_level: str,
    timeout_s: int,
    high_risk: bool,
    decision_hint: dict[str, Any] | None,
) -> dict[str, Any]:
    clean_task, resolved_device = task, device_id
    if high_risk:
        try:
            from dharma import gate_action

            verdict = gate_action(
                "mobile_control",
                avatar="Matsya",
                detail=clean_task[:240],
                metadata={"profile_id": owner, "device_id": resolved_device},
            )
            if not verdict.allowed:
                return envelope(status="blocked", summary="; ".join(verdict.reasons))
        except Exception as exc:
            return envelope(status="blocked", summary=f"Dharma gate unavailable: {exc}")

    timeout_s = max(30, min(int(timeout_s), 1_800))
    task_id = str(uuid.uuid4())
    run_dir = ARTIFACTS_DIR / "phone-use" / owner / task_id
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        admission = _request(
            "POST",
            "/api/run",
            payload=_task_payload(
                task=clean_task,
                task_id=task_id,
                device_id=resolved_device,
                mode=mode,
                app_scope=app_scope,
                verification_level=verification_level,
            ),
            timeout_s=30,
        )
        tasks = admission.get("tasks", []) if isinstance(admission, dict) else []
        if not tasks or not isinstance(tasks[0], dict):
            raise ArtemisAdapterError(
                str(admission.get("error") or "Artemis did not admit the task")
                if isinstance(admission, dict)
                else "Artemis did not admit the task"
            )
        task_id = str(tasks[0].get("session_id") or tasks[0].get("task_id") or task_id)
        deadline = time.monotonic() + timeout_s
        result: dict[str, Any] = {}
        while time.monotonic() < deadline:
            try:
                result = _request("GET", f"/api/sessions/{task_id}", timeout_s=15)
            except ArtemisAdapterError as exc:
                if exc.status_code != 404:
                    raise
                status_payload = _request("GET", "/api/status", timeout_s=10)
                live_rows = []
                if isinstance(status_payload, dict):
                    for key in ("tasks", "active_tasks", "queue", "running"):
                        value = status_payload.get(key)
                        if isinstance(value, list):
                            live_rows.extend(item for item in value if isinstance(item, dict))
                result = next(
                    (
                        dict(item)
                        for item in live_rows
                        if task_id in {
                            str(item.get("session_id") or ""),
                            str(item.get("task_id") or ""),
                        }
                    ),
                    {"status": "launching", "session_id": task_id},
                )
            status = str(result.get("status") or "running").lower()
            if status in _TERMINAL:
                break
            time.sleep(1.5)
        else:
            return envelope(
                status="running",
                summary="The Android task is still running; Artemis retained the task for later inspection.",
                provenance={"engine": "artemis", "task_id": task_id, "profile_id": owner},
                task_id=task_id,
                device_id=resolved_device,
            )
    except ArtemisAdapterError as exc:
        return envelope(
            status="error",
            summary=str(exc),
            error="artemis_task_failed",
            provenance={"engine": "artemis", "task_id": task_id, "profile_id": owner},
            task_id=task_id,
        )

    manifest_path = run_dir / "result.json"
    manifest_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    status = str(result.get("status") or "unknown").lower()
    output = result.get("output") or result.get("result") or result.get("summary")
    summary = str(output or result.get("error") or f"Android task finished with status {status}")[:4_000]
    verification_hint = _android_verification_hint(clean_task, result)
    return envelope(
        status="ok" if status in _SUCCESS else "error",
        summary=summary,
        artifacts=[artifact(
            type="report",
            label="Android task result",
            path=manifest_path,
            mime_type="application/json",
            description="Profile-scoped Artemis task manifest.",
        )],
        ui=ui_panel(
            title="Android task",
            summary=summary,
            sections=[
                {"title": "Device", "body": str((grant or {}).get("label") or resolved_device)},
                {"title": "Mode", "body": mode},
            ],
            primary_artifact_label="Android task result",
            tone="computer-use",
        ),
        provenance={
            "engine": "artemis",
            "task_id": task_id,
            "profile_id": owner,
            "device_id": resolved_device,
            "admission_decision": decision_hint,
            "verification_decision": verification_hint,
        },
        requires_confirmation=False,
        task_id=task_id,
        device_id=resolved_device,
        result=result,
        decision_hint=decision_hint,
        verification_hint=verification_hint,
    )


def _register_approvals() -> None:
    import anumati

    anumati.register_executor("phone", _execute_phone_proposal)


_register_approvals()
