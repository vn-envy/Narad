"""
HyperFrames video rendering skill — HTML-to-MP4 fallback for Krishna.

Krishna's video generation priority cascade:
  1. generate_video_clip()         — Veo 3 AI video (preferred, GEMINI_API_KEY required)
  2. create_video_hyperframes()    — THIS FILE: HTML→MP4 via HyperFrames CLI
  3. create_video()                — moviepy programmatic (always available, last resort)

HyperFrames renders animated HTML/CSS pages to MP4 using a headless browser.
Installation: npm install -g @heygen/hyperframes  (one-time, requires Node.js 18+)
Docs: https://github.com/heygen-com/hyperframes
"""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

from narad_config import ARTIFACTS_DIR
from tool_result import artifact, envelope, ui_panel, write_html_surface, write_json

_SERVER_MEDIA_BASE = os.environ.get("MEDIA_URL_BASE", "http://localhost:8000/media")
TEMPLATE_DIR = Path(__file__).parent / "templates"


def list_animation_templates() -> dict:
    """List all available pre-built animation HTML templates.

    Templates are self-contained HTML files with CSS/JS animations ready to render
    via create_video_hyperframes(). Pass the html_code of a chosen template directly.

    Returns:
        status:    "ok" | "error"
        templates: List of {name, file, description, slots} dicts.
        message:   Human-readable summary.
    """
    if not TEMPLATE_DIR.exists():
        return {
            "status":    "error",
            "templates": [],
            "message":   f"Template directory not found: {TEMPLATE_DIR}",
        }

    template_meta = {
        "fade_in_title":       "Animated title reveal with fade-in + subtitle slide-up + particle field.",
        "slide_deck_transition": "Full-screen slides with horizontal CSS transition navigation (arrow/swipe).",
        "chart_reveal":        "Animated bar chart with line trend overlay — SVG path draw animation.",
        "text_typewriter":     "Terminal-style typewriter reveal with cursor blink and staggered blocks.",
        "kinetic_list":        "GSAP staggered list item entrance with progress bar and replay button.",
    }

    templates = []
    for html_file in sorted(TEMPLATE_DIR.glob("*.html")):
        name = html_file.stem
        content = html_file.read_text(encoding="utf-8")
        # Extract {{SLOT|default}} slot names for documentation
        import re
        slots = list(dict.fromkeys(re.findall(r'\{\{([A-Z0-9_]+)\|?[^}]*\}\}', content)))
        templates.append({
            "name":        name,
            "file":        str(html_file),
            "description": template_meta.get(name, "Animation template."),
            "slots":       slots,
        })

    return {
        "status":    "ok",
        "templates": templates,
        "message":   (
            f"{len(templates)} animation template(s) available. "
            "Read the html file, fill {{SLOT}} values, then pass to create_video_hyperframes()."
        ),
    }


def _check_hyperframes() -> str | None:
    """Return path to hyperframes binary, or None if not installed."""
    path = shutil.which("hyperframes")
    if path:
        return path
    # Also check common npm global bin locations
    for candidate in [
        Path.home() / ".npm-global" / "bin" / "hyperframes",
        Path("/usr/local/bin/hyperframes"),
        Path("/opt/homebrew/bin/hyperframes"),
    ]:
        if candidate.exists():
            return str(candidate)
    return None


