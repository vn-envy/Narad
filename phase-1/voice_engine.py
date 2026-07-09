"""
Voice engine — tiered local-first TTS + STT for Narad.

Tiers (voice out), best available wins unless NARAD_TTS_ENGINE forces one:
  1. smallest — Smallest.ai Waves cloud TTS. Preferred when SMALLEST_API_KEY
                is connected (via Kunji). 5 distinct avatar voices, multilingual
                (English + Hindi native). Local tiers remain as fallback.
  2. voxcpm   — VoxCPM (pip: voxcpm). Highest local quality, zero-shot cloning,
                needs GPU/MPS. Model id via NARAD_VOXCPM_MODEL.
  3. kokoro   — Kokoro-82M (pip: kokoro). Tiny, CPU-fast, runs anywhere.
                English + Hindi voices.
  4. sarvam   — Sarvam AI cloud API. Only used when SARVAM_API_KEY is set.
                (Handled by voice_api falling back to tts_api.)

Voice in:
  faster-whisper (pip: faster-whisper), CPU int8 by default. Model size via
  NARAD_WHISPER_MODEL (tiny/base/small/medium). If missing, the frontend
  falls back to browser speech recognition.

Everything imports lazily — the server runs fine with none of these installed.
"""

from __future__ import annotations

import importlib.util
import io
import logging
import os
import threading
import wave
from typing import Any

logger = logging.getLogger("narad.voice")

# ---------------------------------------------------------------- avatar voices

# Kokoro voice ids per avatar. lang 'en' uses American voices, 'hi' Hindi ones.
KOKORO_VOICES: dict[str, dict[str, str]] = {
    "krishna":     {"en": "am_michael", "hi": "hm_omega"},
    "rama":        {"en": "am_adam",    "hi": "hm_psi"},
    "parashurama": {"en": "am_onyx",    "hi": "hm_omega"},
    "hanuman":     {"en": "am_puck",    "hi": "hm_psi"},
    "narad":       {"en": "am_liam",    "hi": "hm_omega"},
}
_KOKORO_LANG_CODE = {"en": "a", "hi": "h"}  # kokoro pipeline lang codes

# Smallest.ai Waves — 5 distinct voices, one per avatar. Override any of them
# with NARAD_SMALLEST_VOICE_<AVATAR> (e.g. NARAD_SMALLEST_VOICE_KRISHNA=raj).
# If a preferred id is missing from the live catalog, the engine substitutes
# the first unused catalog voice so avatars always stay distinct.
SMALLEST_VOICES: dict[str, str] = {
    "krishna":     "magnus",
    "rama":        "aarush",
    "parashurama": "james",
    "hanuman":     "arnav",
    "narad":       "raghav",
}
_SMALLEST_MODEL = os.environ.get("NARAD_SMALLEST_MODEL", "lightning-v3.1")
_SMALLEST_BASE = "https://waves-api.smallest.ai/api/v1"
_SMALLEST_CHUNK_CHARS = 240  # per-request text limit is ~250 chars
_SMALLEST_SAMPLE_RATE = 24_000

# Optional per-avatar reference audio for VoxCPM zero-shot cloning:
#   $NARAD_VOICE_REF_DIR/<avatar>.wav  +  <avatar>.txt (its transcript)
_VOICE_REF_DIR = os.environ.get("NARAD_VOICE_REF_DIR", "")

MAX_TTS_CHARS = 1200


def _has(pkg: str) -> bool:
    try:
        return importlib.util.find_spec(pkg) is not None
    except (ImportError, ValueError):
        return False


