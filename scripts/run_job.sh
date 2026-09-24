#!/bin/bash
# Run one Narad ops script with the pilot environment (.env included), e.g.
#   scripts/run_job.sh scripts/narad_backup.py backup
# launchd uses it for the uptime, backup and drill jobs.
set -euo pipefail

# shellcheck source=scripts/pilot_env.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pilot_env.sh"
cd "$NARAD_ROOT"

if [ ! -x "$NARAD_ROOT/.venv/bin/python" ]; then
    echo "[narad] $NARAD_ROOT/.venv is missing: run Start Narad.command once." >&2
    exit 78  # EX_CONFIG
fi
exec "$NARAD_ROOT/.venv/bin/python" "$@"
