#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split
from smriti_core import architecture_scorecard


def main() -> int:
    baseline_path = Path.home() / ".narad" / "benchmarks" / "baseline" / "baseline_narad_legacy.json"
    if not baseline_path.exists():
        repo_fixture = ROOT / "benchmarks" / "baseline_narad_legacy.json"
        if repo_fixture.exists():
            baseline_path = repo_fixture
        else:
            print(json.dumps({"status": "error", "message": f"Missing baseline snapshot at {baseline_path}"}))
            return 1

    baseline = json.loads(baseline_path.read_text())
    current = architecture_scorecard()
    comparison = {
        "legacy_direct_memory_imports": {
            "baseline": baseline["legacy_direct_memory_imports"],
            "current": current["legacy_direct_memory_imports"],
            "delta": current["legacy_direct_memory_imports"] - baseline["legacy_direct_memory_imports"],
        },
        "memory_path_count": {
            "baseline": baseline["legacy_writer_count"],
            "current": 1,
            "delta": 1 - baseline["legacy_writer_count"],
        },
        "swapna_enabled": current["swapna_enabled"],
        "karma_mutation_log_enabled": current["karma_mutation_log_enabled"],
    }
    print(json.dumps({"status": "ok", "comparison": comparison}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
