#!/bin/bash
# Supervise the Narad family pilot with per-user launchd jobs. No sudo.
#
#   scripts/install_launchd.sh install     install (or update) and load the jobs
#   scripts/install_launchd.sh uninstall   stop and remove them; data, backups and logs stay
#   scripts/install_launchd.sh status      jobs, health, backups, drill, power settings
#   scripts/install_launchd.sh render DIR  only write the filled-in plists to DIR (any OS)
#
# Jobs (templates in scripts/launchd/, copies in ~/Library/LaunchAgents):
#   com.narad.backend   API and web app; restarted by launchd if it exits
#   com.narad.tunnel    cloudflared, restarted if it exits; installed only when
#                       cloudflared, the token file and NARAD_PUBLIC_URL exist
#   com.narad.watchdog  every 2 min: /health; restarts the backend after 3 failures in a row
#   com.narad.uptime    every 5 min: scripts/uptime_ping.py
#   com.narad.backup    daily 03:30: scripts/narad_backup.py backup
#   com.narad.drill     Sundays 04:30: scripts/narad_backup.py drill
#   com.narad.awake     caffeinate -s: no system sleep while on the charger
#   com.narad.cua-driver  cua-driver serve (desktop tasks), telemetry off; installed only
#                       when cua-driver is installed and no other LaunchAgent runs it
#   com.narad.artemis   the Artemis Android service on 127.0.0.1; installed only when
#                       Artemis is under NARAD_ARTEMIS_DIR and NARAD_ARTEMIS_URL is this Mac
#
# Safe to run again: unchanged jobs keep running, changed ones are reloaded.
# Settings come from .env through scripts/pilot_env.sh, exactly as for
# Start Family Pilot.command. Logs: ~/Library/Logs/Narad. LaunchAgents run
# while you are logged in, so after a restart, log in to the Mac once.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/pilot_env.sh
source "$ROOT/scripts/pilot_env.sh"

TEMPLATES="$ROOT/scripts/launchd"
AGENTS_DIR="${NARAD_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
JOBS=(backend tunnel watchdog uptime backup drill awake cua-driver artemis)
DOMAIN="gui/$(id -u)"
if [ "$(uname -s)" = "Darwin" ]; then
    KEY_FILE="${NARAD_BACKUP_KEY_FILE:-$HOME/Library/Application Support/Narad/backup.key}"
else
    KEY_FILE="${NARAD_BACKUP_KEY_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/narad/backup.key}"
fi

log()  { printf '\033[1;36m[narad-launchd]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[narad-launchd]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[narad-launchd]\033[0m %s\n' "$*" >&2; exit 1; }

is_macos() { [ "$(uname -s)" = "Darwin" ]; }
label_of() { echo "$NARAD_JOB_PREFIX.$1"; }
loaded() { launchctl print "$DOMAIN/$(label_of "$1")" >/dev/null 2>&1; }

# Escape a value for an XML text node, then for a sed replacement (| delimiter).
fill() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/[|&\\]/\\&/g'; }

# The cua-driver binary inside CuaDriver.app (the ~/.local/bin link resolved),
# so launchd starts the app's own code; a stand-in path where it is missing.
cua_driver_bin() {
    local found
    found="$(command -v cua-driver 2>/dev/null || true)"
    [ -n "$found" ] || { [ -x "$HOME/.local/bin/cua-driver" ] && found="$HOME/.local/bin/cua-driver"; }
    if [ -z "$found" ]; then
        echo "/Applications/CuaDriver.app/Contents/MacOS/cua-driver"
        return 1
    fi
    readlink -f "$found" 2>/dev/null || echo "$found"
}

render_job() {  # render_job JOB OUTPUT_FILE
    local cua
    cua="$(cua_driver_bin || true)"
    sed -e "s|@ROOT@|$(fill "$ROOT")|g" \
        -e "s|@HOME@|$(fill "$HOME")|g" \
        -e "s|@LOG_DIR@|$(fill "$NARAD_LOG_DIR")|g" \
        -e "s|@CUA_DRIVER_BIN@|$(fill "$cua")|g" \
        "$TEMPLATES/$(label_of "$1").plist" >"$2"
    if command -v plutil >/dev/null 2>&1; then
        plutil -lint -s "$2" >/dev/null || die "Rendered $(label_of "$1") is not a valid plist."
    fi
}

tunnel_ready() {
    command -v cloudflared >/dev/null 2>&1 && [ -s "$TOKEN_FILE" ] && [ -n "$PUBLIC_URL" ]
}

# Why the optional sidecar jobs are not installed ("" when they should be).
cua_driver_skip() {
    cua_driver_bin >/dev/null || { echo "cua-driver is not installed"; return; }
    if ls "$AGENTS_DIR"/com.trycua.*.plist >/dev/null 2>&1; then
        echo "Cua's own LaunchAgent already runs it"
        return
    fi
    if ! loaded cua-driver && cua-driver status 2>/dev/null | grep -qi "daemon is running"; then
        echo "a Cua Driver daemon started outside launchd is running; quit CuaDriver and install again"
    fi
}

