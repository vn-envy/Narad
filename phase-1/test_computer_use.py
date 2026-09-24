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
from unittest.mock import Mock, call, patch

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]
import narad_paths  # noqa: F401

# isort: split
import browser_act_skill
import computer_use_skill
import interaction_targets
from computer_use_skill import (
    _action_requires_confirmation,
    _cua_action_command,
    _normalise_actions,
    _validate_url,
    computer_use,
)

import anumati


def _approved_call(*args, **kwargs):
    """Call computer_use, approve the proposal it waits on, and call it again:
    the second, identical call consumes that approval and runs."""
    waiting = computer_use(*args, **kwargs)
    assert waiting["status"] == "needs_approval", waiting
    anumati.approve(waiting["proposal_id"], profile_id="default", decided_by="default")
    return computer_use(*args, **kwargs)


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
        grant = {"target_id": "target_host", "external_id": "host-primary"}
        with patch.dict(os.environ, {"NARAD_ENABLE_DESKTOP_CONTROL": "0"}), patch.object(
            interaction_targets, "resolve_interaction_target", return_value=grant
        ):
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
        self.assertEqual(click_payload["delivery_mode"], "foreground")
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


class LegacyFormHelperSafetyTests(unittest.TestCase):
    def test_blocked_submit_domains_strip_the_www_prefix_not_its_letters(self) -> None:
        for url in (
            "https://wellsfargo.com/apply",
            "https://www.wellsfargo.com/apply",
            "https://sub.wellsfargo.com/apply",
            "https://WWW.WellsFargo.com./apply",
        ):
            self.assertTrue(browser_act_skill._domain_is_blocked(url), url)
        for url in ("https://example.com/form", "https://notwellsfargo.com/", "https://www.example.org/"):
            self.assertFalse(browser_act_skill._domain_is_blocked(url), url)

        with patch.object(browser_act_skill, "computer_use") as tool:
            blocked = browser_act_skill.browser_fill(
                "https://wellsfargo.com/transfer", {"Amount": "100"}, dry_run=False, confirmed=True
            )
        self.assertEqual(blocked["status"], "blocked")
        tool.assert_not_called()

    def test_upload_and_submit_leaves_confirmation_to_computer_use(self) -> None:
        gated = {
            "status": "confirmation_required",
            "summary": "Action 'upload' may create an external side effect.",
            "requires_confirmation": True,
            "session_id": "browser_abc",
            "observation": {"title": "Form", "fields": []},
            "action_results": [{"action": "set_field", "status": "ok"}],
        }
        with patch.object(browser_act_skill, "computer_use", return_value=gated) as tool:
            preview = browser_act_skill.browser_upload_and_submit(
                "https://example.com/form",
                {"Name": "Ada"},
                {"Resume": "/tmp/resume.pdf"},
                session_id="browser_abc",
            )
            browser_act_skill.browser_upload_and_submit(
                "https://example.com/form",
                {"Name": "Ada"},
                {"Resume": "/tmp/resume.pdf"},
                session_id="browser_abc",
                confirmed=True,
            )

        first, second = (item.kwargs for item in tool.call_args_list)
        self.assertFalse(first["confirmed"])
        self.assertEqual(
            [item["action"] for item in first["actions"]], ["set_field", "upload", "submit"]
        )
        self.assertTrue(second["confirmed"])
        self.assertEqual(preview["status"], "confirmation_required")
        self.assertTrue(preview["requires_confirmation"])
        self.assertEqual(preview["files_uploaded"], [])


