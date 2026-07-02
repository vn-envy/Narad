#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["NARAD_HOME"] = tmpdir
        import importlib
        import narad_config
        import smriti_core
        import swapna

        narad_config = importlib.reload(narad_config)
        smriti_core = importlib.reload(smriti_core)
        swapna = importlib.reload(swapna)

        smriti_core.capture_episode(
            session_id="baseline-1",
            task="User prefers concise architectural notes and has a milestone deadline.",
            avatar="Rama",
            result="Goal accepted. Must preserve provenance.",
            user_id="validator",
            trace_session_id="trace-baseline-1",
        )
        recall_packet = asyncio.run(
            smriti_core.recall_context(
                "architectural notes",
                user_id="validator",
                avatar="Rama",
            )
        )
        dream_result = swapna.dream(user_id="validator", apply=True, max_episodes=1)
        scorecard = smriti_core.architecture_scorecard()

        payload = {
            "status": "ok",
            "episode_store_exists": (narad_config.EPISODE_DIR / "validator.jsonl").exists(),
            "recall_has_context": bool(recall_packet["context"]),
            "swapna_status": dream_result["status"],
            "swapna_inbox_items": len(smriti_core.load_swapna_inbox()),
            "scorecard": scorecard,
        }
        print(json.dumps(payload, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
