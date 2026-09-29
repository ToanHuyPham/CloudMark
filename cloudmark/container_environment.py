from __future__ import annotations

import platform
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONTAINER_ENVIRONMENT_VERSION = "container-environment-v1"
CONTAINER_CGROUP_MAX_BYTES = 4_096
CONTAINER_MOUNTINFO_MAX_BYTES = 65_536
CONTAINER_ROOT_FILESYSTEMS = {"overlay", "fuse.overlayfs"}


def _bounded_text(path: Path, maximum_bytes: int) -> tuple[str | None, bool]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            value = handle.read(maximum_bytes + 1)
    except OSError:
        return None, False
    return value[:maximum_bytes], len(value) > maximum_bytes


def _cgroup_hints(*values: str | None) -> tuple[list[str], list[str], str]:
    text = "\n".join(value.lower() for value in values if value is not None)
    runtimes: set[str] = set()
    orchestrators: set[str] = set()
    if re.search(r"(?:^|[/.-])docker(?:[/.-]|$)", text):
        runtimes.add("docker")
    if "libpod" in text:
        runtimes.add("podman")
    if "containerd" in text:
        runtimes.add("containerd")
    if re.search(r"(?:^|[/.-])crio(?:[/.-]|$)", text):
        runtimes.add("cri-o")
    if re.search(r"(?:^|/)lxc(?:[./-]|/)", text):
        runtimes.add("lxc")
    if "machine.slice/machine-" in text:
        runtimes.add("systemd-nspawn")
    if "kubepods" in text:
        orchestrators.add("kubernetes")
    lines = [line for value in values if value is not None for line in value.splitlines()]
    if any(line.startswith("0::") for line in lines):
        cgroup_version = "v2"
    elif any(len(line.split(":", 2)) == 3 for line in lines):
        cgroup_version = "v1"
    else:
        cgroup_version = "unknown"
    return sorted(runtimes), sorted(orchestrators), cgroup_version


def _root_filesystem(mountinfo: str | None) -> str | None:
    if mountinfo is None:
        return None
    for line in mountinfo.splitlines():
        left, separator, right = line.partition(" - ")
        left_fields = left.split()
        right_fields = right.split()
        if separator and len(left_fields) >= 5 and left_fields[4] == "/" and right_fields:
            filesystem = right_fields[0].lower()
            return filesystem if re.fullmatch(r"[a-z0-9_.+-]{1,64}", filesystem) else None
    return None


def collect_container_environment(
    *,
    proc_root: Path = Path("/proc"),
    filesystem_root: Path = Path("/"),
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "methodology_version": CONTAINER_ENVIRONMENT_VERSION,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.system(),
        "scope": "current-cloudmark-process-guest-container-context",
        "container_status": "unavailable",
        "container_detected": False,
        "runtime_hints": [],
        "orchestrator_hints": [],
        "cgroup_version": "unknown",
        "root_filesystem": None,
        "root_filesystem_overlay_like": False,
        "markers": {
            "docker": False,
            "podman": False,
        },
        "policy": {
            "read_only": True,
            "runtime_engine_queried": False,
            "kubernetes_api_queried": False,
            "container_identifier_persisted": False,
            "raw_cgroup_path_persisted": False,
            "raw_mountinfo_persisted": False,
            "host_boundary_verified": False,
            "absence_proves_host_execution": False,
        },
    }
    if platform.system() != "Linux":
        return {
            **base,
            "evidence_status": "unavailable",
            "reason": "Container environment collection currently supports Linux procfs only.",
        }

    pid1_cgroup, pid1_truncated = _bounded_text(proc_root / "1" / "cgroup", CONTAINER_CGROUP_MAX_BYTES)
    self_cgroup, self_truncated = _bounded_text(proc_root / "self" / "cgroup", CONTAINER_CGROUP_MAX_BYTES)
    mountinfo, mountinfo_truncated = _bounded_text(
        proc_root / "self" / "mountinfo",
        CONTAINER_MOUNTINFO_MAX_BYTES,
    )
    docker_marker = (filesystem_root / ".dockerenv").is_file()
    podman_marker = (filesystem_root / "run" / ".containerenv").is_file()
    runtimes, orchestrators, cgroup_version = _cgroup_hints(
        None if pid1_truncated else pid1_cgroup,
        None if self_truncated else self_cgroup,
    )
    if docker_marker:
        runtimes = sorted(set(runtimes) | {"docker"})
    if podman_marker:
        runtimes = sorted(set(runtimes) | {"podman"})
    root_filesystem = _root_filesystem(None if mountinfo_truncated else mountinfo)
    overlay_like = root_filesystem in CONTAINER_ROOT_FILESYSTEMS
    partial = (
        pid1_cgroup is None
        or self_cgroup is None
        or mountinfo is None
        or pid1_truncated
        or self_truncated
        or mountinfo_truncated
    )
    detected = bool(runtimes or orchestrators)
    if detected:
        status = "detected"
    elif overlay_like:
        status = "suspected"
    elif partial:
        status = "unavailable"
    else:
        status = "not-detected"
    return {
        **base,
        "evidence_status": "partial" if partial else "complete",
        "container_status": status,
        "container_detected": detected,
        "runtime_hints": runtimes,
        "orchestrator_hints": orchestrators,
        "cgroup_version": cgroup_version,
        "root_filesystem": root_filesystem,
        "root_filesystem_overlay_like": overlay_like,
        "markers": {
            "docker": docker_marker,
            "podman": podman_marker,
        },
        "sources": {
            "pid1_cgroup": {
                "status": "observed" if pid1_cgroup is not None and not pid1_truncated else "unavailable",
                "truncated": pid1_truncated,
            },
            "self_cgroup": {
                "status": "observed" if self_cgroup is not None and not self_truncated else "unavailable",
                "truncated": self_truncated,
            },
            "self_mountinfo": {
                "status": "observed" if mountinfo is not None and not mountinfo_truncated else "unavailable",
                "truncated": mountinfo_truncated,
            },
            "fixed_markers": {"status": "observed"},
        },
        **(
            {"reason": "One or more bounded Linux container evidence sources were unavailable or truncated."}
            if partial
            else {}
        ),
    }
