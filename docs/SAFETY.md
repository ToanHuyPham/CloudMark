# Safety model

CloudMark assumes benchmark machines may be blank, but it does not interpret
permission to install tools as permission to destroy data or attack third-party
systems.

## Storage

- Filesystem test-file mode is the only enabled storage mode in `0.5.0`.
- The selected directory is resolved before execution.
- Free space must cover the test file plus the larger of 1 GiB or 5% volume
  reserve.
- `fio` receives an exact filename under CloudMark's benchmark directory.
- The file and fio log files are removed in a `finally` block after completion,
  failure, timeout, cancellation, or an interrupted CLI session.
- Raw devices, TRIM, full-device preconditioning, and power-loss tests are off.

## Compute and memory

- CPU and memory profiles require explicit load confirmation.
- CPU, memory, and storage saturation suites cannot overlap locally.
- CPU duration, warm-up, prime limit, and thread count come from versioned
  profiles rather than caller-controlled command fragments.
- The native memory tool accepts only read, write, copy, and triad kernels.
- Memory profiles allocate a fixed working set and preserve at least 512 MiB of
  available memory when the operating system exposes that measurement.
- The native source is compiled in the benchmark workspace with an exact GCC
  argument list; compiler output is retained when compilation fails.
- Cancellation terminates the current child process. A failed or cancelled run
  retains completed jobs only as partial evidence.

## Runner controls

- Every run has a bounded timeout between 30 seconds and 12 hours.
- Cancellation terminates the active child process and then performs cleanup.
- Commands are passed as argument arrays with `shell=False`.
- Completed jobs are retained as partial evidence, but cancelled or failed runs
  are never treated as complete assessment results.
- A Controller restart marks stale queued/running jobs as failed and interrupted.

## Network

- There is no arbitrary target-IP load endpoint.
- The project policy disables cloud-to-controller measurements.
- Provider throughput runs only between paired, authenticated agents.
- Agent tasks are restricted to exact iperf3 and ping argument lists, ports
  5201–5210, stream counts 1/4/8/16, and a 60-second per-measurement duration
  cap.
- UDP uses one stream and an Agent-enforced 100 kbit/s–1 Gbit/s absolute rate
  range. The standard Controller profile applies the tighter 1 Mbit/s–1 Gbit/s
  range after deriving targets from measured directional TCP throughput.
- Ping count, interval, and timeout are bounded. Loopback, unspecified,
  multicast, and link-local peer addresses are rejected.
- Route, MTU, bounded numeric path-trace, NIC-driver, offload, and TCP
  congestion-control evidence is read-only. `tracepath` accepts only the exact
  paired address and a fixed eight-hop ceiling; it is not a subnet or port
  scanner. `ethtool` is restricted to fixed query arguments against the
  route-derived egress interface; CloudMark never changes NIC or kernel network
  configuration.
- Pre/post interface-counter snapshots use structured `ip -s -j link` output
  from that same route-derived interface. CloudMark never resets counters, and
  a counter decrease is reported as unavailable evidence rather than coerced
  into a delta.
- Network v9 reads `ethtool -S` only for the route-derived interface. It
  examines at most 4,096 lines, accepts queue indexes 0-127, recognizes only a
  bounded common counter-name set, and never changes queue, RSS, RPS, XPS, IRQ,
  or NIC configuration. Unknown driver counters remain unclassified.
- Network v9 resolver diagnostics read at most 64 KiB from Linux
  `/etc/resolv.conf` and, when `dig` is present, query only `example.com.` for
  A and AAAA once per Agent with fixed retry and timeout bounds. The Controller
  cannot submit a hostname. Search-domain names and answer addresses are not
  persisted, and the diagnostic never claims provider DNS ownership.
- Network v9 runs `ethtool -x` only for the route-derived interface and parses
  at most 4,096 RSS entries with queue indexes 0-127. It never persists the RSS
  hash key. Linux sysfs RPS/XPS masks are limited to 128 queues, procfs affinity
  is limited to 256 interface-exposed MSI IRQs, and each control-file read is
  capped at 4,096 bytes. CloudMark never runs `ethtool -X`, writes queue/IRQ
  controls, or claims that guest-visible placement proves physical-host or
  provider-fabric configuration.
