#!/usr/bin/env python3
"""Screenshot harness for the Narad PWA: every phone screen, on common Android
sizes, in light and dark.

It serves the production build (phase-4/frontend/dist, so run `npm run build`
first) through Playwright route interception, with a mocked API built from
fixtures/*.json. There is no server and no real data: fixtures use placeholder
people ("Asha Sharma") and example.com addresses. Each shot also gets a small
audit (anything wider than the screen, tap targets under 44 px, text under
12 px, requests that left the page's origin) in report.json, and index.html
puts every image on one page.

    cd phase-4/frontend && npm run build
    /path/to/.venv/bin/python scripts/screenshots/shoot.py            # everything
    /path/to/.venv/bin/python scripts/screenshots/shoot.py --only chat-cards,activity \\
        --sizes 360x780 --themes light --out /tmp/shots

Chromium comes from NARAD_CHROMIUM_EXECUTABLE, else the Playwright build this
repository's sandbox ships (see CHROMIUM below), else Playwright's own.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page, Route, sync_playwright

HERE = Path(__file__).resolve().parent
FRONTEND = HERE.parents[1]
DIST = FRONTEND / "dist"
FIXTURES = HERE / "fixtures"
ORIGIN = "http://localhost:4173"
CHROMIUM = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
DEFAULT_SIZES = ["360x780", "412x915"]
DEFAULT_THEMES = ["light", "dark"]
STATIC_PREFIXES = ("/assets/", "/icons/", "/fonts/")
STATIC_FILES = {"/", "/index.html", "/manifest.webmanifest", "/sw.js", "/favicon.ico"}
ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36"
)

# ── Fixtures ─────────────────────────────────────────────────────────────────

_REL = re.compile(r"^@now([+-])(\d+)([smhd])$")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def _resolve_times(value: Any, now: datetime) -> Any:
    """"@now-5m" style strings become ISO timestamps, so times read "5 min ago"."""
    if isinstance(value, dict):
        return {key: _resolve_times(item, now) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_times(item, now) for item in value]
    if isinstance(value, str):
        match = _REL.match(value)
        if match:
            sign, amount, unit = match.groups()
            delta = timedelta(**{_UNITS[unit]: int(amount)})
            return (now + delta if sign == "+" else now - delta).isoformat()
    return value


def load_fixtures() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    data: dict[str, Any] = {}
    for path in sorted(FIXTURES.glob("*.json")):
        data[path.stem] = _resolve_times(json.loads(path.read_text(encoding="utf-8")), now)
    return data


def crop_svg(label: str, value: str, unit: str, blurred: bool = False) -> str:
    """A crop of a printed lab report line, as the review screen shows it."""
    blur = '<filter id="b"><feGaussianBlur stdDeviation="1.3"/></filter>' if blurred else ""
    style = ' filter="url(#b)"' if blurred else ""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="84" viewBox="0 0 640 84">'
        f"<defs>{blur}</defs>"
        '<rect width="640" height="84" fill="#fbfaf6"/>'
        f'<g{style} font-family="Courier New, monospace" font-size="24" fill="#1d1d1d">'
        f'<text x="18" y="52">{label}</text>'
        f'<text x="330" y="52" font-weight="bold">{value}</text>'
        f'<text x="430" y="52">{unit}</text></g></svg>'
    )


CROPS = {
    "haemoglobin.svg": crop_svg("HAEMOGLOBIN", "11.2", "g/dL"),
    "tsh.svg": crop_svg("TSH (ULTRASENSITIVE)", "4.8", "µIU/mL", blurred=True),
    "sugar.svg": crop_svg("GLUCOSE FASTING", "96", "mg/dL"),
}

# ── Scenes ───────────────────────────────────────────────────────────────────


@dataclass
class Scene:
    name: str
    description: str
    run: Callable[[Page, "MockApi"], None]
    profile: str = "owner"          # owner | member | none
    thread: bool = False            # restore the history thread
    consent: str | None = None      # None (not needed) | "en" | "hi"
    host_down: bool = False
    push_on: bool = False           # this phone already has notifications on
    path: str = "/"


@dataclass
class MockApi:
    fixtures: dict[str, Any]
    scene: Scene
    frames: dict[str, bytes]
    stream: str | None = None
    host_down: bool = False
    unmatched: list[str] = field(default_factory=list)
    external: list[str] = field(default_factory=list)
    approvals: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending: list[Route] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.host_down = self.scene.host_down
        self.approvals = json.loads(json.dumps(self.fixtures["approvals"]))

    # Responses ---------------------------------------------------------------

    @property
    def profile(self) -> dict[str, Any]:
        profiles = self.fixtures["profiles"]
        return profiles["member"] if self.scene.profile == "member" else profiles["owner"]

    def task(self, task_id: str, events: bool = True) -> dict[str, Any] | None:
        task = self.fixtures["tasks"].get(task_id)
        if not task:
            return None
        payload = dict(task)
        if not events:
            payload.pop("events", None)
        elif payload["status"] == "waiting_approval" and payload.get("proposal_id"):
            payload["approval"] = self.approvals.get(payload["proposal_id"])
        return payload

    def sse(self, key: str) -> str:
        stream = self.fixtures["streams"][key]
        lines = []
        for event in stream["events"]:
            data = event["data"]
            if isinstance(data, str) and data.startswith("@task:"):
                data = self.task(data[6:], events=False)
            elif isinstance(data, str) and data.startswith("@approval:"):
                data = self.approvals[data[10:]]
            lines.append("data: " + json.dumps({"type": event["type"], "data": data}, ensure_ascii=False) + "\n\n")
        return "".join(lines)

    def api(self, method: str, path: str, query: dict[str, list[str]], body: str | None) -> tuple[int, Any, str] | None:
        """(status, body, content type) for an API call; None leaves the request pending."""
        fx = self.fixtures
        settings = fx["settings"]
        if path == "/health":
            return (530, {"detail": "down"}, "json") if self.host_down else (200, {"ok": True}, "json")
        if self.host_down:
            return 530, {"detail": "Cloudflare: origin unreachable"}, "json"

        if path == "/profiles/session":
            return 200, {"profile": self.profile}, "json"
        if path == "/profiles/media-session":
            return 204, None, "json"
        if path == "/profiles" and method == "GET":
            return 200, {"profiles": fx["profiles"]["family"]}, "json"
        if path == "/consent" and method == "GET":
            lang = query.get("lang", ["en"])[0]
            lang = lang if lang in ("en", "hi") else "en"
            needs = self.scene.consent is not None
            return 200, {
                "profile": self.profile["user_id"],
                "current_version": fx["consent"]["current_version"],
                "accepted": not needs,
                "owner": self.profile["is_owner"],
                "needs_consent": needs,
                "enforced": True,
                "sheet": {"part": "a", "lang": lang, "languages": ["en", "hi"], "markdown": fx["consent"]["sheet"][lang]},
            }, "json"
        if path == "/capabilities":
            return 200, settings["capabilities"], "json"
        if path == "/onboarding":
            return 200, {**settings["onboarding"], "user_id": self.profile["user_id"]}, "json"

        # Chat
        if path == "/threads/latest":
            thread = fx["thread"]
            if self.scene.thread:
                return 200, {"has_thread": True, "thread": {"session_id": thread["session_id"]}}, "json"
            return 200, {"has_thread": False, "thread": None}, "json"
        if path.startswith("/thread/"):
            thread = fx["thread"]
            if self.scene.thread and path == f"/thread/{thread['session_id']}" and method == "GET":
                return 200, {"session_id": thread["session_id"], "turns": thread["turns"], "working_state": None}, "json"
            return 404, {"detail": "Not Found"}, "json"
        if path == "/chat" and method == "POST":
            key = self.stream or "answer"
            return 200, self.sse(key), "sse"
        if path.startswith("/chat/attach/"):
            # A stream that ended without "done": the app re-attaches. Leaving
            # this pending keeps the turn streaming for the screenshot.
            return None
        if path == "/feedback":
            return 200, {"ok": True}, "json"

        # Approvals
        match = re.fullmatch(r"/approvals/(apr_[0-9a-f]{16})(?:/(approve|reject|edit))?", path)
        if match:
            proposal = self.approvals.get(match.group(1))
            if not proposal:
                return 404, {"detail": "Not Found"}, "json"
            if match.group(2) == "approve":
                proposal.update(status="executed", decided_at=datetime.now(timezone.utc).isoformat(),
                                result={"status": "ok", "summary": "Sent to office@school.example.com"})
            elif match.group(2) == "reject":
                proposal.update(status="rejected")
            return 200, proposal, "json"

        # Kriya tasks
        if path == "/tasks" and method == "GET":
            wanted = [s for s in ",".join(query.get("status", [])).split(",") if s]
            tasks = [self.task(tid, events=False) for tid in fx["tasks"]]
            tasks = [t for t in tasks if t and (not wanted or t["status"] in wanted)]
            return 200, {"tasks": tasks}, "json"
        match = re.fullmatch(r"/tasks/(tsk_[0-9a-f]{16})(?:/(frame|cancel|resume|takeover))?", path)
        if match:
            task = self.task(match.group(1))
            if not task:
                return 404, {"detail": "Task not found"}, "json"
            if match.group(2) == "frame":
                frame = self.frames["login" if task["status"] == "waiting_help" else "search"]
                return 200, frame, "image/png"
            if match.group(2) == "takeover":
                return 200, {"ok": True}, "json"
            return 200, task, "json"

        # Documents
        match = re.fullmatch(r"/documents/reviews/(rev_[0-9a-f]{16})", path)
        if match:
            review = fx["review"].get(match.group(1))
            return (200, review, "json") if review else (404, {"detail": "Not Found"}, "json")
        if path.startswith("/crops/"):
            svg = CROPS.get(path.rsplit("/", 1)[-1])
            return (200, svg, "image/svg+xml") if svg else (404, {"detail": "Not Found"}, "json")
        if path.startswith("/media/"):
            return 200, self.frames["search"], "image/png"

        # Trust
        if path == "/privacy/egress":
            return 200, fx["egress"], "json"

        # Activity and notifications
        if path == "/inbox":
            return 200, fx["inbox"], "json"
        if path == "/inbox/mark-read":
            return 200, {"ok": True}, "json"
        if path == "/notifications/preferences":
            return 200, settings["notification_preferences"], "json"
        if path == "/care-circle":
            return 200, settings["care_circle"], "json"
        if path == "/care-circle/shared-with-me":
            return 200, {"shared": settings["shared_with_me"]}, "json"
        if path == "/push/devices":
            return 200, {"devices": settings["push_devices"] if self.scene.push_on else []}, "json"
        if path == "/push/vapid-public-key":
            return 200, settings["vapid"], "json"
        if path == "/push/subscribe":
            return 200, {"ok": True}, "json"

        # Paths
        workflows = fx["workflows"]
        if path == "/workflows":
            return 200, {"workflows": workflows["workflows"]}, "json"
        if path == "/workflow-runs":
            return 200, {"runs": workflows["runs"]}, "json"
        match = re.fullmatch(r"/workflow-runs/(wfr_[0-9a-f]+)", path)
        if match:
            run = next((item for item in workflows["runs"] if item["run_id"] == match.group(1)), None)
            return (200, run, "json") if run else (404, {"detail": "Not Found"}, "json")

        # Voice
        if path == "/voice/status":
            return 200, settings["voice_status"], "json"
        if path == "/voice/preferences":
            return 200, settings["voice_preferences"], "json"

        # Quiet screens behind System
        if path in ("/memory", "/sankalpa"):
            return 200, {"memories": [], "records": [], "sankalpas": []}, "json"
        return 404, {"detail": "Not Found"}, "json"

    # Routing -----------------------------------------------------------------

    def handle(self, route: Route) -> None:
        request = route.request
        url = urlparse(request.url)
        if f"{url.scheme}://{url.netloc}" != ORIGIN:
            self.external.append(request.url)
            if url.hostname in ("fonts.googleapis.com", "fonts.gstatic.com"):
                route.continue_()   # older builds loaded Google Fonts; show them as they were
            else:
                route.abort()
            return
        path = url.path
        if path in STATIC_FILES or path.startswith(STATIC_PREFIXES) or (request.resource_type == "document"):
            self.static(route, path)
            return
        result = self.api(request.method, path, parse_qs(url.query), request.post_data)
        if result is None:
            self.pending.append(route)  # pending on purpose
            return
        status, body, kind = result
        if status == 404 and kind == "json":
            self.unmatched.append(f"{request.method} {path}")
        if kind == "json":
            route.fulfill(status=status, content_type="application/json",
                          body="" if body is None else json.dumps(body, ensure_ascii=False))
        elif kind == "sse":
            route.fulfill(status=status, content_type="text/event-stream", body=body)
        elif isinstance(body, bytes):
            route.fulfill(status=status, content_type=kind, body=body, headers={"Cache-Control": "no-store"})
        else:
            route.fulfill(status=status, content_type=kind, body=body)

    def static(self, route: Route, path: str) -> None:
        if self.host_down and route.request.resource_type == "document":
            route.fulfill(status=530, content_type="text/html", body="<h1>Origin unreachable</h1>")
            return
        target = DIST / path.lstrip("/")
        if path in ("/", "") or not target.is_file():
            target = DIST / "index.html"
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".webmanifest":
            kind = "application/manifest+json"
        route.fulfill(status=200, content_type=kind, body=target.read_bytes())


# ── Page setup ───────────────────────────────────────────────────────────────

INIT_SCRIPT = r"""
(() => {
  const seed = %(seed)s;
  try {
    if (!sessionStorage.getItem('__narad_seeded')) {
      localStorage.clear();
      for (const [key, value] of Object.entries(seed)) localStorage.setItem(key, value);
      sessionStorage.setItem('__narad_seeded', '1');
    }
  } catch (error) { /* storage is optional */ }
  // No real service worker here: a stand-in registration lets the phone
  // notification card reach its real "off" or "on" state at once.
  const subscription = %(subscription)s;
  const registration = {
    pushManager: {
      getSubscription: async () => subscription,
      subscribe: async () => subscription,
    },
    addEventListener() {}, removeEventListener() {},
    update: async () => undefined,
    waiting: null, installing: null, active: {},
  };
  const container = {
    controller: null,
    ready: Promise.resolve(registration),
    register: async () => registration,
    getRegistration: async () => registration,
    addEventListener() {}, removeEventListener() {},
  };
  Object.defineProperty(Navigator.prototype, 'serviceWorker', { configurable: true, get: () => container });
})();
"""

AUDIT_SCRIPT = r"""
() => {
  const vw = window.innerWidth;
  const describe = el => {
    const label = el.getAttribute('aria-label') || el.getAttribute('title') || (el.innerText || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 40);
    return `${el.tagName.toLowerCase()}${label ? ` "${label}"` : ''}`;
  };
  const visible = el => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || Number(s.opacity) === 0) return false;
    return r.bottom > 0 && r.top < window.innerHeight;
  };
  // Wider than the screen is fine inside a sideways scroller (a table, a
  // zoomed page) and for decoration; anything else is cut off or overflows.
  const intended = el => {
    if (el.closest('svg, [aria-hidden="true"], [data-sonner-toaster]')) return true;
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const s = getComputedStyle(p);
      if ((s.overflowX === 'auto' || s.overflowX === 'scroll') && p.getBoundingClientRect().right <= vw + 1) return true;
    }
    return false;
  };
  const overflow = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if ((r.right > vw + 1 || r.left < -1) && !intended(el)) overflow.push(`${describe(el)} ${Math.round(r.left)}..${Math.round(r.right)}`);
  }
  const small = [];
  const targets = document.querySelectorAll('button, a[href], [role=button], [role=tab], [role=switch], [role=checkbox], input:not([type=hidden]), select, textarea');
  for (const el of targets) {
    if (!visible(el) || el.disabled) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 44 || r.height < 44) small.push(`${describe(el)} ${Math.round(r.width)}x${Math.round(r.height)}`);
  }
  const sizes = {};
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!node.textContent.trim() || !node.parentElement || !visible(node.parentElement)) continue;
    const size = Math.round(parseFloat(getComputedStyle(node.parentElement).fontSize) * 2) / 2;
    sizes[size] = (sizes[size] || 0) + node.textContent.trim().length;
  }
  const chars = Object.values(sizes).reduce((a, b) => a + b, 0) || 1;
  const under12 = Object.entries(sizes).filter(([s]) => Number(s) < 12).reduce((a, [, n]) => a + n, 0);
  return {
    pageScrollsSideways: document.documentElement.scrollWidth > vw + 1,
    overflow: overflow.slice(0, 20),
    smallTargets: small.slice(0, 40),
    smallTargetCount: small.length,
    textUnder12pxShare: Math.round(under12 / chars * 100),
    fontSizes: sizes,
  };
}
"""


def settle(page: Page, ms: int = 600) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=4000)
    except Exception:  # noqa: BLE001 - a pending re-attach keeps the network busy on purpose
        pass
    page.wait_for_timeout(ms)


def click_nav(page: Page, name: str) -> None:
    """Open a surface from the app's navigation, whatever its current labels."""
    candidates = {
        "chat": ["Chat", "Open Chat"],
        "activity": ["Activity", "Open Activity"],
        "paths": ["Paths", "Open Workflows", "Workflows"],
        "you": ["You", "Open System"],
    }[name]
    for label in candidates:
        locator = page.get_by_role("button", name=re.compile(rf"^{re.escape(label)}\b"))
        if locator.count():
            locator.first.click()
            settle(page)
            if name == "you" and label == "Open System":
                tab = page.get_by_role("tab", name="Profile")
                if tab.count():
                    tab.first.click()
                    settle(page)
            return
    raise RuntimeError(f"No navigation button for {name}")


