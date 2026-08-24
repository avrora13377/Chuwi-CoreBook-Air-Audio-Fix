#!/bin/bash
# uninstall.sh — remove the CoreBook Air Plus speaker fix
set -euo pipefail
[ "$(id -u)" -ne 0 ] && { echo "run as root: sudo $0"; exit 1; }

systemctl disable --now corebook-speakers.service 2>/dev/null || true
rm -f /etc/systemd/system/corebook-speakers.service
rm -f /usr/lib/systemd/system-sleep/corebook-audio
rm -f /usr/local/bin/corebook-speakers.sh
rm -rf /usr/local/lib/corebook-audio
systemctl daemon-reload
echo "removed. (your speakers will be silent again after next boot)"
