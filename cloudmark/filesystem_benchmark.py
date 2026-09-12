from __future__ import annotations

import hashlib
import math
import os
import platform
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from .profiles import STORAGE_PROFILES
from .runner import JobContext, RunStopped


FILESYSTEM_METHODOLOGY_VERSION = "storage-filesystem-v1"
FILESYSTEM_OPERATION_CONTRACT = (
    ("small-file-create", "create"),
    ("small-file-stat", "stat"),
    ("small-file-read-verify", "read-verify"),
    ("small-file-rename", "rename"),
    ("small-file-delete", "delete"),
    ("durable-create-fsync", "durable-create"),
)


class FilesystemBenchmarkError(RuntimeError):
    def __init__(self, message: str):
        super().__init__(message)
        self.partial_result: dict[str, Any] | None = None


def _safe_run_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "run"


def deterministic_payload(index: int, size: int) -> bytes:
    if not 0 <= index <= 1_000_000 or not 512 <= size <= 16_384:
        raise FilesystemBenchmarkError("Filesystem payload shape is outside the fixed safety bounds.")
    seed = hashlib.sha256(f"cloudmark-filesystem-v1:{index}".encode("ascii")).digest()
    blocks = [hashlib.sha256(seed + counter.to_bytes(4, "big")).digest() for counter in range(math.ceil(size / 32))]
    return b"".join(blocks)[:size]


def latency_percentiles_ms(samples_ns: list[int]) -> dict[str, float]:
    if not samples_ns or any(not isinstance(value, int) or value < 0 for value in samples_ns):
        raise FilesystemBenchmarkError("Filesystem latency samples are missing or invalid.")
    ordered = sorted(samples_ns)

    def percentile(value: float) -> float:
        index = max(0, math.ceil(value / 100 * len(ordered)) - 1)
        return round(ordered[index] / 1_000_000, 6)

    return {
        "minimum": round(ordered[0] / 1_000_000, 6),
        "p50": percentile(50),
        "p95": percentile(95),
        "p99": percentile(99),
        "maximum": round(ordered[-1] / 1_000_000, 6),
    }


def filesystem_preflight(profile_name: str, workspace: Path) -> dict[str, Any]:
    profile = STORAGE_PROFILES.get(profile_name)
    if not profile or profile.get("executor") != "native-filesystem":
        raise FilesystemBenchmarkError(f"Unknown native filesystem profile: {profile_name}")
    if profile.get("methodology_version") != FILESYSTEM_METHODOLOGY_VERSION:
        raise FilesystemBenchmarkError("Filesystem methodology does not match the installed executor contract.")
    operation_contract = tuple(
        (str(job.get("name", "")), str(job.get("operation", ""))) for job in profile.get("jobs", [])
    )
    if operation_contract != FILESYSTEM_OPERATION_CONTRACT:
        raise FilesystemBenchmarkError("Filesystem operations do not match the fixed executor contract.")
    file_count = int(profile.get("file_count", 0))
    file_bytes = int(profile.get("file_bytes", 0))
    directory_count = int(profile.get("directory_count", 0))
    durable_count = int(profile.get("durable_file_count", 0))
    if not 1 <= file_count <= 4096 or not 512 <= file_bytes <= 16_384:
        raise FilesystemBenchmarkError("Filesystem file count or size is outside the safety contract.")
    if not 1 <= directory_count <= 64 or file_count % directory_count != 0:
        raise FilesystemBenchmarkError("Filesystem directory shape is outside the safety contract.")
    if not 1 <= durable_count <= min(256, file_count):
        raise FilesystemBenchmarkError("Durable small-file count is outside the safety contract.")
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(workspace)
    required = int(profile["file_size_mib"]) * 1024 * 1024
    generated_payload_bytes = (file_count + durable_count) * file_bytes
    if not generated_payload_bytes <= required <= 64 * 1024 * 1024:
        raise FilesystemBenchmarkError("Filesystem workspace allowance is outside the safety contract.")
    reserve = max(1024**3, int(usage.total * 0.05))
    if usage.free - required < reserve:
        raise FilesystemBenchmarkError(
            f"Not enough free space. Required workspace: {required} bytes; safety reserve: {reserve} bytes."
        )
    estimated_seconds = int(profile["estimated_minutes"]) * 60
    return {
        "executor": "python-standard-library",
        "tool_version": f"Python {platform.python_version()}",
        "workspace": str(workspace),
        "required_workspace_bytes": required,
        "generated_payload_bytes": generated_payload_bytes,
        "free_bytes": usage.free,
        "reserve_bytes": reserve,
        "file_count": file_count,
        "file_bytes": file_bytes,
        "directory_count": directory_count,
        "durable_file_count": durable_count,
        "estimated_seconds": estimated_seconds,
        "default_timeout_seconds": max(300, estimated_seconds + 180),
        "job_count": len(profile["jobs"]),
        "profile_version": profile["profile_version"],
        "methodology_version": profile["methodology_version"],
        "destructive": False,
        "raw_device": False,
    }


