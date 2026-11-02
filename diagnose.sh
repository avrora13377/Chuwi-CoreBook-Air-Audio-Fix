#!/bin/bash
# diagnose.sh — collect everything needed to debug the speaker fix. Run: sudo ./diagnose.sh
# Writes /tmp/corebook-diagnose.txt and prints it. Contains no serial numbers or hostname.
OUT=/tmp/corebook-diagnose.txt
{
sec() { echo; echo "===== $* ====="; }
sec "system";   uname -r; grep -E '^(PRETTY_NAME)=' /etc/os-release
pipewire --version 2>&1 | head -2; wireplumber --version 2>&1 | head -1
sec "dmi";      for k in bios-version baseboard-version system-product-name; do echo "$k: $(dmidecode -s $k 2>&1)"; done
sec "codecs";   grep -H -E 'Codec:|Vendor Id|Subsystem Id|Revision Id' /proc/asound/card*/codec#* 2>&1
cat /proc/asound/cards
ls /dev/snd/
sec "kernel rejected verbs this boot (dmesg 'out of range')"
echo "count: $(dmesg | grep -c 'out of range')"; dmesg | grep 'out of range' | tail -3
sec "DAC amps (0x11 analog, 0x22/0x23 I2S)"
for n in 0x11 0x22 0x23; do
  grep -h -A12 "^Node $n \[" /proc/asound/card*/codec#* 2>/dev/null | grep -E "^Node|Amp-Out|Converter" | head -4
done
sec "gpio";     command -v gpioset; gpioset --version 2>&1 | head -2; gpiodetect 2>&1
gpioinfo 2>&1 | awk '/^gpiochip/ {c=$1} /line +4[: ]/ {print c": "$0}'
echo "gpioset processes:"; pgrep -a gpioset || echo "  NONE RUNNING"
sec "services"
for u in corebook-speakers corebook-speakers-keepalive; do
  systemctl show -p ActiveState,SubState,Result,NRestarts,ExecMainStatus "$u" 2>&1
done
sec "unit file in use"; systemctl cat corebook-speakers.service 2>&1 | head -30
sec "systemd ordering cycles / corebook messages (this boot)"
journalctl -b --no-pager 2>/dev/null | grep -iE 'ordering cycle|corebook' | tail -30
sec "service logs";  journalctl -b --no-pager -u corebook-speakers -u corebook-speakers-keepalive 2>&1 | tail -60
sec "config";   cat /etc/default/corebook-audio 2>&1
sec "volume (run as normal user too: wpctl get-volume @DEFAULT_AUDIO_SINK@)"
for d in /run/user/*; do u=${d##*/}; user=$(id -nu "$u" 2>/dev/null) || continue
  runuser -u "$user" -- env XDG_RUNTIME_DIR="$d" wpctl get-volume @DEFAULT_AUDIO_SINK@ 2>&1; done
} 2>&1 | tee "$OUT"
echo; echo "Saved to $OUT"
