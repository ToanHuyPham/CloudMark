# Disk methodology

Storage is CloudMark's first production-oriented executor. A single throughput
number is insufficient for databases, web applications, media, backup, or
analytics. Every profile therefore combines workload shape, queue depth, tail
latency, CPU cost, and one-second behavior over time.

## Safety boundary

All profiles use one temporary file inside the configured workspace. Preflight
requires the file size plus the larger of 1 GiB or 5% of total filesystem
capacity to remain free. CloudMark never accepts a raw device, never formats a
volume, and removes the temporary file and fio logs on completion, failure,
timeout, or cancellation.

## Versioning

Storage results record:

- profile version;
- `storage-v1` methodology version;
- exact fio version;
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

## Reported evidence

Each job retains:

- read and write bytes, IOPS, and bytes per second;
- P50/P90/P95/P99/P99.9 completion latency;
- user and system CPU utilization;
- actual runtime;
- one-second bandwidth, IOPS, and latency points separated by direction;
- the complete workload definition.

One-second fio logs use KiB/s for bandwidth and nanoseconds for latency;
CloudMark normalizes them to bytes/s and milliseconds before persistence.

## Interpretation

- QD1 random latency influences OS boot and latency-sensitive applications.
- 4/8/16 KiB random and synchronous writes influence transactional databases.
- Large sequential throughput influences media, backup, restore, and analytics.
- Mixed sustained time series reveal burst-credit exhaustion and throttling.
- CPU per unit of I/O helps detect an instance bottleneck rather than a storage limit.

CloudMark does not infer durability, replication, snapshot quality, or SLA from
disk performance. Those claims require provider API evidence and verified
restore drills.
