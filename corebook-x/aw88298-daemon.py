#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# aw88298-daemon.py — Chuwi CoreBook X (Conexant SN6180 + AWINIC AW88298) left speaker.
#
# This is a Python port of aw88298-daemon.sh by Francisco Montanes Garcia (@pacomont),
# https://github.com/pacomont/chuwi-corebook-x-left-speaker  (GPL-2.0-or-later),
# Copyright (C) 2026 Francisco Montanes Garcia <fco.gmon@gmail.com>
# All hardware knowledge in here (audio path, register values, Force Boost finding,
# volume offset, suspend/resume recovery) is his. The port only replaces the hda-verb /
# i2cset / i2cget subprocesses with direct hwdep and i2c-dev ioctls and adds safety guards.
#
# Audio path:  HDA stream -> DAC 0x22 -> pin 0x1d -> I2S -> AW88298 (I2C 0x34) -> left speaker
#              DAC 0x11 (pin 0x17, analog) keeps driving the right speaker.
#
# Differences from the shell original (all deliberate):
#   * refuses to run unless the codec subsystem id is 0x27821221 AND the amp answers with
#     chip id 0x1852 on I2C 0x34 (nothing is written before that check)
#   * finds the hwdep device and the I2C bus automatically instead of hard-coding
#     hwC1D0 / bus 0
#   * single process, no per-tick fork storm; reconnects after codec/I2C errors
#   * `--probe` mode: read-only check (prints the chip id and the HDA stream state)
#
# Usage:  sudo ./aw88298-daemon.py            # run (what the systemd unit does)
#         sudo ./aw88298-daemon.py --probe    # read-only self-check, writes nothing

import ctypes
import fcntl
import glob
import os
import re
import signal
import struct
import sys
import time

SUBSYS_ID = '0x27821221'
AMP_ADDR = 0x34
CHIP_ID = 0x1852
POLL_INTERVAL = 1.0      # seconds; >1 s keeps us out of the audio path
VOL_OFFSET = 10          # DAC 0x22 amp = max(0, DAC 0x11 amp - 10), calibrated by pacomont

# AW88298 registers (16-bit, big-endian on the wire)
REG_ID, REG_SYSCTRL, REG_SYSCTRL2, REG_I2SCTRL = 0x00, 0x04, 0x05, 0x06
REG_HAGCCFG1, REG_HAGCCFG2, REG_BSTCTRL2 = 0x09, 0x0A, 0x61

# SYSCTRL values that mean "amp is powered down" (BE): PWDN+AMPPD variants
SYSCTRL_DOWN = (0x4003, 0x0003, 0x4005)

HDA_PVERSION, HDA_VERB_WRITE = 0x80044810, 0xC0084811
I2C_RDWR, I2C_M_RD = 0x0707, 0x0001

STOP = False


def log(msg):
    print(time.strftime('[%H:%M:%S] ') + msg, flush=True)


def _stop(*_):
    global STOP
    STOP = True


# ----------------------------------------------------------------------------- HDA
def pack(nid, verb, param):
    return ((nid & 0xFF) << 24) | ((verb & 0xFFF) << 8) | (param & 0xFFFF)


def find_codec():
    """Return the hwdep path of the CoreBook X SN6180 codec, or None."""
    for proc in sorted(glob.glob('/proc/asound/card[0-9]*/codec#*')):
        try:
            head = open(proc, errors='ignore').read(4000)
        except OSError:
            continue
        if ('SN6180' in head or 'Conexant' in head) and f'Subsystem Id: {SUBSYS_ID}' in head:
            card = proc.split('/')[3].replace('card', '')
            return f'/dev/snd/hwC{card}D0'
    return None


class Hda:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR)
        fcntl.ioctl(self.fd, HDA_PVERSION, struct.pack('I', 1))

    def verb(self, nid, verb, param=0):
        res = fcntl.ioctl(self.fd, HDA_VERB_WRITE, struct.pack('II', pack(nid, verb, param), 0))
        return struct.unpack('II', res)[1]

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


# ----------------------------------------------------------------------------- I2C
class _I2cMsg(ctypes.Structure):
    _fields_ = [('addr', ctypes.c_uint16), ('flags', ctypes.c_uint16),
                ('len', ctypes.c_uint16), ('buf', ctypes.c_void_p)]


class _I2cRdwr(ctypes.Structure):
    _fields_ = [('msgs', ctypes.POINTER(_I2cMsg)), ('nmsgs', ctypes.c_uint32)]


def find_i2c_bus():
    """Bus number of the ACPI client i2c-AWDZ8298:* (the amplifier), or None."""
    for path in sorted(glob.glob('/sys/bus/i2c/devices/i2c-AWDZ8298:*')):
        m = re.search(r'/i2c-(\d+)/i2c-AWDZ8298', os.path.realpath(path))
        if m:
            return int(m.group(1))
    return None