- Address classes and observed IP hops remain descriptive. They never establish
  path ownership or prove that traffic crossed the public Internet.
- Servers use one-shot mode and an independent watchdog deadline.
- Cancelling a run prevents queued work from starting; active child processes
  retain bounded task and watchdog timeouts.
- Repeated campaigns are manual-dispatch only. Creating a campaign does not
  start a Run, and every window requires both `confirm_network_load=true` and
  `confirm_campaign_window=true`.
- A campaign locks one Agent pair, topology evidence class, profile, and
  methodology. Only completed comparison-eligible Runs count, at most once per
  UTC day. Failed or cancelled attempts remain visible and never consume a
  valid window.
- Campaign completion is temporal evidence for one pair, not provider-wide
  quality evidence and not authorization for unattended scheduling.
- A profile or methodology upgrade supersedes an unfinished campaign instead
  of mutating its immutable contract or dispatching it under new semantics.
- Public DDoS, spoofing, reflection, and amplification are outside project
  scope. Future resilience tests require authenticated, operator-owned targets
  with enforced rate and duration caps.

## Remote execution

- Remote tasks are authenticated per Agent and restricted to compute, memory,
  storage, guarded network, guarded PostgreSQL, and guarded Web/API/TLS kinds; arbitrary shell
  commands are refused.
- The Agent validates suite, installed profile, protocol version, explicit load
  confirmation, and timeout before executing.
- The Agent workspace is configured locally and cannot be supplied by a remote
  task.
- One saturation task is allowed per Agent, and peer-network work cannot overlap
  with saturation work in the same session.
- Task heartbeat and cancellation continue while the benchmark child process is
  active. A 20-second control outage stops Agent load; a 45-second heartbeat gap
  closes the task at the Controller.
- Completed remote evidence must match the dispatched profile and methodology
  versions. Failed or cancelled evidence remains partial.

## Database

- Database runs require two authenticated provider Agents and explicit
  `confirm_database_load` authorization.
- PostgreSQL uses an ephemeral cluster below the Target Agent workspace. The
  Agent refuses caller-controlled data directories and preserves at least 1 GiB
  or 5% free space.
- Port, scale factor, client count, thread count, duration, database name,
  username, server settings, and pgbench scripts are allow-listed.
- Database v2 permits exactly one fixed-count tail shape: four clients, 1,000
  transactions per client, the built-in TPC-B-like script, and a 120-second
  task deadline. The Agent-wide ceiling is 16,000 transactions; no API caller
  can supply a transaction count or logging path.
- Generator pgbench logs are created only below a generated
  `database-client-logs/task_*` directory, parsed up to 8 MiB and 20,000 rows,
  and removed after success, failure, cancellation, timeout, or parser error.
  Exact row-count mismatch, malformed data, or truncation remains partial
  evidence rather than an invented percentile.
- Timed Database v2 jobs sample only Linux `/proc/stat` and the active pgbench
  `/proc/<pid>/stat`, cap evidence at 120 summaries, and become
  comparison-ineligible when pgbench reaches 90% of one logical CPU. No CPU
  configuration is changed.
- Host authentication permits only the exact paired Generator address. No
  database password or generated secret is persisted in a task payload.
- Durability settings stay enabled. The executor does not present an unsafe
  `fsync=off` result as production database performance.
- The Target watchdog stops PostgreSQL and removes the generated cluster after
  success, failure, timeout, cancellation, or more than 20 seconds without
  successful Controller contact.
- The logical recovery profile accepts no caller-controlled database name,
  query, path, format, or tool argument. It uses fixed loopback connections,
  the `cloudmark_restore` database, a custom-format uncompressed archive, and
  four fixed pgbench row-count queries.
- Recovery begins only after Generator load completes. Free space must cover
  twice the estimated dataset plus the normal reserve. The archive may not
  exceed twice the estimate. The restored database and archive are removed on
  success and best-effort cleanup runs on failure; unverified recovery cleanup
  makes the Run comparison-ineligible.

