"""
Voice API — voice-in (STT) and voice-out (TTS) endpoints.

GET  /voice/status          → engine availability + active tiers (for the caller's profile)
POST /voice/tts   {text, avatar, lang?}          → {audio_b64, format, engine, ...}
                  with "Accept: audio/wav"        → the WAV itself (what the PWA uses)
POST /voice/stt   multipart audio (+ lang form)  → {text, language, duration, engine}

Both prefer Sarvam (Bulbul v3 voices, Saaras speech-to-text) when a key is
connected, the privacy gateway rates Sarvam trusted, and the caller's profile
hasn't asked to keep its voice on the Mac; then local tiers (VoxCPM → Kokoro
for voice out, mlx-whisper → faster-whisper for voice in). When no STT engine
is available the frontend falls back to browser speech recognition.

TTS takes one spoken segment (a sentence or two) per request; the PWA splits
a streaming reply and asks for each segment as soon as it is complete. Both
endpoints return a Server-Timing header so time to first audio can be measured
on the phone. GET/PUT /voice/preferences live in server.py next to the
identity helpers.
"""

from __future__ import annotations

import asyncio
import base64
import os
import tempfile
import time
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from voice_engine import MAX_TTS_CHARS, voice_engine

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


def _server_timing(name: str, engine: str, work_ms: float, total_ms: float, cache: str | None = None) -> str:
    parts = [f'{name};dur={work_ms:.1f};desc="{engine}"']
    if cache:
        parts.append(f'cache;desc="{cache}"')
    parts.append(f"total;dur={total_ms:.1f}")
    return ", ".join(parts)


@voice_router.get("/voice/status")
async def voice_status():
    return await asyncio.to_thread(voice_engine.status)


@voice_router.post("/voice/tts")
async def voice_tts(req: VoiceTTSRequest, request: Request):
    started = time.perf_counter()
    clean = req.text.strip()
    if not clean:
        raise HTTPException(status_code=400, detail="Empty text")
    if len(clean) > MAX_TTS_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Too long for one spoken segment ({MAX_TTS_CHARS} characters max): send it sentence by sentence.",
        )

    # Trusted Sarvam when connected and allowed for this profile, then local
    # tiers; blocking synth runs off the event loop.
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
    cache = "hit" if out.get("cached") else "miss"
    headers = {
        "Server-Timing": _server_timing(
            "tts", out["engine"], out.get("synth_ms", 0.0), (time.perf_counter() - started) * 1000, cache
        ),
        "X-Narad-TTS-Engine": out["engine"],
        "X-Narad-TTS-Cache": cache,
        "Cache-Control": "no-store",
    }
    if "audio/" in request.headers.get("accept", ""):
        return Response(content=out["audio"], media_type="audio/wav", headers=headers)
    return JSONResponse({
        "audio_b64":   base64.b64encode(out["audio"]).decode(),
        "format":      "wav",
        "engine":      out["engine"],
        "sample_rate": out["sample_rate"],
        "avatar":      req.avatar,
        "lang":        req.lang,
        "cached":      bool(out.get("cached")),
    }, headers=headers)


@voice_router.post("/voice/stt")
async def voice_stt(response: Response, audio: UploadFile = File(...), lang: str = Form("")):
    started = time.perf_counter()
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
        work_started = time.perf_counter()
        result: dict[str, Any] = await asyncio.to_thread(voice_engine.transcribe, tmp.name, lang or None)
        work_ms = (time.perf_counter() - work_started) * 1000
    except RuntimeError as exc:
        _record_voice("stt", None, False, audio_bytes=len(data))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    _record_voice("stt", result.get("engine"), True, audio_bytes=len(data))
    response.headers["Server-Timing"] = _server_timing(
        "stt", str(result.get("engine") or ""), work_ms, (time.perf_counter() - started) * 1000
    )
    return result
