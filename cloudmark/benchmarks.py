from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .profiles import STORAGE_PROFILES


class BenchmarkError(RuntimeError):
    pass


def _percentile(section: dict[str, Any], key: str) -> float | None:
    latency = section.get("clat_ns") or section.get("lat_ns") or {}
    percentiles = latency.get("percentile") or {}
    value = percentiles.get(key)
    return float(value) / 1_000_000 if value is not None else None


def _metrics(job: dict[str, Any], workload: dict[str, Any]) -> dict[str, Any]:
    read = job.get("read", {})
    write = job.get("write", {})
    return {
        "name": workload["name"],
        "workload": workload,
        "read": {
            "iops": read.get("iops", 0),
            "bandwidth_bytes_per_second": read.get("bw_bytes", 0),
            "p50_ms": _percentile(read, "50.000000"),
            "p90_ms": _percentile(read, "90.000000"),
            "p95_ms": _percentile(read, "95.000000"),
            "p99_ms": _percentile(read, "99.000000"),
            "p999_ms": _percentile(read, "99.900000"),
        },
        "write": {
            "iops": write.get("iops", 0),
            "bandwidth_bytes_per_second": write.get("bw_bytes", 0),
            "p50_ms": _percentile(write, "50.000000"),
            "p90_ms": _percentile(write, "90.000000"),
            "p95_ms": _percentile(write, "95.000000"),
            "p99_ms": _percentile(write, "99.000000"),
            "p999_ms": _percentile(write, "99.900000"),
        },
        "cpu": {"user_percent": job.get("usr_cpu"), "system_percent": job.get("sys_cpu")},
    }


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
    return {
        "fio": fio,
        "workspace": str(workspace),
        "file_size_bytes": required,
        "free_bytes": usage.free,
        "reserve_bytes": reserve,
        "destructive": False,
        "raw_device": False,
    }


def run_storage(profile_name: str, workspace: Path, run_id: str) -> dict[str, Any]:
    preflight = storage_preflight(profile_name, workspace)
    profile = STORAGE_PROFILES[profile_name]
    test_file = workspace / f"{run_id}.fio"
    fio = preflight["fio"]
    ioengine = "windowsaio" if os.name == "nt" else "libaio"
    size = f"{profile['file_size_mib']}m"
    results: list[dict[str, Any]] = []
    started = time.monotonic()
    try:
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
            "--output-format=json",
        ]
        prepared = subprocess.run(prepare, capture_output=True, text=True, check=False)
        if prepared.returncode != 0:
            raise BenchmarkError(f"fio preparation failed: {prepared.stderr.strip()}")

        for workload in profile["jobs"]:
            command = [
                fio,
                f"--name={workload['name']}",
                f"--filename={test_file}",
                f"--size={size}",
                f"--rw={workload['rw']}",
                f"--bs={workload['bs']}",
                f"--iodepth={workload['iodepth']}",
                f"--runtime={workload['runtime']}",
                f"--ioengine={ioengine}",
                "--direct=1",
                "--time_based=1",
                "--group_reporting=1",
                "--lat_percentiles=1",
                "--percentile_list=50:90:95:99:99.9",
                "--output-format=json",
            ]
            if "rwmixread" in workload:
                command.append(f"--rwmixread={workload['rwmixread']}")
            if workload.get("fsync"):
                command.append("--fsync=1")
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            if completed.returncode != 0:
                raise BenchmarkError(f"fio job {workload['name']} failed: {completed.stderr.strip()}")
            payload = json.loads(completed.stdout)
            results.append(_metrics(payload["jobs"][0], workload))
    finally:
        try:
            test_file.unlink(missing_ok=True)
        except OSError:
            pass
    return {
        "profile": profile_name,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "preflight": preflight,
        "jobs": results,
        "safety": {
            "mode": "filesystem-test-file",
            "raw_device": False,
            "test_file_removed": not test_file.exists(),
        },
    }
