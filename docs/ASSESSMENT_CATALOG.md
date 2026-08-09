# CloudMark comprehensive assessment catalog

This document defines the technical scope of CloudMark for cloud instances,
VPS systems, bare-metal servers, and self-hosted public-cloud infrastructure.
Catalog coverage does not imply that every executor is already available. The
dashboard and API always publish one of three states:

- `Available`: evidence collection or an executor is usable in this release;
- `Partial`: some evidence is available, but not enough to assess the complete domain;
- `Roadmap`: the domain is in product scope but is not used for scoring yet.

A workload label is produced only when its hard gates have the required
metrics, valid topology, healthy tools, and minimum sample count. Missing
evidence returns `Insufficient evidence`, never an artificial zero.

## Technical domain matrix

| # | Domain | Target evidence and measurements | Minimum topology | Current state |
|---:|---|---|---|---|
| 1 | System & Hardware Inventory | OS/kernel, firmware, CPU topology, RAM, NUMA, disks, filesystems, NICs, clock, runtime and tool capabilities | 1 system | Available |
| 2 | Provider & Instance Identity | Trusted metadata, declared manifests, provider/region/zone/SKU, evidence source and confidence | 1 system | Available |
| 3 | Virtualization & Topology | Hypervisor/container evidence, vCPU placement, NUMA exposure, nested virtualization, and overcommit indicators | 1 system; 2–3 instances for comparison | Partial |
| 4 | CPU & Compute | Single/multi-thread, integer, floating point, compression, crypto, compilation, sustained throughput, steal/throttle, and performance per watt when available | 1 system; 2–3 instances for variance | Partial |
| 5 | Memory & NUMA | Read/write/copy bandwidth, latency, remote NUMA penalty, page size, swap pressure, and sustained stability | 1 system | Partial |
| 6 | Storage, Filesystem & Object | Sequential/random/mixed I/O, queue-depth sweep, sync/fsync, P50–P99.9, burst/throttle, metadata, integrity, object PUT/GET/list, and snapshot/restore | 1 system for block; 2–3 for object/backup | Available |
| 7 | Network & Connectivity | TCP/UDP, 1–16 streams, idle/loaded RTT, jitter/loss/reorder, retransmits, MTU, DNS, IPv4/IPv6, private/public, and cross-zone/region | 2 agents + Controller | Partial |
| 8 | GPU & Accelerators | Model/driver, VRAM, H2D/D2H, compute, tensor/floating-point profiles, framework probes, thermal/power stability, and media encode/decode | 1 GPU system; 2 for serving | Roadmap |
| 9 | Web, API & TLS | Static/JSON, TLS handshake, keep-alive, HTTP/2/3, concurrency ramp, P50–P99, error rate, saturation, soak, and reverse proxy | target + generator + Controller | Roadmap |
| 10 | Database & Cache | PostgreSQL/MySQL OLTP, read-only/read-write, connection scaling, checkpoint/fsync, Redis GET/SET/pipeline/persistence, and replication lag | server + client; 3+ for replication | Roadmap |
| 11 | Containers & Kubernetes | Runtime discovery, pull/unpack, cold start, overlay I/O, pod density, service latency, CNI, scheduling, and autoscaling response | 1 for containers; 2–3+ for Kubernetes | Partial |
| 12 | Security & Isolation | Port/exposure inventory, firewall/security-group evidence, TLS posture, IAM/RBAC, hardening, tenant-isolation signals, and auditability | 1–2 systems; control-plane adapter when required | Roadmap |
| 13 | Reliability, HA & DR | Replication, controlled failover, load-balancer health, node replacement, snapshot/restore, backup integrity, and RPO/RTO drills | 3 agents + Controller; 4 recommended | Roadmap |
| 14 | Observability & Operations | Metrics/logs/traces, clock sync, alert path, agent overhead, log-delivery loss, retention, and export evidence | 1 system; 2+ for the delivery path | Roadmap |
| 15 | Provisioning & Control Plane | Create/delete/resize, attach/detach, snapshot, API latency/errors/rate limits, quotas, and idempotency | Controller + least-privilege adapter | Roadmap |
| 16 | Cost & Efficiency | Timestamped pricing with currency and source, egress/storage cost, price/performance, utilization, right-sizing, and license context | benchmark data + pricing source | Roadmap |
| 17 | Consistency & Noisy Neighbor | Variance across instances and time windows, P10/P50/P90, worst observed, burst credits, steal time, throttling, and recovery | 2–3 same-SKU instances across time windows | Roadmap |

