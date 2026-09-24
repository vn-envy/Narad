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

The PWA sends a sentence or two per request, so a reply starts speaking with
its first sentence. Segments land in an in-memory LRU cache, Sarvam calls share
one pooled HTTP client with a short timeout, and after a Sarvam failure the
next segments go straight to the local voices for a cool-down.

Voice in (first available wins unless NARAD_STT_ENGINE forces one):
  1. sarvam      — Sarvam Saaras (REST, clips under 30 s) when connected and
                   trusted. NARAD_SARVAM_STT_MODE=codemix (default) keeps Hinglish
                   as spoken: Hindi in Devanagari, English words in Latin script.
  2. mlx_whisper — whisper-large-v3-turbo on the Mac's GPU (pip: mlx-whisper,
                   Apple Silicon only). Model via NARAD_MLX_WHISPER.
  3. whisper     — faster-whisper (pip: faster-whisper), CPU int8. Model size via
                   NARAD_WHISPER_MODEL. If none is available, the frontend falls
                   back to browser speech recognition.
Local speech models load on first use and unload after NARAD_STT_IDLE_UNLOAD_S
(default 300 s) idle, to respect the Mac's memory budget.

A profile that keeps its voice on the Mac (voice_preferences) never reaches the
Sarvam tiers, whatever Sarvam's trust rating.

