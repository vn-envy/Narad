"""The phone surface: an Android task that Artemis runs, watched by Kriya.

Artemis runs the whole task on the phone with its own agent, so there is no
operator loop here. Kriya holds the handle and does everything around it:

  admit     local rules only (risk_policy.classify_phone_task): the task
            classifier in English, Hinglish and Hindi, plus the banking/UPI/
            wallet app denylist, which can only tighten
  approve   a commit-class task waits in ``waiting_approval`` on an Anumati
            proposal (surface ``task``, preview kind ``phone``) before
            anything reaches the phone; it always runs in verified mode, and a
            denylisted app must be allowed for this task on the card (an edit:
            a new proposal with its own hash)
  dispatch  POST /api/run with a session id Narad stores first, so a
            restart re-attaches instead of dispatching twice
  follow    poll the task and its steps: each step becomes a line in the
            step list, the latest screenshot is the live frame (memory only,
            and never while a banking or UPI app is in front), and a
            denylisted app that was not allowed stops the task
  finish    Artemis's own verified-mode result (the Checker's run_outcome)
            decides done or failed; cancel stops the task on Artemis too

One task per phone at a time (the runtime's lock key is the device serial).
A task Artemis stopped tracking (no row, no queue entry, no device lease) for
a minute is stopped and failed, so nothing stays ``running`` forever.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

import artemis_adapter as artemis
from interaction_targets import operation_lock
from kriya import store

from risk_policy import classify_phone_task, denylisted_package

log = logging.getLogger("narad.kriya")

POLL_S = 1.5
STEPS_EVERY_S = 3.0
LIVE_CHECK_EVERY_S = 10.0
ORPHAN_AFTER_S = 60.0
LOST_CONTACT_AFTER_S = 90.0
STOP_WAIT_S = 15.0
DEFAULT_TIMEOUT_S = 1800
_MODES = ("fast", "verified")
_LEVELS = ("off", "final", "checkpoints", "strict")


def _stop_types():
    from kriya.runtime import _Stop

    return _Stop


# ── Admission ────────────────────────────────────────────────────────────────


def admit(owner: str, goal: str, options: dict[str, Any] | None) -> dict[str, Any]:
    """The task's ``phone`` envelope, or ValueError in plain words."""
    options = dict(options or {})
    if not artemis.artemis_status().get("configured"):
        raise ValueError("Android control is not set up on this Mac yet (Artemis is not configured)")
    try:
        device, grant = artemis.resolve_device(owner, str(options.get("device") or ""))
    except artemis.ArtemisAdapterError as exc:
        raise ValueError(str(exc)) from exc
    app_scope = str(options.get("app_scope") or "").strip()
    admission = classify_phone_task(goal, app_scope)
    risky = admission.needs_approval
    mode = str(options.get("mode") or ("verified" if risky else "fast")).strip().lower()
    level = str(options.get("verification_level") or "final").strip().lower()
    if mode not in _MODES:
        raise ValueError("mode must be 'fast' or 'verified'")
    if level not in _LEVELS:
        raise ValueError("verification_level must be off, final, checkpoints, or strict")
    if risky and mode != "verified":
        raise ValueError("High-risk Android tasks must use mode='verified'")
    if risky and level == "off":
        raise ValueError("A consequential phone task must be verified: verification_level cannot be off")
    try:
        timeout_s = int(options.get("timeout_s") or DEFAULT_TIMEOUT_S)
    except (TypeError, ValueError):
        timeout_s = DEFAULT_TIMEOUT_S
    return {
        "device_id": device,
        "device_label": str((grant or {}).get("label") or device),
        "target_id": (grant or {}).get("target_id"),
        "mode": mode,
        "verification_level": level,
        "app_scope": app_scope,
        "timeout_s": max(60, min(timeout_s, 3600)),
        "risk": {
            "needs_approval": risky,
            "category": admission.verdict.category,
            "reason": admission.verdict.reason,
        },
        "apps": [dict(app) for app in admission.apps],
        "allowed_apps": [],
    }


def lock_key(task: store.Task) -> str:
    return f"phone:{(task.envelope.get('phone') or {}).get('device_id') or 'unknown'}"


# ── The live frame ───────────────────────────────────────────────────────────


