"""The browser surfaces a Kriya task drives.

``browser``: the isolated local Chromium that ``computer_use`` also uses (one
loop thread, one process), with a fresh context per task that no
``computer_use`` session can reach. ``cloud_browser``: a self-hosted remote
Chromium reached over CDP, for unauthenticated tasks only (owner decision 2):
it gets a fresh context per task on a connection kept per profile, and never
receives cookies, storage state, credentials or vault values.

Each action runs by ref with a 5 s actionability timeout, then the page
settles (navigation finished, the DOM quiet for 350 ms and no document, XHR
or fetch request in flight, all bounded at 4 s), and the result carries what
changed so the runtime can verify the step. Addresses are checked with the
same policy as ``computer_use`` before navigating and wherever a page lands.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import computer_use_skill
from computer_use_skill import (
    _BROWSER_MANAGER,
    _ELEMENT_DETAILS_JS,
    NavigationRefused,
    _BrowserSession,
    _injection_signals,
    _validate_url,
)
from kriya.perception import PAGE_STATE_JS, Observation, PageState, build_observation

VIEWPORT = {"width": 1280, "height": 800}
ACTION_TIMEOUT_MS = 5_000
NAVIGATION_TIMEOUT_MS = 30_000
SETTLE_MAX_S = 4.0
QUIET_MS = 350
FRAME_MIN_INTERVAL_S = 0.4  # at most ~2.5 frames a second, however many viewers
_TRACKED_REQUESTS = frozenset({"document", "xhr", "fetch"})
_TAKEOVER_KEYS = frozenset({
    "Enter", "Tab", "Backspace", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Delete",
})

# Counts DOM mutations in every document, so a step can tell "nothing
# happened" from "the page reacted" and the settle wait can see a quiet DOM.
_MUTATION_JS = """(() => {
    if (window.__naradMut !== undefined) return;
    window.__naradMut = 0;
    window.__naradLastMut = 0;
    try {
        new MutationObserver((records) => {
            window.__naradMut += records.length;
            window.__naradLastMut = performance.now();
        }).observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
    } catch (error) {}
})();"""


class StaleRef(LookupError):
    """The element a ref named is no longer on the page."""


@dataclass
class ActResult:
    status: str  # ok | error | refused | stale
    url_before: str = ""
    url_after: str = ""
    effect: bool = False  # the URL, the DOM, the tabs or the downloads changed
    value: str | None = None  # a field's value after typing, selecting or ticking
    checked: bool | None = None
    download: str | None = None
    dialog: str | None = None
    error: str = ""
    settle_ms: int = 0


def cloud_browser_configured() -> bool:
    return bool(os.environ.get("NARAD_CLOUD_BROWSER_URL", "").strip())


def cloud_browser_endpoint() -> str:
    """The CDP address with its token, e.g. ``wss://browser.example.com/?token=…``.

    ``NARAD_CLOUD_BROWSER_TOKEN`` is added as the ``token`` query parameter
    (``NARAD_CLOUD_BROWSER_TOKEN_PARAM`` renames it). The address never
    carries anything about the person or the task."""
    url = os.environ.get("NARAD_CLOUD_BROWSER_URL", "").strip()
    token = os.environ.get("NARAD_CLOUD_BROWSER_TOKEN", "").strip()
    if not url or not token:
        return url
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query))
    query[os.environ.get("NARAD_CLOUD_BROWSER_TOKEN_PARAM", "token").strip() or "token"] = token
    return urlunparse(parsed._replace(query=urlencode(query)))


def cloud_browser_host() -> str:
    return urlparse(os.environ.get("NARAD_CLOUD_BROWSER_URL", "")).hostname or "cloud-browser"


# One CDP connection per profile, on the browser loop: profiles never share one.
_CLOUD_BROWSERS: dict[str, Any] = {}


async def _cloud_browser(profile_id: str) -> Any:
    browser = _CLOUD_BROWSERS.get(profile_id)
    if browser is not None and browser.is_connected():
        return browser
    await _BROWSER_MANAGER._ensure_browser()  # starts Playwright on the shared loop
    browser = await _BROWSER_MANAGER._playwright.chromium.connect_over_cdp(
        cloud_browser_endpoint(), timeout=20_000
    )
    _CLOUD_BROWSERS[profile_id] = browser
    return browser


def _log_cloud_session() -> None:
    """One egress-ledger line per cloud-browser session, in the caller's profile
    (a cloud destination: logged like every model call, with no content)."""
    try:
        import privacy_gateway

        privacy_gateway.record_egress(
            model=f"cloud_browser/{cloud_browser_host()}",
            source="kriya_cloud_browser",
            tier=privacy_gateway.provider_tier("cloud_browser"),
        )
    except Exception:
        pass


def _chrome_user_agent(version: str) -> str:
    major = (version or "").split(".", 1)[0] or "140"
    return (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major}.0.0.0 Safari/537.36"
    )


class BrowserSurface:
    """One task's page. Sync methods run on the shared browser loop."""

    def __init__(self, *, task_id: str, profile_id: str, goal: str, cloud: bool = False) -> None:
        self.task_id = task_id
        self.profile_id = profile_id
        self.goal = goal
        self.kind = "cloud_browser" if cloud else "browser"
        self.run_dir = artifacts_dir(profile_id, task_id)
        self.session: _BrowserSession | None = None
        self._inflight: set[Any] = set()
        self._http_status: int | None = None
        self._dialog: str | None = None
        self._frame: tuple[float, bytes] | None = None
        self._screenshot_count = 0
        self._downloads = 0

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _run(self, coroutine: Any, timeout_s: float = 60) -> Any:
        return _BROWSER_MANAGER.run(coroutine, int(timeout_s))

    @property
    def is_open(self) -> bool:
        return self.session is not None

    @property
    def url(self) -> str:
        return self.session.page.url if self.session is not None else ""

    def open(self, start_url: str = "") -> None:
        if self.kind == "cloud_browser":
            _log_cloud_session()  # here, on the task's thread: the ledger is per profile
        self._run(self._open_async(start_url), 90)

    async def _open_async(self, start_url: str) -> None:
        manager = _BROWSER_MANAGER
        await manager._ensure_browser()
        browser = await _cloud_browser(self.profile_id) if self.kind == "cloud_browser" else manager._browser
        self.run_dir.mkdir(parents=True, exist_ok=True)
        context = await browser.new_context(
            viewport=dict(VIEWPORT),
            accept_downloads=self.kind == "browser",
            ignore_https_errors=False,
            locale="en-IN",
            user_agent=_chrome_user_agent(getattr(browser, "version", "")),
        )
        await context.add_init_script(_MUTATION_JS)
        context.on("request", self._on_request)
        context.on("requestfinished", self._on_request_done)
        context.on("requestfailed", self._on_request_done)
        context.on("response", self._on_response)
        context.on("page", self._on_page)
        page = await context.new_page()
        self._prepare_page(page)
        self.session = _BrowserSession(
            session_id=f"task_{self.task_id}",
            owner_profile_id=self.profile_id,
            context=context,
            page=page,
            run_dir=self.run_dir,
            task=self.goal[:500],
            start_url=start_url,
        )
        manager._record(self.session, "session_started", {"surface": self.kind, "start_url": start_url})
        if start_url:
            await self._navigate(start_url)

    def close(self) -> None:
        if self.session is None:
            return
        session, self.session = self.session, None
        try:
            self._run(session.context.close(), 20)
        except Exception:
            pass

    # ── page events ──────────────────────────────────────────────────────────

    def _prepare_page(self, page: Any) -> None:
        page.set_default_timeout(ACTION_TIMEOUT_MS)
        page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
        page.on("dialog", self._on_dialog)

    def _on_page(self, page: Any) -> None:
        self._prepare_page(page)

    def _on_request(self, request: Any) -> None:
        if request.resource_type in _TRACKED_REQUESTS:
            self._inflight.add(request)

    def _on_request_done(self, request: Any) -> None:
        self._inflight.discard(request)

    def _on_response(self, response: Any) -> None:
        try:
            request = response.request
            # Answered is settled enough: a fetch whose body the page never
            # reads (uncached) never reports "finished" at all.
            self._inflight.discard(request)
            if request.resource_type == "document" and self.session is not None and (
                response.frame == self.session.page.main_frame
            ):
                self._http_status = response.status
        except Exception:
            pass

    async def _on_dialog(self, dialog: Any) -> None:
        # A page's alert, confirm or prompt: Narad never answers yes for the
        # person. Alerts are acknowledged; everything else is dismissed.
        self._dialog = f"{dialog.type}: {dialog.message}"[:240]
        try:
            await (dialog.accept() if dialog.type in {"alert", "beforeunload"} else dialog.dismiss())
        except Exception:
            pass

    # ── navigation ───────────────────────────────────────────────────────────

    async def _navigate(self, url: str) -> None:
        assert self.session is not None
        self._http_status = None
        await _BROWSER_MANAGER._navigate(self.session.page, url, NAVIGATION_TIMEOUT_MS // 1000)

    async def _leave_refused(self) -> str | None:
        assert self.session is not None
        refusal = await _BROWSER_MANAGER._leave_refused_pages(self.session)
        return refusal

    # ── perception ───────────────────────────────────────────────────────────

    def observe(self, *, screenshot: bool = False) -> Observation:
        return self._run(self._observe_async(screenshot), 45)

    async def _observe_async(self, screenshot: bool) -> Observation:
        assert self.session is not None
        refusal = await self._leave_refused()
        self._follow_newest_page()
        page = self.session.page
        try:
            snapshot = await page.aria_snapshot(mode="ai", boxes=True, timeout=10_000)
        except Exception:
            await asyncio.sleep(0.5)  # a navigation replaced the document mid-read
            snapshot = await page.aria_snapshot(mode="ai", boxes=True, timeout=10_000)
        state, secrets = await self._page_state(page)
        state.refused = refusal or ""
        state.http_status = self._http_status
        state.pages = len(self.session.context.pages)
        try:
            title = await page.title()
        except Exception:
            title = ""
        observation = build_observation(
            snapshot,
            url=page.url,
            title=title,
            state=state,
            injection_scan=lambda text: _injection_signals(text[:20_000]),
            secrets=secrets,
        )
        if screenshot or observation.poor:
            observation.screenshot = await self._jpeg(page, quality=60)
        return observation

    async def _page_state(self, page: Any) -> tuple[PageState, list[str]]:
        state = PageState(viewport_height=VIEWPORT["height"], viewport_width=VIEWPORT["width"])
        secrets: list[str] = []
        for index, frame in enumerate(page.frames[:6]):
            try:
                data = await frame.evaluate(PAGE_STATE_JS)
            except Exception:
                continue
            state.password_fields += int(data.get("passwords") or 0)
            state.captcha += int(data.get("captcha") or 0)
            state.canvases += int(data.get("canvases") or 0)
            state.dialogs.extend(str(item) for item in data.get("dialogs") or [] if item is not None)
            secrets.extend(str(item) for item in data.get("secrets") or [] if item)
            if index == 0:
                state.scroll_y = int(data.get("scrollY") or 0)
                state.scroll_height = int(data.get("scrollHeight") or 0)
                state.viewport_height = int(data.get("viewportHeight") or state.viewport_height)
                state.viewport_width = int(data.get("viewportWidth") or state.viewport_width)
        return state, secrets

    def _follow_newest_page(self) -> None:
        assert self.session is not None
        pages = [page for page in self.session.context.pages if not page.is_closed()]
        if pages and self.session.page not in pages[-1:]:
            self.session.page = pages[-1]

    # ── risk inputs ──────────────────────────────────────────────────────────

    def element_details(self, ref: str) -> dict[str, Any] | None:
        """What a ref really is (tag, type, label, form, dialog), for the risk policy."""
        return self._run(self._element_details_async(ref), 20)

    async def _element_details_async(self, ref: str) -> dict[str, Any] | None:
        assert self.session is not None
        try:
            locator = self.session.page.locator(f"aria-ref={ref}")
            if await locator.count() == 0:
                return None
            return await locator.first.evaluate(_ELEMENT_DETAILS_JS)
        except Exception:
            return None

    def screenshot_file(self, prefix: str = "approval") -> str | None:
        """A PNG of the viewport in the task's artifact folder (an approval preview)."""
        return self._run(self._screenshot_file_async(prefix), 30)

    async def _screenshot_file_async(self, prefix: str) -> str | None:
        assert self.session is not None
        self._screenshot_count += 1
        path = self.run_dir / f"{prefix}-{self._screenshot_count:04d}.png"
        try:
            await self.session.page.screenshot(path=str(path), full_page=False)
        except Exception:
            return None
        return str(path)

    # ── live view ────────────────────────────────────────────────────────────

    def frame(self) -> bytes | None:
        """The latest viewport as a JPEG, kept only in memory."""
        cached = self._frame
        if cached and time.monotonic() - cached[0] < FRAME_MIN_INTERVAL_S:
            return cached[1]
        if self.session is None:
            return None
        try:
            data = self._run(self._jpeg(self.session.page, quality=55), 10)
        except Exception:
            return cached[1] if cached else None
        if data:
            self._frame = (time.monotonic(), data)
        return data

    async def _jpeg(self, page: Any, *, quality: int) -> bytes | None:
        try:
            return await page.screenshot(type="jpeg", quality=quality, full_page=False, timeout=5_000)
        except Exception:
            return None

    # ── acting ───────────────────────────────────────────────────────────────

    def execute(self, action: dict[str, Any]) -> ActResult:
        return self._run(self._execute_async(action), 60)

    async def _effect_marker(self) -> tuple[str, tuple[int, ...], int, int]:
        assert self.session is not None
        counts: list[int] = []
        for frame in self.session.page.frames[:6]:
            try:
                counts.append(int(await frame.evaluate("() => window.__naradMut || 0")))
            except Exception:
                counts.append(-1)
        return self.session.page.url, tuple(counts), len(self.session.context.pages), self._downloads

    async def _execute_async(self, action: dict[str, Any]) -> ActResult:
        assert self.session is not None
        page = self.session.page
        kind = action["action"]
        before = await self._effect_marker()
        self._dialog = None
        result = ActResult(status="ok", url_before=page.url)
        started = time.monotonic()
        try:
            await self._perform(action)
        except StaleRef as exc:
            result.status, result.error = "stale", str(exc)
        except NavigationRefused as exc:
            result.status, result.error = "refused", str(exc)
        except Exception as exc:
            result.status, result.error = "error", _short_error(exc)
        if result.status in {"ok", "error"}:
            result.settle_ms = int(await self._settle(started) * 1000)
            refusal = await self._leave_refused()
            if refusal:
                result.status, result.error = "refused", refusal
        self._follow_newest_page()
        after = await self._effect_marker()
        result.url_after = self.session.page.url
        result.effect = before != after
        result.dialog = self._dialog
        if result.status == "ok" and kind in {"fill", "type", "select", "check", "uncheck"}:
            await self._read_back(action, result)
        return result

    async def _perform(self, action: dict[str, Any]) -> None:
        assert self.session is not None
        page = self.session.page
        kind = action["action"]
        if kind == "navigate":
            await self._navigate(str(action.get("url") or ""))
            return
        if kind == "back":
            await page.go_back(wait_until="domcontentloaded", timeout=15_000)
            return
        if kind == "wait":
            await page.wait_for_timeout(max(0, min(int(action.get("ms") or 1000), 3000)))
            return
        if kind == "scroll" and not action.get("ref"):
            step = VIEWPORT["height"] * 0.8
            direction = -1 if str(action.get("direction") or "down").lower() == "up" else 1
            await page.mouse.move(VIEWPORT["width"] / 2, VIEWPORT["height"] / 2)
            await page.mouse.wheel(0, direction * step)
            return
        if kind == "press" and not action.get("ref"):
            await page.keyboard.press(str(action.get("key") or "Enter"))
            return
        ref = str(action.get("ref") or "")
        if not ref:
            raise ValueError(f"{kind} needs a ref from the current page")
        locator = page.locator(f"aria-ref={ref}")
        if await locator.count() == 0:
            raise StaleRef(f"{ref} is no longer on the page")
        locator = locator.first
        value = str(action.get("value") if action.get("value") is not None else action.get("text") or "")
        if kind == "click":
            await locator.click(timeout=ACTION_TIMEOUT_MS)
        elif kind == "scroll":
            await locator.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT_MS)
        elif kind == "fill":
            await locator.fill(value, timeout=ACTION_TIMEOUT_MS)
        elif kind == "type":
            await locator.click(timeout=ACTION_TIMEOUT_MS)
            await locator.press_sequentially(value, delay=25, timeout=ACTION_TIMEOUT_MS * 4)
        elif kind == "select":
            try:
                await locator.select_option(label=value, timeout=ACTION_TIMEOUT_MS)
            except Exception:
                await locator.select_option(value=value, timeout=ACTION_TIMEOUT_MS)
        elif kind == "check":
            await locator.check(timeout=ACTION_TIMEOUT_MS)
        elif kind == "uncheck":
            await locator.uncheck(timeout=ACTION_TIMEOUT_MS)
        elif kind == "press":
            await locator.press(str(action.get("key") or "Enter"), timeout=ACTION_TIMEOUT_MS)
        elif kind == "download":
            if self.kind != "browser":
                raise ValueError("Downloads happen only in the browser on the Mac")
            async with page.expect_download(timeout=15_000) as pending:
                await locator.click(timeout=ACTION_TIMEOUT_MS)
            download = await pending.value
            folder = self.run_dir / "downloads"
            folder.mkdir(parents=True, exist_ok=True)
            name = re.sub(r"[^A-Za-z0-9._-]+", "-", download.suggested_filename).strip(".-")
            destination = folder / (name or f"download-{self._downloads + 1}")
            await download.save_as(str(destination))
            self._downloads += 1
        else:
            raise ValueError(f"Unsupported action {kind!r}")

    async def _settle(self, started: float) -> float:
        """Wait for navigation or a quiet DOM with no requests in flight (bounded)."""
        assert self.session is not None
        deadline = started + SETTLE_MAX_S
        page = self.session.page
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=max(100, int((deadline - time.monotonic()) * 1000)))
        except Exception:
            pass
        quiet_since_network = None
        while time.monotonic() < deadline:
            self._follow_newest_page()
            page = self.session.page
            try:
                idle_ms = float(await page.evaluate("() => performance.now() - (window.__naradLastMut || 0)"))
            except Exception:
                await asyncio.sleep(0.1)  # the document is being replaced
                continue
            network_idle = not self._inflight
            if network_idle and quiet_since_network is None:
                quiet_since_network = time.monotonic()
            elif not network_idle:
                quiet_since_network = None
            dom_quiet = idle_ms >= QUIET_MS
            # Long-polling requests never finish: after 1.5 s a quiet DOM is enough.
            if dom_quiet and (network_idle or time.monotonic() - started > 1.5):
                break
            await asyncio.sleep(0.05)
        return time.monotonic() - started

    async def _read_back(self, action: dict[str, Any], result: ActResult) -> None:
        assert self.session is not None
        try:
            locator = self.session.page.locator(f"aria-ref={action.get('ref')}").first
            kind = action["action"]
            if kind in {"check", "uncheck"}:
                result.checked = await locator.is_checked(timeout=2_000)
            elif kind == "select":
                picked = await locator.evaluate(
                    "el => el.selectedOptions ? [...el.selectedOptions].map(o => o.label + '\\u0000' + o.value)"
                    ".join('\\u0001') : (el.value || el.innerText || '')"
                )
                result.value = str(picked)
            else:
                result.value = await locator.evaluate(
                    "el => ('value' in el && el.tagName !== 'BUTTON') ? String(el.value) : (el.innerText || '')"
                )
        except Exception:
            pass

    # ── takeover (only while the task waits for the person) ──────────────────

    def takeover(self, kind: str, *, x: float = 0.0, y: float = 0.0, text: str = "", key: str = "",
                 direction: str = "down") -> dict[str, Any]:
        return self._run(self._takeover_async(kind, x, y, text, key, direction), 30)

    async def _takeover_async(self, kind: str, x: float, y: float, text: str, key: str, direction: str):
        assert self.session is not None
        self._follow_newest_page()
        page = self.session.page
        started = time.monotonic()
        if kind == "click":
            px = max(0.0, min(1.0, float(x))) * VIEWPORT["width"]
            py = max(0.0, min(1.0, float(y))) * VIEWPORT["height"]
            await page.mouse.click(px, py)
        elif kind == "type":
            await page.keyboard.type(str(text)[:500], delay=15)
        elif kind == "key":
            if key not in _TAKEOVER_KEYS:
                raise ValueError("That key is not available here")
            await page.keyboard.press(key)
        elif kind == "scroll":
            await page.mouse.move(VIEWPORT["width"] / 2, VIEWPORT["height"] / 2)
            await page.mouse.wheel(0, (-1 if direction == "up" else 1) * VIEWPORT["height"] * 0.8)
        elif kind == "back":
            await page.go_back(wait_until="domcontentloaded", timeout=15_000)
        else:
            raise ValueError("Unknown takeover action")
        await self._settle(started)
        refusal = await self._leave_refused()
        self._follow_newest_page()
        self._frame = None
        return {"url": self.session.page.url, "refused": refusal}

    # ── the approved-step check ──────────────────────────────────────────────

    def approved_mismatch(self, page_url: str, ref: str, label: str) -> str | None:
        """Why an approved step may not run here: the page or its target changed."""
        if self.url != page_url:
            return "The page changed after this was approved; nothing was done."
        if label:
            from risk_policy import element_label

            details = self.element_details(ref)
            if element_label(details) != label:
                return f'"{label}" is no longer on the page as approved; nothing was done.'
        return None


def _short_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    name = type(exc).__name__
    if "Timeout" in name or "Timeout" in text[:80]:
        head = text.split("Call log:", 1)[0].strip()
        return f"Timed out after {ACTION_TIMEOUT_MS // 1000} s: {head[:160]}"
    return f"{name}: {text[:220]}"


def artifacts_dir(profile_id: str, task_id: str) -> Path:
    """``computer-use/<profile>/task_<id>``: served by /media to that profile only."""
    return computer_use_skill._COMPUTER_ARTIFACTS_DIR / profile_id / f"task_{task_id}"


def validate_start_url(url: str) -> str:
    return _validate_url(url)
