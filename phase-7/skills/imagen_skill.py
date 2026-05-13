"""
Imagen 4 Fast image generation skill — AI-generated images for Krishna.

Krishna calls generate_image() during slide BUILD (hero images, section visuals)
and video BUILD (static scene frames before compositing).

Requires GEMINI_API_KEY env var.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from narad_config import ARTIFACTS_DIR

_SERVER_MEDIA_BASE = os.environ.get("MEDIA_URL_BASE", "http://localhost:8000/media")


def generate_image(prompt: str) -> dict:
    """Generate an AI image from a text description using Imagen 4 Fast.

    Use during:
    - Slide BUILD: hero images, section backgrounds, diagram illustrations.
      The returned url is directly embeddable in HTML as <img src="...">.
    - Video BUILD: static scene frames — pass the path to create_video() code
      as a PIL-loadable image for use as an ImageClip background.

    prompt: Describe precisely — style, subject, composition, colour palette.
      Good: "Minimalist binary search tree diagram, flat design, blue and white, clean"
      Good: "Mountain landscape at dusk, watercolour style, muted tones, wide shot"

    Returns: {status, url, path} on success
             {status, error} on failure.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return {"status": "unavailable", "error": "GEMINI_API_KEY not set — Imagen unavailable"}

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return {"status": "unavailable", "error": "google-genai not installed. Run: pip install google-genai"}

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_images(
            model="imagen-4.0-fast-generate-001",
            prompt=prompt,
            config=types.GenerateImagesConfig(number_of_images=1),
        )

        if not response.generated_images:
            return {"status": "error", "error": "Imagen returned no images"}

        img = response.generated_images[0].image

        run_id = uuid.uuid4().hex[:8]
        out_dir = ARTIFACTS_DIR / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        img_path = out_dir / "image.png"
        img.save(str(img_path))

        return {
            "status": "ok",
            "url":    f"{_SERVER_MEDIA_BASE}/{run_id}/image.png",
            "path":   str(img_path),
        }
    except Exception as exc:
        return {"status": "error", "error": f"Imagen failed: {str(exc)[:300]}"}
