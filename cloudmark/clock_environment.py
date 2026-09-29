from __future__ import annotations

import math
import os
import platform
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CLOCK_ENVIRONMENT_VERSION = "clock-environment-v1"
CLOCKSOURCE_MAX_BYTES = 4_096
TIMENS_OFFSETS_MAX_BYTES = 4_096
TIMEDATECTL_MAX_BYTES = 32
CLOCKSOURCE_MAX_COUNT = 32
CLOCK_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_.+-]{1,64}$")
TIMENS_CLOCKS = {"monotonic", "boottime"}


def _bounded_text(path: Path, maximum_bytes: int) -> tuple[str | None, bool]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            value = handle.read(maximum_bytes + 1)
    except OSError:
        return None, False
    return value[:maximum_bytes], len(value) > maximum_bytes


def _clock_info(name: str) -> dict[str, Any]:
    try:
        info = time.get_clock_info(name)
    except (ValueError, OSError):
        return {"status": "unavailable"}
    implementation = " ".join(str(info.implementation).split())[:160]
    resolution = float(info.resolution)
    if not implementation or not math.isfinite(resolution) or resolution <= 0:
        return {"status": "unavailable"}
    return {
        "status": "observed",
        "implementation": implementation,
        "resolution_seconds": resolution,
        "monotonic": bool(info.monotonic),
        "adjustable": bool(info.adjustable),
    }


def _clocksource_evidence(root: Path) -> dict[str, Any]:
    current_text, current_truncated = _bounded_text(root / "current_clocksource", 128)
    available_text, available_truncated = _bounded_text(root / "available_clocksource", CLOCKSOURCE_MAX_BYTES)
    current = current_text.strip() if current_text is not None and not current_truncated else ""
    available = available_text.split() if available_text is not None and not available_truncated else []
    valid = (
        bool(current)
        and CLOCK_IDENTIFIER_PATTERN.fullmatch(current) is not None
        and bool(available)
        and len(available) <= CLOCKSOURCE_MAX_COUNT
        and len(set(available)) == len(available)
        and all(CLOCK_IDENTIFIER_PATTERN.fullmatch(item) for item in available)
        and current in available
    )
    return {
        "status": "observed" if valid else "unavailable",
        "current": current if valid else None,
        "available": available if valid else [],
        "truncated": current_truncated or available_truncated,
        **(
            {"reason": "Linux clocksource controls were missing, truncated, malformed, or inconsistent."}
            if not valid
            else {}
        ),
    }


def _time_namespace_evidence(path: Path) -> dict[str, Any]:
    text, truncated = _bounded_text(path, TIMENS_OFFSETS_MAX_BYTES)
    if text is None or truncated:
        return {
            "status": "unavailable",
            "offsets": [],
            "nonzero_offset_observed": False,
            "truncated": truncated,
            "reason": "Linux time-namespace offsets were unavailable or truncated.",
        }
    offsets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 3 or fields[0] not in TIMENS_CLOCKS or fields[0] in seen:
            return {
                "status": "unavailable",
                "offsets": [],
                "nonzero_offset_observed": False,
                "truncated": False,
                "reason": "Linux time-namespace offsets were malformed or outside policy.",
            }
        try:
            seconds = int(fields[1])
            nanoseconds = int(fields[2])
        except ValueError:
            return {
                "status": "unavailable",
                "offsets": [],
                "nonzero_offset_observed": False,
                "truncated": False,
                "reason": "Linux time-namespace offsets were malformed or outside policy.",
            }
        if abs(seconds) > 2**63 - 1 or abs(nanoseconds) >= 1_000_000_000:
            return {
                "status": "unavailable",
                "offsets": [],
                "nonzero_offset_observed": False,
                "truncated": False,
                "reason": "Linux time-namespace offsets were malformed or outside policy.",
            }
        seen.add(fields[0])
        offsets.append({"clock": fields[0], "seconds": seconds, "nanoseconds": nanoseconds})
    if not offsets:
        return {
            "status": "unavailable",
            "offsets": [],
            "nonzero_offset_observed": False,
            "truncated": False,
            "reason": "Linux exposed no bounded time-namespace offset rows.",
        }
    return {
        "status": "observed",
        "offsets": offsets,
        "nonzero_offset_observed": any(item["seconds"] or item["nanoseconds"] for item in offsets),
        "truncated": False,
        "raw_namespace_identifier_persisted": False,
    }


