# CloudMark technical roadmap

This roadmap separates **measurement**, **evidence**, and **scoring**. CloudMark
concludes that a system fits a workload only when the required metrics and run
confidence are available. CPU names, RAM capacity, and provider marketing never
replace benchmarks.

## Product scope

The milestone sequence below is an **implementation order**, not a limit on
CloudMark's scope. The product catalog contains 17 technical domains:

1. system and hardware inventory;
2. provider and instance identity;
3. virtualization and topology;
4. CPU and compute;
5. memory and NUMA;
6. storage, filesystem, and object services;
7. network and connectivity;
8. GPU and accelerators;
9. web, API, and TLS;
10. database and cache;
11. containers and Kubernetes;
12. security and isolation;
13. reliability, HA, and DR;
14. observability and operations;
15. provisioning and control plane;
16. cost and efficiency;
17. consistency and noisy-neighbor behavior.

See [`ASSESSMENT_CATALOG.md`](ASSESSMENT_CATALOG.md) for metrics, minimum
topology, and the state of each domain. Every domain has equal product-level
scope. Storage is implemented first because it is an operator priority and its
safe executor is more mature.

## Fixed principles

- The Controller coordinates and stores results; it never joins the benchmark data path.
- Provider network performance is measured directly between Agent A and Agent B.
- Raw results are immutable and version the methodology, tool, and profile.
- Every conclusion identifies passing metrics, missing metrics, and confidence reductions.
- One run on one VM never represents an entire provider.
- VM performance cannot establish durability, SLA, snapshot, managed-service,
  or compliance claims.

## M0 — Safe foundation (available in 0.1.0)

- cross-platform inventory;
- trusted AWS, Azure, and Google Cloud metadata detection;
- `declared, unverified` manifests for regional and self-hosted clouds;
- Controller API, write token, SQLite WAL, and run history;
- bootstrap plans for apt, dnf/yum, and zypper;
- filesystem-safe `fio`, temporary files, free-space reserve, and no raw devices;
- 30-minute pairing sessions, up to eight agents, and `ready` after two agents join;
- local dashboard and OpenAPI v1.

## M1 — Storage qualification (first executor priority)

Version `0.2.0` delivers the shared cancellable runner, five filesystem-safe
profiles, versioned results, one-second fio bandwidth/IOPS/latency logs, and
partial-result persistence. The remaining M1 items below continue toward full
storage-service qualification.

### Block and local storage

- sequential 1 MiB reads/writes with single and multiple jobs;
- random 4/8/16 KiB at QD1, QD4, QD16, QD32, and QD64;
- mixed 70/30 and 50/50 workloads;
- synchronous database writes and fsync/fdatasync latency;
- P50/P90/P95/P99/P99.9, bandwidth, IOPS, and CPU per IOPS;
- per-second time series for burst-credit and throttling detection;
- separate warm-up, steady-state, and cooldown phases;
- multiple working-set sizes to reduce cache distortion;
- filesystem metadata and small-file profiles;
- integrity checksums after write/read;
- SMART/NVMe health only when the OS and provider permit it.

### Storage services and backup

- object-storage PUT/GET/list/delete across object sizes;
- multipart upload, time to first byte, and concurrency scaling;
- snapshot create/restore time through provider adapters;
- backup/restore throughput with checksum verification;
- RPO/RTO drills using 2–3 nodes and a control-plane adapter.

M1 emits separate capabilities for transactional databases, latency-sensitive
web, general purpose, analytics throughput, media scratch, and backup targets.
It does not collapse everything into one disk score.

## M2 — Provider-internal network executor (partial in 0.3.0)

Required topology: Controller + Agent A + Agent B.

- TCP A→B and B→A using 1/4/8/16 streams — available;
- authenticated agent heartbeat and durable allow-listed task queues — available;
- fixed port range, duration/stream caps, one-shot servers, watchdog, and cleanup — available;
- simultaneous bidirectional TCP mode — available in `network-v2`;
- adaptive UDP rate sweep, loss, jitter, and reorder — available in `network-v2`;
- idle ICMP RTT and loaded TCP_INFO RTT comparison — available and explicitly unscored;
- topology-aware practical ceiling and bufferbloat classification — planned;
- retransmissions, congestion control, MTU, route, and NIC-offload evidence;
- sender/receiver CPU to detect generator bottlenecks;
- separate same-zone, cross-zone, and cross-region labels;
- short-burst and sustained runs;
- mTLS enrollment and policy-configurable rate limits — planned.

