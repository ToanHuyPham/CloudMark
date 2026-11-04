from __future__ import annotations

import ctypes.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GPU_ENVIRONMENT_VERSION = "gpu-environment-v3"
GPU_MAX_DEVICES = 16
GPU_SYSFS_MAX_BYTES = 4_096
GPU_TOOL_OUTPUT_MAX_BYTES = 65_536
GPU_SAFE_LABEL_MAX_LENGTH = 160
GPU_PCI_ADDRESS = re.compile(r"^(?:[0-9A-Fa-f]{4,8}:)?[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-7]$")
GPU_CARD_NAME = re.compile(r"^card([0-9]{1,3})$")
GPU_HEX_ID = re.compile(r"^0x([0-9A-Fa-f]{4})$")
GPU_DRIVER_NAME = re.compile(r"^[A-Za-z0-9_.+-]{1,64}$")
GPU_WINDOWS_VENDOR = re.compile(r"(?:^|\\)VEN_([0-9A-Fa-f]{4})(?:&|$)")
GPU_WINDOWS_DEVICE = re.compile(r"(?:^|&)DEV_([0-9A-Fa-f]{4})(?:&|$)")
GPU_WINDOWS_SUBSYSTEM = re.compile(r"(?:^|&)SUBSYS_([0-9A-Fa-f]{8})(?:&|$)")
GPU_OPENCL_PROBE_VERSION = "gpu-opencl-probe-v1"

GPU_VENDOR_NAMES = {
    "0x1002": "AMD",
    "0x10de": "NVIDIA",
    "0x1414": "Microsoft",
    "0x15ad": "VMware",
    "0x1a03": "ASPEED",
    "0x1af4": "Virtio",
    "0x8086": "Intel",
}


def _bounded_text(path: Path, maximum_bytes: int = GPU_SYSFS_MAX_BYTES) -> tuple[str | None, bool]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            value = handle.read(maximum_bytes + 1)
    except OSError:
        return None, False
    return value[:maximum_bytes], len(value) > maximum_bytes


def _hex_identifier(path: Path) -> str | None:
    value, truncated = _bounded_text(path, 32)
    if value is None or truncated:
        return None
    normalized = value.strip().lower()
    return normalized if GPU_HEX_ID.fullmatch(normalized) else None


def _bounded_integer(path: Path, *, minimum: int, maximum: int) -> int | None:
    value, truncated = _bounded_text(path, 64)
    if value is None or truncated:
        return None
    try:
        parsed = int(value.strip())
    except ValueError:
        return None
    return parsed if minimum <= parsed <= maximum else None


def _safe_label(value: Any) -> str | None:
    normalized = " ".join(str(value or "").split())
    if not normalized or len(normalized) > GPU_SAFE_LABEL_MAX_LENGTH:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        return None
    return normalized


def _uevent(path: Path) -> tuple[dict[str, str], bool]:
    value, truncated = _bounded_text(path)
    if value is None or truncated:
        return {}, truncated
    fields: dict[str, str] = {}
    for line in value.splitlines()[:64]:
        key, separator, raw_value = line.partition("=")
        if not separator or key not in {"DRIVER", "PCI_SLOT_NAME"}:
            continue
        normalized = _safe_label(raw_value)
        if normalized is not None:
            fields[key] = normalized
    return fields, False


def _driver_name(device: Path, uevent: dict[str, str]) -> str | None:
    driver = uevent.get("DRIVER")
    if driver and GPU_DRIVER_NAME.fullmatch(driver):
        return driver
    try:
        name = (device / "driver").resolve(strict=True).name
    except OSError:
        return None
    return name if GPU_DRIVER_NAME.fullmatch(name) else None


def _pci_address(device: Path, uevent: dict[str, str]) -> str | None:
    value = uevent.get("PCI_SLOT_NAME") or device.name
    if not GPU_PCI_ADDRESS.fullmatch(value):
        return None
    fields = value.lower().split(":")
    return ":".join(fields[-3:]) if len(fields) == 3 else value.lower()


def _function_type(device: Path, total_vfs: int | None) -> str:
    if (device / "physfn").exists():
        return "virtual-function"
    if total_vfs is not None and total_vfs > 0:
        return "physical-function"
    return "standard-or-undetected"


