from __future__ import annotations

import os
import platform
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


STORAGE_ENVIRONMENT_VERSION = "storage-environment-v1"
MOUNTINFO_MAX_BYTES = 1024 * 1024
MOUNTINFO_MAX_LINES = 4096
SYSFS_VALUE_MAX_BYTES = 256
SLAVE_DEVICE_MAX_COUNT = 16

_SAFE_MOUNT_FLAGS = {
    "async",
    "dirsync",
    "discard",
    "lazytime",
    "nodev",
    "noexec",
    "noatime",
    "nodiratime",
    "nosuid",
    "relatime",
    "ro",
    "rw",
    "strictatime",
    "sync",
}
_SAFE_MOUNT_PREFIXES = ("commit=", "data=", "errors=")
_NETWORK_FILESYSTEMS = {
    "9p",
    "ceph",
    "cifs",
    "davfs",
    "fuse.sshfs",
    "glusterfs",
    "lustre",
    "nfs",
    "nfs4",
    "smb3",
}
_MEMORY_FILESYSTEMS = {"hugetlbfs", "ramfs", "tmpfs"}
_SAFE_TEXT_PATTERN = re.compile(r"^[A-Za-z0-9 ._+:/()\[\]-]{1,128}$")
_DEVICE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _read_bounded(path: Path, limit: int) -> str | None:
    try:
        with path.open("rb") as handle:
            value = handle.read(limit + 1)
    except OSError:
        return None
    if len(value) > limit:
        return None
    try:
        return value.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None


def _decode_mount_field(value: str) -> str:
    replacements = {"040": " ", "011": "\t", "012": "\n", "134": "\\"}
    return re.sub(r"\\([0-7]{3})", lambda match: replacements.get(match.group(1), match.group(0)), value)


def _safe_mount_options(*values: str) -> list[str]:
    options: set[str] = set()
    for value in values:
        for item in value.split(",")[:128]:
            normalized = item.strip().lower()
            if normalized in _SAFE_MOUNT_FLAGS or normalized.startswith(_SAFE_MOUNT_PREFIXES):
                if len(normalized) <= 64:
                    options.add(normalized)
    return sorted(options)[:32]


def parse_mountinfo(value: str) -> list[dict[str, Any]]:
    if len(value.encode("utf-8")) > MOUNTINFO_MAX_BYTES:
        return []
    entries: list[dict[str, Any]] = []
    for line in value.splitlines()[:MOUNTINFO_MAX_LINES]:
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if separator < 6 or len(fields) < separator + 4:
            continue
        major_minor = fields[2]
        if not re.fullmatch(r"\d{1,7}:\d{1,7}", major_minor):
            continue
        mount_point = _decode_mount_field(fields[4])
        filesystem = fields[separator + 1].lower()
        source = _decode_mount_field(fields[separator + 2])
        if (
            not mount_point.startswith("/")
            or len(mount_point.encode("utf-8")) > 1024
            or any(ord(character) < 32 for character in mount_point)
            or not re.fullmatch(r"[a-z0-9._+-]{1,64}", filesystem)
        ):
            continue
        entries.append({
            "major_minor": major_minor,
            "mount_point": mount_point,
            "filesystem_type": filesystem,
            "source": source if len(source.encode("utf-8")) <= 4096 else "",
            "mount_options": _safe_mount_options(fields[5], fields[separator + 3]),
        })
    return entries


def select_workspace_mount(
    entries: list[dict[str, Any]],
    workspace: str,
    major_minor: str,
) -> dict[str, Any] | None:
    workspace_path = PurePosixPath(workspace)
    candidates: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("major_minor") != major_minor:
            continue
        try:
            workspace_path.relative_to(PurePosixPath(str(entry["mount_point"])))
        except ValueError:
            continue
        candidates.append(entry)
    return max(candidates, key=lambda entry: len(str(entry["mount_point"])), default=None)


def _source_class(filesystem: str, source: str, sysfs_device_exists: bool) -> str:
    if filesystem in _NETWORK_FILESYSTEMS:
        return "network-filesystem"
    if filesystem in _MEMORY_FILESYSTEMS:
        return "memory-filesystem"
    if filesystem == "overlay":
        return "overlay-filesystem"
    if source.startswith("/dev/") or sysfs_device_exists:
        return "block-device"
    return "virtual-or-unknown"