artemis_skip() {
    [ "${NARAD_DISABLE_ARTEMIS:-0}" != "1" ] || { echo "NARAD_DISABLE_ARTEMIS=1"; return; }
    [ -x "$ARTEMIS_DIR/.venv/bin/artemis-admin" ] || { echo "Artemis is not installed under $ARTEMIS_DIR"; return; }
    [ "$NARAD_ARTEMIS_URL" = "http://127.0.0.1:$ARTEMIS_PORT" ] \
        || echo "NARAD_ARTEMIS_URL points elsewhere ($NARAD_ARTEMIS_URL)"
}

wait_unloaded() {
    local _
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        loaded "$1" || return 0
        sleep 0.5
    done
}

install_job() {
    local job="$1" label target rendered
    label="$(label_of "$job")"
    target="$AGENTS_DIR/$label.plist"
    rendered="$(mktemp "${TMPDIR:-/tmp}/narad-$job.XXXXXX")"
    render_job "$job" "$rendered"
    if [ -f "$target" ] && cmp -s "$rendered" "$target" && loaded "$job"; then
        rm -f "$rendered"
        log "$label unchanged, running"
        return
    fi
    if loaded "$job"; then
        launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
        wait_unloaded "$job"
    fi
    mv -f "$rendered" "$target"
    chmod 644 "$target"
    launchctl enable "$DOMAIN/$label" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$target" || die "launchctl could not load $label; see $NARAD_LOG_DIR."
    log "$label loaded"
}

remove_job() {  # remove_job JOB [REASON]
    local job="$1" reason="${2:-removed}" label target
    label="$(label_of "$job")"
    target="$AGENTS_DIR/$label.plist"
    if ! loaded "$job" && [ ! -f "$target" ]; then
        [ "$reason" = "removed" ] || log "$label $reason"
        return
    fi
    if loaded "$job"; then
        launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
        wait_unloaded "$job"
    fi
    rm -f "$target"
    log "$label $reason"
}

print_power_advice() {
    cat <<'EOF'

Keep the host awake on the charger. These need your password, so run them yourself:
  sudo pmset -c sleep 0          # never sleep on the charger (lid open)
  sudo pmset -c disksleep 0
  sudo pmset -c displaysleep 10  # the screen may sleep; Narad keeps running
  sudo pmset -c womp 1           # wake for network access
  sudo pmset -c tcpkeepalive 1
To keep serving with the lid closed as well (a MacBook sleeps on lid close by default):
  sudo pmset -a disablesleep 1   # applies on battery too; undo before travelling:
                                 #   sudo pmset -a disablesleep 0
Check with: pmset -g
The Mac is fanless: give it a hard, open surface. The lid open sheds heat best
under sustained load; closed is fine for a mostly idle host.
EOF
}

cmd_install() {
    is_macos || die "launchd is macOS-only. On this system, run scripts/run_backend.sh under your own supervisor."
    [ -x "$ROOT/.venv/bin/python" ] || die "Run Start Narad.command once to install Narad first."
    "$ROOT/.venv/bin/python" -c "import cryptography" 2>/dev/null \
        || die "The backup encryption library is missing: $ROOT/.venv/bin/pip install -e \"$ROOT\""
    if ! loaded backend && lsof -nP -iTCP:"$BACKEND_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
        die "Port $BACKEND_PORT is in use. Stop Start Family Pilot.command (Ctrl-C in its window) and retry."
    fi
    [ -n "$PUBLIC_URL" ] || warn "NARAD_PUBLIC_URL is not set in $ROOT/.env: Narad will serve this Mac only."
    narad_build_frontend || die "The web app did not build; see the npm output above."
    mkdir -p "$AGENTS_DIR" "$NARAD_LOG_DIR"
    chmod 700 "$NARAD_LOG_DIR"

    local why
    for job in "${JOBS[@]}"; do
        if [ "$job" = tunnel ] && ! tunnel_ready; then
            remove_job tunnel "skipped: needs cloudflared, $TOKEN_FILE and NARAD_PUBLIC_URL"
            continue
        fi
        if [ "$job" = cua-driver ]; then
            why="$(cua_driver_skip)"
            if [ -n "$why" ]; then
                remove_job cua-driver "skipped: $why"
                continue
            fi
            # Persist the opt-out for the daemon however it starts, not only in this job's environment.
            cua-driver telemetry disable >/dev/null 2>&1 \
                || warn "Could not persist the Cua Driver telemetry opt-out; run: cua-driver telemetry disable"
        fi
        if [ "$job" = artemis ]; then
            why="$(artemis_skip)"
            if [ -n "$why" ]; then
                remove_job artemis "skipped: $why"
                continue
            fi
        fi
        install_job "$job"
    done

    if [ ! -f "$KEY_FILE" ]; then
        log "Making the first encrypted backup now, so the backup key is created while you watch..."
        "$ROOT/scripts/run_job.sh" "$ROOT/scripts/narad_backup.py" backup \
            || warn "The first backup failed; see the message above. The nightly job will retry."
    fi
    log "Done. Logs: $NARAD_LOG_DIR   Status: scripts/install_launchd.sh status"
    print_power_advice
}

