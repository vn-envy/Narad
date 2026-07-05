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
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger("narad.scribe")

# Import paths resolved at call time to avoid circular deps
_PHASE2 = Path(__file__).parent.parent / "phase-2"

from narad_config import EPISODE_DIR as _EPISODE_DIR
from narad_config import TRACE_DIR as _TRACE_DIR


def _session_episode_results(session_id: str, user_id: str) -> dict[str, str]:
    """avatar → real result text from the canonical episode store.

    Traces only carry result_len/latency_ms; the wiki used to be compiled from
    those placeholders ("[Completed in 3.1s, response ~800 chars]"). Episodes
    hold the actual output, so read them and keep the last result per avatar.
    """
    path = _EPISODE_DIR / f"{user_id}.jsonl"
    if not path.exists():
        return {}
    results: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("session_id") != session_id and row.get("trace_session_id") != session_id:
                continue
            avatar = row.get("avatar", "")
            result = (row.get("result") or "").strip()
            if avatar and result:
                results[avatar] = result
    except Exception:
        return {}
    return results


def _load_trace(session_id: str) -> list[dict[str, Any]]:
    """Load Yantra trace events for a session."""
    path = _TRACE_DIR / f"{session_id}.jsonl"
    if path.exists():
        return [json.loads(line) for line in path.read_text().splitlines() if line]
    # Fallback: ask Tracer directly (handles any future path changes)
    try:
        from yantra import Tracer
        return Tracer.load(session_id)
    except Exception:
        pass
    return []


def _git_commit_wiki(wiki_dir: Path, user_id: str) -> None:
    """Commit wiki changes to the wiki's OWN Git repo — never a parent repo.

    Historical bug: when WIKI_DIR lived inside the source repo, scribe made
    684 commits into Narad's own history. Guard: the git toplevel must BE
    wiki_dir. If wiki_dir has no repo of its own, init one.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=wiki_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        toplevel = Path(result.stdout.strip()).resolve() if result.returncode == 0 else None
        if toplevel != wiki_dir.resolve():
            if toplevel is not None:
                return  # inside a FOREIGN repo (e.g. source tree) — never commit
            init = subprocess.run(
                ["git", "init"], cwd=wiki_dir, capture_output=True, timeout=5
            )
            if init.returncode != 0:
                return
        subprocess.run(
            ["git", "add", str(wiki_dir / user_id)],
            cwd=wiki_dir,
            capture_output=True,
            timeout=5,
        )
        subprocess.run(
            [
                "git",
                "-c", "user.name=Narad Scribe",
                "-c", "user.email=scribe@narad.local",
                "commit", "-m", f"scribe: update {user_id} project wiki",
            ],
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
        from project_manager import detect_project
        from smriti_v2 import WIKI_DIR, add_episode

        events = _load_trace(session_id)
        if not events:
            return

        # Extract avatar_start → avatar_done pairs; real results come from the
        # canonical episode store, latency notes are only the fallback.
        real_results = _session_episode_results(session_id, user_id)
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
                fallback_note = (
                    f"[Completed in {latency_ms / 1000:.1f}s, "
                    f"response ~{result_len} chars]"
                ) if result_len else f"[Completed in {latency_ms / 1000:.1f}s]"
                result_text = real_results.get(avatar, "") or fallback_note
                episodes.append((avatar, pending.pop(avatar), result_text[:3000]))

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
