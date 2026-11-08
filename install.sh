#!/bin/bash
# install.sh — Chuwi CoreBook internal speaker fixes. Run from the repo root:  sudo ./install.sh
#
# The machine is detected from the Conexant codec's subsystem id:
#   0x20147054  CoreBook Air Plus  -> SmartPA bring-up + stream keep-alive (bin/, systemd/)
#   0x27821221  CoreBook X         -> AW88298 left speaker daemon        (corebook-x/, experimental)
# Anything else is refused unless you set FORCE=1 PROFILE=airplus|corebook-x.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo $0"; exit 1; }
command -v python3 >/dev/null || { echo "ERROR: python3 not found"; exit 1; }

echo "== detecting machine =="
codec="$(grep -lE 'SN6180|Conexant' /proc/asound/card*/codec#* 2>/dev/null | head -1 || true)"
[ -n "$codec" ] || { echo "ERROR: no Conexant/SN6180 codec found - these fixes are not for this machine."; exit 1; }
sub="$(grep -m1 'Subsystem Id' "$codec" | awk '{print $3}')"
echo "codec: $codec   subsystem: $sub"

case "$sub" in
    0x20147054) PROFILE=airplus ;;
    0x27821221) PROFILE=corebook-x ;;
    *)
        if [ "${FORCE:-0}" = 1 ] && { [ "${PROFILE:-}" = airplus ] || [ "${PROFILE:-}" = corebook-x ]; }; then
            echo "WARNING: unknown subsystem $sub, forcing profile $PROFILE (raw vendor commands will be written!)"
        else
            echo "ERROR: unknown subsystem $sub. These fixes write raw commands to hardware."
            echo "       Supported: 0x20147054 (Air Plus), 0x27821221 (CoreBook X)."
            echo "       Override at your own risk: sudo FORCE=1 PROFILE=airplus|corebook-x $0"
            exit 1
        fi ;;
esac
echo "profile: $PROFILE"

# ------------------------------------------------------------------ CoreBook Air Plus
install_airplus() {
    if ! command -v gpioset >/dev/null; then
        echo "ERROR: gpioset not found. GPIO4 (PA enable) cannot be held without it."
        echo "  Arch/CachyOS: sudo pacman -S libgpiod"
        echo "  Debian/Ubuntu: sudo apt install gpiod     Fedora: sudo dnf install libgpiod-utils"
        [ "${ALLOW_NO_GPIO:-0}" = 1 ] || exit 1
        echo "  ALLOW_NO_GPIO=1 set - continuing without GPIO hold"
    fi
    [ -f "$REPO/firmware/pa8022_fw.hex" ] || { echo "ERROR: $REPO/firmware/pa8022_fw.hex missing"; exit 1; }

    echo "== installing files =="
    install -Dm755 "$REPO/bin/pa-bringup.py"       /usr/local/lib/corebook-audio/pa-bringup.py
    install -Dm755 "$REPO/bin/maintain-stream.py"  /usr/local/lib/corebook-audio/maintain-stream.py
    install -Dm644 "$REPO/firmware/pa8022_fw.hex"  /usr/local/lib/corebook-audio/firmware/pa8022_fw.hex
    install -Dm755 "$REPO/bin/speaker-restore.sh"  /usr/local/bin/corebook-speakers.sh

    if [ ! -e /etc/default/corebook-audio ]; then
        cat > /etc/default/corebook-audio <<'CFG'
# CoreBook Air Plus speaker fix settings (read by the systemd units and speaker-restore.sh)
# 1 = convert wire-format verb tables so the kernel accepts them (see pa-bringup.py)
COREBOOK_WIRE=0
# Optional: set sink volume after each restore, e.g. 0.4. Empty = do not touch.
#COREBOOK_SINK_VOLUME=0.4
#COREBOOK_GPIO_CHIP=0
#COREBOOK_GPIO_LINE=4
#ALLOW_NO_GPIO=1
CFG
    fi

    echo "== systemd units =="
    install -Dm644 "$REPO/systemd/corebook-speakers.service"            /etc/systemd/system/corebook-speakers.service
    install -Dm644 "$REPO/systemd/corebook-speakers-keepalive.service"  /etc/systemd/system/corebook-speakers-keepalive.service

    mkdir -p /usr/lib/systemd/system-sleep
    cat > /usr/lib/systemd/system-sleep/corebook-audio <<'HOOK'
#!/bin/bash
# After resume the PA has lost its state: re-run bring-up (restart, because the unit is RemainAfterExit)
if [ "$1" = post ]; then
    /usr/bin/systemctl restart corebook-speakers.service --no-block
fi
HOOK
    chmod +x /usr/lib/systemd/system-sleep/corebook-audio

    systemctl daemon-reload
    systemctl enable corebook-speakers.service corebook-speakers-keepalive.service

    echo "== starting =="
    if systemctl restart corebook-speakers.service && systemctl restart corebook-speakers-keepalive.service; then
        echo "OK - speakers should work now."
    else
        echo "start failed - see: journalctl -u corebook-speakers -u corebook-speakers-keepalive -b"
    fi
    cat <<'EOT'

Commands:
  systemctl status corebook-speakers corebook-speakers-keepalive
  journalctl -u corebook-speakers -u corebook-speakers-keepalive -b
  sudo ./diagnose.sh        # collect a report to send when something is wrong
Settings: /etc/default/corebook-audio   (then: sudo systemctl restart corebook-speakers)
EOT
}

