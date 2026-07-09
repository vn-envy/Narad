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
from voice_engine import KOKORO_VOICES, VoiceEngine, _pcm_to_wav


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
        if eng.tts_tiers() in ([], ["sarvam"]):
            with self.assertRaises((RuntimeError, ValueError)):
                eng.synthesize("hello", "krishna")

    def test_empty_text_rejected(self) -> None:
        with self.assertRaises(ValueError):
            VoiceEngine().synthesize("   ", "krishna")


if __name__ == "__main__":
    unittest.main()
