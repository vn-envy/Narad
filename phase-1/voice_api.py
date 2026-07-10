"""
Voice API — voice-in (STT) and voice-out (TTS) endpoints.

GET  /voice/status          → engine availability + active tiers
POST /voice/tts   {text, avatar, lang?}   → {audio_b64, format, engine, ...}
POST /voice/stt   multipart audio file    → {text, language, duration, engine}

TTS prefers Smallest.ai Waves when a key is connected, then local tiers
(VoxCPM → Kokoro) — zero API credits by default.
STT uses local faster-whisper; when unavailable the frontend falls back to
browser speech recognition.
"""

from __future__ import annotations

import asyncio
import base64
import os
import tempfile

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel
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

    # Smallest.ai cloud when connected, then local tiers; blocking synth runs
    # off the event loop.
    tiers = voice_engine.tts_tiers()
    if not tiers:
        raise HTTPException(
            status_code=503,
            detail="No voice engine available. Connect a Smallest.ai key in "
                   "Settings → Connections, or install a local engine: "
                   "pip install 'narad-harness[voice]'.",
        )
    try:
        out = await asyncio.to_thread(
            voice_engine.synthesize, clean, req.avatar, req.lang
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "audio_b64":   base64.b64encode(out["audio"]).decode(),
        "format":      "wav",
        "engine":      out["engine"],
        "sample_rate": out["sample_rate"],
        "avatar":      req.avatar,
        "lang":        req.lang,
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
