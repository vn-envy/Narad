"""
TTS API — Sarvam AI text-to-speech for voice-enabled avatars.

POST /tts  {text, avatar, lang?}  →  {audio_b64, format: "wav"}

lang="en" (default) — English TTS
lang="hi"           — translate to Hindi first, then Hindi TTS

Voice mapping (Sarvam bulbul:v2):
  Krishna → abhilash
  Rama    → hitesh
  Parashurama → karun
"""

from __future__ import annotations

import base64
import os
import re

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

tts_router = APIRouter()

SARVAM_TTS_URL       = "https://api.sarvam.ai/text-to-speech"
SARVAM_TRANSLATE_URL = "https://api.sarvam.ai/translate"

AVATAR_VOICES: dict[str, str] = {
    "krishna": "abhilash",
    "rama":    "hitesh",
    "parashurama": "karun",
}


class TTSRequest(BaseModel):
    text:   str
    avatar: str
    lang:   str = "en"   # "en" | "hi"


def _truncate_hindi(text: str, limit: int = 490) -> str:
    """Truncate Hindi text at a sentence boundary (।) within limit chars."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # Prefer breaking at Hindi danda (।) or a space
    for sep in ('।', ' '):
        idx = cut.rfind(sep)
        if idx > limit // 2:
            return cut[:idx + (1 if sep == '।' else 0)].strip()
    return cut.strip()


async def _translate_to_hindi(text: str, api_key: str, client: httpx.AsyncClient) -> str:
    # Translate at most 400 chars of English to avoid Hindi expansion exceeding 500
    payload = {
        "input":                text[:400],
        "source_language_code": "en-IN",
        "target_language_code": "hi-IN",
        "speaker_gender":       "Male",
        "mode":                 "formal",
        "model":                "mayura:v1",
        "enable_preprocessing": False,
    }
    headers = {"api-subscription-key": api_key, "Content-Type": "application/json"}
    resp = await client.post(SARVAM_TRANSLATE_URL, json=payload, headers=headers)
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code,
                            detail=f"Sarvam translate error: {resp.text[:300]}")
    hindi = resp.json().get("translated_text", text)
    return _truncate_hindi(hindi)


async def _tts_call(
    text: str,
    speaker: str,
    lang_code: str,
    api_key: str,
    client: httpx.AsyncClient,
) -> bytes:
    payload = {
        "inputs":               [text],
        "target_language_code": lang_code,
        "speaker":              speaker,
        "pitch":                0,
        "pace":                 1.0,
        "loudness":             1.5,
        "speech_sample_rate":   22050,
        "enable_preprocessing": True,
        "model":                "bulbul:v2",
    }
    headers = {"api-subscription-key": api_key, "Content-Type": "application/json"}
    resp = await client.post(SARVAM_TTS_URL, json=payload, headers=headers)
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code,
                            detail=f"Sarvam TTS error: {resp.text[:300]}")
    audios = resp.json().get("audios", [])
    if not audios:
        raise HTTPException(status_code=502, detail="Sarvam returned no audio")
    return base64.b64decode(audios[0])


@tts_router.post("/tts")
async def synthesise(req: TTSRequest):
    api_key = os.environ.get("SARVAM_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="SARVAM_API_KEY not configured")

    speaker = AVATAR_VOICES.get(req.avatar.lower())
    if not speaker:
        raise HTTPException(
            status_code=400,
            detail=f"Avatar '{req.avatar}' has no voice. Supported: {list(AVATAR_VOICES)}",
        )

    # Text arrives pre-cleaned from the frontend; backend does a light final pass
    clean = req.text.strip()
    if not clean:
        raise HTTPException(status_code=400, detail="Empty text")

    async with httpx.AsyncClient(timeout=30) as client:
        if req.lang == "hi":
            hindi = await _translate_to_hindi(clean, api_key, client)
            audio = await _tts_call(hindi, speaker, "hi-IN", api_key, client)
            return {
                "audio_b64":   base64.b64encode(audio).decode(),
                "format":      "wav",
                "speaker":     speaker,
                "avatar":      req.avatar,
                "lang":        "hi",
                "translated":  hindi,
            }
        else:
            audio = await _tts_call(clean, speaker, "en-IN", api_key, client)
            return {
                "audio_b64": base64.b64encode(audio).decode(),
                "format":    "wav",
                "speaker":   speaker,
                "avatar":    req.avatar,
                "lang":      "en",
            }
