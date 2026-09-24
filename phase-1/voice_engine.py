"""
Voice engine — tiered TTS + STT for Narad, Sarvam first for Indian languages.

Tiers (voice out), best available wins unless NARAD_TTS_ENGINE forces one:
  1. sarvam  — Sarvam Bulbul v3 cloud TTS. Used when SARVAM_API_KEY is connected
               (via Kunji) AND the privacy gateway rates Sarvam `trusted`
               (NARAD_PROVIDER_TIERS=sarvam=trusted): replies are read aloud
               as-is, so only local or trusted providers may receive them.
               11 Indian languages + Indian English, one voice per avatar.
  2. voxcpm  — VoxCPM (pip: voxcpm). Highest local quality, zero-shot cloning,
               needs GPU/MPS. Model id via NARAD_VOXCPM_MODEL.
  3. kokoro  — Kokoro-82M (pip: kokoro). Tiny, CPU-fast, runs anywhere.
               English + Hindi voices.

Voice in (first available wins unless NARAD_STT_ENGINE forces one):
  1. sarvam  — Sarvam Saaras (REST, clips under 30 s) when connected and
               trusted. NARAD_SARVAM_STT_MODE=codemix (default) keeps Hinglish as
               spoken: Hindi in Devanagari, English words in Latin script.
  2. whisper — faster-whisper (pip: faster-whisper), CPU int8. Model size via
               NARAD_WHISPER_MODEL. If both are missing, the frontend falls back
               to browser speech recognition.

Everything imports lazily — the server runs fine with none of these installed.
"""

from __future__ import annotations

import base64
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

# Sarvam Bulbul v3 speakers, one distinct voice per avatar. Override any of
# them with NARAD_SARVAM_VOICE_<AVATAR> (lowercase speaker id, e.g. "kabir").
SARVAM_VOICES: dict[str, str] = {
    "narad":       "shubh",
    "krishna":     "kabir",
    "rama":        "aditya",
    "parashurama": "ratan",
    "hanuman":     "rohan",
}
# Narad language hints → Sarvam BCP-47 codes (Bulbul v3 and Saaras share them).
SARVAM_LANGUAGES: dict[str, str] = {
    "en": "en-IN", "hi": "hi-IN", "bn": "bn-IN", "gu": "gu-IN", "kn": "kn-IN",
    "ml": "ml-IN", "mr": "mr-IN", "od": "od-IN", "or": "od-IN", "pa": "pa-IN",
    "ta": "ta-IN", "te": "te-IN",
}
_SARVAM_BASE = os.environ.get("SARVAM_BASE_URL", "https://api.sarvam.ai").rstrip("/")
_SARVAM_TTS_MODEL = os.environ.get("NARAD_SARVAM_TTS_MODEL", "bulbul:v3")
_SARVAM_STT_MODEL = os.environ.get("NARAD_SARVAM_STT_MODEL", "saaras:v3")
_SARVAM_CHUNK_CHARS = 1000  # bulbul:v3 accepts 2,500; smaller chunks return sooner
_SARVAM_SAMPLE_RATE = 24_000


def _sarvam_key() -> str:
    return os.environ.get("SARVAM_API_KEY", "").strip()


def _raw_content_allowed(provider: str) -> bool:
    """Speech and audio cannot be pseudonymised: only local or trusted providers."""
    try:
        import privacy_gateway

        return privacy_gateway.raw_allowed(provider)
    except Exception:
        return False


