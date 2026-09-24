#!/bin/bash
# Start Narad securely at your NARAD_PUBLIC_URL through Cloudflare Tunnel.
# The family's public address lives only in .env (untracked), never in git.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [ -f "$ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT/.env"
    set +a
fi

PUBLIC_URL="${NARAD_PUBLIC_URL:-}"
BACKEND_HOST="127.0.0.1"
BACKEND_PORT="${NARAD_PORT:-8000}"
TOKEN_FILE="${NARAD_CLOUDFLARE_TOKEN_FILE:-$HOME/.cloudflared/narad-token}"
FRONTEND_DIR="$ROOT/phase-4/frontend"
ARTEMIS_DIR="${NARAD_ARTEMIS_DIR:-$HOME/.narad/integrations/artemis}"
ARTEMIS_PORT="${NARAD_ARTEMIS_PORT:-8124}"

log()  { printf '\033[1;36m[family-pilot]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[family-pilot]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[family-pilot]\033[0m %s\n' "$*" >&2; exit 1; }

command -v cloudflared >/dev/null 2>&1 || die "cloudflared is not installed. Run: brew install cloudflared"
[ -s "$TOKEN_FILE" ] || die "Cloudflare tunnel credential is missing: $TOKEN_FILE"
[ -x "$ROOT/.venv/bin/python" ] || die "Run Start Narad.command once to install Narad first."

[ -n "$PUBLIC_URL" ] || die "Set NARAD_PUBLIC_URL=https://<your-narad-host> in $ROOT/.env (kept out of git)."

export PATH="$HOME/.local/bin:$PATH"

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    log "Installing frontend dependencies..."
    (cd "$FRONTEND_DIR" && npm ci)
fi

NEEDS_BUILD=0
[ -f "$FRONTEND_DIR/dist/index.html" ] || NEEDS_BUILD=1
if [ "$NEEDS_BUILD" -eq 0 ] && find "$FRONTEND_DIR/src" "$FRONTEND_DIR/public" \
    -type f -newer "$FRONTEND_DIR/dist/index.html" -print -quit 2>/dev/null | grep -q .; then
    NEEDS_BUILD=1
fi
if [ "$NEEDS_BUILD" -eq 1 ]; then
    log "Building the latest Narad interface..."
    (cd "$FRONTEND_DIR" && npm run build)
fi

if lsof -nP -iTCP:"$BACKEND_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    die "Port $BACKEND_PORT is already in use. Stop the existing Narad server and retry."
fi

export NARAD_AUTH=strict
export NARAD_PUBLIC_URL="$PUBLIC_URL"
export MEDIA_URL_BASE="$PUBLIC_URL/media"
export NARAD_ALLOWED_ORIGINS="${NARAD_ALLOWED_ORIGINS:-$PUBLIC_URL,http://localhost:5174,http://127.0.0.1:5174}"
export NARAD_ENABLE_DESKTOP_CONTROL="${NARAD_ENABLE_DESKTOP_CONTROL:-1}"
export NARAD_DESKTOP_PROVIDER="${NARAD_DESKTOP_PROVIDER:-cua}"
# Cloud Jev on every browser step costs 0.5-2 s and only adds an advisory
# warning; turn-routing Jev is shadow-only. Both stay off unless asked for.
export NARAD_JEV_COMPUTER_MODE="${NARAD_JEV_COMPUTER_MODE:-off}"
export NARAD_JEV_ROUTE_MODE="${NARAD_JEV_ROUTE_MODE:-off}"
# Owner decision (2026-09-24): Sarvam is trusted for this household (training
# opted out, minimum retention). Speech goes to local or trusted providers only.
export NARAD_PROVIDER_TIERS="${NARAD_PROVIDER_TIERS:-sarvam=trusted}"
export NARAD_JEV_PHONE_MODE="${NARAD_JEV_PHONE_MODE:-active}"
export NARAD_ARTEMIS_URL="${NARAD_ARTEMIS_URL:-http://127.0.0.1:$ARTEMIS_PORT}"
export ARTEMIS_KEEP_DEVICE_AWAKE="${ARTEMIS_KEEP_DEVICE_AWAKE:-false}"
export ARTEMIS_HELPER_AUTO_INSTALL="${ARTEMIS_HELPER_AUTO_INSTALL:-false}"
# Vendor CLIs must not self-update or send telemetry from the pilot host.
export BSK_AUTO_UPDATE="${BSK_AUTO_UPDATE:-off}"
export CUA_DRIVER_RS_TELEMETRY_ENABLED="${CUA_DRIVER_RS_TELEMETRY_ENABLED:-false}"
export CUA_TELEMETRY_ENABLED="${CUA_TELEMETRY_ENABLED:-false}"

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
"$ROOT/.venv/bin/python" narad_server_entry.py \
    --host "$BACKEND_HOST" --port "$BACKEND_PORT" &
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
cloudflared tunnel run \
    --url "http://$BACKEND_HOST:$BACKEND_PORT" \
    --token-file "$TOKEN_FILE" &
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
