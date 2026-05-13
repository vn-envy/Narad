"""
Parashurama video generation skill — code-only, no AI diffusion models.

Parashurama writes Python code using moviepy + Pillow.
The executor runs it in a sandbox and returns the .mp4 path.

Supported styles:
  slides          — text slides with fade transitions (title + bullet points)
  code_walkthrough — syntax-highlighted code that types out line by line
  chart           — animated matplotlib bar or line chart

Limitations (Parashurama communicates these to the user):
  - No photorealistic frames — programmatic text, shapes, charts only
  - Rendering is fast but output is "designed", not "filmed"
  - For AI-generated imagery a diffusion model would be needed
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

_PHASE7 = Path(__file__).parent.parent
sys.path.insert(0, str(_PHASE7))
from executor import execute_code

_SERVER_MEDIA_BASE = os.environ.get("MEDIA_URL_BASE", "http://localhost:8000/media")

# ── Style prompt templates (shown in docstring so the LLM knows what's possible) ──

_STYLE_HINTS = """
Available styles and what code they expect:

SLIDES — title + bullet-point slides with crossfade
  Required variables in your code:
    slides = [{"title": "...", "bullets": ["...", "..."]}, ...]
    duration_per_slide = 3   # seconds
    output_path = os.path.join(OUTPUT_DIR, "video.mp4")
  Use moviepy ImageClip from PIL-rendered frames. Pillow draws text.

CODE_WALKTHROUGH — code that appears character by character
  Required variables:
    code_text = "..."        # the code to animate
    output_path = os.path.join(OUTPUT_DIR, "video.mp4")
  Render each frame by revealing one more character of code_text.

CHART — animated matplotlib chart (bar or line)
  Required variables:
    data = {"labels": [...], "values": [...], "title": "..."}
    chart_type = "bar" | "line"
    output_path = os.path.join(OUTPUT_DIR, "video.mp4")
  Animate the chart growing from 0 to final values over ~3 seconds.

GENERATIVE_ART — numpy/PIL pixel-level procedural art
  Examples: Mandelbrot zoom, Julia set, Perlin noise terrain, L-system growth
  Render N frames by varying a parameter (zoom, seed, iteration depth).
  Each frame: compute numpy array → convert to PIL Image → np.array for moviepy.
  Cap at 60–120 frames (fps=24) for reasonable render time.

MATPLOTLIB_ANIMATION — matplotlib FuncAnimation → mp4
  For animated line traces, scatter evolutions, waveforms, bar races.
  Use: matplotlib.animation.FuncAnimation + ffmpeg writer (writer='ffmpeg').
  Save with: anim.save(path, fps=24, dpi=100, writer='ffmpeg',
                       extra_args=['-vcodec','libx264'])
  Always set fig facecolor for a clean background; use dark palette for drama.

PARTICLE — numpy physics simulation rendered frame-by-frame
  Simulate N particles with velocity + simple force (gravity, orbits, repulsion).
  Render each frame as a black PIL image with PIL.ImageDraw circles.
  50–200 particles at fps=24 over 5–10s produces smooth results.

All code MUST:
  - Use moviepy v2.x: `from moviepy import ImageClip, concatenate_videoclips`
    NOT `from moviepy.editor import ...` (v1 API, unavailable)
  - Write output to: os.path.join(OUTPUT_DIR, "video.mp4")
  - Use fps=24 and codec='libx264' in write_videofile(logger=None)
  - Work without any network access or external files
"""


def create_video(
    code: str,
    style: str = "slides",
    output_filename: str = "",
) -> dict:
    """Generate a video by executing Python code using moviepy and Pillow.

    Parashurama must pass complete, working Python code that:
    - Uses moviepy (v2.x: `from moviepy import ImageClip, ...`) and Pillow to create video frames
    - Writes the final .mp4 to: os.path.join(OUTPUT_DIR, "video.mp4")
    - Does NOT import subprocess, requests, socket, or any network library
    - Does NOT use absolute file paths outside OUTPUT_DIR

    Style guide:
      slides          — text slides with fade transitions
      code_walkthrough — animated code reveal
      chart           — animated matplotlib chart

    All output files must be written to the OUTPUT_DIR variable (pre-set by executor).

    Returns a dict with status, url (playable in the frontend), and any error details.

    IMPORTANT: This tool produces programmatic video — text, shapes, and charts only.
    Always inform the user: "This is a code-rendered video (no photorealistic imagery)."
    """
    if not code or not code.strip():
        return {
            "status":  "error",
            "message": "code parameter is required — pass complete Python code to execute",
            "url":     "",
        }

    fname = output_filename or f"video_{uuid.uuid4().hex[:8]}.mp4"
    if not fname.endswith(".mp4"):
        fname += ".mp4"

    result = execute_code(code)

    mp4_files = [f for f in result.get("output_files", []) if f.endswith(".mp4")]

    if result["status"] == "ok" and mp4_files:
        out_path = mp4_files[0]
        rel = Path(out_path).name
        run_id = result["run_id"]
        url = f"{_SERVER_MEDIA_BASE}/{run_id}/{rel}"
        return {
            "status":     "ok",
            "url":        url,
            "file_path":  out_path,
            "duration_s": 0,   # moviepy doesn't report back duration easily
            "message":    f"Video rendered in {result['duration_s']}s. Programmatic rendering — text/shapes/charts only.",
        }

    # Keep the error terse — large stderr blocks inflate context and can cause
    # the LLM to produce malformed follow-up tool calls on the retry.
    stderr_snippet = result['stderr'][:300].strip()
    return {
        "status":  result["status"],
        "url":     "",
        "message": (
            f"Execution failed ({result['status']}). "
            f"Error: {stderr_snippet}. "
            "Fix the code and try again (one retry only — if it fails again, "
            "explain the limitation to the user instead of retrying further)."
        ),
    }
