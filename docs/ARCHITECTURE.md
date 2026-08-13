# CloudMark architecture

CloudMark separates measurement, evidence, and scoring so that a new scoring
model never destroys or rewrites the original benchmark result.

## Components

1. **Controller API** runs on the operator machine, binds to loopback by
   default, stores runs in SQLite, and issues short-lived pairing sessions.
2. **Agent CLI** runs on clean Linux or Windows machines, collects inventory,
   heartbeats, polls its authenticated queue, and executes versioned allow-listed
   network or single-system benchmark tasks. Tool bootstrap remains an explicit
   operator action.
3. **Dashboard** is a local React/vinext application. Read endpoints are public
   on loopback; writes require the controller token.
4. **Benchmark runners** invoke allow-listed binaries with exact argument lists.
   They never accept arbitrary shell fragments. The shared runner owns process
   timeout, cancellation, heartbeat, progress, and child-process termination.
5. **Profiles** define workload size, duration, safety limits, and required
   agent topology.
6. **Assessment catalog** describes all technical domains independently of
   executor availability, and records whether each domain is available,
   partial, or roadmap.
7. **Suitability engine** consumes only valid catalog evidence and maps it to
   workload-specific gates. Missing evidence remains unknown rather than zero.

## Run lifecycle

```text
queued → running → completed
                 ↘ failed
                 ↘ cancelled
```

SQLite stores phase, current job, completed/total steps, normalized progress,
heartbeat, cancellation request, and runner/methodology/tool versions. Compute,
memory, and storage persist completed jobs as partial results during execution.
A Controller restart marks unfinished runs as interrupted instead of leaving
them running forever.

Cancellation is cooperative at the runner boundary and forcefully terminates
the current allow-listed child process when necessary. Benchmark cleanup still
runs before the terminal state is stored.

## Evidence flow

```text
Inventory + versioned benchmark runs + provider/control-plane evidence
                              │
                              ▼
              17-domain assessment catalog
                              │
                              ▼
      workload gates + confidence + stability + cost context
                              │
                              ▼
               12 suitability recommendations
```

Catalog breadth and executor availability are intentionally separate. Adding a
domain to the product scope never permits it to influence a score before its
measurement and safety gates are implemented.

## Suitability evaluation

`suitability-v1` is a read-time projection over immutable completed runs. It
does not rewrite benchmark evidence or persist a synthetic aggregate. The
engine first partitions evidence by explicit target identity, rejects unknown
methodologies and cleanup-unverified results, selects the strongest fresh
compatible observation for each metric, then evaluates versioned Essential,
Standard, and Demanding hard gates.

Each check returns its threshold, operator, source Run ID, profile,
methodology, observation time, unit, quality, and freshness. Coverage and the
pass ratio among measured checks are separate fields. Missing evidence blocks a
classification rather than reducing a score. Known domain gaps cap otherwise
passing targets at `Conditional fit`.

Provider readiness is a separate projection. It counts same-product targets,
measurement windows, observed suites, and missing operational domains, but
does not produce a provider rating until the complete aggregation contract is
implemented.

`provider-observations-v3` adds a second read-time projection for repeated
measurements. Cohorts must match provider, SKU, region, operating system,
profile, methodology, metric, unit, paired topology, and topology evidence
class. It de-duplicates a paired network Run, uses UTC calendar days as
windows, and reports descriptive distributions only. Trusted Agent metadata
may independently derive a placement scope; contradictory operator
declarations fail closed to observational evidence. Globally routable peer
addresses do not by themselves prove public-Internet traversal.
The minimum comparable cohort is nine samples across three targets and three
windows. Smaller cohorts remain visible as observations; no relative provider
ranking is computed.

## Local saturation executors

CPU, memory, and storage share one exclusive Controller admission group. A
second saturation run is rejected while one is queued or active, preventing
CloudMark from invalidating its own baseline. The CPU executor calls sysbench
with exact arguments. The Linux memory executor compiles the packaged C/OpenMP
source into the configured workspace, then executes only allow-listed kernels.
Its compiler identity is part of the evidence. No profile accepts an arbitrary
binary, shell fragment, kernel name, thread count, or duration from an API
caller.

