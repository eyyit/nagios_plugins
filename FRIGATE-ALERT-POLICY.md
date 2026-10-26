# Frigate camera alert policy, 2026-10-26

The camera checks distinguish active failures from already-recovered recording
gaps. Sensor collector, memory check, and unrelated services are unchanged.

* A single recovered gap does not page. It remains visible in check output,
  perfdata and the Frigate recording history.
* The rolling one-hour budget pages for at least three gaps of 30 seconds or
  more, or at least 180 seconds missing in gaps of five seconds or more. Smaller
  gaps are excluded to avoid normal segment-boundary jitter.
* Missing/disabled recording, missing files, and active recording age over the
  existing 120-second threshold remain faults. The existing three Nagios
  attempts mean notification is not instantaneous at the age threshold.
* Video corruption, missing/truncated/undecodable audio, and A/V timestamp or
  video-tail mismatch still page when multiple spaced samples fail. The latest
  three clips are sampled at least 25 seconds apart, spanning at least 45s.
* Audio presence/decoding and A/V timing are separate counters (`audio_bad`
  versus `av_timing_bad`). Decodable audio with a long video tail gap is not
  described as corrupt audio. Silence remains valid.
* All proven camera faults use one CRITICAL incident level, avoiding the
  previous WARNING-to-CRITICAL-to-WARNING notification chain for the same
  fault. UNKNOWN remains distinct for failed diagnostic operations. Existing
  `notification_interval=0`, recovery notifications and five-minute startup
  grace remain unchanged.

Run `python3 check_frigate_test.py` before installation. Tests cover hourly
count/loss boundaries, budget expiration, active outages, audio decode rules,
separate timing classification and severity aggregation. Source and tests live
in `/opt/repos/nagios_plugins`; no Nagios reload is needed for plugin updates.

Rollback copies are outside Git under
`/ssd/docker/frigate/alert-timing-20261026/`. Restore `check_frigate` as mode 0755
and `check_frigate_test.py` as mode 0644. Speaker mappings are merged separately
in `/etc/nagios/nrpe_local.cfg`; restoring its rollback copy requires reloading
`nagios-nrpe-server`. Do not reset repositories or overwrite unrelated edits.
