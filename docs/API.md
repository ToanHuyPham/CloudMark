# CloudMark API v1

Default base URL: `http://127.0.0.1:8787/api/v1`.

Read operations are available on loopback. Write operations require:

```http
X-CloudMark-Token: <token printed by cloudmark serve>
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | API status and version |
| GET | `/system` | Inventory and provider evidence |
| GET | `/system?refresh=true` | Refresh inventory and metadata |
| GET | `/dashboard` | Compact aggregated local dashboard payload |
| GET | `/suitability` | Versioned target-scoped workload gates and provider-readiness evidence |
| GET | `/provider-comparisons` | Exact-cohort repeated-window descriptive statistics |
| GET | `/provider-comparisons.csv` | Bounded audit CSV with exact contracts, statistics, windows, and Run IDs |
| GET | `/cost-observations` | Immutable timestamped operator cost context |
| POST | `/cost-observations` | Record source-bound cost context for one exact target |
| GET | `/cost-observations.csv` | Bounded audit CSV of raw cost observations and non-scoring policy |
| GET | `/profiles` | Benchmark and scenario profiles |
| GET | `/network-campaigns` | Repeated network campaign projections |
| POST | `/network-campaigns` | Create an immutable fixed-pair campaign without starting traffic |
| GET | `/network-campaigns/{id}` | One campaign, attempts, and UTC-day progress |
| POST | `/network-campaigns/{id}/runs` | Manually dispatch the next eligible campaign window |
| GET | `/storage-campaigns` | Repeated storage campaign projections |
| POST | `/storage-campaigns` | Create an exact-contract campaign from a completed baseline without starting load |
| GET | `/storage-campaigns/{id}` | One storage campaign, attempts, and UTC completion-day progress |
| POST | `/storage-campaigns/{id}/runs` | Manually dispatch the next eligible storage window |
| GET | `/runs` | Run history |
| GET | `/runs/{id}` | One run and its complete raw evidence |
| POST | `/runs` | Submit an asynchronous run |
| POST | `/runs/{id}/cancel` | Cancel a queued or running benchmark |
| POST | `/sessions` | Create a 30-minute pairing session |
| GET | `/sessions/{id}` | Session and joined agents |
| POST | `/sessions/{id}/join` | Join with the short-lived session token |
| POST | `/agents/{id}/heartbeat` | Refresh authenticated agent presence |
| POST | `/agents/{id}/tasks/next` | Atomically claim the next allow-listed task |
| POST | `/agents/{id}/tasks/{taskId}/progress` | Publish progress and poll cancellation |
| POST | `/agents/{id}/tasks/{taskId}/result` | Complete, fail, or cancel a claimed task |

## Create an inventory run

```http
POST /api/v1/runs
Content-Type: application/json
X-CloudMark-Token: ...