These saturation executors can run on the Controller/CLI host or an explicitly
selected Agent in version `0.5.0`. The request persists its execution mode and
Agent ID. Remote results add the Agent version, identity, session, target
inventory, and provider evidence. The Controller rejects suite/profile,
profile-version, methodology-version, or protocol-version mismatches before a
remote result becomes completed evidence.

## Remote task control

Each remote saturation run creates one durable Agent task. The Agent sends
progress and partial evidence every second while also polling cancellation.
Controller contact loss beyond 20 seconds makes the Agent cancel its child
process; a task heartbeat gap beyond 45 seconds makes the Controller fail and
close the remote task. Controller restart cancels all unfinished task records,
which causes a surviving Agent to stop on its next control poll.

Only one saturation task can target an Agent at a time. Network assessment and
single-system saturation are mutually exclusive within the same Agent session.
Different Agents may be assessed independently without introducing a global
Controller lock.

## Client/server workload services

Database and web executors use the same paired topology as provider
network assessment while retaining service-specific task allow-lists. A start
task creates an ephemeral service on the Target, bounded client tasks execute on
the Generator, and a stop task verifies cleanup. The service watchdog is owned
by the Target Agent rather than the Controller, so cleanup deadlines survive a
lost control connection.

`database-postgresql-v1` is the first implementation. It accepts only fixed
PostgreSQL settings and built-in pgbench workloads. The Controller never sends
SQL, paths, credentials, or arbitrary server configuration to an Agent.

`web-http-v1` uses the same lifecycle to create an isolated Nginx service on
the Target. The Agent generates fixed payloads and an ephemeral certificate,
binds the exact advertised address on ports 58080 and 58443, and permits only
the paired Generator address. Generator tasks accept only versioned
ApacheBench jobs over three fixed endpoints. The Controller never accepts an
arbitrary URL and is not an HTTP/TLS traffic endpoint.

## Network direction policy

The controller does not participate in provider throughput measurements.

```text
Controller ── control only ── Agent A
Controller ── control only ── Agent B
Agent A    ══ benchmark data ══ Agent B
```

`cloud_to_controller_network_test` is hard-coded `false` in the dashboard API.
The controller may coordinate sessions but cloud agents do not benchmark toward
the operator's home machine.

`network-peer-quick` preserves the `network-v1` directional TCP baseline.
`network-peer-standard` uses `network-v4`: fixed route/interface/MTU probes,
read-only NIC driver/offload and TCP congestion-control capture, bounded idle
ICMP, directional TCP scaling, capped UDP sweeps derived from each direction's
measured TCP peak, simultaneous bidirectional TCP, and Generator headroom
validation. The Agent independently validates every peer address, port,
duration, stream count, protocol, rate, ping bound, and path-probe argument
before it starts a child process. Network-v2 and network-v3 results remain
readable as legacy evidence but do not claim the v4 NIC/TCP-control contract.

## Persistence

SQLite tables:

- `runs`: immutable request and result payloads with lifecycle state;
- `sessions`: short-lived distributed assessment sessions;
- `agents`: participants and their inventory evidence.
- `agent_tasks`: durable per-agent task lifecycle, payload, result, and error.

WAL mode permits dashboard reads while a benchmark updates its job state.
Indexes exist only for current query patterns: run status/start time and agents
by session.

## Trust boundaries

- Controller write operations require `X-CloudMark-Token`.
- Join tokens are short-lived session secrets stored as SHA-256 hashes; one
  token can enroll the small set of agents participating in that session.
- Every joined worker receives a separate random credential. Only its SHA-256
  hash is persisted, and it can claim or finish tasks assigned to that agent.
- Remote joins require HTTPS unless the operator explicitly enables HTTP inside
  a trusted private network.
- Version 0.5 does not yet provide mTLS enrollment. HTTPS/VPN termination and
  access control remain operator responsibilities for remote deployments.
- Provider metadata probes use fixed link-local endpoints, ignore proxies, have
  short timeouts, and never retrieve user-data or credentials.

## Versioning

API paths use `/api/v1`. Every future benchmark result will carry the CloudMark,
profile, tool, and methodology versions required to reproduce it.
