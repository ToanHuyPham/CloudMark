# PostgreSQL database methodology

CloudMark Standard methodology `database-postgresql-v2` measures a real
PostgreSQL service from a separate pgbench Generator, adds exact fixed-count
transaction tail latency, and validates Generator process CPU. Quick remains on
the readable `database-postgresql-v1` contract. The Controller coordinates the
run and stores evidence; it does not carry database traffic.

## Topology

- Target Agent: creates and hosts an isolated PostgreSQL cluster under its
  CloudMark workspace.
- Generator Agent: runs exact built-in pgbench workloads against the Target.
- Controller: validates admission, schedules tasks, reports progress, and stores
  results.

Both Agents must advertise peer-reachable unicast addresses. The Target must
report `postgres`, `initdb`, `pg_isready`, and `pgbench`; the Generator must
report `pgbench`. Standard v2 also requires pgbench transaction logging and
Linux procfs process accounting on the Generator. PostgreSQL listens on the Target address and loopback using
port `55432`. Host access is restricted to the paired Generator address.

## Profiles

| Profile | Scale | Workloads | Duration |
|---|---:|---|---|
| `postgres-peer-quick` | 10 | select-only at 1 and 4 clients; TPC-B-like at 4 clients | 15–30 seconds |
| `postgres-peer-standard` | 50 | select-only and TPC-B-like at 1/4/16 clients; four-client connection churn; one four-client fixed-count tail job | 30–60 seconds plus 1,000 transactions/client |
| `postgres-peer-recovery` | 20 | four-client durable workload, logical backup, restore, and row-count verification | 30-second load plus bounded 300-second backup/restore stages |

Every measured job has a short warm-up. Clients and worker threads are stored
with each result. The standard profile separates read-only scaling, durable
read/write transaction scaling, and per-transaction connection overhead.
Standard v2 adds one TPC-B-like job with four clients and exactly 1,000
transactions per client. This produces at most 4,000 transaction log rows and
does not mix a time-limited throughput window with the tail-latency contract.

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

## Logical backup and restore profile

`database-postgresql-recovery-v1` runs after its fixed durable workload has
completed and no Generator load remains active. On the Target, CloudMark uses
fixed loopback-only commands to:

1. record row counts for `pgbench_accounts`, `pgbench_branches`,
   `pgbench_tellers`, and `pgbench_history`;
2. create an uncompressed custom-format `pg_dump` artifact below the active
   service workspace;
3. create the fixed `cloudmark_restore` database from `template0`;
4. restore with `pg_restore --exit-on-error --no-owner --no-privileges`;
5. record the same four restored row counts; and
6. drop the restored database and remove the backup artifact.

The result retains backup size, backup/restore durations, source/restored row
counts, expected scale-shape validation, tool versions, and cleanup evidence.
The backup artifact is bounded to twice the estimated dataset size, and the
Target must retain the normal 1 GiB or 5% filesystem reserve plus space for the
backup and restored database.

This profile proves only a logical backup/restore path inside one ephemeral
Target. Row-count equality is not a cryptographic data checksum. It does not
measure provider snapshots, object storage, cross-zone transfer, replica
promotion, point-in-time recovery, RPO, RTO, or managed-database operations.

## Metrics and evidence status

CloudMark preserves transactions per second, transactions processed, failed
transactions, average transaction latency, initial connection time, one-second
TPS/latency progress, tool versions, profile and methodology versions, Agent
identity, dataset scale, durability settings, and raw pgbench output.

For the Standard v2 tail job, pgbench logs every transaction under a generated
Generator workspace. CloudMark parses the transaction duration field directly,
uses nearest-rank P50/P95/P99/P99.9 and maximum, and requires the parsed row
count to exactly match the fixed 4,000-transaction contract. It reads at most
8 MiB and 20,000 rows, reports truncation or malformed rows as partial, and
removes the log directory before accepting the evidence. These percentiles are
not inferred from one-second averages.

Standard v2 also records one-second Linux host utilization, steal time, and
pgbench process CPU summaries for every timed workload. Missing samples or a
pgbench peak at or above 90% of one logical CPU makes the Run
comparison-ineligible. Physical/PITR backup, replication, failover,
MySQL/MariaDB, Redis, cache persistence, and managed-service control-plane
behavior remain outside this methodology, so the database domain stays
`Partial`.

## Safety and cleanup

- only built-in `select-only` and `tpcb-like` scripts are accepted;
- arbitrary SQL, database names, usernames, paths, ports, scale factors, client
  counts, thread counts, and durations cannot be supplied by an API caller;
- the scale factor is capped at 100 and measured durations at 60 seconds;
- the tail job is fixed at 1,000 transactions per client, 16,000 transactions
  as an Agent-wide hard ceiling, a 120-second task deadline, 8 MiB of log input,
  and 20,000 parsed rows;
- the Agent preserves at least 1 GiB or 5% of the filesystem, whichever is
  larger;
- the cluster path is generated beneath the Agent workspace;
- only the paired Generator address receives host access; and
- PostgreSQL is stopped and the complete temporary cluster is removed after
  success, failure, timeout, or cancellation. The Target watchdog initiates
  cleanup after more than 20 seconds without successful Controller contact.
- Generator transaction logs are created only under the Agent workspace and
  removed after success, failure, timeout, cancellation, or parser failure.

The operator must still verify that port `55432/TCP` is reachable only between
the paired provider machines and must run the Agent as a non-root account,
because PostgreSQL `initdb` refuses root execution.

## Comparison contract

Compare only results with the same profile version, methodology version,
PostgreSQL and pgbench major versions, instance topology, operating system,
placement scope, and controlled background-load conditions. One pair and one
time window cannot establish provider-wide database quality.
