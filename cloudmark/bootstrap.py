from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any


PACKAGES = {
    "base": {
        "apt": ["curl", "jq", "pciutils", "dmidecode", "sysstat", "numactl"],
        "dnf": ["curl", "jq", "pciutils", "dmidecode", "sysstat", "numactl"],
        "zypper": ["curl", "jq", "pciutils", "dmidecode", "sysstat", "numactl"],
    },
    "storage": {
        "apt": ["fio", "smartmontools", "nvme-cli"],
        "dnf": ["fio", "smartmontools", "nvme-cli"],
        "zypper": ["fio", "smartmontools", "nvme-cli"],
    },
    "network": {
        "apt": ["iperf3", "ethtool", "mtr-tiny", "dnsutils"],
        "dnf": ["iperf3", "ethtool", "mtr", "bind-utils"],
        "zypper": ["iperf3", "ethtool", "mtr", "bind-utils"],
    },
    "compute": {
        "apt": ["sysbench"],
        "dnf": ["sysbench"],
        "zypper": ["sysbench"],
    },
    "memory": {
        "apt": ["gcc", "libgomp1"],
        "dnf": ["gcc", "libgomp"],
        "zypper": ["gcc", "libgomp1"],
    },
    "database": {
        "apt": ["sysbench", "postgresql", "postgresql-contrib", "redis-server"],
        "dnf": ["sysbench", "postgresql-server", "redis"],
        "zypper": ["sysbench", "postgresql-server", "redis"],
    },
    "web": {
        "apt": ["nginx", "apache2-utils"],
        "dnf": ["nginx", "httpd-tools"],
        "zypper": ["nginx", "apache2-utils"],
    },
}


@dataclass(frozen=True)
class BootstrapPlan:
    manager: str
    packs: list[str]
    packages: list[str]
    commands: list[list[str]]
    requires_admin: bool
    notes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "manager": self.manager,
            "packs": self.packs,
            "packages": self.packages,
            "commands": self.commands,
            "requires_admin": self.requires_admin,
            "notes": self.notes,
        }


def detect_manager() -> str:
    if os.name == "nt":
        return "winget" if shutil.which("winget") else "windows-bundle"
    for executable, manager in (("apt-get", "apt"), ("zypper", "zypper"), ("dnf", "dnf"), ("yum", "dnf")):
        if shutil.which(executable):
            return manager
    return "unsupported"


def create_plan(packs: list[str]) -> BootstrapPlan:
    manager = detect_manager()
    normalized = list(dict.fromkeys(["base", *packs]))
    packages: list[str] = []
    notes: list[str] = []
    for pack in normalized:
        if pack not in PACKAGES:
            raise ValueError(f"Unknown bootstrap pack: {pack}")
        packages.extend(PACKAGES[pack].get(manager, []))
    packages = list(dict.fromkeys(packages))
    commands: list[list[str]] = []
    if manager == "apt":
        commands = [["apt-get", "update"], ["apt-get", "install", "-y", *packages]]
    elif manager == "dnf":
        commands = [["dnf", "install", "-y", *packages]]
    elif manager == "zypper":
        commands = [["zypper", "--non-interactive", "refresh"], ["zypper", "--non-interactive", "install", *packages]]
        notes.append("SLES may require an active SUSE registration or the offline CloudMark tool bundle.")
    elif manager == "winget":
        notes.append("Windows uses winget plus portable CloudMark tool bundles; benchmark package mapping is not yet automatic.")
    elif manager == "windows-bundle":
        notes.append("winget is unavailable; use the signed CloudMark Windows tool bundle.")
    else:
        notes.append(f"No supported package manager detected on {platform.platform()}.")
    return BootstrapPlan(manager, normalized, packages, commands, True, notes)


def execute_plan(plan: BootstrapPlan) -> list[dict[str, Any]]:
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() != 0:
        raise PermissionError("Bootstrap requires root. Re-run with sudo.")
    results: list[dict[str, Any]] = []
    for command in plan.commands:
        completed = subprocess.run(command, check=False)
        results.append({"command": command, "returncode": completed.returncode})
        if completed.returncode != 0:
            raise RuntimeError(f"Bootstrap command failed: {' '.join(command)}")
    return results
