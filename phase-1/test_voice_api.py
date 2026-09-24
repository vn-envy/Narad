"""Voice endpoints: one spoken segment per /voice/tts call with timing headers
and a per-profile cache, and each profile's own voice preferences.

All offline: engines are stubbed and every profile lives in a temp dir.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]
import narad_paths  # noqa: F401

# isort: split
import server
import voice_engine as voice_module
from fastapi.testclient import TestClient

import family_profiles
import onboarding
import pilot_metrics
import profile_context

_WAV = b"RIFF\x24\x00\x00\x00WAVEfmt "


def _speech(text: str, *_: object) -> dict:
    return {"audio": _WAV + text.encode(), "engine": "kokoro", "sample_rate": 24_000, "voice": "am_liam"}


class VoiceApiTest(unittest.TestCase):
    """Strict auth with the owner (default), Alice and Bob."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.engine = voice_module.voice_engine
        self.patches = [
            patch.object(server, "_AUTH_MODE", "strict"),
            patch.object(server, "_login_failures", {}),
            patch.object(family_profiles, "FAMILY_PROFILES_PATH", self.root / "family_profiles.json"),
            patch.object(family_profiles, "PROFILE_SESSION_SECRET_PATH", self.root / "profile_secret"),
            patch.object(profile_context, "PROFILES_DIR", self.root / "profiles"),
            patch.object(onboarding, "ONBOARDING_PATH", self.root / "onboarding.json"),
            patch.object(self.engine, "tts_cache", voice_module.TTSCache(1024 * 1024)),
            patch.object(self.engine, "_sarvam_down_until", 0.0),
        ]
        for active in self.patches:
            active.start()
        self.client = TestClient(server.app)
        family_profiles.update_profile("default", pin="8642")
        family_profiles.create_profile("Alice", "2468")
        family_profiles.create_profile("Bob", "1357")
        self.pins = {"alice": "2468", "bob": "1357"}
        for profile_id in self.pins:  # voice needs the current consent sheet accepted
            pilot_metrics.record_consent(profile_id, version=pilot_metrics.CONSENT_VERSION, accepted=True)
        self.tokens: dict[str, str] = {}

    def tearDown(self) -> None:
        for active in reversed(self.patches):
            active.stop()
        self.tempdir.cleanup()

    def _headers(self, user_id: str, **extra: str) -> dict[str, str]:
        if user_id not in self.tokens:
            response = self.client.post("/profiles/login", json={"user_id": user_id, "pin": self.pins[user_id]})
            self.assertEqual(response.status_code, 200, response.text)
            self.tokens[user_id] = response.json()["token"]
        return {"Authorization": f"Bearer {self.tokens[user_id]}", **extra}

    def _tts(self, user_id: str, text: str, **extra: str):
        return self.client.post(
            "/voice/tts",
            json={"text": text, "avatar": "krishna", "lang": "hi"},
            headers=self._headers(user_id, **extra),
        )

    # ── /voice/tts ────────────────────────────────────────────────────────────

    def test_segment_audio_with_timing_headers_and_a_per_profile_cache(self) -> None:
        kokoro = Mock(side_effect=_speech)
        with patch.object(self.engine, "tts_tiers", return_value=["kokoro"]), \
             patch.object(self.engine, "_tts_kokoro", kokoro):
            first = self._tts("alice", "आपकी रिपोर्ट तैयार है।", Accept="audio/wav")
            again = self._tts("alice", "आपकी रिपोर्ट तैयार है।", Accept="audio/wav")
            bob = self._tts("bob", "आपकी रिपोर्ट तैयार है।", Accept="audio/wav")
            as_json = self._tts("alice", "आपकी रिपोर्ट तैयार है।")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.headers["content-type"], "audio/wav")
        self.assertTrue(first.content.startswith(b"RIFF"))
        self.assertEqual(first.headers["x-narad-tts-engine"], "kokoro")
        self.assertEqual(first.headers["x-narad-tts-cache"], "miss")
        self.assertRegex(first.headers["server-timing"], r'^tts;dur=[\d.]+;desc="kokoro", cache;desc="miss", total;dur=[\d.]+$')
        self.assertEqual(first.headers["cache-control"], "no-store")
        self.assertEqual(again.headers["x-narad-tts-cache"], "hit")
        self.assertEqual(again.content, first.content)
        self.assertEqual(bob.headers["x-narad-tts-cache"], "miss")
        self.assertEqual(kokoro.call_count, 2)
        body = as_json.json()
        self.assertTrue(body["cached"])
        self.assertEqual(body["engine"], "kokoro")
        self.assertIn("audio_b64", body)

    def test_text_longer_than_a_segment_is_refused(self) -> None:
        with patch.object(self.engine, "tts_tiers", return_value=["kokoro"]):
            response = self._tts("alice", "word " * 300)
        self.assertEqual(response.status_code, 413)

    def test_sarvam_calls_pass_the_gateway_and_land_in_the_ledger_once_each(self) -> None:
        sarvam = Mock(side_effect=lambda text, avatar, lang: {**_speech(text), "engine": "sarvam"})
        env = {"SARVAM_API_KEY": "sk_test", "NARAD_PROVIDER_TIERS": "sarvam=trusted", "NARAD_TTS_ENGINE": "sarvam"}
        ledger = self.root / "profiles" / "alice" / "privacy" / "egress.jsonl"
        with patch.dict(os.environ, env), patch.object(self.engine, "_tts_sarvam", sarvam):
            first = self._tts("alice", "Your report is ready.", Accept="audio/wav")
            again = self._tts("alice", "Your report is ready.", Accept="audio/wav")
            rows = [json.loads(line) for line in ledger.read_text().splitlines()]
            # Alice keeps her voice on the Mac: Sarvam is out, even for a cached phrase.
            put = self.client.put("/voice/preferences", json={"keep_voice_on_mac": True},
                                  headers=self._headers("alice"))
            local_only = self._tts("alice", "Your report is ready.", Accept="audio/wav")
        self.assertEqual(first.headers["x-narad-tts-engine"], "sarvam")
        self.assertEqual(again.headers["x-narad-tts-cache"], "hit")
        self.assertEqual(sarvam.call_count, 1)
        self.assertEqual([(row["source"], row["tier"], row["profile"]) for row in rows], [("tts", "trusted", "alice")])
        self.assertEqual(put.status_code, 200)
        self.assertEqual(local_only.status_code, 503)  # no local engine in this test, and no Sarvam
        self.assertEqual(len(ledger.read_text().splitlines()), 1)

    # ── /voice/preferences ────────────────────────────────────────────────────

    def test_preferences_belong_to_each_profile(self) -> None:
        defaults = {"reply_language": "en", "script": "devanagari", "keep_voice_on_mac": False}
        self.assertEqual(self.client.get("/voice/preferences", headers=self._headers("alice")).json(),
                         {"profile": "alice", "preferences": defaults})
        saved = self.client.put(
            "/voice/preferences",
            json={"reply_language": "hi", "script": "roman", "keep_voice_on_mac": True},
            headers=self._headers("alice"),
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        alice = {"reply_language": "hi", "script": "roman", "keep_voice_on_mac": True}
        self.assertEqual(saved.json()["preferences"], alice)
        partial = self.client.put("/voice/preferences", json={"script": "devanagari"}, headers=self._headers("alice"))
        self.assertEqual(partial.json()["preferences"], {**alice, "script": "devanagari"})

        self.assertEqual(self.client.get("/voice/preferences", headers=self._headers("bob")).json(),
                         {"profile": "bob", "preferences": defaults})
        self.assertTrue((self.root / "profiles" / "alice" / "voice.json").exists())
        self.assertFalse((self.root / "profiles" / "bob" / "voice.json").exists())
        self.assertTrue(self.client.get("/voice/status", headers=self._headers("alice")).json()["keep_voice_on_mac"])
        self.assertFalse(self.client.get("/voice/status", headers=self._headers("bob")).json()["keep_voice_on_mac"])

    def test_preferences_cannot_be_read_or_written_for_someone_else(self) -> None:
        as_alice = self._headers("bob", **{"X-Narad-Profile-ID": "alice"})
        self.assertEqual(self.client.put("/voice/preferences", json={"keep_voice_on_mac": True},
                                         headers=as_alice).status_code, 403)
        self.assertEqual(self.client.get("/voice/preferences?user_id=alice",
                                         headers=self._headers("bob")).status_code, 403)
        self.assertEqual(self.client.get("/voice/preferences").status_code, 401)
        self.assertEqual(self.client.put("/voice/preferences", json={"reply_language": "fr"},
                                         headers=self._headers("alice")).status_code, 422)
        self.assertFalse((self.root / "profiles" / "alice" / "voice.json").exists())

    def test_reply_language_carries_the_script(self) -> None:
        self.assertIn("Devanagari", server._reply_language_instruction("hi"))
        self.assertIn("Devanagari", server._reply_language_instruction("hi-Deva"))
        roman = server._reply_language_instruction("hi-Latn")
        self.assertIn("Roman script", roman)
        self.assertIn("never Devanagari", roman)
        auto_roman = server._reply_language_instruction("auto-Latn")
        self.assertIn("language the person used", auto_roman)
        self.assertIn("Roman script", auto_roman)
        self.assertEqual(server._reply_language_instruction("auto"), "")
        self.assertEqual(server._reply_language_instruction("en"), "")


if __name__ == "__main__":
    unittest.main()