def _safe_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized if _SAFE_TEXT_PATTERN.fullmatch(normalized) else None


def _bounded_integer(path: Path, maximum: int) -> int | None:
    value = _read_bounded(path, SYSFS_VALUE_MAX_BYTES)
    if value is None or not value.isdigit():
        return None
    parsed = int(value)
    return parsed if 0 <= parsed <= maximum else None


def _scheduler(path: Path) -> dict[str, Any]:
    value = _read_bounded(path, SYSFS_VALUE_MAX_BYTES)
    if not value:
        return {"status": "unavailable", "selected": None, "available": []}
    tokens = value.split()
    available: list[str] = []
    selected: str | None = None
    for token in tokens[:16]:
        is_selected = token.startswith("[") and token.endswith("]")
        name = token.strip("[]")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name):
            continue
        available.append(name)
        if is_selected:
            selected = name
    if selected is None and len(available) == 1:
        selected = available[0]
    return {
        "status": "observed" if selected else "partial",
        "selected": selected,
        "available": available,
    }


def _device_type(name: str) -> str:
    if name.startswith("nvme"):
        return "nvme"
    if name.startswith("vd"):
        return "virtio-block"
    if name.startswith("xvd"):
        return "xen-block"
    if name.startswith("sd"):
        return "scsi-or-virtual-scsi"
    if name.startswith("dm-"):
        return "device-mapper"
    if name.startswith("md"):
        return "software-raid"
    if name.startswith("loop"):
        return "loop"
    return "other"


def _block_root(device_path: Path) -> tuple[Path, str, str | None] | None:
    parts = device_path.parts
    try:
        block_index = parts.index("block")
        disk_name = parts[block_index + 1]
    except (ValueError, IndexError):
        return None
    if not _DEVICE_NAME_PATTERN.fullmatch(disk_name):
        return None
    disk_root = Path(*parts[: block_index + 2])
    leaf_name = device_path.name
    partition = leaf_name if leaf_name != disk_name and _DEVICE_NAME_PATTERN.fullmatch(leaf_name) else None
    return disk_root, disk_name, partition


def _collect_block_device_from_path(resolved: Path) -> dict[str, Any]:
    root_shape = _block_root(resolved)
    if root_shape is None:
        return {"status": "partial", "reason": "The sysfs block-device path shape is not recognized."}
    disk_root, disk_name, partition = root_shape
    queue = disk_root / "queue"
    rotational = _bounded_integer(queue / "rotational", 1)
    slave_names: list[str] = []
    try:
        for child in sorted((disk_root / "slaves").iterdir(), key=lambda item: item.name)[:SLAVE_DEVICE_MAX_COUNT]:
            if _DEVICE_NAME_PATTERN.fullmatch(child.name):
                slave_names.append(child.name)
    except OSError:
        pass
    result = {
        "status": "observed",
        "kernel_name": disk_name,
        "partition_name": partition,
        "device_type": _device_type(disk_name),
        "vendor": _safe_text(_read_bounded(disk_root / "device" / "vendor", SYSFS_VALUE_MAX_BYTES)),
        "model": _safe_text(_read_bounded(disk_root / "device" / "model", SYSFS_VALUE_MAX_BYTES)),
        "scheduler": _scheduler(queue / "scheduler"),
        "rotational": bool(rotational) if rotational is not None else None,
        "logical_block_size_bytes": _bounded_integer(queue / "logical_block_size", 16 * 1024 * 1024),
        "physical_block_size_bytes": _bounded_integer(queue / "physical_block_size", 16 * 1024 * 1024),
        "minimum_io_size_bytes": _bounded_integer(queue / "minimum_io_size", 1024 * 1024 * 1024),
        "optimal_io_size_bytes": _bounded_integer(queue / "optimal_io_size", 1024 * 1024 * 1024),
        "read_ahead_kib": _bounded_integer(queue / "read_ahead_kb", 1024 * 1024),
        "request_queue_depth": _bounded_integer(queue / "nr_requests", 1024 * 1024),
        "discard_max_bytes": _bounded_integer(queue / "discard_max_bytes", 2**63 - 1),
        "write_cache": _safe_text(_read_bounded(queue / "write_cache", SYSFS_VALUE_MAX_BYTES)),
        "zoned": _safe_text(_read_bounded(queue / "zoned", SYSFS_VALUE_MAX_BYTES)),
        "stacked_slave_devices": slave_names,
        "stacked_slave_count": len(slave_names),
    }
    observed_fields = sum(
        result[key] is not None
        for key in ("rotational", "logical_block_size_bytes", "physical_block_size_bytes", "read_ahead_kib")
    )
    if observed_fields == 0:
        result["status"] = "partial"
        result["reason"] = "The device is visible, but bounded queue attributes are unavailable."
    return result


