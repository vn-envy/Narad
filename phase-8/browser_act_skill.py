"""Backward-compatible form helpers on Narad's persistent browser runtime.

These functions retain Matsya's original public API while sharing the browser
session returned by browser_screenshot. New workflows should prefer the broader
computer_use tool directly.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from computer_use_skill import computer_use

_BLOCKED_SUBMIT_DOMAINS = {
    "bankofamerica.com",
    "chase.com",
    "wellsfargo.com",
    "citibank.com",
    "irs.gov",
    "ssa.gov",
    "healthcare.gov",
    "cms.gov",
}


def _domain_is_blocked(url: str) -> bool:
    domain = (urlparse(url).hostname or "").lower().rstrip(".").removeprefix("www.")
    return any(domain == blocked or domain.endswith(f".{blocked}") for blocked in _BLOCKED_SUBMIT_DOMAINS)


def _target(query: str) -> dict[str, str]:
    return {"query": str(query)}


def _legacy_fields(payload: dict[str, Any], *, dry_run: bool | None = None) -> dict[str, Any]:
    """Add legacy aliases without discarding the standard tool envelope."""
    result = dict(payload)
    observation = result.get("observation") or {}
    action_results = result.get("action_results") or []
    result.update({
        "run_id": result.get("session_id", ""),
        "page_title": observation.get("title", ""),
        "fields_detected": observation.get("fields", []),
        "field_count": len(observation.get("fields", [])),
        "screenshot_path": observation.get("screenshot_path"),
        "message": result.get("summary", ""),
    })
    if dry_run is not None:
        result["dry_run"] = dry_run
    if action_results:
        result["submitted"] = any(
            item.get("action") == "submit" and item.get("status") == "ok"
            for item in action_results
        )
    return result


def browser_screenshot(url: str, session_id: str = "") -> dict[str, Any]:
    """Open or continue an isolated browser session and inspect the current page.

    Args:
        url: Full http:// or https:// URL to inspect.
        session_id: Existing id to continue. Empty creates a persistent session;
            pass its returned session_id to every later form call.
    """
    payload = computer_use(
        task=f"Inspect the form at {url}",
        start_url="" if session_id else url,
        session_id=session_id,
        actions=[],
        environment="browser",
        dry_run=False,
    )
    return _legacy_fields(payload)


def browser_fill(
    url: str,
    fields: dict[str, Any],
    dry_run: bool = True,
    session_id: str = "",
    confirmed: bool = False,
) -> dict[str, Any]:
    """Fill fields in one browser session and optionally submit.

    dry_run=True changes only the in-page form state and returns a screenshot.
    It does not submit. dry_run=False fills the fields, then stops at the
    submit with status "needs_approval": the person sees the filled form on an
    approval card on their phone, and Narad submits it when they tap Approve.
    ``confirmed`` is accepted for compatibility and approves nothing.
    """
    if not isinstance(fields, dict):
        message = "fields must be an object"
        return {"status": "error", "summary": message, "message": message}
    if not dry_run and _domain_is_blocked(url):
        message = f"Submission is blocked for sensitive domain {urlparse(url).hostname}."
        return {
            "status": "blocked",
            "summary": message,
            "message": message,
            "requires_confirmation": False,
        }

    actions: list[dict[str, Any]] = [
        {"action": "set_field", "target": _target(name), "value": value}
        for name, value in fields.items()
    ]
    if not dry_run:
        actions.append({"action": "submit", "intent": "submit form"})

    payload = computer_use(
        task=f"{'Preview' if dry_run else 'Submit'} the form at {url}",
        start_url="" if session_id else url,
        session_id=session_id,
        actions=actions,
        environment="browser",
        # Filling is a reversible page-local preview, so execute it. The submit
        # waits for the person's Anumati approval inside computer_use.
        dry_run=False,
        confirmed=confirmed,
    )
    return _legacy_fields(payload, dry_run=dry_run)


def browser_upload_and_submit(
    url: str,
    fields: dict[str, Any],
    file_uploads: dict[str, str],
    session_id: str = "",
    confirmed: bool = False,
) -> dict[str, Any]:
    """Fill, then upload and submit once the person approves on their phone.

    Always pass the session_id returned by browser_screenshot/browser_fill so
    the reviewed page, cookies, and field state remain intact. The fields are
    filled as a page-local preview and the batch stops before any file is
    uploaded, with status "needs_approval"; Narad uploads and submits exactly
    this when the person taps Approve. ``confirmed`` is accepted for
    compatibility and approves nothing.
    """
    if _domain_is_blocked(url):
        message = f"Submission is blocked for sensitive domain {urlparse(url).hostname}."
        return {
            "status": "blocked",
            "summary": message,
            "message": message,
            "requires_confirmation": False,
        }

    actions: list[dict[str, Any]] = [
        {"action": "set_field", "target": _target(name), "value": value}
        for name, value in fields.items()
    ]
    actions.extend(
        {"action": "upload", "target": _target(selector), "path": path}
        for selector, path in file_uploads.items()
    )
    actions.append({"action": "submit", "intent": "upload files and submit form"})
    payload = computer_use(
        task=f"Upload files and submit the form at {url}",
        start_url="" if session_id else url,
        session_id=session_id,
        actions=actions,
        environment="browser",
        # Upload and submit wait for the person's approval inside computer_use.
        dry_run=False,
        confirmed=confirmed,
    )
    result = _legacy_fields(payload, dry_run=False)
    result["files_uploaded"] = [
        item.get("action")
        for item in result.get("action_results", [])
        if item.get("action") == "upload" and item.get("status") == "ok"
    ]
    return result