{"suite":"inventory","profile":"default"}
```

The response is `202 Accepted`. Poll `/runs/{id}` until the state is
`completed`, `failed`, or `cancelled`.

Provider detection behind `/system` uses only fixed identity endpoints with
proxies disabled, sub-second timeouts, and a 64 KiB response cap. AWS requires
an ASCII/control-free bounded IMDSv2 token and complete identity document;
Azure requires a complete compute identity; Google Cloud additionally requires
the metadata-flavor response. Malformed or incomplete evidence returns
`Unknown`. Declared manifests remain bounded and explicitly unverified, and
their local path is not returned.

System and Agent inventory include `memory.environment` using the
`memory-environment-v2` contract. Memory Run results retain the same observation
under `result.preflight.memory_environment`. It contains bounded guest-visible
Linux NUMA nodes, CPU lists/counts, node memory, relative distance values, page
size, evidence status, and explicit no-host-placement/no-performance policy.
Relative distances are not latency measurements.
The nested `paging` object adds bounded swap, anonymous huge-page, HugeTLB,
THP-policy, and zswap snapshot fields. `snapshot_only=true` and
`pressure_measured=false` prevent instantaneous usage from being interpreted as
a pressure or performance measurement.
Memory preflight also returns `memory_allocation_boundary`, including redacted
cgroup version, limit/current/headroom, host availability, effective
availability, levels checked, limiting ancestor depth, and verification status.
The cgroup path is never persisted.
Compute and memory preflight return `cpu_execution_boundary` with host,
affinity, and effective logical-core counts. CPU IDs are never returned, and
verified cgroup quota adds fractional `quota_capacity_cores`, a
`quota_thread_ceiling`, levels checked, and limiting ancestor depth. Cgroup
paths are never returned.

Inventory also includes `container.environment` under the
`container-environment-v1` contract on Linux. It reads bounded fixed procfs
sources and fixed Docker/Podman marker presence, then exposes only normalized
runtime/orchestrator hints, cgroup version, root filesystem class, source
status, and explicit limitations. Container IDs, raw cgroup paths, mountinfo,
runtime-daemon responses, and Kubernetes API data are never returned. A
`not-detected` result does not prove direct host execution.

Inventory includes `clock.environment` under `clock-environment-v1`. Portable
clock semantics are always attempted; Linux adds bounded clocksource and time-
namespace context plus fixed `NTP`/`NTPSynchronized` systemd property queries.
The payload labels these as OS assertions and keeps
`cloudmark_ntp_validation_performed=false`, `offset_measured=false`, and
`drift_measured=false`. No time peer, RTC, raw procfs/sysfs content, or namespace
identifier is returned.

`/dashboard` is a presentation endpoint polled by the local UI. It retains the
latest completed result for each system suite/target and each paired suite,
plus all active Runs. Older history entries retain lifecycle metadata but omit
their result from this payload. Raw tool output is omitted, and only the last
storage job retains a presentation timeline capped at 90 points. These changes
do not modify SQLite evidence. Use `/runs/{id}` for the complete immutable Run,
raw tool output, and full-resolution time series. API JSON is transmitted in a
compact UTF-8 representation; field values and Unicode are unchanged.

While running, the response includes `progress`, `phase`, `current_job`,
`completed_steps`, `total_steps`, `heartbeat_at`, and version fields for the
runner, methodology, and measurement tool. Compute, memory, and storage results
are updated after each completed job so a failed or cancelled run preserves
partial evidence.

## Create a compute run

```json
{
  "suite": "compute",
  "profile": "compute-quick",
  "confirm_load": true,
  "timeout_seconds": 600
}
```

Supported profiles are `compute-quick` and `compute-standard`. `confirm_load`
is mandatory because the executor intentionally saturates selected CPU cores.
The result contains `compute_jobs`, per-second sysbench samples, latency,
stability, host telemetry, and all-core scaling evidence.

Add an authenticated Agent target to run the same profile on a provider VM:

```json
{
  "suite": "compute",
  "profile": "compute-quick",
  "agent_id": "agent_123",
  "confirm_load": true,
  "timeout_seconds": 600
}
```

## Create a memory run

```json
{
  "suite": "memory",
  "profile": "memory-quick",
  "confirm_load": true,
  "timeout_seconds": 600
}
```

Supported profiles are `memory-quick` and `memory-standard`. The Linux executor
compiles the packaged C/OpenMP tool with GCC after enforcing its fixed allocation
and 512 MiB memory reserve. The result contains `memory_jobs`, bandwidth,
processed bytes, checksums, tool/compiler identity, and host telemetry.

Compute, memory, and storage are mutually exclusive per execution target. The
API returns `400` if another one is queued or running on the same host. Omit
`agent_id` to execute on the Controller host; supply one explicit online Agent
ID for remote execution. CloudMark never silently chooses or redirects a target.

## Create a storage run

```json
{
  "suite": "storage",
  "profile": "disk-quick",
  "confirm_write": true,
  "timeout_seconds": 600
}
```

`confirm_write` is mandatory because even safe filesystem mode writes a
temporary file. The API rejects the run if `fio` is unavailable or the safety
reserve cannot be maintained.

Supported storage profiles are `disk-quick`, `disk-standard`, `disk-database`,
`disk-throughput`, and `disk-sustained`.

## Create a Linux Security Posture run

```json
{
  "suite": "security",
  "profile": "linux-security-posture",
  "agent_id": "agent_optional"
}
```

Omit `agent_id` to collect on a Linux Controller host, or select an online
authenticated Linux Agent reporting `security_posture_linux`. The suite is
read-only and does not require a load-confirmation flag. Results retain
`linux-security-posture-v2`, execution-target attribution, evidence status,
coverage, fixed control sources, redaction policy, and normalized guest
controls. No security score or provider-security claim is returned.

## Create a peer network run

```json
{
  "suite": "network",
  "profile": "network-peer-quick",
  "session_id": "session_123",
  "confirm_network_load": true
}
```

The session must contain an online `target` and `generator`. Both must advertise
a peer-reachable IP and report `iperf3`. Network v9 additionally requires both
Agents to report `iproute2`, `tracepath`, `ethtool`, and Linux TCP
congestion-control evidence before load starts. `confirm_network_load` is
mandatory.
Supported profiles are `network-peer-quick` (`network-v1`) and
`network-peer-standard` (`network-v9`). Quick executes directional TCP only.
Standard executes 21 bounded peer evidence steps: two pre-load and two
post-load route/interface/MTU and numeric path-trace, read-only NIC driver/offload,
TCP congestion-control, structured aggregate interface counters, and bounded
driver-exposed per-queue counters, one versioned fixed UDP/TCP A/AAAA
system-resolver diagnostic,
and bounded guest RSS/RPS/XPS/MSI IRQ-affinity observations per Agent; idle latency;
directional TCP scaling; UDP rate sweeps derived from each direction's TCP
baseline; and one simultaneous bidirectional TCP measurement. Its result
includes aggregate byte/packet/error/drop deltas, versioned observational queue
packet/byte distributions when the NIC driver exposes recognized counters, guest-visible
steering/IRQ placement when Linux exposes it, resolver
configuration and bounded query outcomes when available, and comparison eligibility based on
stable pre/post routes, destination-reaching bounded traces, a complete
NIC/TCP-control/counter window, and Generator CPU/scaling headroom. Address
class and observed hops never prove public-Internet transit. No performance
traffic is sent to the Controller.

Resolver observations expose `diagnostic_version`, at most four bounded query
records, and a Controller-rederived `transport_comparison` for A/AAAA over UDP
and TCP. Returned answer addresses are never included. A TCP response is marked
as recovery only when the matching UDP response carried TC; otherwise the API
does not claim automatic fallback. Version 3 also exposes a Controller-rederived
`dnssec_summary`. `resolver_asserted_authenticated_data` records the configured
resolver's AD response only; `cloudmark_dnssec_validation_performed` is always
false, and signatures are never returned.

## Create and run a repeated network campaign

Create the contract only after the same Target and Generator are online and
the pairing topology is correct:

```http
POST /api/v1/network-campaigns
Content-Type: application/json
X-CloudMark-Token: ...

