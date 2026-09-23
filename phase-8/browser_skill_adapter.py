"""Narrow BrowserSkill adapter for profile-scoped, signed-in Chromium use.

The adapter deliberately exposes only typed browser operations. It never passes
shell text through to ``bsk`` and does not expose BrowserSkill's JavaScript
``evaluate`` command.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from computer_use_skill import _validate_url

from narad_config import ARTIFACTS_DIR
from profile_context import current_profile_id, validate_profile_id

_REF_RE = re.compile(r"@?(e\d+)\b")
# VOM renders one ref per line: ``@e5 button "Send" [ctx: ...] placeholder="..."``.
_REF_LINE_RE = re.compile(r'^\s*@(e\d+)\s+([^\s"\[]+)(?:\s+("(?:[^"\\]|\\.)*"))?(.*)$')
_PLACEHOLDER_RE = re.compile(r'\bplaceholder=("(?:[^"\\]|\\.)*")')
_EFFECT_STATES = frozenset({"none", "unknown", "committed"})
_SESSIONS_LOCK = threading.RLock()


@dataclass
class BrowserSkillSession:
    session_id: str
    bsk_session_id: str
    owner_profile_id: str
    browser_instance_id: str
    task: str
    run_dir: Path
    last_url: str = ""
    created_at: float = field(default_factory=time.time)


_SESSIONS: dict[str, BrowserSkillSession] = {}


class BrowserSkillError(RuntimeError):
    def __init__(self, message: str, *, effect_state: str = "none") -> None:
        super().__init__(message)
        self.effect_state = effect_state


def _binary() -> str | None:
    configured = os.environ.get("NARAD_BSK_BINARY", "").strip()
    if configured:
        return configured if Path(configured).expanduser().is_file() else None
    return shutil.which("bsk")


def _decode_json(text: str) -> Any:
    candidate = (text or "").strip()
    if not candidate:
        return {}
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        starts = [index for index, char in enumerate(candidate) if char in "[{"]
        for index in starts:
            try:
                return json.loads(candidate[index:])
            except json.JSONDecodeError:
                continue
    return {"message": candidate[:2_000]}


def _effect_state(payload: Any) -> str:
    """Read ``effect_state`` from a ``bsk --json`` error.

    The CLI renders errors flat as ``{code, message, hint, exit_code, data}``
    and the daemon reports the input outcome at ``data.effect_state``.
    """
    if not isinstance(payload, dict):
        return "none"
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    state = str(data.get("effect_state") or payload.get("effect_state") or "none")
    return state if state in _EFFECT_STATES else "unknown"


def _error_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        for key in ("message", "error", "detail", "reason"):
            value = payload.get(key)
            if value:
                return str(value)[:1_200]
    return (fallback or "BrowserSkill command failed").strip()[:1_200]


def _run(args: list[str], *, timeout_s: int = 45, auto_start: bool = True) -> Any:
    binary = _binary()
    if not binary:
        raise BrowserSkillError("BrowserSkill CLI is not installed")
    env = os.environ.copy()
    # Only the exact value "off" disables bsk's self-update.
    env["BSK_AUTO_UPDATE"] = "off"
    if not auto_start:
        env["BSK_AUTO_START"] = "0"
    command = [binary, "--json", *args]
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=max(2, timeout_s),
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise BrowserSkillError(
            f"BrowserSkill command timed out after {timeout_s}s; inspect current state before retrying",
            effect_state="unknown",
        ) from exc
    payload = _decode_json(completed.stdout or completed.stderr)
    if completed.returncode != 0:
        raise BrowserSkillError(
            _error_message(payload, completed.stderr or completed.stdout),
            effect_state=_effect_state(payload),
        )
    return payload


def browser_skill_status() -> dict[str, Any]:
    binary = _binary()
    if not binary:
        return {
            "available": False,
            "ready": False,
            "binary": None,
            "reason": "Install the bsk CLI and BrowserSkill Chromium extension",
            "browsers": [],
        }
    try:
        payload = _run(["status"], timeout_s=4, auto_start=False)
    except BrowserSkillError as exc:
        return {
            "available": True,
            "ready": False,
            "binary": binary,
            "reason": str(exc),
            "browsers": [],
        }
    browsers = payload.get("browsers", []) if isinstance(payload, dict) else []
    return {
        "available": True,
        "ready": bool(browsers),
        "binary": binary,
        "reason": None if browsers else "BrowserSkill is installed but no Chromium extension is connected",
        "version": payload.get("daemon_version") if isinstance(payload, dict) else None,
        "protocol_version": payload.get("protocol_version") if isinstance(payload, dict) else None,
        "browsers": browsers if isinstance(browsers, list) else [],
        "active_sessions": len(_SESSIONS),
    }


def _owned_session(session_id: str, owner_profile_id: str) -> BrowserSkillSession:
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(session_id)
    if session is None:
        raise KeyError(f"Unknown or expired signed-in browser session: {session_id}")
    if session.owner_profile_id != owner_profile_id:
        raise PermissionError("This signed-in browser session belongs to another Narad profile")
    return session


def open_browser_skill_session(
    *,
    task: str,
    session_id: str = "",
    browser_instance_id: str = "",
    owner_profile_id: str | None = None,
) -> tuple[BrowserSkillSession, bool]:
    owner = validate_profile_id(owner_profile_id or current_profile_id())
    if session_id:
        return _owned_session(session_id, owner), False
    status = browser_skill_status()
    if not status.get("ready"):
        raise BrowserSkillError(str(status.get("reason") or "BrowserSkill is not ready"))
    browsers = status.get("browsers") or []
    selected = str(browser_instance_id or "").strip()
    if selected and not any(
        selected in {str(item.get("instance_id") or ""), str(item.get("label") or "")}
        for item in browsers
        if isinstance(item, dict)
    ):
        raise BrowserSkillError("The selected BrowserSkill browser is not connected")
    if not selected and len(browsers) > 1:
        raise BrowserSkillError("Multiple BrowserSkill browsers are connected; select a profile-bound target")
    args = ["session", "start", "--name", task[:80] or "Narad browser task", "--no-focus"]
    if selected:
        args.extend(["--browser", selected])
    payload = _run(args, timeout_s=20)
    bsk_session_id = str(payload.get("session_id") or "") if isinstance(payload, dict) else ""
    if not bsk_session_id:
        raise BrowserSkillError("BrowserSkill did not return a session id")
    public_id = f"signed_{uuid.uuid4().hex[:12]}"
    run_dir = ARTIFACTS_DIR / "computer-use" / owner / public_id
    run_dir.mkdir(parents=True, exist_ok=True)
    session = BrowserSkillSession(
        session_id=public_id,
        bsk_session_id=bsk_session_id,
        owner_profile_id=owner,
        browser_instance_id=str(payload.get("browser_instance_id") or selected),
        task=task,
        run_dir=run_dir,
    )
    with _SESSIONS_LOCK:
        _SESSIONS[public_id] = session
    return session, True


def _target_args(action: dict[str, Any], *, optional: bool = False) -> list[str]:
    target = action.get("target") if isinstance(action.get("target"), dict) else {}
    ref = action.get("ref") or target.get("ref")
    selector = action.get("selector") or target.get("selector")
    if ref:
        value = str(ref)
        return [f"@{value.removeprefix('@')}"]
    if selector:
        return ["--selector", str(selector)]
    if optional:
        return []
    raise ValueError("Signed-in browser actions require a fresh ref or an explicit selector")


def _download_path(session: BrowserSkillSession, requested: str, action_index: int) -> Path:
    """Confine signed-in downloads to this profile's session download folder."""
    download_dir = (session.run_dir / "downloads").resolve()
    if not requested:
        output_path = download_dir / f"download-{action_index:04d}.bin"
    else:
        relative = Path(requested)
        if relative.is_absolute() or requested.startswith("~") or ".." in relative.parts:
            raise ValueError("download path must be a relative file name inside the session downloads folder")
        output_path = (download_dir / relative).resolve()
        if output_path == download_dir or not output_path.is_relative_to(download_dir):
            raise ValueError("download path must stay inside the session downloads folder")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path


