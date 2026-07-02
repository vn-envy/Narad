#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[1/5] Backend contract tests"
python3 -m unittest discover -s "$ROOT_DIR/phase-1" -p 'test_*contract.py'

echo "[2/5] Tool smoke tests"
python3 -m unittest discover -s "$ROOT_DIR/phase-8" -p 'test_tool_smoke.py'

echo "[3/5] Skill registry tests"
python3 -m unittest discover -s "$ROOT_DIR/phase-9" -p 'test_skills.py'

echo "[4/5] Python compile checks"
python3 -m compileall "$ROOT_DIR/phase-1" "$ROOT_DIR/phase-8" "$ROOT_DIR/phase-9"

echo "[5/5] Frontend production build"
(
  cd "$ROOT_DIR/phase-4/frontend"
  npm run build
)

echo "Smoke checks passed."
