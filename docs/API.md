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
| GET | `/profiles` | Benchmark and scenario profiles |
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
a peer-reachable IP and report `iperf3`. Network v6 additionally requires both
Agents to report `iproute2`, `tracepath`, `ethtool`, and Linux TCP congestion-control
evidence before load starts. `confirm_network_load` is mandatory.
Supported profiles are `network-peer-quick` (`network-v1`) and
`network-peer-standard` (`network-v6`). Quick executes directional TCP only.
Standard executes 21 bounded peer evidence steps: two pre-load and two
post-load route/interface/MTU and numeric path-trace, read-only NIC driver/offload, TCP
congestion-control, and structured interface-counter probes; idle latency;
directional TCP scaling; UDP rate sweeps derived from each direction's TCP
baseline; and one simultaneous bidirectional TCP measurement. Its result
includes byte/packet/error/drop deltas and comparison eligibility based on
stable pre/post routes, destination-reaching bounded traces, a complete
NIC/TCP-control/counter window, and Generator CPU/scaling headroom. Address
class and observed hops never prove public-Internet transit. No performance
traffic is sent to the Controller.

## Create a PostgreSQL peer run

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
`confirm_database_load` is mandatory because the run creates a temporary
dataset and generates read/write transactions. Supported profiles are
`postgres-peer-quick` and `postgres-peer-standard`. The result contains
`database_measurements`, fixed durability settings, tool versions, target and
generator identity, and cleanup evidence. Transaction traffic never traverses
the Controller.

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
online Generator with `ab`. `confirm_web_load` is mandatory because the run
creates a temporary service and generates bounded HTTP/TLS load. Supported
profiles are `web-peer-quick` and `web-peer-standard`. The result contains
`web_measurements`, request/error counts, throughput, P50/P90/P95/P99/maximum
latency, transfer evidence, TLS protocol/cipher evidence, tool versions,
target/generator identity, and cleanup status. Only the fixed Target address,
ports 58080/58443, and CloudMark endpoints are accepted; traffic never
traverses the Controller.

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

The `provider-observations-v3` response groups fresh valid evidence only when
provider, product/SKU, region, operating system, profile, methodology, metric,
unit, paired topology, and topology evidence class match. A UTC calendar day is
one measurement window. Each metric
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

The complete machine-readable contract is in
[`openapi/cloudmark-v1.yaml`](../openapi/cloudmark-v1.yaml).