def send(page: Page, api: MockApi, stream: str, wait_done: bool = True) -> None:
    api.stream = stream
    query = api.fixtures["streams"][stream]["query"]
    box = page.get_by_placeholder(re.compile("Ask Narad"))
    box.click()
    box.fill(query)
    box.press("Enter")
    if wait_done:
        page.wait_for_timeout(1500)
        settle(page, 800)
    else:
        page.wait_for_timeout(1400)


def scroll_chat_to(page: Page, selector: str) -> None:
    locator = page.locator(selector).first
    if locator.count():
        locator.scroll_into_view_if_needed()
        page.evaluate("el => el.scrollIntoView({ block: 'start' })", locator.element_handle())
        page.wait_for_timeout(400)


def open_egress(page: Page) -> None:
    link = page.get_by_role("button", name=re.compile("What left my Mac"))
    if link.count():
        link.first.click()
    else:
        page.get_by_role("button", name=re.compile("DeepSeek saw this")).first.click()
        page.wait_for_timeout(300)
        page.get_by_role("button", name=re.compile("See everything that left")).first.click()
    settle(page)


def _noop(page: Page, api: MockApi) -> None:
    settle(page, 900)


def _voice(page: Page, api: MockApi, settings: bool) -> None:
    page.get_by_role("button", name=re.compile("[Vv]oice")).first.click()
    settle(page, 1200)
    if settings:
        page.get_by_role("button", name="Voice settings").first.click()
        page.wait_for_timeout(400)


