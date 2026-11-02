#!/bin/bash
# speaker-restore.sh — CoreBook Air Plus internal speakers: full boot/resume restore.
#   0) wait until the Conexant codec exists
#   1) hold GPIO4 (PA enable) high  -- BEFORE the bring-up, never dropped afterwards
#   2) SmartPA bring-up through the codec's vendor bridge (retried up to 3x)
#   3) make sure the desktop sound server is not muting the speaker DACs
# Safe to re-run anytime. Settings can be put in /etc/default/corebook-audio.

LOG_TAG="corebook-speakers"
log() { echo "$(date '+%F %T') $LOG_TAG: $*"; }

if [ -r /etc/default/corebook-audio ]; then set -a; . /etc/default/corebook-audio; set +a; fi
GPIO_CHIP="${COREBOOK_GPIO_CHIP:-0}"
GPIO_LINE="${COREBOOK_GPIO_LINE:-4}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRINGUP="$HERE/pa-bringup.py"
[ -f "$BRINGUP" ] || BRINGUP=/usr/local/lib/corebook-audio/pa-bringup.py

# --- 0) wait for the codec (boot / resume race) --------------------------------
for i in $(seq 1 30); do
    grep -qsE 'SN6180|Conexant' /proc/asound/card*/codec#* && break
    sleep 1
done
grep -qsE 'SN6180|Conexant' /proc/asound/card*/codec#* \
    || { log "ERROR: Conexant codec not found in /proc/asound"; exit 1; }

# --- 1) GPIO4 (PA enable, INF GpioNumberForPA=4) -------------------------------
hold_gpio() {
    command -v gpioset >/dev/null 2>&1 || { log "ERROR: gpioset not found (install libgpiod tools)"; return 1; }
    pkill -x gpioset 2>/dev/null && sleep 0.3
    if gpioset --help 2>&1 | grep -q -- '--daemonize'; then
        gpioset --chip "$GPIO_CHIP" --daemonize "${GPIO_LINE}=1"          # libgpiod v2
    else
        gpioset --mode=signal --background "$GPIO_CHIP" "${GPIO_LINE}=1"  # libgpiod v1 (untested)
    fi
    sleep 0.3
    if pgrep -x gpioset >/dev/null; then
        log "GPIO${GPIO_LINE} held high on chip ${GPIO_CHIP} (gpioset pid $(pgrep -x gpioset | head -1))"
    else
        log "ERROR: gpioset is not running - GPIO${GPIO_LINE} NOT held"; return 1
    fi
}
if ! hold_gpio; then
    [ "${ALLOW_NO_GPIO:-0}" = 1 ] || { log "aborting (set ALLOW_NO_GPIO=1 in /etc/default/corebook-audio to continue anyway)"; exit 1; }
fi

# --- 2) PA bring-up ------------------------------------------------------------
if [ ! -f "$BRINGUP" ]; then log "ERROR: pa-bringup.py not found"; exit 1; fi
rc=1
for attempt in 1 2 3; do
    /usr/bin/python3 "$BRINGUP"
    rc=$?
    log "bring-up attempt $attempt rc=$rc"
    [ "$rc" -eq 0 ] && break
    sleep 1
done

# --- 3) unmute every desktop session's default sink ----------------------------
# WirePlumber caches sink state; a plain "unmute" is treated as already-done.
# The mute TOGGLE forces it to re-apply values onto the speaker DACs.
# Volume is NOT touched unless COREBOOK_SINK_VOLUME is set (e.g. 0.4).
found=0
for d in /run/user/*; do
    u="${d##*/}"
    case "$u" in ''|*[!0-9]*) continue;; esac
    user="$(id -nu "$u" 2>/dev/null)" || continue
    for i in $(seq 1 15); do
        if runuser -u "$user" -- env XDG_RUNTIME_DIR="$d" \
               wpctl set-mute @DEFAULT_AUDIO_SINK@ 1 >/dev/null 2>&1 \
           && sleep 0.2 \
           && runuser -u "$user" -- env XDG_RUNTIME_DIR="$d" \
               wpctl set-mute @DEFAULT_AUDIO_SINK@ 0 >/dev/null 2>&1; then
            if [ -n "${COREBOOK_SINK_VOLUME:-}" ]; then
                runuser -u "$user" -- env XDG_RUNTIME_DIR="$d" \
                    wpctl set-volume @DEFAULT_AUDIO_SINK@ "$COREBOOK_SINK_VOLUME" >/dev/null 2>&1
            fi
            log "sink resynced for uid $u ($user)"
            found=1
            break
        fi
        sleep 1
    done
done
[ "$found" = 0 ] && log "no desktop session found to unmute (boot before login is normal)"

exit $rc
