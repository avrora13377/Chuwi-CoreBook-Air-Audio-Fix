# How it works — technical deep dive

This document explains the complete audio architecture of the CoreBook Air Plus,
the hidden vendor protocol used to control the speaker amplifier, and every step
of the bring-up sequence.

---

## 1. The players

| Component | Role |
|---|---|
| **SN6180** (Conexant/Synaptics) | HDA codec on the AMD HD-Audio controller (card 1, `hwC1D0`). Contains the normal widget graph *plus* a hidden vendor node `0x1B`. |
| **SmartPA ("PA8022"-class)** | External Class-D amplifier with its own DSP. Drives both internal speakers. Receives audio digitally via I²S, is controlled via I²C at address `0x2d` behind the codec's bridge. |
| **Vendor driver `CHDRT64ISST.sys`** | Windows-only. Knows the bridge protocol, downloads the PA firmware, runs the init tables, manages GPIO4. This driver does not exist for Linux — that's the whole problem. |
| **GPIO4** | Embedded-controller GPIO (`gpiochip0`, line 4). PA enable line per INF parameter `GpioNumberForPA=4`. |

## 2. Audio path

The kernel's HDA auto-parser actually does most of the routing correctly:

```
PCM device (analog)  →  HDA stream tag 5
   ├─ channel 0 → DAC 0x22 → pin 0x1d (I²S out, hidden) ─┐
   └─ channel 1 → DAC 0x23 → pin 0x26 (I²S out, hidden) ─┤ I²S
                                                          ▼
                                                    SmartPA → speakers
```

You can verify this binding in `/proc/asound/card1/codec#0`
(`Converter: stream=5, channel=...` under nodes `0x22`/`0x23`).

What the kernel cannot do: initialize the amplifier itself.

## 3. The vendor bridge protocol (codec node 0x1B)

Node `0x1B` is a "Vendor Defined Widget" that implements an I²C master
connected to the SmartPA. All communication happens through ordinary HDA
verbs; a transaction is a small program of up to 50 verbs written into a
command buffer and then committed:

| Verb (on NID 0x1B) | Meaning |
|---|---|
| `0x520 ← 0x80|sub` | select sub-address |
| `0x521 ← addr` | select I²C device address (`0x2d` = the PA) |
| `0x526 / 0x527 ← hi/lo` | register address high/low byte |
| `0x52A+i ← byte` | data slot *i* (for writes) |
| `0x528 ← flags|(count-1)` | header: bit4=read, count of bytes |
| `0x529 ← 1` | commit (execute transaction) |
| `0x529 ← 2` | release |
| `0xD29 → status` | poll: bit1 = ready, bit2 = error |
| `0xD2A+i → byte` | pull result slot *i* (after reads) |

A register write therefore looks like:
```
[data_slot(0,val), HI=hi, LO=lo, HDR=0x00, COMMIT=1, COMMIT=2] + padding to 50
```

The PA itself has a small flat 8-bit-decoded register file (the high address
byte is ignored by the chip), plus a firmware/DSP area loaded by streaming
byte pairs.

### Firmware download

`firmware/pa8022_fw.hex` contains 2948 bytes extracted from the Windows
driver's INF file (`ext-OEM-common.inf`, key `8022Register`). It is written as
1474 `(register, value)` pairs through the framed protocol, followed by an init
table (`AMP_INIT`) that configures gain structure, boost converter, DSP routing
and finally takes the amp out of reset (`0x700 ← 3`).

## 4. The full bring-up sequence

Executed by [`bin/pa-bringup.py`](../bin/pa-bringup.py):

1. **Preamble** — pincfg override + codec-GPIO verbs from the INF (sent even
   though the codec reports zero GPIOs; Windows does the same).
2. **Engine verbs** — codec-internal engine setup observed at driver load.
3. **RMW unlock** — read-modify-write enable on `0x1B` registers
   (`0xB13|=2`, `0xB12|=0x20`).
4. **Fast link config** (`F7464_VERBS`) — pins powered, converter formats,
   both I²S link engines programmed for 48 kHz / BCLK÷1.
5. **Firmware download** — 1474 writes (~5 s).
6. **Slow link config** (`SLOW_A`/`SLOW_B`) — second-stage engine tuning.
7. **Amp init table** — the big register sequence, ending with both
   **link commit verbs** `0x1B62C0F` (link A) and `0x1B6310F` (link B).
8. **Power tail** — final power-state writes.
9. **Plumbing** — pins `0x1d`/`0x26`: OUT-enable + EAPD + D0; DACs `0x22`/`0x23`
   bound to stream tag 5, channels 0/1, format 48 kHz/16-bit/stereo.
10. **Sanity check** — read PA status register `0x7E`; a live chip returns a
    ticking value (`0xfa` when fully up).

## 5. GPIO4

`gpioset --chip=0 --daemon 4=1` holds line 4 of `gpiochip0` high forever
(daemon mode). Without it the PA's output stage stays disabled even with
perfect configuration.

## 6. Why the PipeWire dance?

The bring-up sequence leaves the two speaker DAC amps muted (the vendor init
tables include mute writes; Windows unmutes afterwards from its mixer state).

Naively unmuting fails twice over:

* `amixer cset` writes get overwritten/ignored while WirePlumber owns the card.
* `wpctl set-mute 0` alone is treated as *no-op* because WirePlumber's cached
  state already says "unmuted" — it never re-pushes values to hardware that was
  changed behind its back.

The reliable recipe (what `speaker-restore.sh` does):

```bash
wpctl set-mute @DEFAULT_AUDIO_SINK@ 1   # force a state change...
sleep 0.2
wpctl set-mute @DEFAULT_AUDIO_SINK@ 0   # ...so WirePlumber re-applies volume
wpctl set-volume @DEFAULT_AUDIO_SINK@ 1.0
```

After this, `/proc/asound/card*/codec#0` shows both DACs as `[0x4a 0x4a]`
(full volume, unmuted) and sound flows.

> Historical note: a stale WirePlumber mute was also responsible for weeks of
> confusion during reverse-engineering — the amp had been correctly configured
> more than once before, but one stereo path stayed hardware-muted regardless.

## 7. Persistence model

Nothing about this fix lives in the kernel; all state (codec engine regs, PA
RAM, pin settings) is volatile and lost on power-off or suspend. Hence:

* `corebook-speakers.service` re-runs everything at boot.
* A `system-sleep` hook re-runs it after resume.

Both are installed by `install.sh`.
