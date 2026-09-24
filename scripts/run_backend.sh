#!/bin/bash
# Run the Narad backend in the foreground with the family-pilot environment.
# launchd keeps it alive (com.narad.backend); Start Family Pilot.command runs it
# in the background. exec keeps a single PID, so either supervisor can stop it.
set -euo pipefail

# shellcheck source=scripts/pilot_env.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pilot_env.sh"
cd "$NARAD_ROOT"

if [ ! -x "$NARAD_ROOT/.venv/bin/python" ]; then
    echo "[narad] $NARAD_ROOT/.venv is missing: run Start Narad.command once." >&2
    exit 78  # EX_CONFIG
fi
if [ ! -f "$FRONTEND_DIR/dist/index.html" ]; then
    echo "[narad] The web app is not built; scripts/install_launchd.sh install builds it." >&2
fi

exec "$NARAD_ROOT/.venv/bin/python" narad_server_entry.py --host "$BACKEND_HOST" --port "$BACKEND_PORT"
