# CloudMark current state

Last updated: 2026-08-11

## Repository baseline

- Public repository: `https://github.com/ToanHuyPham/CloudMark`
- Primary branch: `main`
- Product version: `0.5.0`
- Handoff baseline before the Recovery Kit: `75a1959`

Always verify the current commit and working tree instead of assuming this
baseline is still the repository head.

## Implemented and verified

- detailed local and Agent inventory;
- AWS, Azure, and Google Cloud metadata detection with evidence provenance;
- bootstrap planning for supported Linux package managers;
- versioned compute quick and standard profiles using sysbench;
- versioned native memory-bandwidth quick and standard profiles;
- filesystem-safe fio quick, standard, database, throughput, and sustained
  profiles;
- progress, heartbeat, timeout, cancellation, cleanup, and partial-result
  preservation;
- local Controller API, authenticated mutations, SQLite history, and dashboard;
- persistent authenticated Agents and explicit remote CPU/memory/storage
  dispatch;
- guarded, bidirectional TCP measurements between paired Agents;
- simulation-verified `network-v2` standard orchestration for bounded idle
  latency, loaded TCP RTT, adaptive UDP loss/jitter sweeps, and simultaneous
  bidirectional TCP; provider-pair validation is intentionally deferred until
  the complete project is ready for operator testing;
- simulation-verified `database-postgresql-v1` paired executor with isolated
  Target clusters, Generator-side built-in pgbench workloads, durable settings,
  progress/control heartbeat, fixed safety limits, and verified cleanup; the
  milestone passes 53 Python tests, 3 rendered-dashboard tests, dashboard lint,
  and the production dashboard build without running a real load;
- simulation-verified `web-http-v1` paired executor with an isolated Nginx
  Target, fixed HTTP/HTTPS endpoints, Generator-side ApacheBench workloads,
  exact address allow-listing, TLS 1.2 evidence, progress/control heartbeat,
  fixed safety limits, and verified cleanup; the complete milestone passes 64
  Python tests, 3 rendered-dashboard tests, dashboard lint, and the production
  build without starting provider load;
- responsive dashboard navigation and execution-target selection;
- `suitability-v1` target-scoped Essential, Standard, and Demanding workload
  gates for all 12 use cases, with per-check Run ID/profile/methodology/time
  provenance, 30-day freshness, cleanup/methodology validity gates, explicit
  blockers, and separate provider-readiness criteria; missing evidence is never
  converted to zero and provider status remains `not-rated`; the milestone
  passes 70 Python tests, 3 rendered-dashboard tests, dashboard lint, and the
  production build without starting provider load;
- `provider-observations-v1` exact provider/SKU/region/OS cohorts with strict
  profile/methodology compatibility, UTC-day windows, network Run
  de-duplication, median/P10/P90/best/worst/spread statistics, and a guarded
  nine-sample/three-target/three-window comparable state; the milestone passes
  72 Python tests, 3 rendered-dashboard tests, dashboard lint, and the
  production build without starting provider load;
- repository-level Codex guidance, durable handoff documentation, consistent
  SQLite runtime snapshots, guarded secret backup, recoverable restore, and
  safe Windows local-process launch/stop scripts.

## Last verified provider target

The last official baseline used a Google Cloud `e2-standard-4` VM in
`asia-east1-c` with Ubuntu 22.04, four vCPUs, 16 GiB memory, a 120 GB balanced
persistent disk, and no GPU. Network addresses, session tokens, Agent tokens,
and SSH credentials are intentionally excluded from tracked documentation.

The environment was verified on kernel `6.8.0-1065-gcp`. Do not assume that the
VM, firewall, reverse tunnel, or Agent remains online after reopening the
project.

## Last verified benchmark evidence

These values describe one VM and must not be generalized to all Google Cloud
instances or to the provider as a whole.

### Compute Standard

- single-thread integer rate: 1,425.18 events/s;
- all-core integer rate: 3,155.80 events/s;
- sustained all-core rate: 3,148.21 events/s;
- reported scaling efficiency: 55.36%;
- observed steal time remained below 0.04% in the recorded phases.

