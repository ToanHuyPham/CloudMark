# Clock environment methodology

## Scope

`clock-environment-v1` is a read-only inventory observation for benchmark and
evidence timestamp interpretation. It records portable Python clock semantics
on every supported platform and bounded Linux clocksource, time-namespace, and
systemd time assertions when available.

It is not an NTP benchmark or an independent clock-synchronization validator.
CloudMark does not contact a time peer, inspect NTP packets, query the RTC,
measure absolute offset/drift/jitter, or change any clock/time service.

## Fixed evidence sources

Portable evidence uses Python `time.get_clock_info()` for `time`, `monotonic`,
and `perf_counter`, retaining implementation, resolution, monotonicity, and
adjustability.

Linux additionally reads:

- `current_clocksource`, capped at 128 bytes;
- `available_clocksource`, capped at 4 KiB and 32 normalized identifiers; and
- `/proc/self/timens_offsets`, capped at 4 KiB and limited to `monotonic` and
  `boottime` rows.

Raw sysfs/procfs text and time-namespace identifiers are not persisted.

When `timedatectl` is installed, CloudMark executes exactly two fixed read-only
property queries with a three-second deadline and 32-byte output cap:

```text
timedatectl --no-pager show --property=NTP --value
timedatectl --no-pager show --property=NTPSynchronized --value
```

The systemd source maps these as separate boolean properties, and documents
`show`/property output for machine consumption:
[timedate1 D-Bus contract](https://github.com/systemd/systemd/blob/main/man/org.freedesktop.timedate1.xml),
[timedatectl implementation](https://github.com/systemd/systemd/blob/main/src/timedate/timedatectl.c).

CloudMark labels both values as operating-system assertions. It does not infer
the active NTP implementation or peer, and
`cloudmark_ntp_validation_performed=false`, `offset_measured=false`, and
`drift_measured=false` remain explicit.

## Status and interpretation

Evidence is `complete` only when the portable clocks, Linux clocksource,
time-namespace rows, and both fixed systemd properties are observed. Missing,
malformed, truncated, unsupported, or timed-out components produce `partial`
evidence rather than a pass/fail conclusion. Non-Linux platforms retain the
portable clock semantics and report Linux/systemd fields unavailable.

A non-zero time-namespace offset is guest configuration evidence, not measured
provider clock error. A synchronized systemd assertion is not proof of current
offset, upstream accuracy, holdover quality, leap handling, or cross-machine
alignment. Provider comparison and workload suitability do not consume this
observation as a scored metric.
