#!/usr/bin/env python3
"""
pa-bringup.py — SmartPA (PA8022-class) initialization for Chuwi CoreBook Air Plus
via the Conexant/Synaptics SN6180 HDA codec's hidden vendor bridge (node 0x1B).

Replicates what the Windows vendor driver (CHDRT64ISST.sys) does at driver load:
  codec preamble -> RMW unlock -> fast I2S/link config -> PA firmware download ->
  slow link config -> AMP init table -> both link commit verbs -> power tail ->
  pin/DAC plumbing.

Self-contained: only needs firmware/pa8022_fw.hex next to it.
Run as root. Exit code 0 = success.

GPIO4 (PA enable) is NOT handled here any more: speaker-restore.sh holds it BEFORE
running this script. Run this file standalone only if GPIO4 is already high.

Set COREBOOK_WIRE=1 to convert the 'wire format' verb tables (nid in bits 27:20) to
the hwdep ioctl format (nid in bits 31:24). Without it, the kernel rejects those
words ('out of range cmd' in dmesg) and they never reach the codec.

See docs/HOW-IT-WORKS.md in this repository for a full protocol explanation.
"""
import os, re, sys, time, struct, fcntl

# ---------------------------------------------------------------- HDA hwdep --
def find_hwdep():
    """Locate the SN6180/Conexant codec's hwdep device across cards."""
    import glob
    for proc in sorted(glob.glob('/proc/asound/card[0-9]*/codec#*')):
        try:
            head = open(proc, errors='ignore').read(4000)
        except OSError:
            continue
        if 'SN6180' in head or 'Conexant' in head:
            card = proc.split('/')[3].replace('card', '')
            return f'/dev/snd/hwC{card}D0'
    return None   # never guess: vendor verbs sent to the wrong codec are dangerous

HDA_HWDEP = find_hwdep()
PVERSION, VERB_WRITE = 0x80044810, 0xC0084811

class Codec:
    def __init__(self):
        if not HDA_HWDEP:
            raise FileNotFoundError('Conexant/SN6180 codec not found in /proc/asound')
        self.fd = os.open(HDA_HWDEP, os.O_RDWR)
        fcntl.ioctl(self.fd, PVERSION, struct.pack('I', 1))
    def verb(self, w):
        res = fcntl.ioctl(self.fd, VERB_WRITE, struct.pack('II', w, 0))
        return struct.unpack('II', res)[1]
    def raw(self, w): return self.verb(w)
    def close(self): os.close(self.fd)

def verb(nid, vrb, payload):
    return ((nid & 0xFF) << 24) | ((vrb & 0xFFF) << 8) | (payload & 0xFF)

WIRE = os.environ.get('COREBOOK_WIRE', '0') == '1'

def to_ioctl(w):
    """HDA wire word (nid in bits 27:20) -> hwdep ioctl word (nid in bits 31:24)."""
    return (((w >> 20) & 0xFF) << 24) | (w & 0xFFFFF)

def wire(w):
    return to_ioctl(w) if WIRE else w

PAD = 0x000F0000
V_SEL1, V_SEL2 = 0x520, 0x521     # bridge: select sub/device
V_HI, V_LO     = 0x526, 0x527     # bridge: register address hi/lo
V_HDR          = 0x528            # bridge: flags|count-1
V_COMMIT       = 0x529            # bridge: transaction commit/release
AMP_ADDR       = 0x2d             # I2C address of the SmartPA on the bridge

def data_slot(i, b): return verb(0x1B, 0x52A + i, b)
def pull_slot(i):     return verb(0x1B, 0xD2A + i, 0x00)

# ------------------------------------------------------------- verb tables --
ENGINE_VERBS = [0x01C1F81D, 0x011F0600, 0x016F0900, 0x01CCB000,
                0x01CCA000, 0x01C23140, 0x01970533]

OVERRIDE_VERBS = [0x01C1F81D, 0x01C1E038, 0x000F0000, 0x01C1E018, 0x000F0000]

SLOW_A = [
    0x170500,
    0x1D70740, 0x1D70500, 0x2270500, 0x2220031, 0x1E70500, 0x1E70720, 0x2470500, 0x2420031,
    0x1B63201, 0x1B6007F, 0x1B6013F, 0x1B60212, 0x1B60301, 0x1B60417, 0x1B60600, 0x1B60720,
    0x1B60840, 0x1B60960, 0x1B60D01, 0x1B60E17, 0x1B61000, 0x1B61120, 0x1B62817, 0x1B62897,
    0x1B60C0F, 0x1B63900, 0x1B63B01, 0x1B63C03, 0x1B31A01,
]
SLOW_B = [
    0x170500,
    0x2670740, 0x2670500, 0x2370500, 0x2320031, 0x2770500, 0x2770720, 0x2570500, 0x2520031,
    0x1B63301, 0x1B6147F, 0x1B6153F, 0x1B61612, 0x1B61701, 0x1B61817, 0x1B61A00, 0x1B61B20,
    0x1B61C40, 0x1B61D60, 0x1B62101, 0x1B62217, 0x1B62400, 0x1B62520, 0x1B62D17, 0x1B62D97,
    0x1B6200F, 0x1B63900, 0x1B63B01, 0x1B63C03, 0x1B31C01,
]