# --------------------------------------------------------------------- CoreBook X
install_corebook_x() {
    if systemctl list-unit-files 2>/dev/null | grep -qE '^corebook-speakers(-keepalive)?\.service'; then
        echo "ERROR: the Air Plus fix is installed as a systemd service on this machine. It is not for the"
        echo "       CoreBook X and conflicts with this one. Remove it first: sudo ./uninstall.sh"
        echo "       (then power off completely and run this again)"
        exit 1
    fi
    if systemctl list-unit-files aw88298.service 2>/dev/null | grep -q aw88298; then
        echo "ERROR: pacomont's aw88298.service is installed too. Run only one of them:"
        echo "       sudo systemctl disable --now aw88298   (then run this again)"
        exit 1
    fi
    if [ -e /usr/local/bin/corebook-speakers.sh ]; then
        echo "note: a stray /usr/local/bin/corebook-speakers.sh (old Air Plus fix) exists; no service uses it,"
        echo "      it is harmless. Delete it with: sudo rm -f /usr/local/bin/corebook-speakers.sh"
    fi
    ls /sys/bus/i2c/devices/i2c-AWDZ8298:* >/dev/null 2>&1 \
        || { echo "ERROR: the Awinic amplifier (ACPI AWDZ8298) is not visible under /sys/bus/i2c/devices."; exit 1; }
    modprobe i2c-dev || { echo "ERROR: cannot load the i2c-dev kernel module"; exit 1; }

    echo "== installing files =="
    install -Dm755 "$REPO/corebook-x/aw88298-daemon.py" /usr/local/lib/corebook-audio/aw88298-daemon.py
    install -Dm644 "$REPO/corebook-x/COPYING"           /usr/local/share/doc/corebook-audio/COPYING.corebook-x
    echo i2c-dev > /etc/modules-load.d/corebook-x-i2c-dev.conf

    echo "== read-only check of codec and amplifier =="
    if ! python3 /usr/local/lib/corebook-audio/aw88298-daemon.py --probe; then
        echo "ERROR: probe failed - the service was NOT enabled and nothing was written to the amplifier."
        exit 1
    fi

    echo "== systemd unit =="
    install -Dm644 "$REPO/corebook-x/corebook-x-aw88298.service" /etc/systemd/system/corebook-x-aw88298.service
    systemctl daemon-reload
    systemctl enable corebook-x-aw88298.service
    echo "== starting (keep the volume low for the first test) =="
    if systemctl restart corebook-x-aw88298.service; then
        echo "OK - play something; both speakers should work."
    else
        echo "start failed - see: journalctl -u corebook-x-aw88298 -b"
    fi
    cat <<'EOT'

CoreBook X support is EXPERIMENTAL and based on pacomont's AW88298 daemon (GPL-2.0-or-later):
  https://github.com/pacomont/chuwi-corebook-x-left-speaker
Commands:
  systemctl status corebook-x-aw88298
  journalctl -u corebook-x-aw88298 -b
  sudo python3 /usr/local/lib/corebook-audio/aw88298-daemon.py --probe     # read-only self-check
  sudo ./diagnose-x.sh --probe-i2c                                          # report for bug reports
EOT
}

echo
case "$PROFILE" in
    airplus)     install_airplus ;;
    corebook-x)  install_corebook_x ;;
esac
echo
echo "Settings and removal: see README.md.  Remove with: sudo ./uninstall.sh"