def _offline_banner(page: Page, api: MockApi) -> None:
    settle(page)
    api.host_down = True
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(900)


SCENES: list[Scene] = [
    Scene("gate", "Profile picker before sign-in", _noop, profile="none"),
    Scene("consent-en", "Consent sheet, English", _noop, profile="member", consent="en"),
    Scene("consent-hi", "Consent sheet, Hindi", _noop, profile="member", consent="hi"),
    Scene("chat-empty", "Chat with no history", _noop),
    Scene("chat-history", "Chat restored from the Mac: Hindi and English answers with receipts", _noop, thread=True),
    Scene("chat-streaming", "An answer streaming in", lambda p, a: send(p, a, "partial", wait_done=False), thread=True),
    Scene("chat-answer", "A finished answer with its receipt and feedback", lambda p, a: send(p, a, "answer"), thread=True),
    Scene("chat-cards", "Task, approval and document-review cards in the chat", lambda p, a: send(p, a, "errand")),
    Scene("chat-card-task", "The task card", lambda p, a: (send(p, a, "errand"), scroll_chat_to(p, '[aria-label^="Task:"]'))),
    Scene("chat-card-approval", "The approval card", lambda p, a: (send(p, a, "errand"), scroll_chat_to(p, '[aria-label^="Approval:"]'))),
    Scene("receipt-sheet", "Privacy receipt sheet", lambda p, a: (settle(p), p.get_by_role("button", name=re.compile("DeepSeek saw this")).first.click(), p.wait_for_timeout(500)), thread=True),
    Scene("egress", "What left my Mac", lambda p, a: (settle(p), open_egress(p)), thread=True),
    Scene("approval-sheet", "Approval opened from a notification", _noop, path="/?approval=apr_1a2b3c4d5e6f7a8b"),
    Scene("task-screen", "Task screen with the live view", _noop, path="/?task=tsk_0123456789abcdef"),
    Scene("task-help", "Task waiting for help: takeover", _noop, path="/?task=tsk_fedcba9876543210"),
    Scene("task-approval", "Task waiting for an OK", _noop, path="/?task=tsk_00aa11bb22cc33dd"),
    Scene("review", "Document review with crops", _noop, path="/?review=rev_0f1e2d3c4b5a6978"),
    Scene("activity", "Activity inbox", lambda p, a: click_nav(p, "activity")),
    Scene("activity-deeplink", "Activity opened from a notification", _noop, path="/?activity=evt_med_1"),
    Scene("paths", "Paths", lambda p, a: click_nav(p, "paths")),
    Scene("path-deeplink", "One path opened from a link", _noop, path="/?path=wfr_placeholder"),
    Scene("you", "You: profile, notifications, sign out", lambda p, a: click_nav(p, "you"), push_on=True),
    Scene("you-member", "You, for a family member", lambda p, a: click_nav(p, "you"), profile="member"),
    Scene("voice", "Voice mode", lambda p, a: _voice(p, a, settings=False)),
    Scene("voice-settings", "Voice settings", lambda p, a: _voice(p, a, settings=True)),
    Scene("offline", "The Mac is asleep", _noop, host_down=True),
    Scene("offline-banner", "The Mac went away while the app was open", _offline_banner, thread=True),
]