F7464_VERBS = [
    0x170500,
    0x1D70740, 0x1D70500, 0x2270500, 0x2220031, 0x1E70500, 0x1E70720,
    0x2470500, 0x2420031, 0x243704A,
    0x2670740, 0x2670500, 0x2370500, 0x2320031, 0x2770500, 0x2770720,
    0x2570500, 0x2520031, 0x253704A,
    0x1B63201, 0x1B6003F, 0x1B6011F, 0x1B60232, 0x1B60344, 0x1B60417,
    0x1B60600, 0x1B60700, 0x1B60D44, 0x1B60E17, 0x1B61000, 0x1B61100,
    0x1B22C80, 0x1B6282F, 0x1B628AF, 0x1B62C03,
    0x1B63301, 0x1B6143F, 0x1B6151F, 0x1B61632, 0x1B61744, 0x1B61817,
    0x1B61A00, 0x1B61B00, 0x1B62144, 0x1B62217, 0x1B62400, 0x1B62500,
    0x1B22D80, 0x1B62D2F, 0x1B62DAF, 0x1B63103,
    0x1B60C03, 0x1B62003,
]

AMP_INIT = [
    (0x600, 0x30),
    (0x700, 0x00), (0x0000, 0x25), (0x0003, 0x30), (0x0004, 0x80), (0x0005, 0x30), (0x0006, 0x80),
    (0x0008, 0x50), (0x0009, 0x2F), (0x0001, 0x45),
    (0x0260, 0x04),
    (0x0500, 0x3F), (0x0501, 0x1F), (0x0502, 0x02), (0x0503, 0x3F), (0x0504, 0x1F), (0x0505, 0x02),
    (0x0506, 0x17), (0x0507, 0x00), (0x0508, 0x17), (0x0509, 0x00), (0x050A, 0x17), (0x050B, 0x20),
    (0x050C, 0x17), (0x050D, 0x20), (0x050E, 0x17), (0x050F, 0x40), (0x0510, 0x17), (0x0511, 0x60),
    (0x0512, 0x17), (0x0513, 0x80), (0x0514, 0x17), (0x0515, 0xA0), (0x0516, 0x7F), (0x0517, 0x7F),
    (0x0518, 0x44), (0x0519, 0x00), (0x051A, 0x05), (0x051B, 0x0F), (0x051C, 0x00), (0x051D, 0x00),
    (0x051E, 0x03), (0x051F, 0x0F),
    (0x0270, 0x08), (0x0295, 0x07), (0x0296, 0x07),
    (0x0701, 0x77), (0x0702, 0x19), (0x0703, 0x19),
    (0x0C04, 0x01), (0x0C05, 0x01), (0x0C06, 0x04), (0x0C07, 0x01), (0x0C08, 0x01), (0x0C09, 0x20),
    (0x029B, 0x21), (0x029D, 0x04), (0x02A1, 0x08),
    (0x02D0, 0x3F), (0x02D1, 0x03), (0x02D2, 0x30),
    (0x010A, 0x03), (0x010B, 0x0F), (0x010D, 0x03), (0x0130, 0x03),
    (0x0261, 0x82), (0x0262, 0x82), (0x0210, 0x85),
    (0x02A0, 0x00), (0x0303, 0x00),
    (0x0600, 0x30), (0x0293, 0x08), (0x02F6, 0x0F),
    'MS10',
    (0x02F6, 0x0E),
    'MS10',
    (0x0293, 0x00), (0x0263, 0x01), (0x0264, 0x11),
    (0x0100, 0x01), (0x0286, 0x01), (0x0285, 0x04),
    'MS10',
    (0x0285, 0x00), (0x0286, 0x00), (0x0600, 0x00),
    'MS10',
    (0x0700, 0x03),
]

POWER_TAIL = [
    (0x600, 0x30), (0x293, 0x08), (0x2F6, 0x0F),
    'MS10',
    (0x2F6, 0x0E),
    'MS10',
    (0x293, 0x00), (0x600, 0x00),
]

# ------------------------------------------------------- bridge primitives --
def sel(c, sub=0):
    c.verb(verb(0x1B, V_SEL1, 0x80 | sub))
    c.verb(verb(0x1B, V_SEL2, AMP_ADDR))

def fwrite(c, reg, val):
    """Single register write to the PA through the framed bridge protocol."""
    hi, lo = (reg >> 8) & 0xFF, reg & 0xFF
    words = [data_slot(0, val),
             verb(0x1B, V_HI, hi), verb(0x1B, V_LO, lo),
             verb(0x1B, V_HDR, 0x00),
             verb(0x1B, V_COMMIT, 0x01), verb(0x1B, V_COMMIT, 0x02)]
    words += [PAD] * (50 - len(words))
    sel(c)
    for w in words[:50]:
        c.verb(w)

