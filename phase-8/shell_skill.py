"""
Parashurama shell execution skill — allowlisted subprocess runner.

Runs real shell commands in the user's working directory.
Covers git, npm, pytest, docker, cargo, go, and file inspection tools.

Safety model:
  - Base command must be in _ALLOWLIST
  - Pattern blocklist catches destructive flags (rm -rf, sudo, pipe-to-shell, etc.)
  - Hard timeout (default 60s, configurable via SHELL_TIMEOUT env var)
  - Working directory must be under home or an explicitly passed path
  - stdout/stderr are capped to avoid context overflow
"""
from __future__ import annotations

import os
import re
import shlex
import stat
import subprocess
import time
from pathlib import Path

TIMEOUT_S = int(os.environ.get("SHELL_TIMEOUT", "60"))

_ALLOWLIST = {
    # VCS
    "git",
    # JS / Node ecosystem
    "npm", "npx", "yarn", "pnpm", "node", "bun", "deno",
    # Python ecosystem
    "python", "python3", "pip", "pip3", "uv", "pytest",
    "ruff", "mypy", "black", "isort", "flake8", "bandit",
    # Build
    "make", "cmake",
    # Containers
    "docker", "docker-compose",
    # Rust
    "cargo", "rustc", "rustfmt", "clippy",
    # Go
    "go", "gofmt",
    # File inspection (read-only)
    "ls", "cat", "find", "grep", "head", "tail", "wc", "sort",
    "uniq", "diff", "file", "stat", "du", "df", "md5", "sha256sum",
    # Safe filesystem ops
    "mkdir", "cp", "mv", "touch", "pwd", "which", "env",
    "printenv", "echo", "printf",
    # Text processing
    "jq", "yq", "sed", "awk", "cut", "xargs", "tr",
    # Network (read-only or targeted)
    "curl", "wget", "ping",
    # Package inspectors
    "brew", "apt", "apt-get",
}

# Patterns blocked even if the base command is in the allowlist
_BLOCKLIST_PATTERNS = [
    r"\brm\s+-[^=]*r",          # rm -r, rm -rf, rm -Rf
    r"\bsudo\b",
    r"\bsu\s",
    r"\|\s*(sh|bash|zsh|fish|python|python3|node|ruby|perl)\b",  # pipe to shell
    r">\s*/(?:etc|usr|bin|sbin|System|Library|private)",         # write to system dirs
    r"\bchmod\s+[0-7]*7[0-7]{2}",  # world-writable perms
    r"\bchown\s+root",
    r":\(\)\s*\{",               # fork bomb
    r"\bdd\s+if=",               # disk dump
    r"\bmkfs\b",                 # format disk
    r"--no-verify\b",            # git bypass
]

_BLOCKED_REs = [re.compile(p, re.IGNORECASE) for p in _BLOCKLIST_PATTERNS]


def _check_command(command: str) -> str | None:
    """Return an error message if the command is unsafe, else None."""
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return f"Could not parse command: {exc}"

    if not tokens:
        return "Empty command."

    base = Path(tokens[0]).name.lower()
    if base not in _ALLOWLIST:
        return (
            f"Command '{base}' is not in the allowlist. "
            f"Allowed: {', '.join(sorted(_ALLOWLIST))}"
        )

    for pattern in _BLOCKED_REs:
        if pattern.search(command):
            return f"Blocked pattern detected in command: '{pattern.pattern}'"

    return None


