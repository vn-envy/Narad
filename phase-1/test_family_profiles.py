from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import google_workspace
import health_skill
import server
from fastapi.testclient import TestClient

import family_profiles
import onboarding
import profile_context


class FamilyProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.patches = [
            patch.object(family_profiles, "FAMILY_PROFILES_PATH", self.root / "family_profiles.json"),
            patch.object(family_profiles, "PROFILE_SESSION_SECRET_PATH", self.root / "profile_secret"),
            patch.object(profile_context, "PROFILES_DIR", self.root / "profiles"),
            patch.object(onboarding, "ONBOARDING_PATH", self.root / "onboarding.json"),
            patch.object(health_skill, "_DB_PATH", self.root / "legacy-health.db"),
            patch.object(google_workspace, "_TOKEN_PATH", self.root / "legacy-google.json"),
        ]
        for active_patch in self.patches:
            active_patch.start()
        self.client = TestClient(server.app)

    def tearDown(self) -> None:
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.tempdir.cleanup()

    def _create_alice(self) -> dict:
        response = self.client.post("/profiles", json={"display_name": "Alice", "pin": "2468"})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_profile_create_login_and_identity_enforcement(self) -> None:
        initial = self.client.get("/profiles")
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json()["profiles"][0]["user_id"], "default")
        unlocked_login = self.client.post(
            "/profiles/login", json={"user_id": "default", "pin": ""}
        )
        self.assertEqual(unlocked_login.status_code, 401)

        secured = self.client.post(
            "/profiles/bootstrap", json={"user_id": "default", "pin": "8642"}
        )
        self.assertEqual(secured.status_code, 200, secured.text)
        repeated = self.client.post(
            "/profiles/bootstrap", json={"user_id": "default", "pin": "8642"}
        )
        self.assertEqual(repeated.status_code, 400)

        session = self._create_alice()
        token = session["token"]
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Narad-Profile-ID": "alice",
        }

        restored = self.client.get("/profiles/session", headers=headers)
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["profile"]["display_name"], "Alice")

        mismatch = self.client.get("/onboarding?user_id=default", headers=headers)
        self.assertEqual(mismatch.status_code, 403)

        wrong_pin = self.client.post("/profiles/login", json={"user_id": "alice", "pin": "1111"})
        self.assertEqual(wrong_pin.status_code, 401)

    def test_health_and_google_paths_are_profile_isolated(self) -> None:
        family_profiles.create_profile("Alice", "2468")
        family_profiles.create_profile("Bob", "1357")

        with profile_context.profile_scope("alice"):
            health_skill.log_symptom("headache", 4, "Alice only")
            alice_token_path = google_workspace._token_path()
        with profile_context.profile_scope("bob"):
            health_skill.log_symptom("back pain", 3, "Bob only")
            bob_token_path = google_workspace._token_path()

        with profile_context.profile_scope("alice"):
            alice_log = health_skill.get_health_log(days=30)
        with profile_context.profile_scope("bob"):
            bob_log = health_skill.get_health_log(days=30)

        self.assertIn("headache", str(alice_log).lower())
        self.assertNotIn("back pain", str(alice_log).lower())
        self.assertIn("back pain", str(bob_log).lower())
        self.assertNotIn("headache", str(bob_log).lower())
        self.assertNotEqual(alice_token_path, bob_token_path)
        self.assertEqual(alice_token_path.parent.name, "alice")
        self.assertEqual(bob_token_path.parent.name, "bob")


if __name__ == "__main__":
    unittest.main()