def collect_linux_block_device(major_minor: str, sys_dev_block: Path = Path("/sys/dev/block")) -> dict[str, Any]:
    link = sys_dev_block / major_minor
    try:
        resolved = link.resolve(strict=True)
    except OSError:
        return {"status": "unavailable", "reason": "No guest-visible sysfs block device maps to the workspace mount."}
    return _collect_block_device_from_path(resolved)


def _unavailable(platform_name: str, reason: str) -> dict[str, Any]:
    return {
        "methodology_version": STORAGE_ENVIRONMENT_VERSION,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform_name,
        "evidence_status": "unavailable",
        "mount": {"status": "unavailable", "reason": reason},
        "block_device": {"status": "unavailable", "reason": reason},
        "policy": {
            "read_only": True,
            "guest_visible_only": True,
            "physical_device_claim": False,
            "raw_source_persisted": False,
        },
    }


def _collect_storage_environment(
    workspace: Path,
    *,
    mountinfo_path: Path = Path("/proc/self/mountinfo"),
    sys_dev_block: Path = Path("/sys/dev/block"),
) -> dict[str, Any]:
    platform_name = platform.system()
    if platform_name != "Linux":
        return _unavailable(platform_name, "Detailed storage-environment evidence currently requires Linux procfs and sysfs.")
    workspace = workspace.resolve()
    mountinfo = _read_bounded(mountinfo_path, MOUNTINFO_MAX_BYTES)
    if mountinfo is None:
        return _unavailable(platform_name, "Bounded Linux mount information is unavailable.")
    try:
        device_number = workspace.stat().st_dev
        major_minor = f"{os.major(device_number)}:{os.minor(device_number)}"
    except OSError:
        return _unavailable(platform_name, "The workspace filesystem identity could not be observed.")
    mount = select_workspace_mount(parse_mountinfo(mountinfo), str(workspace), major_minor)
    if mount is None:
        return _unavailable(platform_name, "No bounded mount record matches the workspace filesystem.")
    block_device = collect_linux_block_device(major_minor, sys_dev_block)
    source_class = _source_class(
        str(mount["filesystem_type"]),
        str(mount.pop("source")),
        block_device.get("status") in {"observed", "partial"},
    )
    mount_result = {
        "status": "observed",
        "mount_point": mount["mount_point"],
        "filesystem_type": mount["filesystem_type"],
        "source_class": source_class,
        "mount_options": mount["mount_options"],
        "major_minor": major_minor,
    }
    block_required = source_class == "block-device"
    evidence_status = "complete" if not block_required or block_device.get("status") == "observed" else "partial"
    return {
        "methodology_version": STORAGE_ENVIRONMENT_VERSION,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform_name,
        "evidence_status": evidence_status,
        "mount": mount_result,
        "block_device": block_device,
        "policy": {
            "read_only": True,
            "guest_visible_only": True,
            "physical_device_claim": False,
            "raw_source_persisted": False,
        },
    }


def collect_storage_environment(
    workspace: Path,
    *,
    mountinfo_path: Path = Path("/proc/self/mountinfo"),
    sys_dev_block: Path = Path("/sys/dev/block"),
) -> dict[str, Any]:
    try:
        return _collect_storage_environment(
            workspace,
            mountinfo_path=mountinfo_path,
            sys_dev_block=sys_dev_block,
        )
    except (IndexError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        return _unavailable(platform.system(), "Storage-environment collection failed closed without affecting the benchmark.")
