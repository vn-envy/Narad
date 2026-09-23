from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
