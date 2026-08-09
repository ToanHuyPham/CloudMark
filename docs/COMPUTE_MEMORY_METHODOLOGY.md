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

This is a CloudMark-specific userspace bandwidth workload, not an official
STREAM result. It does not yet measure loaded latency, NUMA locality penalties,
page size behavior, memory-error correction, or swap pressure.

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