def _linux_gpu_devices(drm_root: Path) -> tuple[list[dict[str, Any]], bool]:
    try:
        cards = sorted(
            (entry for entry in drm_root.iterdir() if GPU_CARD_NAME.fullmatch(entry.name)),
            key=lambda entry: int(GPU_CARD_NAME.fullmatch(entry.name).group(1)),  # type: ignore[union-attr]
        )
    except OSError:
        return [], True
    truncated = len(cards) > GPU_MAX_DEVICES
    devices: list[dict[str, Any]] = []
    seen_addresses: set[str] = set()
    for card in cards[:GPU_MAX_DEVICES]:
        device = card / "device"
        if not device.exists():
            continue
        vendor_id = _hex_identifier(device / "vendor")
        device_id = _hex_identifier(device / "device")
        subsystem_vendor_id = _hex_identifier(device / "subsystem_vendor")
        subsystem_device_id = _hex_identifier(device / "subsystem_device")
        pci_class = _hex_identifier(device / "class")
        if pci_class is None:
            class_text, class_truncated = _bounded_text(device / "class", 32)
            normalized_class = (class_text or "").strip().lower()
            pci_class = normalized_class if not class_truncated and re.fullmatch(r"0x[0-9a-f]{6}", normalized_class) else None
        if pci_class is not None and not pci_class.startswith("0x03"):
            continue
        uevent, uevent_truncated = _uevent(device / "uevent")
        pci_address = _pci_address(device, uevent)
        if pci_address and pci_address in seen_addresses:
            continue
        if pci_address:
            seen_addresses.add(pci_address)
        driver = _driver_name(device, uevent)
        numa_node = _bounded_integer(device / "numa_node", minimum=-1, maximum=1023)
        if numa_node == -1:
            numa_node = None
        vram_total = _bounded_integer(device / "mem_info_vram_total", minimum=0, maximum=2**63 - 1)
        if vram_total is None:
            vram_total = _bounded_integer(device / "lmem_total_bytes", minimum=0, maximum=2**63 - 1)
        total_vfs = _bounded_integer(device / "sriov_totalvfs", minimum=0, maximum=65_535)
        enabled_vfs = _bounded_integer(device / "sriov_numvfs", minimum=0, maximum=65_535)
        required = (vendor_id, device_id, pci_class, driver)
        status = "observed" if all(value is not None for value in required) and not uevent_truncated else "partial"
        devices.append(
            {
                "index": len(devices),
                "status": status,
                "drm_card": card.name,
                "pci_address": pci_address,
                "pci_class": pci_class,
                "vendor_id": vendor_id,
                "vendor_name": GPU_VENDOR_NAMES.get(vendor_id or "", "Unknown"),
                "device_id": device_id,
                "subsystem_vendor_id": subsystem_vendor_id,
                "subsystem_device_id": subsystem_device_id,
                "driver": driver,
                "driver_version": None,
                "product_name": None,
                "numa_node": numa_node,
                "vram_total_bytes": vram_total,
                "memory_capacity_source": (
                    "linux-drm-vram-total" if vram_total is not None else "unavailable"
                ),
                "memory_capacity_may_be_truncated": False,
                "function_type": _function_type(device, total_vfs),
                "sriov_total_vfs": total_vfs,
                "sriov_enabled_vfs": enabled_vfs,
                "iommu_group_visible": (device / "iommu_group").exists(),
            }
        )
    return devices, truncated


def parse_nvidia_smi_inventory(value: str) -> list[dict[str, Any]]:
    if len(value.encode("utf-8", errors="replace")) > GPU_TOOL_OUTPUT_MAX_BYTES:
        return []
    records: list[dict[str, Any]] = []
    for line in value.splitlines()[:GPU_MAX_DEVICES]:
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 5:
            return []
        try:
            index = int(fields[0])
            memory_mib = int(fields[2])
        except ValueError:
            return []
        pci_address = fields[4].lower()
        pci_parts = pci_address.split(":")
        if len(pci_parts) == 3 and len(pci_parts[0]) == 8:
            pci_address = f"{pci_parts[0][-4:]}:{pci_parts[1]}:{pci_parts[2]}"
        if (
            not 0 <= index < GPU_MAX_DEVICES
            or not 0 <= memory_mib <= 2**32
            or not GPU_PCI_ADDRESS.fullmatch(pci_address)
        ):
            return []
        product_name = _safe_label(fields[1])
        driver_version = _safe_label(fields[3])
        if product_name is None or driver_version is None:
            return []
        records.append(
            {
                "index": index,
                "product_name": product_name,
                "vram_total_bytes": memory_mib * 1024 * 1024,
                "driver_version": driver_version,
                "pci_address": pci_address,
            }
        )
    return records


