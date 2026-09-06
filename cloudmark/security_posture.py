from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Any, Callable


SECURITY_POSTURE_VERSION = "linux-security-posture-v1"
SECURITY_CONTROL_MAX_BYTES = 4096


def _integer_parser(
    labels: dict[int, str],
    *,
    minimum: int = 0,
    maximum: int = 4,
) -> Callable[[bytes], dict[str, Any]]:
    def parse(raw: bytes) -> dict[str, Any]:
        text = raw.decode("ascii", errors="strict").strip()
        value = int(text)
        if not minimum <= value <= maximum:
            raise ValueError("integer control is outside its documented range")
        return {"value": value, "classification": labels.get(value, "implementation-defined")}

    return parse


def _boolean_parser(enabled_label: str, disabled_label: str) -> Callable[[bytes], dict[str, Any]]:
    return _integer_parser({0: disabled_label, 1: enabled_label}, maximum=1)


def _lsm_parser(raw: bytes) -> dict[str, Any]:
    values = [value.strip() for value in raw.decode("ascii", errors="strict").strip().split(",") if value.strip()]
    if not values or len(values) > 32 or any(not value.replace("-", "").replace("_", "").isalnum() for value in values):
        raise ValueError("LSM list is invalid or outside its bound")
    return {
        "active_modules": values,
        "module_count": len(values),
        "mandatory_access_control_visible": any(value in {"apparmor", "selinux", "smack"} for value in values),
    }


def _apparmor_parser(raw: bytes) -> dict[str, Any]:
    value = raw.decode("ascii", errors="strict").strip().upper()
    if value not in {"Y", "N"}:
        raise ValueError("AppArmor enablement value is invalid")
    return {"enabled": value == "Y", "classification": "enabled" if value == "Y" else "disabled"}


def _lockdown_parser(raw: bytes) -> dict[str, Any]:
    values = raw.decode("ascii", errors="strict").strip().split()
    selected = next((value[1:-1] for value in values if value.startswith("[") and value.endswith("]")), None)
    if selected not in {"none", "integrity", "confidentiality"}:
        raise ValueError("kernel lockdown mode is invalid")
    return {"mode": selected}


def _core_pattern_parser(raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="strict").strip()
    if not text:
        raise ValueError("core pattern is empty")
    return {
        "classification": "pipe-handler" if text.startswith("|") else "filesystem-pattern",
        "raw_pattern_persisted": False,
    }


def _cgroup_parser(raw: bytes) -> dict[str, Any]:
    controllers = raw.decode("ascii", errors="strict").strip().split()
    if len(controllers) > 64 or any(not value.replace("_", "").isalnum() for value in controllers):
        raise ValueError("cgroup controller list is invalid")
    return {"version": 2, "controllers": controllers, "controller_count": len(controllers)}


CONTROL_SPECS: tuple[tuple[str, str, Callable[[bytes], dict[str, Any]]], ...] = (
    (
        "aslr",
        "proc/sys/kernel/randomize_va_space",
        _integer_parser({0: "disabled", 1: "conservative", 2: "full"}, maximum=2),
    ),
    (
        "ptrace_scope",
        "proc/sys/kernel/yama/ptrace_scope",
        _integer_parser(
            {0: "classic", 1: "restricted", 2: "admin-only", 3: "disabled-after-boot"},
            maximum=3,
        ),
    ),
    ("protected_hardlinks", "proc/sys/fs/protected_hardlinks", _boolean_parser("enabled", "disabled")),
    ("protected_symlinks", "proc/sys/fs/protected_symlinks", _boolean_parser("enabled", "disabled")),
    (
        "protected_fifos",
        "proc/sys/fs/protected_fifos",
        _integer_parser({0: "disabled", 1: "owner-protected", 2: "all-writers-protected"}, maximum=2),
    ),
    (
        "protected_regular",
        "proc/sys/fs/protected_regular",
        _integer_parser({0: "disabled", 1: "owner-protected", 2: "all-writers-protected"}, maximum=2),
    ),
    (
        "unprivileged_bpf",
        "proc/sys/kernel/unprivileged_bpf_disabled",
        _integer_parser({0: "enabled", 1: "disabled", 2: "disabled-until-reboot"}, maximum=2),
    ),
    (
        "kernel_pointer_restriction",
        "proc/sys/kernel/kptr_restrict",
        _integer_parser({0: "unrestricted", 1: "privileged", 2: "hidden"}, maximum=2),
    ),
    ("dmesg_restriction", "proc/sys/kernel/dmesg_restrict", _boolean_parser("restricted", "unrestricted")),
    (
        "perf_event_scope",
        "proc/sys/kernel/perf_event_paranoid",
        _integer_parser(
            {-1: "unrestricted", 0: "raw-trace-restricted", 1: "cpu-events-restricted", 2: "kernel-restricted", 3: "userspace-only", 4: "disabled"},
            minimum=-1,
            maximum=4,
        ),
    ),
    ("core_pattern", "proc/sys/kernel/core_pattern", _core_pattern_parser),
    ("linux_security_modules", "sys/kernel/security/lsm", _lsm_parser),
    ("apparmor", "sys/module/apparmor/parameters/enabled", _apparmor_parser),
    ("selinux_enforcing", "sys/fs/selinux/enforce", _boolean_parser("enforcing", "permissive")),
    ("kernel_lockdown", "sys/kernel/security/lockdown", _lockdown_parser),
    ("fips", "proc/sys/crypto/fips_enabled", _boolean_parser("enabled", "disabled")),
    ("cgroup_v2", "sys/fs/cgroup/cgroup.controllers", _cgroup_parser),
)


