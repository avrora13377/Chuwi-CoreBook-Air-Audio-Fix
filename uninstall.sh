#!/bin/bash
# uninstall.sh — removes whichever CoreBook speaker fix is installed (Air Plus and/or CoreBook X).
set -u
[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo $0"; exit 1; }

systemctl disable --now corebook-speakers-keepalive.service corebook-speakers.service \
                        corebook-x-aw88298.service 2>/dev/null
pkill -x gpioset 2>/dev/null     # Air Plus only: releases GPIO4

rm -f /etc/systemd/system/corebook-speakers.service \
      /etc/systemd/system/corebook-speakers-keepalive.service \
      /etc/systemd/system/corebook-x-aw88298.service \
      /usr/lib/systemd/system-sleep/corebook-audio \
      /usr/local/bin/corebook-speakers.sh \
      /etc/modules-load.d/corebook-x-i2c-dev.conf
rm -rf /usr/local/lib/corebook-audio /usr/local/share/doc/corebook-audio
systemctl daemon-reload
echo "removed. /etc/default/corebook-audio (if any) was left in place - delete it by hand if you want."
echo "Power the laptop off completely once, so the amplifier starts from a clean state."
