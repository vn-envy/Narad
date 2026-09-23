from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]
import narad_paths  # noqa: F401

# isort: split
import browser_act_skill
import computer_use_skill
from computer_use_skill import (
    _action_requires_confirmation,
    _cua_action_command,
    _normalise_actions,
    _validate_url,
    computer_use,
)


class ComputerUseContractTests(unittest.TestCase):
    def test_url_policy_rejects_credential_and_metadata_urls(self) -> None:
        self.assertEqual(_validate_url("https://example.com/path"), "https://example.com/path")
        with self.assertRaises(ValueError):
            _validate_url("file:///etc/passwd")
        with self.assertRaises(ValueError):
            _validate_url("https://user:secret@example.com")
        with self.assertRaises(ValueError):
            _validate_url("http://169.254.169.254/latest/meta-data")

    def test_action_contract_and_confirmation_classification(self) -> None:
        actions = _normalise_actions(
            [
                {"action": "scroll", "delta_y": 500},
                {"action": "click", "target": {"role": "button", "name": "Next"}},
            ],
            "browser",
        )
        self.assertEqual([item["action"] for item in actions], ["scroll", "click"])
        self.assertFalse(_action_requires_confirmation(actions[1]))
        self.assertTrue(_action_requires_confirmation({"action": "submit"}))
        self.assertTrue(_action_requires_confirmation({"action": "press", "key": "Enter"}))
        self.assertTrue(_action_requires_confirmation({"action": "click", "x": 20, "y": 30}))
        self.assertTrue(
            _action_requires_confirmation(
                {"action": "fill", "target": {"label": "Password"}, "value": "secret"}
            )
        )
        self.assertEqual(
            _normalise_actions([{"type": "close"}], "browser"),
            [{"action": "close"}],
        )
        with self.assertRaises(ValueError):
            _normalise_actions([{"action": "execute_javascript"}], "browser")

    def test_browser_action_preview_does_not_execute(self) -> None:
        manager = Mock()
        manager.open.return_value = (SimpleNamespace(session_id="browser_test"), True)
        manager.observe.return_value = {
            "url": "https://example.com",
            "title": "Example",
            "fields": [],
            "interactive_elements": [],
            "prompt_injection_signals": [],
            "screenshot_path": None,
        }
        with patch.object(computer_use_skill, "_BROWSER_MANAGER", manager):
            payload = computer_use(
                "Open the menu",
                start_url="https://example.com",
                actions=[{"action": "click", "target": {"role": "button", "name": "Menu"}}],
                dry_run=True,
            )

        self.assertEqual(payload["status"], "preview")
        self.assertEqual(payload["session_id"], "browser_test")
        self.assertEqual(len(payload["planned_actions"]), 1)
        manager.execute.assert_not_called()

    def test_legacy_form_helpers_forward_session_and_confirmation(self) -> None:
        response = {
            "status": "ok",
            "summary": "ready",
            "session_id": "browser_abc",
            "observation": {
                "title": "Form",
                "fields": [{"ref": "e1", "name": "Email"}],
                "screenshot_path": "/tmp/screenshot.png",
            },
            "action_results": [],
        }
        with patch.object(browser_act_skill, "computer_use", return_value=response) as tool:
            screenshot = browser_act_skill.browser_screenshot("https://example.com/form")
            browser_act_skill.browser_fill(
                "https://example.com/form",
                {"Email": "user@example.com"},
                dry_run=False,
                session_id=screenshot["session_id"],
                confirmed=True,
            )

        second_call = tool.call_args_list[1].kwargs
        self.assertEqual(second_call["session_id"], "browser_abc")
        self.assertEqual(second_call["start_url"], "")
        self.assertTrue(second_call["confirmed"])
        self.assertEqual(second_call["actions"][-1]["action"], "submit")

    def test_desktop_is_preview_only_when_disabled(self) -> None:
        with patch.dict(os.environ, {"NARAD_ENABLE_DESKTOP_CONTROL": "0"}):
            payload = computer_use(
                "Click the selected desktop control",
                environment="desktop",
                actions=[{"action": "click", "x": 50, "y": 50}],
                dry_run=True,
            )
        self.assertEqual(payload["status"], "preview")
        self.assertTrue(payload["requires_confirmation"])
        self.assertFalse(payload["readiness"]["enabled"])

    def test_cua_commands_use_exact_typed_desktop_targets(self) -> None:
        screenshot = Path("/tmp/narad-cua-test.png")
        click = _cua_action_command(
            "/usr/local/bin/cua-driver",
            {"action": "click", "x": 10, "y": 20, "button": "left"},
            screenshot,
        )
        capture = _cua_action_command(
            "/usr/local/bin/cua-driver",
            {"action": "screenshot"},
            screenshot,
        )

        self.assertEqual(click[:3], ["/usr/local/bin/cua-driver", "call", "click"])
        click_payload = json.loads(click[3])
        capture_payload = json.loads(capture[3])
        self.assertEqual(click_payload["target"], {"kind": "desktop", "display_id": "primary"})
        self.assertEqual(capture[:3], ["/usr/local/bin/cua-driver", "call", "get_desktop_state"])
        self.assertEqual(capture_payload["screenshot_out_file"], str(screenshot))
        self.assertNotIn("switch", click)
        self.assertNotIn("permissions", click)

    def test_desktop_drag_is_supported_and_confirmation_gated(self) -> None:
        actions = _normalise_actions(
            [{"action": "drag", "x1": 10, "y1": 20, "x2": 30, "y2": 40}],
            "desktop",
        )
        self.assertEqual(actions[0]["action"], "drag")
        self.assertTrue(_action_requires_confirmation(actions[0], "desktop"))


