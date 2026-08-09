from __future__ import annotations

import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .profiles import COMPUTE_PROFILES, MEMORY_PROFILES
from .runner import JobContext, RunStopped


NATIVE_MEMORY_VERSION = "1.0"


class ComputeError(RuntimeError):
    pass


def _command_version(command: list[str]) -> str:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    lines = (completed.stdout or completed.stderr).strip().splitlines()
    return lines[0][:160] if lines else "unknown"


def _sysbench_version(executable: str) -> str:
    value = _command_version([executable, "--version"])
    match = re.search(r"sysbench\s+(\d+)\.(\d+)", value, re.IGNORECASE)
    if match and int(match.group(1)) < 1:
        raise ComputeError("CloudMark requires sysbench 1.0 or newer for versioned compute profiles.")
    return value


def _memory_available_bytes() -> int | None:
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for line in meminfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    try:
        return int(os.sysconf("SC_AVPHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _compile_memory_tool(workspace: Path) -> dict[str, str]:
    compiler = shutil.which("gcc")
    if not compiler:
        raise ComputeError("gcc is not installed. Run CloudMark bootstrap with the memory pack.")
    source = Path(__file__).with_name("native") / "memory_bench.c"
    if not source.is_file():
        raise ComputeError("Packaged native memory benchmark source is missing.")
    tool_dir = workspace / "native-tools"
    tool_dir.mkdir(parents=True, exist_ok=True)
    binary = tool_dir / ("cloudmark-memory-bench.exe" if os.name == "nt" else "cloudmark-memory-bench")
    temporary = binary.with_suffix(binary.suffix + ".building")
    command = [compiler, "-O3", "-std=c11", "-fopenmp", str(source), "-lm", "-o", str(temporary)]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ComputeError(f"Unable to compile the native memory benchmark: {exc}") from exc
    if completed.returncode != 0 or not temporary.is_file():
        temporary.unlink(missing_ok=True)
        detail = (completed.stderr or completed.stdout).strip()
        raise ComputeError(f"Native memory benchmark compilation failed: {detail[:1200]}")
    temporary.replace(binary)
    return {
        "binary": str(binary.resolve()),
        "compiler": compiler,
        "compiler_version": _command_version([compiler, "--version"]),
    }


def system_preflight(suite: str, profile_name: str, workspace: Path) -> dict[str, Any]:
    if suite == "compute":
        profiles = COMPUTE_PROFILES
    elif suite == "memory":
        profiles = MEMORY_PROFILES
    else:
        raise ComputeError(f"Unsupported system benchmark suite: {suite}")
    if profile_name not in profiles:
        raise ComputeError(f"Unknown {suite} profile: {profile_name}")
    profile = profiles[profile_name]
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    logical_cores = max(1, os.cpu_count() or 1)
    estimated_seconds = sum(int(job["runtime"]) + int(job.get("warmup", 0)) for job in profile["jobs"])
    result: dict[str, Any] = {
        "suite": suite,
        "profile": profile_name,
        "profile_version": profile["profile_version"],
        "methodology_version": profile["methodology_version"],
        "workspace": str(workspace),
        "logical_cores": logical_cores,
        "job_count": len(profile["jobs"]),
        "estimated_seconds": estimated_seconds,
        "default_timeout_seconds": max(180, estimated_seconds + 180),
        "requires_admin": False,
        "writes_benchmark_data": False,
    }
    if suite == "compute":
        executable = shutil.which("sysbench")
        if not executable:
            raise ComputeError("sysbench is not installed. Run CloudMark bootstrap with the compute pack.")
        result.update({"tool": executable, "tool_name": "sysbench", "tool_version": _sysbench_version(executable)})
    else:
        if not sys.platform.startswith("linux"):
            raise ComputeError("The native memory benchmark currently supports Linux with GCC and OpenMP.")
        array_bytes = int(profile["array_size_mib"]) * 1024 * 1024
        allocated_bytes = array_bytes * 3
        available = _memory_available_bytes()
        reserve = 512 * 1024 * 1024
        if available is not None and available - allocated_bytes < reserve:
            raise ComputeError(
                f"Not enough available memory. Benchmark allocation: {allocated_bytes} bytes; safety reserve: {reserve} bytes."
            )
        native = _compile_memory_tool(workspace)
        result.update(
            {
                "tool": native["binary"],
                "tool_name": "cloudmark-memory-bench",
                "tool_version": NATIVE_MEMORY_VERSION,
                "compiler": native["compiler"],
                "compiler_version": native["compiler_version"],
                "array_bytes": array_bytes,
                "allocated_bytes": allocated_bytes,
                "available_memory_bytes": available,
                "safety_reserve_bytes": reserve,
            }
        )
    return result


def _resolve_threads(value: Any, logical_cores: int) -> int:
    if value == "all":
        return logical_cores
    if value == "half":
        return max(1, math.ceil(logical_cores / 2))
    threads = int(value)
    if not 1 <= threads <= logical_cores:
        raise ComputeError(f"Profile requested {threads} threads on a {logical_cores}-thread system.")
    return threads


def _telemetry_snapshot() -> dict[str, Any]:
    snapshot: dict[str, Any] = {"captured_at": datetime.now(timezone.utc).isoformat()}
    stat = Path("/proc/stat")
    if stat.exists():
        fields = stat.read_text(encoding="utf-8", errors="replace").splitlines()[0].split()
        names = ["user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal", "guest", "guest_nice"]
        try:
            snapshot["cpu_ticks"] = {name: int(value) for name, value in zip(names, fields[1:])}
        except ValueError:
            pass
    loadavg = Path("/proc/loadavg")
    if loadavg.exists():
        values = loadavg.read_text(encoding="utf-8", errors="replace").split()
        if len(values) >= 3:
            try:
                snapshot["load_average"] = [float(values[0]), float(values[1]), float(values[2])]
            except ValueError:
                pass
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        mhz: list[float] = []
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("cpu mhz"):
                try:
                    mhz.append(float(line.split(":", 1)[1].strip()))
                except (IndexError, ValueError):
                    pass
        if mhz:
            snapshot["frequency_mhz"] = {"minimum": min(mhz), "average": sum(mhz) / len(mhz), "maximum": max(mhz)}
    return snapshot


def _telemetry_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"before": before, "after": after}
    first = before.get("cpu_ticks")
    second = after.get("cpu_ticks")
    if isinstance(first, dict) and isinstance(second, dict):
        names = ["user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal"]
        deltas = {name: max(0, int(second.get(name, 0)) - int(first.get(name, 0))) for name in names}
        total = sum(deltas.values())
        if total:
            idle = deltas["idle"] + deltas["iowait"]
            result["utilization_percent"] = round((total - idle) * 100 / total, 3)
            result["steal_percent"] = round(deltas["steal"] * 100 / total, 3)
    return result


def _latencies(output: str) -> dict[str, float | None]:
    values: dict[str, float | None] = {}
    labels = {"minimum_ms": "min", "average_ms": "avg", "maximum_ms": "max", "p95_ms": "95th percentile"}
    for key, label in labels.items():
        match = re.search(rf"^\s*{re.escape(label)}:\s*([0-9.]+)", output, re.MULTILINE | re.IGNORECASE)
        values[key] = float(match.group(1)) if match else None
    return values


def _sysbench_series(output: str) -> list[dict[str, float]]:
    points: list[dict[str, float]] = []
    pattern = re.compile(
        r"\[\s*([0-9.]+)s\s*\].*?eps:\s*([0-9.]+).*?lat\s*\(ms,95%\):\s*([0-9.]+)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(output):
        points.append({"elapsed_seconds": float(match.group(1)), "events_per_second": float(match.group(2)), "p95_ms": float(match.group(3))})
    return points


def _stability(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "minimum": None, "maximum": None, "cv_percent": None}
    mean = statistics.fmean(values)
    return {
        "mean": round(mean, 3),
        "minimum": round(min(values), 3),
        "maximum": round(max(values), 3),
        "cv_percent": round(statistics.pstdev(values) * 100 / mean, 3) if mean else None,
    }


def parse_sysbench_cpu(output: str) -> dict[str, Any]:
    event_rate = re.search(r"events per second:\s*([0-9.]+)", output, re.IGNORECASE)
    total_events = re.search(r"total number of events:\s*([0-9]+)", output, re.IGNORECASE)
    elapsed = re.search(r"total time:\s*([0-9.]+)s", output, re.IGNORECASE)
    if not total_events or not elapsed:
        raise ComputeError("sysbench CPU output is missing total events or elapsed time.")
    elapsed_seconds = float(elapsed.group(1))
    events = int(total_events.group(1))
    rate = float(event_rate.group(1)) if event_rate else events / elapsed_seconds
    series = _sysbench_series(output)
    return {
        "events": events,
        "events_per_second": round(rate, 3),
        "elapsed_seconds": elapsed_seconds,
        "latency": _latencies(output),
        "time_series": series,
        "stability": _stability([point["events_per_second"] for point in series]),
    }


def _partial_result(
    suite: str,
    profile_name: str,
    profile: dict[str, Any],
    preflight: dict[str, Any],
    jobs: list[dict[str, Any]],
    started: float,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "suite": suite,
        "profile": profile_name,
        "profile_version": profile["profile_version"],
        "methodology_version": profile["methodology_version"],
        "tool": {
            "name": preflight["tool_name"],
            "version": preflight["tool_version"],
            "compiler_version": preflight.get("compiler_version"),
        },
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "preflight": preflight,
        f"{suite}_jobs": list(jobs),
        "validity": {
            "same_profile_required": True,
            "same_tool_version_required": True,
            "cross_architecture_comparable": False,
            "background_load_must_be_controlled": True,
        },
    }
    if suite == "compute":
        single = next((job for job in jobs if job["threads"] == 1 and "sustained" not in job["name"]), None)
        multi = next((job for job in jobs if job["threads"] == preflight["logical_cores"] and "sustained" not in job["name"]), None)
        if single and multi:
            ideal = float(single["metrics"]["events_per_second"]) * int(multi["threads"])
            result["scaling"] = {
                "single_events_per_second": single["metrics"]["events_per_second"],
                "all_core_events_per_second": multi["metrics"]["events_per_second"],
                "all_core_threads": multi["threads"],
                "efficiency_percent": round(float(multi["metrics"]["events_per_second"]) * 100 / ideal, 3) if ideal else None,
            }
    return result


def run_system_benchmark(
    suite: str,
    profile_name: str,
    workspace: Path,
    run_id: str,
    *,
    context: JobContext | None = None,
) -> dict[str, Any]:
    profiles = COMPUTE_PROFILES if suite == "compute" else MEMORY_PROFILES if suite == "memory" else None
    if profiles is None:
        raise ComputeError(f"Unsupported system benchmark suite: {suite}")
    preflight = system_preflight(suite, profile_name, workspace)
    profile = profiles[profile_name]
    context = context or JobContext(
        run_id,
        total_steps=len(profile["jobs"]),
        timeout_seconds=preflight["default_timeout_seconds"],
    )
    results: list[dict[str, Any]] = []
    started = time.monotonic()
    pending_error: BaseException | None = None
    try:
        for index, workload in enumerate(profile["jobs"]):
            name = str(workload["name"])
            threads = _resolve_threads(workload["threads"], int(preflight["logical_cores"]))
            partial = _partial_result(suite, profile_name, profile, preflight, results, started)
            warmup_result = None
            if suite == "compute":
                warmup_seconds = int(workload.get("warmup", 0))
                if warmup_seconds:
                    context.report("warming-up", name, partial_result=partial)
                    warmup_command = [
                        str(preflight["tool"]),
                        "cpu",
                        f"--threads={threads}",
                        f"--time={warmup_seconds}",
                        "--events=0",
                        f"--cpu-max-prime={int(workload['cpu_max_prime'])}",
                        "run",
                    ]
                    warmup_result = context.run_process(warmup_command, label=f"compute warm-up {name}")
                    if warmup_result.returncode != 0:
                        raise ComputeError(f"compute warm-up {name} failed: {warmup_result.stderr.strip()[:1200]}")
                context.report("benchmarking", name, partial_result=partial)
                command = [
                    str(preflight["tool"]),
                    "cpu",
                    f"--threads={threads}",
                    f"--time={int(workload['runtime'])}",
                    "--events=0",
                    "--percentile=95",
                    "--report-interval=1",
                    f"--cpu-max-prime={int(workload['cpu_max_prime'])}",
                    "run",
                ]
            else:
                command = [
                    str(preflight["tool"]),
                    "--kernel",
                    str(workload["kernel"]),
                    "--bytes",
                    str(preflight["array_bytes"]),
                    "--seconds",
                    str(int(workload["runtime"])),
                    "--threads",
                    str(threads),
                ]
            before = _telemetry_snapshot()
            completed = context.run_process(command, label=f"{suite} job {name}")
            after = _telemetry_snapshot()
            if completed.returncode != 0:
                raise ComputeError(f"{suite} job {name} failed: {completed.stderr.strip()[:1200]}")
            try:
                metrics = parse_sysbench_cpu(completed.stdout) if suite == "compute" else json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                raise ComputeError(f"Native memory job {name} returned invalid JSON output.") from exc
            if not isinstance(metrics, dict):
                raise ComputeError(f"{suite} job {name} returned an unexpected result shape.")
            results.append(
                {
                    "name": name,
                    "workload": workload,
                    "threads": threads,
                    "metrics": metrics,
                    "host": _telemetry_delta(before, after),
                    "raw": {
                        "command": list(completed.args),
                        "stdout": completed.stdout,
                        "stderr": completed.stderr,
                        "process_elapsed_seconds": completed.elapsed_seconds,
                        "warmup": (
                            {
                                "command": list(warmup_result.args),
                                "stderr": warmup_result.stderr,
                                "process_elapsed_seconds": warmup_result.elapsed_seconds,
                            }
                            if warmup_result is not None
                            else None
                        ),
                    },
                }
            )
            next_job = str(profile["jobs"][index + 1]["name"]) if index + 1 < len(profile["jobs"]) else None
            context.complete_step(
                "benchmarking" if next_job else "completed",
                next_job,
                partial_result=_partial_result(suite, profile_name, profile, preflight, results, started),
            )
    except BaseException as exc:
        pending_error = exc
    result = _partial_result(suite, profile_name, profile, preflight, results, started)
    if pending_error is not None:
        if isinstance(pending_error, RunStopped):
            pending_error.partial_result = result
        raise pending_error
    return result
