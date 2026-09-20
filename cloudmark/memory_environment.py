from __future__ import annotations

import os
import platform
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MEMORY_ENVIRONMENT_VERSION = "memory-environment-v2"
MEMORY_ENVIRONMENT_MAX_BYTES = 4096
MEMORY_ENVIRONMENT_MEMINFO_MAX_BYTES = 65_536
MEMORY_ENVIRONMENT_MAX_NODES = 64
MEMORY_ENVIRONMENT_MAX_NODE_INDEX = 1023
MEMORY_ENVIRONMENT_MAX_CPU_INDEX = 8191
MEMORY_ENVIRONMENT_MAX_DISTANCE = 65_535


def _bounded_text(path: Path, maximum_bytes: int = MEMORY_ENVIRONMENT_MAX_BYTES) -> tuple[str | None, bool]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            value = handle.read(maximum_bytes + 1)
    except OSError:
        return None, False
    return value[:maximum_bytes].strip(), len(value) > maximum_bytes


def _parse_index_list(value: str, *, maximum_index: int, maximum_count: int) -> list[int] | None:
    normalized = value.strip()
    if not normalized:
        return []
    if not re.fullmatch(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*", normalized):
        return None
    indexes: set[int] = set()
    for item in normalized.split(","):
        bounds = item.split("-", 1)
        first = int(bounds[0])
        last = int(bounds[-1])
        if first > last or last > maximum_index or last - first + 1 > maximum_count:
            return None
        indexes.update(range(first, last + 1))
        if len(indexes) > maximum_count:
            return None
    return sorted(indexes)


def _parse_node_meminfo(value: str, node_id: int) -> dict[str, int | None]:
    fields: dict[str, int] = {}
    pattern = re.compile(rf"^Node\s+{node_id}\s+(MemTotal|MemFree):\s+(\d+)\s+kB$", re.MULTILINE)
    for match in pattern.finditer(value):
        fields[match.group(1)] = int(match.group(2)) * 1024
    return {
        "total_bytes": fields.get("MemTotal"),
        "free_bytes": fields.get("MemFree"),
    }


def _page_size_bytes() -> int | None:
    try:
        value = int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    return value if 0 < value <= 1024 * 1024 * 1024 else None


def _selected_policy(value: str | None) -> str | None:
    if value is None:
        return None
    selected = re.search(r"\[([a-z_+-]+)\]", value.lower())
    return selected.group(1).replace("+", "_") if selected else None


def _paging_evidence(
    proc_meminfo_path: Path,
    transparent_hugepage_root: Path,
    zswap_enabled_path: Path,
) -> dict[str, Any]:
    text, truncated = _bounded_text(proc_meminfo_path, MEMORY_ENVIRONMENT_MEMINFO_MAX_BYTES)
    byte_fields = {"SwapTotal", "SwapFree", "AnonHugePages", "Hugepagesize", "Hugetlb", "Zswap", "Zswapped"}
    count_fields = {"HugePages_Total", "HugePages_Free", "HugePages_Rsvd", "HugePages_Surp"}
    values: dict[str, int] = {}
    if text is not None:
        for line in text.splitlines():
            match = re.fullmatch(r"([A-Za-z_]+):\s+(\d+)(?:\s+kB)?", line.strip())
            if not match or match.group(1) not in byte_fields | count_fields:
                continue
            value = int(match.group(2))
            values[match.group(1)] = value * 1024 if match.group(1) in byte_fields else value
    swap_total = values.get("SwapTotal")
    swap_free = values.get("SwapFree")
    swap_used = max(0, swap_total - swap_free) if swap_total is not None and swap_free is not None else None
    thp_enabled_text, thp_enabled_truncated = _bounded_text(transparent_hugepage_root / "enabled")
    thp_defrag_text, thp_defrag_truncated = _bounded_text(transparent_hugepage_root / "defrag")
    thp_enabled = _selected_policy(thp_enabled_text)
    thp_defrag = _selected_policy(thp_defrag_text)
    zswap_text, zswap_truncated = _bounded_text(zswap_enabled_path)
    zswap_enabled = (
        zswap_text.strip().lower() in {"1", "y", "yes", "true"}
        if zswap_text is not None and zswap_text.strip().lower() in {"0", "1", "n", "no", "y", "yes", "false", "true"}
        else None
    )
    status = (
        "observed"
        if text is not None and not truncated and swap_total is not None and swap_free is not None
        else "partial" if text is not None else "unavailable"
    )
    result: dict[str, Any] = {
        "status": status,
        "source": "linux-procfs-and-sysfs",
        "snapshot_only": True,
        "pressure_measured": False,
        "swap_total_bytes": swap_total,
        "swap_free_bytes": swap_free,
        "swap_used_bytes": swap_used,
        "swap_used_percent": (
            round(swap_used * 100 / swap_total, 6) if swap_used is not None and swap_total else None
        ),
        "anonymous_huge_pages_bytes": values.get("AnonHugePages"),
        "hugetlb_bytes": values.get("Hugetlb"),
        "huge_page_size_bytes": values.get("Hugepagesize"),
        "huge_pages_total": values.get("HugePages_Total"),
        "huge_pages_free": values.get("HugePages_Free"),
        "huge_pages_reserved": values.get("HugePages_Rsvd"),
        "huge_pages_surplus": values.get("HugePages_Surp"),
        "zswap_pool_bytes": values.get("Zswap"),
        "zswapped_original_bytes": values.get("Zswapped"),
        "transparent_hugepage": {
            "status": "observed" if thp_enabled is not None or thp_defrag is not None else "unavailable",
            "enabled_policy": thp_enabled,
            "defrag_policy": thp_defrag,
            "truncated": thp_enabled_truncated or thp_defrag_truncated,
        },
        "zswap": {
            "status": "observed" if zswap_enabled is not None else "unavailable",
            "enabled": zswap_enabled,
            "truncated": zswap_truncated,
        },
        "truncated": truncated,
    }
    if status != "observed":
        result["reason"] = "Bounded Linux memory/paging snapshot fields were unavailable or incomplete."
    return result


def collect_memory_environment(
    *,
    sysfs_root: Path = Path("/sys/devices/system/node"),
    proc_meminfo_path: Path = Path("/proc/meminfo"),
    transparent_hugepage_root: Path = Path("/sys/kernel/mm/transparent_hugepage"),
    zswap_enabled_path: Path = Path("/sys/module/zswap/parameters/enabled"),
) -> dict[str, Any]:
    observed_at = datetime.now(timezone.utc).isoformat()
    base: dict[str, Any] = {
        "methodology_version": MEMORY_ENVIRONMENT_VERSION,
        "observed_at": observed_at,
        "platform": platform.system(),
        "scope": "guest-visible-linux-numa-topology",
        "source": "linux-sysfs",
        "page_size_bytes": _page_size_bytes(),
        "node_count": 0,
        "online_node_ids": [],
        "nodes": [],
        "numa_exposed": False,
        "remote_node_penalty_measured": False,
        "paging": {
            "status": "unavailable",
            "snapshot_only": True,
            "pressure_measured": False,
        },
        "policy": {
            "read_only": True,
            "guest_visible_only": True,
            "physical_host_placement_claim": False,
            "performance_claim": False,
        },
    }
    if platform.system() != "Linux":
        return {
            **base,
            "evidence_status": "unavailable",
            "reason": "Guest NUMA topology collection currently supports Linux only.",
        }
    base["paging"] = _paging_evidence(proc_meminfo_path, transparent_hugepage_root, zswap_enabled_path)
    if not sysfs_root.is_dir():
        return {
            **base,
            "evidence_status": "unavailable",
            "reason": "Linux did not expose the guest NUMA node sysfs directory.",
        }

    online_text, online_truncated = _bounded_text(sysfs_root / "online")
    online_nodes = (
        _parse_index_list(
            online_text,
            maximum_index=MEMORY_ENVIRONMENT_MAX_NODE_INDEX,
            maximum_count=MEMORY_ENVIRONMENT_MAX_NODES,
        )
        if online_text is not None and not online_truncated
        else None
    )
    discovered_nodes = sorted(
        int(path.name[4:])
        for path in sysfs_root.glob("node*")
        if path.is_dir()
        and path.name[4:].isdigit()
        and int(path.name[4:]) <= MEMORY_ENVIRONMENT_MAX_NODE_INDEX
    )
    node_ids = online_nodes if online_nodes is not None else discovered_nodes[:MEMORY_ENVIRONMENT_MAX_NODES]
    if not node_ids:
        return {
            **base,
            "evidence_status": "unavailable",
            "reason": "Linux exposed no bounded online NUMA nodes.",
        }

    partial = (
        online_nodes is None
        or len(discovered_nodes) > MEMORY_ENVIRONMENT_MAX_NODES
        or base["paging"]["status"] != "observed"
    )
    nodes: list[dict[str, Any]] = []
    for node_id in node_ids[:MEMORY_ENVIRONMENT_MAX_NODES]:
        node_root = sysfs_root / f"node{node_id}"
        cpu_text, cpu_truncated = _bounded_text(node_root / "cpulist")
        cpu_ids = (
            _parse_index_list(
                cpu_text,
                maximum_index=MEMORY_ENVIRONMENT_MAX_CPU_INDEX,
                maximum_count=MEMORY_ENVIRONMENT_MAX_CPU_INDEX + 1,
            )
            if cpu_text is not None and not cpu_truncated
            else None
        )
        meminfo_text, meminfo_truncated = _bounded_text(node_root / "meminfo")
        memory = _parse_node_meminfo(meminfo_text, node_id) if meminfo_text is not None else {
            "total_bytes": None,
            "free_bytes": None,
        }
        distance_text, distance_truncated = _bounded_text(node_root / "distance")
        distance_values = (
            [int(item) for item in distance_text.split()]
            if distance_text is not None
            and not distance_truncated
            and distance_text.split()
            and all(item.isdigit() for item in distance_text.split())
            else []
        )
        distances_valid = (
            len(distance_values) == len(node_ids)
            and all(0 < value <= MEMORY_ENVIRONMENT_MAX_DISTANCE for value in distance_values)
        )
        node_status = (
            "observed"
            if cpu_ids is not None
            and memory["total_bytes"] is not None
            and not meminfo_truncated
            and distances_valid
            else "partial"
        )
        if node_status == "partial":
            partial = True
        node: dict[str, Any] = {
            "node": node_id,
            "status": node_status,
            "cpu_list": cpu_text if cpu_ids is not None else None,
            "cpu_count": len(cpu_ids) if cpu_ids is not None else None,
            "memory_total_bytes": memory["total_bytes"],
            "memory_free_bytes": memory["free_bytes"],
            "distances": (
                [
                    {"target_node": target_node, "distance": distance}
                    for target_node, distance in zip(node_ids, distance_values)
                ]
                if distances_valid
                else []
            ),
        }
        if node_status == "partial":
            node["reason"] = "One or more bounded CPU, memory, or distance fields were unavailable or malformed."
        nodes.append(node)

    return {
        **base,
        "evidence_status": "partial" if partial else "complete",
        "node_count": len(nodes),
        "online_node_ids": list(node_ids),
        "nodes": nodes,
        "numa_exposed": len(nodes) > 1,
        "distance_interpretation": "relative-linux-numa-distance-not-latency",
        **(
            {"reason": "Guest NUMA topology was incomplete or exceeded a collection bound."}
            if partial
            else {}
        ),
    }