def _read_control(root: Path, relative_path: str, parser: Callable[[bytes], dict[str, Any]]) -> dict[str, Any]:
    source = f"/{relative_path.replace(os.sep, '/')}"
    path = root.joinpath(*relative_path.split("/"))
    if path.is_symlink():
        return {"status": "unavailable", "source": source, "reason": "Symbolic-link control paths are refused."}
    try:
        with path.open("rb") as handle:
            raw = handle.read(SECURITY_CONTROL_MAX_BYTES + 1)
    except OSError:
        return {"status": "unavailable", "source": source, "reason": "Control file is unavailable."}
    if len(raw) > SECURITY_CONTROL_MAX_BYTES:
        return {"status": "unavailable", "source": source, "reason": "Control file exceeds the read bound."}
    try:
        value = parser(raw)
    except (UnicodeError, ValueError):
        return {"status": "unavailable", "source": source, "reason": "Control value is malformed or unsupported."}
    return {"status": "observed", "source": source, "read_only": True, **value}


def _secure_boot(root: Path) -> dict[str, Any]:
    directory = root / "sys" / "firmware" / "efi" / "efivars"
    try:
        paths = sorted(directory.glob("SecureBoot-*"))[:9]
    except OSError:
        paths = []
    if not paths:
        return {
            "status": "unavailable",
            "source": "/sys/firmware/efi/efivars/SecureBoot-*",
            "reason": "Secure Boot EFI variable is unavailable.",
        }
    if len(paths) > 8:
        return {
            "status": "unavailable",
            "source": "/sys/firmware/efi/efivars/SecureBoot-*",
            "reason": "Secure Boot EFI variable count exceeds the bound.",
        }
    path = paths[0]
    if path.is_symlink():
        return {
            "status": "unavailable",
            "source": "/sys/firmware/efi/efivars/SecureBoot-*",
            "reason": "Symbolic-link EFI variables are refused.",
        }
    try:
        with path.open("rb") as handle:
            raw = handle.read(6)
    except OSError:
        raw = b""
    if len(raw) != 5 or raw[4] not in {0, 1}:
        return {
            "status": "unavailable",
            "source": "/sys/firmware/efi/efivars/SecureBoot-*",
            "reason": "Secure Boot EFI variable is malformed.",
        }
    return {
        "status": "observed",
        "source": "/sys/firmware/efi/efivars/SecureBoot-*",
        "read_only": True,
        "enabled": raw[4] == 1,
        "variable_identifier_persisted": False,
    }


def collect_linux_security_posture(
    root: Path = Path("/"),
    *,
    platform_name: str | None = None,
) -> dict[str, Any]:
    system = platform_name or platform.system()
    controls: dict[str, dict[str, Any]] = {}
    if system != "Linux":
        for name, relative_path, _parser in CONTROL_SPECS:
            controls[name] = {
                "status": "unavailable",
                "source": f"/{relative_path}",
                "reason": "This control is currently implemented only for Linux.",
            }
        controls["secure_boot"] = {
            "status": "unavailable",
            "source": "/sys/firmware/efi/efivars/SecureBoot-*",
            "reason": "This control is currently implemented only for Linux.",
        }
    else:
        resolved_root = root.resolve()
        for name, relative_path, parser in CONTROL_SPECS:
            controls[name] = _read_control(resolved_root, relative_path, parser)
        controls["secure_boot"] = _secure_boot(resolved_root)
    observed = sum(control.get("status") == "observed" for control in controls.values())
    return {
        "methodology_version": SECURITY_POSTURE_VERSION,
        "platform": system,
        "evidence_status": "complete" if observed == len(controls) else ("partial" if observed else "unavailable"),
        "observed_controls": observed,
        "total_controls": len(controls),
        "controls": controls,
        "policy": {
            "read_only": True,
            "maximum_bytes_per_control": SECURITY_CONTROL_MAX_BYTES,
            "missing_evidence_is_zero": False,
            "security_score": False,
            "raw_core_pattern_persisted": False,
            "efi_variable_identifier_persisted": False,
        },
    }