def _timedatectl_boolean(executable: str, property_name: str) -> dict[str, Any]:
    command = [
        executable,
        "--no-pager",
        "show",
        f"--property={property_name}",
        "--value",
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
        return {"status": "unavailable", "value": None}
    value = result.stdout.strip().lower()
    if (
        result.returncode != 0
        or len(result.stdout.encode("utf-8", errors="replace")) > TIMEDATECTL_MAX_BYTES
        or value not in {"yes", "no", "true", "false"}
    ):
        return {"status": "unavailable", "value": None}
    return {"status": "observed", "value": value in {"yes", "true"}}


def _systemd_time_evidence(executable: str | None) -> dict[str, Any]:
    if executable is None:
        return {
            "status": "unavailable",
            "ntp_service_active_assertion": None,
            "system_clock_synchronized_assertion": None,
            "reason": "timedatectl is not installed or systemd-timedated is unavailable.",
        }
    ntp = _timedatectl_boolean(executable, "NTP")
    synchronized = _timedatectl_boolean(executable, "NTPSynchronized")
    status = "observed" if ntp["status"] == synchronized["status"] == "observed" else "partial"
    return {
        "status": status,
        "source": "systemd-timedated-properties-via-timedatectl",
        "ntp_service_active_assertion": ntp["value"],
        "system_clock_synchronized_assertion": synchronized["value"],
        "cloudmark_ntp_validation_performed": False,
        "offset_measured": False,
        "drift_measured": False,
        "peer_identity_persisted": False,
        **(
            {"reason": "One or more fixed systemd time properties were unavailable."}
            if status != "observed"
            else {}
        ),
    }


def collect_clock_environment(
    *,
    clocksource_root: Path = Path("/sys/devices/system/clocksource/clocksource0"),
    timens_offsets_path: Path = Path("/proc/self/timens_offsets"),
    timedatectl: str | None = None,
) -> dict[str, Any]:
    observed_at = datetime.now(timezone.utc).isoformat()
    python_clocks = {
        name: _clock_info(name)
        for name in ("time", "monotonic", "perf_counter")
    }
    base: dict[str, Any] = {
        "methodology_version": CLOCK_ENVIRONMENT_VERSION,
        "observed_at": observed_at,
        "platform": platform.system(),
        "scope": "guest-clock-configuration-and-os-sync-assertion",
        "python_clocks": python_clocks,
        "clocksource": {"status": "unavailable", "current": None, "available": []},
        "time_namespace": {
            "status": "unavailable",
            "offsets": [],
            "nonzero_offset_observed": False,
        },
        "system_time": {
            "status": "unavailable",
            "ntp_service_active_assertion": None,
            "system_clock_synchronized_assertion": None,
        },
        "policy": {
            "read_only": True,
            "system_clock_changed": False,
            "rtc_queried": False,
            "network_request_performed": False,
            "cloudmark_ntp_validation_performed": False,
            "offset_measured": False,
            "drift_measured": False,
            "time_namespace_identifier_persisted": False,
        },
    }
    if platform.system() != "Linux":
        return {
            **base,
            "evidence_status": "partial",
            "reason": "Portable clock semantics were observed; Linux clocksource, time namespace, and systemd assertions are unavailable.",
        }
    clocksource = _clocksource_evidence(clocksource_root)
    time_namespace = _time_namespace_evidence(timens_offsets_path)
    system_time = _systemd_time_evidence(timedatectl or shutil.which("timedatectl"))
    complete = all(
        item.get("status") == "observed"
        for item in (*python_clocks.values(), clocksource, time_namespace, system_time)
    )
    return {
        **base,
        "evidence_status": "complete" if complete else "partial",
        "clocksource": clocksource,
        "time_namespace": time_namespace,
        "system_time": system_time,
        **(
            {"reason": "One or more bounded clock-context sources were unavailable or partial."}
            if not complete
            else {}
        ),
    }
