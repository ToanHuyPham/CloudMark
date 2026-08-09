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
| GET | `/dashboard` | Aggregated local dashboard payload |
| GET | `/profiles` | Benchmark and scenario profiles |
| GET | `/runs` | Run history |
| GET | `/runs/{id}` | One run and raw result |
| POST | `/runs` | Submit an asynchronous run |
| POST | `/runs/{id}/cancel` | Cancel a queued or running benchmark |
| POST | `/sessions` | Create a 30-minute pairing session |
| GET | `/sessions/{id}` | Session and joined agents |
| POST | `/sessions/{id}/join` | Join with the short-lived session token |
| POST | `/agents/{id}/heartbeat` | Refresh authenticated agent presence |
| POST | `/agents/{id}/tasks/next` | Atomically claim the next allow-listed task |
| POST | `/agents/{id}/tasks/{taskId}/result` | Complete or fail a claimed task |

## Create an inventory run

```http
POST /api/v1/runs
Content-Type: application/json
X-CloudMark-Token: ...

{"suite":"inventory","profile":"default"}
```

The response is `202 Accepted`. Poll `/runs/{id}` until the state is
`completed`, `failed`, or `cancelled`.

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

Compute, memory, and storage are mutually exclusive local saturation suites.
The API returns `400` if another one is queued or running.
They execute on the Controller host in version `0.4.0`; this endpoint does not
silently redirect a local suite to a registered remote Agent.

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
a peer-reachable IP and report `iperf3`. `confirm_network_load` is mandatory.
Supported profiles are `network-peer-quick` and `network-peer-standard`.
Version 0.4 executes TCP A→B and B→A only; it does not execute UDP or send data
to the Controller.

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

1. `POST /sessions` with the controller token.
2. Copy the returned session ID and short-lived join token to each agent.
3. Each persistent agent calls `/sessions/{id}/join`. The response includes a
   unique `agent_id` and an agent credential that is never returned again.
4. The agent uses `X-CloudMark-Agent-Token` for heartbeat, polling, and result
   submission. The Controller stores only its SHA-256 hash.
5. Read `/sessions/{id}` to verify the target and generator are online.

Agent endpoints are internal protocol endpoints for `cloudmark agent`. They do
not accept the Controller token. A task is scoped to one agent and can contain
only an executor kind implemented by the agent allow-list.

The complete machine-readable contract is in
[`openapi/cloudmark-v1.yaml`](../openapi/cloudmark-v1.yaml).
