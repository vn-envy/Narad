#!/bin/bash
# Start Narad — double-click this file in Finder.
# First run sets everything up (venv + frontend build); later runs start in seconds.

set -e
cd "$(dirname "$0")"

echo "🪕  Narad"
echo

# 1. Python venv + backend install (first run only)
if [ ! -x .venv/bin/python ]; then
    echo "First run: creating Python environment…"
    python3 -m venv .venv
    ./.venv/bin/pip install --upgrade pip -q
    echo "Installing Narad (this can take a few minutes)…"
    ./.venv/bin/pip install -e . -q
fi
source .venv/bin/activate

# 2. Frontend build (first run only, or after you delete dist/)
if [ ! -f phase-4/frontend/dist/index.html ]; then
    echo "First run: building the frontend…"
    ( cd phase-4/frontend && npm ci && npm run build )
fi

# 3. Open the browser once the server is up, then run the server
( for i in $(seq 1 60); do
      curl -s -o /dev/null http://127.0.0.1:8000/health && { open http://127.0.0.1:8000; exit; }
      sleep 1
  done ) &

echo
echo "Starting narad-server at http://127.0.0.1:8000 — close this window (or press Ctrl+C) to stop."
echo
exec narad-server
