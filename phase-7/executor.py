"""
Phase 7 — Sandboxed Code Executor

Runs Parashurama-generated Python code in a subprocess with:
  - Timeout enforcement
  - Dangerous-import blocklist
  - Isolated output directory
  - Structured result dict

All generated files land in phase-7/outputs/<run_id>/
The caller is responsible for moving or serving them.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from narad_config import ARTIFACTS_DIR as _OUTPUTS_DIR

TIMEOUT_S = int(os.environ.get("EXECUTOR_TIMEOUT", "90"))

# Patterns that would let executed code escape the sandbox
_BLOCKLIST = [
    "os.system(",
    "os.popen(",
    "os.remove(",
    "os.unlink(",
    "os.rmdir(",
    "shutil.rmtree(",
    "shutil.move(",
    "__import__('subprocess')",
    "__import__(\"subprocess\")",
    "import subprocess",
    "import socket",
    "import urllib",
    "import requests",
    "import httpx",
    "import aiohttp",
    "open('/",   # absolute path writes outside output dir
    'open("/',
]


def _check_safety(code: str) -> str | None:
    """Return an error string if the code contains blocked patterns, else None."""
    for pattern in _BLOCKLIST:
        if pattern in code:
            return f"Blocked pattern detected: '{pattern}'"
    return None


def execute_code(
    code: str,
    output_dir: Path | None = None,
    timeout_s: int = TIMEOUT_S,
) -> dict:
    """
    Run `code` in an isolated subprocess.

    The code receives `OUTPUT_DIR` as an environment variable pointing to the
    dedicated output directory for this run. All generated files must be written
    there. The caller reads `output_files` from the result dict to find them.

    Returns:
        {
            "status":       "ok" | "error" | "timeout" | "blocked",
            "stdout":       str,
            "stderr":       str,
            "output_files": list[str],   # absolute paths to generated files
            "duration_s":   float,
            "run_id":       str,
        }
    """
    run_id = str(uuid.uuid4())[:8]
    run_dir = _OUTPUTS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if output_dir is None:
        output_dir = run_dir

    safety_err = _check_safety(code)
    if safety_err:
        return {
            "status":       "blocked",
            "stdout":       "",
            "stderr":       safety_err,
            "output_files": [],
            "duration_s":   0.0,
            "run_id":       run_id,
        }

    # Wrap the user code so OUTPUT_DIR is available as a variable.
    # IMPORTANT: do NOT use textwrap.indent on user code — adding indentation to
    # module-level code causes IndentationError in the subprocess.
    wrapper = (
        "import os, sys\n"
        f"OUTPUT_DIR = {str(output_dir)!r}\n"
        "os.makedirs(OUTPUT_DIR, exist_ok=True)\n"
        "os.chdir(OUTPUT_DIR)\n"
        "\n"
        "# ── USER CODE ────────────────────────────────────────\n"
        + code.strip()
        + "\n# ── END USER CODE ────────────────────────────────────\n"
    )

    # Write to a temp file so tracebacks show line numbers
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, dir=run_dir
    ) as f:
        f.write(wrapper)
        script_path = f.name

    # Inherit os.environ so the subprocess can import venv packages (moviepy, Pillow, etc.).
    # The blocklist above prevents dangerous operations; clearing PYTHONPATH would prevent
    # any package imports and make the executor useless.
    env = {**os.environ, "OUTPUT_DIR": str(output_dir)}

    t0 = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(run_dir),
            env=env,
        )
        duration = time.monotonic() - t0

        output_files = [
            str(p) for p in run_dir.iterdir()
            if p.is_file() and p.suffix in (".mp4", ".wav", ".mp3", ".png", ".gif", ".docx", ".pdf", ".html")
        ]

        return {
            "status":       "ok" if result.returncode == 0 else "error",
            "stdout":       result.stdout[:4000],
            "stderr":       result.stderr[:2000],
            "output_files": output_files,
            "duration_s":   round(duration, 2),
            "run_id":       run_id,
        }

    except subprocess.TimeoutExpired:
        return {
            "status":       "timeout",
            "stdout":       "",
            "stderr":       f"Execution exceeded {timeout_s}s timeout",
            "output_files": [],
            "duration_s":   timeout_s,
            "run_id":       run_id,
        }
    except Exception as exc:
        return {
            "status":       "error",
            "stdout":       "",
            "stderr":       str(exc),
            "output_files": [],
            "duration_s":   round(time.monotonic() - t0, 2),
            "run_id":       run_id,
        }
    finally:
        try:
            Path(script_path).unlink(missing_ok=True)
        except Exception:
            pass
