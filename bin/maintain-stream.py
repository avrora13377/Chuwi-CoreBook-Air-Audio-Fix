#!/usr/bin/env python3
"""Keep I2S DACs 0x22/0x23 attached to the live analog PCM stream.

The HDA parser owns DAC 0x11 (Speaker) and resets 0x22/0x23 on every PCM
start/stop. We keep copying stream, format and gain from 0x11 onto them.
Never forces 100% volume.

Differences from the fork's original: handles SIGTERM (DACs get muted on
`systemctl stop`), survives codec errors / resume by reconnecting instead of
dying, default poll interval 0.2 s.
Usage: maintain-stream.py [interval_seconds]
"""
import os, re, sys, time, struct, fcntl, glob, signal

STOP = False
def _stop(*_):
    global STOP
    STOP = True

def find_hwdep():
    for proc in sorted(glob.glob('/proc/asound/card[0-9]*/codec#*')):
        try:
            head = open(proc, errors='ignore').read(4000)
        except OSError:
            continue
        if 'SN6180' in head or 'Conexant' in head:
            card = proc.split('/')[3].replace('card', '')
            return f'/dev/snd/hwC{card}D0', proc
    return None, None

PVERSION, VERB_WRITE = 0x80044810, 0xC0084811

class Codec:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR)
        fcntl.ioctl(self.fd, PVERSION, struct.pack('I', 1))
    def raw(self, w):
        res = fcntl.ioctl(self.fd, VERB_WRITE, struct.pack('II', w, 0))
        return struct.unpack('II', res)[1]
    def close(self):
        try: os.close(self.fd)
        except OSError: pass

def pack(nid, verb, param):
    return ((nid & 0xFF) << 24) | ((verb & 0xFFF) << 8) | (param & 0xFFFF)

def parse_node(text, nid):
    pat = re.compile(rf'^Node {nid} \[.*?(?=^Node |\Z)', re.M | re.S)
    m = pat.search(text)
    if not m:
        return None
    block = m.group(0)
    out = {'stream': 0, 'channel': 0, 'amp_l': 0x20, 'amp_r': 0x20}
    cm = re.search(r'Converter:\s*stream=(\d+),\s*channel=(\d+)', block)
    if cm:
        out['stream'] = int(cm.group(1)); out['channel'] = int(cm.group(2))
    am = re.search(r'Amp-Out vals:\s*\[([0-9a-fx]+)\s+([0-9a-fx]+)\]', block)
    if am:
        out['amp_l'] = int(am.group(1), 16); out['amp_r'] = int(am.group(2), 16)
    return out

def get_format(c, nid):   return c.raw(pack(nid, 0xA00, 0)) & 0xFFFF
def set_stream(c, nid, stream, ch): c.raw(pack(nid, 0x706, ((stream & 0xF) << 4) | (ch & 0xF)))
def set_format(c, nid, fmt):        c.raw(pack(nid, 0x200, fmt & 0xFFFF))
def set_amp(c, nid, gain, mute=False):
    gain = max(0, min(0x7F, gain))
    c.raw(pack(nid, 0x300, 0xB000 | (0x80 if mute else 0) | gain))

def mute_dacs(c):
    for nid in (0x22, 0x23):
        set_stream(c, nid, 0, 0)
        set_amp(c, nid, 0, mute=True)

def run_once(interval):
    """Returns when STOP is set; raises OSError on codec trouble."""
    path, proc = find_hwdep()
    if not path:
        raise OSError('codec not found')
    c = Codec(path)
    print(f'maintain-stream: {path}', flush=True)
    last = None
    try:
        while not STOP:
            text = open(proc, errors='ignore').read()
            src = parse_node(text, '0x11')
            if src:
                stream = src['stream']
                gain = min(src['amp_l'] & 0x7F, src['amp_r'] & 0x7F)
                muted = bool((src['amp_l'] | src['amp_r']) & 0x80) or stream == 0
                fmt = get_format(c, 0x11) if stream else 0
                state = (stream, fmt, gain, muted)
                if state != last:
                    if stream == 0:
                        set_stream(c, 0x22, 0, 0); set_stream(c, 0x23, 0, 0)
                        set_amp(c, 0x22, gain, mute=True); set_amp(c, 0x23, gain, mute=True)
                        print('idle - I2S DACs disconnected/muted', flush=True)
                    else:
                        set_format(c, 0x22, fmt); set_format(c, 0x23, fmt)
                        set_stream(c, 0x22, stream, 0); set_stream(c, 0x23, stream, 1)
                        set_amp(c, 0x22, gain, mute=muted); set_amp(c, 0x23, gain, mute=muted)
                        print(f'bound stream={stream} fmt=0x{fmt:04x} gain=0x{gain:02x} mute={muted}', flush=True)
                    last = state
                elif stream:
                    # parser may drop the binding without changing 0x11's state
                    set_stream(c, 0x22, stream, 0); set_stream(c, 0x23, stream, 1)
            time.sleep(interval)
    finally:
        try: mute_dacs(c)
        except OSError: pass
        c.close()

def main():
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else 0.2
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    fails = 0
    while not STOP:
        try:
            run_once(interval)
        except (OSError, ValueError) as e:
            fails += 1
            print(f'maintain-stream: {e}; reconnecting in 2s (#{fails})', flush=True)
            if fails >= 30:
                return 1            # let systemd restart us / give up visibly
            for _ in range(20):
                if STOP: break
                time.sleep(0.1)
    print('maintain-stream: stopped, I2S DACs muted', flush=True)
    return 0

if __name__ == '__main__':
    sys.exit(main())