Controller run: `run_01858b80be9f4e51`.

### Memory Standard

- single-thread read: 7.70 GiB/s;
- all-core read: 28.91 GiB/s;
- all-core copy: 39.42 GiB/s;
- all-core triad: 40.93 GiB/s.

Controller run: `run_974b2c11529d431e`.

### Disk Standard

- sequential read/write: approximately 173.6 MiB/s;
- random read QD1: 2,199.86 IOPS;
- random read QD32: 3,705.56 IOPS;
- random write QD1: 2,416.69 IOPS;
- mixed 70/30: 2,601.85 read IOPS and 1,112.68 write IOPS;
- synchronous database-style write: 738.17 IOPS.

Controller run: `run_d735cd8a36134c7a`.

### Disk Database

- random 8 KiB read QD1: 2,088.70 IOPS;
- random 8 KiB write QD1: 2,189.88 IOPS;
- random 8 KiB read QD16: 3,713.65 IOPS;
- mixed 70/30: 2,599.72 read IOPS and 1,113.74 write IOPS;
- synchronous write: 667.60 IOPS;
- temporary test file removal was verified;
- no fio process or non-tool workspace file remained after completion.

Controller run: `run_1c572100e8704843`.

## Known limitations

- network coverage is Partial: `network-v2` now implements idle latency,
  directional TCP scaling, adaptive UDP jitter/loss sweeps, loaded TCP RTT, and
  simultaneous bidirectional throughput; route/MTU evidence,
  generator-saturation rejection, repeated topology-aware windows, and mTLS
  remain unimplemented;
- PostgreSQL database coverage is Partial: read-only, durable read/write,
  concurrency, and connection churn are implemented; transaction tail
  percentiles, replication, recovery, MySQL/MariaDB, Redis, and managed-service
  behavior remain unavailable;
- an abrupt Agent or host termination can leave an isolated PostgreSQL task
  directory for manual operator review; the Agent refuses to overwrite or
  automatically delete unknown residual state; the same review requirement
  applies to an isolated Web service directory;
- Web/API/TLS coverage is Partial: fixed static/JSON endpoints, HTTP/HTTPS
  concurrency, connection churn, transfer rate, and tail latency are
  implemented; generator-saturation validation, dynamic applications,
  HTTP/2/3, CDN, WAF, autoscaling, and DDoS resilience remain unavailable;
- GPU evidence and GPU benchmarks are not complete;
- scheduled sampling campaigns, cross-zone analysis, timestamped cost,
  operational domains, and final provider ratings remain Roadmap; suitability
  evaluates individual targets while provider observations remain descriptive;
- Windows is suitable for the Controller and inventory, but benchmark executor
  parity with Linux is incomplete;
- one VM and one time window cannot establish provider-wide quality.

## Next priorities

1. Add route/MTU evidence, generator-saturation checks, and repeated network
   windows before promoting the network domain from Partial.
2. Add Web generator-saturation validation and dynamic application,
   HTTP/2/3, reverse-proxy, CDN, WAF, and autoscaling evidence.
3. Extend database coverage with transaction tail latency, MySQL/MariaDB,
   Redis, replication, backup/restore, and recovery evidence.
4. Complete remaining compute, memory/NUMA, GPU, security, reliability,
   observability, container, and control-plane executors.
5. Add explicit sampling campaigns, timestamped price inputs, and cohort
   export before any final provider-rating methodology.
6. Calibrate and version requirement thresholds across regional clouds, global
   clouds, and self-operated bare metal before treating them as stable policy.
7. Run provider-machine validation only after the development milestones are
   complete and the operator explicitly starts acceptance testing.

## Operational reminder

The local dashboard, Controller process, reverse tunnel, and remote Agent are
ephemeral processes. Source control does not preserve their live state. Follow
`docs/OPERATIONS_RUNBOOK.md` and restore `.cloudmark` from a protected snapshot
when moving to another machine.