def _pcm_to_wav(audio: Any, sample_rate: int) -> bytes:
    """float32/float64 numpy array (-1..1) → 16-bit PCM WAV bytes."""
    import numpy as np

    arr = np.asarray(audio, dtype="float32").flatten()
    pcm = (np.clip(arr, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class VoiceEngine:
    """Lazy, thread-safe singleton wrapping the local voice models."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._kokoro_pipelines: dict[str, Any] = {}   # lang_code → KPipeline
        self._voxcpm: Any = None
        self._whisper: Any = None
        self._device: str | None = None
        self._smallest_catalog: list[str] | None = None  # live voice ids, cached

    # ------------------------------------------------------------- capability

    def device(self) -> str:
        if self._device is None:
            dev = "cpu"
            if _has("torch"):
                try:
                    import torch

                    if torch.backends.mps.is_available():
                        dev = "mps"
                    elif torch.cuda.is_available():
                        dev = "cuda"
                except Exception:  # noqa: BLE001 — capability probe only
                    pass
            self._device = dev
        return self._device

    def tts_tiers(self) -> list[str]:
        """Available TTS engines, best first."""
        tiers: list[str] = []
        forced = os.environ.get("NARAD_TTS_ENGINE", "auto").lower()
        if os.environ.get("SMALLEST_API_KEY", "").strip():
            tiers.append("smallest")  # preferred when connected; locals stay as fallback
        if _has("voxcpm") and self.device() != "cpu":
            tiers.append("voxcpm")
        if _has("kokoro"):
            tiers.append("kokoro")
        if os.environ.get("SARVAM_API_KEY"):
            tiers.append("sarvam")
        if forced != "auto":
            return [t for t in tiers if t == forced]
        return tiers

    def stt_available(self) -> bool:
        return _has("faster_whisper")

    def status(self) -> dict[str, Any]:
        return {
            "device": self.device(),
            "tts": {
                "tiers": self.tts_tiers(),
                "active": (self.tts_tiers() or [None])[0],
            },
            "stt": {
                "engine": "faster-whisper" if self.stt_available() else None,
                "available": self.stt_available(),
                "model": os.environ.get("NARAD_WHISPER_MODEL", "small"),
            },
        }

    # ------------------------------------------------------------------- TTS

    def synthesize(self, text: str, avatar: str, lang: str = "en") -> dict[str, Any]:
        """Blocking. Returns {audio: bytes, engine, sample_rate}. Raises RuntimeError
        when no local engine can serve (caller may fall back to cloud)."""
        text = text.strip()[:MAX_TTS_CHARS]
        if not text:
            raise ValueError("empty text")
        avatar = avatar.lower()
        for tier in self.tts_tiers():
            if tier == "sarvam":
                break  # cloud fallback is handled by the API layer
            try:
                if tier == "smallest":
                    return self._tts_smallest(text, avatar, lang)
                if tier == "voxcpm":
                    return self._tts_voxcpm(text, avatar)
                if tier == "kokoro":
                    return self._tts_kokoro(text, avatar, lang)
            except Exception:  # noqa: BLE001 — degrade to next tier
                logger.exception("TTS tier %s failed; trying next", tier)
        raise RuntimeError("no local TTS engine available")

    # ------------------------------------------------------- Smallest.ai Waves

    def _smallest_voices_available(self) -> list[str]:
        """Live voice-id catalog, fetched once. Empty list on any failure."""
        if self._smallest_catalog is not None:
            return self._smallest_catalog
        voices: list[str] = []
        try:
            import httpx

            resp = httpx.get(
                f"{_SMALLEST_BASE}/{_SMALLEST_MODEL}/get_voices",
                headers={"Authorization": f"Bearer {os.environ['SMALLEST_API_KEY'].strip()}"},
                timeout=15,
            )
            if resp.status_code == 200:
                payload = resp.json()
                items = payload.get("voices", payload) if isinstance(payload, dict) else payload
                if isinstance(items, list):
                    voices = [
                        v.get("voiceId") or v.get("voice_id") or v.get("id", "")
                        for v in items
                        if isinstance(v, dict)
                    ]
                    voices = [v for v in voices if v]
        except Exception:  # noqa: BLE001 — catalog is a nicety, not a dependency
            logger.exception("Smallest.ai voice catalog fetch failed")
        self._smallest_catalog = voices
        return voices

    def _smallest_voice(self, avatar: str) -> str:
        """env override > preferred map > first unused catalog voice. Always distinct."""
        override = os.environ.get(f"NARAD_SMALLEST_VOICE_{avatar.upper()}", "").strip()
        if override:
            return override
        preferred = SMALLEST_VOICES.get(avatar, SMALLEST_VOICES["narad"])
        catalog = self._smallest_voices_available()
        if not catalog or preferred in catalog:
            return preferred
        taken = set(SMALLEST_VOICES.values())
        for candidate in catalog:
            if candidate not in taken:
                return candidate
        return catalog[0]

    @staticmethod
    def _chunk_text(text: str, limit: int) -> list[str]:
        """Split on sentence boundaries, hard-wrapping any oversized sentence."""
        import re

        sentences = re.split(r"(?<=[.!?।])\s+", text)
        chunks: list[str] = []
        current = ""
        for sentence in sentences:
            while len(sentence) > limit:  # pathological run-on — hard split
                chunks.append(sentence[:limit])
                sentence = sentence[limit:]
            if len(current) + len(sentence) + 1 <= limit:
                current = f"{current} {sentence}".strip()
            else:
                if current:
                    chunks.append(current)
                current = sentence
        if current:
            chunks.append(current)
        return chunks or [text[:limit]]

    def _tts_smallest(self, text: str, avatar: str, lang: str) -> dict[str, Any]:
        import httpx
        import numpy as np

        key = os.environ["SMALLEST_API_KEY"].strip()
        voice = self._smallest_voice(avatar)
        pcm_parts: list[Any] = []
        with httpx.Client(timeout=30) as client:
            for chunk in self._chunk_text(text, _SMALLEST_CHUNK_CHARS):
                resp = client.post(
                    f"{_SMALLEST_BASE}/{_SMALLEST_MODEL}/get_speech",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "text": chunk,
                        "voice_id": voice,
                        "sample_rate": _SMALLEST_SAMPLE_RATE,
                        "output_format": "wav",
                    },
                )
                resp.raise_for_status()
                with wave.open(io.BytesIO(resp.content), "rb") as w:
                    frames = w.readframes(w.getnframes())
                pcm_parts.append(np.frombuffer(frames, dtype="<i2"))
        pcm = np.concatenate(pcm_parts).astype("float32") / 32767.0
        return {
            "audio": _pcm_to_wav(pcm, _SMALLEST_SAMPLE_RATE),
            "engine": "smallest",
            "sample_rate": _SMALLEST_SAMPLE_RATE,
            "voice": voice,
        }

    def _tts_kokoro(self, text: str, avatar: str, lang: str) -> dict[str, Any]:
        import numpy as np

        lang = lang if lang in _KOKORO_LANG_CODE else "en"
        lang_code = _KOKORO_LANG_CODE[lang]
        with self._lock:
            pipe = self._kokoro_pipelines.get(lang_code)
            if pipe is None:
                from kokoro import KPipeline

                pipe = KPipeline(lang_code=lang_code)
                self._kokoro_pipelines[lang_code] = pipe
        voices = KOKORO_VOICES.get(avatar, KOKORO_VOICES["narad"])
        voice = voices.get(lang, voices["en"])
        chunks = [audio for _, _, audio in pipe(text, voice=voice)]
        audio = np.concatenate([np.asarray(c) for c in chunks])
        return {
            "audio": _pcm_to_wav(audio, 24_000),
            "engine": "kokoro",
            "sample_rate": 24_000,
            "voice": voice,
        }

    def _tts_voxcpm(self, text: str, avatar: str) -> dict[str, Any]:
        with self._lock:
            if self._voxcpm is None:
                from voxcpm import VoxCPM

                model_id = os.environ.get("NARAD_VOXCPM_MODEL", "openbmb/VoxCPM-0.5B")
                self._voxcpm = VoxCPM.from_pretrained(model_id)
        prompt_wav, prompt_text = self._voice_ref(avatar)
        wav = self._voxcpm.generate(
            text=text,
            prompt_wav_path=prompt_wav,
            prompt_text=prompt_text,
        )
        sr = getattr(self._voxcpm, "sample_rate", 16_000) or 16_000
        return {
            "audio": _pcm_to_wav(wav, int(sr)),
            "engine": "voxcpm",
            "sample_rate": int(sr),
            "voice": avatar if prompt_wav else "default",
        }

    @staticmethod
    def _voice_ref(avatar: str) -> tuple[str | None, str | None]:
        if not _VOICE_REF_DIR:
            return None, None
        wav = os.path.join(_VOICE_REF_DIR, f"{avatar}.wav")
        txt = os.path.join(_VOICE_REF_DIR, f"{avatar}.txt")
        if os.path.isfile(wav) and os.path.isfile(txt):
            with open(txt, encoding="utf-8") as f:
                return wav, f.read().strip()
        return None, None

    # ------------------------------------------------------------------- STT

    def transcribe(self, audio_path: str) -> dict[str, Any]:
        """Blocking. Returns {text, language, duration}."""
        if not self.stt_available():
            raise RuntimeError("faster-whisper not installed")
        with self._lock:
            if self._whisper is None:
                from faster_whisper import WhisperModel

                size = os.environ.get("NARAD_WHISPER_MODEL", "small")
                self._whisper = WhisperModel(size, device="cpu", compute_type="int8")
        segments, info = self._whisper.transcribe(audio_path, vad_filter=True)
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return {
            "text": text,
            "language": getattr(info, "language", None),
            "duration": round(getattr(info, "duration", 0.0), 2),
        }


voice_engine = VoiceEngine()
