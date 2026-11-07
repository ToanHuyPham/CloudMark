"""No-allocation planning boundary for the pending GPU transfer executor."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .gpu_environment import parse_opencl_probe
import json


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
