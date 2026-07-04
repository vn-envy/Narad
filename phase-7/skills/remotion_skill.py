"""
Remotion video rendering skill — Krishna's React/HTML video editor.

Remotion (https://remotion.dev) renders videos from React components. Krishna
owns all media output, so this tool lives with create_video / generate_video_clip.
Unlike create_video (Python + moviepy via the sandboxed executor), this shells out
to the Remotion Node CLI against a project scaffold in ./remotion/.

Two modes:
  1. TEMPLATE — pick a pre-built composition and pass props (safe, fast):
       render_remotion(template="Slides", props={"slides": [...], "secondsPerSlide": 3})
  2. ESCAPE HATCH — author a raw Remotion component as TSX (full control):
       render_remotion(component_tsx="export const Custom: React.FC = () => {...}")
     The TSX MUST export a component named `Custom`. It is written to
     src/generated/Custom.tsx and rendered as composition id "Custom".

Requires Node.js (>=18) and, on first render, an automatic one-time download of
the Remotion headless-Chrome shell.

Licensing note: Remotion is free for individuals, non-profits, and companies with
<=3 employees; larger for-profits need a paid Remotion Company License.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from narad_config import ARTIFACTS_DIR

_SERVER_MEDIA_BASE = os.environ.get("MEDIA_URL_BASE", "http://localhost:8000/media")

_PROJECT_DIR = Path(__file__).parent / "remotion"
_CUSTOM_TSX = _PROJECT_DIR / "src" / "generated" / "Custom.tsx"

_TEMPLATES = {
    "TitleCard": "Title + subtitle, spring scale-in. Props: title, subtitle, bg, accent.",
    "Slides": "Bulleted slides with transitions. Props: slides[{title,bullets[]}], secondsPerSlide, bg, accent.",
    "LowerThird": "Name/role banner over transparent bg (compositable). Props: name, role, accent.",
    "CodeReveal": "Types code out char-by-char. Props: code, bg, accent.",
}


def _bin() -> Path:
    """Path to the local Remotion CLI binary (may not exist yet)."""
    return _PROJECT_DIR / "node_modules" / ".bin" / "remotion"


# Key dist files that must exist for the CLI to actually run. Checking the .bin
# symlink alone is not enough: an interrupted npm install can leave the symlink
# present while a package's dist/ is only half-extracted.
_REQUIRED = (
    "node_modules/.bin/remotion",
    "node_modules/remotion/dist/cjs/version.js",
    "node_modules/@remotion/renderer/dist/index.js",
    "node_modules/@remotion/bundler/dist/index.js",
)


def _deps_ok() -> bool:
    return all((_PROJECT_DIR / p).exists() for p in _REQUIRED)


def _ensure_deps() -> tuple[bool, str]:
    if _deps_ok():
        return True, ""
    if shutil.which("npm") is None:
        return False, "npm/Node.js not found on PATH — install Node >=18 to use Remotion."
    proc = subprocess.run(
        ["npm", "install", "--no-audit", "--no-fund"],
        cwd=str(_PROJECT_DIR),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=900,
    )
    if proc.returncode != 0 or not _deps_ok():
        return False, (
            f"npm install did not complete cleanly: {proc.stderr[-300:].strip()}. "
            "Try removing phase-7/skills/remotion/node_modules and reinstalling."
        )
    return True, ""


def render_remotion(
    template: str = "",
    props: object = None,
    component_tsx: str = "",
    output_filename: str = "",
    timeout_s: int = 600,
) -> dict:
    """Render an .mp4 from a Remotion (React) composition.

    Choose ONE mode:
      TEMPLATE:  render_remotion(template="Slides", props={...})
        Valid templates: TitleCard, Slides, LowerThird, CodeReveal.
      ESCAPE HATCH:  render_remotion(component_tsx="export const Custom ...")
        Author any Remotion component. It MUST export a component named `Custom`.
        Import Remotion primitives from 'remotion' (AbsoluteFill, useCurrentFrame,
        interpolate, spring, Sequence, useVideoConfig, Img, Audio, Video).

    props (dict or JSON string) is passed to the composition. Optional universal
    props: durationSeconds, durationInFrames, fps, width, height — these resize/
    retime the composition without editing the template.

    Returns {status, url, file_path, message} on success (url is the /media/… link),
    or {status, message} on failure.
    """
    # Normalise props → JSON string
    if props is None:
        props_obj: dict = {}
    elif isinstance(props, str):
        try:
            props_obj = json.loads(props) if props.strip() else {}
        except json.JSONDecodeError as exc:
            return {"status": "error", "url": "", "message": f"props is not valid JSON: {exc}"}
    elif isinstance(props, dict):
        props_obj = props
    else:
        return {"status": "error", "url": "", "message": "props must be a dict or JSON string."}

    # Resolve composition + write the escape-hatch component if given
    if component_tsx and component_tsx.strip():
        if "Custom" not in component_tsx:
            return {
                "status": "error",
                "url": "",
                "message": "component_tsx must export a component named `Custom` "
                "(e.g. `export const Custom: React.FC = () => {...}`).",
            }
        try:
            _CUSTOM_TSX.parent.mkdir(parents=True, exist_ok=True)
            _CUSTOM_TSX.write_text(component_tsx, encoding="utf-8")
        except OSError as exc:
            return {"status": "error", "url": "", "message": f"Could not write Custom.tsx: {exc}"}
        composition = "Custom"
    elif template in _TEMPLATES:
        composition = template
    else:
        return {
            "status": "error",
            "url": "",
            "message": (
                "Provide either template=<one of "
                f"{', '.join(sorted(_TEMPLATES))}> with props, or component_tsx=<TSX exporting Custom>."
            ),
        }

    ok, err = _ensure_deps()
    if not ok:
        return {"status": "unavailable", "url": "", "message": err}

    # Output under the media dir so the server serves it at /media/<run_id>/video.mp4
    run_id = uuid.uuid4().hex[:8]
    out_dir = ARTIFACTS_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = output_filename or "video.mp4"
    if not fname.endswith(".mp4"):
        fname += ".mp4"
    out_path = out_dir / fname

    cmd = [
        str(_bin()),
        "render",
        "src/index.ts",
        composition,
        str(out_path),
        f"--props={json.dumps(props_obj)}",
        "--log=error",
    ]

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(_PROJECT_DIR),
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "url": "",
            "message": f"Remotion render timed out after {timeout_s}s. "
            "Reduce duration/complexity or raise timeout_s.",
        }

    if proc.returncode == 0 and out_path.exists():
        return {
            "status": "ok",
            "url": f"{_SERVER_MEDIA_BASE}/{run_id}/{fname}",
            "file_path": str(out_path),
            "message": f"Rendered composition '{composition}' with Remotion.",
        }

    stderr_snippet = (proc.stderr or proc.stdout)[-400:].strip()
    return {
        "status": "error",
        "url": "",
        "message": (
            f"Remotion render failed (exit {proc.returncode}). Error: {stderr_snippet}. "
            "Fix the props/component and retry once; if it fails again, explain the limitation."
        ),
    }
