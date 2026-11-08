#!/bin/bash
# diagnose-x.sh — READ-ONLY hardware report for the Chuwi CoreBook X (Awinic AW88298 left speaker).
#
# Writes nothing to the codec or to any amplifier. It only reads /proc, /sys, dmesg, lspci and
# the ACPI tables. Two opt-in extras:
#   --probe-i2c         read the amplifier ID register (reg 0x00) over I2C. This is a standard
#                       register read: the only byte sent is the register address 0x00, then 2
#                       bytes are read back. No register is written. Loads the i2c-dev module.
#                       Needs i2c-tools.
#   --with-acpi-tables  also pack the raw ACPI tables into /tmp/corebook-x-acpi.tar.gz
#                       (look inside before sharing; the tables describe your machine).
#
# Usage:  sudo ./diagnose-x.sh [--probe-i2c] [--with-acpi-tables]
# Output: /tmp/corebook-x-report.txt   (board serial number / UUID lines are filtered out)

PROBE=0
TABLES=0
for a in "$@"; do
    case "$a" in
        --probe-i2c) PROBE=1 ;;
        --with-acpi-tables) TABLES=1 ;;
        *) echo "unknown option: $a"; exit 1 ;;
    esac
done

OUT=/tmp/corebook-x-report.txt
have() { command -v "$1" >/dev/null 2>&1; }
sec()  { echo; echo "===== $* ====="; }

