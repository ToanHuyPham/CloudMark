# Redis methodology

`database-redis-v1` runs an authenticated ephemeral Redis service on the Target
and fixed `redis-benchmark` GET/SET jobs on the paired Generator. Quick and
Standard cover 64-byte and 1 KiB values, 1/16/64 clients, and pipeline depths
1/16 with at most 50,000 requests per job. Standard retains request rate,
average/minimum/P50/P95/P99/maximum latency and Generator process CPU.

Redis binds only the Target address on TCP 56379. A per-Run password exists only
in Controller memory and the two authenticated task claim responses; it never
enters SQLite or evidence. AOF persistence is enabled with `appendfsync everysec`.
The Target watchdog terminates Redis and removes its configuration, password,
AOF/RDB files, and logs after success, failure, cancellation, timeout, or lost
Controller contact. Comparison requires AOF evidence, Generator CPU below 90%
of one logical core, and verified cleanup. This does not measure replication,
cluster mode, eviction, failover, or managed Redis control planes.
