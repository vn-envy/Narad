"""
Operational ml-intern wrapper for research-to-experiment flows.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from tool_result import artifact, ensure_artifact_dir, envelope, ui_panel, write_html_surface, write_json

_LOCAL_MODEL_PREFIXES = ("ollama/", "vllm/", "lm_studio/", "llamacpp/", "local/")


def _find_ml_intern() -> str | None:
    explicit = os.environ.get("ML_INTERN_BIN", "").strip()
    if explicit:
        return explicit
    return shutil.which("ml-intern")


def _requires_hf_token(model: str, sandbox_tools: bool) -> bool:
    if sandbox_tools:
        return True
    normalized = (model or "").strip().lower()
    if not normalized:
        return True
    return not normalized.startswith(_LOCAL_MODEL_PREFIXES)


def _local_env_ready(model: str) -> tuple[bool, str | None]:
    normalized = (model or "").strip().lower()
    if not normalized.startswith(_LOCAL_MODEL_PREFIXES):
        return True, None
    provider = normalized.split("/", 1)[0]
    shared = os.environ.get("LOCAL_LLM_BASE_URL", "").strip()
    specific_base = os.environ.get(f"{provider.upper()}_BASE_URL", "").strip()
    if shared or specific_base:
        return True, None
    return False, f"{provider.upper()}_BASE_URL or LOCAL_LLM_BASE_URL not set"


def _task_needs_github(task: str) -> bool:
    lowered = task.lower()
    return any(token in lowered for token in ("github", "repo", "repository", "commit", "pull request", "pr "))


def _build_command(ml_intern_bin: str, task: str, model: str, max_iterations: int, sandbox_tools: bool) -> list[str]:
    cmd = [ml_intern_bin]
    if model.strip():
        cmd.extend(["--model", model.strip()])
    cmd.extend(["--max-iterations", str(max(1, min(max_iterations, 200)))])
    if sandbox_tools:
        cmd.append("--sandbox-tools")
    cmd.append(task.strip())
    return cmd


def inspect_ml_intern_status(model: str = "", sandbox_tools: bool = False) -> dict[str, Any]:
    ml_intern = _find_ml_intern()
    if not ml_intern:
        return {
            "available": False,
            "ready": False,
            "preview_only": False,
            "reason": "ml-intern binary not found on PATH",
            "binary": None,
            "warnings": [],
            "env_requirements": [
                "Install huggingface/ml-intern and expose `ml-intern` on PATH.",
            ],
        }

    warnings: list[str] = []
    ready = True
    reason: str | None = None
    env_requirements: list[str] = []

    if _requires_hf_token(model, sandbox_tools):
        env_requirements.append("HF_TOKEN")
        if not os.environ.get("HF_TOKEN", "").strip():
            ready = False
            reason = "HF_TOKEN not set for hosted or sandbox ml-intern runs"

    local_ready, local_reason = _local_env_ready(model)
    if not local_ready:
        ready = False
        reason = local_reason or reason
        env_requirements.append("LOCAL_LLM_BASE_URL")

    return {
        "available": True,
        "ready": ready,
        "preview_only": not ready,
        "reason": reason,
        "binary": ml_intern,
        "warnings": warnings,
        "env_requirements": env_requirements,
    }


def _failure_reason(result: subprocess.CompletedProcess[str]) -> str:
    stderr = (result.stderr or "").strip().lower()
    stdout = (result.stdout or "").strip().lower()
    combined = f"{stderr}\n{stdout}"
    if "hf_token" in combined or "hugging face token" in combined or "401" in combined:
        return "ml-intern could not authenticate with Hugging Face. Check HF_TOKEN and provider access."
    if "not found" in combined and "model" in combined:
        return "ml-intern could not resolve the requested model. Check the model id or provider prefix."
    if "sandbox" in combined and "token" in combined:
        return "ml-intern sandbox mode requires HF_TOKEN even for local models."
    return "ml-intern finished with an error. Inspect the captured logs for the exact failure."


def run_ml_experiment(
    task: str,
    model: str = "",
    max_iterations: int = 50,
    sandbox_tools: bool = False,
    dry_run: bool = True,
) -> dict:
    """Run or preview an ml-intern experiment job."""
    if not task or not task.strip():
        return envelope(status="error", summary="ML experiment task cannot be empty.", error="task cannot be empty")

    status = inspect_ml_intern_status(model=model, sandbox_tools=sandbox_tools)
    ml_intern = status.get("binary")
    if not ml_intern:
        summary = "ml-intern is not installed, so Narad cannot execute autonomous ML experiment runs yet."
        return envelope(
            status="unavailable",
            summary=summary,
            error=status["reason"],
            ui=ui_panel(
                title="ml-intern unavailable",
                summary=summary,
                sections=[{
                    "title": "Install",
                    "body": "Clone huggingface/ml-intern, run `uv sync`, then `uv tool install -e .` so `ml-intern` is available globally.",
                }],
            ),
            provenance={"tool": "run_ml_experiment", "method": "ml-intern"},
        )

    command = _build_command(
        ml_intern_bin=ml_intern,
        task=task,
        model=model,
        max_iterations=max_iterations,
        sandbox_tools=sandbox_tools,
    )

    warnings = list(status.get("warnings", []))
    if _task_needs_github(task) and not os.environ.get("GITHUB_TOKEN", "").strip():
        warnings.append("GITHUB_TOKEN is not set; repo-related actions may be limited.")

    summary = "Prepared an ml-intern experiment run."
    sections = [
        {"title": "Task", "body": task[:900]},
        {"title": "Command", "body": shlex.join(command)},
        {
            "title": "Readiness",
            "body": "Ready to execute." if status["ready"] else f"Preview only right now: {status['reason']}",
        },
        {
            "title": "Environment",
            "body": ", ".join(status.get("env_requirements", [])) or "No extra environment requirements detected.",
        },
    ]
    if warnings:
        sections.append({"title": "Warnings", "body": " | ".join(warnings[:4])})

    if dry_run:
        return envelope(
            status="preview",
            summary=summary,
            requires_confirmation=True,
            ui=ui_panel(
                title="ML experiment preview",
                summary=summary,
                sections=sections,
            ),
            provenance={
                "tool": "run_ml_experiment",
                "method": "ml-intern",
                "command": shlex.join(command),
                "readiness": status,
            },
            command=shlex.join(command),
            expected_artifacts=["stdout.log", "stderr.log", "run_manifest.json", "tool_result.json"],
            readiness=status,
            warnings=warnings,
        )

    if not status["ready"]:
        return envelope(
            status="error",
            summary="ml-intern is not ready to execute this run yet.",
            error=status["reason"],
            ui=ui_panel(
                title="ml-intern not ready",
                summary="Execution is blocked until the required runtime configuration is available.",
                sections=sections,
            ),
            provenance={
                "tool": "run_ml_experiment",
                "method": "ml-intern",
                "command": shlex.join(command),
                "readiness": status,
            },
        )

    out_dir = ensure_artifact_dir("ml_intern")
    stdout_path = out_dir / "stdout.log"
    stderr_path = out_dir / "stderr.log"
    command_path = out_dir / "command.txt"
    manifest_path = out_dir / "run_manifest.json"
    command_path.write_text(shlex.join(command), encoding="utf-8")
    write_json(
        manifest_path,
        {
            "tool": "run_ml_experiment",
            "task": task,
            "command": command,
            "command_str": shlex.join(command),
            "model": model,
            "max_iterations": max_iterations,
            "sandbox_tools": sandbox_tools,
            "started_at": out_dir.name,
            "warnings": warnings,
        },
    )

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=1800,
            cwd=str(Path.cwd()),
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        summary = "ml-intern timed out before the experiment could finish."
        stderr_path.write_text("Timed out", encoding="utf-8")
        artifacts = [
            artifact(type="log", label="Command", path=command_path, mime_type="text/plain"),
            artifact(type="json", label="Run manifest", path=manifest_path, mime_type="application/json"),
            artifact(type="log", label="Error log", path=stderr_path, mime_type="text/plain"),
        ]
        artifacts.append(
            write_html_surface(
                out_dir=out_dir,
                title="ML experiment timeout",
                summary=summary,
                sections=sections,
                artifacts=artifacts,
            )
        )
        return envelope(
            status="error",
            summary=summary,
            artifacts=artifacts,
            ui=ui_panel(title="ML experiment timeout", summary=summary, sections=sections),
            provenance={"tool": "run_ml_experiment", "method": "ml-intern", "command": shlex.join(command)},
            error="timeout",
        )
    except Exception as exc:
        return envelope(
            status="error",
            summary=f"ml-intern failed to launch: {exc}",
            error=str(exc),
            provenance={"tool": "run_ml_experiment", "method": "ml-intern", "command": shlex.join(command)},
        )

    stdout_path.write_text(result.stdout or "", encoding="utf-8")
    stderr_path.write_text(result.stderr or "", encoding="utf-8")
    write_json(
        out_dir / "tool_result.json",
        {
            "tool": "run_ml_experiment",
            "task": task,
            "command": shlex.join(command),
            "returncode": result.returncode,
            "model": model,
            "max_iterations": max_iterations,
            "sandbox_tools": sandbox_tools,
            "warnings": warnings,
        },
    )
    artifacts = [
        artifact(type="log", label="Command", path=command_path, mime_type="text/plain"),
        artifact(type="json", label="Run manifest", path=manifest_path, mime_type="application/json"),
        artifact(type="log", label="Stdout log", path=stdout_path, mime_type="text/plain"),
        artifact(type="log", label="Stderr log", path=stderr_path, mime_type="text/plain"),
    ]
    output_preview = (result.stdout or result.stderr or "").strip()[:1400] or "No output captured."
    render_sections = [
        {"title": "Task", "body": task[:900]},
        {"title": "Command", "body": shlex.join(command)},
        {"title": "Output preview", "body": output_preview},
    ]
    html_surface = write_html_surface(
        out_dir=out_dir,
        title="ML experiment report",
        summary="ml-intern finished and Narad captured the run logs as artifacts.",
        sections=render_sections,
        artifacts=artifacts,
    )
    artifacts.append(html_surface)
    return envelope(
        status="ok" if result.returncode == 0 else "error",
        summary=(
            "ml-intern completed the requested experiment run."
            if result.returncode == 0
            else _failure_reason(result)
        ),
        artifacts=artifacts,
        ui=ui_panel(
            title="ML experiment report",
            summary="Captured the ml-intern run as a reusable experiment artifact.",
            sections=render_sections[:2],
            primary_artifact_label="ML experiment report",
        ),
        provenance={
            "tool": "run_ml_experiment",
            "method": "ml-intern",
            "command": shlex.join(command),
            "output_dir": str(out_dir),
            "readiness": status,
        },
        returncode=result.returncode,
        readiness=status,
        warnings=warnings,
    )
