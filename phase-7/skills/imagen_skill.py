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


def _generate_image_mimo(prompt: str) -> dict:
    """Mimo image generation — fallback when GEMINI_API_KEY is not set.

    Uses Mimo's OpenAI-compatible images/generations endpoint.
    Requires MIMO_API_KEY and MIMO_BASE_URL environment variables.
    """
    mimo_key = os.environ.get("MIMO_API_KEY", "")
    mimo_base = os.environ.get("MIMO_BASE_URL", "").rstrip("/")
    if not mimo_key or not mimo_base:
        return {
            "status": "unavailable",
            "error": (
                "Image generation unavailable: neither GEMINI_API_KEY nor "
                "MIMO_API_KEY+MIMO_BASE_URL are set."
            ),
        }

    try:
        import base64 as _b64
        try:
            import httpx as _httpx
        except ImportError:
            import urllib.request as _ur
            import json as _json
            req_data = _json.dumps({
                "model": os.environ.get("MIMO_IMAGE_MODEL", "mimo-2.5-pro"),
                "prompt": prompt,
                "n": 1,
                "size": "1024x1024",
                "response_format": "b64_json",
            }).encode()
            req = _ur.Request(
                f"{mimo_base}/images/generations",
                data=req_data,
                headers={
                    "Authorization": f"Bearer {mimo_key}",
                    "Content-Type": "application/json",
                },
            )
            with _ur.urlopen(req, timeout=60) as resp:
                body = _json.loads(resp.read())
            img_b64 = body["data"][0]["b64_json"]
        else:
            resp = _httpx.post(
                f"{mimo_base}/images/generations",
                headers={"Authorization": f"Bearer {mimo_key}"},
                json={
                    "model": os.environ.get("MIMO_IMAGE_MODEL", "mimo-2.5-pro"),
                    "prompt": prompt,
                    "n": 1,
                    "size": "1024x1024",
                    "response_format": "b64_json",
                },
                timeout=60,
            )
            resp.raise_for_status()
            img_b64 = resp.json()["data"][0]["b64_json"]

        img_bytes = _b64.b64decode(img_b64)

        run_id = uuid.uuid4().hex[:8]
        out_dir = ARTIFACTS_DIR / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        img_path = out_dir / "image.png"
        img_path.write_bytes(img_bytes)

        return {
            "status": "ok",
            "url": f"{_SERVER_MEDIA_BASE}/{run_id}/image.png",
            "path": str(img_path),
            "provider": "mimo",
        }

    except Exception as exc:
        return {"status": "error", "error": f"Mimo image generation failed: {str(exc)[:300]}"}


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
        # Try Mimo image generation as fallback
        return _generate_image_mimo(prompt)

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