def _command_for_action(
    session: BrowserSkillSession,
    action: dict[str, Any],
    action_index: int,
) -> tuple[list[str] | None, Path | None]:
    kind = action["action"]
    sid = session.bsk_session_id
    output_path: Path | None = None
    if kind == "navigate":
        url = str(action.get("url") or action.get("value") or "").strip()
        if not url:
            raise ValueError("navigate requires url")
        return ["navigate", _validate_url(url), "--session", sid], None
    if kind == "back":
        return ["navigate-back", "--session", sid], None
    if kind == "forward":
        return ["navigate-forward", "--session", sid], None
    if kind == "reload":
        return ["reload", "--session", sid], None
    if kind in {"click", "submit", "check", "uncheck"}:
        return ["click", *_target_args(action), "--session", sid], None
    if kind == "hover":
        return ["hover", *_target_args(action), "--session", sid], None
    if kind in {"fill", "set_field", "type"}:
        args = [
            "fill",
            *_target_args(action),
            "--value",
            str(action.get("value", action.get("text", ""))),
            "--session",
            sid,
        ]
        if kind == "type":
            args.append("--no-clear")
        return args, None
    if kind == "select":
        return [
            "select",
            *_target_args(action),
            "--value",
            str(action.get("value", "")),
            "--session",
            sid,
        ], None
    if kind == "press":
        args = ["press", str(action.get("key") or ""), "--session", sid]
        target_args = _target_args(action, optional=True)
        if target_args:
            if target_args[0].startswith("@"):
                args.extend(["--ref", target_args[0]])
            else:
                args.extend(target_args)
        return args, None
    if kind == "scroll":
        return [
            "wheel",
            "--delta-x",
            str(action.get("delta_x", 0)),
            "--delta-y",
            str(action.get("delta_y", 640)),
            "--session",
            sid,
        ], None
    if kind == "wait":
        milliseconds = max(0, min(int(action.get("milliseconds", 1_000)), 30_000))
        return ["wait-ms", f"{milliseconds}ms"], None
    if kind == "screenshot":
        output_path = session.run_dir / f"screenshot-action-{action_index:04d}.png"
        return ["screenshot", "--session", sid, "--out", str(output_path)], output_path
    if kind == "upload":
        paths = action.get("paths", action.get("path", []))
        paths = [paths] if isinstance(paths, str) else list(paths or [])
        if not paths:
            raise ValueError("upload requires path or paths")
        args = ["upload", *_target_args(action)]
        for value in paths:
            path = Path(str(value)).expanduser().resolve()
            if not path.is_file():
                raise ValueError(f"Upload file does not exist: {path}")
            args.extend(["--file", str(path)])
        args.extend(["--session", sid])
        return args, None
    if kind == "download":
        output_path = _download_path(session, str(action.get("path") or "").strip(), action_index)
        return [
            "download",
            *_target_args(action),
            "--out",
            str(output_path),
            "--session",
            sid,
        ], output_path
    if kind == "request_help":
        args = [
            "request-help",
            "--session",
            sid,
            "--prompt",
            str(action.get("prompt") or "Please complete the required browser step"),
        ]
        target_args = _target_args(action, optional=True)
        if target_args and target_args[0].startswith("@"):
            args.extend(["--target", target_args[0]])
        return args, None
    raise ValueError(f"BrowserSkill does not support action {kind!r}")


