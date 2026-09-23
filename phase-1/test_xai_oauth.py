from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split
import xai_oauth


class PkceTest(unittest.TestCase):
    def test_pair_is_urlsafe_and_unpadded(self) -> None:
        verifier, challenge = xai_oauth._pkce_pair()
        for value in (verifier, challenge):
            self.assertNotIn("=", value)
            self.assertNotIn("+", value)
            self.assertNotIn("/", value)
        self.assertGreaterEqual(len(verifier), 43)

    def test_challenge_is_sha256_of_verifier(self) -> None:
        import base64
        import hashlib

        verifier, challenge = xai_oauth._pkce_pair()
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip("=")
        self.assertEqual(challenge, expected)


class EndpointPinningTest(unittest.TestCase):
    def test_accepts_xai_hosts(self) -> None:
        self.assertEqual(xai_oauth._pinned("https://auth.x.ai/oauth2/token", "fb"), "https://auth.x.ai/oauth2/token")
        self.assertEqual(xai_oauth._pinned("https://x.ai/t", "fb"), "https://x.ai/t")

    def test_rejects_http_and_foreign_hosts(self) -> None:
        self.assertEqual(xai_oauth._pinned("http://auth.x.ai/t", "fb"), "fb")
        self.assertEqual(xai_oauth._pinned("https://evil.com/t", "fb"), "fb")
        self.assertEqual(xai_oauth._pinned("https://notx.ai/t", "fb"), "fb")
        self.assertEqual(xai_oauth._pinned("https://x.ai.evil.com/t", "fb"), "fb")
        self.assertEqual(xai_oauth._pinned("", "fb"), "fb")


class StartLoginTest(unittest.TestCase):
    def test_authorize_url_shape(self) -> None:
        out = xai_oauth.start_login("http://127.0.0.1:8000/connections/xai/oauth/callback")
        parsed = urlparse(out["authorize_url"])
        self.assertEqual(parsed.scheme, "https")
        self.assertTrue((parsed.hostname or "").endswith("x.ai"))
        q = parse_qs(parsed.query)
        self.assertEqual(q["response_type"], ["code"])
        self.assertEqual(q["client_id"], [xai_oauth.CLIENT_ID])
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertEqual(q["state"], [out["state"]])
        self.assertIn("offline_access", q["scope"][0])
        # cleanup pending state
        xai_oauth._PENDING.pop(out["state"], None)

    def test_finish_login_rejects_unknown_state(self) -> None:
        with self.assertRaises(ValueError):
            xai_oauth.finish_login("some-code", "never-issued-state")


class StatusTest(unittest.TestCase):
    def test_status_never_leaks_tokens(self) -> None:
        status = xai_oauth.status()
        self.assertEqual(
            set(status), {"signed_in", "expires_at", "expired", "has_refresh"}
        )
        for value in status.values():
            self.assertNotIsInstance(value, str)  # booleans/numbers/None only


class RuntimeCredentialTest(unittest.TestCase):
    def test_expiring_oauth_token_is_refreshed_before_use(self) -> None:
        with TemporaryDirectory() as directory:
            token_path = Path(directory) / "xai_oauth.json"
            token_path.write_text(json.dumps({
                "access_token": "old-oauth-token",
                "refresh_token": "refresh-token",
                "expires_at": time.time() - 1,
            }))
            refreshed = {
                "access_token": "new-oauth-token",
                "refresh_token": "refresh-token",
                "expires_at": time.time() + 3600,
            }
            with (
                patch.object(xai_oauth, "_TOKEN_PATH", token_path),
                patch.object(xai_oauth, "_refresh", return_value=refreshed),
                patch.dict(os.environ, {"XAI_API_KEY": "old-oauth-token"}, clear=False),
            ):
                self.assertTrue(xai_oauth.ensure_runtime_token())
                self.assertEqual(os.environ["XAI_API_KEY"], "new-oauth-token")

    def test_disconnect_does_not_remove_an_external_api_key(self) -> None:
        with TemporaryDirectory() as directory:
            token_path = Path(directory) / "xai_oauth.json"
            token_path.write_text(json.dumps({
                "access_token": "stored-oauth-token",
                "refresh_token": "refresh-token",
                "expires_at": time.time() + 3600,
            }))
            with (
                patch.object(xai_oauth, "_TOKEN_PATH", token_path),
                patch.dict(os.environ, {"XAI_API_KEY": "external-api-key"}, clear=False),
            ):
                self.assertTrue(xai_oauth.disconnect())
                self.assertEqual(os.environ["XAI_API_KEY"], "external-api-key")


class RoutingIgnoresXaiCredentialsTest(unittest.TestCase):
    """Owner policy (2026-09-23): a Grok sign-in is kept only so it can be removed."""

    def test_stored_sign_in_never_makes_xai_routable(self) -> None:
        import model_registry

        import subscription_providers

        with TemporaryDirectory() as directory:
            token_path = Path(directory) / "xai_oauth.json"
            token_path.write_text(json.dumps({
                "access_token": "stored-oauth-token",
                "refresh_token": "refresh-token",
                "expires_at": time.time() + 3600,
            }))
            with (
                patch.object(xai_oauth, "_TOKEN_PATH", token_path),
                patch.object(xai_oauth, "ensure_runtime_token", side_effect=AssertionError("called")),
                patch.dict(os.environ, {"XAI_API_KEY": "stored-oauth-token"}, clear=False),
            ):
                self.assertFalse(model_registry.provider_available_for_model("xai/grok-4.6"))
                adapter = subscription_providers.get_adapter("xai-oauth")
                self.assertFalse(adapter.available())
                self.assertFalse(subscription_providers.subscription_active("xai-oauth"))
                status = adapter.status()
                self.assertTrue(status.signed_in)
                self.assertTrue(status.disabled_by_policy)
                self.assertIn("disconnect", status.detail)
                # Disconnect keeps working so the stored credential can be removed.
                self.assertTrue(xai_oauth.disconnect())
                self.assertNotIn("XAI_API_KEY", os.environ)
                self.assertFalse(adapter.status().signed_in)


if __name__ == "__main__":
    unittest.main()
