#!/bin/bash
# install.sh — CoreBook Air Plus internal speaker fix (patched)
# Run from the repo root (needs firmware/pa8022_fw.hex):  sudo ./install.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo $0"; exit 1; }

echo "== checking dependencies and hardware =="
command -v python3 >/dev/null || { echo "ERROR: python3 not found"; exit 1; }
if ! command -v gpioset >/dev/null; then
    echo "ERROR: gpioset not found. GPIO4 (PA enable) cannot be held without it."
    echo "  Arch/CachyOS: sudo pacman -S libgpiod"
    echo "  Debian/Ubuntu: sudo apt install gpiod     Fedora: sudo dnf install libgpiod-utils"
    [ "${ALLOW_NO_GPIO:-0}" = 1 ] || exit 1
    echo "  ALLOW_NO_GPIO=1 set - continuing without GPIO hold"
fi
[ -f "$REPO/firmware/pa8022_fw.hex" ] || { echo "ERROR: $REPO/firmware/pa8022_fw.hex missing"; exit 1; }

codec="$(grep -lE 'SN6180|Conexant' /proc/asound/card*/codec#* 2>/dev/null | head -1 || true)"
[ -n "$codec" ] || { echo "ERROR: no Conexant/SN6180 codec found - this fix is not for this machine."; exit 1; }
sub="$(grep -m1 'Subsystem Id' "$codec" | awk '{print $3}')"
echo "codec: $codec   subsystem: $sub"
if [ "$sub" != "0x20147054" ]; then
    echo "WARNING: subsystem is not 0x20147054 (tested Air Plus). Firmware/init tables may not match."
    echo "         Continue only if you know what you are doing (writes raw vendor commands to hardware)."
    [ "${FORCE:-0}" = 1 ] || { echo "         Re-run with FORCE=1 to proceed."; exit 1; }
fi

echo "== installing files =="
install -Dm755 "$REPO/bin/pa-bringup.py"       /usr/local/lib/corebook-audio/pa-bringup.py
install -Dm755 "$REPO/bin/maintain-stream.py"  /usr/local/lib/corebook-audio/maintain-stream.py
install -Dm644 "$REPO/firmware/pa8022_fw.hex"  /usr/local/lib/corebook-audio/firmware/pa8022_fw.hex
install -Dm755 "$REPO/bin/speaker-restore.sh"  /usr/local/bin/corebook-speakers.sh

if [ ! -e /etc/default/corebook-audio ]; then
cat > /etc/default/corebook-audio <<'CFG'
# CoreBook speaker fix settings (read by the systemd units and speaker-restore.sh)
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
Remove with: sudo ./uninstall.sh
EOT
