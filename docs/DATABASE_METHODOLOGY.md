# PostgreSQL database methodology

CloudMark database methodology `database-postgresql-v1` measures a real
PostgreSQL service from a separate pgbench generator. The Controller coordinates
the run and stores evidence; it does not carry database traffic.

## Topology

- Target Agent: creates and hosts an isolated PostgreSQL cluster under its
  CloudMark workspace.
- Generator Agent: runs exact built-in pgbench workloads against the Target.
- Controller: validates admission, schedules tasks, reports progress, and stores
  results.

Both Agents must advertise peer-reachable unicast addresses. The Target must
report `postgres`, `initdb`, `pg_isready`, and `pgbench`; the Generator must
report `pgbench`. PostgreSQL listens on the Target address and loopback using
port `55432`. Host access is restricted to the paired Generator address.

## Profiles

| Profile | Scale | Workloads | Duration |
|---|---:|---|---|
| `postgres-peer-quick` | 10 | select-only at 1 and 4 clients; TPC-B-like at 4 clients | 15–30 seconds |
| `postgres-peer-standard` | 50 | select-only and TPC-B-like at 1/4/16 clients; four-client connection churn | 30–60 seconds |

Every measured job has a short warm-up. Clients and worker threads are stored
with each result. The standard profile separates read-only scaling, durable
read/write transaction scaling, and per-transaction connection overhead.

## Fixed server contract

The executor initializes a fresh pgbench dataset and starts PostgreSQL with:

- `fsync=on`;
- `full_page_writes=on`;
- `synchronous_commit=on`;
- `shared_buffers=128MB`; and
- an allow-listed `max_connections` derived from the profile.

Unix-domain sockets are disabled for the ephemeral service. All benchmark
connections use the allow-listed TCP endpoint so packaged PostgreSQL defaults
cannot redirect the service to a privileged socket directory.

These settings prioritize comparability and durability over provider-specific
tuning. Results describe this CloudMark configuration, not the maximum possible
performance of a manually optimized production database.

## Metrics and evidence status

CloudMark preserves transactions per second, transactions processed, failed
transactions, average transaction latency, initial connection time, one-second
TPS/latency progress, tool versions, profile and methodology versions, Agent
identity, dataset scale, durability settings, and raw pgbench output.

`database-postgresql-v1` does not claim transaction-level P95/P99 latency because
standard pgbench summary output does not provide those percentiles. The field is
marked unavailable rather than estimated. Replication, backup/restore, failover,
MySQL/MariaDB, Redis, cache persistence, and managed-service control-plane
behavior remain outside this methodology, so the database domain stays
`Partial`.

## Safety and cleanup

- only built-in `select-only` and `tpcb-like` scripts are accepted;
- arbitrary SQL, database names, usernames, paths, ports, scale factors, client
  counts, thread counts, and durations cannot be supplied by an API caller;
- the scale factor is capped at 100 and measured durations at 60 seconds;
- the Agent preserves at least 1 GiB or 5% of the filesystem, whichever is
  larger;
- the cluster path is generated beneath the Agent workspace;
- only the paired Generator address receives host access; and
- PostgreSQL is stopped and the complete temporary cluster is removed after
  success, failure, timeout, or cancellation. The Target watchdog initiates
  cleanup after more than 20 seconds without successful Controller contact.

The operator must still verify that port `55432/TCP` is reachable only between
the paired provider machines and must run the Agent as a non-root account,
because PostgreSQL `initdb` refuses root execution.

## Comparison contract

Compare only results with the same profile version, methodology version,
PostgreSQL and pgbench major versions, instance topology, operating system,
placement scope, and controlled background-load conditions. One pair and one
time window cannot establish provider-wide database quality.
