# Compute and memory methodology

CloudMark version `0.4.0` introduces production-oriented subsets of CPU and
memory qualification. These results are raw infrastructure evidence, not a
complete compute score. Both domains remain `Partial` until the missing workload
families and topology checks described below are implemented.

## Measurement contract

Only compare results when all of the following match:

- CloudMark profile name and profile version;
- methodology version (`compute-v1` or `memory-v1`);
- measurement tool and tool version;
- operating-system and power-policy context;
- CPU architecture and compatible instruction-set expectations;
- controlled background load and equivalent VM placement policy.

CloudMark sets `cross_architecture_comparable` to `false`. An x86 result and an
Arm result may both be useful, but their event rates are not a claim of equal
work performed across architectures.

Preflight resolves profile thread values against the smaller of the host logical
CPU count and the current process CPU-affinity set when that API is available.
It retains host, affinity, and effective counts without CPU IDs. This prevents
`all` from escaping a container/service cpuset. Preflight also reads cgroup v2
`cpu.max` or v1 CFS quota/period across up to 32 ancestors. It retains the
smallest quota capacity as a fractional-core value and uses its ceiling as the
thread cap. A malformed governing quota fails before load. Quota capacity is a
CPU-time entitlement, not a physical-core count.

## CPU executor

The CPU executor uses the allow-listed `sysbench cpu` integer prime workload.
It records total events, events per second, elapsed time, latency summary,
one-second event-rate samples, coefficient of variation, thread count, and host
telemetry before and after each job. Linux telemetry includes CPU utilization,
steal time, load average, and observed frequency when exposed through `/proc`.

| Profile | Jobs | Runtime excluding warm-up |
|---|---|---:|
| `compute-quick` | single core, all cores, all-core sustained | 95 seconds |
| `compute-standard` | single core, half cores, all cores, five-minute sustained | 435 seconds |

Every CPU job uses `--cpu-max-prime=20000`, a one-second report interval, and a
95th-percentile latency report. The result also calculates all-core scaling
efficiency relative to the single-thread event rate. Scaling efficiency is
diagnostic evidence; it is not a universal CPU quality percentage.

Warm-up is executed as a separate bounded `sysbench cpu` invocation with the
same thread count and prime limit. This keeps the measured one-second series
free of warm-up samples and remains compatible with distribution builds such
as sysbench 1.0.20 that do not implement `--warmup-time`. The warm-up command,
elapsed time, and stderr are retained with each job as execution evidence.

This initial executor does not yet claim floating-point, vector/SIMD, crypto,
compression, compilation, language-runtime, or application-level performance.

## Memory executor

The memory executor builds the packaged `cloudmark-memory-bench` C source with
GCC using optimization and OpenMP. It operates on three independently allocated
arrays to reduce cache-only measurement and runs four explicit kernels:

- `read`: sequentially reads one array;
- `write`: sequentially writes one array;
- `copy`: reads one array and writes a second;
- `triad`: reads two arrays and writes a third.

| Profile | Per-array size | Total allocation | Jobs |
|---|---:|---:|---|
| `memory-quick` | 128 MiB | 384 MiB | single-thread read/copy; all-core read/copy/triad |
| `memory-standard` | 256 MiB | 768 MiB | single-thread and all-core read/write/copy/triad |

The preflight compiler check occurs before load starts. CloudMark refuses the
run when the fixed allocation would leave less than 512 MiB of available
memory. Results include processed bytes, elapsed time, bandwidth, thread count,
kernel, checksum, native tool version, and compiler version.

Inside a Linux cgroup, preflight reads the current process's bounded cgroup
membership and the standard memory controller files. For cgroup v2 it uses
`memory.max - memory.current`; for v1 it uses
`memory.limit_in_bytes - memory.usage_in_bytes`. The effective available amount
is the smaller verified value across the current cgroup, up to 32 ancestors,
and host MemAvailable. This catches a child configured as unlimited beneath a
finite parent.
The 512 MiB reserve is applied to that effective amount. An unlimited cgroup
falls back to host availability; a finite or malformed governing boundary with
unreadable usage fails before native-tool compilation rather than risking a
cgroup OOM kill.

`memory-environment-v2` is collected through bounded read-only Linux procfs/sysfs
before a memory run and in system/Agent inventory. It retains at most 64 online
guest NUMA nodes, CPU lists and counts, node MemTotal/MemFree, Linux relative
distance values, and the guest page size. Each source file is capped at 4 KiB;
CPU indexes are limited to 0-8191 and node indexes to 0-1023. Malformed,
oversized, hidden, or unsupported topology becomes `Partial` or `Unavailable`
evidence and does not block the bandwidth executor.

This topology is guest-visible configuration evidence. Linux distance values
are relative topology weights, not measured nanoseconds. CloudMark does not
infer physical-host placement, memory-controller ownership, or remote-node
performance, and it does not convert a single exposed node into proof that the
physical machine is UMA.

Version 2 also retains a point-in-time paging snapshot: SwapTotal/SwapFree,
anonymous THP bytes, HugeTLB page counts/size, zswap pool/original bytes, the
selected top-level THP enabled/defrag policies, and whether zswap is enabled.
`/proc/meminfo` is capped at 64 KiB and each policy file at 4 KiB. This is
configuration and instantaneous usage context only; `pressure_measured` remains
false and CloudMark does not infer swap latency, reclaim pressure, THP benefit,
or zswap performance.

This is a CloudMark-specific userspace bandwidth workload, not an official
STREAM result. It does not yet measure loaded latency, NUMA locality penalties,
huge-page performance, memory-error correction, or swap pressure over time.

## Operating procedure

1. Use a clean or idle assessment system and record its instance SKU.
2. Set a stable provider power/performance policy when that control is exposed.
3. Install the `compute` and `memory` packs.
4. Run the quick profiles to validate the environment.
5. Run standard profiles at least three times in separate time windows.
6. Preserve every raw result; summarize median, P10/P90, worst observation, and
   sample count only after repeated measurements exist.
7. Do not run CPU, memory, or storage profiles concurrently unless the explicit
   objective is contention testing. Version `0.5.0` enforces this separately on
   the Controller host and on every selected Agent.

## Safety and platform support

These tests intentionally consume CPU or memory bandwidth and can affect
co-located workloads. Administrator privileges are not required for execution.
The native memory executor currently supports Linux environments with GCC and
OpenMP. Windows remains supported for the Controller and inventory, but these
CPU/memory automation paths are not presented as full Windows qualification.
