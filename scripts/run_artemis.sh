#!/bin/bash
# Run the Artemis Android service in the foreground with the pilot environment:
# its admin API on 127.0.0.1:$NARAD_ARTEMIS_PORT only (it has no auth of its
# own), device keep-awake and helper auto-install off (scripts/enroll_android.sh
# installs the helper while you watch). launchd keeps it alive
# (com.narad.artemis); exec keeps a single PID.
set -euo pipefail

# shellcheck source=scripts/pilot_env.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pilot_env.sh"

ADMIN="$ARTEMIS_DIR/.venv/bin/artemis-admin"
if [ ! -x "$ADMIN" ]; then
    echo "[narad] Artemis is not installed under $ARTEMIS_DIR (no .venv/bin/artemis-admin)." >&2
    exit 78  # EX_CONFIG
fi
cd "$ARTEMIS_DIR"
exec "$ADMIN" --host 127.0.0.1 --port "$ARTEMIS_PORT"
