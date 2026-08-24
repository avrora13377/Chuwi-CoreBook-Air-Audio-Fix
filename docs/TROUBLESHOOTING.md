# Troubleshooting

## Quick layer-by-layer check

Run these after a fresh boot if speakers are silent:

```bash
# 1) Did the service run?
systemctl status corebook-speakers
journalctl -u corebook-speakers --no-pager | tail -20

# 2) Is everything alive? Just re-run the restore script:
sudo /usr/local/bin/corebook-speakers.sh
# its last line prints PA status [0x7E] — expect a non-zero value like 0xfa

# 3) Are the speaker DACs unmuted? (both should show [0x4a 0x4a])
grep -A8 'Node 0x22 \[' /proc/asound/card1/codec#* | grep Amp-Out

# 4) Is anything actually playing through the right device?
wpctl status          # default sink must be "Ryzen HD Audio Controller Analog Stereo"
speaker-test -D plughw:1,0 -c2 -t sine -l1
```

## Symptoms

### Silent after boot, works after running the script manually
The service ran before your desktop session existed, so the PipeWire resync
step had nothing to talk to — but the retry loop should cover that. Check the
log line `sink resynced for uid ...`. If missing: your user session lives under
a different UID path; adjust `/run/user/*` handling in
`/usr/local/bin/corebook-speakers.sh`.

### Silent after suspend/resume
The resume hook is not installed or failed. It lives in
`/usr/lib/systemd/system-sleep/corebook-audio`. Test with:
`sudo systemctl start corebook-speakers`.

### Only one channel / very quiet
Check both DAC amps (step 3 above). One showing `[0x80 0x80]` = muted:
re-run the restore script; the mute-toggle forces WirePlumber to re-push.

### Headphones work, speakers never do
That's the original bug — you're in the right repo. Headphone path doesn't go
through the SmartPA.

### Speakers work but no volume control changes loudness
Volume slider maps onto DAC amps only after the sink got resynced once.
Toggle mute twice in the audio applet.

## Adapting to another laptop

1. Confirm the codec: `grep -i conexant /proc/asound/card*/codec#*`
   (SN61xx family). Different vendor → different protocol, this repo won't work.
2. Extract *your* firmware: from the Windows driver package take
   `ext-OEM-common.inf`, find key `8022Register,1,<hex...>`, convert hex bytes
   to a file laid out like `firmware/pa8022_fw.hex` (plain hex, any whitespace).
3. PA I²C address may differ (`AMP_ADDR` in `bin/pa-bringup.py`; sweep it by
   polling status `0xD29` per candidate address).
4. Init tables may differ per board — compare against the driver binary's init
   functions if sound still fails.

## Manual full reset (clean slate)

```bash
# stop sound servers so modules can unload
systemctl --user stop pipewire pipewire.socket wireplumber pulseaudio
sudo sh -c 'rmmod snd_hda_intel snd_hda_codec_atihdmi snd_hda_codec_hdmi \
  snd_hda_codec_conexant snd_hda_codec_generic snd_hda_codec snd_hda_core; \
  modprobe snd_hda_intel'
sudo /usr/local/bin/corebook-speakers.sh
```

Note the codec keeps register state across module reloads; only a real power
cycle clears everything.
