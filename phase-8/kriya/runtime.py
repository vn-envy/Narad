"""Kriya's task runtime: the queue, the surface locks, and the loop.

Each task runs on its own worker thread, outside any chat turn:

  perceive  a viewport-scoped accessibility observation (kriya.perception)
  decide    the operator model picks the next step(s) as JSON (kriya.operator)
  act       by ref, 5 s actionability timeout (kriya.browser)
  settle    navigation finished or the DOM quiet, bounded
  verify    a deterministic check of the step's expectation; on a miss the
            target is re-grounded and the step retried once, then reported

Only the latest observation goes to the operator in full; every earlier step
is one line. A commit-class step (risk_policy) becomes an Anumati proposal
with a screenshot, the task waits in ``waiting_approval``, and the loop runs
exactly that step, once, after the person approves it on their phone; a
rejection stops the task cleanly and an expired approval pauses it. A sign-in
or captcha puts the task in ``waiting_help``: the person finishes it on the
live view and taps Continue. Instruction-like text on a page is flagged to
the operator as untrusted, and every non-read step there needs approval.

Phone tasks (kriya.phone) run on Artemis instead of this loop: Kriya admits
them locally, holds the approval, dispatches, follows the steps and maps the
verified result. Desktop tasks (kriya.desktop, owner only) use this loop on a
window of the Mac through the persistent cua-driver session, and every input
step there waits for an approval.

Locks: one browser task per profile at a time (the isolated and the cloud
browser share the key), one task per phone, one desktop task on the host;
more tasks queue.
Cancel is checked before every action and while waiting, so a task stops
within one step. Tasks survive a restart: unfinished ones are resumed from
their last page, with their step history rebuilt from the event log.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from kriya import store
from kriya.operator import Decision, ModelOperator, OperatorContext, OperatorError
from kriya.perception import FIELD_ROLES, Node, Observation

from profile_context import profile_scope, validate_profile_id

log = logging.getLogger("narad.kriya")

SURFACES = ("browser", "cloud_browser", "desktop", "phone")
_LIVE = frozenset({"running", "waiting_approval", "waiting_help"})
_REF_ACTIONS = frozenset({"click", "fill", "type", "select", "check", "uncheck", "download"})
_INPUT_ACTIONS = frozenset({"fill", "type", "select", "check", "uncheck"})
_SECRET_FIELD = re.compile(r"(?i)password|passcode|otp|one[- ]?time|cvv|cvc|card number|pin\b|aadhaar|pan\b")
# Words that mean a task needs someone's own account: never the cloud browser.
_SIGNED_IN_WORDS = re.compile(
    r"(?i)\b(?:my (?:account|orders?|bookings?|inbox|email|mail|bank|profile|cart|tickets?)|sign ?in|log ?in|"
    r"login|password|otp|account|bank|upi|wallet|netbanking|aadhaar|pan card|passport)\b"
)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _deliver(**kwargs: Any) -> None:
    """vahana.deliver, never raising (tests replace this)."""
    try:
        from vahana import deliver

        deliver(**kwargs)
    except Exception as exc:
        log.warning("Kriya: notification failed: %s", exc)


def _host(url: str) -> str:
    from computer_use_skill import _host_of

    return _host_of(url)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Stop(Exception):
    """The task ends here: done, failed or cancelled."""

    def __init__(self, status: str, summary: str, *, answer: str = "", reason: str = "",
                 extra: dict[str, Any] | None = None) -> None:
        super().__init__(summary)
        self.status, self.summary, self.answer, self.reason = status, summary, answer, reason
        self.extra = extra or {}  # more for the task's result, e.g. a phone task's verification


class _Suspended(Exception):
    """The server is stopping: leave the task as it is, to resume on restart."""


class _Migrate(Exception):
    """A cloud-browser task needs the browser on the Mac (a sign-in or personal data)."""


@dataclass
class _Control:
    cancel: threading.Event = field(default_factory=threading.Event)
    resume: threading.Event = field(default_factory=threading.Event)
    wake: threading.Event = field(default_factory=threading.Event)
    surface: Any = None
    thread: threading.Thread | None = None
    notified: dict[str, float] = field(default_factory=dict)  # help kind -> when the phone was told


@dataclass
class StepOutcome:
    status: str  # ok | failed | no_effect | refused | blocked
    line: str
    observation: Observation
    page_changed: bool = False


def describe(action: dict[str, Any], node: Node | None = None) -> str:
    """One plain-words line for a step, never showing a secret."""
    kind = action.get("action", "")
    name = (node.name if node is not None else "") or str(action.get("name") or action.get("ref") or "")
    label = f'"{name[:60]}"' if name else "the page"
    value = str(action.get("value") if action.get("value") is not None else action.get("text") or "")
    if node is not None and (_SECRET_FIELD.search(name) or node.role == "textbox" and "password" in name.lower()):
        value = "••••"
    if kind in {"fill", "type"}:
        return f'Type "{value[:40]}" into {label}'
    if kind == "select":
        return f'Choose "{value[:40]}" in {label}'
    if kind == "check":
        return f"Tick {label}"
    if kind == "uncheck":
        return f"Untick {label}"
    if kind == "click":
        return f"Click {label}"
    if kind == "press":
        return f"Press {action.get('key') or 'Enter'}" + (f" in {label}" if node is not None else "")
    if kind == "scroll":
        return f"Scroll {str(action.get('direction') or 'down').lower()}"
    if kind == "navigate":
        return f"Open {_host(str(action.get('url') or ''))}"
    if kind == "back":
        return "Go back"
    if kind == "wait":
        return "Wait for the page"
    if kind == "download":
        return f"Download {label}"
    if kind == "hotkey":
        return f"Press {'+'.join(str(key) for key in action.get('keys') or [])}"
    if kind == "open_app":
        return f"Open the app {str(action.get('name') or '')[:40]}"
    if kind == "switch_window":
        return f"Read the window {label}"
    return kind.replace("_", " ").capitalize()


def choose_surface(requested: str, *, goal: str, start_url: str, done_when: str) -> str:
    """The cloud browser only when configured and the task needs no account and
    carries no personal data (owner decision 2); otherwise the Mac's browser."""
    requested = (requested or "browser").strip().lower()
    if requested not in {"browser", "cloud_browser", "auto"}:
        return requested
    from kriya.browser import cloud_browser_configured

    if not cloud_browser_configured() or os.environ.get("NARAD_KRIYA_CLOUD", "auto").lower() in {"0", "off"}:
        return "browser"
    text = " ".join((goal, start_url, done_when))
    if _SIGNED_IN_WORDS.search(text) or _personal(text):
        return "browser"
    return "cloud_browser"


