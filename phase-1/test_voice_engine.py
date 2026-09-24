from __future__ import annotations

import io
import sys
import unittest
import wave
from pathlib import Path

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split
from voice_engine import KOKORO_VOICES, SARVAM_VOICES, VoiceEngine, _pcm_to_wav


class VoiceEngineTest(unittest.TestCase):
    def test_status_shape(self) -> None:
        status = VoiceEngine().status()
        self.assertIn("device", status)
        self.assertIn("tiers", status["tts"])
        self.assertIsInstance(status["tts"]["tiers"], list)
        self.assertIn("available", status["stt"])

    def test_every_avatar_has_en_and_hi_voice(self) -> None:
        for avatar, voices in KOKORO_VOICES.items():
            self.assertIn("en", voices, avatar)
            self.assertIn("hi", voices, avatar)

    def test_pcm_to_wav_roundtrip(self) -> None:
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        audio = np.zeros(2400, dtype="float32")
        data = _pcm_to_wav(audio, 24_000)
        with wave.open(io.BytesIO(data)) as w:
            self.assertEqual(w.getframerate(), 24_000)
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getnframes(), 2400)

    def test_synthesize_raises_cleanly_without_engines(self) -> None:
        eng = VoiceEngine()
        if not eng.tts_tiers():
            with self.assertRaises((RuntimeError, ValueError)):
                eng.synthesize("hello", "krishna")

    def test_empty_text_rejected(self) -> None:
        with self.assertRaises(ValueError):
            VoiceEngine().synthesize("   ", "krishna")

    # ── Sarvam tier ───────────────────────────────────────────────────────────

    def _privacy_home(self):
        import tempfile
        from unittest.mock import patch

        import privacy_gateway

        home = Path(tempfile.mkdtemp(prefix="narad-voice-privacy-"))
        patcher = patch.object(privacy_gateway, "_privacy_dir", lambda profile_id=None: home)
        patcher.start()
        self.addCleanup(patcher.stop)
        return home

    def test_sarvam_voices_all_avatars_distinct(self) -> None:
        self.assertEqual(set(SARVAM_VOICES), set(KOKORO_VOICES))
        self.assertEqual(len(set(SARVAM_VOICES.values())), len(SARVAM_VOICES))

    def test_sarvam_first_for_voice_out_and_in_when_key_present_and_trusted(self) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"SARVAM_API_KEY": "sk_test", "NARAD_PROVIDER_TIERS": "sarvam=trusted"}):
            engine = VoiceEngine()
            self.assertEqual(engine.tts_tiers()[0], "sarvam")
            self.assertEqual(engine.stt_tiers()[0], "sarvam")
            self.assertIn("ta", engine.status()["tts"]["languages"])

    def test_untrusted_sarvam_never_receives_speech_or_text(self) -> None:
        # Speech and audio cannot be pseudonymised, so a redact-tier provider
        # is skipped and everything stays on the Mac.
        import os
        from unittest.mock import patch

        home = self._privacy_home()
        engine = VoiceEngine()
        with patch.dict(os.environ, {"SARVAM_API_KEY": "sk_test", "NARAD_PROVIDER_TIERS": ""}), \
             patch.object(engine, "tts_tiers", return_value=["sarvam"]), \
             patch.object(engine, "_tts_sarvam", side_effect=AssertionError("sent to Sarvam")):
            self.assertNotIn("sarvam", VoiceEngine().tts_tiers())
            self.assertNotIn("sarvam", VoiceEngine().stt_tiers())
            with self.assertRaises(RuntimeError):
                engine.synthesize("Asha's lab report is ready", "krishna")
        self.assertIn("raw_content", (home / "egress.jsonl").read_text())

    def test_sarvam_absent_without_key(self) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"NARAD_PROVIDER_TIERS": "sarvam=trusted"}):
            os.environ.pop("SARVAM_API_KEY", None)
            self.assertNotIn("sarvam", VoiceEngine().tts_tiers())
            self.assertNotIn("sarvam", VoiceEngine().stt_tiers())

    def test_sarvam_tts_request_and_audio(self) -> None:
        import base64
        import os
        from unittest.mock import patch

        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("numpy not installed")
        self._privacy_home()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24_000)
            w.writeframes(b"\x00\x00" * 2400)
        sent: list[dict] = []

        class _Resp:
            status_code = 200

            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return {"audios": [base64.b64encode(buf.getvalue()).decode()]}

        class _Client:
            def __init__(self, *a, **k) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc) -> None:
                pass

            def post(self, url, headers=None, json=None, **_):
                sent.append({"url": url, "headers": headers, "json": json})
                return _Resp()

        with patch.dict(os.environ, {"SARVAM_API_KEY": "sk_test", "NARAD_PROVIDER_TIERS": "sarvam=trusted"}), \
             patch("httpx.Client", _Client):
            out = VoiceEngine().synthesize("आपकी रिपोर्ट तैयार है।", "rama", "hi")
        self.assertEqual(out["engine"], "sarvam")
        self.assertEqual(sent[0]["url"], "https://api.sarvam.ai/text-to-speech")
        self.assertEqual(sent[0]["headers"]["api-subscription-key"], "sk_test")
        self.assertEqual(sent[0]["json"]["language_code"], "hi-IN")
        self.assertEqual(sent[0]["json"]["speaker"], SARVAM_VOICES["rama"])
        self.assertEqual(sent[0]["json"]["model"], "bulbul:v3")
        with wave.open(io.BytesIO(out["audio"])) as w:
            self.assertEqual(w.getnframes(), 2400)

    def test_sarvam_stt_codemix_with_whisper_fallback(self) -> None:
        import os
        import tempfile
        from unittest.mock import patch

        self._privacy_home()
        clip = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
        clip.write(b"fake-opus")
        clip.close()
        self.addCleanup(os.unlink, clip.name)
        sent: list[dict] = []

        class _Resp:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return {"transcript": "मेरा phone number बदल दो", "language_code": "hi-IN"}

        class _Client:
            def __init__(self, *a, **k) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc) -> None:
                pass

            def post(self, url, headers=None, data=None, files=None, **_):
                sent.append({"url": url, "data": data, "file": files["file"][2]})
                return _Resp()

        env = {"SARVAM_API_KEY": "sk_test", "NARAD_PROVIDER_TIERS": "sarvam=trusted"}
        with patch.dict(os.environ, env), patch("httpx.Client", _Client):
            result = VoiceEngine().transcribe(clip.name, "hi")
        self.assertEqual(result["engine"], "sarvam")
        self.assertEqual(result["text"], "मेरा phone number बदल दो")
        self.assertEqual(sent[0]["url"], "https://api.sarvam.ai/speech-to-text")
        self.assertEqual(sent[0]["data"]["mode"], "codemix")
        self.assertEqual(sent[0]["data"]["language_code"], "hi-IN")
        self.assertEqual(sent[0]["file"], "audio/webm")

        engine = VoiceEngine()
        with patch.dict(os.environ, env), \
             patch.object(engine, "stt_tiers", return_value=["sarvam", "whisper"]), \
             patch.object(engine, "_stt_sarvam", side_effect=RuntimeError("503")), \
             patch.object(engine, "_stt_whisper", return_value={"text": "local", "engine": "whisper"}):
            self.assertEqual(engine.transcribe(clip.name)["engine"], "whisper")

    def test_chunk_text_respects_limit(self) -> None:
        chunks = VoiceEngine._chunk_text("A sentence here. " * 60, 240)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 240 for c in chunks))
        self.assertEqual(" ".join(chunks).split(), ("A sentence here. " * 60).split())

    def test_chunk_text_hard_splits_runons(self) -> None:
        chunks = VoiceEngine._chunk_text("x" * 700, 240)
        self.assertTrue(all(len(c) <= 240 for c in chunks))
        self.assertEqual("".join(chunks), "x" * 700)

    def test_sarvam_voice_env_override(self) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"NARAD_SARVAM_VOICE_KRISHNA": "Priya"}):
            self.assertEqual(VoiceEngine()._sarvam_voice("krishna"), "priya")

if __name__ == "__main__":
    unittest.main()