def execute_browser_skill_actions(
    session_id: str,
    actions: list[dict[str, Any]],
    *,
    owner_profile_id: str | None = None,
    timeout_s: int = 180,
) -> list[dict[str, Any]]:
    owner = validate_profile_id(owner_profile_id or current_profile_id())
    session = _owned_session(session_id, owner)
    results: list[dict[str, Any]] = []
    for index, action in enumerate(actions, start=1):
        try:
            command, output_path = _command_for_action(session, action, index)
            payload = _run(command or [], timeout_s=min(timeout_s, 180))
            effect_state = "none" if action["action"] in {"wait", "screenshot"} else "committed"
            if isinstance(payload, dict):
                effect_state = str(payload.get("effect_state") or effect_state)
                # Redirects settle on final_url; history moves report only final_url.
                final_url = str(payload.get("final_url") or "")
                if action["action"] == "navigate":
                    session.last_url = final_url or str(payload.get("url") or action.get("url") or "")
                elif final_url:
                    session.last_url = final_url
            row: dict[str, Any] = {
                "index": index,
                "action": action["action"],
                "status": "ok",
                "effect_state": effect_state,
            }
            if output_path and output_path.exists():
                row["path"] = str(output_path)
            results.append(row)
        except (BrowserSkillError, ValueError) as exc:
            results.append({
                "index": index,
                "action": action["action"],
                "status": "error",
                "effect_state": getattr(exc, "effect_state", "none"),
                "error": str(exc),
            })
            break
    return results