{
  "label": "Provider same-zone repeated network evidence",
  "session_id": "session_123",
  "profile": "network-peer-standard",
  "target_windows": 3
}
```

Creation returns `201` and never starts network traffic. The immutable
`network-campaign-v1` contract records the pair, topology evidence class,
profile version, `network-v9` methodology, and a 3-30 day target. Dispatch the
next eligible window explicitly:

```http
POST /api/v1/network-campaigns/campaign_123/runs
Content-Type: application/json
X-CloudMark-Token: ...

{"confirm_network_load":true,"confirm_campaign_window":true}
```

The response is `202`. CloudMark counts no more than one completed,
comparison-eligible standard Run per UTC calendar day. Failed and cancelled
attempts remain auditable and may be retried on the same day. A window is
blocked when a campaign Run is active, a valid Run already exists for that UTC
day, either Agent is offline, or the immutable pair/topology/profile contract
no longer matches. This campaign establishes time-separated evidence for one
fixed pair only; it does not rate a provider. When the installed standard
profile or methodology changes, an unfinished older campaign is returned as
`superseded`; its Runs remain readable, but the dispatch endpoint refuses to
continue it under the new contract.

## Create and run a repeated storage campaign

Create the contract from a completed storage Run with full measurement,
cleanup, and `storage-environment-v1` evidence:

```http
POST /api/v1/storage-campaigns
Content-Type: application/json
X-CloudMark-Token: ...

