"""
Veo 3.1 video generation skill — AI-generated video clips for Krishna.

Krishna calls generate_video_clip() during the video BUILD phase.
Each call produces one AI video clip (up to 8 seconds) via Veo 3.1.
For multi-scene videos, call once per scene then use create_video() to stitch.

Requires GEMINI_API_KEY env var.
Model override: VEO_MODEL env var (default: veo-3.1-generate-preview).
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from narad_config import ARTIFACTS_DIR

_SERVER_MEDIA_BASE = os.environ.get("MEDIA_URL_BASE", "http://localhost:8000/media")
_VEO_MODEL = os.environ.get("VEO_MODEL", "veo-3.1-generate-preview")
_POLL_INTERVAL_S = 20
_MAX_POLLS = 30  # 10 minutes max


def generate_video_clip(
    prompt: str,
    duration_seconds: int = 5,
    aspect_ratio: str = "16:9",
    with_audio: bool = True,
) -> dict:
    """Generate an AI video clip from a text description using Veo 3.

    Use during video BUILD phase — call once per scene from the confirmed script.
    For multi-scene videos: call generate_video_clip for each scene to get clip paths,
    then call create_video(stitch_code) to concatenate them using MoviePy.

    prompt: Describe the scene visually — motion, setting, lighting, style.
      Good: "A teacher writing equations on a glowing chalkboard, cinematic, soft lighting"
      Bad:  "Explain machine learning" — describe what to SHOW, not what to say.

    duration_seconds: 1–8 seconds per clip (default 5).
    aspect_ratio: "16:9" (landscape, default) or "9:16" (portrait/social).
    with_audio: True generates ambient/cinematic audio alongside the video (default True).

    Returns: {status, url, path, duration_seconds} on success
             {status, error} on failure — fall back to create_video() programmatic rendering.
    """
    # Load .env if GEMINI_API_KEY not already in environment
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        try:
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).parent.parent.parent / ".env", override=False)
            api_key = os.environ.get("GEMINI_API_KEY", "")
        except ImportError:
            pass
    if not api_key:
        return {"status": "unavailable", "error": "GEMINI_API_KEY not set — Veo unavailable. Use create_video() instead."}

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return {"status": "unavailable", "error": "google-genai not installed. Run: pip install google-genai"}

    duration = max(1, min(8, duration_seconds))

    try:
        client = genai.Client(api_key=api_key)
        operation = client.models.generate_videos(
            model=_VEO_MODEL,
            prompt=prompt,
            config=types.GenerateVideosConfig(
                number_of_videos=1,
                duration_seconds=duration,
                aspect_ratio=aspect_ratio,
                enhance_prompt=True,
                generate_audio=with_audio,
            ),
        )

        for _ in range(_MAX_POLLS):
            if operation.done:
                break
            time.sleep(_POLL_INTERVAL_S)
            operation = client.operations.get(operation)

        if not operation.done:
            return {"status": "error", "error": "Veo timed out after 10 minutes. Use create_video() as fallback."}

        videos = operation.response.generated_videos
        if not videos:
            return {"status": "error", "error": "Veo returned no video. Use create_video() as fallback."}

        # Extract bytes — SDK returns video_bytes on the nested Video object
        video_obj = videos[0].video
        video_bytes = getattr(video_obj, "video_bytes", None) or getattr(video_obj, "videoBytes", None)
        if not video_bytes:
            # Some SDK versions return the video object itself as bytes-like
            try:
                video_bytes = bytes(video_obj)
            except Exception:
                return {"status": "error", "error": "Could not extract video bytes from Veo response. Use create_video() as fallback."}

        run_id = uuid.uuid4().hex[:8]
        out_dir = ARTIFACTS_DIR / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        clip_path = out_dir / "clip.mp4"
        clip_path.write_bytes(video_bytes)

        return {
            "status":           "ok",
            "url":              f"{_SERVER_MEDIA_BASE}/{run_id}/clip.mp4",
            "path":             str(clip_path),
            "duration_seconds": duration,
            "model":            _VEO_MODEL,
            "audio":            with_audio,
        }
    except Exception as exc:
        return {"status": "error", "error": f"Veo failed: {str(exc)[:300]}. Use create_video() as fallback."}