def observation_ref_elements(text: str) -> dict[str, dict[str, str]]:
    """Map each ``@eN`` ref in BrowserSkill observation text to its role and label.

    A ref rendered on two lines with different labels is left out, so callers
    treat it as unresolved rather than trusting either reading.
    """
    elements: dict[str, dict[str, str]] = {}
    conflicting: set[str] = set()
    for line in (text or "").splitlines():
        match = _REF_LINE_RE.match(line)
        if not match:
            continue
        ref, role, name, rest = match.groups()
        element = {"ref": ref, "role": role, "name": _json_text(name)}
        placeholder = _PLACEHOLDER_RE.search(rest or "")
        if placeholder:
            element["placeholder"] = _json_text(placeholder.group(1))
        if elements.setdefault(ref, element) != element:
            conflicting.add(ref)
    for ref in conflicting:
        elements.pop(ref, None)
    return elements


def _json_text(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(json.loads(value))
    except json.JSONDecodeError:
        return value.strip('"')


def observe_browser_skill_session(
    session_id: str,
    *,
    owner_profile_id: str | None = None,
    include_screenshot: bool = True,
) -> dict[str, Any]:
    owner = validate_profile_id(owner_profile_id or current_profile_id())
    session = _owned_session(session_id, owner)
    payload = _run(
        ["observe", "--session", session.bsk_session_id, "--max-tokens", "2200"],
        timeout_s=45,
    )
    text = str(payload.get("text") or "") if isinstance(payload, dict) else ""
    refs = sorted(set(_REF_RE.findall(text)), key=lambda value: int(value[1:]))
    screenshot_path: Path | None = None
    capture_id: str | None = None
    if include_screenshot:
        screenshot_path = session.run_dir / f"screenshot-{time.time_ns()}.png"
        screenshot = _run(
            ["screenshot", "--session", session.bsk_session_id, "--out", str(screenshot_path)],
            timeout_s=45,
        )
        if isinstance(screenshot, dict):
            capture_id = str(screenshot.get("capture_id") or "") or None
    return {
        "url": session.last_url,
        "title": "Signed-in Chromium",
        "text": text,
        "interactive_elements": [{"ref": ref, "name": "See observation text"} for ref in refs],
        "fields": [],
        "prompt_injection_signals": [],
        "screenshot_path": str(screenshot_path) if screenshot_path and screenshot_path.exists() else None,
        "capture_id": capture_id,
        "observation_generation": time.time_ns(),
        "truncated": bool(payload.get("truncated")) if isinstance(payload, dict) else False,
        "next_cursor": payload.get("next_cursor") if isinstance(payload, dict) else None,
        "tab_id": payload.get("tab_id") if isinstance(payload, dict) else None,
    }


def close_browser_skill_session(
    session_id: str, *, owner_profile_id: str | None = None
) -> bool:
    owner = validate_profile_id(owner_profile_id or current_profile_id())
    session = _owned_session(session_id, owner)
    try:
        _run(["session", "stop", session.bsk_session_id], timeout_s=20)
    finally:
        with _SESSIONS_LOCK:
            _SESSIONS.pop(session_id, None)
    return True


def shutdown_browser_skill_sessions() -> None:
    with _SESSIONS_LOCK:
        sessions = list(_SESSIONS.values())
    for session in sessions:
        try:
            close_browser_skill_session(
                session.session_id, owner_profile_id=session.owner_profile_id
            )
        except Exception:
            with _SESSIONS_LOCK:
                _SESSIONS.pop(session.session_id, None)
