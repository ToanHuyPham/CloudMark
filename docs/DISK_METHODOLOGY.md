# Disk methodology

Storage is CloudMark's first production-oriented executor. A single throughput
number is insufficient for databases, web applications, media, backup, or
analytics. CloudMark keeps two evidence families separate: direct-I/O `fio`
profiles for block-I/O behavior and a native filesystem profile for small-file
metadata, checksum, and fsync behavior.

## Safety boundary

All profiles use generated files inside the configured workspace. The `fio`
profiles use one exact test-file path. The filesystem profile uses one
Run-specific directory containing only fixed-size generated files. Preflight
requires the configured workspace allowance plus the larger of 1 GiB or 5% of
total filesystem capacity to remain free. CloudMark never accepts a raw device,
never formats a volume, and removes generated files and logs on completion,
failure, timeout, or cancellation.

## Versioning

Storage results record:

- profile version;
- `storage-v1` or `storage-filesystem-v1` methodology version;
- exact fio version or native executor and Python version;
- shared runner version;
- workload arguments and run topology;
- timestamps, phase, and terminal state.

Changing measurement semantics requires a new methodology version. Profile
changes that preserve semantics increment the profile version.

## Profiles

### Disk Quick

- 512 MiB temporary file;
- sequential 1 MiB read/write at queue depth 8;
- random 4 KiB read at queue depth 1;
- mixed 70/30 random 4 KiB at queue depth 16;
- intended for safety validation and a short first baseline.

### Disk Standard

- 4 GiB temporary file;
- sequential read/write;
- 4 KiB QD1 and QD32 random access;
- sustained mixed 70/30;
- 8 KiB synchronous database-style writes;
- intended for general provider comparison.

### Disk Database

- 2 GiB temporary file;
- 8 KiB QD1 reads and writes;
- 8 KiB QD16 reads;
- mixed 70/30 at queue depth 8;
- synchronous 8 KiB writes with fsync;
- intended for transactional database suitability evidence.

### Disk Throughput

- 4 GiB temporary file;
- sequential 1 MiB read/write at QD1 and QD16;
- mixed 128 KiB streaming workload;
- intended for backup, restore, media, and analytics throughput.

### Disk Sustained

- 8 GiB temporary file;
- five to ten minute random and mixed phases;
- sustained sequential write phase;
- explicit ramp time before measurement;
- intended to reveal burst-credit exhaustion and throttling.

### Filesystem Metadata & Integrity

- fixed 2,048-file, 32-directory shape with deterministic 4 KiB payloads;
- single-process sequential create, stat, read with SHA-256 verification,
  rename, and delete phases;
- a separate 128-file create/flush/fsync phase;
- directory fsync timing when supported by the operating system;
- per-operation throughput plus minimum, P50, P95, P99, and maximum latency;
- verified Run-directory cleanup after success, failure, timeout, or cancel;
- no `fio` dependency, so the installed CloudMark Agent itself is sufficient.

The profile intentionally reports a guest-filesystem-and-Python-runtime
measurement. It does not claim kernel-only metadata latency. Cache state is
not manipulated: create-followed-by-stat/read phases may use warm directory,
inode, or page cache, and that scope is recorded on every operation.

## Reported evidence

Each `fio` job retains:

- read and write bytes, IOPS, and bytes per second;
- P50/P90/P95/P99/P99.9 completion latency;
- user and system CPU utilization;
- actual runtime;
- one-second bandwidth, IOPS, and latency points separated by direction;
- the complete workload definition.

One-second fio logs use KiB/s for bandwidth and nanoseconds for latency;
CloudMark normalizes them to bytes/s and milliseconds before persistence.

Each native filesystem operation retains:

- operation count, elapsed time, and operations per second;
- minimum, P50, P95, P99, and maximum user-space elapsed latency;
- bytes processed where applicable;
- cache-scope disclosure;
- SHA-256 verified-file and mismatch counts for read phases;
- per-file fsync count and supported directory-fsync observation;
- workspace-removal evidence.

## Interpretation

- QD1 random latency influences OS boot and latency-sensitive applications.
- 4/8/16 KiB random and synchronous writes influence transactional databases.
- Large sequential throughput influences media, backup, restore, and analytics.
- Mixed sustained time series reveal burst-credit exhaustion and throttling.
- CPU per unit of I/O helps detect an instance bottleneck rather than a storage limit.
- Small-file operations influence package trees, source checkouts, mail queues,
  container layers, and metadata-heavy application workloads.
- Per-file fsync completion is application-visible durability-path evidence. It
  does not prove physical-media persistence or power-loss protection.

CloudMark does not infer durability, replication, snapshot quality, or SLA from
disk performance. Those claims require provider API evidence and verified
restore drills.

Compare only identical profile, methodology, executor/tool version,
architecture, OS, filesystem, mount options, storage allocation, cache context,
power context, and background-load policy. Never compare filesystem operations
per second directly with fio IOPS.