class CuaDriverAdapterTests(unittest.TestCase):
    binary = "/usr/local/bin/cua-driver"

    def test_scroll_sends_direction_amount_and_point(self) -> None:
        screenshot = Path("/tmp/narad-cua-test.png")
        cases = [
            ({"delta_y": 360}, "down", 3),
            ({"delta_y": -240}, "up", 2),
            ({"delta_x": 240}, "right", 2),
            ({"clicks": -4}, "down", 4),
            ({"clicks": 2}, "up", 2),
            ({"direction": "left", "amount": 7}, "left", 7),
            ({}, "down", 5),
        ]
        for extra, direction, amount in cases:
            command = _cua_action_command(
                self.binary, {"action": "scroll", "x": 100, "y": 200, **extra}, screenshot
            )
            self.assertEqual(command[:3], [self.binary, "call", "scroll"])
            payload = json.loads(command[3])
            self.assertEqual(
                {key: payload[key] for key in ("x", "y", "direction", "amount", "by")},
                {"x": 100.0, "y": 200.0, "direction": direction, "amount": amount, "by": "line"},
                extra,
            )
            self.assertNotIn("delta_x", payload)
            self.assertNotIn("delta_y", payload)
        with self.assertRaises(ValueError):
            _cua_action_command(self.binary, {"action": "scroll", "delta_y": 240}, screenshot)

    def _cua_status(self, permission_stdout: str) -> tuple[dict, list]:
        calls: list = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            if command[1:] == ["--version"]:
                return SimpleNamespace(returncode=0, stdout="cua-driver 0.14.0\n", stderr="")
            if command[1:] == ["status"]:
                return SimpleNamespace(returncode=0, stdout="Daemon is running\n", stderr="")
            return SimpleNamespace(returncode=0, stdout=permission_stdout, stderr="")

        with patch.dict(
            os.environ, {"NARAD_ENABLE_DESKTOP_CONTROL": "1", "NARAD_DESKTOP_PROVIDER": "cua"}
        ), patch.object(computer_use_skill.shutil, "which", return_value=self.binary), patch.object(
            computer_use_skill.subprocess, "run", side_effect=fake_run
        ):
            status = computer_use_skill._desktop_driver_status()
        return status["adapters"]["cua"], calls

    def test_permission_check_reads_json_booleans(self) -> None:
        granted, calls = self._cua_status(json.dumps({
            "accessibility": True,
            "screen_recording": True,
            "screen_recording_capturable": None,
            "source": {"attribution": "driver-daemon"},
        }))
        self.assertTrue(granted["permissions_ready"])
        self.assertTrue(granted["ready"])
        self.assertIn([self.binary, "permissions", "status", "--json"], [command for command, _ in calls])
        self.assertTrue(
            all(kwargs["env"]["CUA_DRIVER_RS_TELEMETRY_ENABLED"] == "false" for _, kwargs in calls)
        )

        for denied in (
            "Accessibility:    ❌ not granted\nScreen Recording: ❌ not granted\n",
            json.dumps({"accessibility": False, "screen_recording": True}),
            json.dumps({"accessibility": True, "screen_recording": True, "screen_recording_capturable": False}),
            json.dumps({"daemon_running": True, "status": "unknown", "reason": "not yet available"}),
        ):
            adapter, _ = self._cua_status(denied)
            self.assertFalse(adapter["permissions_ready"], denied)
            self.assertFalse(adapter["ready"], denied)

    def test_unverifiable_driver_effects_are_not_reported_as_success(self) -> None:
        outputs = iter([
            {"effect": "confirmed", "route": "global_input"},
            {"effect": "unverifiable", "route": "global_input"},
            {"effect": "suspected_noop", "route": "global_input"},
            {"platform": "macos"},
        ])
        envs: list = []

        def fake_run(command, **kwargs):
            envs.append(kwargs.get("env") or {})
            return SimpleNamespace(returncode=0, stdout=json.dumps(next(outputs)), stderr="")

        readiness = {
            "available": True,
            "reason": None,
            "selected_provider": "cua",
            "adapters": {"cua": {"binary": self.binary}},
        }
        actions = [
            {"action": "move", "x": 1, "y": 2},
            {"action": "click", "x": 1, "y": 2},
            {"action": "scroll", "x": 1, "y": 2, "delta_y": 240},
        ]
        with tempfile.TemporaryDirectory() as directory, patch.object(
            computer_use_skill, "_COMPUTER_ARTIFACTS_DIR", Path(directory)
        ), patch.object(
            computer_use_skill, "_desktop_driver_status", return_value=readiness
        ), patch.object(
            interaction_targets, "resolve_interaction_target", return_value={"target_id": "target_host"}
        ), patch.object(
            computer_use_skill, "_desktop_decision_hint", return_value=None
        ), patch.object(
            computer_use_skill, "_dharma_gate", return_value=None
        ), patch.object(computer_use_skill.subprocess, "run", side_effect=fake_run):
            payload = _approved_call(
                "Scroll the document",
                environment="desktop",
                actions=actions,
                dry_run=False,
                confirmed=True,
            )

        self.assertEqual(
            [item["status"] for item in payload["action_results"]], ["ok", "unverified", "unverified"]
        )
        self.assertEqual(payload["status"], "unverified")
        self.assertIn("could not verify 2", payload["summary"])
        self.assertNotIn("complete", payload["ui"]["summary"])
        self.assertTrue(all(env.get("CUA_DRIVER_RS_TELEMETRY_ENABLED") == "false" for env in envs))

    def test_pyautogui_fallback_requires_the_same_desktop_grant(self) -> None:
        readiness = {"available": True, "reason": None, "selected_provider": "pyautogui", "adapters": {}}
        executed = Mock(return_value=([{"action": "click", "status": "ok"}], None))
        actions = [{"action": "click", "x": 5, "y": 6}]
        with tempfile.TemporaryDirectory() as directory, patch.object(
            computer_use_skill, "_COMPUTER_ARTIFACTS_DIR", Path(directory)
        ), patch.object(
            computer_use_skill, "_desktop_driver_status", return_value=readiness
        ), patch.object(
            computer_use_skill, "_execute_pyautogui_actions", executed
        ), patch.object(
            computer_use_skill, "_desktop_decision_hint", return_value=None
        ), patch.object(computer_use_skill, "_dharma_gate", return_value=None):
            with patch.object(interaction_targets, "resolve_interaction_target", return_value=None):
                denied = computer_use(
                    "Click", environment="desktop", actions=actions, dry_run=False, confirmed=True
                )
            executed.assert_not_called()
            with patch.object(
                interaction_targets, "resolve_interaction_target", return_value={"target_id": "target_host"}
            ):
                # The model's confirmed=True no longer drives the desktop...
                waiting = computer_use(
                    "Click", environment="desktop", actions=actions, dry_run=False, confirmed=True
                )
                executed.assert_not_called()
                # ...the person's approval does, through the desktop executor.
                anumati.approve(waiting["proposal_id"], profile_id="default", decided_by="default")
                done = anumati.execute_approved(waiting["proposal_id"], profile_id="default")

        self.assertEqual(denied["status"], "unavailable")
        self.assertEqual(denied["error"], "desktop_target_unavailable")
        self.assertEqual(waiting["status"], "needs_approval")
        self.assertEqual(done.status, "executed")
        self.assertEqual(done.result["status"], "ok")
        executed.assert_called_once()

    def test_pyautogui_scroll_uses_wheel_notches(self) -> None:
        fake = SimpleNamespace(FAILSAFE=False, scroll=Mock(), hscroll=Mock(), screenshot=Mock())
        actions = [
            {"action": "scroll", "delta_y": 360},
            {"action": "scroll", "clicks": 2, "x": 5, "y": 6},
            {"action": "scroll", "delta_x": -240},
        ]
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {"pyautogui": fake}):
            results, _ = computer_use_skill._execute_pyautogui_actions(actions, Path(directory))

        self.assertEqual([item["status"] for item in results], ["ok", "ok", "ok"])
        self.assertEqual(fake.scroll.call_args_list, [call(-3), call(2, x=5.0, y=6.0)])
        fake.hscroll.assert_called_once_with(-2)


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
        # The browser URL policy refuses loopback unless the owner names it.
        cls.private_hosts = patch.dict(os.environ, {"NARAD_BROWSER_PRIVATE_HOSTS": "127.0.0.1"})
        cls.private_hosts.start()
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
        cls.private_hosts.stop()

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
        self.assertEqual(blocked["status"], "needs_approval")
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
