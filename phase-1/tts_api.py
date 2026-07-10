"""
Removed — Sarvam TTS was retired in favour of Smallest.ai Waves + local engines.

Voice output now lives entirely in voice_engine.py / voice_api.py
(POST /voice/tts): Smallest.ai when a key is connected via Kunji, then
VoxCPM (GPU/MPS), then Kokoro (CPU). This stub exists only so stale
imports fail loudly with a helpful message; delete it freely.
"""

raise ImportError(
    "tts_api (Sarvam) was removed — use voice_api's POST /voice/tts "
    "(Smallest.ai → VoxCPM → Kokoro) instead"
)
