from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .profiles import STORAGE_PROFILES
from .runner import JobContext, RunStopped


class BenchmarkError(RuntimeError):
    pass


def _percentile(section: dict[str, Any], key: str) -> float | None:
    latency = section.get("clat_ns") or section.get("lat_ns") or {}
    percentiles = latency.get("percentile") or {}
    value = percentiles.get(key)
    return float(value) / 1_000_000 if value is not None else None


def _metrics(
    job: dict[str, Any],
    workload: dict[str, Any],
    time_series: dict[str, Any] | None = None,
) -> dict[str, Any]:
    read = job.get("read", {})
    write = job.get("write", {})
    return {
        "name": workload["name"],
        "workload": workload,
        "runtime_seconds": round(float(job.get("job_runtime", 0)) / 1000, 3),
        "read": {
            "io_bytes": read.get("io_bytes", 0),
            "iops": read.get("iops", 0),
            "bandwidth_bytes_per_second": read.get("bw_bytes", 0),
            "p50_ms": _percentile(read, "50.000000"),
            "p90_ms": _percentile(read, "90.000000"),
            "p95_ms": _percentile(read, "95.000000"),
            "p99_ms": _percentile(read, "99.000000"),
            "p999_ms": _percentile(read, "99.900000"),
        },
        "write": {
            "io_bytes": write.get("io_bytes", 0),
            "iops": write.get("iops", 0),
            "bandwidth_bytes_per_second": write.get("bw_bytes", 0),
            "p50_ms": _percentile(write, "50.000000"),
            "p90_ms": _percentile(write, "90.000000"),
            "p95_ms": _percentile(write, "95.000000"),
            "p99_ms": _percentile(write, "99.000000"),
            "p999_ms": _percentile(write, "99.900000"),
        },
        "cpu": {"user_percent": job.get("usr_cpu"), "system_percent": job.get("sys_cpu")},
        "time_series": time_series or {"interval_ms": 1000, "bandwidth": [], "iops": [], "latency": []},
    }


