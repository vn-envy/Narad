"""
Phase 7 — Sandboxed Code Executor

Runs Parashurama-generated Python code in a subprocess with:
  - AST-based safety analysis (imports, dangerous calls, absolute-path writes)
  - Environment scrubbing — an allowlist of harmless vars; API keys, tokens,
    and other secrets in os.environ are NEVER visible to executed code
  - Wall-clock timeout with process-group kill (no orphaned ffmpeg children)
  - Bounded stdout/stderr capture (spooled to disk, not RAM)
  - Isolated output directory

All generated files land in <ARTIFACTS_DIR>/<run_id>/
The caller is responsible for moving or serving them.

Threat model note: the AST check is a guardrail against careless or misled
generated code, not a jail against a determined adversary. Real containment
comes from the scrubbed environment (nothing worth stealing), the process-group
timeout, and OS-level file permissions.
"""
from __future__ import annotations

import ast
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from narad_config import ARTIFACTS_DIR as _OUTPUTS_DIR

TIMEOUT_S = int(os.environ.get("EXECUTOR_TIMEOUT", "90"))
_STDOUT_CAP = 4000   # chars returned to caller (contract)
_STDERR_CAP = 2000
_READ_CAP = 65536    # bytes read back from spool files
_MAX_OUTPUT_FILES = 50

# ── AST safety analysis ────────────────────────────────────────────────────────

# Module roots that give executed code network access, process control, or
# raw-memory escape hatches. Internal imports by allowed libraries (e.g. moviepy
# using multiprocessing) are unaffected — only the generated code is analyzed.
_BLOCKED_MODULES = {
    "subprocess", "multiprocessing", "ctypes", "pty",
    "socket", "socketserver", "ssl",
    "urllib", "requests", "httpx", "aiohttp", "http",
    "ftplib", "smtplib", "telnetlib", "xmlrpc", "webbrowser",
    "importlib",  # dynamic-import evasion of this very list
}

# Builtins that execute strings, dynamically import, or hang the subprocess.
_BLOCKED_BUILTINS = {"eval", "exec", "compile", "__import__", "breakpoint", "input"}

# Attribute calls on these modules that spawn processes or delete files.
_BLOCKED_ATTRS: dict[str, set[str]] = {
    "os": {
        "system", "popen", "fork", "forkpty", "kill", "killpg",
        "remove", "unlink", "rmdir", "removedirs",
    },
    "shutil": {"rmtree", "move", "chown"},
}
_BLOCKED_ATTR_PREFIXES: dict[str, tuple[str, ...]] = {
    "os": ("exec", "spawn"),  # execv, execvp, spawnl, ...
}


def _attr_blocked(mod: str, name: str) -> bool:
    if name in _BLOCKED_ATTRS.get(mod, set()):
        return True
    return name.startswith(_BLOCKED_ATTR_PREFIXES.get(mod, ()))