class PhoneSurface:
    """What the runtime's frame and takeover routes see of a phone task."""

    kind = "phone"
    url = ""

    def __init__(self) -> None:
        self.is_open = True
        self._lock = threading.Lock()
        self._image = ""
        self._frame: tuple[str, bytes] | None = None
        self.secure_app = ""  # a banking or UPI app is in front: no frames

    def show(self, image_name: str, foreground_app: str) -> None:
        with self._lock:
            self.secure_app = foreground_app if denylisted_package(foreground_app) else ""
            if image_name:
                self._image = image_name
            if self.secure_app:
                self._frame = None

    def frame(self) -> bytes | None:
        """The latest step screenshot, fetched once and kept only in memory."""
        with self._lock:
            name, secure, cached = self._image, self.secure_app, self._frame
        if secure or not name:
            return None
        if cached and cached[0] == name:
            return cached[1]
        data = artemis.session_image(name)
        if data:
            with self._lock:
                if not self.secure_app:
                    self._frame = (name, data)
        return data

    def takeover(self, kind: str, **params: Any) -> dict[str, Any]:
        raise PermissionError("A phone task is controlled on the phone itself; use Stop here")

    def close(self) -> None:
        self.is_open = False
        with self._lock:
            self._frame = None


# ── Approval ─────────────────────────────────────────────────────────────────


def _info(task: store.Task) -> dict[str, Any]:
    return dict(task.envelope.get("phone") or {})


def _save_info(task: store.Task, info: dict[str, Any]) -> store.Task:
    return store.update_task(task.task_id, profile_id=task.profile_id, envelope={**task.envelope, "phone": info})


def approval_spec(task: store.Task, info: dict[str, Any]) -> dict[str, Any]:
    """The hash-bound phone task: goal, phone, mode, scope and the allowed apps."""
    label, device = info["device_label"], info["device_id"]
    allowed = sorted(set(info.get("allowed_apps") or []))
    apps = info.get("apps") or []
    blocked = [app for app in apps if app["package"] not in allowed]
    allowed_names = [app["name"] for app in apps if app["package"] in allowed]
    summary = f"On {label}: {task.goal}"
    if allowed_names:
        summary += f" (allows {', '.join(allowed_names)} for this task)"
    warning = None
    if blocked:
        names = ", ".join(app["name"] for app in blocked)
        warning = (
            f"{names} is a banking or UPI app. Narad will not open it unless you allow it for this task "
            "on the task screen first."
        )
    return {
        "surface": "task",
        "action": info["risk"]["category"],
        "target": f"{task.task_id} @ {label} ({device})"[:500],
        "args": {
            "task_id": task.task_id,
            "goal": task.goal,
            "device_id": device,
            "mode": info["mode"],
            "verification_level": info["verification_level"],
            "app_scope": info.get("app_scope") or "",
            "allowed_apps": allowed,
        },
        "summary": summary,
        "risk_class": info["risk"]["category"],
        "preview": {
            "kind": "phone",
            "device": label,
            "mode": info["mode"],
            "app_scope": info.get("app_scope") or None,
            "reason": info["risk"]["reason"],
            "task_id": task.task_id,
            "task_goal": task.goal[:200],
            "blocked_apps": blocked,
            "allowed_apps": allowed_names,
            "warning": warning,
            "open_in_task": bool(blocked),
        },
    }


def _propose(task: store.Task, info: dict[str, Any]) -> store.Task:
    import anumati

    spec = approval_spec(task, info)
    proposal, _created = anumati.propose(**spec, session_id=task.session_id, profile_id=task.profile_id)
    pending = {"summary": spec["summary"], "args": spec["args"]}
    task = store.update_task(
        task.task_id, profile_id=task.profile_id, status="waiting_approval", proposal_id=proposal.proposal_id,
        pending=pending, detail=f"Waiting for your OK: {spec['summary']}",
    )
    store.add_event(task.task_id, profile_id=task.profile_id, kind="approval_requested",
                    summary=f"Asked for your OK: {spec['summary']}", data={"proposal_id": proposal.proposal_id})
    return task


