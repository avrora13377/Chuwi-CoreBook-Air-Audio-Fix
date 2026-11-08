# Chuwi CoreBook X support (experimental)

Left speaker on the **Chuwi CoreBook X** (Ryzen 5 7430U, Conexant SN6180 codec with subsystem
`0x2782:0x1221`, AWINIC AW88298 smart amplifier at I2C `0x34`).

Audio path: `HDA stream -> DAC 0x22 -> pin 0x1d -> I2S -> AW88298 -> left speaker`.
The right speaker is the codec's normal analog output and already works without any fix.

## Credit and license

All of the hardware knowledge here comes from **Francisco Montanes Garcia (@pacomont)**:
the audio path, the AW88298 register values, the Force Boost finding, the volume offset and the
suspend/resume recovery. `aw88298-daemon.py` is a port of his `aw88298-daemon.sh`
(<https://github.com/pacomont/chuwi-corebook-x-left-speaker>), so it is a derivative work and
stays under **GPL-2.0-or-later** (see `COPYING`). The rest of this repository keeps its own license.

Upstream context: thesofproject/linux#5687, bugzilla.kernel.org #221255. A proper kernel
driver (HDA side-codec) is in progress there; when it lands, this daemon becomes unnecessary.

## What the port changes compared with the shell original

- refuses to run unless the codec subsystem id is `0x27821221` **and** the amp answers with
  chip id `0x1852`; nothing is written before that check
- finds the hwdep device and the I2C bus by itself (no hard-coded `hwC1D0` / bus 0)
- one process with direct ioctls instead of `hda-verb`/`i2cset`/`i2cget` forks every second
  (no `alsa-tools` / `i2c-tools` needed)
- reconnects after codec/I2C errors, `Restart=on-failure` in the unit
- `--probe`: read-only self check

The register values and the order of operations are identical to the original; `tests/` checks
that against a fake codec and a fake amp.

## Install

```
sudo ./install.sh          # detects the CoreBook X by itself
```

The installer first runs `aw88298-daemon.py --probe` (read-only) and only enables the service if
the amplifier answers with the right chip id. Manual check at any time:

```
sudo python3 /usr/local/lib/corebook-audio/aw88298-daemon.py --probe
journalctl -u corebook-x-aw88298 -b
```

## Status / warnings

- **Tested by one CoreBook X owner** (Linux Mint 22.3, kernel 7.0.0-42): the left speaker works,
  headphone mute/unmute works, suspend/resume works, and it keeps working after a reboot and
  after a kernel update. It is still tested on a single unit, so treat it as experimental until
  more owners report back. The author of this repository does not own a CoreBook X.
- The logic is a faithful port of pacomont's daemon; the I2C/HDA ioctl layer of the port is new
  (checked by `tests/` with a fake codec and amplifier, and by the tester above).
- The daemon writes to the amplifier that drives a speaker. Start with a low volume.
- Do not install it together with the Air Plus fix (the installer refuses).
- Only the left speaker/amp at `0x34` is handled. The ACPI device lists a second address `0x35`;
  its role is unknown and it is never touched.
