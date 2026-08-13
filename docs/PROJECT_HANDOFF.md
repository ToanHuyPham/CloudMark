# CloudMark project handoff

## Mission

CloudMark evaluates whether a cloud instance, VPS, bare-metal server, or
self-hosted public-cloud node is suitable for real workloads. It combines
machine inventory, provider evidence, versioned benchmarks, execution metadata,
raw results, repeatability indicators, and workload-specific interpretation.

The long-term product must support both major global providers and providers
that expose no metadata service or public API. Vietnamese infrastructure
providers are a primary use case.

## Product principles

1. **Evidence before scoring.** Unknown or unavailable measurements remain
   unavailable; they do not become zero.
2. **Workload-shaped tests.** Storage, compute, memory, and network behavior is
   measured using multiple profiles rather than a single headline number.
3. **Repeatability.** Profile, methodology, tool, target, Agent, and timing
   information travel with every result.
4. **Safe by default.** Storage tests use temporary filesystem files and retain
   a free-space reserve. Network tests use allow-listed peer tasks.
5. **Provider evaluation requires multiple systems.** A Controller coordinates;
   provider Agents generate and receive provider-side traffic.
6. **Claims remain bounded.** The dashboard labels capabilities as Available,
   Partial, or Roadmap and must not visually imply unsupported coverage.

## Intended assessment domains

- provider and machine identity;
- CPU performance, scaling, stability, and steal time;
- memory bandwidth and scaling;
- storage throughput, IOPS, latency percentiles, synchronous behavior, mixed
  workload behavior, sustained behavior, and cleanup evidence;
- direct provider-internal network throughput, directionality, concurrency,
  latency, jitter, loss, and loaded behavior;
- database client/server behavior;
- web/application hosting behavior;
- GPU and accelerator evidence;
- virtualization, container, Kubernetes, security, availability, backup,
  disaster recovery, observability, and cost evidence;
- workload suitability for storage and backup, web hosting, development and
  test, databases, networking, analytics, AI/ML, containers, disaster recovery,
  virtual desktops, media delivery, and enterprise applications.

Not every domain has an executor yet. See `docs/CURRENT_STATE.md` and
`docs/ASSESSMENT_CATALOG.md` for the implemented boundary.

## Current architecture

```text
Operator system
├── CloudMark Controller API (127.0.0.1:8787)
├── SQLite evidence store (.cloudmark/cloudmark.sqlite3)
└── Local dashboard (localhost:3000-3010)

Provider environment
├── Agent A: target under assessment
├── Agent B: peer/generator for provider-internal tests
└── Optional Agent C: replica, failover, or cross-zone role
```

The Controller may dispatch single-system CPU, memory, and storage suites to an
explicitly selected authenticated Agent. Provider network benchmark traffic
must not traverse the operator system. The standard `network-v5` profile adds
pre/post route and interface-counter snapshots, route/interface/MTU, read-only
NIC driver/offload, and TCP congestion-control evidence plus Generator-headroom
validity gates to bounded idle latency, directional TCP scaling, adaptive UDP
loss/jitter sweeps, and simultaneous bidirectional TCP between the two Agents.
The first paired service executor creates an isolated durable PostgreSQL cluster
on Agent A and runs allow-listed pgbench workloads from Agent B; cleanup is part
of the evidence contract. The second paired service executor creates an
isolated Nginx HTTP/TLS service on Agent A and runs fixed ApacheBench workloads
from Agent B. Both service lifecycles use Target-owned watchdogs and verified
ephemeral cleanup; the Controller never carries benchmark traffic.

The read-time `suitability-v1` engine partitions completed evidence by target,
validates methodology and cleanup, and evaluates all 12 workload categories at
Essential, Standard, and Demanding levels. Every check retains source Run
provenance. It produces target observations only; provider status stays
`not-rated` until multi-target, repeated-window, operational, and cost evidence
is implemented.

`provider-observations-v3` now produces descriptive exact-cohort distributions
for repeated evidence. It requires matching provider/SKU/region/OS and exact
profile/methodology/topology/evidence-class compatibility, exposes Run IDs and
sampling counts, and does not enable provider ratings. Pair declarations are
independently checked when trusted provider metadata can establish a placement
scope; contradictions remain observational. Globally routable addresses alone
never prove public-Internet traversal.

## Repository map

- `cloudmark/`: Python Controller, Agent, inventory, provider detection, and
  benchmark executors.
- `app/`: local dashboard.
- `openapi/`: public Controller API contract.
- `docs/`: architecture, methodology, safety, operations, and roadmap.
- `scripts/`: bootstrap, local operation, and runtime recovery scripts.
- `tests_python/` and `tests/`: non-destructive verification.

## Supported operating-system direction

The Controller supports Windows, Linux, and macOS where the Python runtime is
available. Provider targets prioritize Ubuntu, Debian, RHEL, CentOS-compatible
systems, SLES 12.5/15, and Windows. Executor availability is reported honestly;
Windows benchmark automation is not yet complete.

## Security and secret boundary

The public repository must never contain:

- Controller or Agent tokens;
- SSH private keys;
- active provider IP addresses or firewall secrets;
- `.cloudmark/` runtime state;
- cloud credentials or service-account material.

Examples and documentation use placeholders. Runtime backups containing secrets
must be stored outside the repository and encrypted at rest.

## Handoff procedure for a new Codex task

Open the cloned repository as the primary local-project folder and use this
initial request:

```text
Read AGENTS.md completely. Then read docs/PROJECT_HANDOFF.md,
docs/CURRENT_STATE.md, docs/OPERATIONS_RUNBOOK.md, and docs/DECISIONS.md.
Inspect the repository and Git status. Verify the local environment before
editing. Do not run a benchmark or contact a provider target without explicit
operator authorization. Continue from the documented next priority.
```

The transcript of an earlier task is optional historical evidence. This file,
`AGENTS.md`, the current-state record, Git history, and the runtime snapshot are
the durable handoff contract.
