"""
Voice API — voice-in (STT) and voice-out (TTS) endpoints.

GET  /voice/status          → engine availability + active tiers
POST /voice/tts   {text, avatar, lang?}          → {audio_b64, format, engine, ...}
POST /voice/stt   multipart audio (+ lang form)  → {text, language, duration, engine}

Both prefer Sarvam (Bulbul v3 voices, Saaras speech-to-text) when a key is
connected and the privacy gateway rates Sarvam trusted, then local tiers
(VoxCPM → Kokoro for voice out, faster-whisper for voice in). When no STT
engine is available the frontend falls back to browser speech recognition.
"""

from __future__ import annotations

import asyncio
import base64
import os
import tempfile

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from voice_engine import voice_engine

voice_router = APIRouter()


def _record_voice(kind: str, engine: str | None, ok: bool, **counts: int) -> None:
    """Pilot metric: one voice-in or voice-out request (engine and counts, no text)."""
    try:
        from pilot_metrics import record_voice

        record_voice(kind, engine=engine, ok=ok, **counts)
    except Exception:
        pass


class VoiceTTSRequest(BaseModel):
    text:   str
    avatar: str = "narad"
    lang:   str = "en"   # "en", "hi", or another Sarvam language hint (ta, bn, ...)


@voice_router.get("/voice/status")
async def voice_status():
    return voice_engine.status()


@voice_router.post("/voice/tts")
async def voice_tts(req: VoiceTTSRequest):
    clean = req.text.strip()
    if not clean:
        raise HTTPException(status_code=400, detail="Empty text")

    # Trusted Sarvam when connected, then local tiers; blocking synth runs
    # off the event loop.
    tiers = voice_engine.tts_tiers()
    if not tiers:
        raise HTTPException(
            status_code=503,
            detail="No voice engine available. Connect a Sarvam key in "
                   "Settings → Connections (and mark Sarvam trusted), or install a local engine: "
                   "pip install 'narad-harness[voice]'.",
        )
    try:
        out = await asyncio.to_thread(
            voice_engine.synthesize, clean, req.avatar, req.lang
        )
    except RuntimeError as exc:
        _record_voice("tts", None, False, chars=len(clean))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    _record_voice("tts", out.get("engine"), True, chars=len(clean))
    return {
        "audio_b64":   base64.b64encode(out["audio"]).decode(),
        "format":      "wav",
        "engine":      out["engine"],
        "sample_rate": out["sample_rate"],
        "avatar":      req.avatar,
        "lang":        req.lang,
    }


@voice_router.post("/voice/stt")
async def voice_stt(audio: UploadFile = File(...), lang: str = Form("")):
    if not voice_engine.stt_available():
        raise HTTPException(
            status_code=503,
            detail="No speech-to-text engine — connect a trusted Sarvam key or "
                   "pip install 'narad-harness[voice]'. Frontend will use browser speech recognition.",
        )
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio upload")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio too large (25MB max)")

    suffix = os.path.splitext(audio.filename or "")[1] or ".webm"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(data)
        tmp.close()
        result = await asyncio.to_thread(voice_engine.transcribe, tmp.name, lang or None)
    except RuntimeError as exc:
        _record_voice("stt", None, False, audio_bytes=len(data))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    _record_voice("stt", result.get("engine"), True, audio_bytes=len(data))
    return result
