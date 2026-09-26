# CloudMark current state

Last updated: 2026-09-26

## Repository baseline

- Public repository: `https://github.com/ToanHuyPham/CloudMark`
- Primary branch: `main`
- Product version: `0.5.0`
- Handoff baseline before the Recovery Kit: `75a1959`

Always verify the current commit and working tree instead of assuming this
baseline is still the repository head.

## Implemented and verified

- detailed local and Agent inventory;
- fail-closed AWS, Azure, and Google Cloud metadata detection with evidence
  provenance. Metadata access bypasses environment proxies, uses fixed endpoints
  and headers, caps responses at 64 KiB, and requires complete bounded identity
  fields before assigning 0.99 confidence. AWS tokens reject non-ASCII/control
  characters and oversized values; GCP requires the metadata-flavor response.
  Declared manifests remain visibly unverified, accept only bounded strings,
  and do not persist their local path. Six dedicated no-network tests cover the
  three providers, ordering/fallback, malformed/oversized responses, unsafe
  tokens, and manifests; `provider.py` branch coverage is 86.4%;
- bootstrap planning for apt, dnf/yum, and zypper with preview-by-default
  execution. Confirmed bootstrap now fails closed when detection produces no
  executable package-manager commands instead of reporting an empty success.
  Six dedicated no-install tests cover Windows and Linux manager selection,
  package/pack deduplication, exact argument-array commands, unknown packs,
  root enforcement, first-command failure, and successful result evidence;
  `bootstrap.py` branch coverage is 100.0%;
- versioned compute quick and standard profiles using sysbench. Compute and
  memory preflight cap `all`/`half` thread resolution by the current process CPU
  affinity when the OS exposes it, rather than blindly using host CPU count.
  Preflight retains host, affinity, and effective logical-core counts without
  persisting CPU IDs. Cgroup v2 `cpu.max` and v1 CFS quota are checked across
  the current group and up to 32 ancestors; the smallest capacity is retained
  as a float and its ceiling caps threads. Malformed quota fails closed. The
  dashboard presents the latest Run's thread/quota and memory-headroom boundary
  while explicitly separating fractional capacity from physical cores;
- versioned native memory-bandwidth quick and standard profiles;
- bounded read-only `memory-environment-v2` guest NUMA and paging evidence in inventory
  and every memory preflight/result. Linux sysfs collection retains at most 64
  online nodes, node CPU lists/counts, MemTotal/MemFree, relative distance
  matrices, and page size with a 4 KiB cap per source file and CPU indexes
  limited to 0-8191. Missing, malformed, oversized, non-Linux, and hidden
  topology remains partial or unavailable. Relative distance is explicitly not
  latency; physical-host placement and remote-node performance are never
  claimed. Version 2 adds a 64 KiB-capped `/proc/meminfo` snapshot for swap,
  anonymous huge pages, HugeTLB, and zswap usage plus 4 KiB-capped read-only
  THP enabled/defrag and zswap-enabled policy. Swap usage is explicitly a
  point-in-time snapshot with `pressure_measured=false`, not performance or
  pressure evidence. Memory preflight also resolves cgroup v2
  `memory.max`/`memory.current` or cgroup v1 limit/usage and applies the smaller
  verified headroom across the current cgroup and up to 32 ancestors versus
  host MemAvailable before preserving the 512 MiB reserve. A finite or malformed
  governing boundary with unreadable usage fails closed
  before compilation. Seven fixture-only tests cover sparse topology, bounds, malformed
  evidence, unsupported systems, inventory, and preflight attachment without
  starting memory load. The dashboard presents the selected target's current
  topology and limitations;
- filesystem-safe fio quick, standard, database, throughput, and sustained
  profiles;
- native `storage-filesystem-v1` small-file create/stat/read-and-SHA-256-
  verify/rename/delete and per-file fsync profile, with bounded deterministic
  payloads, user-space latency percentiles, explicit cache/runtime scope,
  cancellation, remote Agent execution, provider-observation metrics, and
  verified workspace cleanup. Eleven dedicated tests cover determinism, preflight,
  execution, cancellation, residual-state refusal, remote dispatch, evidence
  extraction, and dashboard retention. The complete development head passes
  166 Python tests, 3 rendered-dashboard tests, dashboard lint, and the
  production build. Verification used only an eight-file unit fixture and did
  not start a production storage benchmark;
