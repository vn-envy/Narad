#!/bin/bash
# Shared environment for the Narad family pilot. Source it; don't run it.
#
# Start Family Pilot.command and every launchd job (scripts/install_launchd.sh)
# load this one file, so a double-click start and a supervised start run with
# the same settings. The untracked .env is read first and wins over every
# default below; the family's public address lives only there.

# The variables below are read by the scripts that source this file.
# shellcheck disable=SC2034

NARAD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# NARAD_ENV_FILE points elsewhere only for tests; the pilot uses the repo's .env.
if [ -f "${NARAD_ENV_FILE:-$NARAD_ROOT/.env}" ]; then
    set -a
    # shellcheck disable=SC1090,SC1091
    source "${NARAD_ENV_FILE:-$NARAD_ROOT/.env}"
    set +a
fi

PUBLIC_URL="${NARAD_PUBLIC_URL:-}"
BACKEND_HOST="127.0.0.1"
BACKEND_PORT="${NARAD_PORT:-8000}"
TOKEN_FILE="${NARAD_CLOUDFLARE_TOKEN_FILE:-$HOME/.cloudflared/narad-token}"
FRONTEND_DIR="$NARAD_ROOT/phase-4/frontend"
ARTEMIS_DIR="${NARAD_ARTEMIS_DIR:-$HOME/.narad/integrations/artemis}"
ARTEMIS_PORT="${NARAD_ARTEMIS_PORT:-8124}"
if [ "$(uname -s)" = "Darwin" ]; then
    NARAD_LOG_DIR="${NARAD_LOG_DIR:-$HOME/Library/Logs/Narad}"
else
    NARAD_LOG_DIR="${NARAD_LOG_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/narad/logs}"
fi

NARAD_JOB_PREFIX="com.narad"

# launchd starts jobs with a bare PATH: Homebrew (cloudflared, node) and
# ~/.local/bin must be reachable from a supervised start too.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export NARAD_PORT="$BACKEND_PORT"

export NARAD_AUTH=strict
if [ -n "$PUBLIC_URL" ]; then
    export NARAD_PUBLIC_URL="$PUBLIC_URL"
    export MEDIA_URL_BASE="$PUBLIC_URL/media"
fi
export NARAD_ALLOWED_ORIGINS="${NARAD_ALLOWED_ORIGINS:-${PUBLIC_URL:+$PUBLIC_URL,}http://localhost:5174,http://127.0.0.1:5174}"
export NARAD_ENABLE_DESKTOP_CONTROL="${NARAD_ENABLE_DESKTOP_CONTROL:-1}"
export NARAD_DESKTOP_PROVIDER="${NARAD_DESKTOP_PROVIDER:-cua}"
# Cloud Jev on every browser step costs 0.5-2 s and only adds an advisory
# warning; turn-routing Jev is shadow-only. Both stay off unless asked for.
export NARAD_JEV_COMPUTER_MODE="${NARAD_JEV_COMPUTER_MODE:-off}"
export NARAD_JEV_ROUTE_MODE="${NARAD_JEV_ROUTE_MODE:-off}"
# Owner decision (2026-09-24): Sarvam is trusted for this household (training
# opted out, minimum retention). Speech goes to local or trusted providers only.
export NARAD_PROVIDER_TIERS="${NARAD_PROVIDER_TIERS:-sarvam=trusted}"
# Android admission is local (risk_policy) and Artemis's own verified result
# decides a phone task's outcome; no cloud decision service is asked.
export NARAD_ARTEMIS_URL="${NARAD_ARTEMIS_URL:-http://127.0.0.1:$ARTEMIS_PORT}"
export ARTEMIS_KEEP_DEVICE_AWAKE="${ARTEMIS_KEEP_DEVICE_AWAKE:-false}"
export ARTEMIS_HELPER_AUTO_INSTALL="${ARTEMIS_HELPER_AUTO_INSTALL:-false}"
# Vendor CLIs must not self-update or send telemetry from the pilot host.
export BSK_AUTO_UPDATE="${BSK_AUTO_UPDATE:-off}"
export CUA_DRIVER_RS_TELEMETRY_ENABLED="${CUA_DRIVER_RS_TELEMETRY_ENABLED:-false}"
export CUA_TELEMETRY_ENABLED="${CUA_TELEMETRY_ENABLED:-false}"

# True when launchd has the named Narad job (backend, tunnel, ...) loaded.
narad_job_loaded() {
    command -v launchctl >/dev/null 2>&1 \
        && launchctl print "gui/$(id -u)/$NARAD_JOB_PREFIX.$1" >/dev/null 2>&1
}

# Build the web app when it is missing or older than its sources.
narad_build_frontend() {
    if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
        echo "[narad] Installing frontend dependencies..."
        (cd "$FRONTEND_DIR" && npm ci) || return 1
    fi
    local needs_build=0
    [ -f "$FRONTEND_DIR/dist/index.html" ] || needs_build=1
    if [ "$needs_build" -eq 0 ] && find "$FRONTEND_DIR/src" "$FRONTEND_DIR/public" \
        -type f -newer "$FRONTEND_DIR/dist/index.html" -print -quit 2>/dev/null | grep -q .; then
        needs_build=1
    fi
    if [ "$needs_build" -eq 1 ]; then
        echo "[narad] Building the latest Narad interface..."
        (cd "$FRONTEND_DIR" && npm run build) || return 1
    fi
    return 0
}
