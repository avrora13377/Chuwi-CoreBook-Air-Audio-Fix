#!/usr/bin/env python3
"""Offline tests for corebook-x/aw88298-daemon.py using a fake codec and a fake amplifier.

They check that the Python port issues the same hardware operations as pacomont's shell
original (aw88298-daemon.sh): same registers, same values, same order, same endianness.
They do NOT test the real ioctls - that can only be done on the machine
(sudo corebook-x/aw88298-daemon.py --probe).

Run: python3 tests/test_corebook_x.py
"""
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    'aw', os.path.join(HERE, '..', 'corebook-x', 'aw88298-daemon.py'))
aw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aw)
aw.log = lambda *_: None


class FakeI2c:
    def __init__(self):
        self.regs = {0x00: 0x1852, 0x04: 0x4003, 0x61: 0x6673}
        self.writes = []

    def read_be(self, reg):
        return self.regs.get(reg, 0)

    def write_be(self, reg, value):
        self.writes.append((reg, value))
        self.regs[reg] = value


class FakeHda:
    def __init__(self):
        self.stream = 0x00
        self.fmt = {0x11: 0x4011, 0x22: 0x4011}
        self.amp11 = 0x4A
        self.calls = []

    def verb(self, nid, verb, param=0):
        self.calls.append((nid, verb, param))
        if (nid, verb) == (0x11, 0xF06):
            return self.stream
        if verb == 0xA00:
            return self.fmt[nid]
        if (nid, verb) == (0x11, 0xB00):
            return self.amp11
        if (nid, verb) == (0x11, 0x706):       # DAC 0x11 channel changed by apply_stream
            self.stream = param
        if (nid, verb) == (0x22, 0x200):
            self.fmt[0x22] = param
        return 0


def make():
    hda, i2c = FakeHda(), FakeI2c()
    return aw.Daemon(hda, i2c, sleep=lambda *_: None), hda, i2c