{
  "label": "Provider storage repeated evidence",
  "baseline_run_id": "run_123",
  "target_windows": 3
}
```

Creation returns `201`, starts no load, and counts the immutable baseline as
window one. The `storage-campaign-v1` contract locks target/provider/SKU/region/
OS identity, profile and methodology, filesystem/mount semantics, applicable
guest block policy, and exact executor version.

Dispatch a later window explicitly:

```http
POST /api/v1/storage-campaigns/storage_campaign_123/runs
Content-Type: application/json
X-CloudMark-Token: ...

{"confirm_write":true,"confirm_campaign_window":true}
```

The response is `202`. The target must still be online and match the immutable
identity. Both confirmations are retained in the Run request; `/runs` rejects
caller-supplied campaign fields. A Run counts only when every executor-specific
measurement and cleanup gate is complete, its persisted run-time Target and
exact storage contract match, and its timezone-aware start and completion stay
within the dispatched UTC day. Duplicate-day, failed, cancelled, drifted, and
cross-midnight attempts remain visible but do not consume a window. Completion
is temporal evidence for one target, not a provider rating.

## Create a database or cache peer run

```json
{
  "suite": "database",
  "profile": "postgres-peer-quick",
  "session_id": "session_123",
  "confirm_database_load": true
}
```

The session must contain an online Target with `postgres`, `initdb`,
`pg_isready`, and `pgbench`, plus an online Generator with `pgbench`.
Standard Database v2 additionally requires Generator `pgbench_latency_log` and
`procfs_process_cpu` capabilities.
`confirm_database_load` is mandatory because the run creates a temporary
dataset and generates read/write transactions. Supported profiles are
`postgres-peer-quick`, `postgres-peer-standard`, `postgres-peer-recovery`, and
`postgres-peer-checkpoint`. The result contains
`database_measurements`, fixed durability settings, tool versions, target and
generator identity, Generator CPU validity, exact fixed-count transaction
P50/P95/P99/P99.9, Generator-log cleanup, comparison eligibility, and Target
cleanup evidence. Transaction traffic never traverses
the Controller.
The recovery profile adds one Target-side
`database-postgresql-recovery-v1` logical drill after its fixed workload. It
returns backup bytes, backup/restore duration, four source/restored pgbench row
counts, scale-shape validation, recovery-artifact cleanup, and final cluster
cleanup. The API cannot provide a database name, SQL query, path, archive
format, or recovery command argument.

`postgres-peer-checkpoint` uses the same endpoint and confirmation flag under
`database-postgresql-checkpoint-v1`. The Target additionally requires `psql`;
the Generator requires pgbench and Linux process CPU accounting. The fixed
result includes baseline/post-load version-aware counters, requested/timed
checkpoint deltas, write/sync time, buffers written, forced-checkpoint Target
wall time, Generator validity, and cluster cleanup. API callers cannot supply
SQL, checkpoint mode, statistics view, scale, workload shape, or timing.

Redis profiles `redis-peer-quick` and `redis-peer-standard` use the same
database Run endpoint and confirmation flag. They require `redis_server` and
`redis_cli` on Target plus `redis_benchmark` and Linux CPU accounting on
Generator. The per-Run password is memory-only and never appears in the API
result. Evidence includes fixed GET/SET request rate, P50/P95/P99 latency, value
size, concurrency, pipeline depth, AOF policy, Generator validity, and cleanup.

MySQL/MariaDB profiles `mysql-peer-quick` and `mysql-peer-standard` also use
this endpoint and confirmation flag. Target capabilities are `mysql_server`,
`mysql_client`, `mysql_admin`, and `mysql_initializer`; Generator capabilities
are `sysbench_mysql` and Linux CPU accounting. The API accepts no SQL, Lua
script, database name, account, table shape, address, or port override. Results
contain `mysql_measurements`, implementation/version, fixed InnoDB durability,
transactions, TPS, queries, QPS, ignored errors, reconnects, minimum/average/
P99/maximum latency, Generator validity, Sysbench table cleanup, and Target
service cleanup. The per-Run password never appears in the API result or task
read models.

## Create a Web/API/TLS peer run

```json
{
  "suite": "web",
  "profile": "web-peer-quick",
  "session_id": "session_123",
  "confirm_web_load": true
}
```

The session must contain an online Target with `nginx` and `openssl`, plus an
online Generator with `ab`. Standard Web v2 additionally requires Target
`nginx_http2` and Generator `curl_http2` plus `procfs_process_cpu` capabilities.
`confirm_web_load` is mandatory because the run
creates a temporary service and generates bounded HTTP/TLS load. Supported
profiles are `web-peer-quick`, `web-peer-standard`, and `web-peer-http2`. The result contains
`web_measurements`, request/error counts, throughput, P50/P90/P95/P99/maximum
latency, transfer evidence, TLS protocol/cipher evidence, tool versions,
Generator process/host CPU headroom, dynamic reverse-proxy evidence, one fixed
HTTP/2 negotiation observation for Standard, explicit comparison validity,
target/generator identity, and cleanup status. Only the fixed Target address,
ports 58080/58443, and CloudMark endpoints are accepted; the dynamic
application binds only Target loopback port 58081 and traffic never
traverses the Controller.

`web-peer-http2` uses the same endpoint and confirmation flag with methodology
`web-http2-load-v1`. It requires `nginx_http2` on Target and `h2load`,
`h2load_http2_only`, `h2load_request_log`, and Linux CPU accounting on
Generator. The result adds
`http2_measurements` with fixed clients/native threads/max streams/request
counts, request/status/error totals, throughput, transfer bytes, exact-log
P50/P95/P99/maximum latency, Generator CPU, log cleanup, and service cleanup.
The API cannot provide a URL, header, body, timing script, rate, or workload
shape.

## Cancel a run

```http
POST /api/v1/runs/run_123/cancel
Content-Type: application/json
X-CloudMark-Token: ...