report() {
    [ "$(id -u)" -eq 0 ] || echo "NOTE: not root - dmesg, dmidecode and ACPI sections may be empty"

    sec "system"
    uname -r
    grep -E '^PRETTY_NAME=' /etc/os-release
    echo "pipewire: $(pipewire --version 2>&1 | sed -n 2p)"

    sec "dmi (serial numbers / uuids filtered)"
    if have dmidecode; then
        dmidecode -t 0 -t 1 -t 2 2>&1 | grep -viE 'serial|uuid|asset|handle|^$'
    else
        echo "dmidecode not installed"
    fi

    sec "was the Air Plus speaker fix installed on this machine?"
    for u in corebook-speakers corebook-speakers-keepalive; do
        echo "$u: enabled=$(systemctl is-enabled "$u" 2>&1) active=$(systemctl is-active "$u" 2>&1)"
    done
    ls -l /usr/local/lib/corebook-audio /usr/local/bin/corebook-speakers.sh 2>&1 | head

    sec "sound cards / codecs"
    cat /proc/asound/cards
    grep -H -E 'Codec:|Vendor Id|Subsystem Id|Revision Id' /proc/asound/card*/codec#* 2>&1
    aplay -l 2>&1 | head -20

    sec "FULL dump of the Conexant codec (nodes, pins, converters, power state)"
    for f in /proc/asound/card*/codec#*; do
        if grep -qE 'SN6180|Conexant' "$f" 2>/dev/null; then
            echo "--- $f"
            cat "$f"
        fi
    done

    sec "PCI audio devices and bound drivers"
    if have lspci; then
        lspci -nnk | grep -A3 -iE 'audio|multimedia'
    else
        echo "lspci not installed"
    fi

    sec "kernel messages: audio / ACP / I2S / Awinic / I2C (this boot)"
    dmesg 2>&1 | grep -iE 'acp|i2s|aw88|awdz|awinic|snd_|sof|hdaudio|i2c' | tail -80

    sec "relevant kernel modules"
    lsmod | grep -iE 'snd_acp|snd_soc|aw88|acp|sof'
    find "/lib/modules/$(uname -r)" -iname '*aw88*' 2>/dev/null
    find "/lib/modules/$(uname -r)" -iname '*acp*mach*' 2>/dev/null | head -20

    sec "I2C devices (the Awinic amp shows up as i2c-AWDZ8298:*)"
    ls -l /sys/bus/i2c/devices/ 2>&1
    ls /sys/bus/acpi/devices 2>/dev/null | grep -iE 'AWDZ|AWIN' || echo "(no AWDZ* in /sys/bus/acpi/devices)"
    for d in /sys/bus/i2c/devices/i2c-AWDZ*; do
        [ -e "$d" ] || continue
        echo "--- $d -> $(readlink -f "$d")"
        if [ -e "$d/driver" ]; then echo "driver: $(basename "$(readlink -f "$d/driver")")"; else echo "driver: NONE BOUND"; fi
        cat "$d/name" 2>/dev/null
    done
    for d in /sys/bus/acpi/devices/AWDZ*; do
        [ -e "$d" ] || continue
        echo "--- $d"
        for f in hid path status uid; do echo "$f: $(cat "$d/$f" 2>/dev/null)"; done
        ls -l "$d" 2>/dev/null | head -20
    done

    sec "platform devices related to audio"
    ls /sys/bus/platform/devices 2>/dev/null | grep -iE 'acp|sof|i2s|AWDZ|snd' || echo "(none)"

    sec "ACPI: device block(s) mentioning AWDZ (needs iasl)"
    if [ ! -d /sys/firmware/acpi/tables ]; then
        echo "no ACPI tables visible (run as root)"
    elif have iasl; then
        T="$(mktemp -d)"
        cp /sys/firmware/acpi/tables/DSDT /sys/firmware/acpi/tables/SSDT* "$T"/ 2>/dev/null
        (
            cd "$T" || exit 0
            for f in DSDT SSDT*; do iasl -d "$f" >/dev/null 2>&1; done
            for f in $(grep -l 'AWDZ' ./*.dsl 2>/dev/null); do
                echo "--- found in $f"
                grep -n -B6 -A70 'AWDZ' "$f" | head -200
            done
        )
        rm -rf "$T"
    else
        echo "iasl not installed (Arch: acpica, Debian/Ubuntu: acpica-tools). Rerun with --with-acpi-tables instead."
    fi

    sec "GPIO line 121 (reset line mentioned in the public issues)"
    if have gpioinfo; then
        gpioinfo 2>&1 | awk '/^gpiochip/ {c=$1} /line +121[: ]/ {print c": "$0}'
    else
        echo "gpioinfo not installed"
    fi
}

probe_i2c() {
    sec "I2C register read of the amplifier ID (reg 0x00, 2 bytes, no data written)"
    if ! have i2ctransfer; then
        echo "i2ctransfer not installed (package: i2c-tools)"
        return
    fi
    modprobe i2c-dev 2>&1
    found=0
    for d in /sys/bus/i2c/devices/i2c-AWDZ*; do
        [ -e "$d" ] || continue
        found=1
        bus="$(readlink -f "$d" | grep -oE 'i2c-[0-9]+' | head -1 | cut -d- -f2)"
        echo "amp client $d is on bus: ${bus:-unknown}"
        [ -n "$bus" ] || continue
        for addr in 0x34 0x35; do
            printf 'bus %s addr %s: ' "$bus" "$addr"
            i2ctransfer -y "$bus" w1@"$addr" 0x00 r2 2>&1 | tr '\n' ' '
            echo
        done
    done
    [ "$found" = 1 ] || echo "no i2c-AWDZ* client found"
    echo "(public issue reports chip ID 0x1852 at reg 0x00. Sent: register address only, then read. No data written.)"
}

main() {
    report
    [ "$PROBE" = 1 ] && probe_i2c
    if [ "$TABLES" = 1 ]; then
        sec "raw ACPI tables"
        if [ -d /sys/firmware/acpi/tables ]; then
            tar -C /sys/firmware/acpi/tables -czf /tmp/corebook-x-acpi.tar.gz DSDT $(cd /sys/firmware/acpi/tables && ls SSDT* 2>/dev/null) 2>&1 \
                && echo "wrote /tmp/corebook-x-acpi.tar.gz"
        else
            echo "no ACPI tables visible (run as root)"
        fi
    fi
}

main 2>&1 | tee "$OUT"
echo
echo "Saved to $OUT"
[ "$TABLES" = 1 ] && echo "ACPI tables: /tmp/corebook-x-acpi.tar.gz (check before sharing)"
exit 0
