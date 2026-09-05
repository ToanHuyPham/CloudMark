# CloudMark current state

Last updated: 2026-09-05

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
- bounded ephemeral Agent-task secret delivery: plaintext fields exist only in
  Controller memory, are attached only to the authenticated assigned-Agent
  claim response, never enter SQLite/read models/progress/runtime snapshots, and
  are erased on task completion, failure, cancellation, or abort. Controller
  restart intentionally loses them so interrupted work cannot silently reuse a
  persisted service password; this milestone passes 122 Python tests, 3
  rendered-dashboard tests, dashboard lint, and the production build;
- simulation-verified `database-redis-v1` Quick/Standard profiles with
  memory-only per-Run authentication, fixed GET/SET value-size/concurrency/
  pipeline shapes, AOF `appendfsync everysec`, CSV P50/P95/P99 latency,
  Generator CPU validity, watchdog cleanup, and no plaintext credential in
  evidence. Dedicated service-configuration and end-to-end orchestration tests
  verify one shared memory-only password, exact bind/AOF policy, Generator
  evidence, cleanup, and absence of the password from durable task records. The
  development head passes 127 Python tests;
- simulation-verified `database-mysql-v1` Quick/Standard profiles for MySQL and
  MariaDB with isolated non-root Target data directories, initialization before
  network exposure, exact-address TCP 57306 binding, an exact-Generator account
  and memory-only per-Run password, fixed Sysbench OLTP table/workload shapes,
  one-second progress, direct P99 latency, InnoDB flush-at-commit/doublewrite
  evidence, Generator CPU validity, table cleanup, watchdog service cleanup,
  and durable-record credential redaction. Success and client-failure
  orchestration paths are covered without running a real load; the complete
  development head passes 134 Python tests, 3 rendered-dashboard tests,
  dashboard lint, and the production build;
- guarded, bidirectional TCP measurements between paired Agents;
- simulation-verified `network-v6` standard orchestration for allow-listed
  pre/post route-derived interface byte/packet/error/drop deltas,
  route/interface/MTU evidence, bounded numeric path traces with explicit
  endpoint/hop address classes and no public-transit inference, pre/post route
  stability, read-only NIC driver/offload and TCP
  congestion-control capture, bounded idle latency, loaded TCP RTT, adaptive
  UDP loss/jitter sweeps, simultaneous bidirectional TCP, and Generator CPU/
  scaling headroom validity; provider-pair validation is intentionally deferred
  until the complete project is ready for operator testing; the milestone
  passes 90 Python tests, 3 rendered-dashboard tests, dashboard lint, and the
  production build without running a real load;
- `network-campaign-v1` durable fixed-pair acquisition contracts with immutable
  Agent/topology/profile/methodology identity, 3-30 distinct UTC-day targets,
  explicit per-window confirmation, retryable failed attempts, and strict
  comparison-eligibility counting; campaign creation is side-effect free and a
  completed campaign remains one-pair temporal evidence rather than a provider
  rating; that campaign milestone passed 93 Python tests, 3 rendered-
  dashboard tests, dashboard lint, and the production build without starting
  provider load;
- simulation-verified `network-v7` standard orchestration with bounded,
  read-only `ethtool -S` snapshots on the route-derived interface, common
  driver per-queue counter normalization, pre/post queue deltas, active RX/TX
  queue distribution, busiest-queue share, and explicit vendor-counter
  limitations; queue evidence is observational rather than a comparison gate,
  while unfinished campaigns locked to an older standard profile are preserved
  as `superseded`;
- simulation-verified `network-v8` standard orchestration with a bounded
  pre-load Linux system-resolver diagnostic on both Agents: at most 64 KiB of
  resolver configuration, redacted search-domain names, and—when `dig` is
  present—one fixed A and AAAA query for `example.com.` with strict retries and
  deadlines. Query answers are reduced to count and address class; cache state
  and upstream/provider attribution remain explicitly unknown. Resolver
  evidence is observational and does not alter comparison validity. The
  complete development head passes 98 Python tests, 3 rendered-dashboard
  tests, dashboard lint, and the production build without starting provider
  load;
- simulation-verified `network-v9` standard orchestration with bounded,
  read-only guest-visible queue-placement evidence on the route-derived Linux
  interface: at most 4,096 RSS indirection entries across queue indexes 0-127,
  RPS/XPS CPU masks for at most 128 RX/TX queues, and affinity for at most 256
  interface-exposed MSI IRQs. RSS hash keys are not persisted, every control
  file read is capped at 4,096 bytes, Agent evidence is independently bounded
  and normalized by the Controller, and no NIC/kernel setting is changed.
  Steering/affinity evidence is observational and does not claim physical-host
  configuration or alter comparison validity. The complete development head
  passes 102 Python tests, 3 rendered-dashboard tests, dashboard lint, and the
  production build without starting provider load;