- bounded read-only `storage-environment-v1` evidence attached to every storage
  Run: Linux workspace mount resolution, allow-listed mount semantics,
  guest-visible device identity, scheduler, I/O geometry, read-ahead, request
  depth, discard ceiling, write-cache/zoned state, and stacked-device names.
  Raw mount sources, serials, sysfs paths, and physical-device claims are not
  persisted; unsupported platforms and collection failures remain explicit
  unavailable evidence without blocking the benchmark. Provider observations
  advance to v5 and refuse to merge storage metrics across different or
  unverified filesystem/block/tool contracts. Seven dedicated tests cover
  mount parsing, sysfs normalization, source/serial redaction, unsupported and
  failed collection, exact contract construction, and cohort separation. The
  complete development head passes 173 Python tests, 3 rendered-dashboard
  tests, dashboard lint, and the production build without starting a storage
  benchmark;
- `storage-campaign-v1` immutable, baseline-anchored storage acquisition for
  3–30 distinct UTC completion days. Campaign creation is side-effect free and
  counts the complete Linux baseline as window one; every later dispatch
  requires separate write and campaign-window confirmation. Target/provider/
  SKU/region/OS, profile/methodology, run-time Target evidence, full fio or
  native-filesystem measurement payloads, cleanup, and exact filesystem/mount/
  block/tool contracts are enforced. A generic Run submission cannot inject
  campaign metadata, and both confirmations remain in the durable Run request.
  Timezone-naive, reversed, duplicate-day, failed, cancelled, cross-midnight,
  drifted, offline-target, and superseded attempts cannot advance the campaign.
  Completion remains temporal evidence for one target rather than a provider
  rating. Ten dedicated contract, projection, drift, refusal, lifecycle, native
  executor, and Controller-dispatch tests exercise the workflow without
  starting load. The complete development head passes 216 Python tests and 4
  Node tests, plus dashboard lint and the production build;
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
- simulation-verified `system-resolver-diagnostic-v3` evidence within Network
  v9 on both Agents: at most 64 KiB of Linux resolver configuration, redacted
  search-domain names, and—when `dig` is present—four fixed queries covering
  UDP/TCP × A/AAAA for `example.com.`. UDP uses `+notcp +ignore` so truncation
  is observed without a hidden TCP retry; TCP is queried separately. Results
  retain transport, TC state, bounded outcome/address classes, and an explicit
  recovery observation when a truncated UDP answer is followed by a valid TCP
  answer. The same four commands request DNSSEC records and AD reporting;
  CloudMark stores only whether the configured resolver asserted AD and always
  states that it did not independently validate signatures. Cache state and
  upstream/provider attribution remain unknown, and the diagnostic never gates
  network comparison validity. The dashboard shows
  every record type, transport, outcome, elapsed time, version, and bounded
  transport summary while explicitly refusing to infer automatic fallback.
  The complete
  development head passes 216 Python tests and 4 Node tests without starting
  provider load or making a real DNS query during verification;
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
- simulation-verified observational `queue-counters-v3` normalization within
  Network v9. The bounded parser covers common ENA/virtio/netvsc/mlx5
  direction/queue names, Azure MANA indexed names, Google gVNIC bracketed
  byte/drop names, VMware vmxnet3 sectioned packet/byte/error/drop fields,
  Intel i40e hyphen/dot packet and byte names, and Broadcom bnx2x bracketed
  queue names. Component counters are combined only for exact vmxnet3 or
  Broadcom unicast/multicast/broadcast names; conflicting direct/component
  fields become partial, while TSO/LRO/XDP/checksum/descriptor counters remain
  unclassified. Queue deltas retain the normalization version and expose byte
  distribution when a driver does not provide packet-per-queue counters. The
  evidence remains observational and does not alter Network v9 comparison
  validity. The complete development head passes 216 Python tests and 4 Node
  tests without starting provider load;
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
- simulation-verified `database-postgresql-checkpoint-v1` as a separately
  scheduled write/checkpoint profile. The Target forces a baseline checkpoint,
  the Generator runs one fixed durable 60-second TPC-B-like C4 workload with
  Linux CPU evidence, and the Target forces a post-load checkpoint. PostgreSQL
  9.x-16 `pg_stat_bgwriter` and 17+ `pg_stat_checkpointer` fields normalize to
  requested/timed, write/sync-time, and buffer deltas while retaining source
  view and server version. Counter resets, a missing requested increment,
  Generator saturation, or unverified cleanup fail comparison validity. The
  complete development head passes 140 Python tests, 3 rendered-dashboard
  tests, dashboard lint, and the production build without starting load;
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
- packaged Web v2 Python fixture contract tests exercise `/ready`, the exact
  `/api/v2/dynamic` route, deterministic valid 1 KiB JSON, content length/type,
  `no-store`, fixture identity, 404 query rejection, unsupported-method 501,
  fixed loopback bind/port refusal, daemon-thread configuration, interrupt
  shutdown, and server close entirely through fake sockets/server objects. No
  listener or HTTP load is created; `web_fixture.py` branch coverage is 94.0%;
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
- simulation-verified `web-http2-load-v1` as a separate HTTP/2 multiplexing
  profile against the packaged dynamic reverse-proxy path. Three fixed
  client/native-thread/max-stream/request-count shapes run through h2load on
  the Generator. CloudMark parses request/status/error/traffic summaries and a
  bounded all-request TSV log into nearest-rank P50/P95/P99/maximum latency,
  then verifies Generator log cleanup. CloudMark forces HTTP/2-only
  application-protocol negotiation using the installed h2load generation's
  supported option and verifies the observed protocol. Exact request summary
  counters, log rows, Generator CPU headroom, reverse-proxy evidence, and Target
  cleanup are comparison gates. The complete development head passes
  146 Python tests, 3 rendered-dashboard tests, dashboard lint, and the
  production build without starting load;