There is no cloud-to-controller profile. Public-Internet results must not be
combined with private or VPC network results.

## M3 — Compute, memory, and GPU (partial in 0.4.0; remote dispatch in 0.5.0)

- CPU single/multi-thread integer scaling through sysbench — available;
- sustained CPU runs with event-rate stability and steal-time telemetry — available;
- native userspace memory read/write/copy/triad bandwidth — available;
- floating-point, compression, crypto, and compilation workloads — planned;
- memory latency, NUMA topology, and remote-node penalties — planned;
- repeated-run and same-SKU variance analysis — planned;
- authenticated remote dispatch, progress, cancellation, and result attribution for single-system suites — available;
- GPU inventory, H2D/D2H bandwidth, compute, VRAM, and thermal/power stability;
- real media encode/decode through FFmpeg;
- CUDA, ROCm, and oneAPI framework probes when available;
- variance across repeated runs and same-SKU instances.

## M4 — Database and web/application

Client/server workloads use at least two agents to avoid competing with their
own generators for CPU and network resources.

- PostgreSQL pgbench read-only, read/write, connection scaling, and checkpoints;
- MySQL/MariaDB OLTP read/write and fsync-sensitive profiles;
- Redis GET/SET, pipelines, persistence, and tail latency;
- static web, JSON API, TLS, keep-alive, and concurrency ramps;
- reverse proxy, compression, and HTTP/2 or HTTP/3 when supported;
- soak testing, error rate, P95/P99, and saturation point;
- DDoS-style testing only as an **authorized resilience test** on operator-owned
  systems with rate and duration limits, never against third parties.

## M5 — Containers, Kubernetes, HA, and operations

- container cold start, image pull/unpack, and overlay filesystem;
- Kubernetes scheduling, pod density, service latency, and autoscaling response;
- load-balancer health and failover;
- database replication lag and controlled failover;
- snapshot/restore, node replacement, and recovery drills;
- DNS, IPv6, firewall/security-group, and private-connectivity evidence;
- monitoring/logging coverage and clock synchronization;
- provider API create/delete/resize/snapshot latency through least-privilege adapters.

HA and failover tests require at least three nodes to separate the target, load
generator, and replica or witness. Nested VMs on one physical system validate
functionality only; they cannot prove real provider-fabric availability.

## M6 — Suitability and provider scoring

Each use case defines:

1. hard gates — missing evidence makes the system ineligible;
2. weighted metrics — performance and tail latency;
3. stability — variance across runs, instances, and time windows;
4. evidence confidence — topology, sample count, and tool health;
5. operational evidence — snapshot, failover, API, and security;
6. cost input — stored separately with timestamp, currency, and source.

Recommendations are `Excellent`, `Suitable`, `Conditional`, `Not recommended`,
or `Insufficient evidence`. Every label includes reason codes; CloudMark never
shows an unexplained single aggregate score.

Provider scoring aggregates multiple systems, time windows, and zones. Reports
show median, P10/P90, worst observed, sample count, and profile version. SLA,
durability, and compliance are scored only when supported by documents or
matching control-plane drills.

## Minimum system matrix

| Group | Minimum | Recommended |
|---|---:|---:|
| Inventory, CPU, RAM, local/block storage, GPU | 1 | 2–3 same-SKU instances |
| Network, web, client/server database | 2 agents + Controller | 3 for a dedicated generator |
| Replication, failover, load balancer | 3 agents + Controller | 4 to isolate the generator |
| Cross-zone/cross-region and DR | 2–3 agents in different locations | repeat across time windows |

## Recommended implementation order

1. Complete M1 and the time-series schema because storage is the most mature executor.
2. Complete M2 with mTLS, route/MTU capture, repeated windows, and generator-saturation guards.
3. Complete M3 beyond the available integer CPU and memory-bandwidth subsets.
4. Build M4 on the stable runner.
5. Add provider adapters and M5 drills.
6. Lock suitability/provider thresholds in M6 only after collecting real data
   across regional clouds, global clouds, and self-operated bare-metal systems.