`Available` at domain level means that at least one valid measurement path
exists; it does not mean that every measurement in the row is complete. Raw
results must record profile, tool, methodology, timestamp, and topology versions
so actual coverage remains auditable.

Version `0.4.0` implements the integer single/all-core and sustained subset of
domain 4, plus the native read/write/copy/triad bandwidth subset of domain 5.
Floating-point, crypto, compilation, true memory latency, and NUMA penalty
measurements remain missing; both domains therefore remain `Partial` and cannot
independently unlock a suitability label.

Version `0.5.0` can execute those single-system subsets and filesystem-safe
storage profiles on an explicitly selected authenticated Agent. Remote execution
improves topology accuracy and attribution; it does not change incomplete domain
coverage into a complete suitability score.

## Mapping evidence to intended use

Each workload combines multiple domains rather than relying on one benchmark:

| Use case | Representative hard gates | Additional evidence |
|---|---|---|
| Storage & Backup | storage integrity, throughput, restore path | network, cost, reliability |
| Web & App Hosting | CPU, memory, network, web/API tail latency | TLS, autoscaling, observability, cost |
| Dev & Test | compute, memory, storage, provisioning | containers, snapshots, cost |
| Database Management | fsync/tail latency, CPU, RAM, database workload | replication, backup/restore, HA, security |
| Networking & Connectivity | RTT/loss/jitter/throughput, DNS | IPv6, firewall, cross-zone, cost |
| Big Data & Analytics | sustained compute, memory, storage throughput | network scaling, object storage, cost |
| AI & Machine Learning | GPU/accelerator, VRAM, CPU/RAM, storage | network, framework, cost, consistency |
| Container & Kubernetes | containers/Kubernetes, CPU/RAM, network | storage, autoscaling, observability, security |
| Disaster Recovery | backup integrity, replication, failover, RPO/RTO | cross-region network, control plane, operations |
| Virtual Desktop | GPU/media, interactive latency, CPU/RAM | security, connectivity, consistency |
| Media Processing | codec throughput, CPU/GPU, storage throughput | object storage, network/CDN, cost |
| Enterprise Applications | reliability, security, database, operations | IAM/RBAC, control plane, consistency, cost |

## Topology rules

- A single-system test concludes only about the measured system or instance.
- Remote single-system results must identify the Agent, target inventory,
  provider evidence, profile, methodology, protocol, and tool versions.
- Client/server tests separate the target and generator to prevent resource self-contention.
- The Controller coordinates tests but never joins the provider benchmark data path.
- HA/DR tests require independent nodes. Nested VMs on one physical host prove
  functionality, not provider-fabric availability.
- Provider assessment requires multiple same-SKU instances, zones, and time
  windows. Reports must expose sample count, median, P10/P90, and worst observed.

## Safety rules

- Never send load to third-party systems. Web resilience tests run only against
  resources owned by or explicitly authorized for the operator.
- Storage tests use filesystem temporary files by default, preserve a free-space
  reserve, never format or target raw devices, and always clean up.
- CPU and memory saturation tests require confirmation, fixed profile limits,
  local mutual exclusion, cancellation, and a memory-reserve preflight.
- Load-generating tests require rate and duration limits, a watchdog, health
  checks, and an emergency stop path.
- Provider API adapters use least privilege, and every infrastructure mutation
  must produce an audit record.