- `provider-observations-v6` exact provider/SKU/region/OS/topology/evidence-class
  cohorts with strict profile/methodology/topology compatibility, UTC-day
  windows, network Run de-duplication, database/cache engine implementation
  plus exact server-version isolation, and storage filesystem/mount/block/tool
  isolation. Compute/memory cohorts additionally require exact verified tool,
  host/affinity/effective-thread, and cgroup CPU quota boundaries; memory also
  locks compiler, fixed allocation/reserve, cgroup memory limit, guest page
  size/exposed NUMA-node count, swap/HugeTLB capacity, and THP/zswap policy.
  Missing or inconsistent system-boundary evidence remains observational.
  `provider-observation-export-v1` adds a deterministic read-only CSV download
  containing the exact cohort contracts, statistics, UTC windows, target IDs,
  and source Run IDs. It neutralizes spreadsheet-formula prefixes, caps output
  at 50,000 rows/16 MiB, and exports no provider score or ranking.
  PostgreSQL, Redis GET/SET, and
  MySQL/MariaDB read/write metrics now enter descriptive cohorts without being
  converted into a score. Median/P10/P90/best/worst/spread statistics retain a
  guarded nine-sample/three-target/three-window comparable state. MySQL and
  MariaDB or different server versions cannot be silently merged. The complete
  development head passes 219 Python tests, 4 dashboard/CI-contract tests,
  Ruff, 75.7% branch coverage, dashboard lint and strict type checking, OpenAPI
  validation, and the production build without starting provider load;
- implemented and simulation-verified `linux-security-posture-v2` single-target
  executor for a Linux Controller host or authenticated Linux Agent, with
  profile/methodology/tool versions, remote Agent attribution, task heartbeat/
  cancellation, dashboard target selection, and bounded read-only
  Linux kernel, privilege, LSM, Secure Boot, cgroup, network-hardening, and
  exact system-mount evidence. Core handler text, EFI identifiers, mount source
  devices, non-target mountpoints, and raw mount options are not persisted.
  Missing controls remain unavailable and no security score is produced. The
  executor passes nine dedicated tests and the complete development head
  passes 155 Python tests, 3 rendered-dashboard tests, dashboard lint, and the
  production build. Provider security controls remain unavailable, so the
  Security domain is Partial;
- least-privilege GitHub Actions CI in `.github/workflows/ci.yml`, with every
  third-party action pinned to an immutable commit SHA. The Python matrix covers
  the supported 3.9 floor and 3.13 on Linux plus 3.13 on Windows. The dashboard
  job uses pinned Node 22.23.2 and the manifest-pinned pnpm 11.16.0, requires the
  lockfile, then runs ESLint, strict TypeScript checking, OpenAPI parsing/local-
  reference validation, the production build, three rendered-dashboard tests,
  and a CI policy test that rejects mutable action references or benchmark
  commands. Adding the type-check gate exposed and fixed two nullable Generator
  accesses and missing Worker binding types. Five dedicated CLI tests now cover
  version/inventory JSON, dependency preview versus confirmed bootstrap,
  serve/join/Agent argument dispatch, confirmation gates, progress, and mocked
  compute/storage/read-only-security dispatch without starting work. The tests
  exposed and fixed `--timeout-seconds 0` silently selecting the default instead
  of failing its documented bound; `cloudmark/__main__.py` now has 97.6% branch
  coverage. The complete development head passes 216 Python tests and 4 Node
  tests without starting load. A pinned
  optional Python quality environment adds Ruff 0.16.7 `E9`/`F` checks and
  coverage.py 7.10.7 branch measurement compatible with the Python 3.9 floor;
  the clean local baseline is 75.5% and CI enforces a 70.0% minimum. The first
  hosted matrix run exposed and fixed Python 3.9 popcount compatibility, a
  test-only global `os.name` mutation that selected `WindowsPath` on Linux, and
  a Windows-runner temp path crossing the intentionally strict Linux MySQL
  socket bound; production safety limits were preserved. An unsupported pnpm
  setup input was also replaced with an explicit `pnpm install
  --frozen-lockfile` step, removing CI warnings while retaining lockfile
  enforcement;
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
  verify physical-host NIC/interrupt placement; fixed system-resolver
  configuration and explicit UDP/TCP A/AAAA diagnostics are observational,
  with no controlled authoritative server, cache-cold repetition, independent
  DNSSEC validation, proof of automatic resolver fallback, or Windows parity;
  manual fixed-pair repeated UTC-day campaigns are implemented, while
  unattended scheduling, cross-pair
  orchestration, Windows route parity, and mTLS remain unimplemented;