def _gateway_allows(provider: str, source: str, chars: int = 0) -> bool:
    import privacy_gateway

    return privacy_gateway.allow_raw(provider, source=source, chars=chars)


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
        if _sarvam_key() and _raw_content_allowed("sarvam"):
            tiers.append("sarvam")
        if _has("voxcpm") and self.device() != "cpu":
            tiers.append("voxcpm")
        if _has("kokoro"):
            tiers.append("kokoro")
        if forced != "auto":
            return [t for t in tiers if t == forced]
        return tiers

    def stt_tiers(self) -> list[str]:
        """Available STT engines, best first."""
        tiers: list[str] = []
        forced = os.environ.get("NARAD_STT_ENGINE", "auto").lower()
        if _sarvam_key() and _raw_content_allowed("sarvam"):
            tiers.append("sarvam")
        if _has("faster_whisper"):
            tiers.append("whisper")
        if forced != "auto":
            return [t for t in tiers if t == forced]
        return tiers

    def stt_available(self) -> bool:
        return bool(self.stt_tiers())

    def status(self) -> dict[str, Any]:
        stt = self.stt_tiers()
        return {
            "device": self.device(),
            "tts": {
                "tiers": self.tts_tiers(),
                "active": (self.tts_tiers() or [None])[0],
                "languages": sorted(SARVAM_LANGUAGES) if "sarvam" in self.tts_tiers() else ["en", "hi"],
            },
            "stt": {
                "tiers": stt,
                "engine": stt[0] if stt else None,
                "available": bool(stt),
                "model": _SARVAM_STT_MODEL if stt[:1] == ["sarvam"] else os.environ.get("NARAD_WHISPER_MODEL", "small"),
            },
        }

    # ------------------------------------------------------------------- TTS

    def synthesize(self, text: str, avatar: str, lang: str = "en") -> dict[str, Any]:
        """Blocking. Returns {audio: bytes, engine, sample_rate}. Raises RuntimeError
        when no engine can serve."""
        text = text.strip()[:MAX_TTS_CHARS]
        if not text:
            raise ValueError("empty text")
        avatar = avatar.lower()
        for tier in self.tts_tiers():
            try:
                if tier == "sarvam":
                    if not _gateway_allows("sarvam", "tts", len(text)):
                        continue
                    return self._tts_sarvam(text, avatar, lang)
                if tier == "voxcpm":
                    return self._tts_voxcpm(text, avatar)
                if tier == "kokoro":
                    return self._tts_kokoro(text, avatar, lang)
            except Exception:  # noqa: BLE001 — degrade to next tier
                logger.exception("TTS tier %s failed; trying next", tier)
        raise RuntimeError(
            "no TTS engine available — connect a trusted Sarvam key or "
            "pip install 'narad-harness[voice]'"
        )

    # ------------------------------------------------------------ Sarvam Bulbul

    @staticmethod
    def _sarvam_voice(avatar: str) -> str:
        override = os.environ.get(f"NARAD_SARVAM_VOICE_{avatar.upper()}", "").strip().lower()
        return override or SARVAM_VOICES.get(avatar, SARVAM_VOICES["narad"])

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

    def _tts_sarvam(self, text: str, avatar: str, lang: str) -> dict[str, Any]:
        import httpx
        import numpy as np

        voice = self._sarvam_voice(avatar)
        language = SARVAM_LANGUAGES.get(lang, "en-IN")
        pcm_parts: list[Any] = []
        with httpx.Client(timeout=30) as client:
            for chunk in self._chunk_text(text, _SARVAM_CHUNK_CHARS):
                resp = client.post(
                    f"{_SARVAM_BASE}/text-to-speech",
                    headers={"api-subscription-key": _sarvam_key(), "Content-Type": "application/json"},
                    json={
                        "text": chunk,
                        "language_code": language,
                        "speaker": voice,
                        "model": _SARVAM_TTS_MODEL,
                        "speech_sample_rate": _SARVAM_SAMPLE_RATE,
                    },
                )
                resp.raise_for_status()
                for encoded in resp.json().get("audios") or []:
                    with wave.open(io.BytesIO(base64.b64decode(encoded)), "rb") as w:
                        pcm_parts.append(np.frombuffer(w.readframes(w.getnframes()), dtype="<i2"))
        if not pcm_parts:
            raise RuntimeError("Sarvam returned no audio")
        pcm = np.concatenate(pcm_parts).astype("float32") / 32767.0
        return {
            "audio": _pcm_to_wav(pcm, _SARVAM_SAMPLE_RATE),
            "engine": "sarvam",
            "sample_rate": _SARVAM_SAMPLE_RATE,
            "voice": voice,
            "language": language,
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

    def transcribe(self, audio_path: str, lang: str | None = None) -> dict[str, Any]:
        """Blocking. Returns {text, language, duration, engine}."""
        tiers = self.stt_tiers()
        if not tiers:
            raise RuntimeError("no speech-to-text engine: connect a trusted Sarvam key or install faster-whisper")
        for tier in tiers:
            try:
                if tier == "sarvam":
                    if not _gateway_allows("sarvam", "stt", os.path.getsize(audio_path)):
                        continue
                    return self._stt_sarvam(audio_path, lang)
                if tier == "whisper":
                    return self._stt_whisper(audio_path, lang)
            except Exception:  # noqa: BLE001 — degrade to next tier
                logger.exception("STT tier %s failed; trying next", tier)
        raise RuntimeError("speech-to-text failed on every engine")

    def _stt_sarvam(self, audio_path: str, lang: str | None) -> dict[str, Any]:
        import httpx

        data = {
            "model": _SARVAM_STT_MODEL,
            "mode": os.environ.get("NARAD_SARVAM_STT_MODE", "codemix"),
            "language_code": SARVAM_LANGUAGES.get(lang or "", "unknown"),
        }
        ext = os.path.splitext(audio_path)[1].lstrip(".").lower() or "webm"
        with open(audio_path, "rb") as handle, httpx.Client(timeout=45) as client:
            resp = client.post(
                f"{_SARVAM_BASE}/speech-to-text",
                headers={"api-subscription-key": _sarvam_key()},
                data=data,
                files={"file": (os.path.basename(audio_path), handle, f"audio/{ext}")},
            )
        resp.raise_for_status()
        payload = resp.json()
        return {
            "text": str(payload.get("transcript") or "").strip(),
            "language": payload.get("language_code"),
            "duration": None,
            "engine": "sarvam",
        }

    def _stt_whisper(self, audio_path: str, lang: str | None) -> dict[str, Any]:
        with self._lock:
            if self._whisper is None:
                from faster_whisper import WhisperModel

                size = os.environ.get("NARAD_WHISPER_MODEL", "small")
                self._whisper = WhisperModel(size, device="cpu", compute_type="int8")
        segments, info = self._whisper.transcribe(
            audio_path, vad_filter=True, language=lang if lang in ("en", "hi") else None
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return {
            "text": text,
            "language": getattr(info, "language", None),
            "duration": round(getattr(info, "duration", 0.0), 2),
            "engine": "whisper",
        }


voice_engine = VoiceEngine()