def create_video_hyperframes(
    html_code: str,
    duration_seconds: int = 30,
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
    output_filename: str = "",
) -> dict:
    """Render an animated HTML page to MP4 using the HyperFrames CLI.

    Use as the second fallback in Krishna's video cascade (after Veo, before moviepy).
    Requires Node.js 18+ and: npm install -g @heygen/hyperframes

    html_code: Self-contained HTML with CSS animations (keyframes, transitions, etc.).
               Each scene should animate within the specified duration.
               Include all styles inline — no external dependencies except CDN scripts.

    duration_seconds: Total video duration in seconds (default 30).
    width: Output width in pixels (default 1280 for 16:9).
    height: Output height in pixels (default 720 for 16:9).
    fps: Frames per second (default 30).

    Returns:
      {status: "ok", url: ..., path: ..., method: "hyperframes"} on success
      {status: "unavailable", error: "..."} if HyperFrames not installed → fall back to create_video()
      {status: "error", error: "..."} on render failure → fall back to create_video()
    """
    hf_bin = _check_hyperframes()
    if not hf_bin:
        # Surface specific missing dependency so user can act
        node_ok = shutil.which("node") is not None
        npm_ok  = shutil.which("npm") is not None
        hint = (
            "Install Node.js 18+ first: https://nodejs.org/" if not node_ok else
            "Run: npm install -g @heygen/hyperframes" if not npm_ok else
            "Run: npm install -g @heygen/hyperframes"
        )
        return envelope(
            status="unavailable",
            summary="HyperFrames is not installed, so Krishna cannot use the deterministic HTML-to-video renderer yet.",
            error=(
                f"HyperFrames CLI not installed. {hint} Falling back to create_video() (moviepy)."
            ),
            ui=ui_panel(
                title="HyperFrames unavailable",
                summary="Krishna can still fall back to moviepy or Veo, but the deterministic HTML-to-video path is unavailable.",
                sections=[{"title": "Install", "body": hint}],
            ),
            provenance={"tool": "create_video_hyperframes", "method": "hyperframes"},
            node_available=node_ok,
            npm_available=npm_ok,
        )

    run_id = uuid.uuid4().hex[:8]
    out_dir = ARTIFACTS_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    html_path = out_dir / "scene.html"
    video_name = output_filename.strip() or "video.mp4"
    if not video_name.lower().endswith(".mp4"):
        video_name = f"{video_name}.mp4"
    video_path = out_dir / video_name

    html_path.write_text(html_code, encoding="utf-8")

    cmd = [
        hf_bin,
        "render",
        "--input", str(html_path),
        "--output", str(video_path),
        "--duration", str(duration_seconds),
        "--width", str(width),
        "--height", str(height),
        "--fps", str(fps),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute max for rendering
        )

        if result.returncode != 0:
            stderr_preview = result.stderr[:400] if result.stderr else "(no stderr)"
            return envelope(
                status="error",
                summary="HyperFrames ran but the render step failed.",
                error=(
                    f"HyperFrames render failed (exit {result.returncode}): {stderr_preview}. "
                    "Falling back to create_video() (moviepy)."
                ),
                provenance={"tool": "create_video_hyperframes", "method": "hyperframes"},
            )

        if not video_path.exists() or video_path.stat().st_size == 0:
            return envelope(
                status="error",
                summary="HyperFrames finished without producing an MP4 output.",
                error="HyperFrames ran but produced no output file. Falling back to create_video().",
                provenance={"tool": "create_video_hyperframes", "method": "hyperframes"},
            )

        artifacts = [
            artifact(
                type="video",
                label="Rendered MP4",
                path=video_path,
                mime_type="video/mp4",
                description="Deterministic HyperFrames render from the supplied HTML composition.",
            ),
            artifact(
                type="html",
                label="Source composition",
                path=html_path,
                mime_type="text/html",
                description="The exact HTML composition rendered to video.",
            ),
        ]
        html_surface = write_html_surface(
            out_dir=out_dir,
            title="HyperFrames render report",
            summary="Krishna rendered the HTML composition to a deterministic MP4 using HyperFrames.",
            sections=[
                {"title": "Render settings", "body": f"{width}×{height} at {fps} fps for {duration_seconds}s."},
                {"title": "Method", "body": "HyperFrames rendered the composition in headless Chrome and encoded the result to MP4."},
            ],
            artifacts=artifacts,
        )
        artifacts.append(html_surface)
        write_json(
            out_dir / "tool_result.json",
            {
                "tool": "create_video_hyperframes",
                "method": "hyperframes",
                "duration_seconds": duration_seconds,
                "width": width,
                "height": height,
                "fps": fps,
                "video_path": str(video_path),
            },
        )
        return envelope(
            status="ok",
            summary="HyperFrames rendered the requested HTML composition to MP4.",
            artifacts=artifacts,
            ui=ui_panel(
                title="HyperFrames render report",
                summary="Rendered an HTML-native video artifact through HyperFrames.",
                sections=[{"title": "Output", "body": f"The MP4 is available at {_SERVER_MEDIA_BASE}/{run_id}/{video_name}."}],
                primary_artifact_label="Rendered MP4",
            ),
            provenance={"tool": "create_video_hyperframes", "method": "hyperframes", "run_id": run_id},
            url=f"{_SERVER_MEDIA_BASE}/{run_id}/{video_name}",
            path=str(video_path),
            method="hyperframes",
            duration_seconds=duration_seconds,
        )

    except subprocess.TimeoutExpired:
        return envelope(
            status="error",
            summary="HyperFrames timed out during video rendering.",
            error="HyperFrames render timed out after 5 minutes. Falling back to create_video().",
            provenance={"tool": "create_video_hyperframes", "method": "hyperframes"},
        )
    except Exception as exc:
        return envelope(
            status="error",
            summary="HyperFrames raised an unexpected error.",
            error=f"HyperFrames error: {str(exc)[:300]}. Falling back to create_video().",
            provenance={"tool": "create_video_hyperframes", "method": "hyperframes"},
        )