- PostgreSQL database coverage is Partial: read-only, durable read/write,
  concurrency, connection churn, fixed-count transaction tail percentiles, and
  Generator CPU validity are implemented; forced-checkpoint isolation and
  same-Target logical backup/restore with artifact cleanup are implemented;
  MySQL/MariaDB now adds isolated InnoDB point-select, read-only, write-only,
  and read/write Sysbench profiles with P99 and Generator validity; physical/
  PITR backup, cross-zone recovery, replication, MySQL/MariaDB checkpoint
  isolation, binary-log overhead, and managed-service behavior remain
  unavailable;
- an abrupt Agent or host termination can leave an isolated PostgreSQL task
  directory for manual operator review; the Agent refuses to overwrite or
  automatically delete unknown residual state; the same review requirement
  applies to an isolated Web service directory;
- Web/API/TLS coverage is Partial: fixed static/JSON endpoints, a packaged
  dynamic application behind Nginx, HTTP/HTTPS concurrency, Generator CPU
  validity, connection churn, transfer rate, tail latency, and HTTP/2
  negotiation and fixed HTTP/2 multiplexed dynamic load are implemented;
  database-backed applications, HTTP/3, CDN, WAF, autoscaling, and DDoS
  resilience remain unavailable;
- Security coverage is Partial: Linux guest kernel, privilege, LSM, Secure
  Boot, cgroup, selected network-hardening, and exact system-mount controls are
  collected read-only with redaction and Run provenance; Windows parity,
  firewall/security groups, SSH, encryption, vulnerability status, IAM/RBAC,
  tenant isolation, audit delivery, incident response, and compliance remain
  unavailable, and no provider security rating is produced;
- GPU evidence and GPU benchmarks are not complete;
- scheduled sampling campaigns, cross-pair orchestration, cross-zone analysis,
  timestamped cost,
  operational domains, and final provider ratings remain Roadmap; suitability
  evaluates individual targets while provider observations remain descriptive;
- Windows is suitable for the Controller and inventory, but benchmark executor
  parity with Linux is incomplete;
- one VM and one time window cannot establish provider-wide quality.

## Next priorities

1. Add physical-host/fabric and administrative-path verification, additional vendor
   NIC queue-counter normalization, controlled
   authoritative/cache-cold/DNSSEC resolver coverage, unattended campaign
   scheduling, and Windows route parity
   before promoting the network domain from Partial.
2. Add database-backed Web applications, HTTP/3, reverse-proxy variants,
   compression, CDN, WAF, and autoscaling evidence.
3. Extend database coverage with MySQL/MariaDB checkpoint isolation,
   physical/PITR backup, replication, cross-zone recovery, binary-log/
   replication overhead, and RPO/RTO evidence.
4. Complete remaining compute, memory/NUMA, GPU, provider security, reliability,
   observability, container, and control-plane executors.
5. Extend campaigns across independent targets and add timestamped price
   inputs before any final provider-rating methodology; exact-cohort CSV export
   is now available.
6. Calibrate and version requirement thresholds across regional clouds, global
   clouds, and self-operated bare metal before treating them as stable policy.
7. Run provider-machine validation only after the development milestones are
   complete and the operator explicitly starts acceptance testing.

## Operational reminder

The local dashboard, Controller process, reverse tunnel, and remote Agent are
ephemeral processes. Source control does not preserve their live state. Follow
`docs/OPERATIONS_RUNBOOK.md` and restore `.cloudmark` from a protected snapshot
when moving to another machine.