def fread(c, reg, count=1):
    hi, lo = (reg >> 8) & 0xFF, reg & 0xFF
    words = [verb(0x1B, V_HI, hi), verb(0x1B, V_LO, lo),
             verb(0x1B, V_HDR, 0x10 | (count - 1)),
             verb(0x1B, V_COMMIT, 0x01), verb(0x1B, V_COMMIT, 0x02)]
    words += [PAD] * (50 - len(words))
    sel(c)
    for w in words[:50]:
        c.verb(w)
    time.sleep(0.001)
    c.verb(verb(0x1B, 0xD29, 0))
    return [c.verb(pull_slot(i)) & 0xFF for i in range(count)]

def load_fw():
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, '..', 'firmware', 'pa8022_fw.hex'),   # repo layout
                 os.path.join(here, 'firmware', 'pa8022_fw.hex')):        # installed layout
        if os.path.isfile(cand):
            hx = open(cand).read().split()
            b = bytes.fromhex(''.join(hx))
            assert len(b) % 2 == 0
            return [(b[i], b[i + 1]) for i in range(0, len(b), 2)]
    raise FileNotFoundError('pa8022_fw.hex not found next to ' + here)

def oem_preamble(c):
    for w in OVERRIDE_VERBS:
        c.raw(wire(w))
    # INF "GpioVerb" section: codec GPIO bit4 writes (codec reports 0 GPIOs,
    # Windows sends them anyway -- so do we).
    c.raw(verb(0x01, 0x716, 0x00)); c.raw(verb(0x01, 0x717, 0x10)); c.raw(verb(0x01, 0x715, 0x10))
    c.raw(verb(0x01, 0xF15, 0x00))

def enable_rmw(c):
    r = c.raw(verb(0x1B, 0xB13, 0x00)); c.raw(verb(0x1B, 0xB13, (r | 2) & 0xFF))
    r = c.raw(verb(0x1B, 0xB12, 0x00)); c.raw(verb(0x1B, 0xB12, (r | 0x20) & 0xFF))

def burst(c, words):
    for w in words:
        c.verb(wire(w))

# ------------------------------------------------------------------- main ----
def main():
    try:
        c = Codec()
        print(f'using {HDA_HWDEP} (verb tables: {"wire->ioctl converted" if WIRE else "as-is"})', flush=True)

        print('[1/9] codec preamble + engine verbs', flush=True)
        oem_preamble(c); time.sleep(0.3)
        for w in ENGINE_VERBS: c.raw(wire(w))

        print('[2/9] RMW unlock', flush=True)
        enable_rmw(c)

        print('[3/9] fast I2S / link config', flush=True)
        burst(c, F7464_VERBS)

        pairs = load_fw()
        print(f'[4/9] PA firmware download ({len(pairs)} writes)', flush=True)
        t0 = time.time()
        for i, (r, v) in enumerate(pairs):
            fwrite(c, r, v)
            if i % 400 == 399:
                print(f'      {i+1}/{len(pairs)}', flush=True)
        print(f'      done in {time.time()-t0:.1f}s', flush=True)

        print('[5/9] slow link config', flush=True)
        burst(c, SLOW_A); burst(c, SLOW_B)

        print('[6/9] amp init sequence', flush=True)
        sel(c); fwrite(c, 0x600, 0x30); time.sleep(0.01)
        for item in AMP_INIT[1:]:
            if item == 'MS10':
                time.sleep(0.01); continue
            r, v = item; fwrite(c, r, v)
        c.raw(wire(0x1B62C0F)); c.raw(wire(0x1B6310F))          # Link A + Link B commits

        print('[7/9] power tail', flush=True)
        for item in POWER_TAIL:
            if item == 'MS10':
                time.sleep(0.01); continue
            r, v = item; fwrite(c, r, v)
        time.sleep(0.8)

        print('[8/9] pin / DAC plumbing', flush=True)
        for nid in (0x1d, 0x26):                     # I2S output pins: OUT+EAPD+D0
            c.raw(verb(nid, 0x707, 0x40)); c.raw(verb(nid, 0x70C, 0x02)); c.raw(verb(nid, 0x705, 0x00))
        for nid in (0x1e, 0x20, 0x24):               # ADC-side widgets to D0
            c.raw(verb(nid, 0x705, 0x00))
        for nid, ch in ((0x22, 0x00), (0x23, 0x01)): # bind DACs to analog PCM stream
            c.raw(verb(nid, 0x200, 0x11))            # converter format 48k/16/stereo
            c.raw(verb(nid, 0x706, (0x05 << 4) | ch))

        st = fread(c, 0x7E, 2)
        ok = len(st) >= 1 and st[0] != 0x00
        print(f'[9/9] PA status [0x7E] = {[hex(x) for x in st]} '
              f'-> {"ALIVE" if ok else "NOT RESPONDING"}', flush=True)
        c.close()
        return 0 if ok else 2
    except Exception as e:
        print(f'bring-up failed: {e}', flush=True)
        return 1

if __name__ == '__main__':
    sys.exit(main())
