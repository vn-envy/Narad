from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]
import narad_paths  # noqa: F401

# isort: split
import artemis_adapter
import browser_skill_adapter
import computer_use_skill
import interaction_targets

from profile_context import profile_scope


class InteractionTargetTests(unittest.TestCase):
    def test_grants_are_isolated_by_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def target_path(profile_id: str) -> Path:
                return root / profile_id / "interaction_targets.json"

            with patch.object(interaction_targets, "_path", side_effect=target_path):
                alice = interaction_targets.register_interaction_target(
                    "browser_skill", "browser-alice", profile_id="alice"
                )
                interaction_targets.register_interaction_target(
                    "artemis", "phone-bob", profile_id="bob"
                )

                self.assertEqual(
                    interaction_targets.resolve_interaction_target(
                        "browser_skill", alice["target_id"], profile_id="alice"
                    )["external_id"],
                    "browser-alice",
                )
                self.assertIsNone(
                    interaction_targets.resolve_interaction_target(
                        "browser_skill", alice["target_id"], profile_id="bob"
                    )
                )


class BrowserSkillAdapterTests(unittest.TestCase):
    def tearDown(self) -> None:
        browser_skill_adapter._SESSIONS.clear()

    def test_session_actions_use_typed_commands_and_profile_owner(self) -> None:
        calls: list[list[str]] = []

        def fake_run(args: list[str], **_: object):
            calls.append(args)
            if args[:2] == ["session", "start"]:
                return {"session_id": "bsk-1", "browser_instance_id": "browser-a"}
            if args[0] == "observe":
                return {"text": "Email @e1\nContinue @e2", "tab_id": 7}
            if args[0] == "screenshot":
                Path(args[args.index("--out") + 1]).write_bytes(b"png")
                return {"capture_id": "capture-1"}
            return {"effect_state": "committed"}

        with tempfile.TemporaryDirectory() as directory, patch.object(
            browser_skill_adapter, "ARTIFACTS_DIR", Path(directory)
        ), patch.object(
            browser_skill_adapter,
            "browser_skill_status",
            return_value={
                "ready": True,
                "browsers": [{"instance_id": "browser-a", "label": "Alice"}],
            },
        ), patch.object(browser_skill_adapter, "_run", side_effect=fake_run):
            session, created = browser_skill_adapter.open_browser_skill_session(
                task="Inspect account",
                browser_instance_id="browser-a",
                owner_profile_id="alice",
            )
            self.assertTrue(created)
            observation = browser_skill_adapter.observe_browser_skill_session(
                session.session_id, owner_profile_id="alice"
            )
            result = browser_skill_adapter.execute_browser_skill_actions(
                session.session_id,
                [{"action": "click", "ref": "e2"}],
                owner_profile_id="alice",
            )
            with self.assertRaises(PermissionError):
                browser_skill_adapter.observe_browser_skill_session(
                    session.session_id, owner_profile_id="bob"
                )

        self.assertEqual([item["ref"] for item in observation["interactive_elements"]], ["e1", "e2"])
        self.assertEqual(result[0]["effect_state"], "committed")
        self.assertIn(["click", "@e2", "--session", "bsk-1"], calls)

    def test_signed_in_computer_use_requires_profile_grant(self) -> None:
        with profile_scope("alice"), patch.object(
            interaction_targets, "resolve_interaction_target", return_value=None
        ):
            payload = computer_use_skill.computer_use(
                "Inspect my inbox", browser_context="signed_in", dry_run=False
            )
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["error"], "browser_target_unavailable")

    def _session(self, directory: str) -> browser_skill_adapter.BrowserSkillSession:
        session = browser_skill_adapter.BrowserSkillSession(
            session_id="signed_test",
            bsk_session_id="bsk-1",
            owner_profile_id="alice",
            browser_instance_id="browser-a",
            task="Test",
            run_dir=Path(directory) / "signed_test",
        )
        browser_skill_adapter._SESSIONS[session.session_id] = session
        return session

    def test_bsk_errors_read_nested_effect_state_with_auto_update_off(self) -> None:
        responses = iter([
            {
                "code": "timeout",
                "message": "click outcome unknown",
                "hint": "do not retry",
                "exit_code": 7,
                "data": {"reason": "input_outcome_unknown", "effect_state": "unknown"},
            },
            {"code": "cdp_failed", "message": "upload dispatched", "data": {"effect_state": "committed"}},
            {"code": "not_found", "message": "stale ref", "exit_code": 4, "data": {"effect_state": "none"}},
            {"message": "legacy shape", "effect_state": "unknown"},
        ])
        envs: list[dict[str, str]] = []

        def fake_subprocess(command, **kwargs):
            envs.append(kwargs["env"])
            return SimpleNamespace(returncode=1, stdout=json.dumps(next(responses)), stderr="")

        states = []
        with patch.object(browser_skill_adapter, "_binary", return_value="/usr/local/bin/bsk"), patch.object(
            browser_skill_adapter.subprocess, "run", side_effect=fake_subprocess
        ):
            for _ in range(4):
                with self.assertRaises(browser_skill_adapter.BrowserSkillError) as raised:
                    browser_skill_adapter._run(["click", "@e5", "--session", "bsk-1"])
                states.append(raised.exception.effect_state)

        self.assertEqual(states, ["unknown", "committed", "none", "unknown"])
        self.assertTrue(all(env["BSK_AUTO_UPDATE"] == "off" for env in envs))

    def test_signed_in_navigation_is_policy_checked_and_tracks_final_url(self) -> None:
        calls: list[list[str]] = []

        def fake_run(args: list[str], **_: object):
            calls.append(args)
            return {"tab_id": 7, "url": args[1], "final_url": "https://accounts.example.com/home"}

        with tempfile.TemporaryDirectory() as directory, patch.object(
            browser_skill_adapter, "_run", side_effect=fake_run
        ):
            session = self._session(directory)
            rejected = [
                browser_skill_adapter.execute_browser_skill_actions(
                    session.session_id, [{"action": "navigate", "url": url}], owner_profile_id="alice"
                )[0]
                for url in ("http://169.254.169.254/latest/meta-data", "file:///etc/passwd")
            ]
            allowed = browser_skill_adapter.execute_browser_skill_actions(
                session.session_id,
                [{"action": "navigate", "url": "https://example.com/login"}],
                owner_profile_id="alice",
            )

        self.assertEqual([row["status"] for row in rejected], ["error", "error"])
        self.assertEqual([row["effect_state"] for row in rejected], ["none", "none"])
        self.assertEqual(calls, [["navigate", "https://example.com/login", "--session", "bsk-1"]])
        self.assertEqual(allowed[0]["status"], "ok")
        self.assertEqual(session.last_url, "https://accounts.example.com/home")

    def test_signed_in_downloads_stay_in_the_session_downloads_folder(self) -> None:
        calls: list[list[str]] = []

        def fake_run(args: list[str], **_: object):
            calls.append(args)
            return {"tab_id": 7}

        with tempfile.TemporaryDirectory() as directory, patch.object(
            browser_skill_adapter, "_run", side_effect=fake_run
        ):
            session = self._session(directory)
            downloads = (session.run_dir / "downloads").resolve()
            rejected = [
                browser_skill_adapter.execute_browser_skill_actions(
                    session.session_id,
                    [{"action": "download", "ref": "e3", "path": path}],
                    owner_profile_id="alice",
                )[0]
                for path in ("/etc/cron.d/narad", "../../escape.sh", "reports/../../x.sh", "~/Library/x")
            ]
            for action in (
                {"action": "download", "ref": "e3", "path": "statement.pdf"},
                {"action": "download", "ref": "e3"},
            ):
                browser_skill_adapter.execute_browser_skill_actions(
                    session.session_id, [action], owner_profile_id="alice"
                )

        self.assertEqual([row["status"] for row in rejected], ["error"] * 4)
        outputs = [Path(args[args.index("--out") + 1]) for args in calls]
        self.assertEqual(outputs[0], downloads / "statement.pdf")
        self.assertEqual(outputs[1].parent, downloads)
        self.assertEqual(len(outputs), 2)

    def test_observation_refs_resolve_to_vom_role_and_label(self) -> None:
        text = "\n".join([
            '  @e1 textbox "Email" [empty] placeholder="you@example.com"',
            '    @e2 button "Place order" [ctx: Checkout]',
            '  @e3 link "Docs" [→ docs.example.org]',
            '  @e4 textbox [empty] placeholder="Password"',
            '  @e6 button "Continue"',
            '  @e6 button "Pay now"',
            '  text "@e7 button \\"Next\\""',
        ])
        elements = browser_skill_adapter.observation_ref_elements(text)

        self.assertEqual(elements["e2"], {"ref": "e2", "role": "button", "name": "Place order"})
        self.assertEqual(elements["e1"]["placeholder"], "you@example.com")
        self.assertEqual(elements["e3"]["name"], "Docs")
        self.assertEqual(elements["e4"]["placeholder"], "Password")
        self.assertNotIn("e6", elements)
        self.assertNotIn("e7", elements)

    def test_signed_in_ref_actions_are_classified_by_their_observed_label(self) -> None:
        text = '  @e2 button "Next"\n  @e5 button "Place order"\n  @e7 textbox "Password"\n  @e8 button'

        def observe(*_: object, **__: object) -> dict:
            return {
                "url": "https://shop.example.com/cart",
                "title": "Signed-in Chromium",
                "text": text,
                "interactive_elements": [],
                "fields": [],
                "screenshot_path": None,
            }

        session = SimpleNamespace(session_id="signed_test")
        executed = Mock(return_value=[{"index": 1, "action": "click", "status": "ok", "effect_state": "committed"}])

        def run(action: dict) -> dict:
            return computer_use_skill.computer_use(
                "Finish checkout",
                session_id="signed_test",
                browser_context="signed_in",
                actions=[action],
                dry_run=False,
            )

        with profile_scope("alice"), patch.object(
            browser_skill_adapter, "open_browser_skill_session", return_value=(session, False)
        ), patch.object(
            browser_skill_adapter, "observe_browser_skill_session", side_effect=observe
        ), patch.object(browser_skill_adapter, "execute_browser_skill_actions", executed):
            gated = [
                run({"action": "click", "ref": "@e5"}),
                run({"action": "check", "ref": "e5"}),
                run({"action": "click", "target": {"ref": "e9"}}),
                run({"action": "click", "ref": "e8"}),
                run({"action": "fill", "ref": "e7", "value": "hunter2"}),
            ]
            executed.assert_not_called()
            next_page = run({"action": "click", "ref": "e2"})

        for payload in gated:
            self.assertEqual(payload["status"], "confirmation_required")
            self.assertTrue(payload["requires_confirmation"])
        self.assertEqual(next_page["status"], "ok")
        executed.assert_called_once()