def _await_approval(rt: Any, task: store.Task, control: Any, resumed: str) -> Any:
    """Ask (unless a restart found the question already asked) and wait for the
    person's decision; returns the consumed proposal."""
    import anumati

    _Stop = _stop_types()
    if resumed == "waiting_help":  # the OK had expired before the restart
        _wait_for_continue(rt, task, control, str((task.help or {}).get("reason") or task.detail))
        task = _propose(store.get_task(task.task_id, profile_id=task.profile_id), _info(task))
    elif not (resumed == "waiting_approval" and task.proposal_id):
        task = _propose(task, _info(task))
    while True:
        task = store.get_task(task.task_id, profile_id=task.profile_id)
        proposal_id = str(task.proposal_id or "")
        rt._checkpoint(task.task_id, task.profile_id, control, proposal_id=proposal_id)
        try:
            proposal = anumati.get(proposal_id, profile_id=task.profile_id)
        except anumati.ProposalNotFound as exc:
            raise _Stop("failed", "The approval for this phone task disappeared; nothing was done.") from exc
        if proposal.status == "pending":
            control.wake.wait(rt.poll_s)
            control.wake.clear()
            continue
        if proposal.status == "edited" and proposal.superseded_by:
            store.update_task(task.task_id, profile_id=task.profile_id, proposal_id=proposal.superseded_by)
            continue
        if proposal.status == "approved":
            gate = anumati.check(surface="task", action=proposal.action, target=proposal.target,
                                 args=proposal.args, profile_id=task.profile_id)
            if gate is None:
                control.wake.wait(rt.poll_s)
                continue
            if not gate.approved:
                raise _Stop("failed", "This exact phone task already ran after your OK; it is not run twice.")
            summary = str((task.pending or {}).get("summary") or proposal.summary)
            store.update_task(task.task_id, profile_id=task.profile_id, status="running", proposal_id=None,
                              pending=None, detail=f"Approved: {summary}")
            store.add_event(task.task_id, profile_id=task.profile_id, kind="approved", summary=f"You approved: {summary}")
            return gate.proposal
        if proposal.status == "rejected":
            why = f" ({proposal.decision_reason})" if proposal.decision_reason else ""
            raise _Stop("cancelled", f"Stopped before anything ran on the phone. You declined it{why}.",
                        reason="rejected")
        if proposal.status == "expired":
            store.update_task(task.task_id, profile_id=task.profile_id, proposal_id=None, pending=None)
            store.add_event(task.task_id, profile_id=task.profile_id, kind="approval_expired",
                            summary="The approval expired before you decided")
            _wait_for_continue(rt, task, control, "Your OK for this phone task expired. Tap Continue and Narad "
                                                  "will ask again.")
            task = _propose(store.get_task(task.task_id, profile_id=task.profile_id), _info(task))
            continue
        raise _Stop("failed", f"The approval ended as {proposal.status}; nothing was done on the phone.")


def _wait_for_continue(rt: Any, task: store.Task, control: Any, reason: str) -> None:
    _Stop = _stop_types()
    from kriya.runtime import _iso_now

    store.update_task(task.task_id, profile_id=task.profile_id, status="waiting_help",
                      help={"kind": "approval_expired", "reason": reason, "since": _iso_now()}, detail=reason)
    store.add_event(task.task_id, profile_id=task.profile_id, kind="help_needed", summary=reason,
                    data={"help": "approval_expired"})
    control.resume.clear()
    deadline = time.monotonic() + rt.help_timeout_s
    while not control.resume.is_set():
        rt._checkpoint(task.task_id, task.profile_id, control)
        if time.monotonic() > deadline:
            raise _Stop("failed", f"Nobody came back within {int(rt.help_timeout_s // 60)} minutes.")
        control.wake.wait(rt.poll_s)
        control.wake.clear()
    control.resume.clear()
    store.update_task(task.task_id, profile_id=task.profile_id, status="running", help=None,
                      detail="Asking again")
    store.add_event(task.task_id, profile_id=task.profile_id, kind="resumed", summary="You tapped Continue")


def allow_app(task_id: str, *, profile_id: str, package: str, decided_by: str, device: str = "") -> store.Task:
    """The person allows one denylisted app for this task: the pending proposal
    is replaced by one whose hash includes it (they still approve it)."""
    import anumati

    task = store.get_task(task_id, profile_id=profile_id)
    if task.surface != "phone" or task.status != "waiting_approval" or not task.proposal_id:
        raise PermissionError("This task is not waiting for your OK")
    info = _info(task)
    if package not in {app["package"] for app in info.get("apps") or []}:
        raise ValueError("That app is not one this task needs")
    new = anumati.edit(task.proposal_id, {"allow_apps": [package]}, profile_id=profile_id, decided_by=decided_by,
                       device=device)
    name = next(app["name"] for app in info["apps"] if app["package"] == package)
    store.add_event(task_id, profile_id=profile_id, kind="app_allowed", summary=f"You allowed {name} for this task")
    return store.update_task(task_id, profile_id=profile_id, proposal_id=new.proposal_id,
                             pending={"summary": new.summary, "args": new.args},
                             detail=f"Waiting for your OK: {new.summary}")


