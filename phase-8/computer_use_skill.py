"""Persistent, safety-gated browser and desktop control for Matsya.

The browser runtime lives on one dedicated asyncio loop. This matters because
Playwright objects are loop-bound: recreating a loop for every tool call loses
cookies, navigation state, form previews, and the page itself.
"""

from __future__ import annotations

import asyncio
import atexit
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from narad_config import ARTIFACTS_DIR
from profile_context import current_profile_id, validate_profile_id
from tool_result import artifact, envelope, ui_panel

_COMPUTER_ARTIFACTS_DIR = ARTIFACTS_DIR / "computer-use"
_DEFAULT_VIEWPORT = {"width": 1440, "height": 960}
_DEFAULT_SESSION_TTL_S = 20 * 60
_MAX_OBSERVATION_CHARS = 7_000
_MAX_INTERACTIVE_ELEMENTS = 140
_MAX_BATCH_ACTIONS = 24

_SUPPORTED_BROWSER_ACTIONS = frozenset({
    "navigate",
    "back",
    "forward",
    "reload",
    "click",
    "hover",
    "fill",
    "set_field",
    "type",
    "select",
    "check",
    "uncheck",
    "press",
    "scroll",
    "wait",
    "download",
    "upload",
    "submit",
    "screenshot",
    "request_help",
    "close",
})
_SUPPORTED_DESKTOP_ACTIONS = frozenset({
    "move",
    "click",
    "double_click",
    "type",
    "press",
    "hotkey",
    "scroll",
    "drag",
    "wait",
    "screenshot",
})
_HIGH_RISK_PATTERN = re.compile(
    r"\b(submit|send|publish|post|buy|purchase|pay|checkout|confirm|delete|remove|"
    r"cancel\s+(?:account|subscription)|transfer|book|reserve|apply|sign|authorize|"
    r"place\s+(?:your\s+|an?\s+)?order)\b",
    re.IGNORECASE,
)
_SENSITIVE_PATTERN = re.compile(
    r"\b(password|passcode|one[- ]?time|otp|social security|ssn|credit card|cvv|"
    r"bank account|routing number|passport|private key|seed phrase)\b",
    re.IGNORECASE,
)
_PROMPT_INJECTION_PATTERNS = (
    re.compile(r"ignore (?:all |any )?(?:previous|prior|system) instructions", re.IGNORECASE),
    re.compile(r"(?:system|developer) message", re.IGNORECASE),
    re.compile(r"reveal (?:your )?(?:prompt|instructions|secrets)", re.IGNORECASE),
    re.compile(r"do not tell the user", re.IGNORECASE),
    re.compile(r"you are (?:an? )?(?:ai|assistant|agent)", re.IGNORECASE),
)
_BLOCKED_NETWORK_HOSTS = frozenset({
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.azure.internal",
})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_session_id(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return f"browser_{uuid.uuid4().hex[:12]}"
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", value):
        raise ValueError("session_id may only contain letters, numbers, dot, underscore, and dash")
    return value


def _validate_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must be a complete http:// or https:// address")
    if parsed.username or parsed.password:
        raise ValueError("Credentials embedded in URLs are not allowed")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in _BLOCKED_NETWORK_HOSTS:
        raise ValueError(f"Navigation to protected metadata host {hostname} is blocked")

    blocklist = {
        item.strip().lower().rstrip(".")
        for item in os.environ.get("NARAD_BROWSER_BLOCKLIST", "").split(",")
        if item.strip()
    }
    if any(hostname == item or hostname.endswith(f".{item}") for item in blocklist):
        raise ValueError(f"Navigation to {hostname} is blocked by NARAD_BROWSER_BLOCKLIST")

    allowlist = {
        item.strip().lower().rstrip(".")
        for item in os.environ.get("NARAD_BROWSER_ALLOWLIST", "").split(",")
        if item.strip()
    }
    if allowlist and not any(hostname == item or hostname.endswith(f".{item}") for item in allowlist):
        raise ValueError(f"Navigation to {hostname} is outside NARAD_BROWSER_ALLOWLIST")
    return parsed.geturl()


def _injection_signals(text: str) -> list[str]:
    signals: list[str] = []
    for pattern in _PROMPT_INJECTION_PATTERNS:
        match = pattern.search(text or "")
        if match:
            signals.append(match.group(0)[:120])
    return signals[:5]


def _action_target_text(action: dict[str, Any]) -> str:
    target = action.get("target")
    values: list[str] = []
    if isinstance(target, dict):
        values.extend(str(value) for value in target.values() if value is not None)
    elif target:
        values.append(str(target))
    for key in ("selector", "ref", "role", "name", "label", "text", "placeholder", "intent"):
        if action.get(key) is not None:
            values.append(str(action[key]))
    return " ".join(values)


def _action_requires_confirmation(action: dict[str, Any], environment: str = "browser") -> bool:
    kind = str(action.get("action", "")).lower()
    if environment == "desktop":
        return kind not in {"screenshot", "wait", "move"}
    if kind in {"submit", "upload"}:
        return True
    if kind == "press" and str(action.get("key", "")).lower() in {"enter", "return"}:
        return True
    if kind == "click" and (action.get("x") is not None or action.get("y") is not None):
        return True
    target_text = _action_target_text(action)
    if kind in {"click", "press"} and _HIGH_RISK_PATTERN.search(target_text):
        return True
    if kind in {"fill", "set_field", "type", "select", "check"} and _SENSITIVE_PATTERN.search(target_text):
        return True
    return bool(action.get("requires_confirmation", False))


def _normalise_actions(actions: list[dict[str, Any]] | None, environment: str) -> list[dict[str, Any]]:
    if actions is None:
        return []
    if not isinstance(actions, list):
        raise ValueError("actions must be a list of action objects")
    if len(actions) > _MAX_BATCH_ACTIONS:
        raise ValueError(f"A batch may contain at most {_MAX_BATCH_ACTIONS} actions")
    supported = _SUPPORTED_BROWSER_ACTIONS if environment == "browser" else _SUPPORTED_DESKTOP_ACTIONS
    normalised: list[dict[str, Any]] = []
    for index, item in enumerate(actions):
        if not isinstance(item, dict):
            raise ValueError(f"Action {index + 1} must be an object")
        action = dict(item)
        kind = str(
            action.get("action", action.get("type", action.get("kind", "")))
        ).strip().lower()
        if kind not in supported:
            choices = ", ".join(sorted(supported))
            raise ValueError(f"Unsupported {environment} action {kind!r}; use one of: {choices}")
        action["action"] = kind
        action.pop("type", None)
        action.pop("kind", None)
        normalised.append(action)
    return normalised


def _dharma_gate(action: str, detail: str, metadata: dict[str, Any] | None = None) -> str | None:
    try:
        from dharma import gate_action

        verdict = gate_action(action, avatar="Matsya", detail=detail, metadata=metadata)
        return None if verdict.allowed else "; ".join(verdict.reasons)
    except Exception as exc:
        return f"Dharma gate unavailable ({exc}); refusing the side effect"


def _desktop_permission_status() -> dict[str, bool | None]:
    if sys.platform != "darwin":
        return {"screen_recording": None, "accessibility": None}
    screen_recording: bool | None = None
    accessibility: bool | None = None
    try:
        import Quartz

        preflight = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
        screen_recording = bool(preflight()) if preflight else None
    except Exception:
        pass
    try:
        import ctypes

        services = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        trusted = services.AXIsProcessTrusted
        trusted.restype = ctypes.c_bool
        accessibility = bool(trusted())
    except Exception:
        pass
    return {
        "screen_recording": screen_recording,
        "accessibility": accessibility,
    }


def _cua_env() -> dict[str, str]:
    env = os.environ.copy()
    env["CUA_DRIVER_RS_TELEMETRY_ENABLED"] = "false"
    return env


def _desktop_driver_status() -> dict[str, Any]:
    """Resolve the configured desktop adapter without starting or switching it."""
    enabled = os.environ.get("NARAD_ENABLE_DESKTOP_CONTROL", "0").strip().lower() in {
        "1", "true", "on", "yes",
    }
    requested = os.environ.get("NARAD_DESKTOP_PROVIDER", "auto").strip().lower()
    if requested not in {"auto", "cua", "pyautogui"}:
        requested = "auto"

    bundled_cua = Path.home() / ".local" / "bin" / "cua-driver"
    cua_binary = shutil.which("cua-driver") or (str(bundled_cua) if bundled_cua.is_file() else None)
    daemon_ready = False
    permissions_ready = False
    cua_version: str | None = None
    cua_detail = ""
    if cua_binary:
        env = _cua_env()
        try:
            version = subprocess.run(
                [cua_binary, "--version"], capture_output=True, text=True, timeout=3, check=False, env=env
            )
            cua_version = (version.stdout or version.stderr).strip()[:120] or None
            status = subprocess.run(
                [cua_binary, "status"], capture_output=True, text=True, timeout=3, check=False, env=env
            )
            cua_detail = f"{status.stdout}\n{status.stderr}".strip()
            daemon_ready = status.returncode == 0 and "daemon is running" in cua_detail.lower()
            permission = subprocess.run(
                [cua_binary, "permissions", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
                env=env,
            )
            # The JSON form carries the daemon's own TCC booleans; without a
            # trustworthy answer it reports {"status": "unknown"} and omits them.
            try:
                grants = json.loads(permission.stdout or "{}")
            except json.JSONDecodeError:
                grants = {}
            grants = grants if isinstance(grants, dict) else {}
            permissions_ready = (
                permission.returncode == 0
                and grants.get("accessibility") is True
                and grants.get("screen_recording") is True
                and grants.get("screen_recording_capturable") is not False
            )
            permission_detail = str(grants.get("reason") or "") or (
                f"accessibility={grants.get('accessibility')}, "
                f"screen_recording={grants.get('screen_recording')}"
            )
            cua_detail = f"{cua_detail}\n{permission_detail}".strip()
        except (OSError, subprocess.SubprocessError) as exc:
            cua_detail = str(exc)
    cua_ready = bool(cua_binary and daemon_ready and permissions_ready)

    pyautogui_available = importlib.util.find_spec("pyautogui") is not None
    pyautogui_permissions = (
        _desktop_permission_status()
        if enabled and pyautogui_available
        else {"screen_recording": None, "accessibility": None}
    )
    pyautogui_permissions_ready = all(
        value is not False for value in pyautogui_permissions.values()
    )
    pyautogui_ready = pyautogui_available and pyautogui_permissions_ready

    if requested == "cua":
        selected = "cua"
    elif requested == "pyautogui":
        selected = "pyautogui"
    elif cua_ready:
        selected = "cua"
    else:
        selected = "pyautogui"
    selected_ready = cua_ready if selected == "cua" else pyautogui_ready

    if not enabled:
        reason = "Set NARAD_ENABLE_DESKTOP_CONTROL=1 to opt in"
    elif selected == "cua" and not cua_binary:
        reason = "Install Cua Driver to enable desktop control"
    elif selected == "cua" and not daemon_ready:
        reason = "Start CuaDriver.app on the Narad host"
    elif selected == "cua" and not permissions_ready:
        reason = "Grant CuaDriver Accessibility and Screen Recording on the Narad host"
    elif selected == "pyautogui" and not pyautogui_available:
        reason = "Install pyautogui or configure the optional CUA adapter"
    elif selected == "pyautogui" and not pyautogui_permissions_ready:
        reason = "Grant macOS Screen Recording and Accessibility permissions"
    else:
        reason = None

    return {
        "available": enabled and selected_ready,
        "ready": enabled and selected_ready,
        "enabled": enabled,
        "requested_provider": requested,
        "selected_provider": selected,
        "reason": reason,
        "adapters": {
            "cua": {
                "available": bool(cua_binary),
                "ready": cua_ready,
                "binary": cua_binary,
                "version": cua_version,
                "daemon_ready": daemon_ready,
                "permissions_ready": permissions_ready,
                "targets": [{"id": "host-primary", "label": "Narad host desktop"}],
                "reason": None if cua_ready else (
                    reason or cua_detail[-300:] or "Cua Driver is not ready"
                ),
            },
            "pyautogui": {
                "available": pyautogui_available,
                "ready": pyautogui_ready,
                "permissions": pyautogui_permissions,
                "reason": None if pyautogui_ready else (
                    "pyautogui is missing or host permissions are incomplete"
                ),
            },
        },
    }


@dataclass
class _BrowserSession:
    session_id: str
    owner_profile_id: str
    context: Any
    page: Any
    run_dir: Path
    task: str
    start_url: str
    created_at: str = field(default_factory=_utc_now)
    last_used_monotonic: float = field(default_factory=time.monotonic)
    action_count: int = 0
    screenshot_count: int = 0
    replay_actions: list[dict[str, Any]] = field(default_factory=list)


class BrowserSessionManager:
    """Own loop-bound Playwright objects and expose a thread-safe sync facade."""

    def __init__(self, ttl_s: int | None = None) -> None:
        self._ttl_s = ttl_s or int(os.environ.get("NARAD_BROWSER_SESSION_TTL_S", _DEFAULT_SESSION_TTL_S))
        self._thread_lock = threading.Lock()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._playwright: Any = None
        self._browser: Any = None
        self._sessions: dict[str, _BrowserSession] = {}
        self._session_count = 0
        self._launch_error: str | None = None

    def _ensure_thread(self) -> None:
        with self._thread_lock:
            if self._thread and self._thread.is_alive() and self._loop:
                return
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._thread_main,
                name="narad-browser-runtime",
                daemon=True,
            )
            self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("Browser runtime loop did not start")

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            self._loop = None

    def _call(self, coroutine: Any, timeout_s: int = 180) -> Any:
        self._ensure_thread()
        if self._loop is None:
            raise RuntimeError("Browser runtime loop is unavailable")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=max(5, timeout_s))
        except FutureTimeoutError as exc:
            future.cancel()
            raise TimeoutError(f"Browser operation exceeded {timeout_s} seconds") from exc

    async def _ensure_browser(self) -> None:
        if self._browser is not None and self._browser.is_connected():
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            self._launch_error = "Playwright is not installed"
            raise RuntimeError(
                "Playwright is not installed. Install Narad's browser extra and Chromium."
            ) from exc
        try:
            self._playwright = await async_playwright().start()
            headless = os.environ.get("NARAD_BROWSER_HEADLESS", "1").strip().lower() not in {
                "0", "false", "off", "no",
            }
            self._browser = await self._playwright.chromium.launch(headless=headless)
            self._launch_error = None
        except Exception as exc:
            self._launch_error = f"{type(exc).__name__}: {exc}"
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
            self._playwright = None
            self._browser = None
            raise RuntimeError(
                "Chromium could not start. Run `python -m playwright install chromium` and check "
                "local process permissions."
            ) from exc

    async def _cleanup_stale(self) -> None:
        now = time.monotonic()
        stale = [
            session_id
            for session_id, session in self._sessions.items()
            if now - session.last_used_monotonic > self._ttl_s
        ]
        for session_id in stale:
            await self._close_async(session_id)

    async def _navigate(self, page: Any, url: str, timeout_s: int) -> None:
        url = _validate_url(url)
        await page.goto(url, wait_until="domcontentloaded", timeout=min(timeout_s * 1000, 60_000))
        try:
            await page.wait_for_load_state("networkidle", timeout=3_000)
        except Exception:
            pass

    async def _open_async(
        self,
        *,
        task: str,
        start_url: str,
        session_id: str,
        owner_profile_id: str,
        timeout_s: int,
    ) -> tuple[_BrowserSession, bool]:
        await self._cleanup_stale()
        await self._ensure_browser()
        existing = self._sessions.get(session_id)
        if existing is not None:
            if existing.owner_profile_id != owner_profile_id:
                raise PermissionError("This browser session belongs to another Narad profile")
            existing.last_used_monotonic = time.monotonic()
            if task:
                existing.task = task
            if start_url and existing.page.url.rstrip("/") != start_url.rstrip("/"):
                await self._navigate(existing.page, start_url, timeout_s)
                self._record(existing, "navigate", {"action": "navigate", "url": start_url})
            return existing, False

        run_dir = _COMPUTER_ARTIFACTS_DIR / owner_profile_id / session_id
        run_dir.mkdir(parents=True, exist_ok=True)
        context = await self._browser.new_context(
            viewport=_DEFAULT_VIEWPORT,
            accept_downloads=True,
            ignore_https_errors=False,
        )
        page = await context.new_page()
        page.set_default_timeout(min(timeout_s * 1000, 30_000))
        session = _BrowserSession(
            session_id=session_id,
            owner_profile_id=owner_profile_id,
            context=context,
            page=page,
            run_dir=run_dir,
            task=task,
            start_url=start_url,
        )
        self._sessions[session_id] = session
        self._session_count = len(self._sessions)
        self._record(session, "session_started", {"start_url": start_url, "task": task[:500]})
        if start_url:
            try:
                await self._navigate(page, start_url, timeout_s)
                self._record(session, "navigate", {"action": "navigate", "url": start_url})
            except Exception:
                await self._close_async(session_id)
                raise
        self._write_replay(session)
        return session, True

    def open(
        self,
        *,
        task: str,
        start_url: str,
        session_id: str,
        owner_profile_id: str,
        timeout_s: int,
    ) -> tuple[_BrowserSession, bool]:
        return self._call(
            self._open_async(
                task=task,
                start_url=start_url,
                session_id=session_id,
                owner_profile_id=owner_profile_id,
                timeout_s=timeout_s,
            ),
            timeout_s,
        )

    def _record(self, session: _BrowserSession, event: str, payload: dict[str, Any]) -> None:
        trace_path = session.run_dir / "trace.jsonl"
        entry = {
            "timestamp": _utc_now(),
            "session_id": session.session_id,
            "event": event,
            "payload": payload,
        }
        with trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _redact_for_replay(self, session: _BrowserSession, action: dict[str, Any]) -> dict[str, Any]:
        safe = dict(action)
        for key in ("value", "text"):
            if key in safe and safe[key] not in (None, ""):
                slot = len(session.replay_actions) + 1
                safe[key] = f"${{NARAD_REPLAY_VALUE_{slot}}}"
        if isinstance(safe.get("fields"), dict):
            safe["fields"] = {name: "${NARAD_REPLAY_VALUE}" for name in safe["fields"]}
        return safe

    def _write_replay(self, session: _BrowserSession) -> None:
        path = session.run_dir / "replay.py"
        actions_json = json.dumps(session.replay_actions, indent=2, ensure_ascii=False)
        encoded_actions = repr(actions_json)
        start_url = json.dumps(session.start_url)
        task = json.dumps(session.task[:500])
        code = f'''"""Generated by Narad computer_use. Review before running."""
import json
import os

import narad_paths  # noqa: F401
from computer_use_skill import computer_use


def expand_env(value):
    if isinstance(value, str) and value.startswith("${{") and value.endswith("}}"):
        return os.environ.get(value[2:-1], "")
    if isinstance(value, dict):
        return {{key: expand_env(item) for key, item in value.items()}}
    if isinstance(value, list):
        return [expand_env(item) for item in value]
    return value


actions = expand_env(json.loads({encoded_actions}))
result = computer_use(
    task={task},
    start_url={start_url},
    actions=actions,
    dry_run=False,
    confirmed=os.environ.get("NARAD_REPLAY_CONFIRMED") == "1",
)
print(json.dumps(result, indent=2))
'''
        path.write_text(code, encoding="utf-8")

    async def _collect_observation(
        self,
        session: _BrowserSession,
        *,
        include_screenshot: bool,
    ) -> dict[str, Any]:
        page = session.page
        data = await page.evaluate(
            """({maxElements, maxText}) => {
                window.__naradRefCounter = window.__naradRefCounter || 0;
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none'
                        && rect.width > 0 && rect.height > 0;
                };
                const nodes = Array.from(document.querySelectorAll(
                    'a, button, input, textarea, select, summary, [role="button"], '
                    + '[role="link"], [role="checkbox"], [role="radio"], [contenteditable="true"]'
                )).filter(visible).slice(0, maxElements);
                const interactive = nodes.map((el) => {
                    if (!el.dataset.naradRef) {
                        window.__naradRefCounter += 1;
                        el.dataset.naradRef = `e${window.__naradRefCounter}`;
                    }
                    const label = el.labels?.[0]?.innerText?.trim()
                        || el.getAttribute('aria-label')
                        || el.getAttribute('title')
                        || el.getAttribute('placeholder')
                        || el.innerText?.trim()
                        || el.getAttribute('name')
                        || '';
                    return {
                        ref: el.dataset.naradRef,
                        role: el.getAttribute('role') || el.tagName.toLowerCase(),
                        name: label.slice(0, 180),
                        type: el.getAttribute('type') || '',
                        value: el.type === 'password' ? '<redacted>' : String(el.value || '').slice(0, 120),
                        disabled: Boolean(el.disabled),
                    };
                });
                const fields = interactive.filter((item) =>
                    ['input', 'textarea', 'select', 'checkbox', 'radio'].includes(item.role)
                    || ['text', 'email', 'password', 'search', 'tel', 'url', 'number', 'file'].includes(item.type)
                );
                return {
                    text: (document.body?.innerText || '').replace(/\\n{3,}/g, '\\n\\n').slice(0, maxText),
                    interactive,
                    fields,
                };
            }""",
            {"maxElements": _MAX_INTERACTIVE_ELEMENTS, "maxText": _MAX_OBSERVATION_CHARS},
        )
        session.last_used_monotonic = time.monotonic()
        screenshot_path: Path | None = None
        if include_screenshot:
            session.screenshot_count += 1
            screenshot_path = session.run_dir / f"screenshot-{session.screenshot_count:04d}.png"
            await page.screenshot(path=str(screenshot_path), full_page=False)
            self._record(
                session,
                "screenshot",
                {"path": str(screenshot_path), "url": page.url},
            )
        signals = _injection_signals(str(data.get("text", "")))
        return {
            "url": page.url,
            "title": await page.title(),
            "viewport": dict(_DEFAULT_VIEWPORT),
            "text": data.get("text", ""),
            "interactive_elements": data.get("interactive", []),
            "fields": data.get("fields", []),
            "prompt_injection_signals": signals,
            "screenshot_path": str(screenshot_path) if screenshot_path else None,
        }

    def observe(
        self,
        session_id: str,
        *,
        owner_profile_id: str,
        include_screenshot: bool = True,
        timeout_s: int = 60,
    ) -> dict[str, Any]:
        async def _observe() -> dict[str, Any]:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(f"Unknown or expired browser session: {session_id}")
            if session.owner_profile_id != owner_profile_id:
                raise PermissionError("This browser session belongs to another Narad profile")
            return await self._collect_observation(session, include_screenshot=include_screenshot)

        return self._call(_observe(), timeout_s)

    async def _locator(self, page: Any, action: dict[str, Any]) -> Any:
        target = action.get("target") if isinstance(action.get("target"), dict) else {}
        merged = {**target, **{key: action[key] for key in (
            "ref", "selector", "role", "name", "label", "placeholder", "text", "query",
        ) if key in action}}
        locator = None
        if merged.get("ref"):
            ref = str(merged["ref"]).replace('"', '\\"')
            locator = page.locator(f'[data-narad-ref="{ref}"]').first
        elif merged.get("selector"):
            locator = page.locator(str(merged["selector"])).first
        elif merged.get("role"):
            kwargs: dict[str, Any] = {}
            if merged.get("name"):
                kwargs["name"] = str(merged["name"])
            locator = page.get_by_role(str(merged["role"]), **kwargs).first
        elif merged.get("label"):
            locator = page.get_by_label(str(merged["label"]), exact=False).first
        elif merged.get("placeholder"):
            locator = page.get_by_placeholder(str(merged["placeholder"]), exact=False).first
        elif merged.get("text"):
            locator = page.get_by_text(str(merged["text"]), exact=False).first
        elif merged.get("query"):
            query = str(merged["query"])
            candidates = []
            try:
                candidates.append(page.locator(query).first)
            except Exception:
                pass
            candidates.extend([
                page.get_by_label(query, exact=False).first,
                page.get_by_placeholder(query, exact=False).first,
                page.locator(f"[name={json.dumps(query)}]").first,
            ])
            for candidate in candidates:
                try:
                    if await candidate.count() > 0:
                        locator = candidate
                        break
                except Exception:
                    continue
        if locator is None:
            raise ValueError("Action requires a semantic target, selector, ref, or coordinates")
        if await locator.count() == 0:
            raise ValueError(f"No element matched target {_action_target_text(action)!r}")
        return locator

    async def _dynamic_action_requires_confirmation(self, page: Any, action: dict[str, Any]) -> bool:
        if _action_requires_confirmation(action):
            return True
        if action["action"] != "click":
            return False
        try:
            locator = await self._locator(page, action)
            details = await locator.evaluate(
                "el => ({type: el.type || '', text: el.innerText || el.value || '', "
                "aria: el.getAttribute('aria-label') || ''})"
            )
            text = f"{details.get('type', '')} {details.get('text', '')} {details.get('aria', '')}"
            return details.get("type") == "submit" or bool(_HIGH_RISK_PATTERN.search(text))
        except Exception:
            return False

    async def _execute_action(
        self,
        session: _BrowserSession,
        action: dict[str, Any],
        timeout_s: int,
    ) -> dict[str, Any]:
        page = session.page
        kind = action["action"]
        if kind == "navigate":
            await self._navigate(page, str(action.get("url", "")), timeout_s)
        elif kind == "back":
            await page.go_back(wait_until="domcontentloaded", timeout=min(timeout_s * 1000, 30_000))
        elif kind == "forward":
            await page.go_forward(wait_until="domcontentloaded", timeout=min(timeout_s * 1000, 30_000))
        elif kind == "reload":
            await page.reload(wait_until="domcontentloaded", timeout=min(timeout_s * 1000, 30_000))
        elif kind in {"click", "hover"}:
            if action.get("x") is not None and action.get("y") is not None:
                if kind == "hover":
                    await page.mouse.move(float(action["x"]), float(action["y"]))
                else:
                    await page.mouse.click(float(action["x"]), float(action["y"]))
            else:
                locator = await self._locator(page, action)
                await (locator.hover() if kind == "hover" else locator.click())
        elif kind in {"fill", "set_field", "type"}:
            locator = await self._locator(page, action)
            value = str(action.get("value", action.get("text", "")))
            if kind == "set_field":
                details = await locator.evaluate(
                    "el => ({tag: el.tagName.toLowerCase(), type: el.type || ''})"
                )
                if details.get("tag") == "select":
                    try:
                        await locator.select_option(label=value)
                    except Exception:
                        await locator.select_option(value=value)
                elif details.get("type") in {"checkbox", "radio"}:
                    checked = value.strip().lower() in {"true", "1", "yes", "on", "checked"}
                    await (locator.check() if checked else locator.uncheck())
                else:
                    await locator.fill(value)
            elif kind == "fill":
                await locator.fill(value)
            else:
                await locator.click()
                await locator.press_sequentially(value, delay=max(0, int(action.get("delay_ms", 15))))
        elif kind == "select":
            locator = await self._locator(page, action)
            value = str(action.get("value", ""))
            try:
                await locator.select_option(label=value)
            except Exception:
                await locator.select_option(value=value)
        elif kind in {"check", "uncheck"}:
            locator = await self._locator(page, action)
            await (locator.check() if kind == "check" else locator.uncheck())
        elif kind == "press":
            key = str(action.get("key", ""))
            if not key:
                raise ValueError("press requires key")
            has_target = bool(_action_target_text(action))
            if has_target:
                locator = await self._locator(page, action)
                await locator.press(key)
            else:
                await page.keyboard.press(key)
        elif kind == "scroll":
            await page.mouse.wheel(float(action.get("delta_x", 0)), float(action.get("delta_y", 640)))
        elif kind == "wait":
            milliseconds = max(0, min(int(action.get("milliseconds", 1_000)), 30_000))
            await page.wait_for_timeout(milliseconds)
        elif kind == "download":
            locator = await self._locator(page, action)
            async with page.expect_download(timeout=min(timeout_s * 1000, 60_000)) as pending:
                await locator.click()
            download = await pending.value
            download_dir = session.run_dir / "downloads"
            download_dir.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", download.suggested_filename).strip(".-")
            destination = download_dir / (safe_name or f"download-{session.action_count + 1}")
            await download.save_as(str(destination))
        elif kind == "upload":
            locator = await self._locator(page, action)
            paths = action.get("paths", action.get("path", []))
            if isinstance(paths, str):
                paths = [paths]
            if not paths:
                raise ValueError("upload requires path or paths")
            resolved: list[str] = []
            for value in paths:
                path = Path(str(value)).expanduser().resolve()
                if not path.is_file():
                    raise ValueError(f"Upload file does not exist: {path}")
                resolved.append(str(path))
            await locator.set_input_files(resolved)
        elif kind == "submit":
            if _action_target_text(action):
                locator = await self._locator(page, action)
            else:
                locator = page.locator(
                    '[type="submit"], button:has-text("Submit"), button:has-text("Send"), '
                    'button:has-text("Apply"), button:has-text("Confirm")'
                ).first
                if await locator.count() == 0:
                    raise ValueError("No submit control found; provide a target")
            await locator.click()
        elif kind == "screenshot":
            pass
        else:
            raise ValueError(f"Action {kind!r} is not executable in an open page")

        await page.wait_for_timeout(200)
        if session.context.pages:
            session.page = session.context.pages[-1]
        session.action_count += 1
        session.last_used_monotonic = time.monotonic()
        safe_action = self._redact_for_replay(session, action)
        session.replay_actions.append(safe_action)
        self._record(session, "action", safe_action)
        self._write_replay(session)
        return {"index": session.action_count, "action": kind, "status": "ok", "url": session.page.url}

    def execute(
        self,
        session_id: str,
        actions: list[dict[str, Any]],
        *,
        owner_profile_id: str,
        confirmed: bool,
        timeout_s: int,
    ) -> dict[str, Any]:
        async def _execute() -> dict[str, Any]:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(f"Unknown or expired browser session: {session_id}")
            if session.owner_profile_id != owner_profile_id:
                raise PermissionError("This browser session belongs to another Narad profile")
            page_text = await session.page.evaluate("() => (document.body?.innerText || '').slice(0, 7000)")
            injection = _injection_signals(str(page_text))
            if injection and not confirmed and any(
                action["action"] not in {"screenshot", "wait", "scroll", "hover"}
                for action in actions
            ):
                return {
                    "status": "confirmation_required",
                    "requires_confirmation": True,
                    "reason": "Possible prompt injection was detected in page content.",
                    "prompt_injection_signals": injection,
                    "action_results": [],
                }

            results: list[dict[str, Any]] = []
            for action in actions:
                needs_confirmation = await self._dynamic_action_requires_confirmation(session.page, action)
                if needs_confirmation and not confirmed:
                    return {
                        "status": "confirmation_required",
                        "requires_confirmation": True,
                        "reason": f"Action {action['action']!r} may create an external side effect.",
                        "pending_action": action,
                        "prompt_injection_signals": injection,
                        "action_results": results,
                    }
                if needs_confirmation:
                    gate_error = _dharma_gate(
                        "browser_submit",
                        f"{action['action']} on {session.page.url[:180]}",
                        {"session_id": session.session_id, "action": action["action"]},
                    )
                    if gate_error:
                        return {
                            "status": "blocked",
                            "requires_confirmation": False,
                            "reason": gate_error,
                            "action_results": results,
                        }
                try:
                    results.append(await self._execute_action(session, action, timeout_s))
                except Exception as exc:
                    results.append({
                        "action": action["action"],
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    break
            return {
                "status": "ok" if all(item["status"] == "ok" for item in results) else "partial",
                "requires_confirmation": False,
                "prompt_injection_signals": injection,
                "action_results": results,
            }

        return self._call(_execute(), timeout_s)

    async def _close_async(self, session_id: str) -> bool:
        session = self._sessions.pop(session_id, None)
        self._session_count = len(self._sessions)
        if session is None:
            return False
        self._record(session, "session_closed", {"url": session.page.url})
        try:
            await session.context.close()
        except Exception:
            pass
        return True

    def close(self, session_id: str, *, owner_profile_id: str, timeout_s: int = 30) -> bool:
        session = self._sessions.get(session_id)
        if session is not None and session.owner_profile_id != owner_profile_id:
            raise PermissionError("This browser session belongs to another Narad profile")
        return bool(self._call(self._close_async(session_id), timeout_s))

    async def _shutdown_async(self) -> None:
        for session_id in list(self._sessions):
            await self._close_async(session_id)
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._browser = None
        self._playwright = None

    def shutdown(self) -> None:
        if not self._loop or not self._thread or not self._thread.is_alive():
            return
        try:
            self._call(self._shutdown_async(), 15)
        except Exception:
            pass
        loop = self._loop
        if loop:
            loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout=3)

    @property
    def active_session_count(self) -> int:
        return self._session_count

    @property
    def launch_error(self) -> str | None:
        return self._launch_error


_BROWSER_MANAGER = BrowserSessionManager()
atexit.register(_BROWSER_MANAGER.shutdown)


def _browser_artifacts(
    session_id: str,
    observation: dict[str, Any] | None = None,
    *,
    owner_profile_id: str,
) -> list[dict[str, Any]]:
    run_dir = _COMPUTER_ARTIFACTS_DIR / owner_profile_id / session_id
    items: list[dict[str, Any]] = []
    screenshot_path = (observation or {}).get("screenshot_path")
    if screenshot_path:
        items.append(artifact(
            type="image",
            label="Browser screenshot",
            path=screenshot_path,
            mime_type="image/png",
            description="Latest browser viewport for this persistent session.",
        ))
    trace_path = run_dir / "trace.jsonl"
    if trace_path.exists():
        items.append(artifact(
            type="trace",
            label="Browser action trace",
            path=trace_path,
            mime_type="application/x-ndjson",
            description="Timestamped session provenance with sensitive input values redacted.",
        ))
    replay_path = run_dir / "replay.py"
    if replay_path.exists():
        items.append(artifact(
            type="script",
            label="Rerunnable browser script",
            path=replay_path,
            mime_type="text/x-python",
            description="Reviewable replay script; typed values are supplied through environment variables.",
        ))
    download_dir = run_dir / "downloads"
    if download_dir.is_dir():
        for path in sorted(item for item in download_dir.iterdir() if item.is_file()):
            items.append(artifact(
                type="download",
                label=path.name,
                path=path,
                description="Downloaded into Narad's isolated artifact store; not opened automatically.",
            ))
    return items


def _browser_envelope(
    *,
    session_id: str,
    task: str,
    observation: dict[str, Any] | None,
    action_results: list[dict[str, Any]] | None = None,
    planned_actions: list[dict[str, Any]] | None = None,
    status: str = "ok",
    summary: str,
    requires_confirmation: bool = False,
    error: str | None = None,
    session_created: bool = False,
    owner_profile_id: str,
    engine: str = "playwright",
) -> dict[str, Any]:
    observation = observation or {}
    decision_hint = _browser_decision_hint(task, observation)
    if decision_hint:
        observation["decision_hint"] = decision_hint
    page_label = observation.get("title") or observation.get("url") or "blank page"
    sections = [
        {
            "title": "Session",
            "body": (
                f"{session_id} - persistent, signed-in Chromium context"
                if engine == "browser_skill"
                else f"{session_id} - persistent, isolated Chromium context"
            ),
        },
        {"title": "Page", "body": str(page_label)},
    ]
    signals = observation.get("prompt_injection_signals") or []
    if signals:
        sections.append({
            "title": "Safety warning",
            "body": "Possible prompt-injection language was found. Inspect it before allowing actions.",
        })
    return envelope(
        status=status,
        summary=summary,
        artifacts=_browser_artifacts(
            session_id,
            observation,
            owner_profile_id=owner_profile_id,
        ),
        citations=[],
        ui=ui_panel(
            title="Computer use",
            summary=summary,
            sections=sections,
            primary_artifact_label="Browser screenshot" if observation.get("screenshot_path") else None,
            tone="computer-use",
        ),
        provenance={
            "engine": engine,
            "mode": (
                "persistent_signed_in_session"
                if engine == "browser_skill"
                else "persistent_isolated_session"
            ),
            "session_id": session_id,
            "profile_id": owner_profile_id,
            "task": task,
            "captured_at": _utc_now(),
            "decision_hint": decision_hint,
        },
        requires_confirmation=requires_confirmation,
        error=error,
        session_id=session_id,
        session_created=session_created,
        observation=observation,
        action_results=action_results or [],
        planned_actions=planned_actions or [],
    )


def _browser_decision_hint(task: str, observation: dict[str, Any]) -> dict[str, Any] | None:
    mode = os.environ.get("NARAD_JEV_COMPUTER_MODE", "shadow").strip().lower()
    if mode == "off" or not observation or observation.get("decision_hint"):
        return None
    try:
        from decision_contracts import browser_step_v1, compact_decision
        from decision_engine import jev_status

        if not jev_status().get("available"):
            return None
        result = browser_step_v1({
            "goal": task[:1000],
            "page": {
                "url": str(observation.get("url") or "")[:1000],
                "title": str(observation.get("title") or "")[:500],
                "text": str(observation.get("text") or "")[:5000],
            },
            "targets": (observation.get("interactive_elements") or [])[:100],
            "deterministic_injection_signals": observation.get("prompt_injection_signals") or [],
        })
        hint = compact_decision(result)
        hint["mode"] = mode
        injection = result.answers.get("possible_injection")
        if (
            mode == "active"
            and injection is not None
            and bool(injection.value)
            and injection.confidence >= 0.8
        ):
            signals = observation.setdefault("prompt_injection_signals", [])
            if "jev_semantic_prompt_injection" not in signals:
                signals.append("jev_semantic_prompt_injection")
        return hint
    except Exception as exc:
        return {
            "decision_id": "browser_step_v1",
            "status": "error",
            "mode": mode,
            "error": f"{type(exc).__name__}: {exc}"[:300],
        }


_WHEEL_NOTCH_PX = 120  # cua-driver's per-notch line step and the usual wheel delta
_CUA_UNCONFIRMED_EFFECTS = frozenset({"unverifiable", "suspected_noop"})


def _desktop_scroll_steps(action: dict[str, Any]) -> tuple[str, int]:
    """Normalise a desktop scroll to (direction, wheel notches) for every engine.

    ``direction`` with ``amount`` is used as given. ``clicks`` counts wheel
    notches with pyautogui's sign (positive scrolls up). ``delta_x``/``delta_y``
    are browser-style pixels (positive scrolls right/down), 120 px per notch.
    """
    direction = str(action.get("direction") or "").strip().lower()
    if direction:
        if direction not in {"up", "down", "left", "right"}:
            raise ValueError("scroll direction must be up, down, left, or right")
        amount = int(action.get("amount") or 3)
    elif action.get("clicks") is not None:
        clicks = int(action["clicks"])
        direction, amount = ("up" if clicks > 0 else "down"), abs(clicks)
    else:
        delta_x = float(action.get("delta_x") or 0)
        delta_y = action.get("delta_y")
        delta_y = float(delta_y) if delta_y is not None else (0.0 if delta_x else 5.0 * _WHEEL_NOTCH_PX)
        if abs(delta_x) > abs(delta_y):
            direction, pixels = ("right" if delta_x > 0 else "left"), abs(delta_x)
        else:
            direction, pixels = ("down" if delta_y > 0 else "up"), abs(delta_y)
        amount = round(pixels / _WHEEL_NOTCH_PX)
    return direction, max(1, min(amount, 50))


def _cua_action_command(
    binary: str,
    action: dict[str, Any],
    screenshot_path: Path,
    session_id: str = "narad-desktop",
) -> list[str] | None:
    kind = action["action"]
    target = {"kind": "desktop", "display_id": "primary"}
    payload: dict[str, Any] = {"session": session_id}
    if kind == "move":
        payload.update({"target": target, "x": float(action["x"]), "y": float(action["y"])})
        tool = "move_cursor"
    # The driver contract requires delivery_mode on click and refuses background
    # delivery for desktop-scoped targets.
    if kind == "click":
        payload.update({"target": target, "x": float(action["x"]), "y": float(action["y"]), "button": str(action.get("button", "left")), "delivery_mode": "foreground"})
        tool = "click"
    elif kind == "double_click":
        payload.update({"target": target, "x": float(action["x"]), "y": float(action["y"]), "count": 2, "delivery_mode": "foreground"})
        tool = "click"
    elif kind == "type":
        payload.update({"target": target, "text": str(action.get("text", action.get("value", "")))})
        tool = "type_text"
    elif kind == "press":
        payload.update({"target": target, "key": str(action.get("key", ""))})
        tool = "press_key"
    elif kind == "hotkey":
        keys = action.get("keys", [])
        if not isinstance(keys, list) or not keys:
            raise ValueError("hotkey requires a non-empty keys list")
        payload.update({"target": target, "keys": [str(key) for key in keys]})
        tool = "hotkey"
    elif kind == "scroll":
        # Desktop scroll is a wheel at an absolute get_desktop_state point.
        if action.get("x") is None or action.get("y") is None:
            raise ValueError("desktop scroll requires x and y in get_desktop_state coordinates")
        direction, amount = _desktop_scroll_steps(action)
        payload.update({
            "target": target,
            "x": float(action["x"]),
            "y": float(action["y"]),
            "direction": direction,
            "amount": amount,
            "by": "line",
        })
        tool = "scroll"
    elif kind == "drag":
        payload.update({
            "target": target,
            "from_x": float(action["x1"]),
            "from_y": float(action["y1"]),
            "to_x": float(action["x2"]),
            "to_y": float(action["y2"]),
        })
        tool = "drag"
    elif kind == "screenshot":
        payload["screenshot_out_file"] = str(screenshot_path)
        tool = "get_desktop_state"
    elif kind == "wait":
        return None
    elif kind != "move":
        raise ValueError(f"Unsupported CUA action: {kind}")
    return [binary, "call", tool, json.dumps(payload, separators=(",", ":"))]


def _execute_cua_actions(
    binary: str,
    actions: list[dict[str, Any]],
    run_dir: Path,
    session_id: str,
) -> tuple[list[dict[str, Any]], Path | None]:
    results: list[dict[str, Any]] = []
    screenshot_path: Path | None = None
    env = _cua_env()
    for index, action in enumerate(actions):
        kind = action["action"]
        try:
            if kind == "wait":
                time.sleep(max(0, min(float(action.get("seconds", 1)), 30)))
                results.append({"action": kind, "status": "ok"})
                continue
            candidate = run_dir / f"desktop-{time.time_ns()}-{index}.png"
            command = _cua_action_command(binary, action, candidate, session_id)
            completed = subprocess.run(
                command,
                cwd=run_dir,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                env=env,
            )
            if completed.returncode != 0:
                message = (completed.stderr or completed.stdout or "CUA action failed").strip()[:800]
                raise RuntimeError(message)
            if kind == "screenshot" and candidate.exists():
                screenshot_path = candidate
            parsed: dict[str, Any] = {}
            try:
                parsed = json.loads(completed.stdout) if completed.stdout.strip() else {}
            except json.JSONDecodeError:
                pass
            effect = parsed.get("effect") if isinstance(parsed, dict) else None
            # The driver delivered the input but could not confirm it landed.
            status = "unverified" if effect in _CUA_UNCONFIRMED_EFFECTS else "ok"
            results.append({"action": kind, "status": status, "effect": effect, "driver_result": parsed})
        except Exception as exc:
            results.append({"action": kind, "status": "error", "error": f"{type(exc).__name__}: {exc}"})
            break
    if screenshot_path is None:
        candidate = run_dir / f"desktop-{time.time_ns()}-final.png"
        try:
            completed = subprocess.run(
                _cua_action_command(
                    binary, {"action": "screenshot"}, candidate, session_id
                ),
                cwd=run_dir,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                env=env,
            )
            if completed.returncode == 0 and candidate.exists():
                screenshot_path = candidate
        except Exception:
            pass
    return results, screenshot_path


def _desktop_decision_hint(
    task: str,
    actions: list[dict[str, Any]],
    *,
    results: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    mode = os.environ.get("NARAD_JEV_COMPUTER_MODE", "shadow").strip().lower()
    if mode == "off":
        return None
    try:
        from decision_contracts import compact_decision, desktop_admission_v1, desktop_verify_v1
        from decision_engine import jev_status

        if not jev_status().get("available"):
            return {"status": "unavailable", "mode": mode, "error": "Jev is not configured"}
        state = {
            "goal": task[:1200],
            "actions": actions[:24],
            "driver_results": (results or [])[:24],
        }
        decision = desktop_verify_v1(state) if results is not None else desktop_admission_v1(state)
        hint = compact_decision(decision)
        hint["mode"] = mode
        return hint
    except Exception as exc:
        return {
            "status": "error",
            "mode": mode,
            "error": f"{type(exc).__name__}: {exc}"[:300],
        }


def _execute_pyautogui_actions(
    actions: list[dict[str, Any]],
    run_dir: Path,
) -> tuple[list[dict[str, Any]], Path | None]:
    import pyautogui

    pyautogui.FAILSAFE = True
    results: list[dict[str, Any]] = []
    screenshot_path: Path | None = None
    for action in actions:
        kind = action["action"]
        try:
            if kind == "move":
                pyautogui.moveTo(float(action["x"]), float(action["y"]), duration=float(action.get("duration", 0.2)))
            elif kind == "click":
                pyautogui.click(float(action["x"]), float(action["y"]), button=str(action.get("button", "left")))
            elif kind == "double_click":
                pyautogui.doubleClick(float(action["x"]), float(action["y"]), interval=float(action.get("interval", 0.12)))
            elif kind == "type":
                pyautogui.write(str(action.get("text", action.get("value", ""))), interval=float(action.get("interval", 0.02)))
            elif kind == "press":
                pyautogui.press(str(action.get("key", "")))
            elif kind == "hotkey":
                keys = action.get("keys", [])
                if not isinstance(keys, list) or not keys:
                    raise ValueError("hotkey requires a non-empty keys list")
                pyautogui.hotkey(*(str(key) for key in keys))
            elif kind == "scroll":
                direction, notches = _desktop_scroll_steps(action)
                point = (
                    {"x": float(action["x"]), "y": float(action["y"])}
                    if action.get("x") is not None and action.get("y") is not None
                    else {}
                )
                if direction in {"up", "down"}:
                    pyautogui.scroll(notches if direction == "up" else -notches, **point)
                else:
                    pyautogui.hscroll(notches if direction == "right" else -notches, **point)
            elif kind == "drag":
                pyautogui.moveTo(float(action["x1"]), float(action["y1"]))
                pyautogui.dragTo(float(action["x2"]), float(action["y2"]), duration=float(action.get("duration", 0.4)))
            elif kind == "wait":
                time.sleep(max(0, min(float(action.get("seconds", 1)), 30)))
            elif kind == "screenshot":
                screenshot_path = run_dir / f"desktop-{time.time_ns()}.png"
                pyautogui.screenshot(str(screenshot_path))
            results.append({"action": kind, "status": "ok"})
        except Exception as exc:
            results.append({"action": kind, "status": "error", "error": f"{type(exc).__name__}: {exc}"})
            break
    if screenshot_path is None:
        try:
            screenshot_path = run_dir / f"desktop-{time.time_ns()}-final.png"
            pyautogui.screenshot(str(screenshot_path))
        except Exception:
            screenshot_path = None
    return results, screenshot_path


def _desktop_use(
    *,
    task: str,
    session_id: str,
    actions: list[dict[str, Any]],
    dry_run: bool,
    confirmed: bool,
    owner_profile_id: str,
    target_id: str = "",
) -> dict[str, Any]:
    readiness = _desktop_driver_status()
    engine = str(readiness["selected_provider"])
    from interaction_targets import resolve_interaction_target

    # The host-desktop grant is recorded under the "cua" kind; it gates every
    # engine that drives this desktop, including the pyautogui fallback.
    grant = resolve_interaction_target("cua", target_id, profile_id=owner_profile_id)
    if grant is None:
        return envelope(
            status="unavailable",
            summary="Grant the Narad host desktop to this family profile in onboarding first.",
            error="desktop_target_unavailable",
            readiness=readiness,
        )
    decision_hint = _desktop_decision_hint(task, actions)
    needs_confirmation = any(_action_requires_confirmation(action, "desktop") for action in actions)
    summary = f"Desktop action plan prepared with {len(actions)} action(s)."
    if dry_run:
        return envelope(
            status="preview",
            summary=summary,
            ui=ui_panel(
                title="Desktop control preview",
                summary=summary,
                sections=[
                    {"title": "Runtime", "body": f"{engine}: {'ready' if readiness['available'] else readiness['reason']}"},
                    {"title": "Safety", "body": "Desktop input always requires explicit confirmation."},
                ],
                tone="computer-use",
            ),
            provenance={
                "engine": engine,
                "session_id": session_id,
                "task": task,
                "profile_id": owner_profile_id,
                "target_id": (grant or {}).get("target_id"),
                "decision_hint": decision_hint,
            },
            requires_confirmation=needs_confirmation,
            session_id=session_id,
            planned_actions=actions,
            readiness=readiness,
        )
    if not readiness["available"]:
        return envelope(
            status="unavailable",
            summary=str(readiness["reason"] or "Desktop control is unavailable."),
            requires_confirmation=False,
            provenance={"engine": engine, "session_id": session_id, "profile_id": owner_profile_id},
            readiness=readiness,
        )
    if needs_confirmation and not confirmed:
        return envelope(
            status="confirmation_required",
            summary="Desktop actions are ready but require explicit user confirmation.",
            requires_confirmation=True,
            provenance={"engine": engine, "session_id": session_id, "profile_id": owner_profile_id},
            session_id=session_id,
            planned_actions=actions,
            readiness=readiness,
        )
    gate_error = _dharma_gate(
        "desktop_control",
        f"desktop batch ({len(actions)} actions)",
        {"session_id": session_id, "action_count": len(actions), "engine": engine},
    )
    if gate_error:
        return envelope(
            status="blocked",
            summary=gate_error,
            requires_confirmation=False,
            provenance={"engine": engine, "session_id": session_id, "profile_id": owner_profile_id},
        )

    run_dir = _COMPUTER_ARTIFACTS_DIR / owner_profile_id / session_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if engine == "cua":
        binary = str(readiness["adapters"]["cua"]["binary"])
        results, screenshot_path = _execute_cua_actions(binary, actions, run_dir, session_id)
    else:
        results, screenshot_path = _execute_pyautogui_actions(actions, run_dir)
    artifacts = []
    if screenshot_path:
        artifacts.append(artifact(
            type="image",
            label="Desktop screenshot",
            path=screenshot_path,
            mime_type="image/png",
            description="Desktop state after the confirmed action batch.",
        ))
    verification_hint = _desktop_decision_hint(task, actions, results=results)
    unverified = sum(item["status"] == "unverified" for item in results)
    summary = f"Executed {sum(item['status'] in {'ok', 'unverified'} for item in results)} desktop action(s)."
    if unverified:
        summary += f" The driver could not verify {unverified} of them; check the screenshot before continuing."
    if any(item["status"] == "error" for item in results):
        status = "partial"
    else:
        status = "unverified" if unverified else "ok"
    return envelope(
        status=status,
        summary=summary,
        artifacts=artifacts,
        ui=ui_panel(
            title="Desktop control",
            summary=(
                f"Confirmed desktop action batch complete through {engine}."
                if status == "ok"
                else summary
            ),
            primary_artifact_label="Desktop screenshot" if screenshot_path else None,
            tone="computer-use",
        ),
        provenance={
            "engine": engine,
            "session_id": session_id,
            "task": task,
            "profile_id": owner_profile_id,
            "target_id": (grant or {}).get("target_id"),
            "decision_hint": decision_hint,
            "verification_hint": verification_hint,
        },
        requires_confirmation=False,
        session_id=session_id,
        action_results=results,
        readiness=readiness,
    )


# BrowserSkill executes these as a click on the target ref.
_SIGNED_REF_CLICK_ACTIONS = frozenset({"click", "submit", "check", "uncheck", "download"})
_SIGNED_REF_INPUT_ACTIONS = frozenset({"fill", "set_field", "type", "select", "press"})


def _signed_action_requires_confirmation(
    action: dict[str, Any], ref_elements: dict[str, dict[str, str]]
) -> bool:
    """Classify a signed-in action by the element its ``@eN`` ref names.

    A bare ref carries no label, so "Send" or "Place order" would otherwise pass
    as "e5". Resolve it from the latest observation and apply the same risk
    check used for isolated-browser targets; a ref that is missing or has no
    accessible label cannot be classified, so it needs approval.
    """
    if _action_requires_confirmation(action):
        return True
    kind = action["action"]
    if kind not in _SIGNED_REF_CLICK_ACTIONS | _SIGNED_REF_INPUT_ACTIONS:
        return False
    target = action.get("target") if isinstance(action.get("target"), dict) else {}
    ref = str(action.get("ref") or target.get("ref") or "").strip().removeprefix("@")
    if not ref:
        return False
    element = ref_elements.get(ref)
    if element is None or not (element.get("name") or element.get("placeholder")):
        return True
    resolved_target = {
        key: element[key] for key in ("role", "name", "placeholder") if element.get(key)
    }
    kinds = {kind, "click"} if kind in _SIGNED_REF_CLICK_ACTIONS else {kind}
    return any(
        _action_requires_confirmation({**action, "action": resolved_kind, "ref": None, "target": resolved_target})
        for resolved_kind in kinds
    )


def _signed_browser_use(
    *,
    task: str,
    start_url: str,
    session_id: str,
    target_id: str,
    actions: list[dict[str, Any]],
    dry_run: bool,
    confirmed: bool,
    timeout_s: int,
    owner_profile_id: str,
) -> dict[str, Any]:
    from browser_skill_adapter import (
        BrowserSkillError,
        close_browser_skill_session,
        execute_browser_skill_actions,
        observation_ref_elements,
        observe_browser_skill_session,
        open_browser_skill_session,
    )
    from interaction_targets import resolve_interaction_target

    browser_instance_id = ""
    if not session_id:
        grant = resolve_interaction_target(
            "browser_skill", target_id, profile_id=owner_profile_id
        )
        if grant:
            browser_instance_id = str(grant.get("external_id") or "")
        elif target_id or owner_profile_id != "default":
            return envelope(
                status="unavailable",
                summary="The selected signed-in browser is not granted to this Narad profile.",
                error="browser_target_unavailable",
            )

    try:
        session, created = open_browser_skill_session(
            task=task,
            session_id=session_id,
            browser_instance_id=browser_instance_id,
            owner_profile_id=owner_profile_id,
        )
        if start_url and created:
            navigation = execute_browser_skill_actions(
                session.session_id,
                [{"action": "navigate", "url": start_url}],
                owner_profile_id=owner_profile_id,
                timeout_s=timeout_s,
            )
            if not navigation or navigation[0].get("status") != "ok":
                reason = navigation[0].get("error") if navigation else "navigation failed"
                raise BrowserSkillError(str(reason))
        planned = list(actions)

        if planned and planned[-1]["action"] == "close":
            if len(planned) > 1:
                observation = observe_browser_skill_session(
                    session.session_id, owner_profile_id=owner_profile_id
                )
                observation["prompt_injection_signals"] = _injection_signals(
                    str(observation.get("text") or "")
                )
                return _browser_envelope(
                    session_id=session.session_id,
                    task=task,
                    observation=observation,
                    planned_actions=planned,
                    status="error",
                    summary="close must be the only action in its batch",
                    error="invalid_close_batch",
                    session_created=created,
                    owner_profile_id=owner_profile_id,
                    engine="browser_skill",
                )
            close_browser_skill_session(
                session.session_id, owner_profile_id=owner_profile_id
            )
            return envelope(
                status="ok",
                summary=f"Signed-in browser session {session.session_id} closed.",
                artifacts=_browser_artifacts(
                    session.session_id, owner_profile_id=owner_profile_id
                ),
                provenance={
                    "engine": "browser_skill",
                    "session_id": session.session_id,
                    "profile_id": owner_profile_id,
                },
                session_id=session.session_id,
            )

        observation = observe_browser_skill_session(
            session.session_id, owner_profile_id=owner_profile_id
        )
        signals = _injection_signals(str(observation.get("text") or ""))
        observation["prompt_injection_signals"] = signals
        # This observation rebuilt BrowserSkill's ref store, so its labels are
        # the elements the planned refs will actually hit.
        ref_elements = observation_ref_elements(str(observation.get("text") or ""))
        needs_confirmation = any(
            _signed_action_requires_confirmation(action, ref_elements) for action in planned
        )
        mutating = any(
            action["action"] not in {"wait", "scroll", "hover", "screenshot", "request_help"}
            for action in planned
        )
        if planned and dry_run:
            return _browser_envelope(
                session_id=session.session_id,
                task=task,
                observation=observation,
                planned_actions=planned,
                status="preview",
                summary=f"Prepared {len(planned)} signed-in browser action(s); nothing was executed.",
                requires_confirmation=needs_confirmation or bool(signals and mutating),
                session_created=created,
                owner_profile_id=owner_profile_id,
                engine="browser_skill",
            )
        if signals and mutating and not confirmed:
            return _browser_envelope(
                session_id=session.session_id,
                task=task,
                observation=observation,
                planned_actions=planned,
                status="confirmation_required",
                summary="Possible prompt-injection language was detected in the signed-in page.",
                requires_confirmation=True,
                session_created=created,
                owner_profile_id=owner_profile_id,
                engine="browser_skill",
            )
        if needs_confirmation and not confirmed:
            return _browser_envelope(
                session_id=session.session_id,
                task=task,
                observation=observation,
                planned_actions=planned,
                status="confirmation_required",
                summary="The signed-in browser action may create an external side effect.",
                requires_confirmation=True,
                session_created=created,
                owner_profile_id=owner_profile_id,
                engine="browser_skill",
            )
        if needs_confirmation:
            gate_error = _dharma_gate(
                "browser_submit",
                f"signed-in browser batch ({len(planned)} actions)",
                {"session_id": session.session_id, "profile_id": owner_profile_id},
            )
            if gate_error:
                return _browser_envelope(
                    session_id=session.session_id,
                    task=task,
                    observation=observation,
                    planned_actions=planned,
                    status="blocked",
                    summary=gate_error,
                    session_created=created,
                    owner_profile_id=owner_profile_id,
                    engine="browser_skill",
                )

        results = execute_browser_skill_actions(
            session.session_id,
            planned,
            owner_profile_id=owner_profile_id,
            timeout_s=timeout_s,
        ) if planned else []
        observation = observe_browser_skill_session(
            session.session_id, owner_profile_id=owner_profile_id
        )
        observation["prompt_injection_signals"] = _injection_signals(
            str(observation.get("text") or "")
        )
        complete = sum(item.get("status") == "ok" for item in results)
        status = "ok" if all(item.get("status") == "ok" for item in results) else "partial"
        unknown_effect = any(
            item.get("effect_state") == "unknown"
            or (item.get("status") != "ok" and item.get("effect_state") == "committed")
            for item in results
        )
        summary = (
            f"Signed-in browser session ready on {observation.get('title') or observation.get('url')}."
            if not planned
            else f"Executed {complete} of {len(planned)} signed-in browser action(s)."
        )
        if unknown_effect:
            summary += " One action may already have taken effect; inspect before retrying."
        return _browser_envelope(
            session_id=session.session_id,
            task=task,
            observation=observation,
            action_results=results,
            status=status,
            summary=summary,
            session_created=created,
            owner_profile_id=owner_profile_id,
            engine="browser_skill",
        )
    except KeyError as exc:
        return envelope(status="error", summary=str(exc), error="browser_session_not_found")
    except PermissionError as exc:
        return envelope(status="blocked", summary=str(exc), error="browser_session_forbidden")
    except BrowserSkillError as exc:
        return envelope(
            status="error",
            summary=str(exc),
            error="browser_skill_failed",
            effect_state=exc.effect_state,
            provenance={"engine": "browser_skill", "profile_id": owner_profile_id},
        )


def computer_use(
    task: str,
    start_url: str = "",
    session_id: str = "",
    actions: list[dict[str, Any]] | None = None,
    environment: str = "browser",
    browser_context: str = "isolated",
    target_id: str = "",
    dry_run: bool = True,
    confirmed: bool = False,
    timeout_s: int = 180,
) -> dict[str, Any]:
    """Observe and operate a persistent browser or an explicitly enabled desktop.

    Use one returned ``session_id`` for the full task. Browser observations expose
    semantic element refs such as ``e12``; later actions can target those refs.
    Multiple actions may be batched to avoid one model round-trip per keystroke.

    Args:
        task: User goal for provenance and session continuity.
        start_url: Initial browser URL. Omit after the session has started.
        session_id: Existing session id to continue, or empty to create one.
        actions: Action objects using an `action` key (`type` and `kind`
            are accepted aliases). Browser actions include navigate, click, hover,
            fill, set_field, type, select, check, press, scroll, wait, download, upload, submit,
            screenshot, back, forward, reload, and close.
        environment: ``browser`` (default) or ``desktop``.
        browser_context: ``isolated`` for Playwright or ``signed_in`` for a
            profile-granted BrowserSkill Chromium instance.
        target_id: Opaque profile-granted target id for a signed-in browser.
        dry_run: True previews the action batch without executing it. An empty
            action list still opens/observes the browser because that is read-only.
        confirmed: Set only after the user explicitly approves a warned external
            side effect. Desktop input always requires confirmation.
        timeout_s: Overall operation timeout, clamped to 5-300 seconds.

    Returns:
        Standard Narad tool envelope with session id, compact observation,
        screenshot/trace/replay artifacts, action results, and safety state.
    """
    environment = (environment or "browser").strip().lower()
    if environment not in {"browser", "desktop"}:
        return envelope(
            status="error",
            summary="environment must be 'browser' or 'desktop'",
            error="invalid_environment",
        )
    browser_context = (browser_context or "isolated").strip().lower()
    if browser_context == "auto":
        browser_context = "signed_in" if str(session_id).startswith("signed_") else "isolated"
    if browser_context not in {"isolated", "signed_in"}:
        return envelope(
            status="error",
            summary="browser_context must be 'isolated' or 'signed_in'",
            error="invalid_browser_context",
        )
    owner_profile_id = validate_profile_id(current_profile_id())
    timeout_s = max(5, min(int(timeout_s), 300))
    try:
        resolved_session_id = _safe_session_id(session_id)
        normalised = _normalise_actions(actions, environment)
        if start_url:
            start_url = _validate_url(start_url)
    except (TypeError, ValueError) as exc:
        return envelope(status="error", summary=str(exc), error="invalid_computer_use_request")

    if environment == "desktop":
        return _desktop_use(
            task=task,
            session_id=resolved_session_id.replace("browser_", "desktop_", 1),
            actions=normalised,
            dry_run=dry_run,
            confirmed=confirmed,
            owner_profile_id=owner_profile_id,
            target_id=target_id,
        )

    if browser_context == "signed_in":
        return _signed_browser_use(
            task=task,
            start_url=start_url,
            session_id=session_id,
            target_id=target_id,
            actions=normalised,
            dry_run=dry_run,
            confirmed=confirmed,
            timeout_s=timeout_s,
            owner_profile_id=owner_profile_id,
        )
    if any(action["action"] == "request_help" for action in normalised):
        return envelope(
            status="error",
            summary="request_help is available only with browser_context='signed_in'",
            error="request_help_requires_signed_in_browser",
        )

    try:
        session, created = _BROWSER_MANAGER.open(
            task=task,
            start_url=start_url,
            session_id=resolved_session_id,
            owner_profile_id=owner_profile_id,
            timeout_s=timeout_s,
        )
        if normalised and normalised[-1]["action"] == "close":
            if len(normalised) > 1:
                return _browser_envelope(
                    session_id=session.session_id,
                    task=task,
                    observation=_BROWSER_MANAGER.observe(
                        session.session_id,
                        owner_profile_id=owner_profile_id,
                        timeout_s=timeout_s,
                    ),
                    planned_actions=normalised,
                    status="error",
                    summary="close must be the only action in its batch",
                    error="invalid_close_batch",
                    session_created=created,
                    owner_profile_id=owner_profile_id,
                )
            closed = _BROWSER_MANAGER.close(
                session.session_id, owner_profile_id=owner_profile_id
            )
            return envelope(
                status="ok",
                summary=f"Browser session {session.session_id} closed." if closed else "Browser session was already closed.",
                artifacts=_browser_artifacts(
                    session.session_id, owner_profile_id=owner_profile_id
                ),
                ui=ui_panel(title="Computer use", summary="Persistent browser session closed.", tone="computer-use"),
                provenance={
                    "engine": "playwright",
                    "session_id": session.session_id,
                    "profile_id": owner_profile_id,
                },
                requires_confirmation=False,
                session_id=session.session_id,
            )

        if normalised and dry_run:
            observation = _BROWSER_MANAGER.observe(
                session.session_id,
                owner_profile_id=owner_profile_id,
                timeout_s=timeout_s,
            )
            needs_confirmation = any(_action_requires_confirmation(action) for action in normalised)
            return _browser_envelope(
                session_id=session.session_id,
                task=task,
                observation=observation,
                planned_actions=normalised,
                status="preview",
                summary=f"Prepared {len(normalised)} browser action(s); no actions were executed.",
                requires_confirmation=needs_confirmation,
                session_created=created,
                owner_profile_id=owner_profile_id,
            )

        action_result: dict[str, Any] = {
            "status": "ok",
            "action_results": [],
            "requires_confirmation": False,
        }
        if normalised:
            action_result = _BROWSER_MANAGER.execute(
                session.session_id,
                normalised,
                owner_profile_id=owner_profile_id,
                confirmed=confirmed,
                timeout_s=timeout_s,
            )
        observation = _BROWSER_MANAGER.observe(
            session.session_id,
            owner_profile_id=owner_profile_id,
            timeout_s=timeout_s,
        )
        if action_result["status"] in {"confirmation_required", "blocked"}:
            return _browser_envelope(
                session_id=session.session_id,
                task=task,
                observation=observation,
                action_results=action_result.get("action_results"),
                planned_actions=normalised,
                status=action_result["status"],
                summary=str(action_result.get("reason", "Action batch needs confirmation.")),
                requires_confirmation=bool(action_result.get("requires_confirmation")),
                session_created=created,
                owner_profile_id=owner_profile_id,
            )
        completed = sum(item.get("status") == "ok" for item in action_result.get("action_results", []))
        summary = (
            f"Browser session ready on {observation.get('title') or observation.get('url')}."
            if not normalised
            else f"Executed {completed} of {len(normalised)} browser action(s); the session remains open."
        )
        return _browser_envelope(
            session_id=session.session_id,
            task=task,
            observation=observation,
            action_results=action_result.get("action_results"),
            status=action_result.get("status", "ok"),
            summary=summary,
            session_created=created,
            owner_profile_id=owner_profile_id,
        )
    except KeyError as exc:
        return envelope(status="error", summary=str(exc), error="browser_session_not_found")
    except PermissionError as exc:
        return envelope(status="blocked", summary=str(exc), error="browser_session_forbidden")
    except Exception as exc:
        return envelope(
            status="error",
            summary=f"Browser runtime failed: {exc}",
            error=f"{type(exc).__name__}: {exc}",
            provenance={
                "engine": "playwright",
                "session_id": resolved_session_id,
                "profile_id": owner_profile_id,
            },
            session_id=resolved_session_id,
        )


def browser_runtime_status() -> dict[str, Any]:
    """Return a cheap capability probe without launching Chromium."""
    playwright_available = importlib.util.find_spec("playwright") is not None
    desktop = _desktop_driver_status()
    try:
        from browser_skill_adapter import browser_skill_status

        signed_in = browser_skill_status()
    except Exception as exc:
        signed_in = {
            "available": False,
            "ready": False,
            "reason": f"BrowserSkill status failed: {type(exc).__name__}: {exc}",
        }
    return {
        "available": playwright_available,
        "engine": "playwright",
        "managed": True,
        "persistent_sessions": True,
        "isolated_contexts": True,
        "batched_actions": True,
        "active_sessions": _BROWSER_MANAGER.active_session_count,
        "launch_error": _BROWSER_MANAGER.launch_error,
        "reason": None if playwright_available else "Playwright is not installed",
        "contexts": {
            "isolated": {
                "available": playwright_available,
                "engine": "playwright",
                "authenticated": False,
            },
            "signed_in": {
                **signed_in,
                "engine": "browser_skill",
                "authenticated": True,
                "high_trust": True,
            },
        },
        "desktop": desktop,
    }


def shutdown_computer_use() -> None:
    """Close active browser contexts and the managed Chromium process."""
    _BROWSER_MANAGER.shutdown()
    try:
        from browser_skill_adapter import shutdown_browser_skill_sessions

        shutdown_browser_skill_sessions()
    except Exception:
        pass