def _nvidia_smi_inventory(executable: str) -> list[dict[str, Any]]:
    command = [
        executable,
        "--query-gpu=index,name,memory.total,driver_version,pci.bus_id",
        "--format=csv,noheader,nounits",
    ]
    environment = os.environ.copy()
    environment.update({"LC_ALL": "C", "LANG": "C"})
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return parse_nvidia_smi_inventory(result.stdout)


def _enrich_nvidia_devices(devices: list[dict[str, Any]], records: list[dict[str, Any]]) -> None:
    by_address = {str(record["pci_address"]): record for record in records}
    for device in devices:
        if device.get("vendor_id") != "0x10de":
            continue
        record = by_address.get(str(device.get("pci_address") or ""))
        if record is None:
            continue
        device["product_name"] = record["product_name"]
        device["driver_version"] = record["driver_version"]
        device["vram_total_bytes"] = record["vram_total_bytes"]
        device["memory_capacity_source"] = "nvidia-smi-memory-total-mib"
        device["memory_capacity_may_be_truncated"] = False


def _windows_pci_identifiers(value: Any) -> tuple[str | None, str | None, str | None, str | None]:
    text = str(value or "")
    vendor = GPU_WINDOWS_VENDOR.search(text)
    device = GPU_WINDOWS_DEVICE.search(text)
    subsystem = GPU_WINDOWS_SUBSYSTEM.search(text)
    vendor_id = f"0x{vendor.group(1).lower()}" if vendor else None
    device_id = f"0x{device.group(1).lower()}" if device else None
    subsystem_device_id = f"0x{subsystem.group(1)[:4].lower()}" if subsystem else None
    subsystem_vendor_id = f"0x{subsystem.group(1)[4:].lower()}" if subsystem else None
    return vendor_id, device_id, subsystem_vendor_id, subsystem_device_id


def _windows_adapter_ram(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if 0 < parsed <= 2**32 - 1 else None


def parse_windows_cim_inventory(value: str) -> list[dict[str, Any]] | None:
    if len(value.encode("utf-8", errors="replace")) > GPU_TOOL_OUTPUT_MAX_BYTES:
        return None
    try:
        payload = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, list) or len(payload) > GPU_MAX_DEVICES:
        return None
    devices: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            return None
        product_name = _safe_label(item.get("Name"))
        driver_version = _safe_label(item.get("DriverVersion"))
        vendor_id, device_id, subsystem_vendor_id, subsystem_device_id = _windows_pci_identifiers(
            item.get("PNPDeviceID")
        )
        vram_total = _windows_adapter_ram(item.get("AdapterRAM"))
        status = "observed" if product_name and driver_version else "partial"
        devices.append(
            {
                "index": len(devices),
                "status": status,
                "platform_device": f"windows-video-controller-{len(devices)}",
                "drm_card": None,
                "pci_address": None,
                "pci_class": "display-controller",
                "vendor_id": vendor_id,
                "vendor_name": GPU_VENDOR_NAMES.get(vendor_id or "", "Unknown"),
                "device_id": device_id,
                "subsystem_vendor_id": subsystem_vendor_id,
                "subsystem_device_id": subsystem_device_id,
                "driver": None,
                "driver_version": driver_version,
                "product_name": product_name,
                "numa_node": None,
                "vram_total_bytes": vram_total,
                "memory_capacity_source": (
                    "windows-cim-adapterram-uint32" if vram_total is not None else "unavailable"
                ),
                "memory_capacity_may_be_truncated": vram_total is not None,
                "function_type": "windows-cim-controller",
                "sriov_total_vfs": None,
                "sriov_enabled_vfs": None,
                "iommu_group_visible": None,
            }
        )
    return devices


