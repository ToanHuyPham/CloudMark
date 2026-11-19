"""Fixed GPU transfer planning and isolated CLI execution."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .gpu_environment import parse_opencl_probe
import json
import hashlib
import sys
import math
import statistics

from .runner import JobContext


TRANSFER_PLAN_VERSION = "gpu-transfer-plan-v1"
BUFFER_BYTES = 16 * 1024**2
ITERATIONS = 16
DEVICE_RESERVE_BYTES = 256 * 1024**2
HOST_RESERVE_BYTES = 512 * 1024**2


def _integer(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def plan_gpu_transfer(probe: dict[str, Any], device_index: int) -> dict[str, Any]:
    """Validate enumeration and produce a fixed plan; never allocate or dispatch."""
    if not _integer(device_index, 0, 15):
        raise ValueError("GPU device index must be an integer between 0 and 15.")
    normalized = parse_opencl_probe(json.dumps(probe))
    if normalized is None or normalized["status"] != "complete":
        raise ValueError("A complete valid OpenCL probe is required.")
    devices = normalized["devices"]
    if device_index >= len(devices):
        raise ValueError("Selected GPU is absent from the current probe.")
    device = devices[device_index]
    platform = normalized["platforms"][device["platform_index"]]
    for record, fields in (
        (platform, ("name", "vendor", "version")),
        (device, ("name", "vendor", "driver_version", "device_version")),
    ):
        if record.get("status") != "observed" or any(
            not isinstance(record.get(field), str) or not record[field].strip() for field in fields
        ):
            raise ValueError("GPU and platform identity must be fully observed.")
    if device.get("available") is not True:
        raise ValueError("Selected OpenCL GPU is not reported available.")
    if type(device.get("unified_memory")) is not bool:
        raise ValueError("Unified-memory semantics must be observed.")
    if type(device.get("address_bits")) is not int or device["address_bits"] not in (32, 64):
        raise ValueError("Unsupported or unavailable device address width.")
    capacity = device.get("global_memory_bytes")
    allocation_limit = device.get("max_allocation_bytes")
    if not _integer(capacity, 1, 2**63 - 1) or not _integer(allocation_limit, 1, capacity):
        raise ValueError("GPU memory limits must be positive and consistent.")
    device_allocation = 2 * BUFFER_BYTES
    if allocation_limit < BUFFER_BYTES or capacity - device_allocation < DEVICE_RESERVE_BYTES:
        raise ValueError("GPU capacity cannot accommodate the fixed buffers and reserve.")
    return {
        "plan_version": TRANSFER_PLAN_VERSION,
        "evidence_status": "planned-not-measured",
        "executor_available": False,
        "platform": deepcopy(platform),
        "device": deepcopy(device),
        "buffer_bytes": BUFFER_BYTES,
        "iterations": ITERATIONS,
        "operations": ["host-to-device", "device-to-host", "device-to-device"],
        "device_allocation_bytes": device_allocation,
        "device_capacity_reserve_bytes": DEVICE_RESERVE_BYTES,
        "device_free_memory_verified": False,
        "host_allocation_budget_bytes": 4 * BUFFER_BYTES,
        "host_reserve_bytes": HOST_RESERVE_BYTES,
        "host_headroom_verified": False,
        "interpretation": "shared-memory-copy" if device["unified_memory"] else "discrete-device-transfer",
        "policy": {
            "load_generated": False,
            "allocation_performed": False,
            "execution_authorized": False,
            "reprobe_required_before_execution": True,
            "identity_match_required_before_execution": True,
        },
    }


def run_gpu_transfer(device_index: int, *, confirm_load: bool, context: JobContext) -> dict[str, Any]:
    """Execute only the packaged worker with explicit confirmation and a bounded deadline."""
    if confirm_load is not True or not _integer(device_index, 0, 15):
        raise ValueError("GPU transfer requires explicit load confirmation and a valid device index.")
    if context.timeout_seconds > 90:
        raise ValueError("GPU transfer timeout must not exceed 90 seconds.")
    context.report("gpu-transfer", "isolated-copy-worker")
    completed = context.run_process(
        [sys.executable, "-m", "cloudmark.gpu_transfer_worker", "--device-index", str(device_index), "--confirm-load"],
        label="GPU transfer",
    )
    if completed.returncode or completed.stderr.strip() or len(completed.stdout.encode("utf-8")) > 65536:
        raise ValueError("GPU transfer worker failed; no completed measurement is available.")
    payload = json.loads(completed.stdout)
    result = validate_transfer_result(payload, device_index)
    context.complete_step("completed", None, partial_result=result)
    return result


def validate_transfer_result(payload: Any, device_index: int) -> dict[str, Any]:
    if not _integer(device_index, 0, 15):
        raise ValueError("Invalid requested GPU device index.")
    payload = deepcopy(payload)
    if not isinstance(payload, dict) or payload.get("methodology_version") != "gpu-transfer-v1":
        raise ValueError("Invalid GPU transfer result version.")
    plan = payload.get("plan") or {}
    device = plan.get("device") if isinstance(plan, dict) else None
    if (not isinstance(plan, dict) or not isinstance(device, dict)
            or plan.get("plan_version") != TRANSFER_PLAN_VERSION
            or type(device.get("index")) is not int or device["index"] != device_index):
        raise ValueError("GPU transfer target contract mismatch.")
    if plan.get("buffer_bytes") != BUFFER_BYTES or plan.get("iterations") != ITERATIONS:
        raise ValueError("GPU transfer allocation contract mismatch.")
    platform = plan.get("platform")
    if not isinstance(platform, dict):
        raise ValueError("GPU platform evidence missing.")
    # Rebuild the selected-device planning contract, rather than trusting fields
    # supplied by a remote worker. Normalize indexes only for this local check.
    selected = {**device, "index": 0, "platform_index": 0, "platform_device_index": 0}
    probe = {
        "methodology_version": "gpu-opencl-probe-v1", "status": "complete",
        "platform_count": 1, "device_count": 1,
        "platforms": [{**platform, "index": 0}], "devices": [selected],
        "policy": {"read_only": True, "context_created": False, "queue_created": False,
                   "buffer_allocated": False, "kernel_compiled": False, "load_generated": False,
                   "extension_list_persisted": False},
    }
    canonical = plan_gpu_transfer(probe, 0)
    for key in canonical.keys() - {"platform", "device"}:
        if type(plan.get(key)) is not type(canonical[key]) or plan[key] != canonical[key]:
            raise ValueError("GPU transfer planning evidence is inconsistent.")
    if (not _integer(platform.get("index"), 0, 7)
            or not _integer(device.get("platform_index"), 0, 7)
            or device["platform_index"] != platform["index"]
            or not _integer(device.get("platform_device_index"), 0, 15)):
        raise ValueError("GPU transfer platform selection is inconsistent.")
    if not _integer(payload.get("host_available_bytes"), HOST_RESERVE_BYTES + 4 * BUFFER_BYTES, 2**63 - 1):
        raise ValueError("GPU transfer host memory evidence is incomplete.")
    expected = hashlib.sha256(hashlib.shake_256(b"cloudmark-gpu-transfer-v1").digest(BUFFER_BYTES)).hexdigest()
    measurements = payload.get("measurements")
    names = ["host-to-device", "device-to-host", "device-to-device"]
    if not isinstance(measurements, list) or len(measurements) != 3 or payload.get("cleanup_verified") is not True:
        raise ValueError("Incomplete GPU transfer or cleanup evidence.")
    total_ns = 0
    for name, item in zip(names, measurements):
        if not isinstance(item, dict):
            raise ValueError("Malformed GPU transfer measurement.")
        samples = item.get("samples_ns")
        if (item.get("name") != name or item.get("bytes_per_iteration") != BUFFER_BYTES
                or item.get("iterations") != ITERATIONS or item.get("sha256") != expected
                or item.get("integrity_verified") is not True or not isinstance(samples, list)
                or len(samples) != ITERATIONS or not all(_integer(value, 1, 90 * 10**9) for value in samples)):
            raise ValueError("GPU transfer measurement contract mismatch.")
        item["bandwidth_bytes_per_second"] = BUFFER_BYTES * ITERATIONS * 10**9 / sum(samples)
        ordered = sorted(samples)
        mean = statistics.mean(samples)
        item["latency_summary"] = {
            "version": "gpu-transfer-summary-v1",
            "sample_count": len(samples),
            "percentile_method": "nearest-rank",
            "minimum_ms": ordered[0] / 10**6,
            "maximum_ms": ordered[-1] / 10**6,
            "mean_ms": mean / 10**6,
            **{f"p{percentile}_ms": ordered[math.ceil(percentile * len(samples) / 100) - 1] / 10**6
               for percentile in (50, 95, 99)},
            "coefficient_of_variation_percent": statistics.pstdev(samples) / mean * 100,
            "tail_claim": "descriptive-small-sample-only",
        }
        total_ns += sum(samples)
    if total_ns > 90 * 10**9:
        raise ValueError("GPU transfer samples exceed the maximum execution deadline.")
    payload.update({"suite": "gpu", "profile": "gpu-transfer-quick", "profile_version": "1.0",
                    "evidence_status": "measured", "timing_scope": "synchronous-host-wall-clock",
                    "device_free_memory_verified": False})
    payload["gpu_transfer_measurements"] = deepcopy(measurements)
    return payload
