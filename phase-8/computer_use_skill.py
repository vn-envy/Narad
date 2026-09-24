"""Persistent, safety-gated browser and desktop control for Matsya.

The browser runtime lives on one dedicated asyncio loop. This matters because
Playwright objects are loop-bound: recreating a loop for every tool call loses
cookies, navigation state, form previews, and the page itself.
"""

from __future__ import annotations

import asyncio
import atexit
import importlib.util
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from narad_config import ARTIFACTS_DIR
from profile_context import current_profile_id, validate_profile_id
from risk_policy import COMMIT, Verdict, action_target_text, classify_browser_action, element_label
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


def _env_hosts(name: str) -> set[str]:
    return {
        item.strip().lower().rstrip(".")
        for item in os.environ.get(name, "").split(",")
        if item.strip()
    }


def _private_address_reason(hostname: str) -> str | None:
    """Why a browser must not reach `hostname`: it resolves off the public internet.

    Covers loopback (Narad's own API, which trusts loopback in local mode, and
    the Artemis admin), RFC 1918 LANs, CGNAT/tailnet, link-local metadata,
    unique-local and unspecified addresses, including numeric spellings such
    as 2130706433 or [::ffff:127.0.0.1] that the resolver normalises."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except (OSError, UnicodeError):
        return None  # unresolvable: the browser reports its own clear error
    for info in infos:
        try:
            address = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        except ValueError:
            continue
        mapped = getattr(address, "ipv4_mapped", None)
        if mapped is not None:
            address = mapped
        if not address.is_global:
            return f"Navigation to {hostname} ({address}) is blocked: it is not a public internet address"
    return None


def _validate_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must be a complete http:// or https:// address")
    if parsed.username or parsed.password:
        raise ValueError("Credentials embedded in URLs are not allowed")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in _BLOCKED_NETWORK_HOSTS:
        raise ValueError(f"Navigation to protected metadata host {hostname} is blocked")
    # Loopback and LAN hosts only when the owner names them explicitly.
    if hostname not in _env_hosts("NARAD_BROWSER_PRIVATE_HOSTS"):
        reason = _private_address_reason(hostname)
        if reason:
            raise ValueError(reason)

    blocklist = _env_hosts("NARAD_BROWSER_BLOCKLIST")
    if any(hostname == item or hostname.endswith(f".{item}") for item in blocklist):
        raise ValueError(f"Navigation to {hostname} is blocked by NARAD_BROWSER_BLOCKLIST")

    allowlist = _env_hosts("NARAD_BROWSER_ALLOWLIST")
    if allowlist and not any(hostname == item or hostname.endswith(f".{item}") for item in allowlist):
        raise ValueError(f"Navigation to {hostname} is outside NARAD_BROWSER_ALLOWLIST")
    return parsed.geturl()


# Blank tabs and Chromium's own error page: nothing was fetched for them.
_BLANK_PAGE_URLS = frozenset({
    "about:blank", "chrome://newtab/", "chrome://new-tab-page/", "chrome-error://chromewebdata/",
})


class NavigationRefused(ValueError):
    """The browser reached an address the URL policy refuses and was taken off it."""


def _landed_url_refusal(url: str) -> str | None:
    """Why a page may not stay where it actually landed (None: it may).

    ``_validate_url`` checks an address before navigating; a redirect, a link,
    a history move or a new tab can still end somewhere it would refuse, so
    the final address gets the same policy."""
    if not url or url in _BLANK_PAGE_URLS:
        return None
    try:
        _validate_url(url)
    except ValueError as exc:
        return f"Stopped: the browser was sent to an address Narad does not allow. {exc}"
    return None


def _upload_path(value: Any) -> Path:
    """A file the active profile may hand to a web form (never Narad's secrets)."""
    from host_access import path_access_error

    path = Path(str(value)).expanduser().resolve()
    denied = path_access_error(path)
    if denied:
        raise ValueError(f"Upload blocked: {denied}")
    if not path.is_file():
        raise ValueError(f"Upload file does not exist: {path}")
    return path


def _injection_signals(text: str) -> list[str]:
    signals: list[str] = []
    for pattern in _PROMPT_INJECTION_PATTERNS:
        match = pattern.search(text or "")
        if match:
            signals.append(match.group(0)[:120])
    return signals[:5]


def _action_target_text(action: dict[str, Any]) -> str:
    return action_target_text(action)


def _action_requires_confirmation(action: dict[str, Any], environment: str = "browser") -> bool:
    """Static risk check (risk_policy v2): True for commit-class actions."""
    return classify_browser_action(action, environment=environment).needs_approval


# What an element on the page really is, for the risk check: its label, its
# type (a <button> submits only inside a form), and whether it sits in a
# search form or a cookie/consent banner.
_ELEMENT_DETAILS_JS = """el => {
    const form = el.form || el.closest('form');
    const tag = el.tagName.toLowerCase();
    const type = tag === 'button'
        ? (el.getAttribute('type') || (form ? 'submit' : 'button'))
        : (el.getAttribute('type') || el.type || '');
    const box = el.closest('[role="dialog"],[role="alertdialog"],dialog,[aria-modal="true"],'
        + '[id*="cookie" i],[class*="cookie" i],[id*="consent" i],[class*="consent" i]');
    return {
        tag,
        type: String(type).toLowerCase(),
        role: el.getAttribute('role') || '',
        text: (el.innerText || (tag === 'input' ? el.value : '') || '').trim().slice(0, 160),
        aria: el.getAttribute('aria-label') || '',
        label: ((el.labels && el.labels[0] && el.labels[0].innerText) || '').trim().slice(0, 160),
        placeholder: el.getAttribute('placeholder') || '',
        title: el.getAttribute('title') || '',
        name: el.getAttribute('name') || '',
        in_search_form: Boolean(form && (form.getAttribute('role') === 'search'
            || form.querySelector('input[type="search"]') || /search/i.test(form.getAttribute('action') || ''))),
        context: box ? [box.id, String(box.className || ''), box.getAttribute('aria-label') || '',
            (box.innerText || '').slice(0, 300)].join(' ') : '',
    };
}"""
# Actions whose target element is looked up on the page before classifying.
_RESOLVED_ACTIONS = frozenset({"click", "submit", "press", "fill", "set_field", "type", "select", "check", "uncheck"})


def _target_name(action: dict[str, Any]) -> str:
    target = action.get("target") if isinstance(action.get("target"), dict) else {}
    for key in ("name", "label", "text", "placeholder", "query", "intent", "ref", "selector"):
        value = target.get(key) or action.get(key)
        if value:
            return str(value)
    return str(action.get("target") or "") if isinstance(action.get("target"), str) else ""


def _steps_summary(actions: list[dict[str, Any]], labels: list[str | None], where: str) -> str:
    """Plain-language steps built from the actual actions, never the model's prose."""
    steps: list[str] = []
    for index, action in enumerate(actions):
        kind = action["action"]
        label = labels[index] if index < len(labels) else None
        name = str(label or _target_name(action) or "the page")[:60]
        if kind in {"fill", "set_field", "type", "select"}:
            value = str(action.get("value", action.get("text", "")))
            if classify_browser_action({"action": "fill", "target": {"label": name}}).category == "sensitive_input":
                value = "••••"
            steps.append(f'enter "{value[:60]}" in "{name}"')
        elif kind in {"check", "uncheck"}:
            steps.append(f'{kind} "{name}"')
        elif kind == "upload":
            paths = action.get("paths", action.get("path", []))
            files = [Path(str(item)).name for item in ([paths] if isinstance(paths, str) else paths or [])]
            steps.append(f'upload {", ".join(files) or "a file"} to "{name}"')
        elif kind == "press":
            steps.append(f'press {action.get("key", "")} in "{name}"')
        elif kind in {"click", "double_click"} and action.get("x") is not None:
            steps.append(f"{kind.replace('_', ' ')} at ({action.get('x')}, {action.get('y')})")
        elif kind in {"click", "submit"}:
            steps.append(f'{kind} "{name}"')
        elif kind == "navigate":
            steps.append(f"open {str(action.get('url', ''))[:80]}")
        elif kind == "hotkey":
            steps.append(f"press {'+'.join(str(key) for key in action.get('keys') or [])}")
        elif kind == "drag":
            steps.append(f"drag from ({action.get('x1')}, {action.get('y1')}) to ({action.get('x2')}, {action.get('y2')})")
        else:
            steps.append(kind.replace("_", " "))
    shown = steps[:5] + ([f"and {len(steps) - 5} more step(s)"] if len(steps) > 5 else [])
    text = "; ".join(shown) or "no steps"
    return f"On {where}: {text}" if where else text[:1].upper() + text[1:]


def _host_of(url: str) -> str:
    return (urlparse(url or "").hostname or url or "the page").removeprefix("www.")


def _media_path(path: str | Path | None) -> str | None:
    """A /media path the app can load for this profile's own capture."""
    if not path:
        return None
    try:
        relative = Path(path).resolve().relative_to(ARTIFACTS_DIR.resolve())
    except (ValueError, OSError):
        return None
    return f"/media/{relative.as_posix()}"


def _verdict_payload(verdict: Verdict) -> dict[str, str]:
    return {"risk": verdict.risk, "category": verdict.category, "reason": verdict.reason}


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
            # Wait while still holding the lock: offloaded tools call in from
            # several worker threads, and one that saw this thread alive with
            # no loop yet would otherwise start a second loop (and Chromium).
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
        # A public URL may redirect to a private one; never show its content.
        refusal = _landed_url_refusal(page.url)
        if refusal:
            await page.goto("about:blank")
            raise NavigationRefused(refusal)
        try:
            await page.wait_for_load_state("networkidle", timeout=3_000)
        except Exception:
            pass

    async def _leave_refused_pages(self, session: _BrowserSession) -> str | None:
        """Take every tab off an address the URL policy refuses, before anything
        reads it: close a refused popup, blank the last tab. Returns the refusal."""
        refusal: str | None = None
        for page in list(session.context.pages):
            reason = _landed_url_refusal(page.url)
            if reason is None:
                continue
            refusal = refusal or reason
            if len(session.context.pages) > 1:
                await page.close()
            else:
                await page.goto("about:blank")
        if refusal:
            session.page = session.context.pages[-1]
            self._record(session, "navigation_refused", {"reason": refusal})
        return refusal

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
        code = f'''"""Generated by Narad computer_use. Review before running.

Commit-class steps (submit, pay, send, upload, ...) stop and wait for approval
in the Narad app, exactly as they did in the original session.
"""
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
        # A late script or meta redirect can move the page after the last check.
        refusal = await self._leave_refused_pages(session)
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
        observation = {
            "url": page.url,
            "title": await page.title(),
            "viewport": dict(_DEFAULT_VIEWPORT),
            "text": data.get("text", ""),
            "interactive_elements": data.get("interactive", []),
            "fields": data.get("fields", []),
            "prompt_injection_signals": signals,
            "screenshot_path": str(screenshot_path) if screenshot_path else None,
        }
        if refusal:
            observation["navigation_refused"] = refusal
        return observation

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

    async def _element_details(self, page: Any, action: dict[str, Any]) -> dict[str, Any] | None:
        """What the action's target is on the page, or None if it cannot be resolved."""
        kind = action["action"]
        if kind not in _RESOLVED_ACTIONS or action.get("x") is not None or action.get("y") is not None:
            return None
        try:
            if not _action_target_text(action):
                if kind != "press":
                    return None
                # A key press with no target lands on the focused element.
                return await page.evaluate(
                    f"() => {{ const el = document.activeElement; "
                    f"return el && el !== document.body ? ({_ELEMENT_DETAILS_JS})(el) : null; }}"
                )
            locator = await self._locator(page, action)
            return await locator.evaluate(_ELEMENT_DETAILS_JS)
        except Exception:
            return None

    async def _classify(self, page: Any, action: dict[str, Any], injection: bool) -> tuple[Verdict, dict | None]:
        details = await self._element_details(page, action)
        return classify_browser_action(action, details, injection=injection), details

    async def _approved_page_mismatch(
        self, session: _BrowserSession, actions: list[dict[str, Any]], approved: dict[str, Any]
    ) -> str | None:
        """Why an approved batch may not run here: the page or its target changed."""
        if session.page.url != approved.get("page_url"):
            return (
                f"The page changed after this was approved (it is now on {_host_of(session.page.url)}); "
                "nothing was done."
            )
        expected = approved.get("target_label")
        if expected and actions:
            details = await self._element_details(session.page, actions[0])
            if element_label(details) != expected:
                return f'"{expected}" is no longer on the page as approved; nothing was done.'
        return None

    async def _approval_screenshot(self, session: _BrowserSession) -> str | None:
        session.screenshot_count += 1
        path = session.run_dir / f"approval-{session.screenshot_count:04d}.png"
        try:
            await session.page.screenshot(path=str(path), full_page=False)
        except Exception:
            return None
        return str(path)

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
                path = _upload_path(value)
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
        timeout_s: int,
        approval_check: Callable[[dict[str, Any]], Any] | None = None,
        approved: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a batch; benign steps run, the first commit-class step needs approval.

        ``approval_check(request)`` may return an Anumati gate for the rest of
        the batch from that step on (approved: consumed; already_executed).
        ``approved`` holds the arguments of an approval being carried out: the
        page and target must still be the ones approved, and the whole batch
        then runs without asking again.
        """
        async def _execute() -> dict[str, Any]:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(f"Unknown or expired browser session: {session_id}")
            if session.owner_profile_id != owner_profile_id:
                raise PermissionError("This browser session belongs to another Narad profile")

            def _refused(reason: str, results: list[dict[str, Any]]) -> dict[str, Any]:
                return {
                    "status": "blocked",
                    "requires_confirmation": False,
                    "reason": reason,
                    "error": "navigation_refused",
                    "action_results": results,
                }

            refusal = await self._leave_refused_pages(session)
            if refusal:
                return _refused(refusal, [])
            if approved is not None:
                mismatch = await self._approved_page_mismatch(session, actions, approved)
                if mismatch:
                    return {
                        "status": "blocked",
                        "requires_confirmation": False,
                        "reason": mismatch,
                        "error": "page_changed",
                        "action_results": [],
                    }
            page_text = await session.page.evaluate("() => (document.body?.innerText || '').slice(0, 7000)")
            injection = _injection_signals(str(page_text))

            results: list[dict[str, Any]] = []
            cleared = approved is not None  # the rest of the batch is approved
            for index, action in enumerate(actions):
                verdict, details = await self._classify(session.page, action, bool(injection))
                if verdict.needs_approval and not cleared:
                    request = {
                        "surface": "browser",
                        "action": verdict.category,
                        "target": f"{session.session_id} @ {session.page.url}",
                        "risk_class": verdict.category,
                        "reason": verdict.reason,
                        "args": {
                            "session_id": session.session_id,
                            "page_url": session.page.url,
                            "actions": actions[index:],
                            "target_label": element_label(details) or None,
                        },
                    }
                    gate = approval_check(request) if approval_check is not None else None
                    if gate is not None and gate.approved:
                        cleared = True
                    elif gate is not None:
                        return {
                            "status": "already_done",
                            "requires_confirmation": False,
                            "proposal": gate.proposal,
                            "action_results": results,
                        }
                    else:
                        return {
                            "status": "needs_approval",
                            "requires_confirmation": True,
                            "reason": verdict.reason,
                            "approval_request": request,
                            "screenshot_path": await self._approval_screenshot(session),
                            "page_title": await session.page.title(),
                            "prompt_injection_signals": injection,
                            "action_results": results,
                        }
                if verdict.needs_approval:
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
                    # A click, submit, key press or history move can navigate too.
                    refusal = await self._leave_refused_pages(session)
                except NavigationRefused as exc:
                    refusal = str(exc)
                    results.append({"action": action["action"], "status": "refused", "error": refusal})
                except Exception as exc:
                    results.append({
                        "action": action["action"],
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    break
                if refusal:
                    if results[-1]["status"] == "ok":
                        results[-1] = {
                            **results[-1], "status": "refused", "error": refusal, "url": session.page.url,
                        }
                    return _refused(refusal, results)
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
    **extra: Any,
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
        **extra,
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
                    {"title": "Safety", "body": "Desktop input always waits for approval in the Narad app."},
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
    consumed = None
    if needs_confirmation:
        import anumati

        gate = anumati.require(
            **_desktop_approval_spec(task, actions, grant), profile_id=owner_profile_id
        )
        if gate.status == "needs_approval":
            return anumati.needs_approval_result(
                gate.proposal,
                provenance={"engine": engine, "session_id": session_id, "profile_id": owner_profile_id},
                session_id=session_id,
                planned_actions=actions,
                readiness=readiness,
            )
        if gate.status == "already_executed":
            return anumati.already_executed_result(gate.proposal, session_id=session_id)
        consumed = gate.proposal
    result = _run_desktop_batch(
        task=task, session_id=session_id, actions=actions, owner_profile_id=owner_profile_id,
        grant=grant, readiness=readiness, decision_hint=decision_hint,
    )
    if consumed is not None:
        import anumati

        anumati.record_result(consumed.proposal_id, result, profile_id=owner_profile_id)
    return result


def _desktop_approval_spec(task: str, actions: list[dict[str, Any]], grant: dict[str, Any]) -> dict[str, Any]:
    """The hash-bound part of a desktop batch: the granted target and the exact steps."""
    target_id = str(grant.get("target_id") or "")
    return {
        "surface": "desktop",
        "action": "input",
        "target": f"Narad host desktop ({target_id or 'host-primary'})",
        "args": {"target_id": target_id, "actions": actions},
        "summary": _steps_summary(actions, [], "the Narad host desktop"),
        "risk_class": "desktop_input",
        "preview": {"kind": "desktop", "task": task[:300], "step_count": len(actions)},
    }


def _execute_desktop_proposal(proposal: Any) -> dict[str, Any]:
    """Run an approved desktop batch exactly as approved, if the grant still holds."""
    from interaction_targets import operation_lock, resolve_interaction_target

    owner = proposal.profile_id
    with operation_lock("desktop"):
        readiness = _desktop_driver_status()
        grant = resolve_interaction_target("cua", proposal.args.get("target_id") or "", profile_id=owner)
        if grant is None:
            return {"status": "error", "summary": "The Narad host desktop is no longer granted to this profile."}
        if not readiness["available"]:
            return {"status": "unavailable", "summary": str(readiness["reason"] or "Desktop control is unavailable.")}
        return _run_desktop_batch(
            task=str(proposal.preview.get("task") or proposal.summary),
            session_id=f"desktop_{proposal.proposal_id.removeprefix('apr_')}",
            actions=list(proposal.args.get("actions") or []),
            owner_profile_id=owner,
            grant=grant,
            readiness=readiness,
            decision_hint=None,
        )


def _run_desktop_batch(
    *,
    task: str,
    session_id: str,
    actions: list[dict[str, Any]],
    owner_profile_id: str,
    grant: dict[str, Any],
    readiness: dict[str, Any],
    decision_hint: dict[str, Any] | None,
) -> dict[str, Any]:
    engine = str(readiness["selected_provider"])
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
            description="Desktop state after the approved action batch.",
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
                f"Approved desktop action batch complete through {engine}."
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


def _signed_ref(action: dict[str, Any]) -> str:
    target = action.get("target") if isinstance(action.get("target"), dict) else {}
    return str(action.get("ref") or target.get("ref") or "").strip().removeprefix("@")


def _signed_action_verdict(
    action: dict[str, Any], ref_elements: dict[str, dict[str, str]], *, injection: bool = False
) -> Verdict:
    """Classify a signed-in action by the element its ``@eN`` ref names.

    A bare ref carries no label, so "Send" or "Place order" would otherwise pass
    as "e5". Resolve it from the latest observation and apply the same risk
    policy used for isolated-browser targets; a ref that is missing or has no
    accessible label cannot be classified, so it needs approval.
    """
    kind = action["action"]
    ref = _signed_ref(action)
    if not ref or kind not in _SIGNED_REF_CLICK_ACTIONS | _SIGNED_REF_INPUT_ACTIONS:
        return classify_browser_action(action, injection=injection)
    element = ref_elements.get(ref)
    if element is None or not (element.get("name") or element.get("placeholder")):
        if injection:
            return Verdict(COMMIT, "injection", "The page contains instruction-like text")
        return Verdict(COMMIT, "unclassified", f"@{ref} has no label Narad can check")
    details = {
        "role": element.get("role"),
        "text": element.get("name"),
        "placeholder": element.get("placeholder"),
        "context": element.get("context"),
    }
    bare = {**action, "ref": None, "target": {}}
    kinds = [kind, "click"] if kind in _SIGNED_REF_CLICK_ACTIONS and kind != "click" else [kind]
    verdicts = [classify_browser_action({**bare, "action": item}, details, injection=injection) for item in kinds]
    target = action.get("target") if isinstance(action.get("target"), dict) else {}
    if any(target.get(key) or action.get(key) for key in ("name", "label", "text", "placeholder", "intent")):
        # The model's own words for the target count too: "Pay" on a ref
        # labelled "Next" is still asked about.
        verdicts.append(classify_browser_action({**action, "ref": None}, injection=injection))
    return next((verdict for verdict in verdicts if verdict.needs_approval), verdicts[0])


def _signed_navigation_refused(
    *,
    session_id: str,
    task: str,
    results: list[dict[str, Any]],
    planned: list[dict[str, Any]],
    created: bool,
    owner_profile_id: str,
) -> dict[str, Any]:
    """The signed-in tab landed on a refused address; nothing was read from it."""
    refused = next(item for item in results if item.get("status") == "refused")
    summary = str(refused.get("error") or "Stopped: the browser reached an address Narad does not allow.")
    summary += (
        " The signed-in session was closed; start a new one to continue."
        if refused.get("session_closed")
        else " The browser went back to the previous page."
    )
    return _browser_envelope(
        session_id=session_id,
        task=task,
        observation=None,
        action_results=results,
        planned_actions=planned,
        status="blocked",
        summary=summary,
        error="navigation_refused",
        session_created=created,
        owner_profile_id=owner_profile_id,
        engine="browser_skill",
    )


def _signed_browser_use(
    *,
    task: str,
    start_url: str,
    session_id: str,
    target_id: str,
    actions: list[dict[str, Any]],
    dry_run: bool,
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
            if navigation and navigation[0].get("status") == "refused":
                return _signed_navigation_refused(
                    session_id=session.session_id,
                    task=task,
                    results=navigation,
                    planned=actions,
                    created=created,
                    owner_profile_id=owner_profile_id,
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
        commit = next(
            (
                verdict
                for verdict in (
                    _signed_action_verdict(action, ref_elements, injection=bool(signals)) for action in planned
                )
                if verdict.needs_approval
            ),
            None,
        )
        if planned and dry_run:
            return _browser_envelope(
                session_id=session.session_id,
                task=task,
                observation=observation,
                planned_actions=planned,
                status="preview",
                summary=f"Prepared {len(planned)} signed-in browser action(s); nothing was executed.",
                requires_confirmation=commit is not None,
                session_created=created,
                owner_profile_id=owner_profile_id,
                engine="browser_skill",
            )
        consumed = None
        if commit is not None:
            import anumati

            gate = anumati.require(
                **_signed_approval_spec(session.session_id, observation, planned, ref_elements, commit),
                profile_id=owner_profile_id,
            )
            if gate.status == "needs_approval":
                return _browser_envelope(
                    session_id=session.session_id,
                    task=task,
                    observation=observation,
                    planned_actions=planned,
                    status="needs_approval",
                    summary=anumati.waiting_message(gate.proposal),
                    requires_confirmation=True,
                    session_created=created,
                    owner_profile_id=owner_profile_id,
                    engine="browser_skill",
                    approval=gate.proposal.to_payload(),
                    proposal_id=gate.proposal.proposal_id,
                )
            if gate.status == "already_executed":
                return anumati.already_executed_result(gate.proposal, session_id=session.session_id)
            consumed = gate.proposal
        result = _run_signed_batch(
            session_id=session.session_id,
            task=task,
            planned=planned,
            created=created,
            owner_profile_id=owner_profile_id,
            timeout_s=timeout_s,
            observation=observation,
            commit=commit is not None,
        )
        if consumed is not None:
            import anumati

            anumati.record_result(consumed.proposal_id, result, profile_id=owner_profile_id)
        return result
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


def _signed_approval_spec(
    session_id: str,
    observation: dict[str, Any],
    planned: list[dict[str, Any]],
    ref_elements: dict[str, dict[str, str]],
    verdict: Verdict,
) -> dict[str, Any]:
    """The hash-bound part of a signed-in batch: page, steps, and what each ref names."""
    url = str(observation.get("url") or "")
    refs = {ref: ref_elements.get(ref) for ref in (_signed_ref(action) for action in planned) if ref}
    labels = [
        ((ref_elements.get(_signed_ref(action)) or {}).get("name")
         or (ref_elements.get(_signed_ref(action)) or {}).get("placeholder"))
        for action in planned
    ]
    signals = observation.get("prompt_injection_signals") or []
    return {
        "surface": "signed_in_browser",
        "action": verdict.category,
        "target": f"{session_id} @ {url}",
        "args": {"session_id": session_id, "page_url": url, "actions": planned, "refs": refs},
        "summary": _steps_summary(planned, labels, f"{_host_of(url)} (signed in)"),
        "risk_class": verdict.category,
        "preview": {
            "kind": "browser",
            "signed_in": True,
            "page_url": url,
            "screenshot_url": _media_path(observation.get("screenshot_path")),
            "reason": verdict.reason,
            "warning": (
                "This page contains text that tries to instruct Narad. Check it before approving."
                if signals else None
            ),
        },
    }


def _run_signed_batch(
    *,
    session_id: str,
    task: str,
    planned: list[dict[str, Any]],
    created: bool,
    owner_profile_id: str,
    timeout_s: int,
    observation: dict[str, Any] | None,
    commit: bool,
) -> dict[str, Any]:
    from browser_skill_adapter import execute_browser_skill_actions, observe_browser_skill_session

    if commit:
        gate_error = _dharma_gate(
            "browser_submit",
            f"signed-in browser batch ({len(planned)} actions)",
            {"session_id": session_id, "profile_id": owner_profile_id},
        )
        if gate_error:
            return _browser_envelope(
                session_id=session_id,
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
        session_id,
        planned,
        owner_profile_id=owner_profile_id,
        timeout_s=timeout_s,
    ) if planned else []
    if any(item.get("status") == "refused" for item in results):
        return _signed_navigation_refused(
            session_id=session_id,
            task=task,
            results=results,
            planned=planned,
            created=created,
            owner_profile_id=owner_profile_id,
        )
    observation = observe_browser_skill_session(session_id, owner_profile_id=owner_profile_id)
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
        session_id=session_id,
        task=task,
        observation=observation,
        action_results=results,
        status=status,
        summary=summary,
        session_created=created,
        owner_profile_id=owner_profile_id,
        engine="browser_skill",
        url=observation.get("url"),
        screenshot_url=_media_path(observation.get("screenshot_path")),
    )


def _execute_signed_in_proposal(proposal: Any) -> dict[str, Any]:
    """Run an approved signed-in batch, only on the page and refs that were approved."""
    from browser_skill_adapter import (
        BrowserSkillError,
        observation_ref_elements,
        observe_browser_skill_session,
        open_browser_skill_session,
    )
    from interaction_targets import operation_lock

    owner = proposal.profile_id
    args = proposal.args
    session_id = str(args.get("session_id") or "")
    with operation_lock(f"signed_in:{owner}"):
        try:
            open_browser_skill_session(task=proposal.summary, session_id=session_id, owner_profile_id=owner)
            observation = observe_browser_skill_session(session_id, owner_profile_id=owner)
            url = str(observation.get("url") or "")
            if url != args.get("page_url"):
                return {
                    "status": "blocked",
                    "error": "page_changed",
                    "summary": (
                        f"The signed-in page changed after this was approved (it is now on {_host_of(url)}); "
                        "nothing was done."
                    ),
                }
            current = observation_ref_elements(str(observation.get("text") or ""))
            if any(current.get(ref) != element for ref, element in (args.get("refs") or {}).items()):
                return {
                    "status": "blocked",
                    "error": "target_changed",
                    "summary": "The buttons or fields on the page changed after this was approved; nothing was done.",
                }
            observation["prompt_injection_signals"] = _injection_signals(str(observation.get("text") or ""))
            return _run_signed_batch(
                session_id=session_id,
                task=proposal.summary,
                planned=list(args.get("actions") or []),
                created=False,
                owner_profile_id=owner,
                timeout_s=180,
                observation=observation,
                commit=True,
            )
        except KeyError:
            return {"status": "error", "summary": "The signed-in browser session has ended; nothing was done."}
        except PermissionError as exc:
            return {"status": "blocked", "summary": str(exc)}
        except BrowserSkillError as exc:
            return {"status": "error", "summary": str(exc), "error": f"effect_state={exc.effect_state}"}


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
            False runs the batch: reading, navigating, typing into fields, search,
            filters, paging and cookie banners run at once; the first commit-class
            step (submit, send, pay, book, apply, upload, delete, account change,
            a secret, all desktop input) stops the batch with status
            "needs_approval" and an approval card on the person's phone. Narad
            runs the rest of the batch itself once they tap Approve, on the same
            page, exactly as shown. Tell them it is waiting for their OK.
        confirmed: Accepted for compatibility and ignored: it approves nothing.
            Only the person's tap on the approval card does.
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

    from interaction_targets import operation_lock

    if environment == "desktop":
        with operation_lock("desktop"):  # one host desktop, shared by every profile
            return _desktop_use(
                task=task,
                session_id=resolved_session_id.replace("browser_", "desktop_", 1),
                actions=normalised,
                dry_run=dry_run,
                owner_profile_id=owner_profile_id,
                target_id=target_id,
            )

    if browser_context == "signed_in":
        with operation_lock(f"signed_in:{owner_profile_id}"):
            return _signed_browser_use(
                task=task,
                start_url=start_url,
                session_id=session_id,
                target_id=target_id,
                actions=normalised,
                dry_run=dry_run,
                timeout_s=timeout_s,
                owner_profile_id=owner_profile_id,
            )
    if any(action["action"] == "request_help" for action in normalised):
        return envelope(
            status="error",
            summary="request_help is available only with browser_context='signed_in'",
            error="request_help_requires_signed_in_browser",
        )

    session_lock = operation_lock(f"browser:{resolved_session_id}")
    session_lock.acquire()
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

        return _run_isolated_batch(
            session_id=session.session_id,
            task=task,
            actions=normalised,
            created=created,
            owner_profile_id=owner_profile_id,
            timeout_s=timeout_s,
        )
    except KeyError as exc:
        return envelope(status="error", summary=str(exc), error="browser_session_not_found")
    except PermissionError as exc:
        return envelope(status="blocked", summary=str(exc), error="browser_session_forbidden")
    except NavigationRefused as exc:
        # The start URL redirected somewhere refused: the page was blanked, and a
        # session opened for it was closed.
        return envelope(
            status="blocked",
            summary=str(exc),
            error="navigation_refused",
            provenance={
                "engine": "playwright",
                "session_id": resolved_session_id,
                "profile_id": owner_profile_id,
            },
            session_id=resolved_session_id,
        )
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
    finally:
        session_lock.release()


def _run_isolated_batch(
    *,
    session_id: str,
    task: str,
    actions: list[dict[str, Any]],
    created: bool,
    owner_profile_id: str,
    timeout_s: int,
    approved: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a batch in an open isolated session and report it.

    Benign steps run; the first commit-class step stops the batch with an
    Anumati proposal for the rest of it. ``approved`` carries out an approved
    proposal: the page and target must still match, then the rest runs.
    """
    import anumati

    consumed: list[str] = []

    def _approval_check(request: dict[str, Any]) -> Any:
        gate = anumati.check(
            surface=request["surface"],
            action=request["action"],
            target=request["target"],
            args=request["args"],
            profile_id=owner_profile_id,
        )
        if gate is not None and gate.approved:
            consumed.append(gate.proposal.proposal_id)
        return gate

    try:
        action_result: dict[str, Any] = {"status": "ok", "action_results": [], "requires_confirmation": False}
        if actions:
            action_result = _BROWSER_MANAGER.execute(
                session_id,
                actions,
                owner_profile_id=owner_profile_id,
                timeout_s=timeout_s,
                approval_check=None if approved is not None else _approval_check,
                approved=approved,
            )
        observation = _BROWSER_MANAGER.observe(session_id, owner_profile_id=owner_profile_id, timeout_s=timeout_s)
        result = _isolated_batch_envelope(
            action_result, observation,
            session_id=session_id, task=task, actions=actions, created=created, owner_profile_id=owner_profile_id,
        )
    except Exception as exc:
        for proposal_id in consumed:
            anumati.record_result(
                proposal_id, {"status": "error", "summary": f"Browser runtime failed: {exc}"}, profile_id=owner_profile_id
            )
        raise
    for proposal_id in consumed:
        anumati.record_result(proposal_id, result, profile_id=owner_profile_id)
    return result


def _isolated_batch_envelope(
    action_result: dict[str, Any],
    observation: dict[str, Any],
    *,
    session_id: str,
    task: str,
    actions: list[dict[str, Any]],
    created: bool,
    owner_profile_id: str,
) -> dict[str, Any]:
    import anumati

    status = action_result["status"]
    late_refusal = observation.get("navigation_refused")
    if late_refusal and status not in {"needs_approval", "blocked"}:
        action_result = {**action_result, "status": "blocked", "reason": late_refusal, "error": "navigation_refused"}
        status = "blocked"
    if status == "already_done":
        return anumati.already_executed_result(action_result["proposal"], session_id=session_id)
    if status == "needs_approval":
        proposal = _propose_isolated_batch(action_result, owner_profile_id)
        return _browser_envelope(
            session_id=session_id,
            task=task,
            observation=observation,
            action_results=action_result.get("action_results"),
            planned_actions=actions,
            status="needs_approval",
            summary=anumati.waiting_message(proposal),
            requires_confirmation=True,
            session_created=created,
            owner_profile_id=owner_profile_id,
            approval=proposal.to_payload(),
            proposal_id=proposal.proposal_id,
        )
    if status == "blocked":
        return _browser_envelope(
            session_id=session_id,
            task=task,
            observation=observation,
            action_results=action_result.get("action_results"),
            planned_actions=actions,
            status="blocked",
            summary=str(action_result.get("reason") or "The action batch was blocked."),
            error=action_result.get("error"),
            session_created=created,
            owner_profile_id=owner_profile_id,
        )
    completed = sum(item.get("status") == "ok" for item in action_result.get("action_results", []))
    summary = (
        f"Browser session ready on {observation.get('title') or observation.get('url')}."
        if not actions
        else f"Executed {completed} of {len(actions)} browser action(s); the session remains open."
    )
    return _browser_envelope(
        session_id=session_id,
        task=task,
        observation=observation,
        action_results=action_result.get("action_results"),
        status=action_result.get("status", "ok"),
        summary=summary,
        session_created=created,
        owner_profile_id=owner_profile_id,
        url=observation.get("url"),
        screenshot_url=_media_path(observation.get("screenshot_path")),
    )


def _propose_isolated_batch(action_result: dict[str, Any], owner_profile_id: str) -> Any:
    """Turn a stopped batch into a pending proposal with its page screenshot."""
    import anumati

    request = action_result["approval_request"]
    args = request["args"]
    url = str(args["page_url"])
    signals = action_result.get("prompt_injection_signals") or []
    proposal, _created = anumati.propose(
        surface=request["surface"],
        action=request["action"],
        target=request["target"],
        args=args,
        summary=_steps_summary(args["actions"], [args.get("target_label")], _host_of(url)),
        risk_class=request["risk_class"],
        preview={
            "kind": "browser",
            "signed_in": False,
            "page_url": url,
            "page_title": action_result.get("page_title"),
            "screenshot_url": _media_path(action_result.get("screenshot_path")),
            "reason": request["reason"],
            "warning": (
                "This page contains text that tries to instruct Narad. Check it before approving."
                if signals else None
            ),
        },
        profile_id=owner_profile_id,
    )
    return proposal


def _execute_browser_proposal(proposal: Any) -> dict[str, Any]:
    """Run an approved isolated-browser batch in its session, as approved."""
    from interaction_targets import operation_lock

    args = proposal.args
    session_id = str(args.get("session_id") or "")
    with operation_lock(f"browser:{session_id}"):
        try:
            return _run_isolated_batch(
                session_id=session_id,
                task=proposal.summary,
                actions=list(args.get("actions") or []),
                created=False,
                owner_profile_id=proposal.profile_id,
                timeout_s=180,
                approved=args,
            )
        except KeyError:
            return {
                "status": "error",
                "summary": "The browser session has closed (idle sessions end after 20 minutes); nothing was done.",
            }
        except PermissionError as exc:
            return {"status": "blocked", "summary": str(exc)}
        except Exception as exc:
            return {"status": "error", "summary": f"Browser runtime failed: {exc}"}


def _register_approvals() -> None:
    import anumati

    anumati.register_executor("browser", _execute_browser_proposal)
    anumati.register_executor("signed_in_browser", _execute_signed_in_proposal)
    anumati.register_executor("desktop", _execute_desktop_proposal)


_register_approvals()


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