Everything imports lazily — the server runs fine with none of these installed.
"""

from __future__ import annotations

import base64
import gc
import importlib.util
import io
import logging
import os
import platform
import sys
import threading
import time
import wave
from collections import OrderedDict
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
# A segment normally comes back well inside a second; past this, local TTS
# answers instead. Connecting is capped too, so a dead network can't stall a reply.
_SARVAM_TTS_TIMEOUT_S = float(os.environ.get("NARAD_SARVAM_TTS_TIMEOUT", "5"))
_SARVAM_STT_TIMEOUT_S = 45.0
_SARVAM_CONNECT_TIMEOUT_S = 3.0
# After a Sarvam failure, skip it this long so the reply's later segments
# don't each wait out their own timeout.
_SARVAM_COOLDOWN_S = float(os.environ.get("NARAD_SARVAM_COOLDOWN", "30"))

_MLX_WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"
_STT_ALIASES = {"mlx": "mlx_whisper", "faster_whisper": "whisper", "faster-whisper": "whisper"}
_WHISPER_LANGUAGES = {"en", "hi", "bn", "gu", "kn", "ml", "mr", "pa", "ta", "te", "ur"}
_STT_IDLE_UNLOAD_S = float(os.environ.get("NARAD_STT_IDLE_UNLOAD_S", "300"))


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


def _cloud_voice_allowed() -> bool:
    """False when the current profile keeps its voice on the Mac (or that can't be read)."""
    try:
        import voice_preferences

        return voice_preferences.cloud_voice_allowed()
    except Exception:
        return False


def _profile_id() -> str:
    try:
        from profile_context import current_profile_id

        return current_profile_id()
    except Exception:
        return "default"


# Optional per-avatar reference audio for VoxCPM zero-shot cloning:
#   $NARAD_VOICE_REF_DIR/<avatar>.wav  +  <avatar>.txt (its transcript)
_VOICE_REF_DIR = os.environ.get("NARAD_VOICE_REF_DIR", "")

# One spoken segment: the PWA sends a sentence or two, 360 characters at most.
MAX_TTS_CHARS = 1000
_TTS_CACHE_BYTES = int(float(os.environ.get("NARAD_TTS_CACHE_MB", "32")) * 1024 * 1024)


def _has(pkg: str) -> bool:
    try:
        return importlib.util.find_spec(pkg) is not None
    except (ImportError, ValueError):
        return False


def _mlx_whisper_available() -> bool:
    """mlx-whisper runs only on Apple Silicon Macs."""
    return sys.platform == "darwin" and platform.machine() == "arm64" and _has("mlx_whisper")


def _whisper_language(lang: str | None) -> str | None:
    return lang if lang in _WHISPER_LANGUAGES else None


def _release_mlx_whisper() -> None:
    """Drop mlx-whisper's cached model and hand its Metal buffers back."""
    holder = getattr(sys.modules.get("mlx_whisper.transcribe"), "ModelHolder", None)
    if holder is not None:
        holder.model = None
        holder.model_path = None
    mx = sys.modules.get("mlx.core")
    clear = getattr(mx, "clear_cache", None) or getattr(getattr(mx, "metal", None), "clear_cache", None)
    if callable(clear):
        try:
            clear()
        except Exception:  # noqa: BLE001 — best effort
            pass


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


class TTSCache:
    """In-memory LRU of synthesized segments, bounded by bytes.

    A key is (profile, engine, voice, language, text). The profile is in it
    because a hit is instant: a shared cache would let one family member probe
    what another had read aloud.
    """

    def __init__(self, max_bytes: int, max_entries: int = 512) -> None:
        self._items: OrderedDict[tuple[str, ...], dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.bytes = 0
        self.hits = 0
        self.misses = 0

    def get(self, key: tuple[str, ...]) -> dict[str, Any] | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return item

    def put(self, key: tuple[str, ...], value: dict[str, Any]) -> None:
        size = len(value.get("audio") or b"")
        if not size or size > self.max_bytes // 4:
            return
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self.bytes -= len(old["audio"])
            self._items[key] = value
            self.bytes += size
            while self._items and (self.bytes > self.max_bytes or len(self._items) > self.max_entries):
                _, evicted = self._items.popitem(last=False)
                self.bytes -= len(evicted["audio"])

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.bytes = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"entries": len(self._items), "bytes": self.bytes, "hits": self.hits, "misses": self.misses}


class VoiceEngine:
    """Lazy, thread-safe singleton wrapping the local voice models."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._kokoro_pipelines: dict[str, Any] = {}   # lang_code → KPipeline
        self._voxcpm: Any = None
        self._whisper: Any = None
        self._device: str | None = None
        self._http: Any = None                        # pooled httpx.Client for Sarvam
        self._http_lock = threading.Lock()
        self._sarvam_down_until = 0.0
        self._mlx_lock = threading.Lock()             # one Metal transcription at a time
        self._stt_used: dict[str, float] = {}         # local STT engine → last use
        self._unload_timer: threading.Timer | None = None
        self.tts_cache = TTSCache(_TTS_CACHE_BYTES)

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
        """Available TTS engines for the current profile, best first."""
        tiers: list[str] = []
        forced = os.environ.get("NARAD_TTS_ENGINE", "auto").lower()
        if _sarvam_key() and _raw_content_allowed("sarvam") and _cloud_voice_allowed():
            tiers.append("sarvam")
        if _has("voxcpm") and self.device() != "cpu":
            tiers.append("voxcpm")
        if _has("kokoro"):
            tiers.append("kokoro")
        if forced != "auto":
            return [t for t in tiers if t == forced]
        return tiers

    def stt_tiers(self) -> list[str]:
        """Available STT engines for the current profile, best first."""
        tiers: list[str] = []
        forced = os.environ.get("NARAD_STT_ENGINE", "auto").lower()
        forced = _STT_ALIASES.get(forced, forced)
        if _sarvam_key() and _raw_content_allowed("sarvam") and _cloud_voice_allowed():
            tiers.append("sarvam")
        if _mlx_whisper_available():
            tiers.append("mlx_whisper")
        if _has("faster_whisper"):
            tiers.append("whisper")
        if forced != "auto":
            return [t for t in tiers if t == forced]
        return tiers

    def stt_available(self) -> bool:
        return bool(self.stt_tiers())

    @staticmethod
    def _stt_model(engine: str | None) -> str | None:
        if engine == "sarvam":
            return _SARVAM_STT_MODEL
        if engine == "mlx_whisper":
            return os.environ.get("NARAD_MLX_WHISPER", _MLX_WHISPER_MODEL)
        if engine == "whisper":
            return os.environ.get("NARAD_WHISPER_MODEL", "small")
        return None

    def status(self) -> dict[str, Any]:
        stt = self.stt_tiers()
        tts = self.tts_tiers()
        return {
            "device": self.device(),
            "keep_voice_on_mac": not _cloud_voice_allowed(),
            "tts": {
                "tiers": tts,
                "active": (tts or [None])[0],
                "languages": sorted(SARVAM_LANGUAGES) if "sarvam" in tts else ["en", "hi"],
                "cache": self.tts_cache.stats(),
            },
            "stt": {
                "tiers": stt,
                "engine": stt[0] if stt else None,
                "available": bool(stt),
                "model": self._stt_model(stt[0] if stt else None),
            },
        }

    # ------------------------------------------------------------ Sarvam health

    def _sarvam_http(self) -> Any:
        """One pooled client: segments reuse a warm TLS connection."""
        with self._http_lock:
            if self._http is None:
                import httpx

                self._http = httpx.Client(
                    timeout=httpx.Timeout(_SARVAM_TTS_TIMEOUT_S, connect=_SARVAM_CONNECT_TIMEOUT_S),
                    limits=httpx.Limits(max_connections=8, max_keepalive_connections=4, keepalive_expiry=90.0),
                )
            return self._http

    def _sarvam_cooling(self) -> bool:
        return time.monotonic() < self._sarvam_down_until

    def _sarvam_failed(self, exc: Exception) -> None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (400, 413, 422):  # this text was refused; the service is fine
            return
        self._sarvam_down_until = time.monotonic() + _SARVAM_COOLDOWN_S

    # ------------------------------------------------------------------- TTS

    def _tts_key(self, tier: str, text: str, avatar: str, lang: str) -> tuple[str, ...]:
        if tier == "sarvam":
            voice, language = self._sarvam_voice(avatar), SARVAM_LANGUAGES.get(lang, "en-IN")
        elif tier == "kokoro":
            language = lang if lang in _KOKORO_LANG_CODE else "en"
            voices = KOKORO_VOICES.get(avatar, KOKORO_VOICES["narad"])
            voice = voices.get(language, voices["en"])
        else:
            voice, language = avatar, lang
        return (_profile_id(), tier, voice, language, text)

    def synthesize(self, text: str, avatar: str, lang: str = "en") -> dict[str, Any]:
        """Blocking. Returns {audio: bytes, engine, sample_rate, cached, synth_ms}.
        Raises RuntimeError when no engine can serve."""
        text = text.strip()[:MAX_TTS_CHARS]
        if not text:
            raise ValueError("empty text")
        avatar = avatar.lower()
        for tier in self.tts_tiers():
            started = time.perf_counter()
            key = self._tts_key(tier, text, avatar, lang)
            hit = self.tts_cache.get(key)
            if hit is not None:
                return {**hit, "cached": True, "synth_ms": (time.perf_counter() - started) * 1000}
            try:
                if tier == "sarvam":
                    if self._sarvam_cooling() or not _gateway_allows("sarvam", "tts", len(text)):
                        continue
                    try:
                        out = self._tts_sarvam(text, avatar, lang)
                    except Exception as exc:
                        self._sarvam_failed(exc)
                        raise
                elif tier == "voxcpm":
                    out = self._tts_voxcpm(text, avatar)
                elif tier == "kokoro":
                    out = self._tts_kokoro(text, avatar, lang)
                else:
                    continue
            except Exception:  # noqa: BLE001 — degrade to next tier
                logger.exception("TTS tier %s failed; trying next", tier)
                continue
            self.tts_cache.put(key, out)
            return {**out, "cached": False, "synth_ms": (time.perf_counter() - started) * 1000}
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
        client = self._sarvam_http()
        pcm_parts: list[Any] = []
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
                timeout=httpx.Timeout(_SARVAM_TTS_TIMEOUT_S, connect=_SARVAM_CONNECT_TIMEOUT_S),
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
            raise RuntimeError(
                "no speech-to-text engine: connect a trusted Sarvam key, or install "
                "mlx-whisper (Apple Silicon) or faster-whisper"
            )
        for tier in tiers:
            try:
                if tier == "sarvam":
                    if self._sarvam_cooling() or not _gateway_allows("sarvam", "stt", os.path.getsize(audio_path)):
                        continue
                    try:
                        return self._stt_sarvam(audio_path, lang)
                    except Exception as exc:
                        self._sarvam_failed(exc)
                        raise
                if tier == "mlx_whisper":
                    return self._stt_mlx_whisper(audio_path, lang)
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
        with open(audio_path, "rb") as handle:
            resp = self._sarvam_http().post(
                f"{_SARVAM_BASE}/speech-to-text",
                headers={"api-subscription-key": _sarvam_key()},
                data=data,
                files={"file": (os.path.basename(audio_path), handle, f"audio/{ext}")},
                timeout=httpx.Timeout(_SARVAM_STT_TIMEOUT_S, connect=_SARVAM_CONNECT_TIMEOUT_S),
            )
        resp.raise_for_status()
        payload = resp.json()
        return {
            "text": str(payload.get("transcript") or "").strip(),
            "language": payload.get("language_code"),
            "duration": None,
            "engine": "sarvam",
        }

    def _stt_mlx_whisper(self, audio_path: str, lang: str | None) -> dict[str, Any]:
        import mlx_whisper

        model = os.environ.get("NARAD_MLX_WHISPER", _MLX_WHISPER_MODEL)
        self._touch_stt("mlx_whisper")
        with self._mlx_lock:
            # Loads the model on first use and keeps it until the idle unload.
            result = mlx_whisper.transcribe(
                audio_path, path_or_hf_repo=model, language=_whisper_language(lang)
            )
        self._touch_stt("mlx_whisper")
        segments = result.get("segments") or []
        return {
            "text": str(result.get("text") or "").strip(),
            "language": result.get("language"),
            "duration": round(float(segments[-1].get("end", 0.0)), 2) if segments else None,
            "engine": "mlx_whisper",
        }

    def _stt_whisper(self, audio_path: str, lang: str | None) -> dict[str, Any]:
        with self._lock:
            if self._whisper is None:
                from faster_whisper import WhisperModel

                size = os.environ.get("NARAD_WHISPER_MODEL", "small")
                self._whisper = WhisperModel(size, device="cpu", compute_type="int8")
            model = self._whisper
        self._touch_stt("whisper")
        segments, info = model.transcribe(audio_path, vad_filter=True, language=_whisper_language(lang))
        text = " ".join(seg.text.strip() for seg in segments).strip()
        self._touch_stt("whisper")
        return {
            "text": text,
            "language": getattr(info, "language", None),
            "duration": round(getattr(info, "duration", 0.0), 2),
            "engine": "whisper",
        }

    # ------------------------------------------------------ idle model unload

    def _touch_stt(self, engine: str) -> None:
        """Note a local speech model's use; it unloads after _STT_IDLE_UNLOAD_S idle."""
        with self._lock:
            self._stt_used[engine] = time.monotonic()
            if self._unload_timer is None and _STT_IDLE_UNLOAD_S > 0:
                self._schedule_unload(_STT_IDLE_UNLOAD_S)

    def _schedule_unload(self, delay: float) -> None:
        timer = threading.Timer(delay, self.unload_idle_stt)
        timer.daemon = True
        self._unload_timer = timer
        timer.start()

    def unload_idle_stt(self, now: float | None = None) -> list[str]:
        """Unload local speech models idle for _STT_IDLE_UNLOAD_S; returns their names."""
        now = time.monotonic() if now is None else now
        unloaded: list[str] = []
        with self._lock:
            self._unload_timer = None
            for engine, used in list(self._stt_used.items()):
                if now - used < _STT_IDLE_UNLOAD_S:
                    continue
                del self._stt_used[engine]
                if engine == "whisper":
                    self._whisper = None
                unloaded.append(engine)
            if self._stt_used:
                oldest = min(self._stt_used.values())
                self._schedule_unload(max(1.0, _STT_IDLE_UNLOAD_S - (now - oldest)))
        if "mlx_whisper" in unloaded:
            with self._mlx_lock:
                _release_mlx_whisper()
        if unloaded:
            gc.collect()
            logger.info("Unloaded idle speech-to-text models: %s", ", ".join(unloaded))
        return unloaded


voice_engine = VoiceEngine()
