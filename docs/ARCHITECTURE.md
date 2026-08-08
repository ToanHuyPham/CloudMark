# CloudMark architecture

CloudMark separates measurement, evidence, and scoring so that a new scoring
model never destroys or rewrites the original benchmark result.

## Components

1. **Controller API** runs on the operator machine, binds to loopback by
   default, stores runs in SQLite, and issues short-lived pairing sessions.
2. **Agent CLI** runs on clean Linux or Windows machines, collects inventory,
   installs approved tools, and executes versioned profiles.
3. **Dashboard** is a local React/vinext application. Read endpoints are public
   on loopback; writes require the controller token.
4. **Benchmark runners** invoke allow-listed binaries with exact argument lists.
   They never accept arbitrary shell fragments.
5. **Profiles** define workload size, duration, safety limits, and required
   agent topology.

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

## Persistence

SQLite tables:

- `runs`: immutable request and result payloads with lifecycle state;
- `sessions`: short-lived multi-node labs;
- `agents`: participants and their inventory evidence.

WAL mode permits dashboard reads while a benchmark updates its job state.
Indexes exist only for current query patterns: run status/start time and agents
by session.

## Trust boundaries

- Controller write operations require `X-CloudMark-Token`.
- Join tokens are short-lived session secrets stored as SHA-256 hashes; one
  token can enroll the small set of agents participating in that session.
- Remote joins require HTTPS unless the operator explicitly enables HTTP inside
  a trusted private network.
- Provider metadata probes use fixed link-local endpoints, ignore proxies, have
  short timeouts, and never retrieve user-data or credentials.

## Versioning

API paths use `/api/v1`. Every future benchmark result will carry the CloudMark,
profile, tool, and methodology versions required to reproduce it.