- simulation-verified `database-postgresql-v1` paired executor with isolated
  Target clusters, Generator-side built-in pgbench workloads, durable settings,
  progress/control heartbeat, fixed safety limits, and verified cleanup; the
  milestone passes 53 Python tests, 3 rendered-dashboard tests, dashboard lint,
  and the production dashboard build without running a real load;
- simulation-verified `database-postgresql-v2` Standard orchestration with the
  durable v1 throughput/concurrency/connection-churn jobs plus one exact
  four-client, 1,000-transactions-per-client TPC-B-like tail job. CloudMark
  parses every bounded transaction log row into nearest-rank
  P50/P95/P99/P99.9/maximum, requires an exact 4,000-row contract, caps input at
  8 MiB and 20,000 rows, and verifies Generator log cleanup. Timed jobs retain
  one-second Linux host/steal and pgbench process CPU summaries; missing CPU or
  a 90%-of-one-core peak makes the Run comparison-ineligible. Quick remains
  readable as `database-postgresql-v1`. The complete development head passes
  118 Python tests, 3 rendered-dashboard tests, dashboard lint, and the
  production build without starting provider load;
- simulation-verified `database-postgresql-recovery-v1` as a separate
  same-Target logical backup/restore profile. After a fixed durable workload and
  after Generator load ends, the Target records four pgbench table counts,
  creates an uncompressed custom-format pg_dump archive, restores it into the
  fixed `cloudmark_restore` database, verifies source/restored row-count
  equality and scale shape, then removes the restored database and archive.
  Free-space reserve, archive-size bounds, fixed loopback commands, recovery
  cleanup, and final cluster cleanup are enforced. The evidence does not claim
  snapshots, PITR, cross-zone DR, RPO, or RTO. The complete development head
  passes 121 Python tests, 3 rendered-dashboard tests, dashboard lint, and the
  production build without starting provider load;
- simulation-verified `web-http-v1` paired executor with an isolated Nginx
  Target, fixed HTTP/HTTPS endpoints, Generator-side ApacheBench workloads,
  exact address allow-listing, TLS 1.2 evidence, progress/control heartbeat,
  fixed safety limits, and verified cleanup; the complete milestone passes 64
  Python tests, 3 rendered-dashboard tests, dashboard lint, and the production
  build without starting provider load;
- simulation-verified `web-http-v2` Standard orchestration with a packaged
  deterministic 1 KiB Python application on Target loopback port 58081 behind
  the exact Nginx listener, three dynamic HTTP/1.1 concurrency workloads,
  bounded one-second Linux Generator host/steal and ApacheBench process CPU
  summaries, a 90%-of-one-core Generator rejection gate, and one fixed HTTPS
  curl observation that must actually negotiate HTTP/2. The HTTP/2 observation
  is explicitly not a throughput claim. Target Nginx and Generator curl HTTP/2
  capabilities, dynamic reverse-proxy evidence, Generator headroom, and cleanup are required
  for comparison eligibility. Quick remains readable as `web-http-v1`. The
  complete development head passes 111 Python tests, 3 rendered-dashboard
  tests, dashboard lint, and the production build without starting provider
  load;
- responsive dashboard navigation and execution-target selection; the mobile
  navigation uses stable 12 px labels in a contained horizontal scroller and
  was browser-verified at 390 px and 1,280 px without page-level horizontal
  overflow;
- production dashboard visual system updated to a black/dark-navy/white palette
  with electric-blue status accents, higher-contrast panels, consistent rounded
  geometry, improved focus states, 15 px body copy, 13 px mobile navigation,
  and a matching favicon/social-preview asset. Browser verification at 1,280 px
  and 390 px confirmed full-width cards, contained horizontal navigation, no
  page-level horizontal overflow, and no console errors;
- `suitability-v1` target-scoped Essential, Standard, and Demanding workload
  gates for all 12 use cases, with per-check Run ID/profile/methodology/time
  provenance, 30-day freshness, cleanup/methodology validity gates, explicit
  blockers, and separate provider-readiness criteria; missing evidence is never
  converted to zero and provider status remains `not-rated`; the milestone
  passes 70 Python tests, 3 rendered-dashboard tests, dashboard lint, and the
  production build without starting provider load;
- `provider-observations-v4` exact provider/SKU/region/OS/topology/evidence-class
  cohorts with strict profile/methodology/topology compatibility, UTC-day
  windows, network Run de-duplication, and database/cache engine implementation
  plus exact server-version isolation. PostgreSQL, Redis GET/SET, and
  MySQL/MariaDB read/write metrics now enter descriptive cohorts without being
  converted into a score. Median/P10/P90/best/worst/spread statistics retain a
  guarded nine-sample/three-target/three-window comparable state. MySQL and
  MariaDB or different server versions cannot be silently merged. The complete
  development head passes 135 Python tests, 3 rendered-dashboard tests,
  dashboard lint, and the production build without starting provider load;