def _personal(text: str) -> bool:
    """Rules and the family name list (no ML: this runs on every fill)."""
    try:
        import privacy_gateway

        return bool(privacy_gateway.find_spans(text, use_ml=False))
    except Exception:
        return True  # unsure: keep it on the Mac


def _default_surface(task: store.Task) -> Any:
    if task.surface == "desktop":
        from kriya.desktop import DesktopSurface

        return DesktopSurface(task_id=task.task_id, profile_id=task.profile_id, goal=task.goal)
    from kriya.browser import BrowserSurface

    return BrowserSurface(
        task_id=task.task_id, profile_id=task.profile_id, goal=task.goal, cloud=task.surface == "cloud_browser"
    )


def _default_operator(task: store.Task) -> Any:
    if task.surface == "desktop":
        from kriya.operator import DESKTOP_SYSTEM_PROMPT

        return ModelOperator(system_prompt=DESKTOP_SYSTEM_PROMPT)
    return ModelOperator()


_WHERE = {"browser": "the browser on the Mac", "cloud_browser": "the cloud browser", "desktop": "the Mac's desktop"}


def where(task: store.Task) -> str:
    """The surface in plain words: "the browser on the Mac", a phone's name..."""
    if task.surface == "phone":
        return str((task.envelope.get("phone") or {}).get("device_label") or "the phone")
    return _WHERE.get(task.surface, task.surface)