def _windows_cim_inventory(executable: str) -> tuple[list[dict[str, Any]], bool]:
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "$items = @(Get-CimInstance Win32_VideoController | "
        "Select-Object Name,DriverVersion,AdapterRAM,PNPDeviceID); "
        "ConvertTo-Json -InputObject $items -Compress -Depth 2"
    )
    command = [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return [], False
    if result.returncode != 0 or result.stderr.strip():
        return [], False
    devices = parse_windows_cim_inventory(result.stdout)
    return (devices, True) if devices is not None else ([], False)


def _bounded_count(value: Any, maximum: int) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum else None


def _bounded_opencl_bytes(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**63 - 1 else None


def _unavailable_opencl_probe() -> dict[str, Any]:
    return {
        "methodology_version": GPU_OPENCL_PROBE_VERSION,
        "status": "unavailable",
        "platform_count": 0,
        "device_count": 0,
        "platforms": [],
        "devices": [],
        "reason_code": "opencl-probe-unavailable",
        "policy": {
            "read_only": True,
            "context_created": False,
            "queue_created": False,
            "buffer_allocated": False,
            "kernel_compiled": False,
            "load_generated": False,
            "extension_list_persisted": False,
        },
    }


def parse_opencl_probe(value: str) -> dict[str, Any] | None:
    if len(value.encode("utf-8", errors="replace")) > GPU_TOOL_OUTPUT_MAX_BYTES:
        return None
    try:
        payload = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("methodology_version") != GPU_OPENCL_PROBE_VERSION:
        return None
    status = payload.get("status")
    platform_count = _bounded_count(payload.get("platform_count"), 8)
    device_count = _bounded_count(payload.get("device_count"), GPU_MAX_DEVICES)
    platforms = payload.get("platforms")
    devices = payload.get("devices")
    policy = payload.get("policy")
    if (
        status not in {"complete", "partial", "unavailable"}
        or platform_count is None
        or device_count is None
        or not isinstance(platforms, list)
        or not isinstance(devices, list)
        or len(platforms) != platform_count
        or len(devices) != device_count
        or not isinstance(policy, dict)
    ):
        return None
    expected_policy = _unavailable_opencl_probe()["policy"]
    if policy != expected_policy:
        return None
    normalized_platforms: list[dict[str, Any]] = []
    for index, item in enumerate(platforms):
        if (
            not isinstance(item, dict)
            or item.get("index") != index
            or item.get("status") not in {"observed", "partial"}
        ):
            return None
        text_values: dict[str, str | None] = {}
        for key in ("name", "vendor", "version"):
            text_values[key] = _safe_label(item.get(key)) if item.get(key) is not None else None
            if item.get(key) is not None and text_values[key] is None:
                return None
        normalized_platforms.append({"index": index, "status": item["status"], **text_values})
    normalized_devices: list[dict[str, Any]] = []
    for index, item in enumerate(devices):
        if (
            not isinstance(item, dict)
            or item.get("index") != index
            or item.get("status") not in {"observed", "partial"}
        ):
            return None
        platform_index = _bounded_count(item.get("platform_index"), max(0, platform_count - 1))
        platform_device_index = _bounded_count(item.get("platform_device_index"), GPU_MAX_DEVICES - 1)
        if platform_count == 0 or platform_index is None or platform_device_index is None:
            return None
        text_values = {}
        for key in ("name", "vendor", "driver_version", "device_version", "opencl_c_version"):
            text_values[key] = _safe_label(item.get(key)) if item.get(key) is not None else None
            if item.get(key) is not None and text_values[key] is None:
                return None
        global_memory = _bounded_opencl_bytes(item.get("global_memory_bytes"))
        max_allocation = _bounded_opencl_bytes(item.get("max_allocation_bytes"))
        if (
            (item.get("global_memory_bytes") is not None and global_memory is None)
            or (item.get("max_allocation_bytes") is not None and max_allocation is None)
            or (
                global_memory is not None
                and max_allocation is not None
                and max_allocation > global_memory
            )
        ):
            return None
        address_bits = item.get("address_bits")
        if address_bits is not None and address_bits not in {16, 32, 64, 128}:
            return None
        booleans: dict[str, bool | None] = {}
        for key in ("available", "compiler_available", "unified_memory"):
            raw = item.get(key)
            if raw is not None and not isinstance(raw, bool):
                return None
            booleans[key] = raw
        normalized_devices.append(
            {
                "index": index,
                "platform_index": platform_index,
                "platform_device_index": platform_device_index,
                "status": item["status"],
                **text_values,
                "global_memory_bytes": global_memory,
                "max_allocation_bytes": max_allocation,
                "address_bits": address_bits,
                **booleans,
            }
        )
    reason_code = payload.get("reason_code")
    if reason_code is not None and reason_code != "opencl-probe-unavailable":
        return None
    derived_status = (
        "complete"
        if all(item["status"] == "observed" for item in (*normalized_platforms, *normalized_devices))
        else "partial"
    )
    if status == "unavailable":
        if platform_count or device_count or reason_code != "opencl-probe-unavailable":
            return None
    elif status != derived_status or reason_code is not None:
        return None
    return {
        "methodology_version": GPU_OPENCL_PROBE_VERSION,
        "status": status,
        "platform_count": platform_count,
        "device_count": device_count,
        "platforms": normalized_platforms,
        "devices": normalized_devices,
        "policy": expected_policy,
        **({"reason_code": reason_code} if reason_code else {}),
    }


def _opencl_probe() -> dict[str, Any]:
    command = [sys.executable, "-m", "cloudmark.gpu_opencl_probe"]
    environment = os.environ.copy()
    environment.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return _unavailable_opencl_probe()
    if result.returncode != 0 or result.stderr.strip():
        return _unavailable_opencl_probe()
    return parse_opencl_probe(result.stdout) or _unavailable_opencl_probe()


def collect_gpu_environment(
    *,
    drm_root: Path = Path("/sys/class/drm"),
    nvidia_smi: str | None = None,
    powershell: str | None = None,
    opencl_loader: str | None = None,
) -> dict[str, Any]:
    platform_name = platform.system()
    tools = {
        "nvidia_smi": bool(nvidia_smi or shutil.which("nvidia-smi")),
        "rocm_smi": shutil.which("rocm-smi") is not None,
        "clinfo": shutil.which("clinfo") is not None,
        "vulkaninfo": shutil.which("vulkaninfo") is not None,
        "vainfo": shutil.which("vainfo") is not None,
        "ffmpeg": shutil.which("ffmpeg") is not None,
    }
    discovered_opencl = (
        opencl_loader if opencl_loader is not None else ctypes.util.find_library("OpenCL")
    )
    tools["opencl_loader"] = bool(discovered_opencl)
    opencl = _opencl_probe() if discovered_opencl else _unavailable_opencl_probe()
    base: dict[str, Any] = {
        "methodology_version": GPU_ENVIRONMENT_VERSION,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform_name,
        "scope": "guest-visible-accelerator-inventory",
        "detection_status": "unavailable",
        "device_count": 0,
        "devices": [],
        "runtime_tools": tools,
        "opencl": opencl,
        "policy": {
            "read_only": True,
            "load_generated": False,
            "driver_configuration_changed": False,
            "framework_support_verified": False,
            "compute_performance_measured": False,
            "vram_bandwidth_measured": False,
            "media_performance_measured": False,
            "thermal_stability_measured": False,
            "physical_host_gpu_claim": False,
            "gpu_uuid_persisted": False,
            "serial_number_persisted": False,
            "iommu_group_identifier_persisted": False,
            "windows_pnp_instance_identifier_persisted": False,
            "nvidia_smi_inventory_queried": False,
            "windows_cim_queried": False,
            "raw_command_output_persisted": False,
            "opencl_probe_subprocess_isolated": True,
        },
    }
    if platform_name == "Windows":
        executable = powershell or shutil.which("powershell.exe") or shutil.which("powershell")
        if executable is None:
            return {
                **base,
                "evidence_status": "unavailable",
                "reason": "PowerShell is unavailable for the fixed Windows video-controller query.",
            }
        devices, valid = _windows_cim_inventory(executable)
        if not valid:
            return {
                **base,
                "evidence_status": "unavailable",
                "reason": "The bounded Windows video-controller query failed or returned malformed evidence.",
            }
        partial = any(device.get("status") != "observed" for device in devices)
        base["policy"]["windows_cim_queried"] = True
        return {
            **base,
            "evidence_status": "partial" if partial else "complete",
            "detection_status": "detected" if devices else "not-detected",
            "device_count": len(devices),
            "devices": devices,
            "truncated": False,
            **(
                {"reason": "One or more Windows video controllers exposed incomplete bounded identity."}
                if partial
                else {}
            ),
        }
    if platform_name != "Linux":
        return {
            **base,
            "evidence_status": "unavailable",
            "reason": "Bounded GPU hardware inventory currently supports Linux DRM sysfs and Windows CIM only.",
        }
    devices, truncated = _linux_gpu_devices(drm_root)
    executable = nvidia_smi or shutil.which("nvidia-smi")
    if executable and any(device.get("vendor_id") == "0x10de" for device in devices):
        _enrich_nvidia_devices(devices, _nvidia_smi_inventory(executable))
        base["policy"]["nvidia_smi_inventory_queried"] = True
    inaccessible = not drm_root.is_dir()
    if inaccessible:
        evidence_status = "unavailable"
        detection_status = "unavailable"
    elif truncated or any(device.get("status") != "observed" for device in devices):
        evidence_status = "partial"
        detection_status = "detected" if devices else "unavailable"
    else:
        evidence_status = "complete"
        detection_status = "detected" if devices else "not-detected"
    return {
        **base,
        "evidence_status": evidence_status,
        "detection_status": detection_status,
        "device_count": len(devices),
        "devices": devices,
        "truncated": truncated,
        **(
            {
                "reason": "Linux DRM sysfs was unavailable, truncated, or exposed incomplete device identity."
            }
            if evidence_status != "complete"
            else {}
        ),
    }