def _operation_result(
    name: str,
    operation: str,
    samples_ns: list[int],
    elapsed_seconds: float,
    *,
    bytes_processed: int = 0,
    integrity: dict[str, Any] | None = None,
    durability: dict[str, Any] | None = None,
    cache_scope: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "operation": operation,
        "operation_count": len(samples_ns),
        "elapsed_seconds": round(elapsed_seconds, 6),
        "operations_per_second": round(len(samples_ns) / elapsed_seconds, 3) if elapsed_seconds > 0 else 0.0,
        "latency_ms": latency_percentiles_ms(samples_ns),
        "bytes_processed": bytes_processed,
        "integrity": integrity or {"status": "not-applicable"},
        "durability": durability or {"status": "not-applicable"},
        "cache_scope": cache_scope,
    }


def _partial_result(
    profile_name: str,
    profile: dict[str, Any],
    preflight: dict[str, Any],
    operations: list[dict[str, Any]],
    root: Path,
    started: float,
) -> dict[str, Any]:
    return {
        "suite": "storage",
        "profile": profile_name,
        "profile_version": profile["profile_version"],
        "methodology_version": profile["methodology_version"],
        "tool": {
            "name": "cloudmark-filesystem-bench",
            "version": f"{FILESYSTEM_METHODOLOGY_VERSION}; {preflight['tool_version']}",
        },
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "preflight": preflight,
        "filesystem_operations": list(operations),
        "measurement_contract": {
            "worker_model": "single-process-sequential",
            "payload": f"deterministic-{preflight['file_bytes']}-byte-sha256-verified",
            "latency_clock": "python-perf-counter-ns",
            "cache_control": "observed-not-flushed",
            "claim_scope": "guest-filesystem-and-python-runtime",
        },
        "safety": {
            "mode": "generated-filesystem-workspace",
            "raw_device": False,
            "test_file_removed": not root.exists(),
            "workspace_removed": not root.exists(),
        },
    }