class I2c:
    def __init__(self, bus):
        self.fd = os.open(f'/dev/i2c-{bus}', os.O_RDWR)

    def _xfer(self, msgs):
        arr = (_I2cMsg * len(msgs))(*msgs)
        data = _I2cRdwr(arr, len(msgs))
        fcntl.ioctl(self.fd, I2C_RDWR, data)

    def read_be(self, reg):
        wbuf = (ctypes.c_uint8 * 1)(reg)
        rbuf = (ctypes.c_uint8 * 2)()
        self._xfer([_I2cMsg(AMP_ADDR, 0, 1, ctypes.cast(wbuf, ctypes.c_void_p)),
                    _I2cMsg(AMP_ADDR, I2C_M_RD, 2, ctypes.cast(rbuf, ctypes.c_void_p))])
        return (rbuf[0] << 8) | rbuf[1]

    def write_be(self, reg, value):
        wbuf = (ctypes.c_uint8 * 3)(reg, (value >> 8) & 0xFF, value & 0xFF)
        self._xfer([_I2cMsg(AMP_ADDR, 0, 3, ctypes.cast(wbuf, ctypes.c_void_p))])

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


# ------------------------------------------------------------------------ the logic
class Daemon:
    def __init__(self, hda, i2c, sleep=time.sleep):
        self.hda, self.i2c, self.sleep = hda, i2c, sleep
        self.last_stream = 0
        self.last_vol = None
        self.amp_on = False

    # -- HDA helpers
    def get_stream(self):
        return self.hda.verb(0x11, 0xF06, 0) & 0xFF

    def get_format(self, nid):
        return self.hda.verb(nid, 0xA00, 0) & 0xFFFF

    def hda_power_cycle(self):
        # After suspend/resume DAC 0x22 and pin 0x1d keep sensible verb values but no audio
        # flows over the I2S link; forcing D3 -> D0 wakes the codec's internal I2S link.
        self.hda.verb(0x1D, 0x705, 0x3)
        self.hda.verb(0x22, 0x705, 0x3)
        self.sleep(0.05)
        self.hda.verb(0x22, 0x705, 0x0)
        self.hda.verb(0x1D, 0x705, 0x0)
        self.sleep(0.02)
        log('HDA power cycle: DAC 0x22 and pin 0x1d forced D3->D0')

    def apply_stream(self, stream_raw):
        stream_id = (stream_raw >> 4) & 0xF
        stream_r = (stream_id << 4) | 1
        stream_l = (stream_id << 4) | 0
        fmt = self.get_format(0x11)
        self.hda.verb(0x11, 0x706, stream_r)      # DAC 0x11 -> right channel only
        self.hda.verb(0x22, 0x706, stream_l)      # DAC 0x22 -> left channel only
        self.hda.verb(0x22, 0x200, fmt)           # same format as DAC 0x11
        self.last_vol = None                      # force volume re-application
        log(f'Stream {stream_id}: DAC 0x11=0x{stream_r:x}(R) DAC 0x22=0x{stream_l:x}(L) fmt=0x{fmt:x}')

    def mirror_volume(self):
        v11 = self.hda.verb(0x11, 0xB00, 0x8000) & 0xFF
        muted = bool(v11 & 0x80)
        gain = v11 & 0x7F
        if muted:
            v22, sig = 0, 'muted'
        else:
            v22 = max(0, min(0x4A, gain - VOL_OFFSET))
            sig = v22
        if sig != self.last_vol:
            if muted:
                self.hda.verb(0x22, 0x300, 0xB080)
                self.hda.verb(0x22, 0x300, 0x9080)
                log(f'Vol mirror: DAC 0x11=0x{v11:02x} [MUTED] -> DAC 0x22 muted')
            else:
                self.hda.verb(0x22, 0x300, 0xB000 | v22)
                self.hda.verb(0x22, 0x300, 0x9000 | v22)
                log(f'Vol mirror: DAC 0x11=0x{v11:02x} -> DAC 0x22=0x{v22:02x}')
            self.last_vol = sig

    # -- amplifier
    def force_boost(self):
        # BSTCTRL2 bits 14:12 = 001 (Force Boost); the chip defaults to Smart Boost 2, which
        # idles the boost converter during silence and glitches the start of every sound.
        be = self.i2c.read_be(REG_BSTCTRL2)
        new = (be & 0x8FFF) | 0x1000
        self.i2c.write_be(REG_BSTCTRL2, new)
        log(f'BSTCTRL2: 0x{be:04x} -> 0x{new:04x} BE (Force Boost)')

    def setup_amp(self):
        self.hda_power_cycle()
        self.i2c.write_be(REG_SYSCTRL, 0x2000)        # power on, AV10, I2SEN off
        self.sleep(0.003)
        self.i2c.write_be(REG_SYSCTRL, 0x7040)        # SPK_GAIN=7 + I2SEN
        self.sleep(0.002)
        self.i2c.write_be(REG_SYSCTRL2, 0x006B)       # HAGCE=1, BST_IPEAK=11, HMUTE=0
        self.i2c.write_be(REG_I2SCTRL, 0x14E8)        # INPLEV=0 dB
        self.i2c.write_be(REG_HAGCCFG1, 0x7940)
        self.i2c.write_be(REG_HAGCCFG2, 0x0070)
        self.hda.verb(0x1D, 0x707, 0x40)              # pin 0x1d: output
        self.hda.verb(0x1D, 0x70C, 0x02)              # pin 0x1d: EAPD
        self.force_boost()
        self.amp_on = True
        cur = self.get_stream()
        if cur != 0:
            self.apply_stream(cur)
            self.mirror_volume()

    def shutdown_amp(self):
        self.i2c.write_be(REG_SYSCTRL2, 0x0038)       # HMUTE
        self.sleep(0.001)
        self.i2c.write_be(REG_SYSCTRL, 0x4003)        # PWDN + AMPPD
        self.hda.verb(0x22, 0x706, 0x00)
        self.amp_on = False

    def cleanup(self):
        log('Shutting down...')
        self.shutdown_amp()
        sid = (self.get_stream() >> 4) & 0xF
        self.hda.verb(0x11, 0x706, sid << 4)          # DAC 0x11 back to channel 0
        log('Restored')

    # -- main loop body (one tick)
    def tick(self):
        stream = self.get_stream()
        if stream != self.last_stream:
            if stream == 0:
                log('Stream stopped')
            else:
                self.apply_stream(stream)
                if not self.amp_on:
                    self.setup_amp()
            self.last_stream = stream

        if stream != 0:
            current = self.get_stream()
            expected_r = (((current >> 4) & 0xF) << 4) | 1
            if current != expected_r or self.get_format(0x11) != self.get_format(0x22):
                self.apply_stream(current)

            if self.i2c.read_be(REG_SYSCTRL) in SYSCTRL_DOWN:
                log('AW88298 powered down, bringing it back up...')
                self.setup_amp()

            bst = self.i2c.read_be(REG_BSTCTRL2)
            if ((bst >> 12) & 0x7) != 1:
                log(f'BSTCTRL2 drifted out of Force Boost (0x{bst:04x}), re-applying')
                self.force_boost()

            self.mirror_volume()