def _edit_task_proposal(old: Any, changes: dict[str, Any]) -> dict[str, Any]:
    """Anumati's editor for ``task`` proposals: only a phone task's app allowance."""
    if (old.preview or {}).get("kind") != "phone" or set(changes) != {"allow_apps"}:
        raise ValueError("This kind of approval cannot be edited; reject it and ask for a new one.")
    task = store.get_task(str(old.args.get("task_id")), profile_id=old.profile_id)
    info = _info(task)
    known = {app["package"] for app in info.get("apps") or []}
    wanted = {str(item) for item in changes.get("allow_apps") or []}
    if not wanted or not wanted <= known:
        raise ValueError("Only the banking or UPI apps this task needs can be allowed")
    info["allowed_apps"] = sorted(set(old.args.get("allowed_apps") or []) | wanted)
    return approval_spec(task, info)


# ── Dispatch and follow ──────────────────────────────────────────────────────


def run(rt: Any, task: store.Task, control: Any, resumed: str) -> None:
    """The whole phone task on the runtime's worker thread (raises _Stop to end).

    ``resumed`` is the status the task had when this thread picked it up:
    after a restart a waiting task keeps waiting on the same question, and a
    dispatched one is followed again, never sent twice."""
    _Stop = _stop_types()
    info = _info(task)
    surface = PhoneSurface()
    control.surface = surface
    try:
        if not info.get("artemis_session"):  # else: dispatched before a restart, follow it again
            if info["risk"]["needs_approval"] and not info.get("approved_args"):
                proposal = _await_approval(rt, task, control, resumed)
                info = _info(store.get_task(task.task_id, profile_id=task.profile_id))
                info["proposal_id"] = proposal.proposal_id
                info["approved_args"] = dict(proposal.args)
                info["allowed_apps"] = list(proposal.args.get("allowed_apps") or [])
                task = _save_info(store.get_task(task.task_id, profile_id=task.profile_id), info)
            args = info.get("approved_args") or {
                "goal": task.goal, "device_id": info["device_id"], "mode": info["mode"],
                "verification_level": info["verification_level"], "app_scope": info.get("app_scope") or "",
                "allowed_apps": [],
            }
            blocked = [app for app in info.get("apps") or [] if app["package"] not in set(args["allowed_apps"])]
            if blocked:
                names = ", ".join(app["name"] for app in blocked)
                raise _Stop("failed", f"{names} is on the banking and UPI list and was not allowed for this task, "
                                      "so nothing was done on the phone. Ask again and tap Allow on the task "
                                      "screen before approving.", reason="app_not_allowed")
            rt._checkpoint(task.task_id, task.profile_id, control)
        # Kriya already runs one task per phone; the operation lock also keeps
        # any other caller's taps off this phone while the task holds it.
        lock = operation_lock(f"android:{info['device_id']}")
        if not lock.acquire(timeout=30):
            raise _Stop("failed", "The phone is busy with another action; ask again in a moment.")
        try:
            if not info.get("artemis_session"):
                task = _dispatch(task, info, args)
            _follow(rt, task, control, surface)
        finally:
            lock.release()
    except _Stop as stop:
        _record(task, stop)
        raise
    finally:
        surface.close()


def _record(task: store.Task, stop: Any) -> None:
    """The consumed approval learns how the task ended (once)."""
    info = _info(store.get_task(task.task_id, profile_id=task.profile_id))
    proposal_id = info.get("proposal_id")
    if not proposal_id or info.get("proposal_recorded"):
        return
    try:
        import anumati

        anumati.record_result(
            proposal_id,
            {"status": "ok" if stop.status == "done" else "error", "summary": stop.summary, "task_id": task.task_id},
            profile_id=task.profile_id,
        )
        info["proposal_recorded"] = True
        _save_info(store.get_task(task.task_id, profile_id=task.profile_id), info)
    except Exception as exc:
        log.warning("Kriya: recording the phone approval failed: %s", exc)


