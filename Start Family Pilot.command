#!/bin/bash
# Start Narad securely at your NARAD_PUBLIC_URL through Cloudflare Tunnel.
# The family's public address lives only in .env (untracked), never in git.
# For an always-on host, let launchd supervise it instead:
#   scripts/install_launchd.sh install

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# .env, the pilot defaults and shared helpers. The launchd jobs load the same
# file, so a double-click start and a supervised start behave the same.
# shellcheck source=scripts/pilot_env.sh
source "$ROOT/scripts/pilot_env.sh"

log()  { printf '\033[1;36m[family-pilot]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[family-pilot]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[family-pilot]\033[0m %s\n' "$*" >&2; exit 1; }

if narad_job_loaded backend; then
    OPEN_URL="${PUBLIC_URL:-http://$BACKEND_HOST:$BACKEND_PORT}"
    log "launchd already runs Narad (scripts/install_launchd.sh status). Opening $OPEN_URL"
    open "$OPEN_URL" 2>/dev/null || true
    exit 0
fi

command -v cloudflared >/dev/null 2>&1 || die "cloudflared is not installed. Run: brew install cloudflared"
[ -s "$TOKEN_FILE" ] || die "Cloudflare tunnel credential is missing: $TOKEN_FILE"
[ -x "$ROOT/.venv/bin/python" ] || die "Run Start Narad.command once to install Narad first."

[ -n "$PUBLIC_URL" ] || die "Set NARAD_PUBLIC_URL=https://<your-narad-host> in $ROOT/.env (kept out of git)."

if [ -z "${NARAD_CF_ACCESS_TEAM_DOMAIN:-}" ] || [ -z "${NARAD_CF_ACCESS_AUD:-}" ]; then
    warn "Cloudflare Access verification is off: set NARAD_CF_ACCESS_TEAM_DOMAIN and NARAD_CF_ACCESS_AUD in $ROOT/.env (see README, Family access with Cloudflare Access)."
fi

narad_build_frontend || die "The Narad interface did not build; see the npm output above."

if lsof -nP -iTCP:"$BACKEND_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    die "Port $BACKEND_PORT is already in use. Stop the existing Narad server and retry."
fi

BACKEND_PID=""
TUNNEL_PID=""
CAFFEINATE_PID=""
ARTEMIS_PID=""

cleanup() {
    trap - EXIT INT TERM
    [ -n "$TUNNEL_PID" ] && kill "$TUNNEL_PID" >/dev/null 2>&1 || true
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" >/dev/null 2>&1 || true
    [ -n "$ARTEMIS_PID" ] && kill "$ARTEMIS_PID" >/dev/null 2>&1 || true
    [ -n "$CAFFEINATE_PID" ] && kill "$CAFFEINATE_PID" >/dev/null 2>&1 || true
    wait >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if command -v cua-driver >/dev/null 2>&1; then
    # The daemon runs every desktop action and reads its own environment and
    # ~/.cua-driver/config.json, not this shell's exports. Persist the opt-out
    # so it holds however the daemon starts, including one already running.
    cua-driver telemetry disable >/dev/null 2>&1 \
        || warn "Could not persist the Cua Driver telemetry opt-out; run: cua-driver telemetry disable"
    if ! cua-driver status 2>/dev/null | grep -qi "daemon is running"; then
        log "Starting the local Cua Driver..."
        open -n -g -a CuaDriver \
            --env CUA_DRIVER_RS_TELEMETRY_ENABLED=false --env CUA_TELEMETRY_ENABLED=false \
            --args serve >/dev/null 2>&1 || warn "Cua Driver needs a manual start."
    fi
else
    warn "Cua Driver is not installed; desktop control will stay unavailable."
fi

if [ "${NARAD_DISABLE_ARTEMIS:-0}" != "1" ]; then
    if curl -fsS "$NARAD_ARTEMIS_URL/api/status" >/dev/null 2>&1; then
        log "Using the running Artemis Android service."
    elif [ -x "$ARTEMIS_DIR/.venv/bin/artemis-admin" ]; then
        log "Starting the local Artemis Android service..."
        (
            cd "$ARTEMIS_DIR"
            exec "$ARTEMIS_DIR/.venv/bin/artemis-admin" --host 127.0.0.1 --port "$ARTEMIS_PORT"
        ) >"$HOME/.narad/artemis.log" 2>&1 &
        ARTEMIS_PID=$!
        for i in $(seq 1 40); do
            curl -fsS "$NARAD_ARTEMIS_URL/api/status" >/dev/null 2>&1 && break
            kill -0 "$ARTEMIS_PID" >/dev/null 2>&1 || break
            sleep 0.5
        done
        curl -fsS "$NARAD_ARTEMIS_URL/api/status" >/dev/null 2>&1 \
            || warn "Artemis did not become ready; see ~/.narad/artemis.log."
    else
        warn "Artemis is not installed under $ARTEMIS_DIR; Android control will stay unavailable."
    fi
fi

log "Starting Narad in strict family-profile mode..."
"$ROOT/scripts/run_backend.sh" &
BACKEND_PID=$!

for i in $(seq 1 90); do
    if ! kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
        die "Narad stopped during startup."
    fi
    if curl -fsS "http://$BACKEND_HOST:$BACKEND_PORT/health" >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
    [ "$i" -eq 90 ] && die "Narad did not become healthy within 45 seconds."
done

log "Publishing $PUBLIC_URL through the outbound Cloudflare tunnel..."
"$ROOT/scripts/run_tunnel.sh" &
TUNNEL_PID=$!

# An always-on family pilot must not disappear when the Mac idles.
caffeinate -i -w $$ >/dev/null 2>&1 &
CAFFEINATE_PID=$!

sleep 2
kill -0 "$TUNNEL_PID" >/dev/null 2>&1 || die "Cloudflare Tunnel stopped during startup."

log "Narad is live at $PUBLIC_URL"
log "Keep this window open. Press Ctrl-C to stop the private family pilot."
# The owner PIN can only be set from the host itself, never through the tunnel.
OPEN_URL="$PUBLIC_URL"
if curl -fsS "http://$BACKEND_HOST:$BACKEND_PORT/profiles" 2>/dev/null | "$ROOT/.venv/bin/python" -c '
import json, sys
profiles = json.load(sys.stdin).get("profiles", [])
sys.exit(0 if any(p.get("is_owner") and not p.get("has_pin") for p in profiles) else 1)
' 2>/dev/null; then
    OPEN_URL="http://$BACKEND_HOST:$BACKEND_PORT"
    log "First run: choose the owner PIN at $OPEN_URL on this Mac, then use $PUBLIC_URL anywhere."
fi
open "$OPEN_URL" 2>/dev/null || true

while kill -0 "$BACKEND_PID" >/dev/null 2>&1 && kill -0 "$TUNNEL_PID" >/dev/null 2>&1; do
    sleep 2
done

warn "A Narad service stopped unexpectedly; shutting down the pilot."
exit 1
