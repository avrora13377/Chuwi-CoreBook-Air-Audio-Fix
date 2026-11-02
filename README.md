# Chuwi CoreBook Air Plus — Internal Speaker Fix for Linux

[![laptop](https://img.shields.io/badge/laptop-Chuwi%20CoreBook%20Air%20Plus-blue)]()
[![audio](https://img.shields.io/badge/audio-SN6180%20%2B%20SmartPA-orange)]()
[![works](https://img.shields.io/badge/status-WORKING-brightgreen)]()

This repository makes the **internal speakers work on Linux** on the
Chuwi CoreBook Air Plus (AMD Ryzen 5 6600H / Conexant Synaptics SN6180 codec).
It should also help with any other machine that uses a **Conexant/Synaptics
SN61xx-class codec driving an external SmartPA** through the same hidden vendor
interface.

Out of the box, Linux plays audio fine to headphones/external displays, but the
built-in speakers stay completely silent. This is not a volume setting, not a
missing ALSA control and not a kernel bug — it is a missing vendor driver.

---

## TL;DR — just make it work

```bash
git clone <this-repo>
cd corebook-air-plus-audio-fix
sudo ./install.sh
```

Sound works immediately. The installer also registers a systemd service so the
speakers are re-initialized automatically at every boot **and after every
suspend/resume**.

Reboot test recommended. Remove anytime with `sudo ./uninstall.sh`.

---

## Why are the speakers silent?

Your speakers have no analog connection to the sound chip at all:

```
                 ┌──────────────────────────────┐
 PCM playback ──►│ SN6180 HDA codec             │
 (stream tag 5)  │                              │      I²S digital audio
                 │  DAC 0x22 ──► I²S-out pin ───┼──────┐
                 │  DAC 0x23 ──► I²S-out pin ───┼──┐   │
                 │                              │  │   ▼
                 │   HIDDEN VENDOR BRIDGE ══════╪══╪═══════╗   (I²C control)
                 │   (codec node 0x1B)          │  │       ▼
                 └──────────────────────────────┘  │  ┌──────────────┐
                                                   └─►│   SmartPA    │──► 🔊
                                                      │ (+ its DSP)  │
                                                      └──────────────┘
```

The SmartPA ("PA8022"-class chip) is a small computer of its own. It needs its
**firmware downloaded** and dozens of registers configured before it will pass
any sound to the speakers. On Windows this is done by Conexant's vendor driver
(`CHDRT64ISST.sys`). That driver does not exist for Linux, the kernel has no idea
the PA exists, and **nothing ever initializes it** — so the amp sits there,
healthy but mute.

Two more things must be right at the same time:

1. **GPIO4** on the embedded controller must be held high (the PA's enable line,
   per the Windows INF parameter `GpioNumberForPA=4`).
2. **The desktop sound server must not be muting the speaker path.**
   WirePlumber/PipeWire can hold a stale mute that maps onto the speaker DACs —
   the mixer UI shows "100 %, unmuted" while the hardware DAC is actually muted.
   This alone causes silence even with a perfectly configured amplifier, and it
   silently defeated every earlier attempt until we caught it red-handed.

## What this fix does

`bin/pa-bringup.py` talks to the codec's *hidden vendor bridge* directly through
the standard HDA hwdep interface and replicates the entire Windows driver boot
sequence:

| Step | What happens |
|------|--------------|
| 1 | Codec "preamble" + engine verbs (as sent by the Windows driver) |
| 2 | Unlock the bridge's read-modify-write mode |
| 3 | Fast I²S/link configuration for both stereo links |
| 4 | **Download the 1474-word PA firmware blob** (`firmware/pa8022_fw.hex`) |
| 5 | Slow link configuration (both links A/B) |
| 6 | Full amplifier register init table + link commit commands |
| 7 | Power-up tail sequence |
| 8 | Wire the mixer graph: DACs → I²S pins → PA, bind them to your PCM stream |
| 9 | Sanity-check that the PA answers |

Then `bin/speaker-restore.sh` holds GPIO4 high and forces PipeWire/WirePlumber
to re-apply its volume state onto the speaker DACs (a plain "unmute" is not
enough — see [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md)).

Everything was obtained by reverse-engineering the Windows driver; no proprietary
code is included, only the raw register/firmware data it sends to the hardware.

---

## Verify it worked

```bash
grep -A8 'Node 0x22 \[' /proc/asound/card1/codec#* | grep Amp-Out
```

Both values should read `[0x4a 0x4a]` (full volume, unmuted). Then play anything.

Service logs: `journalctl -u corebook-speakers`

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — covers "worked until
suspend", "silent after reboot", headphone-vs-speaker confusion, adaption to
other laptop models, and how to check each layer individually.

## Technical deep-dive

- [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) — the audio architecture, the
  vendor bridge protocol, every step of the bring-up explained.
- [docs/REVERSE-ENGINEERING.md](docs/REVERSE-ENGINEERING.md) — how the protocol
  was reconstructed from `CHDRT64ISST.sys` and why some dead ends were dead.

## Scope & compatibility

Tested on: CoreBook Air Plus (Ryzen 5 6600H), CachyOS (kernel 7.1.x), PipeWire.
The approach applies to any board where the SN61xx codec drives an external PA
through node 0x1B, but firmware/init data may differ per model — check your
Windows driver's INF (`ext-OEM-common.inf`, key `8022Register`) if adapting.

Headphones, HDMI/display audio and microphones are unaffected by this fix (they
never used the PA anyway).

## Credits

Reverse-engineered from the vendor driver with lots of framed-I²C packet
capturing, disassembly and stubbornness. Similar prior art:
the CoreBook X "left speaker" project project for the CoreBook X
(different machine, different mechanism, endless inspiration).

## License

MIT — see [LICENSE](LICENSE).