def run_filesystem_storage(
    profile_name: str,
    workspace: Path,
    run_id: str,
    *,
    context: JobContext | None = None,
) -> dict[str, Any]:
    preflight = filesystem_preflight(profile_name, workspace)
    profile = STORAGE_PROFILES[profile_name]
    workspace = Path(preflight["workspace"])
    base = (workspace / "filesystem-benchmarks").resolve()
    root = (base / _safe_run_name(run_id)).resolve()
    try:
        root.relative_to(base)
    except ValueError as exc:
        raise FilesystemBenchmarkError("Filesystem benchmark path escaped its workspace.") from exc
    if root.exists():
        raise FilesystemBenchmarkError("Filesystem benchmark found residual state for this Run.")
    operations: list[dict[str, Any]] = []
    started = time.monotonic()
    context = context or JobContext(
        run_id,
        total_steps=len(profile["jobs"]) + 2,
        timeout_seconds=preflight["default_timeout_seconds"],
    )
    file_count = int(preflight["file_count"])
    file_bytes = int(preflight["file_bytes"])
    directory_count = int(preflight["directory_count"])
    durable_count = int(preflight["durable_file_count"])
    payloads = [deterministic_payload(index, file_bytes) for index in range(file_count)]
    hashes = [hashlib.sha256(payload).digest() for payload in payloads]
    pending_error: BaseException | None = None

    def measure(name: str, operation: str, count: int, action: Callable[[int], None], **metadata: Any) -> None:
        context.report(
            "benchmarking-filesystem",
            name,
            partial_result=_partial_result(profile_name, profile, preflight, operations, root, started),
        )
        samples: list[int] = []
        phase_started = time.perf_counter()
        for index in range(count):
            if index % 32 == 0:
                context.checkpoint()
            item_started = time.perf_counter_ns()
            action(index)
            samples.append(time.perf_counter_ns() - item_started)
        elapsed = time.perf_counter() - phase_started
        operations.append(
            _operation_result(name, operation, samples, elapsed, cache_scope=metadata.pop("cache_scope"), **metadata)
        )
        context.complete_step(
            "benchmarking-filesystem",
            None,
            partial_result=_partial_result(profile_name, profile, preflight, operations, root, started),
        )

    try:
        for index in range(directory_count):
            (root / f"d{index:02d}").mkdir(parents=True, exist_ok=False)
        durable_root = root / "durable"
        durable_root.mkdir()
        files = [root / f"d{index % directory_count:02d}" / f"f{index:05d}.bin" for index in range(file_count)]
        renamed_files = [path.with_suffix(".renamed") for path in files]
        context.complete_step(
            "benchmarking-filesystem",
            "small-file-create",
            partial_result=_partial_result(profile_name, profile, preflight, operations, root, started),
        )

        def create_file(index: int) -> None:
            with files[index].open("xb") as handle:
                handle.write(payloads[index])

        measure(
            "small-file-create",
            "create",
            file_count,
            create_file,
            bytes_processed=file_count * file_bytes,
            cache_scope="filesystem-and-page-cache-influenced",
        )

        def stat_file(index: int) -> None:
            if files[index].stat().st_size != file_bytes:
                raise FilesystemBenchmarkError("Small-file stat size did not match the fixed contract.")

        measure(
            "small-file-stat",
            "stat",
            file_count,
            stat_file,
            cache_scope="warm-directory-and-inode-cache-after-create",
        )
        mismatches = 0

        def read_verify(index: int) -> None:
            nonlocal mismatches
            with files[index].open("rb") as handle:
                value = handle.read(file_bytes + 1)
            if len(value) != file_bytes or hashlib.sha256(value).digest() != hashes[index]:
                mismatches += 1

        measure(
            "small-file-read-verify",
            "read-verify",
            file_count,
            read_verify,
            bytes_processed=file_count * file_bytes,
            cache_scope="warm-page-cache-possible-after-create",
        )
        operations[-1]["integrity"] = {
            "status": "verified" if mismatches == 0 else "mismatch",
            "algorithm": "SHA-256",
            "verified_files": file_count - mismatches,
            "mismatches": mismatches,
        }
        if mismatches:
            raise FilesystemBenchmarkError("Small-file integrity verification detected a checksum mismatch.")

        measure(
            "small-file-rename",
            "rename",
            file_count,
            lambda index: files[index].rename(renamed_files[index]),
            cache_scope="warm-directory-cache-after-create",
        )
        measure(
            "small-file-delete",
            "delete",
            file_count,
            lambda index: renamed_files[index].unlink(),
            cache_scope="warm-directory-cache-after-rename",
        )
        durable_payloads = payloads[:durable_count]
        durable_hashes = hashes[:durable_count]
        durable_files = [durable_root / f"f{index:04d}.bin" for index in range(durable_count)]

        def durable_create(index: int) -> None:
            with durable_files[index].open("xb") as handle:
                handle.write(durable_payloads[index])
                handle.flush()
                os.fsync(handle.fileno())

        directory_fsync: dict[str, Any] = {"status": "unavailable", "reason": "Directory fsync is unavailable."}
        measure_started = time.perf_counter()
        measure(
            "durable-create-fsync",
            "durable-create",
            durable_count,
            durable_create,
            bytes_processed=durable_count * file_bytes,
            durability={"status": "per-file-fsync", "file_fsync_count": durable_count},
            cache_scope="per-file-fsync-before-close",
        )
        if os.name != "nt" and hasattr(os, "O_DIRECTORY"):
            descriptor = os.open(durable_root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                sync_started = time.perf_counter_ns()
                os.fsync(descriptor)
                directory_fsync = {
                    "status": "observed",
                    "duration_ms": round((time.perf_counter_ns() - sync_started) / 1_000_000, 6),
                }
            finally:
                os.close(descriptor)
        durable_mismatches = 0
        for index, path in enumerate(durable_files):
            context.checkpoint()
            with path.open("rb") as handle:
                value = handle.read(file_bytes + 1)
            if len(value) != file_bytes or hashlib.sha256(value).digest() != durable_hashes[index]:
                durable_mismatches += 1
        operations[-1]["integrity"] = {
            "status": "verified" if durable_mismatches == 0 else "mismatch",
            "algorithm": "SHA-256",
            "verified_files": durable_count - durable_mismatches,
            "mismatches": durable_mismatches,
        }
        operations[-1]["durability"]["directory_fsync"] = directory_fsync
        operations[-1]["post_measurement_verification_seconds"] = round(
            max(0.0, time.perf_counter() - measure_started - operations[-1]["elapsed_seconds"]), 6
        )
        if durable_mismatches:
            raise FilesystemBenchmarkError("Durable small-file verification detected a checksum mismatch.")
    except BaseException as exc:
        pending_error = exc
    finally:
        try:
            if root.exists():
                shutil.rmtree(root)
        except OSError:
            pass

    result = _partial_result(profile_name, profile, preflight, operations, root, started)
    if pending_error is not None:
        if isinstance(pending_error, (RunStopped, FilesystemBenchmarkError, OSError)):
            pending_error.partial_result = result
        raise pending_error
    if not result["safety"]["workspace_removed"]:
        error = FilesystemBenchmarkError("Filesystem benchmark workspace cleanup could not be verified.")
        error.partial_result = result
        raise error
    context.complete_step("completed", None, partial_result=result)
    return result
