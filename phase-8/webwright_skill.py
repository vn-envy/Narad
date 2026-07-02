"""
Webwright-backed browser task skill for Matsya.

This wraps the Webwright CLI when it is installed and configured, but stays
safe-by-default:
  - irreversible tasks are refused unless they go through the existing preview
    browser tools
  - every run writes rerunnable artifacts to ~/.narad/artifacts
  - task2ui returns a rendered HTML surface describing the run
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tool_result import (
    artifact,
    citation,
    ensure_artifact_dir,
    envelope,
    ui_panel,
    write_html_surface,
    write_json,
)

_MUTATING_HINTS = {
    "submit",
    "apply",
    "purchase",
    "buy",
    "checkout",
    "book",
    "place order",
    "sign up",
    "sign-up",
    "register",
    "delete",
    "remove",
    "cancel",
    "upload and submit",
}


def _is_irreversible(task: str) -> bool:
    lower = task.lower()
    return any(hint in lower for hint in _MUTATING_HINTS)


def _find_webwright() -> list[str] | None:
    explicit = os.environ.get("WEBWRIGHT_BIN", "").strip()
    if explicit:
        return [explicit]
    binary = shutil.which("webwright")
    if binary:
        return [binary]
    python_bin = shutil.which("python3") or shutil.which("python")
    if python_bin:
        return [python_bin, "-m", "webwright.run.cli"]
    return None


def _configured_args() -> tuple[list[str], str | None]:
    configs = [cfg.strip() for cfg in os.environ.get("WEBWRIGHT_CONFIGS", "").split(",") if cfg.strip()]
    if not configs:
        return [], "WEBWRIGHT_CONFIGS not set. Expected one or more config files such as base.yaml,model_openai.yaml."
    args: list[str] = []
    for cfg in configs:
        args.extend(["-c", cfg])
    return args, None


def web_task(
    task: str,
    start_url: str = "",
    task2ui: bool = False,
    timeout_s: int = 180,
) -> dict:
    """Run a long-horizon browser task via Webwright.

    The tool is intentionally conservative: it refuses irreversible web actions.
    For form submission flows, Narad should still use browser_screenshot /
    browser_fill / browser_upload_and_submit with explicit user confirmation.
    """
    if not task or not task.strip():
        return envelope(
            status="error",
            summary="Web task could not start because the task text was empty.",
            error="task cannot be empty",
        )

    if _is_irreversible(task):
        summary = (
            "This browser task looks irreversible. Narad should use the existing screenshot/fill/"
            "submit workflow with explicit per-form confirmation instead of autonomous Webwright execution."
        )
        return envelope(
            status="needs_confirmation",
            summary=summary,
            requires_confirmation=True,
            ui=ui_panel(
                title="Confirmation required",
                summary=summary,
                sections=[
                    {
                        "title": "Why this was blocked",
                        "body": "The task contains submit/apply/purchase style intent. Webwright is limited to inspect or reversible flows until the action is explicitly confirmed.",
                    }
                ],
            ),
            provenance={"tool": "web_task", "method": "webwright", "blocked": True},
        )

    command_prefix = _find_webwright()
    if not command_prefix:
        summary = "Webwright is not installed, so Matsya cannot run a long-horizon browser program yet."
        return envelope(
            status="unavailable",
            summary=summary,
            error="Install Webwright (`pip install -e .` in a Webwright checkout and `playwright install chromium`).",
            ui=ui_panel(
                title="Webwright unavailable",
                summary=summary,
                sections=[
                    {
                        "title": "Install",
                        "body": "Clone microsoft/Webwright, install it, then export WEBWRIGHT_CONFIGS so Narad can run the CLI with the right model config.",
                    }
                ],
            ),
            provenance={"tool": "web_task", "method": "webwright"},
        )

    config_args, config_error = _configured_args()
    if config_error:
        summary = "Webwright is present, but Narad is missing the config stack needed to launch it."
        return envelope(
            status="unavailable",
            summary=summary,
            error=config_error,
            ui=ui_panel(
                title="Webwright config missing",
                summary=summary,
                sections=[
                    {
                        "title": "Expected env",
                        "body": "Set WEBWRIGHT_CONFIGS to a comma-separated list of Webwright config files, for example base.yaml,model_openai.yaml.",
                    }
                ],
            ),
            provenance={"tool": "web_task", "method": "webwright"},
        )

    out_dir = ensure_artifact_dir("webwright")
    task_id = out_dir.name
    effective_task = task.strip()
    if start_url.strip():
        effective_task += f"\nStart URL: {start_url.strip()}"

    cmd = [
        *command_prefix,
        *config_args,
        "-t",
        effective_task,
    ]

    rerun_path = out_dir / "rerun_web_task.sh"
    rerun_path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + shlex.join(cmd) + "\n", encoding="utf-8")
    try:
        rerun_path.chmod(0o755)
    except Exception:
        pass

    stdout_path = out_dir / "stdout.log"
    stderr_path = out_dir / "stderr.log"
    command_path = out_dir / "command.txt"
    command_path.write_text(shlex.join(cmd), encoding="utf-8")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(30, min(timeout_s, 900)),
            cwd=str(out_dir),
        )
    except subprocess.TimeoutExpired:
        summary = "Webwright timed out before it could finish the browser task."
        stderr_path.write_text("Timed out", encoding="utf-8")
        artifacts = [
            artifact(type="script", label="Rerun script", path=rerun_path, mime_type="text/x-shellscript"),
            artifact(type="log", label="Command", path=command_path, mime_type="text/plain"),
            artifact(type="log", label="Error log", path=stderr_path, mime_type="text/plain"),
        ]
        html_surface = write_html_surface(
            out_dir=out_dir,
            title="Web task timeout",
            summary=summary,
            sections=[{"title": "Task", "body": task[:800]}],
            artifacts=artifacts,
        )
        artifacts.append(html_surface)
        return envelope(
            status="error",
            summary=summary,
            artifacts=artifacts,
            ui=ui_panel(
                title="Web task timeout",
                summary=summary,
                sections=[{"title": "Timeout", "body": "Try a narrower task, a more specific start URL, or a longer timeout."}],
                primary_artifact_label="Web task timeout",
            ),
            provenance={"tool": "web_task", "method": "webwright", "command": shlex.join(cmd)},
            error="timeout",
        )
    except Exception as exc:
        summary = f"Webwright failed to launch: {exc}"
        return envelope(
            status="error",
            summary=summary,
            error=str(exc),
            provenance={"tool": "web_task", "method": "webwright", "command": shlex.join(cmd)},
        )

    stdout_path.write_text(result.stdout or "", encoding="utf-8")
    stderr_path.write_text(result.stderr or "", encoding="utf-8")

    artifacts = [
        artifact(type="script", label="Rerun script", path=rerun_path, mime_type="text/x-shellscript"),
        artifact(type="log", label="Command", path=command_path, mime_type="text/plain"),
        artifact(type="log", label="Stdout log", path=stdout_path, mime_type="text/plain"),
        artifact(type="log", label="Stderr log", path=stderr_path, mime_type="text/plain"),
    ]
    citations: list[dict] = []

    for candidate in (
        out_dir / "final_script.py",
        out_dir / "plan.md",
        out_dir / "trajectory.json",
        out_dir / "report.json",
    ):
        if candidate.exists():
            artifacts.append(
                artifact(
                    type="python" if candidate.suffix == ".py" else "json" if candidate.suffix == ".json" else "markdown",
                    label=candidate.name,
                    path=candidate,
                    mime_type="text/plain" if candidate.suffix in {".md", ".py"} else "application/json",
                )
            )
            if candidate.name == "report.json":
                try:
                    report = json.loads(candidate.read_text(encoding="utf-8"))
                    sources = report.get("sources", [])
                    for source in sources[:8]:
                        if isinstance(source, dict) and source.get("url"):
                            citations.append(
                                citation(
                                    title=source.get("title") or source.get("url"),
                                    url=source["url"],
                                    source=source.get("source", "webwright"),
                                    snippet=str(source.get("snippet", ""))[:240],
                                )
                            )
                except Exception:
                    pass

    summary = (
        "Webwright completed the browser task and wrote rerunnable artifacts."
        if result.returncode == 0
        else "Webwright ran but reported an execution error. Inspect the logs and rerun script."
    )
    sections = [
        {"title": "Task", "body": task[:800]},
        {"title": "Command", "body": shlex.join(cmd)},
        {"title": "Outcome", "body": (result.stdout or result.stderr or "").strip()[:1200] or "No output captured."},
    ]
    if task2ui:
        html_surface = write_html_surface(
            out_dir=out_dir,
            title="Webwright task report",
            summary=summary,
            sections=sections,
            artifacts=artifacts,
            citations=citations,
        )
        artifacts.append(html_surface)

    write_json(
        out_dir / "tool_result.json",
        {
            "tool": "web_task",
            "task": task,
            "start_url": start_url,
            "command": shlex.join(cmd),
            "returncode": result.returncode,
        },
    )

    return envelope(
        status="ok" if result.returncode == 0 else "error",
        summary=summary,
        artifacts=artifacts,
        citations=citations,
        ui=ui_panel(
            title="Webwright task report",
            summary=summary,
            sections=sections[:2] if task2ui else [{"title": "Task", "body": task[:800]}],
            primary_artifact_label="Webwright task report" if task2ui else "Rerun script",
        ),
        provenance={
            "tool": "web_task",
            "method": "webwright",
            "task_id": task_id,
            "command": shlex.join(cmd),
            "output_dir": str(out_dir),
        },
        returncode=result.returncode,
    )
