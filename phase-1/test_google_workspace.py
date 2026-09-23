from __future__ import annotations

import sys
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

_root = next(path for path in Path(__file__).resolve().parents if (path / "narad_paths.py").exists())
sys.path[:0] = [str(_root)]
import google_workspace

import narad_paths  # noqa: F401


class GoogleWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        google_workspace._PENDING.clear()

    def test_scopes_are_incremental_and_photos_uses_picker(self) -> None:
        scopes = google_workspace.requested_scopes(["calendar", "photos"], "read")
        self.assertIn("https://www.googleapis.com/auth/calendar.events.readonly", scopes)
        self.assertIn("https://www.googleapis.com/auth/photospicker.mediaitems.readonly", scopes)
        self.assertNotIn("https://www.googleapis.com/auth/photoslibrary.readonly", scopes)
        self.assertNotIn("https://www.googleapis.com/auth/gmail.readonly", scopes)

    def test_login_uses_pkce_state_and_loopback_redirect(self) -> None:
        with patch.dict("os.environ", {
            "GOOGLE_OAUTH_CLIENT_ID": "client-id",
            "GOOGLE_OAUTH_CLIENT_SECRET": "client-secret",
        }), patch.object(google_workspace, "_load_token", return_value={}):
            result = google_workspace.start_login(
                "http://127.0.0.1:8000/google/callback", ["gmail"], "read"
            )
        query = urllib.parse.parse_qs(urllib.parse.urlparse(result["authorize_url"]).query)
        self.assertEqual(query["state"], [result["state"]])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["redirect_uri"], ["http://127.0.0.1:8000/google/callback"])
        self.assertIn("https://www.googleapis.com/auth/gmail.readonly", query["scope"][0])

    def test_status_reports_read_and_write_separately(self) -> None:
        token = {"refresh_token": "stored", "scope": " ".join(
            google_workspace.requested_scopes(["gmail", "drive"], "write")
        )}
        with patch.dict("os.environ", {
            "GOOGLE_OAUTH_CLIENT_ID": "client-id",
            "GOOGLE_OAUTH_CLIENT_SECRET": "client-secret",
        }), patch.object(google_workspace, "_load_token", return_value=token):
            result = google_workspace.status()
        self.assertTrue(result["services"]["gmail"]["read"])
        self.assertTrue(result["services"]["gmail"]["write"])
        self.assertTrue(result["services"]["drive"]["write"])
        self.assertFalse(result["services"]["calendar"]["read"])


if __name__ == "__main__":
    unittest.main()