class ArtemisAdapterTests(unittest.TestCase):
    def test_preview_is_profile_bound_and_high_risk_is_confirmed(self) -> None:
        grant = {"external_id": "emulator-5554", "label": "Alice phone"}
        with profile_scope("alice"), patch.object(
            artemis_adapter, "resolve_interaction_target", return_value=grant
        ), patch.object(
            artemis_adapter,
            "artemis_status",
            return_value={"available": True, "ready": True, "reason": None},
        ):
            preview = artemis_adapter.phone_use(
                "Send a message to Sam", mode="verified", dry_run=True
            )
            blocked = artemis_adapter.phone_use(
                "Send a message to Sam", mode="verified", dry_run=False
            )

        self.assertEqual(preview["status"], "preview")
        self.assertTrue(preview["requires_confirmation"])
        self.assertEqual(preview["provenance"]["profile_id"], "alice")
        self.assertEqual(blocked["status"], "confirmation_required")

    def test_remote_artemis_requires_https_and_token(self) -> None:
        with patch.dict(
            artemis_adapter.os.environ,
            {"NARAD_ALLOW_REMOTE_ARTEMIS": "1", "NARAD_ARTEMIS_TOKEN": ""},
            clear=False,
        ):
            with self.assertRaises(artemis_adapter.ArtemisAdapterError):
                artemis_adapter._validate_base_url("http://phone.example.test")


if __name__ == "__main__":
    unittest.main()