class Tests(unittest.TestCase):
    def test_setup_amp_register_sequence_matches_shell_original(self):
        d, hda, i2c = make()
        d.setup_amp()
        # shell: i2cset ... 0x04 0x0020 / 0x04 0x4070 / 0x05 0x6B00 / 0x06 0xE814 /
        #        0x09 0x4079 / 0x0A 0x7000, then RMW of 0x61. Those are little-endian words;
        #        the chip sees low byte first, i.e. big-endian value = byte-swapped word.
        sw = lambda le: ((le & 0xFF) << 8) | (le >> 8)
        expected = [(0x04, sw(0x0020)), (0x04, sw(0x4070)), (0x05, sw(0x6B00)),
                    (0x06, sw(0xE814)), (0x09, sw(0x4079)), (0x0A, sw(0x7000)),
                    (0x61, 0x1673)]
        self.assertEqual(i2c.writes, expected)
        self.assertEqual(expected[0][1], 0x2000)
        self.assertEqual(expected[1][1], 0x7040)      # the daemon's own comment: BE 0x7040
        self.assertEqual(expected[3][1], 0x14E8)      # ... and BE 0x14E8
        self.assertTrue(d.amp_on)

    def test_force_boost_preserves_other_bits(self):
        d, _, i2c = make()
        i2c.regs[0x61] = 0x6673                       # reset value documented by pacomont
        d.force_boost()
        self.assertEqual(i2c.regs[0x61], 0x1673)      # documented result
        i2c.regs[0x61] = 0xE5A5                       # bit 15 and low bits must survive
        d.force_boost()
        self.assertEqual(i2c.regs[0x61], (0xE5A5 & 0x8FFF) | 0x1000)

    def test_hda_pin_and_power_cycle_verbs(self):
        d, hda, _ = make()
        d.setup_amp()
        c = hda.calls
        self.assertEqual(c[:4], [(0x1D, 0x705, 3), (0x22, 0x705, 3), (0x22, 0x705, 0), (0x1D, 0x705, 0)])
        self.assertIn((0x1D, 0x707, 0x40), c)
        self.assertIn((0x1D, 0x70C, 0x02), c)

    def test_apply_stream_splits_channels(self):
        d, hda, _ = make()
        d.apply_stream(0x50)                          # stream 5, channel 0
        self.assertIn((0x11, 0x706, 0x51), hda.calls)  # right
        self.assertIn((0x22, 0x706, 0x50), hda.calls)  # left
        self.assertIn((0x22, 0x200, 0x4011), hda.calls)

    def test_volume_mirror_offset_and_mute(self):
        d, hda, _ = make()
        hda.amp11 = 0x4A
        d.mirror_volume()
        self.assertIn((0x22, 0x300, 0xB040), hda.calls)   # 0x4a - 10 = 0x40 (pacomont's calibration)
        self.assertIn((0x22, 0x300, 0x9040), hda.calls)
        hda.calls.clear()
        hda.amp11 = 0x80 | 0x4A                           # headphones plugged: DAC 0x11 muted
        d.mirror_volume()
        self.assertEqual(hda.calls[-2:], [(0x22, 0x300, 0xB080), (0x22, 0x300, 0x9080)])
        hda.calls.clear()
        d.mirror_volume()                                 # unchanged -> no traffic
        self.assertEqual([c for c in hda.calls if c[1] == 0x300], [])

    def test_volume_clamped(self):
        d, hda, _ = make()
        hda.amp11 = 0x05
        d.mirror_volume()
        self.assertIn((0x22, 0x300, 0xB000), hda.calls)

    def test_tick_applies_stream_then_idles(self):
        d, hda, i2c = make()
        d.setup_amp()
        i2c.writes.clear(); hda.calls.clear()
        hda.stream = 0x50                                  # playback starts
        d.tick()
        self.assertIn((0x11, 0x706, 0x51), hda.calls)
        self.assertEqual(i2c.writes, [])                   # healthy amp: nothing rewritten
        # The shell original also re-applies once more on the next poll: DAC 0x11 now reports
        # 0x51 (channel 1) instead of the 0x50 it saw before. It is idempotent; the port keeps it.
        d.tick()
        hda.calls.clear()
        d.tick()                                           # steady state: no re-apply
        self.assertNotIn((0x22, 0x706, 0x50), hda.calls)
        self.assertEqual(i2c.writes, [])

    def test_tick_recovers_powered_down_amp(self):
        d, hda, i2c = make()
        d.setup_amp()
        hda.stream = 0x50
        d.tick()
        i2c.writes.clear()
        i2c.regs[0x04] = 0x4003                            # amp lost power (e.g. after resume)
        d.tick()
        self.assertEqual(i2c.writes[0], (0x04, 0x2000))    # full setup_amp again

    def test_tick_repairs_boost_drift(self):
        d, hda, i2c = make()
        d.setup_amp()
        hda.stream = 0x50
        d.tick()
        i2c.regs[0x61] = 0x6673
        i2c.writes.clear()
        d.tick()
        self.assertEqual(i2c.writes, [(0x61, 0x1673)])

    def test_shutdown_sequence(self):
        d, hda, i2c = make()
        d.setup_amp()
        i2c.writes.clear()
        d.shutdown_amp()
        self.assertEqual(i2c.writes, [(0x05, 0x0038), (0x04, 0x4003)])   # = shell 0x3800 / 0x0340 LE
        self.assertFalse(d.amp_on)

    def test_wire_encoding_big_endian(self):
        sent = []
        i2c = aw.I2c.__new__(aw.I2c)
        i2c._xfer = lambda msgs: sent.append([bytes(__import__('ctypes').string_at(m.buf, m.len)) for m in msgs])
        i2c.write_be(0x04, 0x7040)
        # i2cset -y 0 0x34 0x04 0x4070 w  puts [reg, 0x70, 0x40] on the wire
        self.assertEqual(sent[0][0], bytes([0x04, 0x70, 0x40]))

    def test_pack_matches_hda_verb_encoding(self):
        # hda-verb builds (nid << 24) | (verb << 8) | param
        self.assertEqual(aw.pack(0x22, 0x300, 0xB040), (0x22 << 24) | (0x300 << 8) | 0xB040)
        self.assertEqual(aw.pack(0x1D, 0x707, 0x40), 0x1D070740)

    def test_refuses_unexpected_chip(self):
        from unittest import mock
        i2c = FakeI2c()
        i2c.regs[0x00] = 0x1234
        closed = []
        i2c.close = lambda: closed.append('i')

        class H:
            def __init__(s, p): pass
            def close(s): closed.append('h')

        with mock.patch.object(aw, 'find_codec', lambda: '/dev/null'), \
             mock.patch.object(aw, 'find_i2c_bus', lambda: 0), \
             mock.patch.object(aw.os.path, 'exists', lambda p: True), \
             mock.patch.object(aw, 'Hda', H), \
             mock.patch.object(aw, 'I2c', lambda bus: i2c):
            with self.assertRaises(SystemExit) as cm:
                aw.open_devices()
        self.assertIn('refusing to write', str(cm.exception))
        self.assertEqual(i2c.writes, [])
        self.assertEqual(sorted(closed), ['h', 'i'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
