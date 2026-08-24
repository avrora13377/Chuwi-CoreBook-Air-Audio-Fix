#!/bin/bash
# install.sh — CoreBook Air Plus internal speaker fix
# Copies scripts into /usr/local, installs the systemd unit and a
# suspend/resume hook. Run as root:  sudo ./install.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "run as root: sudo $0"; exit 1
fi

echo "== checking dependencies =="
missing=()
command -v python3 >/dev/null || missing+=(python3)
command -v gpioset >/dev/null || missing+=(libgpiod "gpiod" "libgpiod-utils")
if [ ${#missing[@]} -gt 0 ]; then
    echo "WARNING: not found: ${missing[*]}"
    echo "  Arch/CachyOS: sudo pacman -S python libgpiod"
    echo "  Debian/Ubuntu: sudo apt install python3 gpiod"
    echo "  (continuing anyway — GPIO hold will be skipped if missing)"
fi

echo "== installing files =="
install -Dm755 "$REPO/bin/pa-bringup.py"       /usr/local/lib/corebook-audio/pa-bringup.py
# firmware must sit at ../firmware relative to the script
install -Dm644 "$REPO/firmware/pa8022_fw.hex"  /usr/local/lib/corebook-audio/firmware/pa8022_fw.hex
install -Dm755 "$REPO/bin/speaker-restore.sh"  /usr/local/bin/corebook-speakers.sh

echo "== systemd unit =="
sed "s|YOURNAME/corebook-air-plus-audio-fix|local-install|" \
    "$REPO/systemd/corebook-speakers.service" > /etc/systemd/system/corebook-speakers.service

mkdir -p /usr/lib/systemd/system-sleep
cat > /usr/lib/systemd/system-sleep/corebook-audio <<'EOF'
#!/bin/bash
# re-run speaker restore after suspend/resume (PA loses state on power-off)
if [ "$1" = post ]; then
    /usr/bin/systemctl start corebook-speakers.service --no-block
fi
EOF
chmod +x /usr/lib/systemd/system-sleep/corebook-audio

systemctl daemon-reload
systemctl enable corebook-speakers.service

echo
echo "== running initial bring-up now =="
/usr/local/bin/corebook-speakers.sh && echo "OK - speakers should work now." \
    || echo "bring-up returned an error — see messages above."

cat <<'EOF'

Done. The service runs automatically at every boot and after every resume.
Manual commands:
  systemctl status corebook-speakers     # what happened at boot
  journalctl -u corebook-speakers        # logs
  sudo /usr/local/bin/corebook-speakers.sh   # run manually anytime
Remove with: uninstall.sh from this repository.
EOF