def seed_for(scene: Scene, fixtures: dict[str, Any]) -> dict[str, str]:
    seed: dict[str, str] = {}
    if scene.profile != "none":
        profile = fixtures["profiles"]["member" if scene.profile == "member" else "owner"]
        session = {"profile": profile, "token": "fixture-session-token", "expires_at": int(time.time()) + 86400 * 20}
        seed["narad_profile_session"] = json.dumps(session)
        user = profile["user_id"]
        seed[f"avatara_convo_session_id:{user}"] = fixtures["thread"]["session_id"] if scene.thread else "sess_fresh_thread"
        if scene.consent:
            seed[f"narad_consent_lang:{user}"] = scene.consent
        if scene.push_on:
            seed[f"narad_push_enabled:{user}"] = "1"
    return seed


def render_frames(browser) -> dict[str, bytes]:
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    frames = {}
    for name in ("search", "login"):
        page.set_content((FIXTURES / f"frame-{name}.html").read_text(encoding="utf-8"))
        frames[name] = page.screenshot(type="png")
    page.close()
    return frames


def contact_sheet(out: Path, shots: list[dict[str, Any]]) -> None:
    rows = []
    for scene in SCENES:
        mine = [shot for shot in shots if shot["scene"] == scene.name]
        if not mine:
            continue
        cells = "".join(
            f'<figure><img loading="lazy" src="{shot["file"]}" alt=""><figcaption>{shot["size"]} {shot["theme"]}'
            f'{" · overflow" if shot["audit"]["overflow"] else ""}</figcaption></figure>'
            for shot in mine
        )
        rows.append(f"<section><h2>{scene.name}</h2><p>{scene.description}</p><div>{cells}</div></section>")
    html = (
        "<!doctype html><meta charset=utf-8><title>Narad screens</title>"
        "<style>body{font:14px system-ui;margin:16px;background:#eee}section{margin-bottom:28px}"
        "div{display:flex;gap:12px;overflow-x:auto}figure{margin:0}img{width:260px;border:1px solid #bbb;background:#fff}"
        "figcaption{font-size:12px;color:#555}</style>" + "".join(rows)
    )
    (out / "index.html").write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(FRONTEND / "screenshots"), help="folder for the PNGs (not committed)")
    parser.add_argument("--only", default="", help="comma-separated scene names")
    parser.add_argument("--sizes", default=",".join(DEFAULT_SIZES))
    parser.add_argument("--themes", default=",".join(DEFAULT_THEMES))
    parser.add_argument("--scale", type=float, default=2.0, help="device pixel ratio")
    parser.add_argument("--list", action="store_true", help="list the scenes and exit")
    args = parser.parse_args()

    if args.list:
        for scene in SCENES:
            print(f"{scene.name:20} {scene.description}")
        return 0
    if not (DIST / "index.html").is_file():
        print("No build found: run `npm run build` in phase-4/frontend first.", file=sys.stderr)
        return 2

    wanted = {name.strip() for name in args.only.split(",") if name.strip()}
    scenes = [scene for scene in SCENES if not wanted or scene.name in wanted]
    unknown = wanted - {scene.name for scene in SCENES}
    if unknown:
        print(f"Unknown scenes: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    fixtures = load_fixtures()
    run_id = fixtures["workflows"]["runs"][0]["run_id"]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    executable = os.environ.get("NARAD_CHROMIUM_EXECUTABLE") or (CHROMIUM if Path(CHROMIUM).exists() else None)
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")

    shots: list[dict[str, Any]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=executable,
            proxy={"server": proxy, "bypass": "localhost"} if proxy else None,
        )
        frames = render_frames(browser)
        for size in [item.strip() for item in args.sizes.split(",") if item.strip()]:
            width, height = (int(part) for part in size.split("x"))
            for theme in [item.strip() for item in args.themes.split(",") if item.strip()]:
                for scene in scenes:
                    api = MockApi(fixtures=fixtures, scene=scene, frames=frames)
                    context = browser.new_context(
                        viewport={"width": width, "height": height},
                        device_scale_factor=args.scale,
                        is_mobile=True,
                        has_touch=True,
                        color_scheme=theme,  # type: ignore[arg-type]
                        locale="en-IN",
                        timezone_id="Asia/Kolkata",
                        user_agent=ANDROID_UA,
                        ignore_https_errors=True,
                    )
                    if scene.push_on:
                        context.grant_permissions(["notifications"], origin=ORIGIN)
                    subscription = (
                        json.dumps({"endpoint": fixtures["settings"]["push_devices"][0]["endpoint"],
                                    "options": {"applicationServerKey": None}})
                        if scene.push_on else "null"
                    )
                    context.add_init_script(INIT_SCRIPT % {
                        "seed": json.dumps(seed_for(scene, fixtures)),
                        "subscription": subscription,
                    })
                    context.route("**/*", api.handle)
                    page = context.new_page()
                    errors: list[str] = []
                    page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                    path = scene.path.replace("wfr_placeholder", run_id)
                    page.goto(ORIGIN + path)
                    settle(page)
                    try:
                        scene.run(page, api)
                    except Exception as error:  # noqa: BLE001 - keep going, note it
                        errors.append(f"scene step failed: {error}")
                    file = f"{scene.name}--{size}--{theme}.png"
                    page.screenshot(path=str(out / file))
                    audit = page.evaluate(AUDIT_SCRIPT)
                    shots.append({
                        "scene": scene.name, "size": size, "theme": theme, "file": file,
                        "audit": audit, "errors": errors,
                        "unmatched_api": sorted(set(api.unmatched)),
                        "external_requests": sorted(set(api.external)),
                    })
                    flag = []
                    if audit["overflow"]:
                        flag.append(f"overflow {len(audit['overflow'])}")
                    if audit["smallTargetCount"]:
                        flag.append(f"small targets {audit['smallTargetCount']}")
                    if errors:
                        flag.append(f"errors {len(errors)}")
                    print(f"{file:48} {' · '.join(flag) or 'ok'}")
                    for route in api.pending:
                        try:
                            route.abort()
                        except Exception:  # noqa: BLE001 - the page may be gone already
                            pass
                    context.close()
        browser.close()

    (out / "report.json").write_text(json.dumps(shots, indent=1, ensure_ascii=False), encoding="utf-8")
    contact_sheet(out, shots)
    external = sorted({url for shot in shots for url in shot["external_requests"]})
    print(f"\n{len(shots)} screenshots in {out} (index.html, report.json)")
    print(f"Requests that left the app's origin: {len(external)}")
    for url in external[:10]:
        print(f"  {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
