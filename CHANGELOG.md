# Changelog

## Unreleased

### Fixed
- **GPIO4 was released right after boot.** `corebook-speakers.service` was `Type=oneshot` with
  `RemainAfterExit=no`; systemd killed the `gpioset` daemon (PA enable) when the script
  exited. Now `RemainAfterExit=yes` + `KillMode=process`.
- GPIO4 is held **before** the PA bring-up and is no longer dropped/re-claimed afterwards
  (`pkill gpioset` after bring-up removed).
- Removed `After=multi-user.target` from a unit that is `WantedBy=multi-user.target`
  (ordering cycle).
- Resume hook uses `systemctl restart` (a `start` is a no-op with `RemainAfterExit=yes`).
- `speaker-restore.sh` no longer forces the sink volume to 100% (`COREBOOK_SINK_VOLUME`
  is opt-in).
- `pa-bringup.py` no longer falls back to a hard-coded `/dev/snd/hwC1D0`.
- libgpiod v2 (`--daemonize`) vs v1 handling; a missing `gpioset` is now an error.

### Added
- **CoreBook X support (experimental)**: `corebook-x/aw88298-daemon.py`, a port of pacomont's
  AW88298 left-speaker daemon (GPL-2.0-or-later) with chip-id/subsystem guards, automatic hwdep and
  I2C bus discovery, direct ioctls and reconnect logic; `--probe` read-only mode; its own systemd
  unit; offline tests in `tests/`. Confirmed on one CoreBook X (Linux Mint 22.3, kernel 7.0.0-42):
  left speaker, headphones, suspend/resume, reboot and kernel update.
- `install.sh` detects the machine (`0x20147054` Air Plus, `0x27821221` CoreBook X) and installs
  the matching fix; `uninstall.sh` removes either.
- `diagnose-x.sh`: read-only hardware report for CoreBook X bug reports.
- `bin/maintain-stream.py` + `corebook-speakers-keepalive.service`: keeps DACs 0x22/0x23
  bound to the live PCM stream (idea and first implementation by calyx01).
  Handles SIGTERM, reconnects on codec errors.
- `COREBOOK_WIRE=1` (experimental): converts wire-format verb tables to the hwdep ioctl
  format so the kernel stops rejecting them ("out of range cmd" in dmesg).
- Retry (3x) of the bring-up, wait for the codec to appear (boot/resume race).
- `diagnose.sh`: collects a support report.
- `/etc/default/corebook-audio` configuration file.
- Installer checks: codec present, subsystem id (`FORCE=1` to override), dependencies.
