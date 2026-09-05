# PostgreSQL checkpoint-isolation methodology

`database-postgresql-checkpoint-v1` is a separate two-Agent profile for
observing a forced PostgreSQL checkpoint after a fixed durable write workload.
It does not change the PostgreSQL Quick or Standard contracts and does not
route benchmark traffic through the Controller.

## Workload sequence

1. The Target creates the normal isolated CloudMark PostgreSQL cluster at
   scale factor 20 with `fsync`, full-page writes, and synchronous commit on.
2. The Target executes one fixed `CHECKPOINT`, waits for completion, and then
   captures the baseline cumulative checkpointer counters.
3. The Generator runs the built-in TPC-B-like pgbench workload with four
   clients, two worker threads, five seconds of warm-up, and sixty measured
   seconds. Linux process and host CPU are sampled at one-second intervals.
4. The Target executes a second fixed `CHECKPOINT`, records Target wall-clock
   duration, and captures the post-load counters.
5. The Controller derives non-negative counter deltas and requires at least one
   requested checkpoint increment before the result is comparison-eligible.
6. The Target stops PostgreSQL and verifies removal of the generated cluster.

The API cannot provide SQL, a checkpoint mode, data directory, database name,
scale, duration, client count, thread count, address, or port.

## Version-aware evidence

For PostgreSQL 9.x through 16, CloudMark reads the fixed cumulative fields from
`pg_stat_bgwriter`: requested/timed checkpoint count, checkpoint write time,
checkpoint sync time, and checkpoint buffers written.

For PostgreSQL 17 and newer, it reads the corresponding fields from
`pg_stat_checkpointer`: requested/timed count, write time, sync time, and
buffers written. The Agent normalizes both schemas into one evidence shape but
retains `server_version_num` and the source-view name.

The forced-checkpoint duration is Target wall-clock time around one local psql
invocation and therefore includes small psql startup/connection overhead. It is
not transaction tail latency. Cumulative write and sync time deltas may include
another scheduled checkpoint if one legitimately occurs inside the bracket;
the timed/requested deltas keep that event visible.

## Comparison validity

Comparison eligibility requires:

- a complete baseline and post-load snapshot from the same server version and
  statistics view;
- no counter reset or decrease;
- at least one requested-checkpoint increment;
- a finite non-negative forced-checkpoint duration;
- observed Generator CPU for the pgbench job below 90% of one logical core;
  and
- verified ephemeral PostgreSQL cleanup.

Repeated provider observations keep this metric inside the exact PostgreSQL
server-version, profile, methodology, topology, and evidence-class contract.
No workload-suitability threshold is invented from an uncalibrated checkpoint
duration.

## Safety and limitations

`CHECKPOINT` intentionally causes storage writes and synchronization, so this
profile requires the normal explicit database-load confirmation and must not
overlap another saturation suite on either Agent. It operates only on the
generated PostgreSQL cluster. It never issues a raw-device command, alters a
filesystem, resets PostgreSQL statistics, or changes a host PostgreSQL service.

This profile does not measure crash recovery, WAL replay, PITR, checkpoint
completion during concurrent user load, provider snapshots, power-loss safety,
replication lag, cross-zone recovery, or RPO/RTO. Abrupt host termination can
still leave a generated task directory for operator review under the existing
recovery procedure.