{}
```

Cancellation is accepted only for `queued` or `running` runs. The runner stops
the active child process, removes temporary files, preserves completed job
results, and changes the run state to `cancelled`.

## Pair two provider agents

1. `POST /sessions` with the controller token. Include a topology declaration
   when the pair is intended for provider comparison:

   ```json
   {
     "label": "Provider same-zone assessment",
     "topology": {"scope": "same-zone", "source": "operator-declared"}
   }
   ```

   Accepted scopes are `same-host`, `same-zone`, `cross-zone`, `cross-region`,
   `public-internet`, and `undeclared`. Undeclared sessions remain diagnostic
   only for provider cohorts.

   Session responses add `topology.verification`. Its status is `pending`,
   `unavailable`, `derived`, `confirmed`, `compatible`, or `contradicted`.
   Independent placement observations use trusted provider metadata. Globally
   routable advertised peer endpoints are recorded only as address-class
   evidence because they do not prove that traffic traversed the public
   Internet. Verification summaries do not expose peer addresses.
2. Copy the returned session ID and short-lived join token to each agent.
3. Each persistent agent calls `/sessions/{id}/join`. The response includes a
   unique `agent_id` and an agent credential that is never returned again.
4. The agent uses `X-CloudMark-Agent-Token` for heartbeat, polling, progress,
   cancellation checks, and result submission. The Controller stores only its
   SHA-256 hash.
5. Read `/sessions/{id}` to verify the target and generator are online.

Agent endpoints are internal protocol endpoints for `cloudmark agent`. They do
not accept the Controller token. A task is scoped to one agent and can contain
only an executor kind implemented by the agent allow-list.

For remote single-system tasks, `/progress` updates the parent run and returns
`cancel_requested`. Completed result envelopes must match the dispatched suite,
profile, profile version, methodology version, and `remote-agent-v1` protocol.
The API request-body limit is 16 MiB for bounded raw time-series evidence.

## Read workload suitability

```http
GET /api/v1/suitability
```

The response contains `suitability-v1` evaluations for each observed target at
the Essential, Standard, and Demanding requirement levels. Every metric check
includes its threshold, operator, status, source Run ID, profile, methodology,
time, unit, quality, and staleness. Missing evidence remains `unavailable` or
`stale`; it is never converted to zero. Provider status remains `not-rated`
until the documented multi-target, multi-window, operational, and cost gates
are satisfied.

## Read provider observations

```http
GET /api/v1/provider-comparisons
```

The `provider-observations-v6` response groups fresh valid evidence only when
provider, product/SKU, region, operating system, profile, methodology, metric,
unit, paired topology, and topology evidence class match. Database and cache
metrics additionally require the same engine, implementation, and exact server
version. Compute and memory metrics additionally require the same verified
executor version, host/affinity/effective thread boundary, and cgroup CPU quota
contract. Memory cohorts also require the same compiler, fixed allocation and
reserve, cgroup memory limit, guest page size and exposed NUMA-node count, swap
and HugeTLB capacity, and selected THP/zswap policy. Missing or internally
inconsistent execution evidence leaves a cohort observational. Storage metrics
additionally require the same filesystem, bounded
mount semantics, guest-visible block policy, and exact executor version. A UTC
calendar day is one measurement window. Each metric
cohort exposes sample, target, window, and Run ID sets plus median, P10, P90,
actual minimum/maximum, direction-aware best/worst, and P10-P90 relative
spread.

Statistics are `comparable` only with at least nine samples from three targets
and three UTC-day windows and a verified provider identity. Smaller cohorts
remain `observational`. The endpoint never merges incompatible profiles and
never returns a provider ranking; `rating_status` remains `not-rated`. Paired
network, database, and web runs with undeclared or contradictory topology remain
observational. Operator-declared and independently derived topology remain
separate metric contracts.
PostgreSQL, Redis, and MySQL/MariaDB expose separate descriptive metric keys.
MySQL and MariaDB implementations or different server versions are never
silently merged, even when the CloudMark profile name matches.

## Export provider observations

```http
GET /api/v1/provider-comparisons.csv
```

The read-only `provider-observation-export-v1` response contains one row per
exact metric cohort. It retains the projection/export versions, generation
time, provider/SKU/region/OS identity, every compatibility contract, descriptive
statistics, status/reasons, UTC windows, target IDs, and source Run IDs. The
response is UTF-8 CSV with a fixed filename and remains bounded to 50,000 rows
and 16 MiB.

Text cells that could be interpreted as spreadsheet formulas are prefixed with
an apostrophe. The export preserves `not-rated` and includes no provider score,
rank, or winner field. An empty observation set returns the stable header row.

## Record cost context

```http
POST /api/v1/cost-observations
X-CloudMark-Token: <controller token>
Content-Type: application/json