# ---------------------------------------------------------------------------- main
def open_devices():
    hwdep = find_codec()
    if not hwdep:
        raise SystemExit(f'no Conexant SN6180 codec with subsystem {SUBSYS_ID} found - '
                         'this daemon is only for the Chuwi CoreBook X')
    bus = find_i2c_bus()
    if bus is None:
        raise SystemExit('no i2c-AWDZ8298 client found under /sys/bus/i2c/devices '
                         '(is this a CoreBook X? is the ACPI device missing?)')
    if not os.path.exists(f'/dev/i2c-{bus}'):
        raise SystemExit(f'/dev/i2c-{bus} missing: run "modprobe i2c-dev"')
    hda, i2c = Hda(hwdep), I2c(bus)
    chip = i2c.read_be(REG_ID)
    if chip != CHIP_ID:
        hda.close()
        i2c.close()
        raise SystemExit(f'unexpected chip id 0x{chip:04x} at I2C 0x{AMP_ADDR:02x} on bus {bus} '
                         f'(expected 0x{CHIP_ID:04x}); refusing to write anything')
    log(f'codec {hwdep}, amplifier on i2c-{bus} addr 0x{AMP_ADDR:02x}, chip id 0x{chip:04x}')
    return hda, i2c


def probe():
    hda, i2c = open_devices()
    d = Daemon(hda, i2c)
    print(f'chip id OK; HDA stream of DAC 0x11 = 0x{d.get_stream():02x}; '
          f'SYSCTRL = 0x{i2c.read_be(REG_SYSCTRL):04x}; BSTCTRL2 = 0x{i2c.read_be(REG_BSTCTRL2):04x}')
    print('probe is read-only: nothing was written')
    return 0


def run():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    fails = 0
    while not STOP:
        hda = i2c = d = None
        try:
            hda, i2c = open_devices()
            d = Daemon(hda, i2c)
            log(f'AW88298 daemon started (poll={POLL_INTERVAL}s)')
            d.setup_amp()
            fails = 0
            while not STOP:
                d.tick()
                end = time.time() + POLL_INTERVAL
                while not STOP and time.time() < end:
                    time.sleep(0.1)
        except OSError as e:
            fails += 1
            log(f'I/O error: {e}; reconnecting in 2 s (#{fails})')
            if fails >= 30:
                return 1
            for _ in range(20):
                if STOP:
                    break
                time.sleep(0.1)
        finally:
            if d is not None and STOP:
                try:
                    d.cleanup()
                except OSError:
                    pass
            for dev in (hda, i2c):
                if dev is not None:
                    dev.close()
    return 0


def main():
    if '--probe' in sys.argv[1:]:
        return probe()
    return run()


if __name__ == '__main__':
    sys.exit(main())
