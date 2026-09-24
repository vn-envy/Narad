#!/bin/bash
# Publish the loopback backend at NARAD_PUBLIC_URL through the outbound
# Cloudflare tunnel. launchd keeps it alive (com.narad.tunnel); Start Family
# Pilot.command runs it in the background. exec keeps a single PID.
set -euo pipefail

# shellcheck source=scripts/pilot_env.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pilot_env.sh"

if ! command -v cloudflared >/dev/null 2>&1; then
    echo "[narad] cloudflared is not installed. Run: brew install cloudflared" >&2
    exit 78  # EX_CONFIG
fi
if [ ! -s "$TOKEN_FILE" ]; then
    echo "[narad] Cloudflare tunnel credential is missing: $TOKEN_FILE" >&2
    exit 78
fi

# --no-autoupdate: vendor tools on the pilot host never update themselves.
exec cloudflared tunnel --no-autoupdate run \
    --url "http://$BACKEND_HOST:$BACKEND_PORT" \
    --token-file "$TOKEN_FILE"