## Redis

- Redis requires a two-Agent session and explicit database-load confirmation.
- It binds only the Target address on TCP 56379 and requires a random per-Run
  password delivered through the memory-only task-secret channel.
- GET/SET, request count, clients, pipeline depth, and value size are fixed by
  versioned profiles. AOF persistence remains enabled with fsync every second.
- The Target watchdog terminates Redis and removes configuration, credentials,
  AOF/RDB data, and logs on every terminal path.

## Web, API, and TLS

- Web runs require two authenticated provider Agents and explicit
  `confirm_web_load` authorization.
- Nginx binds the exact Target address on fixed TCP ports 58080 and 58443. It
  allows only the paired Generator and Target addresses, then denies all other
  clients.
- The Agent generates only the fixed health, 1 KiB JSON, and 256 KiB static
  payloads. Scheme, path, port, concurrency, duration, TLS version, and
  keep-alive behavior are allow-listed.
- Web v2 starts only the packaged CloudMark Python fixture on
  `127.0.0.1:58081`; the Controller cannot supply its bind address, port,
  response, code, or Nginx proxy directives. Nginx is the only provider-facing
  listener. Dynamic ApacheBench jobs use the same concurrency, duration, and
  request limits as static jobs.
- Web v2 samples only Linux `/proc/stat` and the active ApacheBench
  `/proc/<pid>/stat` at one-second intervals, retains summaries rather than raw
  process records, caps samples at 120, and rejects comparison evidence at 90%
  of one logical CPU. No CPU setting is changed.
- The HTTP/2 observation uses fixed HTTPS, Target address, port 58443, dynamic
  path, TLS 1.2, ten-second deadline, and curl arguments. It is one diagnostic
  request and never becomes an HTTP/2 throughput or capacity claim.
- The per-run certificate and key are ephemeral. The self-signed certificate
  measures TLS handling and does not claim public trust-chain quality.
- Arbitrary URLs and DDoS traffic are not supported. ApacheBench jobs are
  bounded by time, concurrency, request ceiling, task timeout, and Controller
  contact watchdog.
- The Target watchdog stops Nginx and the packaged application process, then
  removes generated configuration,
  payloads, logs, certificates, and keys on every normal terminal path.
  Unknown residual directories after abrupt host failure require manual review.

## Bootstrap

- Package manager commands are predefined lists, not shell strings.
- Preview is the default. Installation requires `bootstrap --yes` and
  administrator/root privileges.
- Installed package names and commands are visible in the preview.

## Suitability and provider claims

- Suitability evaluation is read-only and never starts a benchmark.
- Failed, cancelled, stale, incompatible, or cleanup-unverified results cannot
  satisfy a workload gate.
- Missing evidence remains unavailable and is never represented as zero.
- Evidence from different target IDs is never combined to make one target
  appear complete.
- A passing measured subset is capped at `Conditional fit` when the required
  product capability is outside the current methodology.
- Provider status remains `not-rated` until independent same-product targets,
  repeated windows, security, reliability, control-plane, and cost evidence are
  available.
- Repeated-window comparison never merges SKU, region, operating-system,
  profile, methodology, or unit boundaries.
- Descriptive distributions remain observation-only below nine samples, three
  targets, or three UTC-day windows, and never trigger a benchmark themselves.

## Secrets

- The local controller token is stored in `.cloudmark/controller.token` and is
  excluded from Git.
- The browser retains it only in `sessionStorage`.
- Pairing secrets are stored as hashes in SQLite.
- Independent agent credentials are returned once and stored only as hashes.
- Ephemeral task secrets are bounded to eight named string fields and exist only
  in Controller memory. They never appear in `agent_tasks.payload_json`, task
  read models, progress responses, runtime snapshots, dashboard responses, or
  Git. Only the authenticated assigned Agent receives them while claiming the
  queued task, and terminal task paths erase them from memory.
- Controller restart intentionally loses ephemeral task secrets. Unfinished
  task recovery closes the associated work rather than reconstructing or
  persisting a plaintext credential.
- Provider credentials and instance user-data are never included in reports.
