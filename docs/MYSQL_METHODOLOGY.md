# MySQL/MariaDB methodology

`database-mysql-v1` measures an isolated MySQL-compatible InnoDB service on a
Target Agent with fixed Sysbench OLTP workloads on a paired Generator Agent.
The Controller coordinates tasks and stores evidence; it never carries SQL or
benchmark traffic.

## Profiles

`mysql-peer-quick` creates four tables with 10,000 rows each, then runs a
single-thread read-only job and a four-thread durable read/write job.

`mysql-peer-standard` creates eight tables with 50,000 rows each, then measures
fixed point-select, read-only, write-only, and read/write shapes at one, four,
or sixteen threads. Every timed job has a fixed warm-up, a maximum 60-second
measurement interval, one-second progress reports, and a fixed P99 percentile
contract. The profile never accepts an operator-supplied Lua script, query,
table count, table size, connection count, duration, address, or port.

## Target service contract

The Target Agent must run as a non-root Linux account. It initializes a fresh
data directory below `mysql-services/task_*` using `mariadb-install-db`,
`mysql_install_db`, or a server that explicitly supports
`--initialize-insecure`. Initialization and service startup use `--no-defaults`
so host configuration cannot silently change the workload.

The service first starts with networking disabled to create the fixed
`cloudmark` database and a `cloudmark` account restricted to the exact paired
Generator address. It then restarts on the exact Target address and TCP 57306.
A random per-Run password is delivered through the memory-only task-secret
channel. CloudMark does not persist it in SQLite, evidence, dashboard data,
runtime snapshots, or Git.

InnoDB uses `innodb_flush_log_at_trx_commit=1` and doublewrite protection.
Binary logging is disabled, so the result is a durable single-node OLTP
baseline and not replication evidence. The server implementation and version
are retained so MySQL and MariaDB results remain identifiable.

## Measurements and validity

CloudMark parses total transactions, TPS, queries, QPS, ignored errors,
reconnects, elapsed time, minimum/average/P99/maximum latency, and bounded
one-second progress evidence. The Generator records Linux process and host CPU
intervals. Comparison eligibility requires:

- complete Sysbench dataset preparation;
- observed Generator CPU for every timed job with peak Sysbench process CPU
  below 90% of one logical core;
- observed flush-at-commit and InnoDB doublewrite settings;
- successful Sysbench table cleanup; and
- verified Target process and workspace cleanup.

A failed, cancelled, timed-out, or cleanup-unverified Run remains partial
evidence. Missing evidence is unavailable, never zero.

## Safety and limitations

Free space must cover the fixed estimated dataset plus the larger of 1 GiB or
5% of the filesystem. The Target watchdog stops the server after its deadline
or lost Controller contact. On every handled terminal path, CloudMark attempts
Generator table cleanup first, then stops the Target process and removes the
complete generated data directory and log.

Version 1 does not measure checkpoint isolation, binary-log overhead,
replication, failover, PITR, managed-service control planes, encryption at
rest, TLS transport, MySQL Router/ProxySQL, or cross-zone recovery. MySQL and
MariaDB implementation differences are retained as evidence and must not be
silently interpreted as one identical database product.