def run_shell(command: str, working_dir: str = "~", timeout_s: int = TIMEOUT_S) -> dict:
    """Run a shell command and return its output.

    Safe for: git, npm/yarn/pnpm, pytest, docker, cargo, go, curl,
    python, pip, make, file inspection (ls/grep/cat/find/diff), jq, sed/awk.

    Args:
        command:     Full shell command string. e.g. "git log --oneline -10"
        working_dir: Directory to run in. Defaults to "~" (home). Can be any
                     project path. e.g. "~/projects/myapp"
        timeout_s:   Max seconds before the process is killed (default 60).

    Returns a dict with:
        status:     "ok" | "error" | "timeout" | "blocked"
        stdout:     Command standard output (capped at 8000 chars)
        stderr:     Command standard error (capped at 2000 chars)
        exit_code:  Integer exit code (0 = success)
        duration_s: Wall-clock seconds
        command:    The command that was run (for traceability)

    WORKFLOW TIP for Parashurama:
        For project tasks, always pass the project root as working_dir.
        Chain read-only inspection first:
          1. run_shell("ls -la", working_dir="~/myproject")
          2. run_shell("git status", working_dir="~/myproject")
          3. run_shell("npm test", working_dir="~/myproject")
    """
    blocked = _check_command(command)
    if blocked:
        return {
            "status":    "blocked",
            "message":   blocked,
            "stdout":    "",
            "stderr":    "",
            "exit_code": -1,
            "duration_s": 0.0,
            "command":   command,
        }

    cwd = Path(working_dir).expanduser().resolve()
    if not cwd.exists():
        return {
            "status":    "error",
            "message":   f"Working directory does not exist: {cwd}",
            "stdout":    "",
            "stderr":    "",
            "exit_code": -1,
            "duration_s": 0.0,
            "command":   command,
        }

    start = time.time()
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(cwd),
            env={**os.environ},
        )
        duration = round(time.time() - start, 2)
        status = "ok" if result.returncode == 0 else "error"
        return {
            "status":    status,
            "stdout":    result.stdout[:8000],
            "stderr":    result.stderr[:2000],
            "exit_code": result.returncode,
            "duration_s": duration,
            "command":   command,
            "message":   (
                f"Exit {result.returncode} in {duration}s."
                + (" stderr present." if result.stderr.strip() else "")
            ),
        }
    except subprocess.TimeoutExpired:
        return {
            "status":    "timeout",
            "message":   f"Command exceeded {timeout_s}s timeout and was killed.",
            "stdout":    "",
            "stderr":    "",
            "exit_code": -1,
            "duration_s": float(timeout_s),
            "command":   command,
        }
    except Exception as exc:
        return {
            "status":    "error",
            "message":   str(exc),
            "stdout":    "",
            "stderr":    "",
            "exit_code": -1,
            "duration_s": round(time.time() - start, 2),
            "command":   command,
        }


# ── File reading ────────────────────────────────────────────────────────────

def read_file(path: str, max_chars: int = 50_000) -> dict:
    """Read a text file from disk and return its content.

    Use this to read scripts, configs, source code, plain-text resumes, HTML,
    JSON, YAML, etc. For PDFs and DOCX use Varaha's extract_document() instead.

    Args:
        path:      File path under ~ (e.g. "~/scripts/job_search.py").
                   Restricted to paths inside the home directory.
        max_chars: Maximum characters to return (default 50 000).
                   Larger files are truncated — check the 'truncated' field.

    Returns:
        status:     "ok" | "error"
        content:    File text content (up to max_chars)
        path:       Resolved absolute path
        size_bytes: Full file size on disk
        truncated:  True if content was cut short at max_chars
    """
    try:
        resolved = Path(path).expanduser().resolve()
        home = Path.home().resolve()
        if not str(resolved).startswith(str(home)):
            return {
                "status": "error",
                "message": f"read_file is restricted to paths under ~. Got: {resolved}",
                "content": "",
            }
        if not resolved.exists():
            return {"status": "error", "message": f"File not found: {resolved}", "content": ""}
        if not resolved.is_file():
            return {"status": "error", "message": f"Path is not a file: {resolved}", "content": ""}

        size_bytes = resolved.stat().st_size
        raw = resolved.read_text(encoding="utf-8", errors="replace")
        truncated = len(raw) > max_chars
        return {
            "status":     "ok",
            "content":    raw[:max_chars],
            "path":       str(resolved),
            "size_bytes": size_bytes,
            "truncated":  truncated,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc), "content": ""}


# ── Script writing ──────────────────────────────────────────────────────────

def write_script(content: str, path: str) -> dict:
    """Write a multi-line script file to disk.

    Use this — NOT run_shell — whenever you need to create a Python, shell, or
    any multi-line script. Embedding multi-line code inside a run_shell command
    produces malformed JSON and will always fail.

    Args:
        content: Full script text (Python, bash, etc.). May contain any characters.
        path:    Destination path under ~ (e.g. "~/scripts/check_jobs.py").
                 Parent directories are created automatically.

    Returns:
        status:    "ok" | "error"
        file_path: Resolved absolute path
        message:   Bytes written or error description
    """
    try:
        resolved = Path(path).expanduser().resolve()
        # Restrict writes to home directory
        home = Path.home().resolve()
        if not str(resolved).startswith(str(home)):
            return {"status": "error", "message": f"write_script is restricted to paths under ~. Got: {resolved}"}
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        # Make executable if it looks like a script with a shebang
        if content.lstrip().startswith("#!"):
            resolved.chmod(resolved.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        return {
            "status":    "ok",
            "file_path": str(resolved),
            "message":   f"Written {len(content.encode())} bytes to {resolved}",
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc), "file_path": ""}


