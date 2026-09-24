#!/bin/bash
# Enroll a family member's Android phone for Narad at home, over ADB.
#
#   scripts/enroll_android.sh                       check adb and list the phones it sees
#   scripts/enroll_android.sh --serial SERIAL --profile PROFILE [--label "Phone name"]
#                                                   set up that one phone for that profile
#   add --dry-run to print every command instead of running it
#
# Nothing on a phone is touched until you name its serial: with one phone
# plugged in the script still asks, so a guest's phone is never enrolled by
# accident. For the named phone it
#   1. checks it is connected and authorised (accept the USB-debugging prompt),
#   2. installs or updates the Artemis helper with Artemis's own CLI
#      (`artemis helper install --serial`, which also turns its accessibility
#      service on), when Artemis is installed under NARAD_ARTEMIS_DIR,
#   3. lets the helper run in the background (Doze allowlist and
#      RUN_ANY_IN_BACKGROUND, the parts of "battery: Unrestricted" adb can set),
#   4. grants the phone to the Narad profile (POST /interaction-targets, owner
#      only, with the host token when Narad runs in strict mode),
#   5. prints what only a person can do on the phone.
# Safe to run again: the helper install is an update, the grant an upsert.
# Phones away from home are not reachable yet (ADB needs the same Wi-Fi or a
# cable); a Narad Companion app is future work.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/pilot_env.sh
source "$ROOT/scripts/pilot_env.sh"

HELPER_PACKAGE="com.artemis.helper"
SERIAL=""
PROFILE=""
LABEL=""
DRY_RUN=0

log()  { printf '\033[1;36m[enroll-android]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[enroll-android]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[enroll-android]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --serial) SERIAL="${2:-}"; shift 2 ;;
        --profile) PROFILE="${2:-}"; shift 2 ;;
        --label) LABEL="${2:-}"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h | --help) usage ;;
        *) warn "Unknown option: $1"; usage ;;
    esac
done

# Run a command, or only show it in a dry run.
run() {
    if [ "$DRY_RUN" = 1 ]; then
        printf '  would run:'
        printf ' %q' "$@"
        printf '\n'
        return 0
    fi
    "$@"
}

have_adb() { command -v adb >/dev/null 2>&1; }

if ! have_adb; then
    if [ "$DRY_RUN" = 1 ]; then
        warn "adb is not installed (brew install android-platform-tools); showing the plan anyway."
    else
        die "adb is not installed. Run: brew install android-platform-tools"
    fi
fi

if [ -z "$SERIAL" ]; then
    if have_adb; then
        log "Phones adb can see (serial, state, model):"
        adb devices -l | sed -n '2,$p' | sed '/^$/d' | sed 's/^/  /'
    fi
    cat <<'EOF'

Pick the phone to enroll and run again with its serial and the Narad profile:
  scripts/enroll_android.sh --serial <serial> --profile <profile id> --label "<phone name>"
A phone shown as "unauthorized" needs you to accept the USB-debugging prompt on it.
EOF
    exit 2
fi

case "$SERIAL" in
    *[!A-Za-z0-9._:-]* | "") die "That serial has characters adb never uses: $SERIAL" ;;
esac
[ -n "$PROFILE" ] || die "Name the Narad profile this phone belongs to: --profile <profile id>"
case "$PROFILE" in
    *[!a-z0-9_-]*) die "Profile ids are lowercase letters, digits, - and _: $PROFILE" ;;
esac
[ -n "$LABEL" ] || LABEL="Phone of $PROFILE"

# 1. Connected and authorised.
if [ "$DRY_RUN" = 0 ]; then
    state="$(adb -s "$SERIAL" get-state 2>/dev/null || true)"
    [ "$state" = "device" ] || die "Phone $SERIAL is not ready (state: ${state:-not connected}). Plug it in, unlock it and accept the USB-debugging prompt."
    log "Phone $SERIAL: $(adb -s "$SERIAL" shell getprop ro.product.model 2>/dev/null | tr -d '\r'), Android $(adb -s "$SERIAL" shell getprop ro.build.version.release 2>/dev/null | tr -d '\r')"
