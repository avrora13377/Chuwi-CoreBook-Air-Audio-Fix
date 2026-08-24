#!/bin/bash
# speaker-restore.sh — CoreBook Air Plus internal speakers: full boot-time restore.
#   1) SmartPA bring-up through the codec's vendor bridge
#   2) hold GPIO4 (PA enable) high
#   3) make sure the desktop sound server is not muting the speaker DACs
# Designed to be run by corebook-speakers.service (root). Safe to re-run anytime,
# including after suspend/resume.

LOG_TAG="corebook-speakers"
log() { echo "$(date '+%F %T') $LOG_TAG: $*"; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRINGUP="$HERE/pa-bringup.py"
[ -f "$BRINGUP" ] || BRINGUP=/usr/local/lib/corebook-audio/pa-bringup.py

# --- 1) PA bring-up ----------------------------------------------------------
if [ -f "$BRINGUP" ]; then
    /usr/bin/python3 "$BRINGUP"
    rc=$?
    log "bring-up rc=$rc"
else
    log "ERROR: pa-bringup.py not found"; rc=1
fi

# --- 2) GPIO4 (PA enable, per INF GpioNumberForPA=4) -------------------------
if command -v gpioset >/dev/null 2>&1; then
    pkill -f "gpioset.*--chip=0.*--daemon" 2>/dev/null; sleep 0.3
    if gpioset --chip=0 --daemon "4=1" 2>/dev/null; then
        log "GPIO4 held high"
    else
        # already held? then we're done anyway
        if gpioget --chip=0 4 >/dev/null 2>&1; then
            log "WARNING: could not claim GPIO4 (and it is not held by us)"
        else
            log "GPIO4 already held (busy = someone holds it, fine)"
        fi
    fi
else
    log "WARNING: gpioset not found (install libgpiod tools); GPIO4 not driven"
fi

# --- 3) unmute every desktop session's default sink --------------------------
# WirePlumber caches its sink state; after our bring-up touches the DAC amps
# behind its back, a plain "unmute" is treated as already-done and never pushed.
# The mute TOGGLE forces it to re-apply values onto the speaker DACs.
found=0
for d in /run/user/*; do
    u="${d##*/}"
    case "$u" in ''|*[!0-9]*) continue;; esac
    user="$(id -nu "$u" 2>/dev/null)" || continue
    for i in $(seq 1 30); do
        if runuser -u "$user" -- env XDG_RUNTIME_DIR="$d" \
            wpctl set-mute @DEFAULT_AUDIO_SINK@ 1 >/dev/null 2>&1 \
        && sleep 0.2 \
        && runuser -u "$user" -- env XDG_RUNTIME_DIR="$d" \
            wpctl set-mute @DEFAULT_AUDIO_SINK@ 0 >/dev/null 2>&1 \
        && runuser -u "$user" -- env XDG_RUNTIME_DIR="$d" \
            wpctl set-volume @DEFAULT_AUDIO_SINK@ 1.0 >/dev/null 2>&1; then
            log "sink resynced for uid $u ($user)"
            found=1
            break
        fi
        sleep 1
    done
done
[ "$found" = 0 ] && log "no graphical session found to unmute (headless boot?)"

exit $rc
