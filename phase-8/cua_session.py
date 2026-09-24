"""One persistent cua-driver MCP session for the Narad host desktop.

cua-driver speaks MCP over stdio (``cua-driver mcp``). On macOS that process
proxies to the CuaDriver.app daemon (``cua-driver serve``), so the Screen
Recording and Accessibility grants stay with the app. Narad keeps a single
session process for the whole host instead of spawning ``cua-driver call``
per action: it runs on its own thread and event loop, is initialised once,
lists the server's tools and checks them against ``TOOLS`` below, and then
serves every desktop call over the same pipe.

A process that exits, or a call that times out (the process is then killed,
since it may be wedged), fails the calls in flight; the next call starts a
fresh process, at most ``_MAX_RESTARTS`` times a minute. A call is never
retried by itself: a click that may have landed must not land twice.
Telemetry is off in the process environment; ``cua-driver telemetry
disable`` (the launchd install and the pilot launcher run it) persists the
opt-out for the daemon too.
"""

from __future__ import annotations

import asyncio
import atexit
import collections
import json
import logging
import os
import shutil
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any

log = logging.getLogger("narad.cua")

PROTOCOL_VERSION = "2025-06-18"  # the legacy initialize flow cua-driver keeps (docs/mcp-protocol-and-skills.md)

# Every cua-driver tool Narad calls, with every argument it may send. The
# self-check at startup compares this table with the server's own tools/list:
# a missing tool, or an argument the tool's input schema does not declare,
# stops the session before anything is sent. Names and arguments come from
# trycua/cua libs/cua-driver at 912a455: the live macOS schemas in
# rust/crates/platform-macos/src/tools/<tool>.rs, the typed ``target`` that
# cua-driver-core/src/action_target.rs adds to the input tools, and the
# ``verify_state`` contract in cua-driver-contract/src/verification.rs.
TOOLS: dict[str, tuple[str, ...]] = {
    "list_windows": ("pid", "on_screen_only"),
    "get_window_state": (
        "pid", "window_id", "include_accessibility_tree", "include_screenshot", "max_elements",
        "max_image_dimension", "screenshot_out_file", "timeout_ms", "session",
    ),
    "get_desktop_state": ("screenshot_out_file", "max_image_dimension", "session"),
    "click": (
        "target", "pid", "window_id", "element_token", "x", "y", "button", "count", "delivery_mode", "session",
    ),
    "type_text": ("target", "pid", "window_id", "element_token", "text", "delivery_mode", "session"),
    "press_key": ("target", "pid", "window_id", "element_token", "key", "modifiers", "delivery_mode", "session"),
    "hotkey": ("target", "pid", "window_id", "keys", "delivery_mode", "session"),
    "scroll": (
        "target", "pid", "window_id", "element_token", "x", "y", "direction", "amount", "by", "delivery_mode",
        "session",
    ),
    "move_cursor": ("target", "x", "y", "session"),
    "drag": ("target", "from_x", "from_y", "to_x", "to_y", "delivery_mode", "session"),
    "launch_app": ("bundle_id", "name"),
    "verify_state": ("pid", "window_id", "expect", "timeout_ms", "stable_samples", "session"),
}

# The canonical Effect contract of every input tool's result
# (docs/action-result-contract.md): what the driver can account for.
EFFECTS = ("confirmed", "partial", "unverifiable", "suspected_noop", "refused")

_CALL_TIMEOUT_S = 60.0
_START_TIMEOUT_S = 20.0
_MAX_RESTARTS = 5  # a minute
_STDERR_TAIL = 40


class CuaSessionError(RuntimeError):
    """The session could not start, stopped, or a call failed on the transport."""


class CuaContractMismatch(CuaSessionError):
    """The server's tools do not match ``TOOLS``: the session refuses to run."""