cmd_uninstall() {
    is_macos || die "launchd is macOS-only; there is nothing to uninstall on this system."
    # The watchdog goes first so it cannot restart the backend mid-removal.
    for job in watchdog uptime backup drill awake tunnel artemis cua-driver backend; do
        remove_job "$job"
    done
    log "Jobs removed. ~/.narad, your backups and $NARAD_LOG_DIR are untouched."
    log "Start Family Pilot.command still works for a manual start."
}

job_line() {
    local job="$1" label info state pid code
    label="$(label_of "$job")"
    if ! loaded "$job"; then
        printf '  %-20s not installed\n' "$label"
        return
    fi
    info="$(launchctl print "$DOMAIN/$label" 2>/dev/null || true)"
    state="$(printf '%s\n' "$info" | sed -n 's/^[[:space:]]*state = //p' | head -n 1)"
    pid="$(printf '%s\n' "$info" | sed -n 's/^[[:space:]]*pid = //p' | head -n 1)"
    code="$(printf '%s\n' "$info" | sed -n 's/^[[:space:]]*last exit code = //p' | head -n 1)"
    printf '  %-20s %s%s, last exit %s\n' "$label" "${state:-loaded}" "${pid:+ (pid $pid)}" "${code:-n/a}"
}

cmd_status() {
    local ops="${NARAD_HOME:-$HOME/.narad}/ops"
    echo "Jobs:"
    if is_macos; then
        for job in "${JOBS[@]}"; do job_line "$job"; done
    else
        echo "  launchd is macOS-only; no jobs on this system."
    fi
    echo "Health:"
    if curl -fsS --noproxy '*' --max-time 5 -o /dev/null "http://$BACKEND_HOST:$BACKEND_PORT/health" 2>/dev/null; then
        echo "  http://$BACKEND_HOST:$BACKEND_PORT/health is answering"
    else
        echo "  http://$BACKEND_HOST:$BACKEND_PORT/health is NOT answering"
    fi
    if [ -n "$(artemis_skip)" ]; then
        echo "  Artemis: not set up ($(artemis_skip))"
    elif curl -fsS --noproxy '*' --max-time 5 -o /dev/null "$NARAD_ARTEMIS_URL/api/status" 2>/dev/null; then
        echo "  Artemis ($NARAD_ARTEMIS_URL) is answering"
    else
        echo "  Artemis ($NARAD_ARTEMIS_URL) is NOT answering"
    fi
    if ! cua_driver_bin >/dev/null; then
        echo "  Cua Driver: not installed"
    elif cua-driver status 2>/dev/null | grep -qi "daemon is running"; then
        echo "  Cua Driver daemon is running"
    else
        echo "  Cua Driver daemon is NOT running"
    fi
    echo "Last uptime check:"
    tail -n 1 "$ops/uptime.jsonl" 2>/dev/null | sed 's/^/  /' || true
    [ -f "$ops/uptime.jsonl" ] || echo "  none yet"
    echo "Backups:"
    if [ -x "$ROOT/.venv/bin/python" ]; then
        { "$ROOT/scripts/run_job.sh" "$ROOT/scripts/narad_backup.py" list 2>&1 || true; } | head -n 5 | sed 's/^/  /'
    fi
    if [ -f "$KEY_FILE" ]; then
        echo "  key: $KEY_FILE"
    else
        echo "  key: not created yet (the first backup creates it)"
    fi
    echo "Last restore drill:"
    tail -n 1 "$ops/backup_drill.jsonl" 2>/dev/null | sed 's/^/  /' || true
    [ -f "$ops/backup_drill.jsonl" ] || echo "  none yet"
    if is_macos; then
        echo "Power (pmset -g):"
        pmset -g 2>/dev/null | grep -E '^[[:space:]]*(sleep|disksleep|displaysleep|womp|tcpkeepalive|SleepDisabled)[[:space:]]' | sed 's/^/ /' || true
    fi
    echo "Logs: $NARAD_LOG_DIR"
}

cmd_render() {
    local out="${1:-}"
    [ -n "$out" ] || die "usage: scripts/install_launchd.sh render DIR"
    mkdir -p "$out"
    for job in "${JOBS[@]}"; do
        render_job "$job" "$out/$(label_of "$job").plist"
    done
    log "Rendered ${#JOBS[@]} plists into $out"
}

case "${1:-}" in
    install) cmd_install ;;
    uninstall) cmd_uninstall ;;
    status) cmd_status ;;
    render) cmd_render "${2:-}" ;;
    *)
        sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
        exit 2
        ;;
esac