def _dharma_refusal(task: store.Task, device: str) -> str | None:
    from computer_use_skill import _dharma_gate

    return _dharma_gate("mobile_control", task.goal[:240], {"task_id": task.task_id, "device_id": device})


def screen_model(status: dict[str, Any]) -> str:
    """The model Artemis's agent sends the phone's screen to, as Narad names models
    (from ``model_info`` in /api/status: provider "google", id "gemini-...")."""
    info = status.get("model_info") if isinstance(status.get("model_info"), dict) else {}
    model, provider = str(info.get("id") or "").strip(), str(info.get("provider") or "").strip()
    if not model:
        return ""
    import privacy_gateway

    if privacy_gateway.provider_for_model(model) != "unknown" or not provider:
        return model
    return f"{provider}/{model}"


def _screens_refusal(readiness: dict[str, Any]) -> str | None:
    """Artemis's agent sees every screen, which cannot be pseudonymised: like any
    screenshot it may reach only a local or trusted model. The check (and its
    line in the egress ledger) goes through the privacy gateway."""
    import privacy_gateway

    model = screen_model(readiness.get("status") or {})
    if not model:
        return "Artemis did not say which model reads the phone's screen, so nothing was sent to the phone."
    if not privacy_gateway.allow_raw(model, source="kriya_phone"):
        return (f"Artemis is set to send the phone's screen to {model}, which is not a local or trusted "
                "provider, so nothing was sent to the phone. Change Artemis's model or the provider's tier.")
    return None


def _dispatch(task: store.Task, info: dict[str, Any], args: dict[str, Any]) -> store.Task:
    _Stop = _stop_types()
    readiness = artemis.artemis_status()
    if not readiness.get("ready"):
        raise _Stop("failed", f"Artemis is not running on the Mac: {readiness.get('reason') or 'no answer'}")
    refusal = _screens_refusal(readiness)
    if refusal:
        raise _Stop("failed", refusal, reason="screen_model")
    try:
        device, _grant = artemis.resolve_device(task.profile_id, str(args["device_id"]))
    except artemis.ArtemisAdapterError as exc:
        raise _Stop("failed", str(exc)) from exc
    if device != args["device_id"]:
        raise _Stop("failed", "The phone this task was approved for is no longer the one granted; nothing ran.")
    if info["risk"]["needs_approval"]:
        refusal = _dharma_refusal(task, device)
        if refusal:
            raise _Stop("failed", f"Policy refused this phone task: {refusal}")
    session_id = str(uuid.uuid4())
    # Stored before the request: after a restart Narad re-attaches to this id
    # and never sends the task a second time.
    info.update(artemis_session=session_id, dispatched_ts=time.time())
    task = _save_info(task, info)
    payload = artemis.task_payload(
        task=str(args["goal"]), task_id=session_id, device_id=device, mode=str(args["mode"]),
        app_scope=str(args.get("app_scope") or ""), verification_level=str(args["verification_level"]),
    )
    try:
        admitted = artemis.start_session(payload)
    except artemis.ArtemisAdapterError as exc:
        why = str(exc)
        if exc.status_code == 409:
            why = "the phone is locked or busy. Unlock it on the home screen and ask again."
        raise _Stop("failed", f"Artemis did not start the task: {why}") from exc
    actual = str(admitted.get("session_id") or admitted.get("task_id") or session_id)
    if actual != session_id:
        info["artemis_session"] = actual
        task = _save_info(task, info)
    mode = "verified" if args["mode"] == "verified" else "fast"
    store.add_event(task.task_id, profile_id=task.profile_id, kind="dispatched",
                    summary=f"Sent to {info['device_label']} ({mode} mode)")
    return store.update_task(task.task_id, profile_id=task.profile_id, detail=f"Working on {info['device_label']}")


def _step_line(step: dict[str, Any]) -> str:
    summary = " ".join(str(step.get("summary") or "").split())
    if summary:
        return summary[:220]
    action = step.get("action_taken")
    if isinstance(action, dict):
        name = str(action.get("name") or action.get("action") or action.get("type") or "").replace("_", " ")
        if name:
            return name.capitalize()[:120]
    return "A step on the phone"


