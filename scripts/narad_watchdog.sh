#!/bin/bash
# Narad watchdog. launchd runs it every 2 minutes (com.narad.watchdog); each
# run is one quick check and exits, never a loop, so the fanless host idles.
#
# It GETs http://127.0.0.1:$NARAD_PORT/health. After NARAD_WATCHDOG_FAILURES
# (default 3) failures in a row it restarts the backend job with
# `launchctl kickstart -k`, then starts counting again, so restarts are at
# least three checks apart. What it does goes to $NARAD_LOG_DIR/watchdog.log,
# and each restart also to NARAD_HOME/ops/watchdog.jsonl for the scorecard.
#
# The optional sidecars get the same treatment when launchd runs them: Artemis
# (com.narad.artemis) must answer GET $NARAD_ARTEMIS_URL/api/status and Cua
# Driver (com.narad.cua-driver) must report "daemon is running"; each has its
# own failure count (watchdog.<job>.failures) and is kickstarted on its own.
#
# It also rotates the Narad logs: a *.log over NARAD_LOG_MAX_BYTES (10 MiB) is
# copied to .1 (the old .1 becomes .2) and truncated in place, which is safe
# because launchd appends to these files.
set -uo pipefail

# shellcheck source=scripts/pilot_env.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pilot_env.sh"

LABEL="$NARAD_JOB_PREFIX.backend"
LIMIT="${NARAD_WATCHDOG_FAILURES:-3}"
MAX_BYTES="${NARAD_LOG_MAX_BYTES:-10485760}"
OPS_DIR="${NARAD_HOME:-$HOME/.narad}/ops"
STATE="$OPS_DIR/watchdog.failures"
mkdir -p "$NARAD_LOG_DIR" "$OPS_DIR"

note() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >>"$NARAD_LOG_DIR/watchdog.log"; }

read_count() {  # read_count [STATE_FILE]
    local value
    value="$(cat "${1:-$STATE}" 2>/dev/null || true)"
    case "$value" in
        '' | *[!0-9]*) echo 0 ;;
        *) echo "$value" ;;
    esac
}

rotate_logs() {
    local file size
    for file in "$NARAD_LOG_DIR"/*.log; do
        [ -f "$file" ] || continue
        size="$(wc -c <"$file" | tr -d ' ')"
        if [ "$size" -gt "$MAX_BYTES" ]; then
            [ -f "$file.1" ] && mv -f "$file.1" "$file.2"
            cp -p "$file" "$file.1" && : >"$file"
            note "rotated $(basename "$file") at $size bytes"
        fi
    done
}

record_restart() {  # record_restart LABEL FAILURES OK
    printf '{"t":%s,"ts":"%s","action":"kickstart","label":"%s","failures":%d,"ok":%s}\n' \
        "$(date +%s)" "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$1" "$2" "$3" >>"$OPS_DIR/watchdog.jsonl"
}

# check_sidecar JOB HEALTH_COMMAND...: only when launchd runs the job.
check_sidecar() {
    local job="$1" state="$OPS_DIR/watchdog.$1.failures" label="$NARAD_JOB_PREFIX.$1" count ok
    shift
    narad_job_loaded "$job" || return 0
    if "$@" >/dev/null 2>&1; then
        count="$(read_count "$state")"
        [ "$count" -gt 0 ] && note "$job healthy again after $count failed check(s)"
        echo 0 >"$state"
        return 0
    fi
    count=$(($(read_count "$state") + 1))
    if [ "$count" -lt "$LIMIT" ]; then
        echo "$count" >"$state"
        note "$job check failed ($count/$LIMIT)"
        return 0
    fi
    echo 0 >"$state"
    note "$job check failed $count times in a row; restarting $label"
    if launchctl kickstart -k "gui/$(id -u)/$label" >>"$NARAD_LOG_DIR/watchdog.log" 2>&1; then
        ok=true
    else
        ok=false
        note "launchctl kickstart of $label failed"
    fi
    record_restart "$label" "$count" "$ok"
}

artemis_healthy() {
    curl -fsS --noproxy '*' --max-time 10 -o /dev/null "$NARAD_ARTEMIS_URL/api/status"
}

cua_driver_healthy() {
    cua-driver status 2>/dev/null | grep -qi "daemon is running"
}

rotate_logs
check_sidecar artemis artemis_healthy
check_sidecar cua-driver cua_driver_healthy

if curl -fsS --noproxy '*' --max-time 10 -o /dev/null "http://$BACKEND_HOST:$BACKEND_PORT/health" 2>/dev/null; then
    previous="$(read_count)"
    [ "$previous" -gt 0 ] && note "healthy again after $previous failed check(s)"
    echo 0 >"$STATE"
    exit 0
fi

failures=$(($(read_count) + 1))
if [ "$failures" -lt "$LIMIT" ]; then
    echo "$failures" >"$STATE"
    note "health check failed ($failures/$LIMIT)"
    exit 0
fi

echo 0 >"$STATE"
if ! command -v launchctl >/dev/null 2>&1; then
    note "health check failed $failures times; launchctl is not available here, nothing restarted"
    exit 0
fi
if ! narad_job_loaded backend; then
    note "health check failed $failures times; $LABEL is not loaded, nothing to restart"
    exit 0
fi

note "health check failed $failures times in a row; restarting $LABEL"
if launchctl kickstart -k "gui/$(id -u)/$LABEL" >>"$NARAD_LOG_DIR/watchdog.log" 2>&1; then
    ok=true
    note "restart requested"
else
    ok=false
    note "launchctl kickstart failed"
fi
record_restart "$LABEL" "$failures" "$ok"
exit 0
