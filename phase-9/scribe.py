"""
Scribe — post-session wiki compiler (llmwiki pattern).

Runs async after every session ends. Reads the Yantra trace, extracts the
task/result pairs from each avatar, calls smriti_v2.add_episode() for each,
then optionally commits the wiki to Git if detected.

Wired into server.py as a fire-and-forget task on 'done' events.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger("narad.scribe")

# Import paths resolved at call time to avoid circular deps
_PHASE2 = Path(__file__).parent.parent / "phase-2"

import sys as _sys_nc
_sys_nc.path.insert(0, str(Path(__file__).parent.parent))
from narad_config import TRACE_DIR as _TRACE_DIR


def _load_trace(session_id: str) -> list[dict[str, Any]]:
    """Load Yantra trace events for a session."""
    path = _TRACE_DIR / f"{session_id}.jsonl"
    if path.exists():
        return [json.loads(line) for line in path.read_text().splitlines() if line]
    # Fallback: ask Tracer directly (handles any future path changes)
    try:
        import sys
        sys.path.insert(0, str(_PHASE2))
        from yantra import Tracer
        return Tracer.load(session_id)
    except Exception:
        pass
    return []


def _git_commit_wiki(wiki_dir: Path, user_id: str) -> None:
    """Commit wiki changes to Git if the wiki dir is inside a Git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=wiki_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return
        subprocess.run(
            ["git", "add", str(wiki_dir / user_id)],
            cwd=wiki_dir,
            capture_output=True,
            timeout=5,
        )
        subprocess.run(
            ["git", "commit", "-m", f"scribe: update {user_id} project wiki"],
            cwd=wiki_dir,
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass  # Git not available or not in a repo — no-op


async def compile_session(session_id: str, user_id: str) -> None:
    """
    Main entry point — called from server.py after each session completes.
    Fire-and-forget: never raises, never blocks the response.
    """
    try:
        import sys
        _phase9 = Path(__file__).parent
        sys.path.insert(0, str(_phase9))
        from smriti_v2 import add_episode, WIKI_DIR
        from project_manager import detect_project

        events = _load_trace(session_id)
        if not events:
            return

        # Extract avatar_start → avatar_done pairs
        pending: dict[str, str] = {}  # avatar → task
        episodes: list[tuple[str, str, str]] = []  # (avatar, task, result)

        for evt in events:
            e_type = evt.get("event")
            avatar = evt.get("avatar")
            task   = evt.get("task", "")

            if e_type == "avatar_start" and avatar:
                pending[avatar] = task or ""
            elif e_type == "avatar_done" and avatar and avatar in pending:
                result_len = evt.get("result_len", 0)
                latency_ms = evt.get("latency_ms", 0)
                result_note = (
                    f"[Completed in {latency_ms / 1000:.1f}s, "
                    f"response ~{result_len} chars]"
                ) if result_len else f"[Completed in {latency_ms / 1000:.1f}s]"
                episodes.append((avatar, pending.pop(avatar), result_note))

        if not episodes:
            return

        # Classify session into a project
        task_texts = [task for _, task, _ in episodes if task]
        project_id = await detect_project(user_id, session_id, task_texts)

        # Write wiki entries under the detected project
        coros = [
            add_episode(user_id, session_id, avatar, task, result, project_id)
            for avatar, task, result in episodes
        ]
        await asyncio.gather(*coros)

        # Git commit (best-effort)
        _git_commit_wiki(WIKI_DIR, user_id)

        log.info(
            "Scribe: compiled %d episodes for session %s → project %s",
            len(episodes), session_id[:8], project_id,
        )

    except Exception as exc:
        log.warning("Scribe: failed for session %s: %s", session_id[:8], exc)
