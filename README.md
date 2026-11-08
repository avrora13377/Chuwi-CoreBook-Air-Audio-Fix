# Chuwi CoreBook Air Plus — Internal Speaker Fix for Linux

![laptop](https://img.shields.io/badge/laptop-Chuwi%20CoreBook%20Air%20Plus-blue)
![audio](https://img.shields.io/badge/audio-SN6180%20%2B%20SmartPA-orange)
![works](https://img.shields.io/badge/status-WORKING-brightgreen)

Makes the **internal speakers work on Linux** on the Chuwi CoreBook Air Plus
(AMD Ryzen 5 6600H / Conexant Synaptics SN6180 codec). It may also help on other machines
that use an SN61xx-class codec driving an external SmartPA through the same hidden vendor
interface (firmware/init data may differ — see *Scope*).

Out of the box Linux plays audio to headphones and HDMI, but the built-in speakers stay
silent. This is not a volume setting and not a kernel bug — it is a missing vendor driver.

---

## Supported machines

| Machine | Codec subsystem |
| ------- | --------------- |
| CoreBook **Air Plus** (Ryzen 5 6600H) | `0x20147054` |
| CoreBook **X** (Ryzen 5 7430U) | `0x27821221` |

`sudo ./install.sh` detects the machine from the codec's subsystem id and installs the matching
fix. Unknown machines are refused (override with `FORCE=1 PROFILE=...` at your own risk).
The CoreBook X profile was tested on one unit (Linux Mint 22.3, kernel 7.0.0-42): left speaker,
headphone mute and suspend/resume work. More reports from other units are welcome.
Details for the X are in [corebook-x/README.md](corebook-x/README.md).

## TL;DR

```
# needs: python3 and the libgpiod tools (gpioset)
#   Arch/CachyOS: sudo pacman -S libgpiod      Debian/Ubuntu: sudo apt install gpiod
#   Fedora:       sudo dnf install libgpiod-utils
git clone https://github.com/avrora13377/Chuwi-CoreBook-Air-Audio-Fix
cd Chuwi-CoreBook-Air-Audio-Fix
sudo ./install.sh
```

Two systemd services are installed: one re-initializes the amplifier at every boot **and
after every suspend/resume**, the other keeps the speaker DACs bound to the live audio
stream. Remove everything with `sudo ./uninstall.sh`.

If something is wrong, run `sudo ./diagnose.sh` and attach `/tmp/corebook-diagnose.txt`
to an issue.

---

## Why are the speakers silent?

The speakers have no analog connection to the sound chip. The codec sends I²S digital audio
to a SmartPA ("PA8022"-class chip) — a small computer of its own. It needs firmware
downloaded and dozens of registers configured before it passes sound. On Windows that is
done by Conexant's vendor driver (`CHDRT64ISST.sys`). Linux has no such driver, so nothing
initializes the amp and it sits there, healthy but mute.

Three more things must be right at the same time:

1. **GPIO4** (the PA enable line, INF `GpioNumberForPA=4`) must be held high — for as long
   as the system runs, not just during bring-up.
2. **The sound server must not mute the speaker path.** WirePlumber/PipeWire can hold a stale
   mute that maps onto the speaker DACs while the mixer UI shows "unmuted".
3. **DACs 0x22/0x23 must stay attached to the live PCM stream.** The HDA parser resets them
   on every playback start/stop (this is what `maintain-stream.py` handles).

## What this fix does

`bin/pa-bringup.py` talks to the codec's hidden vendor bridge (node 0x1B) through the HDA
hwdep interface and replays the Windows driver boot sequence: preamble, link configuration,
**PA firmware download** (`firmware/pa8022_fw.hex`), amp init table, power tail, DAC plumbing.

`bin/speaker-restore.sh` waits for the codec, holds GPIO4 high, runs the bring-up (up to 3
attempts), then toggles WirePlumber mute so the DACs receive the mixer state.

`bin/maintain-stream.py` (long-running) copies the live stream, format and gain from the
analog speaker DAC 0x11 onto the I²S DACs 0x22/0x23, so the volume slider works and the amp
is not left on without a stream. It reconnects on codec errors and mutes the DACs on stop.

| Unit | Role |
| ---- | ---- |
| `corebook-speakers.service` | oneshot, `RemainAfterExit=yes`, `KillMode=process`: bring-up; keeps the `gpioset` daemon alive |
| `corebook-speakers-keepalive.service` | runs `maintain-stream.py`, restarts on failure, restarted together with the first unit |
| `/usr/lib/systemd/system-sleep/corebook-audio` | restarts the first unit after resume |

Everything was obtained by reverse-engineering the Windows driver; no proprietary code is
included, only the raw register/firmware data it sends to the hardware.

## Configuration

`/etc/default/corebook-audio` (created by the installer):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `COREBOOK_WIRE` | `0` | `1` converts the "wire format" verb tables (nid in bits 27:20) to the hwdep ioctl format (nid in bits 31:24). Without it the kernel rejects those words (`out of range cmd` in `dmesg`) and they never reach the codec. **Experimental** — speakers work without it on tested machines. |
| `COREBOOK_SINK_VOLUME` | empty | e.g. `0.4`: set the default sink volume after each restore. Empty = do not touch. |
| `COREBOOK_GPIO_CHIP` / `COREBOOK_GPIO_LINE` | `0` / `4` | GPIO chip and line of the PA enable |
| `ALLOW_NO_GPIO` | unset | `1` = continue even if GPIO4 cannot be held (not recommended) |

After editing: `sudo systemctl restart corebook-speakers`.

## Verify

```
systemctl status corebook-speakers corebook-speakers-keepalive
journalctl -u corebook-speakers -u corebook-speakers-keepalive -b
pgrep -a gpioset                      # must show the process holding GPIO4
grep -A8 'Node 0x22 \[' /proc/asound/card*/codec#* | grep -E 'Amp-Out|Converter'
```

During playback, `0x22`/`0x23` should show the same stream as analog DAC `0x11` (often
stream 5), not stream 0. Note that `Amp-Out vals [0x4a 0x4a]` only means *maximum* gain
(`ofs=0x4a`), not "healthy" — with the volume slider lowered the value is smaller.

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md). Quick list:

- **Silent after boot / sound disappears after a while** — check `pgrep -a gpioset`. If
  nothing runs, GPIO4 is not held (missing `libgpiod` tools, or an old unit file where the
  `gpioset` daemon was killed when the oneshot service ended).
- **Way too loud** — the DACs sit at maximum gain, the loudness comes from the amp table.
  Lower the sink volume (`wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.4`) or set
  `COREBOOK_SINK_VOLUME`.
- **`journalctl -b | grep -i "ordering cycle"`** — a unit ordered `After=multi-user.target`
  while being `WantedBy=` it makes systemd drop a job. The shipped units avoid this.
- **Anything else** — `sudo ./diagnose.sh`.

## Technical deep-dive

- [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) — audio architecture and vendor bridge protocol.
- [docs/REVERSE-ENGINEERING.md](docs/REVERSE-ENGINEERING.md) — how the protocol was reconstructed.

## Scope & compatibility

Tested on: CoreBook Air Plus (Ryzen 5 6600H, codec subsystem `0x20147054`), CachyOS,
PipeWire; a second identical unit. The installer refuses to run if no Conexant codec is
found and requires `FORCE=1` on an unknown codec subsystem, because it writes raw vendor
commands to the hardware. Other boards may need their own firmware (`8022Register` in the
Windows driver's `ext-OEM-common.inf`), PA I²C address (`AMP_ADDR`) and init tables.

Headphones, HDMI audio and microphones are unaffected.

## CoreBook X (experimental)

On the X only the right speaker works out of the box. The left one is an AW88298 amplifier fed
over I2S by the Conexant codec, and nothing in Linux sets it up. `corebook-x/aw88298-daemon.py`
configures the amp over I2C, routes the left channel to the codec's extra DAC and keeps both
volumes in sync. Related kernel work: thesofproject/linux#5687. Read-only self-check:
`sudo python3 /usr/local/lib/corebook-audio/aw88298-daemon.py --probe`.
Start with a low volume and read [corebook-x/README.md](corebook-x/README.md) first.

## Tests

`python3 tests/test_corebook_x.py` checks the CoreBook X daemon against a fake codec and a fake
amplifier (same registers, values and order as the original daemon). It does not replace testing
on the real machine.

## Credits

- Reverse-engineered from the vendor driver with lots of framed-I²C packet capturing,
  disassembly and stubbornness.
- [calyx01](https://github.com/calyx01/Chuwi-CoreBook-Air-Audio-Fix) - fork for the 14"
  CoreBook Air: the DAC stream keep-alive idea, the HDA wire-format vs ioctl packing finding
  and libgpiod v2 handling.
- [pacomont/chuwi-corebook-x-left-speaker](https://github.com/pacomont/chuwi-corebook-x-left-speaker) -
  CoreBook X: audio path, AW88298 register values, Force Boost finding and the original daemon
  that `corebook-x/` is a port of (GPL-2.0-or-later).
- [BenoitAmauryHage] - Who opened this issue and tested every step on his own machine (CoreBook X).

## License

MIT — see [LICENSE](LICENSE), **except** `corebook-x/`, which is a derivative of a
GPL-2.0-or-later work and stays under GPL-2.0-or-later (see [corebook-x/COPYING](corebook-x/COPYING)).
