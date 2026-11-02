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
- `bin/maintain-stream.py` + `corebook-speakers-keepalive.service`: keeps DACs 0x22/0x23
  bound to the live PCM stream (idea and first implementation by calyx01).
  Handles SIGTERM, reconnects on codec errors.
- `COREBOOK_WIRE=1` (experimental): converts wire-format verb tables to the hwdep ioctl
  format so the kernel stops rejecting them ("out of range cmd" in dmesg).
- Retry (3x) of the bring-up, wait for the codec to appear (boot/resume race).
- `diagnose.sh`: collects a support report.
- `/etc/default/corebook-audio` configuration file.
- Installer checks: codec present, subsystem id (`FORCE=1` to override), dependencies.