class TaskRuntime:
    def __init__(
        self,
        *,
        operator_factory: Callable[[store.Task], Any] | None = None,
        surface_factory: Callable[[store.Task], Any] | None = None,
        poll_s: float | None = None,
        workers: int | None = None,
        help_timeout_s: float | None = None,
    ) -> None:
        self._operator_factory = operator_factory or _default_operator
        self._surface_factory = surface_factory or _default_surface
        self.poll_s = poll_s if poll_s is not None else _env_float("NARAD_KRIYA_POLL_S", 0.5)
        self.workers = workers or int(_env_float("NARAD_KRIYA_WORKERS", 3))
        self.help_timeout_s = (
            help_timeout_s if help_timeout_s is not None else _env_float("NARAD_KRIYA_HELP_TIMEOUT_S", 1800)
        )
        self._lock = threading.RLock()
        self._controls: dict[str, _Control] = {}
        self._queue: list[tuple[float, str, str]] = []  # (created_ts, profile_id, task_id)
        self._busy: set[str] = set()
        self._running = 0
        self._stopping = False

    # ── public API ───────────────────────────────────────────────────────────

    def submit(
        self,
        *,
        profile_id: str,
        goal: str,
        start_url: str = "",
        done_when: str = "",
        surface: str = "browser",
        session_id: str | None = None,
        max_steps: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> store.Task:
        """``options`` for a phone task: device, app_scope, mode, verification_level, timeout_s."""
        owner = validate_profile_id(profile_id)
        goal = " ".join(str(goal or "").split())
        if len(goal) < 4:
            raise ValueError("Describe the errand in a sentence")
        requested = (surface or "browser").strip().lower()
        envelope: dict[str, Any] = {}
        if requested == "phone":
            from kriya import phone

            chosen, start_url = "phone", ""
            envelope["phone"] = phone.admit(owner, goal, options)
        elif requested == "desktop":
            from kriya import desktop

            chosen, start_url = "desktop", ""
            envelope["desktop"] = desktop.admit(owner)
        else:
            if start_url:
                from kriya.browser import validate_start_url

                start_url = validate_start_url(start_url)
            chosen = choose_surface(requested, goal=goal, start_url=start_url, done_when=done_when)
            if chosen not in {"browser", "cloud_browser"}:
                raise ValueError(f"Unknown task surface {chosen!r}: use browser, phone or desktop")
        active = store.list_tasks(profile_id=owner, status="queued,running,waiting_approval,waiting_help")
        limit = int(_env_float("NARAD_KRIYA_MAX_ACTIVE", 3))
        if len(active) >= limit:
            raise ValueError(f"{len(active)} tasks are already running or waiting; stop one first")
        steps = int(max_steps or _env_float("NARAD_KRIYA_MAX_STEPS", 30))
        task = store.create_task(
            profile_id=owner,
            goal=goal,
            start_url=start_url,
            done_when=done_when,
            surface=chosen,
            session_id=session_id,
            max_steps=max(3, min(steps, 60)),
            envelope=envelope,
        )
        store.add_event(
            task.task_id, profile_id=owner, kind="created", summary=f"Queued on {where(task)}",
            data={"surface": chosen},
        )
        self._enqueue(task)
        return store.get_task(task.task_id, profile_id=owner)

    def get(self, task_id: str, *, profile_id: str) -> store.Task:
        return store.get_task(task_id, profile_id=validate_profile_id(profile_id))

    def cancel(self, task_id: str, *, profile_id: str) -> store.Task:
        owner = validate_profile_id(profile_id)
        task = store.get_task(task_id, profile_id=owner)
        if not task.active:
            return task
        store.update_task(task_id, profile_id=owner, cancel_requested=True, detail="Stopping…")
        with self._lock:
            control = self._controls.get(task_id)
            queued = any(item[2] == task_id for item in self._queue)
            if queued:
                self._queue = [item for item in self._queue if item[2] != task_id]
        if control is not None:
            control.cancel.set()
            control.wake.set()
        if queued or control is None:
            # Nothing is running it (queued, or not picked up since a restart): stop it here.
            if task.surface == "phone":
                from kriya import phone

                phone.stop_remote(task)  # a dispatched phone task leaves no orphan on Artemis
            if task.proposal_id:
                import anumati

                anumati.supersede(task.proposal_id, profile_id=owner, reason="The task was stopped")
            self._finish(store.get_task(task_id, profile_id=owner), "cancelled",
                         "Stopped before it started." if task.status == "queued" else "Stopped from the phone.",
                         reason="cancelled")
        return store.get_task(task_id, profile_id=owner)

    def resume(self, task_id: str, *, profile_id: str) -> store.Task:
        """The person finished the sign-in (or wants the expired approval asked again)."""
        owner = validate_profile_id(profile_id)
        task = store.get_task(task_id, profile_id=owner)
        if task.status != "waiting_help":
            raise PermissionError("This task is not waiting for you")
        control = self._controls.get(task_id)
        if control is None:
            raise PermissionError("This task is not running on this Mac right now")
        control.resume.set()
        control.wake.set()
        return task

    def takeover(self, task_id: str, *, profile_id: str, kind: str, **params: Any) -> dict[str, Any]:
        """Forward a tap, typing, a key, a scroll or Back to a task paused for help."""
        owner = validate_profile_id(profile_id)
        task = store.get_task(task_id, profile_id=owner)
        if task.status != "waiting_help":
            raise PermissionError("You can only control the page while the task waits for your help")
        control = self._controls.get(task_id)
        surface = control.surface if control is not None else None
        if surface is None or not surface.is_open:
            raise PermissionError("The page is not open right now")
        result = surface.takeover(kind, **params)
        words = {"click": "You tapped the page", "type": "You typed on the page", "key": "You pressed a key",
                 "scroll": "You scrolled", "back": "You went back"}
        # What was typed is never recorded, not even its length.
        store.add_event(task_id, profile_id=owner, kind="takeover", summary=words.get(kind, "You used the page"))
        store.update_task(task_id, profile_id=owner, last_url=surface.url)
        return result

    def frame(self, task_id: str, *, profile_id: str) -> bytes | None:
        owner = validate_profile_id(profile_id)
        task = store.get_task(task_id, profile_id=owner)
        if task.status not in _LIVE:
            return None
        control = self._controls.get(task_id)
        surface = control.surface if control is not None else None
        if surface is None or not surface.is_open:
            return None
        return surface.frame()

    def wait(self, task_id: str, *, profile_id: str, statuses: set[str] | frozenset[str], timeout_s: float = 60):
        """Block until the task reaches one of ``statuses`` (tests and scripts)."""
        deadline = time.monotonic() + timeout_s
        while True:
            task = store.get_task(task_id, profile_id=profile_id)
            if task.status in statuses:
                return task
            if time.monotonic() > deadline:
                raise TimeoutError(f"Task {task_id} is still {task.status} ({task.detail})")
            time.sleep(0.05)

    def resume_all(self) -> int:
        """After a restart: requeue every unfinished task, oldest first."""
        count = 0
        for task in store.active_tasks_everywhere():
            with self._lock:
                if task.task_id in self._controls or any(item[2] == task.task_id for item in self._queue):
                    continue
            if task.status != "queued":
                store.add_event(task.task_id, profile_id=task.profile_id, kind="resumed",
                                summary="Narad restarted; picking this up again")
            self._enqueue(task)
            count += 1
        return count

    def suspend(self) -> None:
        """Server shutdown: stop at the next checkpoint and leave tasks for resume."""
        with self._lock:
            self._stopping = True
            controls = list(self._controls.values())
        for control in controls:
            control.wake.set()
        for control in controls:
            if control.thread is not None:
                control.thread.join(timeout=5)

    # ── queue and locks ──────────────────────────────────────────────────────

    @staticmethod
    def _lock_key(task: store.Task) -> str:
        if task.surface in {"browser", "cloud_browser"}:
            return f"browser:{task.profile_id}"
        if task.surface == "phone":
            from kriya import phone

            return phone.lock_key(task)  # one task per phone, whoever's
        return task.surface  # desktop: one at a time for the whole host

    def _enqueue(self, task: store.Task) -> None:
        with self._lock:
            self._queue.append((task.created_ts, task.profile_id, task.task_id))
            self._queue.sort()
        self._dispatch()

    def _dispatch(self) -> None:
        with self._lock:
            if self._stopping:
                return
            waiting: list[tuple[float, str, str]] = []
            for item in self._queue:
                _, profile_id, task_id = item
                try:
                    task = store.get_task(task_id, profile_id=profile_id)
                except store.TaskNotFound:
                    continue
                key = self._lock_key(task)
                if key in self._busy or self._running >= self.workers:
                    waiting.append(item)
                    continue
                self._busy.add(key)
                self._running += 1
                control = self._controls.setdefault(task_id, _Control())
                thread = threading.Thread(
                    target=self._thread_main, args=(profile_id, task_id, key),
                    name=f"kriya-{task_id}", daemon=True,
                )
                control.thread = thread
                thread.start()
            self._queue = waiting

    def _thread_main(self, profile_id: str, task_id: str, key: str) -> None:
        try:
            with profile_scope(profile_id):
                self._execute(profile_id, task_id)
        except Exception:
            log.exception("Kriya: task %s crashed", task_id)
        finally:
            with self._lock:
                self._busy.discard(key)
                self._running -= 1
                self._controls.pop(task_id, None)
            self._dispatch()

    # ── the task ─────────────────────────────────────────────────────────────

    def _execute(self, profile_id: str, task_id: str) -> None:
        task = store.get_task(task_id, profile_id=profile_id)
        control = self._controls.setdefault(task_id, _Control())
        if task.cancel_requested or control.cancel.is_set():
            self._finish(task, "cancelled", "Stopped before it started.", reason="cancelled")
            return
        resumed_status = task.status
        history = [
            f"{event['step']}. {event['data']['line']}"
            for event in store.list_events(task_id, profile_id=profile_id, limit=400)
            if event["kind"] == "step" and event["data"].get("line")
        ]
        usage = dict(task.usage or {})
        opening = {"phone": "Starting on the phone", "desktop": "Opening the window"}.get(
            task.surface, "Opening the page"
        )
        store.update_task(
            task_id, profile_id=profile_id,
            status="running" if resumed_status in {"queued", "running"} else resumed_status,
            started_ts=task.started_ts or time.time(),
            detail=opening if resumed_status == "queued" else task.detail,
        )
        if resumed_status == "queued":
            store.add_event(task_id, profile_id=profile_id, kind="started", summary="Started")
        operator = None
        surface = None
        try:
            if task.surface == "phone":
                from kriya import phone

                phone.run(self, store.get_task(task_id, profile_id=profile_id), control, resumed_status)
                raise _Stop("failed", "The phone task ended without a result.")
            operator = self._operator_factory(task)
            usage.setdefault("model", getattr(operator, "model", ""))
            while True:
                surface = self._surface_factory(store.get_task(task_id, profile_id=profile_id))
                control.surface = surface
                try:
                    self._open(task, surface)
                    self._loop(task_id, profile_id, surface, operator, control, history, usage, resumed_status)
                    break
                except _Migrate as move:
                    resumed_status = "running"
                    url = surface.url or task.last_url or task.start_url
                    surface.close()
                    task = store.update_task(task_id, profile_id=profile_id, surface="browser", last_url=url)
                    store.add_event(task_id, profile_id=profile_id, kind="moved",
                                    summary=f"{move} Continuing in the browser on the Mac.")
        except _Stop as stop:
            self._finish(store.get_task(task_id, profile_id=profile_id), stop.status, stop.summary,
                         answer=stop.answer, reason=stop.reason, usage=usage, surface=surface,
                         extra=stop.extra)
        except _Suspended:
            store.update_task(task_id, profile_id=profile_id, usage=usage)
        except Exception as exc:
            if self._stopping:
                store.update_task(task_id, profile_id=profile_id, usage=usage)
            else:
                log.exception("Kriya: task %s failed", task_id)
                self._finish(store.get_task(task_id, profile_id=profile_id), "failed",
                             f"Something went wrong: {_short(exc)}", usage=usage, surface=surface)
        finally:
            control.surface = None
            if surface is not None:
                surface.close()
            if operator is not None:
                operator.close()

    def _open(self, task: store.Task, surface: Any) -> None:
        from computer_use_skill import NavigationRefused

        current = store.get_task(task.task_id, profile_id=task.profile_id)
        start = current.last_url or current.start_url
        try:
            surface.open(start)
        except NavigationRefused as exc:
            raise _Stop("failed", str(exc)) from exc
        except ValueError as exc:
            raise _Stop("failed", f"That address cannot be opened: {exc}") from exc

    def _loop(
        self,
        task_id: str,
        profile_id: str,
        surface: Any,
        operator: Any,
        control: _Control,
        history: list[str],
        usage: dict[str, Any],
        resumed_status: str,
    ) -> None:
        task = store.get_task(task_id, profile_id=profile_id)
        last_result = ""
        if resumed_status == "waiting_approval" and task.pending and task.proposal_id:
            outcome = self._await_approval(task, surface, control, task.pending, usage)
            history.append(f"{task.step}. {outcome.line}")
            last_result = outcome.line
        elif resumed_status == "waiting_help":
            help_info = task.help or {}
            self._wait_for_help(task, surface, control, str(help_info.get("kind") or "login"),
                                str(help_info.get("reason") or task.detail), notify=False)
        observation = self._observe(surface, usage)
        done_checked = False
        while True:
            self._checkpoint(task_id, profile_id, control)
            observation = self._page_gate(task_id, profile_id, surface, control, observation, history, usage)
            task = store.get_task(task_id, profile_id=profile_id)
            if task.step >= task.max_steps:
                raise _Stop("failed", f"Stopped after {task.max_steps} steps without finishing.")
            context = OperatorContext(
                goal=task.goal, done_when=task.done_when, step=task.step, max_steps=task.max_steps,
                history=history, last_result=last_result, observation=observation,
            )
            decision = self._decide(task, operator, context, usage)
            self._checkpoint(task_id, profile_id, control)
            if decision.finish == "done":
                problem = _done_when_unmet(task.done_when, observation)
                if problem and not done_checked:
                    done_checked = True
                    last_result = f"Not finished yet: {problem}"
                    continue
                raise _Stop("done", decision.summary or "Done.", answer=decision.answer)
            if decision.finish == "fail":
                raise _Stop("failed", decision.reason or "It could not be finished.")
            if decision.finish == "ask_help":
                observation = self._wait_for_help(
                    task, surface, control, "operator", decision.reason or "Narad needs you on this page."
                )
                last_result = "The person has done what you asked for on the page; continue."
                continue
            step = task.step + 1
            first = decision.actions[0]
            store.update_task(
                task_id, profile_id=profile_id, step=step,
                detail=decision.note or describe(first, observation.refs.get(str(first.get("ref") or ""))),
            )
            lines: list[str] = []
            for index, action in enumerate(decision.actions):
                self._checkpoint(task_id, profile_id, control)
                outcome = self._do_action(task, surface, control, action, observation, step, usage)
                observation = outcome.observation
                lines.append(outcome.line)
                history.append(f"{step}. {outcome.line}")
                remaining = len(decision.actions) - index - 1
                if remaining and outcome.status != "ok":
                    lines.append(f"({remaining} more planned action(s) not done)")
                    break
                if remaining and outcome.page_changed:
                    lines.append(f"(the page changed, so {remaining} more planned action(s) were skipped)")
                    break
            last_result = "; ".join(lines)
            store.update_task(task_id, profile_id=profile_id, last_url=surface.url, usage=usage)

    # ── one action ───────────────────────────────────────────────────────────

    def _do_action(
        self,
        task: store.Task,
        surface: Any,
        control: _Control,
        action: dict[str, Any],
        observation: Observation,
        step: int,
        usage: dict[str, Any],
    ) -> StepOutcome:
        action, node, problem = _ground(action, observation)
        if problem:
            line = f"{describe(action, node)} → not done: {problem}"
            self._step_event(task, step, line, "failed")
            return StepOutcome("failed", line, observation)
        if surface.kind == "cloud_browser" and action["action"] in _INPUT_ACTIONS:
            value = str(action.get("value") or "")
            if _personal(value) or (node is not None and _SECRET_FIELD.search(node.name)):
                raise _Migrate("This step types personal details, which never go to the cloud browser.")
        verdict, details = self._classify(surface, action, node, observation)
        if verdict.needs_approval:
            return self._approval_step(task, surface, control, action, node, verdict, details, observation, step,
                                       usage)
        return self._perform(task, surface, action, node, observation, step, usage)

    def _classify(self, surface: Any, action: dict[str, Any], node: Node | None, observation: Observation):
        from risk_policy import classify_browser_action

        policy_action = {key: value for key, value in action.items() if key not in {"ref", "expect"}}
        if node is not None:
            policy_action["target"] = {"name": node.name, "role": node.role}
        details = None
        if action.get("ref") and action["action"] in {"click", "fill", "type", "select", "check", "uncheck",
                                                        "press"}:
            details = surface.element_details(str(action["ref"]))
        verdict = classify_browser_action(policy_action, details, injection=bool(observation.injection),
                                          environment=getattr(surface, "environment", "browser"))
        return verdict, details

    def _perform(
        self,
        task: store.Task,
        surface: Any,
        action: dict[str, Any],
        node: Node | None,
        observation: Observation,
        step: int,
        usage: dict[str, Any],
        *,
        retry: bool = True,
        prefix: str = "",
    ) -> StepOutcome:
        started = time.monotonic()
        result = surface.execute(action)
        fresh = self._observe(surface, usage)
        usage["act_ms"] = int(usage.get("act_ms", 0)) + int((time.monotonic() - started) * 1000)
        usage["actions"] = int(usage.get("actions", 0)) + 1
        verified, why = _verify(action, node, result, fresh)
        text = prefix + describe(action, node)
        changed = result.url_after != result.url_before
        if result.status == "refused":
            line = f"{text} → refused: {result.error}"
            self._step_event(task, step, line, "refused")
            return StepOutcome("refused", line, fresh, True)
        if verified:
            line = f"{text} → {_effect_words(action, result, fresh)}"
            if getattr(result, "note", ""):
                line += f" ({result.note})"
            self._step_event(task, step, line, "ok")
            return StepOutcome("ok", line, fresh, changed)
        if retry:
            again, again_node = _reground(action, node, fresh)
            if again is not None:
                usage["retries"] = int(usage.get("retries", 0)) + 1
                store.add_event(task.task_id, profile_id=task.profile_id, kind="retry", step=step,
                                summary=f"{text}: {why or result.error}; trying once more")
                return self._perform(task, surface, again, again_node, fresh, step, usage, retry=False,
                                     prefix="(retried) ")
        status = "no_effect" if result.status == "ok" else "failed"
        line = f"{text} → did not work: {why or result.error}"
        self._step_event(task, step, line, status)
        return StepOutcome(status, line, fresh, changed)

    def _step_event(self, task: store.Task, step: int, line: str, status: str) -> None:
        store.add_event(task.task_id, profile_id=task.profile_id, kind="step", step=step, summary=line,
                        data={"line": line, "status": status})

    # ── approvals ────────────────────────────────────────────────────────────

    def _approval_step(
        self,
        task: store.Task,
        surface: Any,
        control: _Control,
        action: dict[str, Any],
        node: Node | None,
        verdict: Any,
        details: dict[str, Any] | None,
        observation: Observation,
        step: int,
        usage: dict[str, Any],
    ) -> StepOutcome:
        from computer_use_skill import _media_path, _steps_summary

        import anumati
        from risk_policy import element_label

        label = element_label(details) or (node.name if node is not None else "")
        page_url = surface.url
        exact = {key: value for key, value in action.items() if key != "expect"}
        args = {
            "task_id": task.task_id,
            "step": step,
            "page_url": page_url,
            "actions": [exact],
            "target_label": label or None,
        }
        policy_like = {**exact, "target": {"name": label}} if label else exact
        desktop = surface.kind == "desktop"
        summary = _steps_summary([policy_like], [label or None],
                                 f"the Mac ({observation.title})" if desktop else _host(page_url))
        target = f"{task.task_id} @ {page_url}"[:500]
        screenshot = surface.screenshot_file("approval")
        proposal, _created = anumati.propose(
            surface="task",
            action=verdict.category,
            target=target,
            args=args,
            summary=summary,
            risk_class=verdict.category,
            preview={
                "kind": "browser",
                "signed_in": False,
                "desktop": desktop,
                "page_url": "" if desktop else page_url,
                "page_title": f"The Mac: {observation.title}" if desktop else observation.title,
                "screenshot_url": _media_path(screenshot),
                "reason": verdict.reason,
                "warning": (
                    "This page contains text that tries to instruct Narad. Check it before approving."
                    if observation.injection else None
                ),
                "task_id": task.task_id,
                "task_goal": task.goal[:200],
            },
            session_id=task.session_id,
            profile_id=task.profile_id,
        )
        pending = {
            "action": verdict.category,
            "target": target,
            "args": args,
            "summary": summary,
            "node": {"role": node.role, "name": node.name} if node is not None else None,
            # A check, not part of what runs: kept out of the hash, used to verify the step.
            "expect": action.get("expect") if isinstance(action.get("expect"), dict) else None,
        }
        usage["approvals"] = int(usage.get("approvals", 0)) + 1
        task = store.update_task(
            task.task_id, profile_id=task.profile_id, status="waiting_approval",
            proposal_id=proposal.proposal_id, pending=pending, detail=f"Waiting for your OK: {summary}",
            last_url=page_url,
        )
        store.add_event(task.task_id, profile_id=task.profile_id, kind="approval_requested", step=step,
                        summary=f"Asked for your OK: {summary}", data={"proposal_id": proposal.proposal_id})
        return self._await_approval(task, surface, control, pending, usage)

    def _await_approval(
        self, task: store.Task, surface: Any, control: _Control, pending: dict[str, Any], usage: dict[str, Any]
    ) -> StepOutcome:
        import anumati

        proposal_id = str(task.proposal_id)
        summary = str(pending.get("summary") or "the step")
        while True:
            self._checkpoint(task.task_id, task.profile_id, control, proposal_id=proposal_id)
            try:
                proposal = anumati.get(proposal_id, profile_id=task.profile_id)
            except anumati.ProposalNotFound as exc:
                raise _Stop("failed", "The approval for this step disappeared; nothing was done.") from exc
            if proposal.status == "pending":
                control.wake.wait(self.poll_s)
                control.wake.clear()
                continue
            if proposal.status == "approved":
                gate = anumati.check(
                    surface="task", action=pending["action"], target=pending["target"], args=pending["args"],
                    profile_id=task.profile_id,
                )
                if gate is None:
                    control.wake.wait(self.poll_s)
                    continue
                store.update_task(task.task_id, profile_id=task.profile_id, status="running", proposal_id=None,
                                  pending=None, detail=f"Approved: {summary}")
                store.add_event(task.task_id, profile_id=task.profile_id, kind="approved",
                                step=int(pending["args"].get("step") or 0), summary=f"You approved: {summary}")
                if not gate.approved:  # the identical step already ran after an approval
                    observation = self._observe(surface, usage)
                    return StepOutcome("ok", f"{summary} → already done earlier", observation)
                return self._run_approved(task, surface, gate.proposal, pending, usage)
            if proposal.status == "rejected":
                why = f" ({proposal.decision_reason})" if proposal.decision_reason else ""
                raise _Stop("cancelled", f"Stopped before this step: {summary}. You declined it{why}.",
                            reason="rejected")
            if proposal.status == "expired":
                store.update_task(task.task_id, profile_id=task.profile_id, proposal_id=None, pending=None)
                store.add_event(task.task_id, profile_id=task.profile_id, kind="approval_expired",
                                summary=f"The approval expired: {summary}")
                observation = self._wait_for_help(
                    task, surface, control, "approval_expired",
                    f"Your OK for “{summary}” expired. Tap Continue and Narad will ask again.",
                )
                line = f"{summary} → not done: the approval expired; ask again if it is still needed"
                return StepOutcome("blocked", line, observation)
            raise _Stop("failed", f"The approval ended as {proposal.status}; nothing more was done.")

    def _run_approved(
        self, task: store.Task, surface: Any, proposal: Any, pending: dict[str, Any], usage: dict[str, Any]
    ) -> StepOutcome:
        from computer_use_skill import _dharma_gate

        import anumati

        args = pending["args"]
        action = dict(args["actions"][0])
        if pending.get("expect"):
            action["expect"] = dict(pending["expect"])
        node_info = pending.get("node") or {}
        node = Node(role=str(node_info.get("role") or ""), name=str(node_info.get("name") or ""),
                    ref=str(action.get("ref") or "")) if node_info else None
        step = int(args.get("step") or task.step)
        mismatch = surface.approved_mismatch(str(args["page_url"]), str(action.get("ref") or ""),
                                             str(args.get("target_label") or ""))
        if mismatch:
            anumati.record_result(proposal.proposal_id, {"status": "blocked", "summary": mismatch},
                                  profile_id=task.profile_id)
            line = f"{pending['summary']} → {mismatch}"
            self._step_event(task, step, line, "blocked")
            return StepOutcome("blocked", line, self._observe(surface, usage))
        refusal = _dharma_gate(
            getattr(surface, "dharma_action", "browser_submit"),
            f"{action['action']} on {str(args['page_url'])[:180]}",
            {"task_id": task.task_id, "action": action["action"]},
        )
        if refusal:
            anumati.record_result(proposal.proposal_id, {"status": "blocked", "summary": refusal},
                                  profile_id=task.profile_id)
            raise _Stop("failed", f"Policy refused this step: {refusal}")
        try:
            # Never retried: a second click could pay or send twice.
            outcome = self._perform(task, surface, action, node, self._observe(surface, usage), step, usage,
                                    retry=False)
        except Exception as exc:
            anumati.record_result(proposal.proposal_id, {"status": "error", "summary": _short(exc)},
                                  profile_id=task.profile_id)
            raise
        anumati.record_result(
            proposal.proposal_id,
            {"status": "ok" if outcome.status == "ok" else "error", "summary": outcome.line, "url": surface.url,
             "task_id": task.task_id},
            profile_id=task.profile_id,
        )
        return outcome

    # ── help ─────────────────────────────────────────────────────────────────

    def _wait_for_help(
        self, task: store.Task, surface: Any, control: _Control, kind: str, reason: str, *, notify: bool = True
    ) -> Observation:
        host = _host(surface.url)
        titles = {
            "login": f"Sign in needed on {host}",
            "captcha": f"A captcha on {host}",
            "approval_expired": "An approval expired",
        }
        store.update_task(
            task.task_id, profile_id=task.profile_id, status="waiting_help",
            help={"kind": kind, "reason": reason, "since": _iso_now(), "url": surface.url},
            detail=reason, last_url=surface.url,
        )
        store.add_event(task.task_id, profile_id=task.profile_id, kind="help_needed", summary=reason,
                        data={"help": kind})
        # A sign-in that did not take pauses again; the phone hears once per 10 minutes.
        if notify and time.monotonic() - control.notified.get(kind, -1e9) > 600:
            control.notified[kind] = time.monotonic()
            _deliver(
                user_id=task.profile_id,
                kind="question",
                title=f"Narad needs your help: {titles.get(kind, 'a task is waiting')}",
                body=f"{reason} Open the task to finish it on the live view, then tap Continue.",
                data={"task_id": task.task_id, "url": f"/?task={task.task_id}"},
                priority="high",
                source="kriya.help",
            )
        control.resume.clear()
        deadline = time.monotonic() + self.help_timeout_s
        while not control.resume.is_set():
            self._checkpoint(task.task_id, task.profile_id, control)
            if time.monotonic() > deadline:
                raise _Stop("failed", f"Nobody came back to help within {int(self.help_timeout_s // 60)} minutes.")
            control.wake.wait(self.poll_s)
            control.wake.clear()
        control.resume.clear()
        store.update_task(task.task_id, profile_id=task.profile_id, status="running", help=None,
                          detail="Continuing after your help")
        store.add_event(task.task_id, profile_id=task.profile_id, kind="resumed", summary="You tapped Continue")
        return surface.observe()

    def _page_gate(
        self,
        task_id: str,
        profile_id: str,
        surface: Any,
        control: _Control,
        observation: Observation,
        history: list[str],
        usage: dict[str, Any],
    ) -> Observation:
        state = observation.state
        if state.refused and not history:
            raise _Stop("failed", state.refused)
        guard = 0
        while (state.login or state.captcha) and guard < 20:
            guard += 1
            if surface.kind == "cloud_browser":
                raise _Migrate(f"{_host(surface.url)} asks for a sign-in, which never happens in the cloud browser.")
            kind = "captcha" if state.captcha else "login"
            reason = (
                f"{_host(surface.url)} shows a captcha. Solve it on the live view, then tap Continue."
                if kind == "captcha" else
                f"{_host(surface.url)} asks you to sign in. Sign in on the live view, then tap Continue."
            )
            task = store.get_task(task_id, profile_id=profile_id)
            observation = self._wait_for_help(task, surface, control, kind, reason)
            state = observation.state
        return observation

    # ── operator ─────────────────────────────────────────────────────────────

    def _decide(self, task: store.Task, operator: Any, context: OperatorContext, usage: dict[str, Any]) -> Decision:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                note = "" if attempt == 0 else (
                    f"Your last reply could not be used ({_short(last_error)}). Reply with one JSON object only."
                )
                decision = operator.decide(context, attempt_note=note)
            except OperatorError as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(min(2.0, self.poll_s * 4))
                    continue
                raise _Stop("failed", f"The operator model could not be reached: {_short(exc)}") from exc
            usage["operator_calls"] = int(usage.get("operator_calls", 0)) + 1
            usage["prompt_tokens"] = int(usage.get("prompt_tokens", 0)) + decision.prompt_tokens
            usage["completion_tokens"] = int(usage.get("completion_tokens", 0)) + decision.completion_tokens
            usage["operator_ms"] = int(usage.get("operator_ms", 0)) + decision.latency_ms
            if decision.used_screenshot:
                usage["screenshots"] = int(usage.get("screenshots", 0)) + 1
            return decision
        raise _Stop("failed", f"The operator's answers could not be used: {_short(last_error)}")

    def _observe(self, surface: Any, usage: dict[str, Any]) -> Observation:
        observation = surface.observe()
        usage["observations"] = int(usage.get("observations", 0)) + 1
        usage["max_observation_chars"] = max(int(usage.get("max_observation_chars", 0)), observation.chars)
        usage["observation_chars"] = int(usage.get("observation_chars", 0)) + observation.chars
        return observation

    # ── control ──────────────────────────────────────────────────────────────

    def _checkpoint(self, task_id: str, profile_id: str, control: _Control, *, proposal_id: str = "") -> None:
        if self._stopping:
            raise _Suspended()
        if control.cancel.is_set() or store.get_task(task_id, profile_id=profile_id).cancel_requested:
            if proposal_id:
                import anumati

                anumati.supersede(proposal_id, profile_id=profile_id, reason="The task was stopped")
            raise _Stop("cancelled", "Stopped from the phone.", reason="cancelled")

    def _finish(
        self,
        task: store.Task,
        status: str,
        summary: str,
        *,
        answer: str = "",
        reason: str = "",
        usage: dict[str, Any] | None = None,
        surface: Any = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if not task.active:
            return
        url = (surface.url if surface is not None and surface.is_open else "") or task.last_url
        result = {"summary": summary, "answer": answer, "reason": reason or status, "url": url, **(extra or {})}
        changes: dict[str, Any] = {
            "status": status, "detail": summary, "result": result, "finished_ts": time.time(),
            "proposal_id": None, "pending": None, "help": None, "last_url": url,
        }
        if usage is not None:
            changes["usage"] = usage
        # The closing event is written first: whoever sees the final status
        # (the phone polling, a waiter) also sees the step that ended it.
        store.add_event(task.task_id, profile_id=task.profile_id, kind=status, summary=summary,
                        data={"answer": answer} if answer else None)
        store.update_task(task.task_id, profile_id=task.profile_id, **changes)
        words = {"done": "Done", "failed": "Could not finish", "cancelled": "Stopped"}[status]
        body = f"{summary} {answer}".strip()
        if status in {"done", "failed"}:
            _deliver(
                user_id=task.profile_id,
                kind="task_done",
                title=f"{words}: {task.goal[:70]}",
                body=body,
                data={"task_id": task.task_id, "status": status, "url": f"/?task={task.task_id}"},
                priority="default",
                source="kriya.finish",
                summary=f"{words}: {task.goal[:80]}",
            )
        _thread_note(task, f"[Task] {words}: {task.goal[:120]}. {body}", status)


# ── helpers ──────────────────────────────────────────────────────────────────


def _short(exc: Any) -> str:
    return " ".join(str(exc or "").split())[:240] or type(exc).__name__


def _ground(action: dict[str, Any], observation: Observation) -> tuple[dict[str, Any], Node | None, str]:
    """Resolve an action's target on the current page: a ref, or a role and name."""
    action = dict(action)
    kind = action["action"]
    if kind == "navigate":
        from kriya.browser import validate_start_url

        try:
            action["url"] = validate_start_url(str(action.get("url") or ""))
        except ValueError as exc:
            return action, None, str(exc)
        return action, None, ""
    ref = str(action.get("ref") or "").strip()
    target = action.get("target") if isinstance(action.get("target"), dict) else {}
    role = str(action.get("role") or target.get("role") or "")
    name = str(action.get("name") or target.get("name") or action.get("label") or "")
    node = observation.refs.get(ref) if ref else None
    if node is None and (role or name):
        found = observation.find(role, name)
        node = observation.refs.get(found) if found else None
    if node is not None:
        action["ref"] = node.ref
        return action, node, ""
    if kind in _REF_ACTIONS:
        if ref:
            return action, None, f"{ref} is not on the current page"
        return action, None, "no ref was given"
    return action, None, ""


def _reground(action: dict[str, Any], node: Node | None, observation: Observation):
    """The same control on a fresh observation: its ref if it is still there,
    else the control with the same role and name. None: nothing to retry."""
    if action["action"] in {"scroll", "wait", "back", "navigate"}:
        return None, None
    if node is None:
        return (dict(action), None) if action["action"] == "press" and not action.get("ref") else (None, None)
    if node.ref in observation.refs and observation.refs[node.ref].role == node.role:
        return dict(action), observation.refs[node.ref]
    found = observation.find(node.role, node.name)
    if not found:
        return None, None
    return {**action, "ref": found}, observation.refs[found]


def _verify(action: dict[str, Any], node: Node | None, result: Any, observation: Observation) -> tuple[bool, str]:
    """A deterministic check that the step did what it was for."""
    if result.status != "ok":
        return False, result.error
    kind = action["action"]
    expect = action.get("expect") if isinstance(action.get("expect"), dict) else {}
    url_part = str(expect.get("url_contains") or "")
    if url_part and url_part not in result.url_after:
        return False, f"the address does not contain {url_part!r}"
    appears = str(expect.get("text_appears") or "")
    if appears and not observation.has_text(appears):
        return False, f"{appears!r} did not appear"
    gone = str(expect.get("text_gone") or "")
    if gone and observation.has_text(gone):
        return False, f"{gone!r} is still on the page"
    value = str(action.get("value") if action.get("value") is not None else action.get("text") or "")
    if kind in {"fill", "type"} and result.value is not None and value.strip():
        if value.strip().lower() not in str(result.value).lower():
            return False, "the field did not keep the text"
    if kind == "select" and result.value is not None and value.strip():
        if value.strip().lower() not in str(result.value).lower():
            return False, "that option was not selected"
    if kind in {"check", "uncheck"} and result.checked is not None and result.checked != (kind == "check"):
        return False, "the box did not change"
    if kind in {"click", "press"} and not (url_part or appears or gone):
        focus_only = node is not None and node.role in FIELD_ROLES
        if not result.effect and not result.dialog and not focus_only:
            return False, "nothing on the page changed"
    if kind == "download" and not result.effect:
        return False, "no file arrived"
    return True, ""


def _effect_words(action: dict[str, Any], result: Any, observation: Observation) -> str:
    kind = action["action"]
    if result.dialog:
        return f"ok; the page asked \"{result.dialog[:80]}\" and Narad dismissed it"
    if result.url_after != result.url_before:
        return f"ok, now on {_host(result.url_after)}{_path(result.url_after)}"
    if kind in {"fill", "type", "select"}:
        return "ok, value set"
    if kind in {"check", "uncheck"}:
        return "ok"
    if kind == "scroll":
        return "ok" if result.effect else "ok, already at the end of the page"
    if kind == "download":
        return "ok, file saved"
    return "ok, the page changed"


def _path(url: str) -> str:
    from urllib.parse import urlparse

    path = urlparse(url).path
    return path if path not in {"", "/"} else ""


def _done_when_unmet(done_when: str, observation: Observation) -> str:
    """Deterministic checks for ``url: ...`` and ``text: ...`` done conditions."""
    problems: list[str] = []
    for clause in filter(None, (item.strip() for item in re.split(r"[;\n]", done_when or ""))):
        key, _, value = clause.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "url" and value and value not in observation.url:
            problems.append(f"the address does not contain {value!r} yet")
        elif key == "text" and value and not observation.has_text(value):
            problems.append(f"{value!r} is not on the page yet")
    return "; ".join(problems)


def _thread_note(task: store.Task, text: str, status: str) -> None:
    session_id = task.session_id
    if not session_id or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", session_id):
        return
    try:
        from conversation_memory import append_turn, load_thread

        if not load_thread(task.profile_id, session_id, limit=1):
            return
        append_turn(
            user_id=task.profile_id,
            session_id=session_id,
            role="assistant",
            text=text,
            metadata={"kind": "task_result", "task_id": task.task_id, "status": status},
        )
    except Exception as exc:
        log.warning("Kriya: chat thread note failed: %s", exc)


_RUNTIME: TaskRuntime | None = None
_RUNTIME_LOCK = threading.Lock()


def runtime() -> TaskRuntime:
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            _RUNTIME = TaskRuntime()
        return _RUNTIME


def set_runtime(value: TaskRuntime | None) -> None:
    """Swap the process runtime (tests and the Pariksha runner)."""
    global _RUNTIME
    with _RUNTIME_LOCK:
        _RUNTIME = value
