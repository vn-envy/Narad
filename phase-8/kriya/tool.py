"""``start_task``: how an avatar hands a multi-step errand to Kriya (web, phone, Mac desktop)."""

from __future__ import annotations

import re
import sys
from typing import Any

from profile_context import current_profile_id, validate_profile_id
from tool_result import envelope

_SESSION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _origin_session_id() -> str | None:
    """The chat thread this tool call belongs to, when it runs inside a turn."""
    agents = sys.modules.get("avatar_agents")
    context = getattr(agents, "_http_session_id_ctx", None)
    value = context.get("") if context is not None else ""
    return value if value and _SESSION_RE.fullmatch(value) else None


def start_task(
    goal: str,
    start_url: str = "",
    surface: str = "browser",
    done_when: str = "",
    device: str = "",
    app: str = "",
) -> dict[str, Any]:
    """Start a multi-step errand that runs by itself in the background.

    Use this for anything that takes more than one or two browser actions:
    searching and comparing on a site, filling a form, booking, finding
    something on a long page. It returns at once with a task id; the person
    sees a task card with a live view of the page and a Stop button, and gets
    a notification when it finishes. Steps that pay, book, send or submit
    wait for their approval on their phone automatically. Use computer_use
    only for a single quick look or action.

    Args:
        goal: The errand in plain words, with every detail the site will need
            that the person gave you (names, dates, cities, limits such as
            "under 6000 rupees"). Never invent personal details.
        start_url: Where to begin (https://...). Leave empty to let the task
            find the site itself.
        surface: "browser" (default; the task runtime picks the Mac's own
            isolated browser or, for public errands with no personal data,
            the cloud browser); "phone" for a task on the person's own
            Android phone (Artemis, at home over ADB: read a message, change
            a setting, use an app); "desktop" for a task in the Mac's own
            apps (the owner only; every click or keystroke waits for their OK).
        done_when: Optional finish condition in plain words, or exact checks
            such as "text: Booking confirmed" or "url: /confirmation".
        device: Phone tasks only: which of the person's phones, when they
            have more than one granted. Leave empty otherwise.
        app: Phone tasks only: an Android package to keep the task inside
            (e.g. "com.whatsapp"). Leave empty to let the task choose.

    Returns:
        status "task_started" with ``task`` (id, status, goal). Tell the person
        in one sentence that it is running and they can watch or stop it on the
        task card. Do not start the same errand again and do not drive the same
        site with computer_use while it runs.
    """
    from kriya.runtime import runtime

    try:
        profile_id = validate_profile_id(current_profile_id())
        task = runtime().submit(
            profile_id=profile_id,
            goal=goal,
            start_url=start_url,
            done_when=done_when,
            surface=surface,
            session_id=_origin_session_id(),
            options={"device": device, "app_scope": app} if str(surface).strip().lower() == "phone" else None,
        )
    except (ValueError, PermissionError) as exc:
        return envelope(status="error", summary=str(exc), error="task_not_started")
    from kriya.runtime import where

    payload = task.to_payload()
    summary = (
        f"Started a background task on {where(task)}: {task.goal[:160]}. The person can watch it live and "
        "stop it on the task card; steps that pay, book, send or submit will wait for their OK on the phone."
    )
    return envelope(
        status="task_started",
        summary=summary,
        provenance={"engine": "kriya", "task_id": task.task_id, "surface": task.surface},
        task=payload,
        task_id=task.task_id,
    )
