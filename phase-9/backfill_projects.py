"""
Backfill all historical sessions into projects.

Run from phase-9/ directory:
    python backfill_projects.py

Reads all Yantra traces from phase-2/yantra_traces/, classifies each session
into a project using LiteLLM, and writes to projects.json.
Respects sessions already assigned (skips them).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_ROOT / "phase-2"))

from project_manager import detect_project, load_projects

sys.path.insert(0, str(_ROOT))
from narad_config import TRACE_DIR as _TRACE_DIR


def _already_assigned(user_id: str, session_id: str) -> bool:
    for p in load_projects(user_id):
        if session_id in p.get("session_ids", []):
            return True
    return False


async def _process(trace_path: Path, user_id: str, sem: asyncio.Semaphore) -> str:
    session_id = trace_path.stem
    if _already_assigned(user_id, session_id):
        return f"skip  {session_id[:8]}"

    events = [json.loads(l) for l in trace_path.read_text().splitlines() if l]
    tasks = [
        e["task"] for e in events
        if e.get("event") == "avatar_start" and e.get("task")
    ]
    if not tasks:
        return f"empty {session_id[:8]}"

    async with sem:
        project_id = await detect_project(user_id, session_id, tasks)
    return f"ok    {session_id[:8]} → {project_id}"


async def backfill(user_id: str = "default") -> None:
    traces = sorted(_TRACE_DIR.glob("*.jsonl"))
    if not traces:
        print(f"No traces found in {_TRACE_DIR}")
        return

    print(f"Backfilling {len(traces)} sessions...")
    sem = asyncio.Semaphore(8)  # max 8 concurrent LiteLLM calls
    results = await asyncio.gather(*[_process(t, user_id, sem) for t in traces])

    counts: dict[str, int] = {}
    for r in results:
        key = r.split()[0]
        counts[key] = counts.get(key, 0) + 1

    print(f"Done. ok={counts.get('ok', 0)}  skip={counts.get('skip', 0)}  empty={counts.get('empty', 0)}")

    from project_manager import load_projects
    projects = load_projects(user_id)
    print(f"\nProjects created ({len(projects)}):")
    for p in sorted(projects, key=lambda x: -len(x.get("session_ids", []))):
        print(f"  [{p['id']}] {p['name']} — {len(p.get('session_ids', []))} sessions")


async def backfill_wiki(user_id: str = "default") -> None:
    """Re-compile wiki entries for all sessions into their project directories."""
    from scribe import compile_session  # scribe.py is in phase-9 (already on sys.path)

    traces = sorted(_TRACE_DIR.glob("*.jsonl"))
    print(f"Compiling wiki for {len(traces)} sessions...")
    sem = asyncio.Semaphore(5)

    async def run(trace_path: Path) -> None:
        async with sem:
            await compile_session(trace_path.stem, user_id)

    await asyncio.gather(*[run(t) for t in traces])
    print("Wiki compilation done.")


if __name__ == "__main__":
    import sys as _sys_main
    if "--wiki" in _sys_main.argv:
        asyncio.run(backfill_wiki())
    else:
        asyncio.run(backfill())