else
    log "Dry run for phone $SERIAL (profile $PROFILE, label \"$LABEL\"); nothing is changed."
fi

# 2. The Artemis helper (install or update; it turns its accessibility service on).
ARTEMIS_CLI="$ARTEMIS_DIR/.venv/bin/artemis"
if [ -x "$ARTEMIS_CLI" ] || [ "$DRY_RUN" = 1 ]; then
    log "Installing or updating the Artemis helper on $SERIAL..."
    run "$ARTEMIS_CLI" helper install --serial "$SERIAL" \
        || warn "The helper install did not finish; see the message above, or turn it on by hand: Settings > Accessibility > Artemis Accessibility Helper."
else
    warn "Artemis is not installed under $ARTEMIS_DIR, so the helper was not installed. Install Artemis, then run this again."
fi

# 3. Let the helper run in the background (what adb can grant).
helper_installed() {
    [ "$DRY_RUN" = 1 ] || adb -s "$SERIAL" shell pm list packages "$HELPER_PACKAGE" 2>/dev/null | grep -q "package:$HELPER_PACKAGE"
}
if helper_installed; then
    log "Letting the helper run in the background..."
    run adb -s "$SERIAL" shell dumpsys deviceidle whitelist "+$HELPER_PACKAGE" \
        || warn "Could not add the helper to the Doze allowlist; set its battery use to Unrestricted by hand."
    run adb -s "$SERIAL" shell cmd appops set "$HELPER_PACKAGE" RUN_ANY_IN_BACKGROUND allow \
        || warn "Could not allow background running; set its battery use to Unrestricted by hand."
fi

# 4. Grant the phone to the profile (owner only; the host token in strict mode).
TOKEN_PATH="${NARAD_HOME:-$HOME/.narad}/config/api_token"
BODY="$(printf '{"kind":"artemis","external_id":"%s","label":"%s","profile_id":"%s"}' \
    "$SERIAL" "$(printf '%s' "$LABEL" | sed 's/["\\]//g')" "$PROFILE")"
URL="http://$BACKEND_HOST:$BACKEND_PORT/interaction-targets"
log "Granting $SERIAL to the Narad profile \"$PROFILE\"..."
if [ "$DRY_RUN" = 1 ]; then
    printf '  would POST %s %s\n' "$URL" "$BODY"
elif [ -s "$TOKEN_PATH" ]; then
    curl -fsS --noproxy '*' --max-time 15 -H "Authorization: Bearer $(cat "$TOKEN_PATH")" \
        -H 'Content-Type: application/json' -d "$BODY" "$URL" >/dev/null \
        || die "Narad did not accept the grant. Is Narad running (scripts/install_launchd.sh status) and does profile \"$PROFILE\" exist?"
else
    curl -fsS --noproxy '*' --max-time 15 -H 'Content-Type: application/json' -d "$BODY" "$URL" >/dev/null \
        || die "Narad did not accept the grant. Start Narad on this Mac and run this again."
fi

cat <<EOF

Done with what the Mac can do. Now, on the phone itself:
  1. Settings > Apps > Artemis Accessibility Helper > Battery: Unrestricted.
  2. Autostart: on Xiaomi, Oppo, Vivo, Realme and OnePlus phones, allow the helper to
     start automatically (Settings > Apps > Autostart or Battery > App launch).
  3. Check that Settings > Accessibility shows the helper as On.
  4. Developer options: decide whether USB debugging stays on for the pilot (needed for
     Narad to reach the phone) or is turned off after each session and re-enrolled.
  5. Wireless: for tasks without the cable, the phone and the Mac must be on the same
     Wi-Fi; pair once with Developer options > Wireless debugging > Pair device, then
     'adb pair <ip>:<port>' and 'adb connect <ip>:<port>' on the Mac.
  6. Try it: ask Narad to open an app on "$LABEL", then stop it from the task card.
Away from home the phone cannot be reached yet.
EOF