{
  "target_id": "controller",
  "amount": "0.125",
  "currency": "USD",
  "billing_unit": "hour",
  "commitment": "on-demand",
  "tax_included": false,
  "source_type": "provider-public-url",
  "source_reference": "https://provider.example/pricing"
}
```

`amount` must be an exact positive decimal string; JSON floating-point numbers
are rejected. An optional timezone-aware `observed_at` is normalized to UTC.
When omitted, Controller receipt time is used and the timestamp source remains
machine-readable. The selected target must already expose provider and SKU
identity. Public URLs must use HTTPS and contain no credentials or fragment.

The response is an immutable `cost-observation-v1` claim with target-identity
provenance and `operator-declared-unverified` status. CloudMark does not fetch
the source, store a document, infer billing terms, normalize units, or calculate
price/performance. `provider_rating_input` remains false.

```http
GET /api/v1/cost-observations
```

This returns the bounded recent collection. Do not submit credentials, signed
URLs, customer/account identifiers, or unredacted invoice content. See
[`COST_OBSERVATION_METHODOLOGY.md`](COST_OBSERVATION_METHODOLOGY.md).

```http
GET /api/v1/cost-observations.csv
```

The read-only `cost-observation-export-v1` response retains each observation's
exact decimal string, target/source/timestamp provenance, claim status, and
machine-readable non-scoring policy. Rows are deterministic, spreadsheet
formula prefixes are neutralized, and output is capped at 1,000 rows and 4 MiB.
No unit/currency normalization, price/performance value, score, or ranking is
added.

The complete machine-readable contract is in
[`openapi/cloudmark-v1.yaml`](../openapi/cloudmark-v1.yaml).
