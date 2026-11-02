#!/bin/bash
set -u
[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo $0"; exit 1; }
systemctl disable --now corebook-speakers-keepalive.service corebook-speakers.service 2>/dev/null
pkill -x gpioset 2>/dev/null     # releases GPIO4
rm -f /etc/systemd/system/corebook-speakers.service \
      /etc/systemd/system/corebook-speakers-keepalive.service \
      /usr/lib/systemd/system-sleep/corebook-audio \
      /usr/local/bin/corebook-speakers.sh
rm -rf /usr/local/lib/corebook-audio
systemctl daemon-reload
echo "removed. /etc/default/corebook-audio left in place (delete it by hand if you want)."