def _foreground(step: dict[str, Any]) -> str:
    meta = step.get("extra_metadata")
    return str(meta.get("foreground_app") or "") if isinstance(meta, dict) else ""


def _follow(rt: Any, task: store.Task, control: Any, surface: PhoneSurface) -> None:
    _Stop = _stop_types()
    info = _info(task)
    session_id = str(info["artemis_session"])
    allowed = set((info.get("approved_args") or {}).get("allowed_apps") or info.get("allowed_apps") or [])
    started = float(info.get("dispatched_ts") or time.time())
    deadline = started + int(info.get("timeout_s") or DEFAULT_TIMEOUT_S)
    seen = int(info.get("steps_seen") or 0)
    absent_since: float | None = None
    unreachable_since: float | None = None
    next_steps = next_live_check = 0.0
    last_line = ""
    while True:
        try:
            rt._checkpoint(task.task_id, task.profile_id, control)
        except _Stop as stop:
            if stop.status == "cancelled":
                stop.summary = _cancel_remote(session_id)
            raise
        now = time.monotonic()
        try:
            row = artemis.get_session(session_id)
            unreachable_since = None
        except artemis.ArtemisAdapterError as exc:
            unreachable_since = unreachable_since or now
            if now - unreachable_since > LOST_CONTACT_AFTER_S:
                raise _Stop("failed", f"Lost contact with Artemis ({exc}). Check the phone.") from exc
            control.wake.wait(POLL_S)
            control.wake.clear()
            continue
        status = str((row or {}).get("status") or "launching").lower()
        if now >= next_steps:
            next_steps = now + STEPS_EVERY_S
            steps = artemis.session_steps(session_id) or []
            before = seen
            for step in steps[seen:]:
                seen += 1
                line = _step_line(step)
                last_line = line or last_line
                store.add_event(task.task_id, profile_id=task.profile_id, kind="step", step=seen, summary=line,
                                data={"line": line, "status": "ok"})
                app = _foreground(step)
                surface.show(str(step.get("post_image_name") or step.get("pre_image_name") or ""), app)
                banned = denylisted_package(app)
                if banned and banned["package"] not in allowed:
                    artemis.stop_session(session_id)
                    raise _Stop("failed", f"Stopped: {banned['name']} opened on the phone. It is a banking or UPI "
                                          "app and was not allowed for this task.", reason="app_not_allowed")
            if seen != before:
                info["steps_seen"] = seen
                task = _save_info(task, info)
                task = store.update_task(task.task_id, profile_id=task.profile_id, step=seen,
                                         detail=last_line or task.detail)
        if artemis.is_terminal(status):
            _finish_from(row or {}, session_id, info, last_line)
        if row is None:
            absent_since = absent_since or now
            if now - absent_since > ORPHAN_AFTER_S:
                raise _Stop("failed", "Artemis has no record of this task any more, so Narad stopped following it. "
                                      "Check the phone.", reason="orphaned")
        elif status == "running":
            if now >= next_live_check:
                next_live_check = now + LIVE_CHECK_EVERY_S
                # The row says running; is anything actually holding the phone for it?
                if artemis.live_task(session_id) is None:
                    absent_since = absent_since or now
                    if now - absent_since > ORPHAN_AFTER_S:
                        artemis.stop_session(session_id)
                        raise _Stop("failed", "Artemis stopped working on this task without finishing it. "
                                              "Check the phone.", reason="orphaned")
                else:
                    absent_since = None
        else:
            absent_since = None  # queued, or another live state Artemis reports
        if time.time() > deadline:
            artemis.stop_session(session_id)
            raise _Stop("failed", f"Stopped after {int(info.get('timeout_s') or DEFAULT_TIMEOUT_S) // 60} minutes "
                                  "without finishing.")
        control.wake.wait(POLL_S)
        control.wake.clear()


def _cancel_remote(session_id: str) -> str:
    """Stop the task on Artemis and wait briefly for it to say so."""
    try:
        stopped = artemis.stop_session(session_id)
    except artemis.ArtemisAdapterError as exc:
        return f"Stopped following it, but Artemis could not be told ({exc}). Check the phone."
    deadline = time.monotonic() + STOP_WAIT_S
    while time.monotonic() < deadline:
        try:
            row = artemis.get_session(session_id)
        except artemis.ArtemisAdapterError:
            break
        if row is None or artemis.is_terminal(str(row.get("status") or "")):
            return "Stopped from the phone app; Artemis stopped the task."
        time.sleep(0.5)
    if stopped:
        return "Stopped from the phone app; Artemis is stopping the task."
    return "Stopped following it; Artemis did not confirm the stop. Check the phone."


