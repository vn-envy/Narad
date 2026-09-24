from __future__ import annotations

import io
import os
import sys
import tempfile
import time
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split
import voice_engine as voice_module
import voice_preferences
from voice_engine import KOKORO_VOICES, SARVAM_VOICES, TTSCache, VoiceEngine, _pcm_to_wav

import profile_context
from profile_context import profile_scope

_TRUSTED = {"SARVAM_API_KEY": "sk_test", "NARAD_PROVIDER_TIERS": "sarvam=trusted"}


def _kokoro_out(text: str = "", *_: object) -> dict:
    return {"audio": b"RIFF" + text.encode() * 4, "engine": "kokoro", "sample_rate": 24_000, "voice": "am_liam"}


class VoiceEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        # Voice preferences are per profile: keep every test's profiles in a temp dir.
        profiles = tempfile.TemporaryDirectory()
        self.addCleanup(profiles.cleanup)
        patcher = patch.object(profile_context, "PROFILES_DIR", Path(profiles.name))
        patcher.start()
        self.addCleanup(patcher.stop)

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

    # ── Segment cache ─────────────────────────────────────────────────────────

    def test_repeated_segment_is_served_from_cache_per_profile(self) -> None:
        engine = VoiceEngine()
        kokoro = Mock(side_effect=_kokoro_out)
        with patch.object(engine, "tts_tiers", return_value=["kokoro"]), \
             patch.object(engine, "_tts_kokoro", kokoro):
            first = engine.synthesize("Namaste.", "narad", "hi")
            again = engine.synthesize("  Namaste. ", "narad", "hi")
            english_voice = engine.synthesize("Namaste.", "narad", "en")
            with profile_scope("alice"):
                other_profile = engine.synthesize("Namaste.", "narad", "hi")
        self.assertFalse(first["cached"])
        self.assertTrue(again["cached"])
        self.assertEqual(again["audio"], first["audio"])
        self.assertFalse(english_voice["cached"])  # another voice and language
        self.assertFalse(other_profile["cached"])  # a hit would tell Alice what the owner heard
        self.assertEqual(kokoro.call_count, 3)
        self.assertEqual(engine.status()["tts"]["cache"]["hits"], 1)

    def test_cache_evicts_least_recently_used_by_bytes(self) -> None:
        cache = TTSCache(max_bytes=1000)
        for n in range(4):
            cache.put(("p", "kokoro", "v", "en", str(n)), {"audio": b"x" * 240})
        self.assertIsNotNone(cache.get(("p", "kokoro", "v", "en", "0")))  # now most recent
        cache.put(("p", "kokoro", "v", "en", "4"), {"audio": b"x" * 240})
        self.assertIsNone(cache.get(("p", "kokoro", "v", "en", "1")))
        self.assertIsNotNone(cache.get(("p", "kokoro", "v", "en", "0")))
        self.assertLessEqual(cache.stats()["bytes"], 1000)
        cache.put(("p", "kokoro", "v", "en", "big"), {"audio": b"x" * 400})  # > a quarter: skipped
        self.assertIsNone(cache.get(("p", "kokoro", "v", "en", "big")))

    # ── Sarvam client, timeout and fallback ───────────────────────────────────

    def test_sarvam_segments_share_one_pooled_client_with_short_timeout(self) -> None:
        import base64

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
            w.writeframes(b"\x00\x00" * 240)
        audio = base64.b64encode(buf.getvalue()).decode()
        clients: list[dict] = []
        timeouts: list = []

        class _Resp:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return {"audios": [audio]}

        class _Client:
            def __init__(self, *a, **k) -> None:
                clients.append(k)

            def post(self, url, headers=None, json=None, timeout=None, **_):
                timeouts.append(timeout)
                return _Resp()

        engine = VoiceEngine()
        with patch.dict(os.environ, _TRUSTED), patch("httpx.Client", _Client):
            engine.synthesize("First sentence.", "narad")
            engine.synthesize("Second sentence.", "narad")
        self.assertEqual(len(clients), 1)  # one TLS connection pool for every segment
        self.assertGreater(clients[0]["limits"].keepalive_expiry, 30)
        self.assertEqual(len(timeouts), 2)
        self.assertLessEqual(timeouts[0].read, 10)
        self.assertLessEqual(timeouts[0].connect, 5)

    def test_failing_sarvam_cools_down_to_the_local_voice(self) -> None:
        import httpx

        home = self._privacy_home()
        engine = VoiceEngine()
        sarvam = Mock(side_effect=httpx.ConnectTimeout("slow network"))
        with patch.dict(os.environ, _TRUSTED), \
             patch.object(engine, "tts_tiers", return_value=["sarvam", "kokoro"]), \
             patch.object(engine, "_tts_sarvam", sarvam), \
             patch.object(engine, "_tts_kokoro", Mock(side_effect=_kokoro_out)):
            first = engine.synthesize("One.", "narad")
            second = engine.synthesize("Two.", "narad")
        self.assertEqual((first["engine"], second["engine"]), ("kokoro", "kokoro"))
        self.assertEqual(sarvam.call_count, 1)  # the second segment didn't wait for Sarvam
        self.assertEqual((home / "egress.jsonl").read_text().count('"source": "tts"'), 1)

    # ── Per-profile "keep my voice on this Mac" ───────────────────────────────

    def test_keep_voice_on_mac_skips_sarvam_for_that_profile_only(self) -> None:
        voice_preferences.save({"keep_voice_on_mac": True}, "alice")
        with patch.dict(os.environ, _TRUSTED):
            with profile_scope("alice"):
                self.assertNotIn("sarvam", VoiceEngine().tts_tiers())
                self.assertNotIn("sarvam", VoiceEngine().stt_tiers())
                self.assertTrue(VoiceEngine().status()["keep_voice_on_mac"])
            with profile_scope("bob"):
                self.assertEqual(VoiceEngine().tts_tiers()[0], "sarvam")
                self.assertEqual(VoiceEngine().stt_tiers()[0], "sarvam")

    def test_stt_tier_order_follows_profile_and_overrides(self) -> None:
        voice_preferences.save({"keep_voice_on_mac": True}, "alice")
        local = patch.multiple(
            voice_module,
            _mlx_whisper_available=Mock(return_value=True),
            _has=Mock(side_effect=lambda pkg: pkg == "faster_whisper"),
        )
        with local, patch.dict(os.environ, _TRUSTED):
            os.environ.pop("NARAD_STT_ENGINE", None)
            self.assertEqual(VoiceEngine().stt_tiers(), ["sarvam", "mlx_whisper", "whisper"])
            with profile_scope("alice"):
                self.assertEqual(VoiceEngine().stt_tiers(), ["mlx_whisper", "whisper"])
                with patch.dict(os.environ, {"NARAD_STT_ENGINE": "sarvam"}):
                    self.assertEqual(VoiceEngine().stt_tiers(), [])  # her choice wins over the override
            for forced, expected in (("mlx", ["mlx_whisper"]), ("whisper", ["whisper"]), ("sarvam", ["sarvam"])):
                with patch.dict(os.environ, {"NARAD_STT_ENGINE": forced}):
                    self.assertEqual(VoiceEngine().stt_tiers(), expected, forced)
            with patch.dict(os.environ, {"SARVAM_API_KEY": ""}):
                self.assertEqual(VoiceEngine().stt_tiers(), ["mlx_whisper", "whisper"])
            self.assertEqual(VoiceEngine().status()["stt"]["model"], "saaras:v3")

    def test_mlx_whisper_is_offered_only_on_apple_silicon(self) -> None:
        with patch.object(voice_module, "_has", return_value=True):
            with patch.object(voice_module, "sys", types.SimpleNamespace(platform="linux", modules=sys.modules)):
                self.assertFalse(voice_module._mlx_whisper_available())
            with patch.object(voice_module, "sys", types.SimpleNamespace(platform="darwin", modules=sys.modules)), \
                 patch.object(voice_module, "platform", types.SimpleNamespace(machine=lambda: "arm64")):
                self.assertTrue(voice_module._mlx_whisper_available())
        with patch.object(voice_module, "_has", return_value=False):
            self.assertFalse(voice_module._mlx_whisper_available())

    def test_mlx_whisper_loads_on_use_passes_language_and_unloads_when_idle(self) -> None:
        clip = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
        clip.write(b"fake-opus")
        clip.close()
        self.addCleanup(os.unlink, clip.name)
        calls: list[tuple] = []

        class ModelHolder:
            model = None
            model_path = None

        def transcribe(path, path_or_hf_repo=None, language=None, **_):
            ModelHolder.model, ModelHolder.model_path = object(), path_or_hf_repo  # loads on first use
            calls.append((path, path_or_hf_repo, language))
            return {"text": " मेरा phone number बदल दो ", "language": "hi", "segments": [{"end": 2.46}]}

        package = types.ModuleType("mlx_whisper")
        package.transcribe = transcribe
        submodule = types.ModuleType("mlx_whisper.transcribe")
        submodule.ModelHolder = ModelHolder
        engine = VoiceEngine()
        # Stands in for the idle timer (no thread): records the delay it was armed with.
        schedule = Mock(side_effect=lambda delay: setattr(engine, "_unload_timer", Mock()))
        with patch.dict(sys.modules, {"mlx_whisper": package, "mlx_whisper.transcribe": submodule}), \
             patch.dict(os.environ, {"NARAD_MLX_WHISPER": "mlx-community/test-turbo"}), \
             patch.object(engine, "stt_tiers", return_value=["mlx_whisper", "whisper"]), \
             patch.object(engine, "_schedule_unload", schedule):
            self.assertIsNone(ModelHolder.model)  # nothing loaded before speech arrives
            result = engine.transcribe(clip.name, "hi")
            engine.transcribe(clip.name, "od")  # no Whisper Odia: let it detect
            self.assertEqual(result, {
                "text": "मेरा phone number बदल दो", "language": "hi", "duration": 2.46, "engine": "mlx_whisper",
            })
            self.assertEqual(calls, [
                (clip.name, "mlx-community/test-turbo", "hi"),
                (clip.name, "mlx-community/test-turbo", None),
            ])
            schedule.assert_called_once_with(voice_module._STT_IDLE_UNLOAD_S)
            engine._whisper = object()
            engine._touch_stt("whisper")
            self.assertEqual(engine.unload_idle_stt(now=time.monotonic() + 10), [])
            self.assertIsNotNone(ModelHolder.model)
            later = time.monotonic() + voice_module._STT_IDLE_UNLOAD_S + 1
            self.assertEqual(sorted(engine.unload_idle_stt(now=later)), ["mlx_whisper", "whisper"])
        self.assertIsNone(ModelHolder.model)
        self.assertIsNone(engine._whisper)

if __name__ == "__main__":
    unittest.main()