def _fio_version(fio: str) -> str:
    try:
        completed = subprocess.run(
            [fio, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    value = (completed.stdout or completed.stderr).strip().splitlines()
    return value[0][:120] if value else "unknown"


def storage_preflight(profile_name: str, workspace: Path) -> dict[str, Any]:
    if profile_name not in STORAGE_PROFILES:
        raise BenchmarkError(f"Unknown storage profile: {profile_name}")
    profile = STORAGE_PROFILES[profile_name]
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(workspace)
    required = int(profile["file_size_mib"]) * 1024 * 1024
    reserve = max(1024 * 1024 * 1024, int(usage.total * 0.05))
    if usage.free - required < reserve:
        raise BenchmarkError(
            f"Not enough free space. Required test file: {required} bytes; safety reserve: {reserve} bytes."
        )
    fio = shutil.which("fio")
    if not fio:
        raise BenchmarkError("fio is not installed. Run CloudMark bootstrap with the storage pack.")
    estimated_seconds = int(profile["estimated_minutes"]) * 60
    return {
        "fio": fio,
        "fio_version": _fio_version(fio),
        "workspace": str(workspace),
        "file_size_bytes": required,
        "free_bytes": usage.free,
        "reserve_bytes": reserve,
        "estimated_seconds": estimated_seconds,
        "default_timeout_seconds": max(300, estimated_seconds + 300),
        "job_count": len(profile["jobs"]),
        "profile_version": profile["profile_version"],
        "methodology_version": profile["methodology_version"],
        "destructive": False,
        "raw_device": False,
    }


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "job"


def _parse_fio_log(path: Path, kind: str) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    direction_names = {0: "read", 1: "write", 2: "trim"}
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            for row in csv.reader(handle):
                if len(row) < 2:
                    continue
                try:
                    elapsed_ms = int(float(row[0].strip()))
                    raw_value = float(row[1].strip())
                    direction_code = int(row[2].strip()) if len(row) > 2 else -1
                    block_size = int(row[3].strip()) if len(row) > 3 else None
                except ValueError:
                    continue
                if kind == "bandwidth":
                    value = raw_value * 1024
                elif kind == "latency":
                    value = raw_value / 1_000_000
                else:
                    value = raw_value
                points.append(
                    {
                        "elapsed_ms": elapsed_ms,
                        "value": round(value, 3),
                        "direction": direction_names.get(direction_code, "unknown"),
                        "block_size": block_size,
                    }
                )
    except OSError:
        return []
    return points


def _collect_time_series(prefix: Path) -> tuple[dict[str, Any], list[Path]]:
    series: dict[str, Any] = {"interval_ms": 1000, "bandwidth": [], "iops": [], "latency": []}
    files: list[Path] = []
    patterns = {
        "bandwidth": f"{prefix.name}_bw*.log",
        "iops": f"{prefix.name}_iops*.log",
        "latency": f"{prefix.name}_lat*.log",
    }
    for kind, pattern in patterns.items():
        for path in sorted(prefix.parent.glob(pattern)):
            files.append(path)
            series[kind].extend(_parse_fio_log(path, kind))
        series[kind].sort(key=lambda item: (item["elapsed_ms"], item["direction"]))
    return series, files


def _partial_result(
    profile_name: str,
    profile: dict[str, Any],
    preflight: dict[str, Any],
    results: list[dict[str, Any]],
    started: float,
    test_file: Path,
) -> dict[str, Any]:
    return {
        "suite": "storage",
        "profile": profile_name,
        "profile_version": profile["profile_version"],
        "methodology_version": profile["methodology_version"],
        "tool": {"name": "fio", "version": preflight["fio_version"]},
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "preflight": preflight,
        "jobs": list(results),
        "safety": {
            "mode": "filesystem-test-file",
            "raw_device": False,
            "test_file_removed": not test_file.exists(),
        },
    }


def run_storage(
    profile_name: str,
    workspace: Path,
    run_id: str,
    *,
    context: JobContext | None = None,
) -> dict[str, Any]:
    preflight = storage_preflight(profile_name, workspace)
    profile = STORAGE_PROFILES[profile_name]
    workspace = Path(preflight["workspace"])
    test_file = workspace / f"{_safe_name(run_id)}.fio"
    fio = str(preflight["fio"])
    ioengine = "windowsaio" if os.name == "nt" else "libaio"
    size = f"{profile['file_size_mib']}m"
    results: list[dict[str, Any]] = []
    log_files: list[Path] = []
    started = time.monotonic()
    context = context or JobContext(
        run_id,
        total_steps=len(profile["jobs"]) + 2,
        timeout_seconds=preflight["default_timeout_seconds"],
    )
    pending_error: BaseException | None = None

    try:
        context.report("preparing", "allocate-test-file", partial_result=_partial_result(profile_name, profile, preflight, results, started, test_file))
        prepare = [
            fio,
            "--name=cloudmark-prepare",
            f"--filename={test_file}",
            f"--size={size}",
            "--rw=write",
            "--bs=1m",
            "--iodepth=8",
            f"--ioengine={ioengine}",
            "--direct=1",
            "--end_fsync=1",
            "--eta=never",
            "--output-format=json",
        ]
        prepared = context.run_process(prepare, label="storage preparation")
        if prepared.returncode != 0:
            raise BenchmarkError(f"fio preparation failed: {prepared.stderr.strip()}")
        context.complete_step("benchmarking", str(profile["jobs"][0]["name"]))

        for index, workload in enumerate(profile["jobs"]):
            job_name = str(workload["name"])
            context.report("benchmarking", job_name, partial_result=_partial_result(profile_name, profile, preflight, results, started, test_file))
            log_prefix = workspace / f"{_safe_name(run_id)}-{index:02d}-{_safe_name(job_name)}"
            command = [
                fio,
                f"--name={job_name}",
                f"--filename={test_file}",
                f"--size={size}",
                f"--rw={workload['rw']}",
                f"--bs={workload['bs']}",
                f"--iodepth={workload['iodepth']}",
                f"--runtime={workload['runtime']}",
                f"--ramp_time={workload.get('ramp_time', 5)}",
                f"--ioengine={ioengine}",
                "--direct=1",
                "--time_based=1",
                "--group_reporting=1",
                "--invalidate=1",
                "--randrepeat=0",
                "--lat_percentiles=1",
                "--percentile_list=50:90:95:99:99.9",
                "--log_avg_msec=1000",
                f"--write_bw_log={log_prefix}",
                f"--write_iops_log={log_prefix}",
                f"--write_lat_log={log_prefix}",
                "--eta=never",
                "--output-format=json",
            ]
            if "rwmixread" in workload:
                command.append(f"--rwmixread={workload['rwmixread']}")
            if workload.get("fsync"):
                command.append("--fsync=1")
            completed = context.run_process(command, label=f"fio job {job_name}")
            if completed.returncode != 0:
                raise BenchmarkError(f"fio job {job_name} failed: {completed.stderr.strip()}")
            try:
                payload = json.loads(completed.stdout)
                fio_job = payload["jobs"][0]
            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                raise BenchmarkError(f"fio job {job_name} returned invalid JSON output") from exc
            time_series, created_logs = _collect_time_series(log_prefix)
            log_files.extend(created_logs)
            results.append(_metrics(fio_job, workload, time_series))
            next_job = str(profile["jobs"][index + 1]["name"]) if index + 1 < len(profile["jobs"]) else "remove-test-file"
            context.complete_step(
                "benchmarking" if index + 1 < len(profile["jobs"]) else "cleanup",
                next_job,
                partial_result=_partial_result(profile_name, profile, preflight, results, started, test_file),
            )
    except BaseException as exc:
        pending_error = exc
    finally:
        try:
            test_file.unlink(missing_ok=True)
        except OSError:
            pass
        for log_file in log_files:
            try:
                log_file.unlink(missing_ok=True)
            except OSError:
                pass
        for remaining in workspace.glob(f"{_safe_name(run_id)}-*_*.log"):
            try:
                remaining.unlink(missing_ok=True)
            except OSError:
                pass

    result = _partial_result(profile_name, profile, preflight, results, started, test_file)
    if pending_error is not None:
        if isinstance(pending_error, RunStopped):
            pending_error.partial_result = result
        raise pending_error
    context.complete_step("completed", None, partial_result=result)
    return result