def verification_result(row: dict[str, Any], checks: dict[str, Any] | None, mode: str) -> dict[str, Any]:
    """Artemis's verified-mode result, in the task's words.

    ``status`` is verified (every check passed), unverified (no check could
    prove it), failed (a check failed), partial (only part of the plan was
    done), blocked (the Checker stopped it), or not_requested (fast mode).
    """
    if mode != "verified":
        return {"status": "not_requested"}
    outcome = (checks or {}).get("run_outcome")
    if not isinstance(outcome, dict):
        return {"status": "unverified", "reason": "Artemis reported no checks for this task"}
    tests = outcome.get("tests") if isinstance(outcome.get("tests"), dict) else {}
    counts = {key: int(tests.get(key) or 0) for key in ("passed", "failed", "inconclusive", "unchecked")}
    task_status = str(outcome.get("task_status") or "")
    failed_items = [
        " ".join(str(item.get("text") or item.get("description") or item.get("check") or "").split())[:160]
        for item in tests.get("failed_items") or [] if isinstance(item, dict)
    ]
    findings = [" ".join(str(item).split())[:160] for item in outcome.get("last_findings") or []][:3]
    if task_status == "blocked":
        status = "blocked"
    elif task_status == "partial":
        status = "partial"
    elif counts["failed"]:
        status = "failed"
    elif counts["passed"] and not counts["inconclusive"] and not counts["unchecked"]:
        status = "verified"
    else:
        status = "unverified"
    return {"status": status, "task_status": task_status, "tests": counts,
            "failed_checks": [item for item in failed_items if item], "findings": findings}


def _finish_from(row: dict[str, Any], session_id: str, info: dict[str, Any], last_line: str) -> None:
    """Raise the task's end from Artemis's terminal row and its Checker verdict."""
    _Stop = _stop_types()
    status = str(row.get("status") or "").lower()
    answer = str(row.get("output") or row.get("result") or row.get("summary") or "")[:1200]
    if status in {"cancelled", "canceled"}:
        raise _Stop("cancelled", "Stopped on the phone before it finished.", reason="cancelled")
    if not artemis.is_success(status):
        error = str(row.get("error") or row.get("error_message") or last_line or status)
        raise _Stop("failed", f"The phone task did not finish: {error[:300]}")
    mode = str((info.get("approved_args") or {}).get("mode") or info.get("mode") or "fast")
    checks = None
    if mode == "verified":
        try:
            checks = artemis.session_checks(session_id)
        except artemis.ArtemisAdapterError:
            checks = None
    verification = verification_result(row, checks, mode)
    extra = {"verification": verification}
    kind = verification["status"]
    if kind in {"failed", "partial", "blocked"}:
        details = "; ".join(verification.get("failed_checks") or verification.get("findings") or [])
        words = {"failed": "some of Artemis's checks failed", "partial": "only part of it was done",
                 "blocked": "Artemis's checker stopped it"}[kind]
        raise _Stop("failed", f"The phone task ended, but {words}{f': {details}' if details else ''}. "
                              "Check the phone.", answer=answer, extra=extra)
    if kind == "unverified":
        why = verification.get("reason") or "no check passed"
        raise _Stop("done", f"Done on the phone, but Artemis could not confirm it ({why}). Check the phone.",
                    answer=answer or last_line, extra=extra)
    note = "Done on the phone; Artemis checked the result." if kind == "verified" else "Done on the phone."
    raise _Stop("done", note, answer=answer or last_line, extra=extra)


def stop_remote(task: store.Task) -> None:
    """Cancel a phone task nobody is following (queued, or after a restart)."""
    session_id = str((task.envelope.get("phone") or {}).get("artemis_session") or "")
    if session_id:
        try:
            artemis.stop_session(session_id)
        except artemis.ArtemisAdapterError as exc:
            log.warning("Kriya: stopping phone task %s on Artemis failed: %s", task.task_id, exc)


def _register() -> None:
    import anumati

    anumati.register_editor("task", _edit_task_proposal)


_register()
