from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

from .tooling import find_postgres_binary, find_web_binary, postgres_tool_supports, web_tool_supports


def _run(command: list[str], timeout: float = 3.0) -> str | None:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _memory_bytes() -> int | None:
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("avail_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("avail_virtual", ctypes.c_ulonglong),
                ("avail_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.total_phys)
        return None

    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for line in meminfo.read_text(errors="replace").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    return None


def _cpu_model() -> str:
    if os.name == "nt":
        value = _run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name)",
            ]
        )
        return value or platform.processor() or "Unknown"
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(errors="replace").splitlines():
            if line.lower().startswith(("model name", "hardware")):
                return line.split(":", 1)[1].strip()
    return platform.processor() or "Unknown"


def _virtualization() -> dict[str, Any]:
    if os.name == "nt":
        raw = _run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,HypervisorPresent | ConvertTo-Json -Compress",
            ]
        )
        if raw:
            try:
                value = json.loads(raw)
                return {
                    "type": "virtual" if value.get("HypervisorPresent") else "physical-or-undetected",
                    "manufacturer": value.get("Manufacturer"),
                    "model": value.get("Model"),
                }
            except json.JSONDecodeError:
                pass
    else:
        detected = _run(["systemd-detect-virt"], timeout=1.0)
        if detected:
            return {"type": "virtual" if detected != "none" else "physical", "technology": detected}
    return {"type": "unknown"}


def _disks(workspace: Path) -> list[dict[str, Any]]:
    disks: list[dict[str, Any]] = []
    if os.name == "nt":
        raw = _run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Get-Volume | Where-Object DriveLetter | Select-Object DriveLetter,FileSystemLabel,FileSystem,Size,SizeRemaining,HealthStatus | ConvertTo-Json -Compress",
            ],
            timeout=6.0,
        )
        if raw:
            try:
                values = json.loads(raw)
                if isinstance(values, dict):
                    values = [values]
                for value in values:
                    disks.append(
                        {
                            "name": f"{value.get('DriveLetter')}:\\",
                            "label": value.get("FileSystemLabel") or None,
                            "filesystem": value.get("FileSystem") or None,
                            "size_bytes": value.get("Size"),
                            "free_bytes": value.get("SizeRemaining"),
                            "health": value.get("HealthStatus") or "Unknown",
                        }
                    )
            except (json.JSONDecodeError, TypeError):
                pass
    else:
        raw = _run(
            [
                "lsblk",
                "--json",
                "--bytes",
                "--output",
                "NAME,TYPE,SIZE,MODEL,ROTA,FSTYPE,MOUNTPOINTS,TRAN",
            ],
            timeout=5.0,
        )
        if raw:
            try:
                values = json.loads(raw).get("blockdevices", [])
                for value in values:
                    disks.append(value)
            except json.JSONDecodeError:
                pass
    if not disks:
        usage = shutil.disk_usage(workspace)
        disks.append(
            {
                "name": str(workspace.anchor or workspace),
                "filesystem": "unknown",
                "size_bytes": usage.total,
                "free_bytes": usage.free,
                "health": "Unknown",
            }
        )
    return disks


def _network_addresses() -> list[dict[str, str]]:
    addresses: set[tuple[str, str]] = set()
    try:
        for family, _, _, _, sockaddr in socket.getaddrinfo(socket.gethostname(), None):
            if family == socket.AF_INET:
                addresses.add(("ipv4", sockaddr[0]))
            elif family == socket.AF_INET6:
                addresses.add(("ipv6", sockaddr[0].split("%", 1)[0]))
    except socket.gaierror:
        pass
    return [{"family": family, "address": address} for family, address in sorted(addresses)]


def collect_inventory(workspace: Path | None = None) -> dict[str, Any]:
    workspace = (workspace or Path.cwd()).resolve()
    uname = platform.uname()
    nginx = find_web_binary("nginx")
    curl = find_web_binary("curl")
    pgbench = find_postgres_binary("pgbench")
    return {
        "hostname": socket.gethostname(),
        "os": {
            "system": uname.system,
            "release": uname.release,
            "version": uname.version,
            "distribution": platform.platform(),
            "architecture": platform.machine(),
        },
        "cpu": {
            "model": _cpu_model(),
            "logical_cores": os.cpu_count() or 1,
        },
        "memory": {"total_bytes": _memory_bytes()},
        "virtualization": _virtualization(),
        "disks": _disks(workspace),
        "network": {"addresses": _network_addresses()},
        "capabilities": {
            "fio": shutil.which("fio") is not None,
            "iperf3": shutil.which("iperf3") is not None,
            "iproute2": shutil.which("ip") is not None,
            "tracepath": shutil.which("tracepath") is not None,
            "dig": shutil.which("dig") is not None,
            "ethtool": shutil.which("ethtool") is not None,
            "tcp_congestion_control": Path("/proc/sys/net/ipv4/tcp_congestion_control").is_file(),
            "postgres": find_postgres_binary("postgres") is not None,
            "initdb": find_postgres_binary("initdb") is not None,
            "pgbench": pgbench is not None,
            "pgbench_latency_log": bool(
                pgbench and postgres_tool_supports("pgbench", pgbench, "transaction-log")
            ),
            "pg_isready": find_postgres_binary("pg_isready") is not None,
            "nginx": nginx is not None,
            "nginx_http2": bool(nginx and web_tool_supports("nginx", nginx, "http2")),
            "ab": find_web_binary("ab") is not None,
            "curl": curl is not None,
            "curl_http2": bool(curl and web_tool_supports("curl", curl, "http2")),
            "openssl": find_web_binary("openssl") is not None,
            "procfs_process_cpu": Path("/proc/stat").is_file() and Path("/proc/self/stat").is_file(),
            "sysbench": shutil.which("sysbench") is not None,
            "gcc": shutil.which("gcc") is not None,
            "docker": shutil.which("docker") is not None,
            "podman": shutil.which("podman") is not None,
        },
    }
