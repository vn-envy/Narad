#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[1/2] Offline smoke suite"
bash "$ROOT_DIR/scripts/run_refinement_smoke.sh"

echo "[2/2] Live 4-agent E2E harness"
"$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/run_live_agent_e2e.py"

echo "Final validation passed."
