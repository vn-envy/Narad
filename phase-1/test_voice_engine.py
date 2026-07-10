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
from voice_engine import KOKORO_VOICES, SMALLEST_VOICES, VoiceEngine, _pcm_to_wav


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

    # ── Smallest.ai tier ──────────────────────────────────────────────────────

    def test_smallest_voices_all_avatars_distinct(self) -> None:
        self.assertEqual(set(SMALLEST_VOICES), set(KOKORO_VOICES))
        self.assertEqual(len(set(SMALLEST_VOICES.values())), len(SMALLEST_VOICES))

    def test_smallest_tier_first_when_key_present(self) -> None:
        import os
        old = os.environ.get("SMALLEST_API_KEY")
        os.environ["SMALLEST_API_KEY"] = "eyJtest"
        try:
            tiers = VoiceEngine().tts_tiers()
            self.assertEqual(tiers[0], "smallest")
        finally:
            if old is None:
                os.environ.pop("SMALLEST_API_KEY", None)
            else:
                os.environ["SMALLEST_API_KEY"] = old

    def test_smallest_tier_absent_without_key(self) -> None:
        import os
        old = os.environ.pop("SMALLEST_API_KEY", None)
        try:
            self.assertNotIn("smallest", VoiceEngine().tts_tiers())
        finally:
            if old is not None:
                os.environ["SMALLEST_API_KEY"] = old

    def test_chunk_text_respects_limit(self) -> None:
        chunks = VoiceEngine._chunk_text("A sentence here. " * 60, 240)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 240 for c in chunks))
        self.assertEqual(" ".join(chunks).split(), ("A sentence here. " * 60).split())

    def test_chunk_text_hard_splits_runons(self) -> None:
        chunks = VoiceEngine._chunk_text("x" * 700, 240)
        self.assertTrue(all(len(c) <= 240 for c in chunks))
        self.assertEqual("".join(chunks), "x" * 700)

    def test_smallest_voice_env_override(self) -> None:
        import os
        os.environ["NARAD_SMALLEST_VOICE_KRISHNA"] = "custom-voice"
        try:
            self.assertEqual(VoiceEngine()._smallest_voice("krishna"), "custom-voice")
        finally:
            os.environ.pop("NARAD_SMALLEST_VOICE_KRISHNA", None)


if __name__ == "__main__":
    unittest.main()
