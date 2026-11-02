# Reverse-engineering notes

How the bridge protocol and bring-up sequence were reconstructed. Included so
future adventurers don't have to walk the same dark road.

## Sources

* `CHDRT64ISST.sys` — the Windows vendor driver (Conexant/Synaptics "ISST"
  audio DSP runtime). Disassembled with capstone via a small VA→offset helper.
* `ext-OEM-common.inf` — driver configuration: verb tables, GPIO settings,
  `I2SBCLK=0xc00`, `GpioNumberForPA=4` and the `8022Register` firmware blob.
* Live probing through `/dev/snd/hwC1D0` ioctls (`HDA_IOCTL_VERB_WRITE`).

## Key discoveries (in order of pain)

1. **The codec lies about its size.** Standard dumps stop at node 0x20, but
   verbs to node `0x1B` are accepted — a vendor-defined widget implementing an
   I²C master with a 50-slot command buffer.
2. **Framed transactions.** A transaction is up to 50 verbs; slots `0x52A+i`
   carry write data, `0xD2A+i` pull read data, `0x529|1/2` commit/release,
   `0xD29` polls status (bit1 ready / bit2 error). Only device address `0x2d`
   ever answers on the bridge bus.
3. **The INF is the map.** Every verb table the Windows driver sends at load
   (`f_7464`, SLOW_A/B, AMP_INIT, POWER_TAIL) appears in or around the INF /
   driver init code — replicating them verbatim worked better than improving
   on them.
4. **Both commit verbs matter.** Link engines A and B each need their commit
   (`0x1B62C0F`, `0x1B6310F`). Missing one = half-dead I²S links.
5. **The PA register space is flat.** Reads alias at +0x600/+0x700; there is no
   paging despite the blob writing register `0x7F`. The high address byte is
   ignored by the chip.
6. **Dead ends worth remembering:** a mystery I²C device at bus-0 address
   `0x33` (not audio — registers evaporate); the "mega-function" in the driver
   that looked like an audio transport but was a pure Windows registry loader
   (15k lines, 279 ZwQueryValueKey calls); bulk-verb DMA paths that turned out
   to be plain sequential senders.
7. **The final boss was software, not silicon.** After the amp had been fully
   configured more than once, speakers stayed silent because WirePlumber kept
   one speaker DAC hardware-muted (`Amp-Out vals [0x80 0x80]`) while ALSA
   reported max/unmuted. Only a mute *toggle* through wpctl forces WirePlumber
   to re-push state onto hardware changed behind its back.

## Toolchain used

* Linux HDA hwdep ioctl interface (`0xC0084811` verb-write) from Python — no
  kernel patches needed for any of this.
* capstone disassembly of the .sys file at its image base.
* `i2cdetect`/`gpioinfo` sweeps to rule out alternate control planes.

## What remains unknown

* Exact meaning of most PA registers (we replicate values, we don't speak DSP).
* Whether the amp's I²S input could also be fed by other sources on custom boards.
* Why the codec accepts engine-register writes only in certain unlock states —
  partially mapped, good enough for a deterministic sequence.
