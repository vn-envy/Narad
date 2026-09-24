"""The desktop surface: one window of the Narad host Mac, through cua-driver.

Desktop tasks are owner-only and every input step waits for an approval
(risk_policy: all desktop input). The loop is the browser's: the operator
sees an accessibility view of one window, picks a step, the step is
approved on the phone, then runs once. What differs is underneath:

  observe  ``get_window_state`` on the host's persistent ``cua-driver mcp``
           session: the window's accessibility elements (role, label, value,
           frame, ``element_token``), no screenshot. Refs are derived from
           each element's role, label and place in the tree, so the same
           control keeps its ref across snapshots and a stale ref can never
           point at a different control.
  act      by ``element_token`` with an explicit ``delivery_mode``
           ("background": no focus stealing); ``open_app`` launches an app,
           ``switch_window`` only changes which window Narad reads.
  verify   the driver's Effect contract (confirmed / partial / unverifiable /
           suspected_noop / refused) plus ``verify_state`` for a step's
           expectation; ``unverifiable`` counts only when the window visibly
           changed, and ``refused`` is reported, never retried.
  frame    a window screenshot kept in memory (PNG), for the live view.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

from kriya.browser import ActResult
from kriya.perception import FIELD_ROLES, MAX_OBSERVATION_CHARS, Node, Observation, PageState, mask_secrets

FRAME_MIN_INTERVAL_S = 0.6
MAX_ELEMENTS = 250
SNAPSHOT_TIMEOUT_MS = 5_000
VERIFY_TIMEOUT_MS = 3_000
DELIVERY_MODE = "background"

# macOS accessibility roles in the words the rest of Kriya uses.
_ROLES = {
    "axbutton": "button", "axmenubutton": "button", "axpopupbutton": "combobox", "axcombobox": "combobox",
    "axtextfield": "textbox", "axtextarea": "textbox", "axsecuretextfield": "textbox", "axsearchfield": "searchbox",
    "axcheckbox": "checkbox", "axradiobutton": "radio", "axlink": "link", "axmenuitem": "menuitem",
    "axtab": "tab", "axslider": "slider", "axincrementor": "spinbutton", "axstatictext": "text",
    "axheading": "heading", "axwindow": "window", "axsheet": "dialog", "axdialog": "dialog",
    "axlist": "list", "axrow": "row", "axcell": "cell", "axdisclosuretriangle": "button",
    "axswitch": "switch", "aximage": "img", "axscrollarea": "scrollarea", "axgroup": "group",
    "axtoolbar": "toolbar", "axoutline": "tree", "axtable": "table",
}
_ACTIONABLE = frozenset({
    "button", "combobox", "textbox", "searchbox", "checkbox", "radio", "link", "menuitem", "tab", "slider",
    "spinbutton", "switch", "row", "cell", "scrollarea",
})


def _role(native: str) -> str:
    key = str(native or "").strip().lower().replace(" ", "")
    return _ROLES.get(key, key.removeprefix("ax") or "generic")


class DesktopSurface:
    """One task's window on the host desktop. Sync methods; the session has its own loop."""

    kind = "desktop"
    environment = "desktop"
    dharma_action = "desktop_control"

    def __init__(self, *, task_id: str, profile_id: str, goal: str, driver: Any = None) -> None:
        self.task_id = task_id
        self.profile_id = profile_id
        self.goal = goal
        self._driver = driver
        self.pid = 0
        self.window_id = 0
        self.app_name = ""
        self.title = ""
        self.is_open = False
        self._tokens: dict[str, str] = {}
        self._nodes: dict[str, Node] = {}
        self._windows: dict[str, dict[str, Any]] = {}
        self._digest = ""
        self._frame: tuple[float, bytes] | None = None
        self._lock = threading.Lock()
        self._shots = 0
        from kriya.browser import artifacts_dir

        self.run_dir = artifacts_dir(profile_id, task_id)

    # ── lifecycle ────────────────────────────────────────────────────────────

    @property
    def driver(self) -> Any:
        if self._driver is None:
            import cua_session

            self._driver = cua_session.session()
        return self._driver

    @property
    def url(self) -> str:
        return f"desktop://window/{self.pid}/{self.window_id}" if self.window_id else ""

    def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        import cua_session

        return cua_session.tool_payload(self.driver.call(tool, arguments))

    def open(self, start_url: str = "") -> None:
        """The window a restart left off on, else the frontmost one on screen."""
        self.driver.start()  # the self-check: refuses a cua-driver whose tools changed
        wanted = str(start_url or "")
        windows = self._list_windows()
        if wanted.startswith("desktop://window/"):
            _, _, rest = wanted.partition("desktop://window/")
            pid, _, window_id = rest.partition("/")
            match = next((w for w in windows if str(w.get("pid")) == pid and str(w.get("window_id")) == window_id),
                         None)
            if match is not None:
                self._select(match)
                self.is_open = True
                return
        if not windows:
            raise ValueError("No window is open on the Mac's screen")
        self._select(windows[0])
        self.is_open = True

    def close(self) -> None:
        self.is_open = False
        with self._lock:
            self._frame = None

    def _list_windows(self) -> list[dict[str, Any]]:
        rows = self._call("list_windows", {"on_screen_only": True}).get("windows") or []
        rows = [row for row in rows if isinstance(row, dict) and row.get("window_id") and row.get("pid")]
        return sorted(rows, key=lambda row: row.get("z_index") if isinstance(row.get("z_index"), int) else -1,
                      reverse=True)

    def _select(self, window: dict[str, Any]) -> None:
        self.pid = int(window["pid"])
        self.window_id = int(window["window_id"])
        self.app_name = str(window.get("app_name") or "")
        self.title = str(window.get("title") or "")
        with self._lock:
            self._frame = None

    # ── perceiving ───────────────────────────────────────────────────────────

    def observe(self, *, screenshot: bool = False) -> Observation:
        state = self._call("get_window_state", {
            "pid": self.pid, "window_id": self.window_id, "include_screenshot": False,
            "max_elements": MAX_ELEMENTS, "timeout_ms": SNAPSHOT_TIMEOUT_MS,
        })
        self.app_name = str(state.get("app_name") or self.app_name)
        self.title = str(state.get("window_title") or self.title)
        nodes = self._nodes_from(state.get("elements") or [])
        windows = self._list_windows()
        self._windows = {f"w{index + 1}": row for index, row in enumerate(windows[:8])}
        window_nodes = [
            Node(role="window", name=f"{row.get('app_name') or ''} — {row.get('title') or ''}".strip(" —"), ref=ref)
            for ref, row in self._windows.items()
        ]
        mask_secrets(nodes)
        for node in nodes:
            if node.role in FIELD_ROLES and node.attrs.get("secure") and node.text:
                node.text = "••••"
        refs = {node.ref: node for node in nodes + window_nodes if node.ref}
        lines = [f"Window: {self.app_name} — {self.title}".strip(" —"),
                 "Other windows (switch_window to read one): "
                 + (", ".join(f"[{node.ref}] {node.name}" for node in window_nodes) or "none")]
        body = [line for line in (_render(node) for node in nodes) if line]
        text = "\n".join(lines + body)
        hidden = 0
        while len(text) > MAX_OBSERVATION_CHARS and body:
            body.pop()
            hidden += 1
            text = "\n".join(lines + body + [f"({hidden} more items not shown)"])
        from computer_use_skill import _injection_signals

        full = "\n".join(filter(None, (f"{node.name} {node.text}".strip() for node in nodes)))
        self._digest = hashlib.sha1(full.encode("utf-8")).hexdigest()
        return Observation(
            url=self.url, title=f"{self.app_name} — {self.title}".strip(" —"), text=text, refs=refs,
            nodes=nodes + window_nodes, state=PageState(), injection=_injection_signals(full),
            hidden_by_budget=hidden, poor=not body,
        )

    def _nodes_from(self, elements: list[dict[str, Any]]) -> list[Node]:
        """Nodes with refs derived from role, label and tree path (stable across snapshots)."""
        nodes: list[Node] = []
        by_index: dict[int, Node] = {}
        seen: dict[str, int] = {}
        tokens: dict[str, str] = {}
        for element in elements:
            if not isinstance(element, dict):
                continue
            role = _role(str(element.get("role") or ""))
            label = " ".join(str(element.get("label") or element.get("title") or "").split())
            value = " ".join(str(element.get("value") or "").split())
            frame = element.get("frame") if isinstance(element.get("frame"), dict) else {}
            box = None
            if frame:
                try:
                    box = (int(frame.get("x", 0)), int(frame.get("y", 0)), int(frame.get("w", 0)),
                           int(frame.get("h", 0)))
                except (TypeError, ValueError):
                    box = None
            text = value if role in FIELD_ROLES else (value or label if role in {"text", "heading"} else "")
            node = Node(role=role, name=label, text=text, depth=int(element.get("depth") or 0), box=box)
            if str(element.get("role") or "").lower() == "axsecuretextfield":
                node.attrs["secure"] = True
            parent = by_index.get(element.get("parent_index")) if isinstance(element.get("parent_index"), int) \
                else None
            if parent is not None:
                node.parent = parent
                parent.children.append(node)
            if role in _ACTIONABLE and element.get("element_token"):
                path = "/".join(f"{item.role}:{item.name}" for item in reversed(node.ancestors()))
                key = f"{path}/{role}:{label}"
                seen[key] = seen.get(key, 0) + 1
                ref = "d" + hashlib.sha1(f"{key}#{seen[key]}".encode("utf-8")).hexdigest()[:6]
                node.ref = ref
                tokens[ref] = str(element["element_token"])
            if isinstance(element.get("element_index"), int):
                by_index[element["element_index"]] = node
            nodes.append(node)
        self._tokens = tokens
        self._nodes = {node.ref: node for node in nodes if node.ref}
        return nodes

    def element_details(self, ref: str) -> dict[str, Any] | None:
        node = self._nodes.get(ref)
        if node is None:
            return None
        return {"tag": "", "type": "", "role": node.role, "text": node.name, "aria": "", "label": "",
                "placeholder": "", "title": "", "name": "", "in_search_form": False, "context": self.title}

    # ── acting ───────────────────────────────────────────────────────────────

    def execute(self, action: dict[str, Any]) -> ActResult:
        before_url, before_digest = self.url, self._digest
        kind = action["action"]
        ref = str(action.get("ref") or "")
        try:
            if kind == "wait":
                time.sleep(max(0.0, min(float(action.get("ms") or 1000) / 1000, 10.0)))
                return ActResult(status="ok", url_before=before_url, url_after=self.url, effect=True)
            if kind == "switch_window":
                window = self._windows.get(ref)
                if window is None:
                    return ActResult(status="error", url_before=before_url, url_after=self.url,
                                     error=f"{ref or 'that window'} is not open")
                self._select(window)
                return ActResult(status="ok", url_before=before_url, url_after=self.url, effect=True)
            tool, arguments = self._arguments(kind, action, ref)
            result = self._call(tool, arguments)
            if kind == "open_app":
                self._front_window_of(str(action.get("name") or ""))
        except KeyError as exc:
            return ActResult(status="stale", url_before=before_url, url_after=self.url,
                             error=f"{exc.args[0]} is not in the window any more")
        except ValueError as exc:
            return ActResult(status="error", url_before=before_url, url_after=self.url, error=str(exc))
        except Exception as exc:
            return ActResult(status="error", url_before=before_url, url_after=self.url,
                             error=" ".join(str(exc).split())[:240])
        return self._effect(action, result, before_url, before_digest)

    def _arguments(self, kind: str, action: dict[str, Any], ref: str) -> tuple[str, dict[str, Any]]:
        window = {"pid": self.pid, "window_id": self.window_id}
        token = {"element_token": self._tokens[ref]} if ref else {}
        if ref and ref not in self._tokens:
            raise KeyError(ref)
        if kind == "click":
            if not token:
                raise ValueError("A desktop click needs a control's ref")
            return "click", {**window, **token, "delivery_mode": DELIVERY_MODE}
        if kind in {"fill", "type"}:
            text = str(action.get("value") if action.get("value") is not None else action.get("text") or "")
            return "type_text", {**window, **token, "text": text, "delivery_mode": DELIVERY_MODE}
        if kind == "press":
            key = str(action.get("key") or "Enter")
            return "press_key", {**window, **token, "key": key.lower() if len(key) > 1 else key,
                                 "delivery_mode": DELIVERY_MODE}
        if kind == "hotkey":
            keys = [str(key) for key in action.get("keys") or [] if str(key)]
            if not keys:
                raise ValueError("hotkey needs keys, e.g. [\"cmd\", \"s\"]")
            return "hotkey", {**window, "keys": keys, "delivery_mode": DELIVERY_MODE}
        if kind == "scroll":
            direction = str(action.get("direction") or "down").lower()
            if direction not in {"up", "down", "left", "right"}:
                raise ValueError("scroll direction must be up, down, left or right")
            if not token:
                area = next((node.ref for node in self._nodes.values() if node.role == "scrollarea"), "")
                token = {"element_token": self._tokens[area]} if area else {}
            if not token:
                raise ValueError("Nothing in this window scrolls")
            return "scroll", {**window, **token, "direction": direction,
                              "amount": max(1, min(int(action.get("amount") or 3), 50)), "by": "line",
                              "delivery_mode": DELIVERY_MODE}
        if kind == "open_app":
            name = " ".join(str(action.get("name") or "").split())
            if not name:
                raise ValueError("open_app needs the app's name")
            return "launch_app", {"name": name[:80]}
        raise ValueError(f"{kind} is not a desktop action")

    def _front_window_of(self, app_name: str) -> None:
        wanted = app_name.lower()
        for _ in range(10):
            for window in self._list_windows():
                if wanted and wanted in str(window.get("app_name") or "").lower():
                    self._select(window)
                    return
            time.sleep(0.3)

    def _effect(self, action: dict[str, Any], result: dict[str, Any], before_url: str,
                before_digest: str) -> ActResult:
        """The Effect contract, and ``verify_state`` for the step's expectation."""
        effect = str(result.get("effect") or "")
        if effect == "refused":
            error = result.get("error") if isinstance(result.get("error"), dict) else {}
            words = " ".join(filter(None, (str(error.get("code") or "refused"), str(error.get("hint") or ""))))
            return ActResult(status="refused", url_before=before_url, url_after=self.url,
                             error=f"cua-driver refused it ({words})")
        notes = [f"driver: {effect}"] if effect else []
        changed = effect in {"confirmed", "partial"}
        expect = action.get("expect") if isinstance(action.get("expect"), dict) else {}
        appears = str(expect.get("text_appears") or "")
        if appears:
            verdict = self._verify_label(appears)
            notes.append(f"verify_state: {verdict}")
            changed = changed or verdict == "satisfied"
        if effect in {"unverifiable", ""} and not changed:
            # Only a visible change counts; a suspected no-op never does.
            try:
                self.observe()
                changed = self._digest != before_digest
            except Exception:
                changed = False
        value = None
        ref = str(action.get("ref") or "")
        if action["action"] in {"fill", "type"} and ref:
            try:
                self.observe()  # read the field back: refs survive a new snapshot
            except Exception:
                pass
            node = self._nodes.get(ref)
            if node is not None and not node.attrs.get("secure") and node.text != "••••":
                value = node.text
        return ActResult(status="ok", url_before=before_url, url_after=self.url, effect=changed, value=value,
                         note="; ".join(notes))

    def _verify_label(self, text: str) -> str:
        try:
            verdict = self._call("verify_state", {
                "pid": self.pid, "window_id": self.window_id, "timeout_ms": VERIFY_TIMEOUT_MS, "stable_samples": 2,
                "expect": [{"element": {"selector": {"label_contains": text[:120]}, "exists": True}}],
            })
        except Exception:
            return "unknown"
        return str(verdict.get("status") or "unknown")

    # ── pictures ─────────────────────────────────────────────────────────────

    def screenshot_file(self, prefix: str = "approval") -> str | None:
        """A PNG of the window in the task's artifact folder (an approval preview)."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._shots += 1
        path = self.run_dir / f"{prefix}-{self._shots:04d}.png"
        try:
            self._call("get_window_state", {
                "pid": self.pid, "window_id": self.window_id, "include_accessibility_tree": False,
                "screenshot_out_file": str(path),
            })
        except Exception:
            return None
        return str(path) if path.exists() else None

    def frame(self) -> bytes | None:
        """The window as a PNG, kept only in memory."""
        import cua_session

        with self._lock:
            cached = self._frame
        if cached and time.monotonic() - cached[0] < FRAME_MIN_INTERVAL_S:
            return cached[1]
        if not self.window_id:
            return None
        try:
            result = self.driver.call("get_window_state", {
                "pid": self.pid, "window_id": self.window_id, "include_accessibility_tree": False,
                "include_screenshot": True, "max_image_dimension": 1280,
            })
        except Exception:
            return cached[1] if cached else None
        image = cua_session.tool_image(result)
        if image is None:
            return None
        with self._lock:
            self._frame = (time.monotonic(), image[0])
        return image[0]

    def takeover(self, kind: str, **params: Any) -> dict[str, Any]:
        raise PermissionError("The Mac's desktop is controlled at the Mac itself; use Stop here")

    def approved_mismatch(self, page_url: str, ref: str, label: str) -> str | None:
        """Why an approved step may not run: another window, or the control changed."""
        if self.url != page_url:
            return "The window changed after this was approved; nothing was done."
        if ref and ref.startswith("d"):
            self.observe()
            node = self._nodes.get(ref)
            if node is None or (label and node.name != label):
                return f'"{label or ref}" is no longer in the window as approved; nothing was done.'
        return None


def admit(owner: str) -> dict[str, Any]:
    """Desktop tasks: the owner only, with the host desktop granted and enabled."""
    from interaction_targets import resolve_interaction_target

    from family_profiles import get_profile

    if not bool((get_profile(owner) or {}).get("is_owner")):
        raise PermissionError("Only the Narad owner can run tasks on the Mac's desktop")
    import computer_use_skill

    readiness = computer_use_skill._desktop_driver_status()
    if not readiness.get("enabled"):
        raise ValueError("Desktop control is off (NARAD_ENABLE_DESKTOP_CONTROL=1 turns it on)")
    if readiness.get("selected_provider") != "cua" or not readiness["adapters"]["cua"].get("ready"):
        reason = readiness["adapters"]["cua"].get("reason") or readiness.get("reason")
        raise ValueError(f"Desktop tasks need Cua Driver: {reason}")
    grant = resolve_interaction_target("cua", "", profile_id=owner)
    if grant is None:
        raise ValueError("Grant the Narad host desktop to your profile in onboarding first")
    return {"target_id": grant.get("target_id"), "label": "Narad host desktop"}


def _render(node: Node) -> str | None:
    """One line per control (with its ref) or piece of text."""
    if node.ref:
        parts = [f"[{node.ref}] {node.role}"]
        if node.name:
            parts.append(json.dumps(node.name[:120], ensure_ascii=False))
        if node.role in FIELD_ROLES:
            parts.append(f"= {json.dumps(node.text[:80], ensure_ascii=False)}" if node.text else "(empty)")
        return " ".join(parts)
    if node.role in {"text", "heading"} and node.text:
        return node.text[:220]
    return None