# ── Cron job management ─────────────────────────────────────────────────────

_NARAD_TAG = "# narad:"


def schedule_cron(schedule: str, command: str, comment: str) -> dict:
    """Add or update a cron job managed by Narad.

    Each job is tagged with a unique comment so it can be found and removed later.
    If a job with the same comment already exists it is replaced.

    Args:
        schedule: Standard cron schedule string, e.g. "0 2 */3 * *" (2 AM every 3 days)
                  Format: minute hour day-of-month month day-of-week
                  Common patterns:
                    Every 3 days at 2 AM: "0 2 */3 * *"
                    Daily at midnight:    "0 0 * * *"
                    Every Sunday at 9 AM: "0 9 * * 0"
        command:  Shell command to execute, e.g. "python3 ~/scripts/check_jobs.py >> ~/scripts/check_jobs.log 2>&1"
        comment:  Short unique identifier for this job (no spaces), e.g. "check_roles_hyderabad"

    Returns:
        status:   "ok" | "error"
        message:  Confirmation or error description
        schedule: The schedule string installed
        command:  The command installed
    """
    try:
        existing_result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        existing = existing_result.stdout if existing_result.returncode == 0 else ""

        # Remove any existing entry with the same tag
        tag = f"{_NARAD_TAG}{comment}"
        lines = [ln for ln in existing.splitlines() if tag not in ln]
        lines.append(f"{schedule} {command}  {tag}")
        new_crontab = "\n".join(lines) + "\n"

        proc = subprocess.run(["crontab", "-"], input=new_crontab, capture_output=True, text=True)
        if proc.returncode == 0:
            return {
                "status":   "ok",
                "schedule": schedule,
                "command":  command,
                "comment":  comment,
                "message":  f"Cron job '{comment}' scheduled: {schedule}  {command}",
            }
        return {"status": "error", "message": proc.stderr[:300]}
    except FileNotFoundError:
        return {"status": "error", "message": "crontab not found on this system. On macOS use launchd or ensure cron is installed."}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def list_cron_jobs() -> dict:
    """List all Narad-managed cron jobs (tagged with # narad:) and the full crontab.

    Returns:
        status:     "ok" | "error"
        narad_jobs: List of cron lines managed by Narad
        raw:        Full crontab output for reference
        message:    Summary
    """
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if result.returncode != 0:
            return {"status": "ok", "narad_jobs": [], "raw": "", "message": "No crontab found."}
        lines = result.stdout.splitlines()
        narad_jobs = [ln for ln in lines if _NARAD_TAG in ln]
        return {
            "status":     "ok",
            "narad_jobs": narad_jobs,
            "raw":        result.stdout,
            "message":    f"{len(narad_jobs)} Narad-managed job(s) found.",
        }
    except Exception as exc:
        return {"status": "error", "narad_jobs": [], "raw": "", "message": str(exc)}


def remove_cron_job(comment: str) -> dict:
    """Remove a Narad-managed cron job by its comment tag.

    Args:
        comment: The comment used when the job was scheduled (e.g. "check_roles_hyderabad")

    Returns:
        status:  "ok" | "error"
        message: Confirmation or error description
    """
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if result.returncode != 0:
            return {"status": "ok", "message": "No crontab found — nothing to remove."}
        tag = f"{_NARAD_TAG}{comment}"
        lines = [ln for ln in result.stdout.splitlines() if tag not in ln]
        new_crontab = "\n".join(lines) + "\n"
        proc = subprocess.run(["crontab", "-"], input=new_crontab, capture_output=True, text=True)
        if proc.returncode == 0:
            return {"status": "ok", "message": f"Removed cron job: {comment}"}
        return {"status": "error", "message": proc.stderr[:300]}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