def _check_safety(code: str) -> str | None:
    """Return an error string if the code contains blocked constructs, else None.

    Unparseable code returns None — it cannot execute either, and the subprocess
    traceback is more useful to the repairing avatar than a 'blocked' verdict.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _BLOCKED_MODULES:
                    return f"Blocked import: '{alias.name}' (line {node.lineno})"

        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _BLOCKED_MODULES:
                return f"Blocked import: 'from {node.module} import …' (line {node.lineno})"

        elif isinstance(node, ast.Call):
            func = node.func

            # Bare builtins: eval("…"), __import__("…"), input(), …
            if isinstance(func, ast.Name):
                if func.id in _BLOCKED_BUILTINS:
                    return f"Blocked call: '{func.id}()' (line {node.lineno})"

                # getattr(os, "system") — the classic string-blocklist evasion
                if func.id == "getattr" and node.args:
                    target = node.args[0]
                    if isinstance(target, ast.Name) and target.id in _BLOCKED_ATTRS:
                        name_arg = node.args[1] if len(node.args) > 1 else None
                        if not isinstance(name_arg, ast.Constant):
                            return (
                                f"Blocked call: getattr on '{target.id}' with "
                                f"dynamic attribute (line {node.lineno})"
                            )
                        if isinstance(name_arg.value, str) and _attr_blocked(target.id, name_arg.value):
                            return (
                                f"Blocked call: getattr({target.id}, "
                                f"'{name_arg.value}') (line {node.lineno})"
                            )

                # open("/abs/path") — writes must stay inside OUTPUT_DIR
                if func.id == "open" and node.args:
                    first = node.args[0]
                    if (
                        isinstance(first, ast.Constant)
                        and isinstance(first.value, str)
                        and first.value.startswith("/")
                    ):
                        return (
                            f"Blocked call: open() with absolute path "
                            f"'{first.value[:60]}' — use OUTPUT_DIR (line {node.lineno})"
                        )

            # os.system(...), shutil.rmtree(...), os.execvp(...)
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id in _BLOCKED_ATTRS and _attr_blocked(func.value.id, func.attr):
                    return (
                        f"Blocked call: '{func.value.id}.{func.attr}()' "
                        f"(line {node.lineno})"
                    )

    return None


# ── Environment scrubbing ──────────────────────────────────────────────────────

# Only these names cross into the subprocess. Everything else — DEEPSEEK_API_KEY,
# GOOGLE_*, and any future secret — stays in the parent.
_ENV_PASSTHROUGH = {
    "PATH", "HOME", "USER", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TERM",
    "TMPDIR", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
    "FFMPEG_BINARY", "IMAGEMAGICK_BINARY", "FONTCONFIG_PATH", "MPLBACKEND",
}
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH")


def _build_env(output_dir: Path) -> dict[str, str]:
    passthrough = set(_ENV_PASSTHROUGH)
    # Operator-extendable (e.g. EXECUTOR_ENV_PASSTHROUGH="IMAGEIO_FFMPEG_EXE")
    for name in os.environ.get("EXECUTOR_ENV_PASSTHROUGH", "").split(","):
        name = name.strip()
        if name:
            passthrough.add(name)

    env: dict[str, str] = {}
    for name in passthrough:
        # Defense in depth: secret-looking names never pass, even if configured.
        if any(marker in name.upper() for marker in _SECRET_MARKERS):
            continue
        value = os.environ.get(name)
        if value is not None:
            env[name] = value

    env.setdefault("MPLBACKEND", "Agg")  # headless matplotlib
    env["OUTPUT_DIR"] = str(output_dir)
    return env


# ── Execution ──────────────────────────────────────────────────────────────────

def _read_capped(path: Path, char_cap: int) -> str:
    try:
        with path.open("r", errors="replace") as handle:
            return handle.read(_READ_CAP)[:char_cap]
    except Exception:
        return ""


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

    # Mandatory Dharma gate — verdict lands in the Karma ledger. Fail closed:
    # if the policy layer itself is broken, generated code does not run.
    try:
        from dharma import gate_action

        verdict = gate_action(
            "executor",
            avatar="Parashurama",
            detail=f"run {run_id}: {code.strip()[:120]}",
        )
        gate_err = None if verdict.allowed else "; ".join(verdict.reasons)
    except Exception as exc:
        gate_err = f"Dharma gate unavailable ({exc}) — refusing to execute."
    if gate_err:
        return {
            "status":       "blocked",
            "stdout":       "",
            "stderr":       gate_err,
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

    env = _build_env(output_dir)
    stdout_path = run_dir / f".stdout-{run_id}"
    stderr_path = run_dir / f".stderr-{run_id}"

    t0 = time.monotonic()
    proc: subprocess.Popen | None = None
    try:
        with stdout_path.open("wb") as out_f, stderr_path.open("wb") as err_f:
            # start_new_session → own process group, so a timeout kill also
            # takes down grandchildren (ffmpeg, soffice, …). Spooling output to
            # disk instead of PIPE keeps runaway prints out of parent RAM.
            # No -I/-E flags: the runtime resolves venv packages via PYTHONPATH,
            # which isolated mode would ignore. Secret isolation comes from the
            # scrubbed env, not interpreter flags.
            proc = subprocess.Popen(
                [sys.executable, script_path],
                stdout=out_f,
                stderr=err_f,
                cwd=str(run_dir),
                env=env,
                start_new_session=True,
            )
            try:
                returncode = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    proc.kill()
                proc.wait()
                return {
                    "status":       "timeout",
                    "stdout":       _read_capped(stdout_path, _STDOUT_CAP),
                    "stderr":       f"Execution exceeded {timeout_s}s timeout",
                    "output_files": [],
                    "duration_s":   timeout_s,
                    "run_id":       run_id,
                }

        duration = time.monotonic() - t0

        output_files = [
            str(p) for p in sorted(run_dir.iterdir())
            if p.is_file()
            and not p.name.startswith(".")
            and p.suffix in (".mp4", ".wav", ".mp3", ".png", ".gif", ".docx", ".pdf", ".html")
        ][:_MAX_OUTPUT_FILES]

        return {
            "status":       "ok" if returncode == 0 else "error",
            "stdout":       _read_capped(stdout_path, _STDOUT_CAP),
            "stderr":       _read_capped(stderr_path, _STDERR_CAP),
            "output_files": output_files,
            "duration_s":   round(duration, 2),
            "run_id":       run_id,
        }

    except Exception as exc:
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                proc.kill()
        return {
            "status":       "error",
            "stdout":       "",
            "stderr":       str(exc),
            "output_files": [],
            "duration_s":   round(time.monotonic() - t0, 2),
            "run_id":       run_id,
        }
    finally:
        for stale in (Path(script_path), stdout_path, stderr_path):
            try:
                stale.unlink(missing_ok=True)
            except Exception:
                pass