class _FormHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = b"""<!doctype html>
<html>
  <head><title>Persistent form</title></head>
  <body>
    <form action="/submitted">
      <label for="name">Name</label>
      <input id="name" name="name" />
      <button type="submit">Submit</button>
    </form>
  </body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@unittest.skipUnless(
    os.environ.get("NARAD_RUN_BROWSER_TESTS") == "1",
    "set NARAD_RUN_BROWSER_TESTS=1 to launch Chromium",
)
class ComputerUseBrowserIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _FormHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/"
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.original_artifact_dir = computer_use_skill._COMPUTER_ARTIFACTS_DIR
        computer_use_skill._COMPUTER_ARTIFACTS_DIR = Path(cls.tmpdir.name)

    @classmethod
    def tearDownClass(cls) -> None:
        computer_use_skill.shutdown_computer_use()
        computer_use_skill._COMPUTER_ARTIFACTS_DIR = cls.original_artifact_dir
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmpdir.cleanup()

    def test_form_state_survives_across_tool_calls(self) -> None:
        opened = computer_use("Inspect the form", start_url=self.url, dry_run=False)
        self.assertEqual(opened["status"], "ok", opened)
        session_id = opened["session_id"]
        name_field = next(
            field for field in opened["observation"]["fields"] if field["name"] == "Name"
        )

        filled = computer_use(
            "Fill the name field",
            session_id=session_id,
            actions=[
                {
                    "action": "set_field",
                    "target": {"ref": name_field["ref"]},
                    "value": "Ada Lovelace",
                }
            ],
            dry_run=False,
        )
        self.assertEqual(filled["status"], "ok", filled)
        replay_path = Path(
            next(item for item in filled["artifacts"] if item["type"] == "script")["path"]
        )
        trace_path = Path(
            next(item for item in filled["artifacts"] if item["type"] == "trace")["path"]
        )
        compile(replay_path.read_text(encoding="utf-8"), str(replay_path), "exec")
        self.assertNotIn("Ada Lovelace", trace_path.read_text(encoding="utf-8"))

        observed = computer_use("Check the same page", session_id=session_id, dry_run=False)
        current_name = next(
            field for field in observed["observation"]["fields"] if field["name"] == "Name"
        )
        self.assertEqual(current_name["value"], "Ada Lovelace")

        submit = next(
            item
            for item in observed["observation"]["interactive_elements"]
            if item["name"] == "Submit"
        )
        blocked = computer_use(
            "Submit the form",
            session_id=session_id,
            actions=[{"action": "click", "target": {"ref": submit["ref"]}}],
            dry_run=False,
            confirmed=False,
        )
        self.assertEqual(blocked["status"], "confirmation_required")
        self.assertTrue(blocked["requires_confirmation"])

        closed = computer_use(
            "Close the browser",
            session_id=session_id,
            actions=[{"action": "close"}],
            dry_run=False,
        )
        self.assertEqual(closed["status"], "ok")


if __name__ == "__main__":
    unittest.main()