class CuaToolError(CuaSessionError):
    """The tool ran and reported an error (MCP ``isError``)."""

    def __init__(self, message: str, *, result: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.result = result or {}


def cua_env() -> dict[str, str]:
    """The session's environment: the host's, with telemetry off."""
    env = os.environ.copy()
    env["CUA_DRIVER_RS_TELEMETRY_ENABLED"] = "false"
    env["CUA_TELEMETRY_ENABLED"] = "false"  # the compatibility name telemetry.rs also reads
    return env


def cua_binary() -> str | None:
    bundled = Path.home() / ".local" / "bin" / "cua-driver"
    return shutil.which("cua-driver") or (str(bundled) if bundled.is_file() else None)


def check_tools(listed: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare a tools/list answer with ``TOOLS``: {"ok", "missing", "arguments"}."""
    by_name = {str(tool.get("name")): tool for tool in listed if isinstance(tool, dict)}
    missing = sorted(name for name in TOOLS if name not in by_name)
    arguments: dict[str, list[str]] = {}
    for name, wanted in TOOLS.items():
        tool = by_name.get(name)
        if tool is None:
            continue
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        declared = set((schema.get("properties") or {}).keys()) if isinstance(schema, dict) else set()
        absent = [arg for arg in wanted if arg not in declared]
        if absent:
            arguments[name] = absent
    return {"ok": not missing and not arguments, "missing": missing, "arguments": arguments}


def tool_payload(result: dict[str, Any]) -> dict[str, Any]:
    """A tools/call result as one dict: its structured content, else its text as JSON."""
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    for part in result.get("content") or []:
        if isinstance(part, dict) and part.get("type") == "text":
            try:
                parsed = json.loads(part.get("text") or "")
            except ValueError:
                return {"summary": str(part.get("text") or "")[:1000]}
            return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {}


def tool_image(result: dict[str, Any]) -> tuple[bytes, str] | None:
    """The first image part of a tools/call result, decoded: (bytes, mime type)."""
    import base64

    for part in result.get("content") or []:
        if isinstance(part, dict) and part.get("type") == "image" and part.get("data"):
            try:
                return base64.b64decode(part["data"]), str(part.get("mimeType") or "image/png")
            except ValueError:
                return None
    return None


def tool_text(result: dict[str, Any]) -> str:
    return " ".join(
        str(part.get("text") or "") for part in result.get("content") or []
        if isinstance(part, dict) and part.get("type") == "text"
    ).strip()


class CuaMcpSession:
    """``cua-driver mcp`` on a private thread and event loop, restarted on failure."""

    def __init__(self, binary: str, *, args: tuple[str, ...] = ("mcp",), call_timeout_s: float = _CALL_TIMEOUT_S,
                 start_timeout_s: float = _START_TIMEOUT_S) -> None:
        self.binary = binary
        self.args = tuple(args)
        self.call_timeout_s = call_timeout_s
        self.start_timeout_s = start_timeout_s
        self._lock = threading.Lock()  # one start at a time
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._ready = False
        self._restarts: collections.deque[float] = collections.deque()
        self._stderr: collections.deque[str] = collections.deque(maxlen=_STDERR_TAIL)
        self.server_info: dict[str, Any] = {}
        self.tools: dict[str, dict[str, Any]] = {}
        self.check: dict[str, Any] | None = None
        self.starts = 0
        self.last_error = ""

    # ── public ───────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the process (if needed), initialise, list and check the tools."""
        with self._lock:
            if self._ready and self._alive():
                return
            now = time.monotonic()
            while self._restarts and now - self._restarts[0] > 60:
                self._restarts.popleft()
            if len(self._restarts) >= _MAX_RESTARTS:
                raise CuaSessionError(
                    f"cua-driver stopped {_MAX_RESTARTS} times in a minute; not restarting it yet ({self.last_error})"
                )
            self._restarts.append(now)
            self._ensure_loop()
            try:
                self._run(self._start(), self.start_timeout_s)
            except CuaContractMismatch:
                self._run(self._stop(), 5)
                raise
            except Exception as exc:
                self.last_error = _short(exc)
                self._run(self._stop(), 5)
                if isinstance(exc, CuaSessionError):
                    raise
                raise CuaSessionError(f"cua-driver mcp did not start: {self.last_error}") from exc

    def call(self, tool: str, arguments: dict[str, Any], *, timeout_s: float | None = None) -> dict[str, Any]:
        """One tools/call; returns the MCP result ({content, structuredContent, isError})."""
        if tool not in TOOLS:
            raise CuaSessionError(f"{tool} is not in Narad's cua-driver tool table")
        unknown = sorted(set(arguments) - set(TOOLS[tool]))
        if unknown:
            raise CuaSessionError(f"{tool} does not take {', '.join(unknown)} in Narad's tool table")
        self.start()
        timeout = timeout_s or self.call_timeout_s
        try:
            result = self._run(self._request("tools/call", {"name": tool, "arguments": arguments}), timeout + 2)
        except (FutureTimeoutError, asyncio.TimeoutError, TimeoutError) as exc:
            self.last_error = f"{tool} timed out after {int(timeout)} s"
            self._run(self._stop(), 5)  # it may be wedged: the next call starts a fresh one
            raise CuaSessionError(self.last_error) from exc
        if not isinstance(result, dict):
            raise CuaSessionError(f"{tool} returned no result object")
        if result.get("isError"):
            raise CuaToolError(tool_text(result) or f"{tool} failed", result=result)
        return result

    def status(self) -> dict[str, Any]:
        return {
            "running": self._ready and self._alive(),
            "binary": self.binary,
            "server": self.server_info,
            "tools": len(self.tools),
            "self_check": self.check,
            "starts": self.starts,
            "last_error": self.last_error or None,
        }

    def close(self) -> None:
        if self._loop is None:
            return
        try:
            self._run(self._stop(), 5)
        except Exception:
            pass
        loop, self._loop = self._loop, None
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    # ── the loop thread ──────────────────────────────────────────────────────

    def _ensure_loop(self) -> None:
        if self._loop is not None and self._thread is not None and self._thread.is_alive():
            return
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=self._loop_main, args=(loop,), name="narad-cua-mcp", daemon=True)
        self._loop, self._thread = loop, thread
        thread.start()

    @staticmethod
    def _loop_main(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()
        loop.close()

    def _run(self, coroutine: Any, timeout_s: float) -> Any:
        if self._loop is None:
            coroutine.close()
            raise CuaSessionError("the cua-driver session is closed")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=timeout_s)
        except FutureTimeoutError:
            future.cancel()
            raise

    def _alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    # ── on the loop ──────────────────────────────────────────────────────────

    async def _start(self) -> None:
        await self._stop()
        self._process = await asyncio.create_subprocess_exec(
            self.binary, *self.args,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=cua_env(), limit=64 * 1024 * 1024,  # a window screenshot comes back inline
        )
        self.starts += 1
        asyncio.ensure_future(self._read(self._process))
        asyncio.ensure_future(self._drain(self._process))
        initialized = await self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "narad", "version": "1"},
        })
        self.server_info = dict((initialized or {}).get("serverInfo") or {})
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(20):
            page = await self._request("tools/list", {"cursor": cursor} if cursor else {})
            listed.extend(item for item in (page or {}).get("tools") or [] if isinstance(item, dict))
            cursor = (page or {}).get("nextCursor")
            if not cursor:
                break
        self.tools = {str(tool.get("name")): tool for tool in listed}
        self.check = check_tools(listed)
        if not self.check["ok"]:
            problems = [f"missing tools: {', '.join(self.check['missing'])}"] if self.check["missing"] else []
            problems += [f"{name} lacks {', '.join(args)}" for name, args in self.check["arguments"].items()]
            self.last_error = "; ".join(problems)
            raise CuaContractMismatch(f"cua-driver's tools do not match Narad's table ({self.last_error})")
        self._ready = True
        self.last_error = ""

    async def _stop(self) -> None:
        self._ready = False
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            try:
                if process.stdin is not None:
                    process.stdin.close()
                await asyncio.wait_for(process.wait(), 2)
            except Exception:
                try:
                    process.kill()
                    await asyncio.wait_for(process.wait(), 2)
                except Exception:
                    pass
        self._fail_pending("cua-driver stopped")

    def _fail_pending(self, why: str) -> None:
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(CuaSessionError(why))

    async def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise CuaSessionError("cua-driver is not running")
        process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
        await process.stdin.drain()

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def _read(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        while True:
            try:
                line = await process.stdout.readline()
            except Exception as exc:
                self.last_error = f"reading cua-driver failed: {_short(exc)}"
                break
            if not line:
                break
            try:
                message = json.loads(line)
            except ValueError:
                continue  # not a JSON-RPC line
            if not isinstance(message, dict) or "id" not in message:
                continue  # a notification or a server request: nothing to do
            future = self._pending.get(message.get("id"))
            if future is None or future.done():
                continue
            if "error" in message:
                error = message.get("error") or {}
                future.set_exception(CuaSessionError(str(error.get("message") or error)[:500]))
            else:
                future.set_result(message.get("result"))
        if process is self._process:
            self._ready = False
            tail = " ".join(list(self._stderr)[-3:])
            self.last_error = self.last_error or f"cua-driver exited ({process.returncode}) {tail}".strip()
        self._fail_pending("cua-driver stopped")

    async def _drain(self, process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        while True:
            line = await process.stderr.readline()
            if not line:
                return
            self._stderr.append(line.decode("utf-8", "replace").strip()[:300])


def _short(exc: Any) -> str:
    return " ".join(str(exc or "").split())[:240] or type(exc).__name__


_SESSION: CuaMcpSession | None = None
_SESSION_LOCK = threading.Lock()


def session(binary: str | None = None) -> CuaMcpSession:
    """The host's session (one per binary path)."""
    global _SESSION
    path = binary or cua_binary()
    if not path:
        raise CuaSessionError("Install Cua Driver to enable desktop control")
    with _SESSION_LOCK:
        if _SESSION is None or _SESSION.binary != path:
            if _SESSION is not None:
                _SESSION.close()
            _SESSION = CuaMcpSession(path)
        return _SESSION


def session_status() -> dict[str, Any] | None:
    return _SESSION.status() if _SESSION is not None else None


def set_session(value: CuaMcpSession | None) -> None:
    """Swap the host session (tests)."""
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is not None and _SESSION is not value:
            _SESSION.close()
        _SESSION = value


def shutdown() -> None:
    set_session(None)


atexit.register(shutdown)
