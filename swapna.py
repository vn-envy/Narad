"""
Swapna — offline dream-cycle orchestrator.

This is a thin first-class subsystem over Smriti Core so the architecture
retains a real cultural boundary:
  - Smriti stores evidence and abstractions
  - Tapas evaluates
  - Swapna consolidates offline
"""

from __future__ import annotations

from typing import Any

from smriti_core import load_swapna_inbox, run_swapna_cycle


def dream(
    *,
    user_id: str = "default",
    project_id: str = "general",
    max_episodes: int = 20,
    apply: bool = False,
) -> dict[str, Any]:
    return run_swapna_cycle(
        user_id=user_id,
        project_id=project_id,
        max_episodes=max_episodes,
        apply=apply,
    )


def inbox() -> list[dict[str, Any]]:
    return load_swapna_inbox()
