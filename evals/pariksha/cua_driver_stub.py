#!/usr/bin/env python3
"""A stand-in for ``cua-driver mcp``: MCP over stdio with a tiny desktop.

Tests start it in place of the real binary. It answers ``initialize``,
``tools/list`` and ``tools/call`` like cua-driver does (newline-delimited
JSON-RPC 2.0), for one window of a notes app with a text field and a Save
button. Every call is appended to ``$CUA_STUB_LOG`` as one JSON line, and the
first line records the telemetry variables the process was started with.

Knobs (environment):
  CUA_STUB_LOG        where to append the calls (required to inspect them)
  CUA_STUB_TOOLS      "ok" (default), "missing" (no verify_state) or "old_scroll"
                      (scroll without direction/amount, the pre-0.15 shape)
  CUA_STUB_EFFECT     the Effect every input tool reports (default confirmed)
  CUA_STUB_CRASH_ON   a tool name: exit without answering the first time it is called
                      (a marker file next to the log makes it once per log)
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

_PNG = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
    )
).decode()

_INPUT = {"target": {"type": "object"}, "session": {"type": "string"}, "pid": {"type": "integer"},
          "window_id": {"type": "integer"}, "element_token": {"type": "string"},
          "delivery_mode": {"type": "string", "enum": ["background", "foreground"]}}


def _schema(**properties: dict) -> dict:
    return {"type": "object", "properties": properties, "additionalProperties": False}


def _tools() -> list[dict]:
    mode = os.environ.get("CUA_STUB_TOOLS", "ok")
    scroll = {**_INPUT, "x": {}, "y": {}, "direction": {}, "amount": {}, "by": {}}
    if mode == "old_scroll":
        scroll = {**_INPUT, "x": {}, "y": {}, "delta_x": {}, "delta_y": {}}
    tools = {
        "list_windows": _schema(pid={}, on_screen_only={}),
        "get_window_state": _schema(pid={}, window_id={}, include_accessibility_tree={}, include_screenshot={},
                                    max_elements={}, max_image_dimension={}, screenshot_out_file={},
                                    timeout_ms={}, session={}, query={}),
        "get_desktop_state": _schema(screenshot_out_file={}, max_image_dimension={}, session={}),
        "click": _schema(**_INPUT, x={}, y={}, button={}, count={}),
        "type_text": _schema(**_INPUT, text={}),
        "press_key": _schema(**_INPUT, key={}, modifiers={}),
        "hotkey": _schema(**_INPUT, keys={}),
        "scroll": _schema(**scroll),
        "move_cursor": _schema(target={}, x={}, y={}, session={}),
        "drag": _schema(target={}, from_x={}, from_y={}, to_x={}, to_y={}, delivery_mode={}, session={}),
        "launch_app": _schema(bundle_id={}, name={}),
        "verify_state": _schema(pid={}, window_id={}, expect={}, timeout_ms={}, stable_samples={}, session={}),
    }
    if mode == "missing":
        tools.pop("verify_state")
    return [{"name": name, "description": name, "inputSchema": schema} for name, schema in tools.items()]


class Desktop:
    def __init__(self) -> None:
        self.value = ""
        self.saved = False

    def elements(self) -> list[dict]:
        return [
            {"element_index": 0, "element_token": "tok-0", "role": "AXWindow", "label": "Notes", "depth": 0,
             "frame": {"x": 0, "y": 0, "w": 800, "h": 600}},
            {"element_index": 1, "element_token": "tok-1", "role": "AXTextArea", "label": "Note",
             "value": self.value, "depth": 1, "frame": {"x": 20, "y": 60, "w": 600, "h": 300}},
            {"element_index": 2, "element_token": "tok-2", "role": "AXButton", "label": "Save", "depth": 1,
             "frame": {"x": 20, "y": 400, "w": 80, "h": 30}},
        ] + ([{"element_index": 3, "element_token": "tok-3", "role": "AXStaticText", "label": "Saved", "depth": 1,
               "frame": {"x": 120, "y": 400, "w": 80, "h": 30}}] if self.saved else [])

    def call(self, name: str, args: dict) -> dict:
        effect = os.environ.get("CUA_STUB_EFFECT", "confirmed")
        if name == "list_windows":
            return {"structuredContent": {"windows": [
                {"window_id": 7, "pid": 4242, "app_name": "Notes", "title": "Shopping list", "z_index": 3,
                 "is_on_screen": True, "bounds": {"x": 0, "y": 0, "width": 800, "height": 600}},
            ]}}
        if name == "get_window_state":
            content = [{"type": "text", "text": "window state"}]
            if args.get("include_screenshot", True):
                content.append({"type": "image", "data": _PNG, "mimeType": "image/png"})
            structured = {"app_name": "Notes", "window_title": "Shopping list", "snapshot_id": "s1"}
            if args.get("include_accessibility_tree", True):
                structured.update({"elements": self.elements(), "element_count": len(self.elements())})
            return {"content": content, "structuredContent": structured}
        if name == "get_desktop_state":
            return {"content": [{"type": "image", "data": _PNG, "mimeType": "image/png"}], "structuredContent": {}}
        if name == "verify_state":
            wanted = (args.get("expect") or [{}])[0].get("element", {})
            label = wanted.get("selector", {}).get("label_contains", "")
            present = any(label and label.lower() in (item.get("label") or "").lower() for item in self.elements())
            value_ok = "value_equals" not in wanted or wanted["value_equals"] == self.value
            status = "satisfied" if present and value_ok else "unsatisfied"
            return {"structuredContent": {"status": status, "stable": True, "elapsed_ms": 5, "samples": 2,
                                          "predicates": [{"index": 0, "status": status}]}}
        if name == "launch_app" and args.get("name") == "Missing":
            return {"isError": True, "content": [{"type": "text", "text": "No application named Missing"}]}
        if name in {"click", "type_text", "press_key", "hotkey", "scroll", "drag", "move_cursor", "launch_app"}:
            if effect == "refused":
                return {"structuredContent": {"effect": "refused", "route": "accessibility",
                                              "error": {"code": "permission_required", "hint": "grant it"}}}
            if name == "type_text" and args.get("element_token") == "tok-1":
                self.value += str(args.get("text") or "")
            if name == "click" and args.get("element_token") == "tok-2":
                self.saved = True
            return {"structuredContent": {"effect": effect, "route": "accessibility",
                                          "delivery": {"mode": args.get("delivery_mode") or "not_applicable"},
                                          "summary": f"{name} done"}}
        return {"isError": True, "content": [{"type": "text", "text": f"unknown tool {name}"}]}


def main() -> None:
    log_path = os.environ.get("CUA_STUB_LOG")
    crash_on = os.environ.get("CUA_STUB_CRASH_ON", "")

    def record(entry: dict) -> None:
        if log_path:
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")

    record({"started": True, "argv": sys.argv[1:],
            "telemetry": os.environ.get("CUA_DRIVER_RS_TELEMETRY_ENABLED"),
            "telemetry_compat": os.environ.get("CUA_TELEMETRY_ENABLED")})
    desktop = Desktop()
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        method, request_id = message.get("method"), message.get("id")
        params = message.get("params") or {}
        if request_id is None:
            continue
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion"), "capabilities": {"tools": {}},
                      "serverInfo": {"name": "cua-driver", "version": "0.28.4-stub"}}
        elif method == "tools/list":
            result = {"tools": _tools()}
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            record({"tool": name, "arguments": args})
            marker = Path(f"{log_path}.crashed") if log_path else None
            if name == crash_on and marker is not None and not marker.exists():
                marker.write_text("1")
                sys.exit(3)
            result = desktop.call(name, args)
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id,
                                         "error": {"code": -32601, "message": f"no method {method}"}}) + "\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