- repository-level Codex guidance, durable handoff documentation, consistent
  SQLite runtime snapshots, guarded secret backup, recoverable restore, and
  safe Windows local-process launch/stop scripts;
- terminal Run states are published only after durable task cleanup, preventing
  callers from observing completion while the worker still holds SQLite state.
- dashboard polling uses non-mutating Run summaries, compact JSON, no raw tool
  output, and a 90-point presentation timeline while `/runs/{id}` and SQLite
  preserve complete evidence; expected client disconnects no longer produce
  misleading server tracebacks; the complete development head passes 95 Python
  tests, 3 rendered-dashboard tests, dashboard lint, and the production build.

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

- network coverage is Partial: `network-v9` implements Linux pre/post
  route-derived interface counters, egress-interface, interface-MTU and
  path-MTU evidence, bounded destination-reaching numeric traces, endpoint/hop
  address classification, route-stability evidence, read-only NIC driver and selected offload state,
  active TCP congestion control, idle latency, directional TCP scaling,
  adaptive UDP jitter/loss sweeps, loaded TCP RTT, simultaneous bidirectional
  throughput, and Generator headroom rejection;
  topology claims are now independently checked when trusted provider metadata
  permits it, while address class and observed hops do not prove administrative
  ownership or a public path, and same-host placement and the physical provider
  fabric cannot yet be proven; bounded common driver per-queue counters are
  observational but are not normalized across every NIC family; guest-visible
  RSS/RPS/XPS and MSI IRQ affinity is now bounded and observational but does not
  verify physical-host NIC/interrupt placement; fixed system-resolver configuration
  and A/AAAA diagnostics are observational, with no controlled authoritative
  server, cache-cold repetition, DNSSEC, TCP fallback, or Windows parity;
  manual fixed-pair repeated UTC-day campaigns are implemented, while
  unattended scheduling, cross-pair
  orchestration, Windows route parity, and mTLS remain unimplemented;
- PostgreSQL database coverage is Partial: read-only, durable read/write,
  concurrency, connection churn, fixed-count transaction tail percentiles, and
  Generator CPU validity are implemented; checkpoint isolation, replication,
  same-Target logical backup/restore, and artifact cleanup are implemented;
  MySQL/MariaDB now adds isolated InnoDB point-select, read-only, write-only,
  and read/write Sysbench profiles with P99 and Generator validity; physical/
  PITR backup, cross-zone recovery, replication, database checkpoint isolation,
  binary-log overhead, and managed-service behavior remain unavailable;
- an abrupt Agent or host termination can leave an isolated PostgreSQL task
  directory for manual operator review; the Agent refuses to overwrite or
  automatically delete unknown residual state; the same review requirement
  applies to an isolated Web service directory;
- Web/API/TLS coverage is Partial: fixed static/JSON endpoints, a packaged
  dynamic application behind Nginx, HTTP/HTTPS concurrency, Generator CPU
  validity, connection churn, transfer rate, tail latency, and HTTP/2
  negotiation are implemented; database-backed applications, HTTP/2 load,
  HTTP/3, CDN, WAF, autoscaling, and DDoS resilience remain unavailable;
- GPU evidence and GPU benchmarks are not complete;
- scheduled sampling campaigns, cross-pair orchestration, cross-zone analysis,
  timestamped cost,
  operational domains, and final provider ratings remain Roadmap; suitability
  evaluates individual targets while provider observations remain descriptive;
- Windows is suitable for the Controller and inventory, but benchmark executor
  parity with Linux is incomplete;
- one VM and one time window cannot establish provider-wide quality.

## Next priorities

1. Add physical-host/fabric and administrative-path verification, wider vendor
   NIC queue-counter normalization, controlled
   authoritative/cache-cold/DNSSEC resolver coverage, unattended campaign
   scheduling, and Windows route parity
   before promoting the network domain from Partial.
2. Add database-backed Web applications, HTTP/2 load, HTTP/3, reverse-proxy
   variants, compression, CDN, WAF, and autoscaling evidence.
3. Extend database coverage with checkpoint isolation, physical/PITR backup,
   replication, cross-zone recovery, binary-log/replication overhead, and
   RPO/RTO evidence.
4. Complete remaining compute, memory/NUMA, GPU, security, reliability,
   observability, container, and control-plane executors.
5. Extend campaigns across independent targets, then add timestamped price
   inputs and cohort export before any final provider-rating methodology.
6. Calibrate and version requirement thresholds across regional clouds, global
   clouds, and self-operated bare metal before treating them as stable policy.
7. Run provider-machine validation only after the development milestones are
   complete and the operator explicitly starts acceptance testing.

## Operational reminder

The local dashboard, Controller process, reverse tunnel, and remote Agent are
ephemeral processes. Source control does not preserve their live state. Follow
`docs/OPERATIONS_RUNBOOK.md` and restore `.cloudmark` from a protected snapshot
when moving to another machine.
