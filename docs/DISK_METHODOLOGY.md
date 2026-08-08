# Disk methodology

Storage is CloudMark's priority module. A single throughput number is not enough
to classify a volume for databases, web applications, media, backup, or big
data.

## Current profiles

### Disk Quick

- 512 MiB temporary file;
- sequential 1 MiB read and write at queue depth 8;
- random 4 KiB read at queue depth 1;
- mixed 70/30 random 4 KiB at queue depth 16;
- expected duration approximately four minutes.

### Disk Standard

- 4 GiB temporary file;
- sequential read/write;
- 4 KiB QD1 and QD32 random tests;
- mixed 70/30 sustained phase;
- 8 KiB synchronous database-style write;
- expected duration approximately 25 minutes.

## Reported evidence

Each job retains IOPS, bytes/second, CPU utilization, and P50/P90/P95/P99/P99.9
latency where supplied by `fio`. Read and write metrics remain separate.

## Interpretation

- QD1 random latency influences OS boot and latency-sensitive applications.
- 4/8/16 KiB random and synchronous write influence transactional databases.
- Large sequential throughput influences media, backup, restore, and analytics.
- Mixed sustained jobs reveal burst-credit exhaustion and provider throttling.

CloudMark will not infer durability, replication, snapshot quality, or SLA from
disk performance. Those require provider API evidence and restore tests.

## Extended storage profile roadmap

The deep profile will add longer steady-state observation, per-second time
series, metadata/small-file workloads, integrity verification, and optional
dedicated-device testing protected by mounted/OS-disk refusal gates.
