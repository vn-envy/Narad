"""
Voice API — voice-in (STT) and voice-out (TTS) endpoints.

GET  /voice/status          → engine availability + active tiers
POST /voice/tts   {text, avatar, lang?}   → {audio_b64, format, engine, ...}
POST /voice/stt   multipart audio file    → {text, language, duration, engine}

TTS resolves local tiers first (VoxCPM → Kokoro) and only falls back to the
Sarvam cloud API when a key is configured — zero API credits by default.
STT uses local faster-whisper; when unavailable the frontend falls back to
browser speech recognition.
"""

from __future__ import annotations

import asyncio
import base64
import os
import tempfile

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel
from tts_api import AVATAR_VOICES, _translate_to_hindi, _tts_call
from voice_engine import voice_engine

voice_router = APIRouter()


class VoiceTTSRequest(BaseModel):
    text:   str
    avatar: str = "narad"
    lang:   str = "en"   # "en" | "hi"


@voice_router.get("/voice/status")
async def voice_status():
    return voice_engine.status()


@voice_router.post("/voice/tts")
async def voice_tts(req: VoiceTTSRequest):
    clean = req.text.strip()
    if not clean:
        raise HTTPException(status_code=400, detail="Empty text")

    # Local tiers first (run blocking synth off the event loop).
    tiers = voice_engine.tts_tiers()
    if any(t in ("voxcpm", "kokoro") for t in tiers):
        try:
            out = await asyncio.to_thread(
                voice_engine.synthesize, clean, req.avatar, req.lang
            )
            return {
                "audio_b64":   base64.b64encode(out["audio"]).decode(),
                "format":      "wav",
                "engine":      out["engine"],
                "sample_rate": out["sample_rate"],
                "avatar":      req.avatar,
                "lang":        req.lang,
            }
        except RuntimeError:
            pass  # fall through to cloud

    # Cloud fallback (Sarvam) — only when a key is configured.
    api_key = os.environ.get("SARVAM_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="No voice engine available. Install one: "
                   "pip install 'narad-harness[voice]' (or set SARVAM_API_KEY).",
        )
    speaker = AVATAR_VOICES.get(req.avatar.lower(), "abhilash")
    async with httpx.AsyncClient(timeout=30) as client:
        if req.lang == "hi":
            hindi = await _translate_to_hindi(clean, api_key, client)
            audio = await _tts_call(hindi, speaker, "hi-IN", api_key, client)
        else:
            audio = await _tts_call(clean[:490], speaker, "en-IN", api_key, client)
    return {
        "audio_b64": base64.b64encode(audio).decode(),
        "format":    "wav",
        "engine":    "sarvam",
        "avatar":    req.avatar,
        "lang":      req.lang,
    }


@voice_router.post("/voice/stt")
async def voice_stt(audio: UploadFile = File(...)):
    if not voice_engine.stt_available():
        raise HTTPException(
            status_code=503,
            detail="Local STT not installed — pip install 'narad-harness[voice]'. "
                   "Frontend will use browser speech recognition.",
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
        result = await asyncio.to_thread(voice_engine.transcribe, tmp.name)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return {**result, "engine": "faster-whisper"}
