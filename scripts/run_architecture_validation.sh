#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 "$ROOT/scripts/capture_architecture_baseline.py"
(
  cd "$ROOT/phase-1" &&
  python3 -m unittest test_server_contract.py test_runtime_contract.py test_cultural_core.py
)
(
  cd "$ROOT/phase-2" &&
  python3 -m unittest test_smriti.py
)
(
  cd "$ROOT/phase-8" &&
  python3 -m unittest test_tool_smoke.py
)
python3 "$ROOT/scripts/compare_architecture_metrics.py"
python3 "$ROOT/scripts/run_cultural_validation.py"
