from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Callable

from .profiles import (
    COMPUTE_PROFILES,
    DATABASE_PROFILES,
    MEMORY_PROFILES,
    NETWORK_PROFILES,
    SCENARIOS,
    STORAGE_PROFILES,
    WEB_PROFILES,
)


SUITABILITY_ENGINE_VERSION = "suitability-v1"
REQUIREMENTS_VERSION = "workload-requirements-1.0"
PROVIDER_OBSERVATION_VERSION = "provider-observations-v3"
EVIDENCE_MAX_AGE_DAYS = 30
EVIDENCE_FUTURE_SKEW_SECONDS = 86_400
COMPARISON_MIN_SAMPLES = 9
COMPARISON_MIN_TARGETS = 3
COMPARISON_MIN_WINDOWS = 3

MIB = 1024**2
GIB = 1024**3

COMPARISON_METRICS: dict[str, dict[str, Any]] = {
    "compute.single_eps": {"label": "Single-thread integer rate", "direction": "higher"},
    "compute.sustained_eps": {"label": "Sustained all-core integer rate", "direction": "higher"},
    "compute.scaling_efficiency_pct": {"label": "Compute scaling efficiency", "direction": "higher"},
    "memory.triad_bps": {"label": "All-core memory triad bandwidth", "direction": "higher"},
    "storage.sequential_read_bps": {"label": "Sequential storage read", "direction": "higher"},
    "storage.sequential_write_bps": {"label": "Sequential storage write", "direction": "higher"},
    "storage.random_read_qd1_iops": {"label": "Low-queue random read", "direction": "higher"},
    "storage.sync_write_iops": {"label": "Durable synchronous write", "direction": "higher"},
    "network.directional_floor_bps": {"label": "Peer TCP directional floor", "direction": "higher"},
    "network.idle_latency_ms": {"label": "Worst peer idle latency", "direction": "lower"},
    "network.idle_loss_pct": {"label": "Worst idle packet loss", "direction": "lower"},
    "network.udp_loss_pct": {"label": "Worst adaptive UDP loss", "direction": "lower"},
    "network.udp_jitter_ms": {"label": "Worst adaptive UDP jitter", "direction": "lower"},
    "database.tpcb_c4_tps": {"label": "Durable TPC-B-like throughput at C4", "direction": "higher"},
    "database.tpcb_c4_latency_ms": {"label": "Durable TPC-B-like average latency", "direction": "lower"},
    "database.tpcb_c4_failed": {"label": "Failed database transactions", "direction": "lower"},
    "web.https_api_c16_rps": {"label": "HTTPS API throughput at C16", "direction": "higher"},
    "web.https_api_c16_p95_ms": {"label": "HTTPS API P95 at C16", "direction": "lower"},
    "web.https_api_c16_success_pct": {"label": "HTTPS API success rate", "direction": "higher"},
}

REQUIREMENT_LEVELS: dict[str, dict[str, str]] = {
    "essential": {
        "label": "Essential",
        "description": "Entry production or light-duty baseline with modest concurrency and capacity.",
    },
    "standard": {
        "label": "Standard",
        "description": "General production baseline with stronger latency, throughput, and capacity gates.",
    },
    "demanding": {
        "label": "Demanding",
        "description": "High-demand baseline for sustained throughput, tighter latency, and higher concurrency.",
    },
}


def _threshold(essential: float, standard: float, demanding: float) -> dict[str, float]:
    return {"essential": essential, "standard": standard, "demanding": demanding}


SCENARIO_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "storage-backup": {
        "rules": [
            ("storage.sequential_read_bps", "Sequential read", ">=", _threshold(50 * MIB, 150 * MIB, 500 * MIB), "B/s"),
            ("storage.sequential_write_bps", "Sequential write", ">=", _threshold(30 * MIB, 100 * MIB, 300 * MIB), "B/s"),
            ("storage.cleanup_verified", "Temporary-file cleanup", "==", _threshold(1, 1, 1), "boolean"),
        ],
        "limitations": ["Snapshot, object-storage durability, restore validation, availability, and cost are not measured."],
        "next_actions": ["Run Disk Throughput or Disk Standard on the intended storage filesystem."],
    },
    "web-app": {
        "rules": [
            ("inventory.logical_cores", "Logical CPU capacity", ">=", _threshold(2, 4, 8), "cores"),
            ("inventory.memory_bytes", "Memory capacity", ">=", _threshold(2 * GIB, 8 * GIB, 16 * GIB), "B"),
            ("compute.single_eps", "Single-thread integer rate", ">=", _threshold(650, 1100, 1800), "events/s"),
            ("web.https_api_c16_rps", "HTTPS API throughput at C16", ">=", _threshold(300, 1500, 5000), "req/s"),
            ("web.https_api_c16_p95_ms", "HTTPS API P95 at C16", "<=", _threshold(75, 30, 15), "ms"),
            ("web.https_api_c16_success_pct", "HTTPS success rate", ">=", _threshold(99.0, 99.9, 99.99), "%"),
            ("network.directional_floor_bps", "Peer TCP directional floor", ">=", _threshold(100_000_000, 500_000_000, 2_000_000_000), "bit/s"),
            ("network.idle_latency_ms", "Worst peer idle latency", "<=", _threshold(50, 20, 8), "ms"),
        ],
        "limitations": ["A bundled dynamic reverse-proxy workload and HTTP/2 negotiation are measured; database-backed applications, HTTP/2 load, HTTP/3, WAF, CDN, autoscaling, and public TLS trust remain unavailable."],
        "next_actions": ["Run Compute Standard, Network Standard, and Web & TLS Peer Standard on the same Target."],
    },
    "dev-test": {
        "rules": [
            ("inventory.logical_cores", "Logical CPU capacity", ">=", _threshold(2, 4, 8), "cores"),
            ("inventory.memory_bytes", "Memory capacity", ">=", _threshold(4 * GIB, 8 * GIB, 16 * GIB), "B"),
            ("compute.sustained_eps", "Sustained all-core integer rate", ">=", _threshold(1200, 2500, 5000), "events/s"),
            ("storage.random_read_qd1_iops", "Low-queue random read", ">=", _threshold(750, 2000, 5000), "IOPS"),
            ("storage.sequential_write_bps", "Sequential write", ">=", _threshold(30 * MIB, 100 * MIB, 250 * MIB), "B/s"),
        ],
        "limitations": ["Provisioning time, image lifecycle, snapshot/clone behavior, automation APIs, and cost are not measured."],
        "next_actions": ["Run Compute Standard, Memory Standard, and Disk Standard on the development target."],
    },
    "database": {
        "rules": [
            ("inventory.logical_cores", "Logical CPU capacity", ">=", _threshold(2, 4, 8), "cores"),
            ("inventory.memory_bytes", "Memory capacity", ">=", _threshold(4 * GIB, 8 * GIB, 16 * GIB), "B"),
            ("storage.sync_write_iops", "Durable 8 KiB synchronous write", ">=", _threshold(150, 500, 1500), "IOPS"),
            ("database.tpcb_c4_tps", "Durable TPC-B-like throughput at C4", ">=", _threshold(100, 500, 1500), "TPS"),
            ("database.tpcb_c4_latency_ms", "Durable TPC-B-like average latency", "<=", _threshold(40, 15, 7), "ms"),
            ("database.tpcb_c4_failed", "Failed transactions", "==", _threshold(0, 0, 0), "count"),
            ("network.idle_latency_ms", "Worst peer idle latency", "<=", _threshold(50, 20, 8), "ms"),
        ],
        "limitations": ["Fixed-count PostgreSQL transaction P95/P99 is measured; replication, failover, backup/restore, MySQL/MariaDB, Redis, and managed-service behavior remain unavailable."],
        "next_actions": ["Run Disk Database, Network Standard, and PostgreSQL Peer Standard on the same Target."],
    },
    "network": {
        "rules": [
            ("network.directional_floor_bps", "Peer TCP directional floor", ">=", _threshold(100_000_000, 500_000_000, 2_000_000_000), "bit/s"),
            ("network.idle_latency_ms", "Worst peer idle latency", "<=", _threshold(50, 20, 8), "ms"),
            ("network.idle_loss_pct", "Worst idle packet loss", "<=", _threshold(1.0, 0.5, 0.1), "%"),
            ("network.udp_loss_pct", "Worst adaptive UDP loss", "<=", _threshold(2.0, 1.0, 0.25), "%"),
            ("network.udp_jitter_ms", "Worst adaptive UDP jitter", "<=", _threshold(10, 3, 1), "ms"),
        ],
        "limitations": ["DNS ownership, administrative path ownership, cross-zone/region topology, physical-host queue behavior, and independent repeated pairs are not fully validated."],
        "next_actions": ["Run Provider Internal Network (network-v9) between equivalent provider instances."],
    },
    "big-data": {
        "rules": [
            ("compute.sustained_eps", "Sustained all-core integer rate", ">=", _threshold(2500, 5000, 10000), "events/s"),
            ("memory.triad_bps", "All-core memory triad bandwidth", ">=", _threshold(10 * GIB, 25 * GIB, 60 * GIB), "B/s"),
            ("storage.sequential_read_bps", "Sequential read", ">=", _threshold(150 * MIB, 500 * MIB, 1000 * MIB), "B/s"),
            ("network.directional_floor_bps", "Peer TCP directional floor", ">=", _threshold(500_000_000, 2_000_000_000, 10_000_000_000), "bit/s"),
        ],
        "blockers": ["A distributed processing, data-lake, shuffle, and ETL methodology is not implemented."],
        "next_actions": ["Complete the distributed analytics executor before assigning suitability."],
    },
    "ai-ml": {
        "rules": [],
        "blockers": ["GPU/accelerator inventory, transfer, compute, framework, training, and inference evidence is unavailable."],
        "next_actions": ["Complete the GPU and accelerator assessment domain."],
    },
    "containers": {
        "rules": [
            ("inventory.container_runtime", "Container runtime detected", "==", _threshold(1, 1, 1), "boolean"),
            ("inventory.logical_cores", "Logical CPU capacity", ">=", _threshold(2, 4, 8), "cores"),
            ("inventory.memory_bytes", "Memory capacity", ">=", _threshold(4 * GIB, 8 * GIB, 16 * GIB), "B"),
        ],
        "blockers": ["Image pull, cold start, overlay I/O, CNI, scheduling, pod density, and autoscaling evidence is unavailable."],
        "next_actions": ["Complete the container and Kubernetes executor before assigning suitability."],
    },
    "dr": {
        "rules": [],
        "blockers": ["Backup integrity, snapshot restore, replication, failover, RPO, and RTO drills are unavailable."],
        "next_actions": ["Complete the reliability, backup, and disaster-recovery drill methodology."],
    },
    "vdi": {
        "rules": [],
        "blockers": ["GPU, display protocol, remote interaction latency, concurrency, and user-experience evidence is unavailable."],
        "next_actions": ["Complete GPU and interactive VDI assessment profiles."],
    },
    "media": {
        "rules": [
            ("storage.sequential_read_bps", "Sequential read", ">=", _threshold(150 * MIB, 500 * MIB, 1000 * MIB), "B/s"),
            ("network.directional_floor_bps", "Peer TCP directional floor", ">=", _threshold(500_000_000, 2_000_000_000, 10_000_000_000), "bit/s"),
        ],
        "blockers": ["Codec throughput, GPU media engines, streaming, CDN, and global distribution evidence is unavailable."],
        "next_actions": ["Complete the media codec and content-delivery executor."],
    },
    "enterprise": {
        "rules": [],
        "blockers": ["IAM/RBAC, compliance, HA, reliability, observability, integration, and control-plane evidence is unavailable."],
        "next_actions": ["Complete security, reliability, observability, and control-plane assessment domains."],
    },
}


EXPECTED_METHODOLOGIES = {
    "compute": {str(profile["methodology_version"]) for profile in COMPUTE_PROFILES.values()},
    "memory": {str(profile["methodology_version"]) for profile in MEMORY_PROFILES.values()},
    "storage": {str(profile["methodology_version"]) for profile in STORAGE_PROFILES.values()},
    "network": {str(profile["methodology_version"]) for profile in NETWORK_PROFILES.values()},
    "database": {str(profile["methodology_version"]) for profile in DATABASE_PROFILES.values()},
    "web": {str(profile["methodology_version"]) for profile in WEB_PROFILES.values()},
}
# Completed network-v2 through network-v8 evidence remains readable after the
# standard profile moves to network-v9. Version 9 adds bounded guest-visible
# steering and IRQ affinity without turning virtual-NIC support into a gate.
EXPECTED_METHODOLOGIES["network"].update(
    {"network-v2", "network-v3", "network-v4", "network-v5", "network-v6", "network-v7", "network-v8"}
)


def _nested(value: Any, *path: str) -> Any:
    current = value
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    return None


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _profile_quality(profile: str) -> tuple[str, int]:
    if any(token in profile for token in ("standard", "sustained", "throughput", "database")):
        return "standard", 2
    if "quick" in profile:
        return "quick", 1
    return "specialized", 2


def _run_is_fresh(run: dict[str, Any]) -> bool:
    return _timestamp_is_fresh(run.get("finished_at") or run.get("started_at"))


def _timestamp_is_fresh(value: Any) -> bool:
    observed = _timestamp(value)
    if observed is None:
        return False
    age_seconds = (datetime.now(timezone.utc) - observed).total_seconds()
    return -EVIDENCE_FUTURE_SKEW_SECONDS <= age_seconds <= EVIDENCE_MAX_AGE_DAYS * 86_400


def _run_targets(run: dict[str, Any]) -> set[str]:
    request = run.get("request") or {}
    result = run.get("result") or {}
    suite = str(run.get("suite", ""))
    agent_id = str(request.get("agent_id", "")).strip()
    if agent_id:
        return {agent_id}
    if suite in {"database", "web"}:
        target_id = str(_nested(result, "target", "id") or "").strip()
        return {target_id} if target_id else set()
    if suite == "network":
        ids: set[str] = set()
        for collection in ("measurements", "latency_measurements", "udp_measurements", "bidirectional_measurements"):
            for measurement in result.get(collection) or []:
                for endpoint in (measurement.get("sender"), measurement.get("receiver")):
                    endpoint_id = str((endpoint or {}).get("id", "")).strip()
                    if endpoint_id:
                        ids.add(endpoint_id)
        return ids
    return {"controller"}


def _run_valid(run: dict[str, Any]) -> tuple[bool, str | None]:
    if run.get("status") != "completed" or not isinstance(run.get("result"), dict):
        return False, "Run is not completed."
    suite = str(run.get("suite", ""))
    result = run["result"]
    if suite in EXPECTED_METHODOLOGIES:
        methodology = str(result.get("methodology_version") or run.get("methodology_version") or "")
        if methodology not in EXPECTED_METHODOLOGIES[suite]:
            return False, "Methodology version is not recognized by the installed evaluator."
    if suite == "storage" and _nested(result, "safety", "test_file_removed") is not True:
        return False, "Storage cleanup is not verified."
    if suite in {"database", "web"} and _nested(result, "cleanup", "cleanup_verified") is not True:
        return False, "Ephemeral service cleanup is not verified."
    if suite == "database" and str(result.get("methodology_version", "")) in {
        "database-postgresql-v2",
        "database-postgresql-recovery-v1",
        "database-redis-v1",
    }:
        if _nested(result, "analysis", "validity", "comparison_eligible") is not True:
            return False, "Database Generator validity, required tail/recovery evidence, or cleanup evidence is insufficient for comparison."
    if suite == "web" and str(result.get("methodology_version", "")) == "web-http-v2":
        if _nested(result, "analysis", "validity", "comparison_eligible") is not True:
            return False, "Web v2 Generator headroom, dynamic reverse-proxy, HTTP/2 negotiation, or cleanup evidence is insufficient for comparison."
    if suite == "network" and str(result.get("methodology_version", "")) in {
        "network-v3",
        "network-v4",
        "network-v5",
        "network-v6",
        "network-v7",
        "network-v8",
        "network-v9",
    }:
        if _nested(result, "analysis", "validity", "comparison_eligible") is not True:
            return False, "Network route, bounded path-trace, NIC, TCP-control, interface-counter, or Generator headroom evidence is insufficient for comparison."
    return True, None


def _evidence_item(
    value: float,
    unit: str,
    *,
    run: dict[str, Any] | None = None,
    source: str = "inventory",
    observed_at: str | None = None,
) -> dict[str, Any]:
    if run is None:
        return {
            "value": value,
            "unit": unit,
            "source": source,
            "run_id": None,
            "profile": None,
            "methodology_version": "inventory-v1",
            "observed_at": observed_at,
            "quality": "observed-fact",
            "stale": not _timestamp_is_fresh(observed_at),
            "_rank": 3,
        }
    profile = str(run.get("profile", ""))
    quality, rank = _profile_quality(profile)
    observed = str(run.get("finished_at") or run.get("started_at") or "") or None
    return {
        "value": value,
        "unit": unit,
        "source": source,
        "run_id": run.get("id"),
        "profile": profile,
        "methodology_version": _nested(run, "result", "methodology_version") or run.get("methodology_version"),
        "observed_at": observed,
        "quality": quality,
        "stale": not _timestamp_is_fresh(observed),
        "_rank": rank,
    }


def _put(evidence: dict[str, dict[str, Any]], key: str, item: dict[str, Any]) -> None:
    existing = evidence.get(key)
    if existing is None:
        evidence[key] = item
        return
    candidate_order = (not item["stale"], item["_rank"], str(item.get("observed_at") or ""))
    existing_order = (not existing["stale"], existing["_rank"], str(existing.get("observed_at") or ""))
    if candidate_order > existing_order:
        evidence[key] = item


def _inventory_evidence(evidence: dict[str, dict[str, Any]], system: dict[str, Any], observed_at: str | None) -> None:
    inventory = system.get("inventory") or {}
    cores = _number(_nested(inventory, "cpu", "logical_cores"))
    memory = _number(_nested(inventory, "memory", "total_bytes"))
    capabilities = inventory.get("capabilities") or {}
    if cores is not None:
        _put(evidence, "inventory.logical_cores", _evidence_item(cores, "cores", observed_at=observed_at))
    if memory is not None:
        _put(evidence, "inventory.memory_bytes", _evidence_item(memory, "B", observed_at=observed_at))
    runtime = 1.0 if capabilities.get("docker") or capabilities.get("podman") else 0.0
    _put(evidence, "inventory.container_runtime", _evidence_item(runtime, "boolean", observed_at=observed_at))


def _extract_run_evidence(evidence: dict[str, dict[str, Any]], run: dict[str, Any]) -> None:
    result = run["result"]
    suite = str(run["suite"])
    if suite == "compute":
        for job in result.get("compute_jobs") or []:
            rate = _number(_nested(job, "metrics", "events_per_second"))
            if rate is None:
                continue
            if job.get("name") == "integer-single":
                _put(evidence, "compute.single_eps", _evidence_item(rate, "events/s", run=run, source="compute"))
            if job.get("name") == "integer-sustained":
                _put(evidence, "compute.sustained_eps", _evidence_item(rate, "events/s", run=run, source="compute"))
        efficiency = _number(_nested(result, "scaling", "efficiency_percent"))
        if efficiency is not None:
            _put(evidence, "compute.scaling_efficiency_pct", _evidence_item(efficiency, "%", run=run, source="compute"))
    elif suite == "memory":
        triad = next((job for job in result.get("memory_jobs") or [] if job.get("name") == "triad-all-cores"), None)
        bandwidth = _number(_nested(triad, "metrics", "bandwidth_bytes_per_second"))
        if bandwidth is not None:
            _put(evidence, "memory.triad_bps", _evidence_item(bandwidth, "B/s", run=run, source="memory"))
    elif suite == "storage":
        for job in result.get("jobs") or []:
            name = str(job.get("name", ""))
            read_bw = _number(_nested(job, "read", "bandwidth_bytes_per_second"))
            write_bw = _number(_nested(job, "write", "bandwidth_bytes_per_second"))
            if "sequential-read" in name and read_bw is not None:
                _put(evidence, "storage.sequential_read_bps", _evidence_item(read_bw, "B/s", run=run, source="storage"))
            if "sequential-write" in name and write_bw is not None:
                _put(evidence, "storage.sequential_write_bps", _evidence_item(write_bw, "B/s", run=run, source="storage"))
            if name in {"random-read-qd1", "database-read-qd1"}:
                read_iops = _number(_nested(job, "read", "iops"))
                if read_iops is not None:
                    _put(evidence, "storage.random_read_qd1_iops", _evidence_item(read_iops, "IOPS", run=run, source="storage"))
            if name == "database-sync":
                write_iops = _number(_nested(job, "write", "iops"))
                if write_iops is not None:
                    _put(evidence, "storage.sync_write_iops", _evidence_item(write_iops, "IOPS", run=run, source="storage"))
        _put(evidence, "storage.cleanup_verified", _evidence_item(1, "boolean", run=run, source="storage"))
    elif suite == "network":
        direction_peaks: dict[str, float] = {}
        for measurement in result.get("measurements") or []:
            rate = _number(_nested(measurement, "metrics", "received_bits_per_second"))
            direction = str(measurement.get("direction", ""))
            if rate is not None and direction:
                direction_peaks[direction] = max(direction_peaks.get(direction, 0), rate)
        if len(direction_peaks) >= 2:
            _put(evidence, "network.directional_floor_bps", _evidence_item(min(direction_peaks.values()), "bit/s", run=run, source="network"))
        latency_values = [
            value for item in result.get("latency_measurements") or []
            if (value := _number(_nested(item, "metrics", "average_ms"))) is not None
        ]
        loss_values = [
            value for item in result.get("latency_measurements") or []
            if (value := _number(_nested(item, "metrics", "loss_percent"))) is not None
        ]
        if latency_values:
            _put(evidence, "network.idle_latency_ms", _evidence_item(max(latency_values), "ms", run=run, source="network"))
        if loss_values:
            _put(evidence, "network.idle_loss_pct", _evidence_item(max(loss_values), "%", run=run, source="network"))
        udp_loss = [
            value for item in result.get("udp_measurements") or []
            if (value := _number(_nested(item, "metrics", "lost_percent"))) is not None
        ]
        udp_jitter = [
            value for item in result.get("udp_measurements") or []
            if (value := _number(_nested(item, "metrics", "jitter_ms"))) is not None
        ]
        if udp_loss:
            _put(evidence, "network.udp_loss_pct", _evidence_item(max(udp_loss), "%", run=run, source="network"))
        if udp_jitter:
            _put(evidence, "network.udp_jitter_ms", _evidence_item(max(udp_jitter), "ms", run=run, source="network"))
    elif suite == "database":
        measurement = next((item for item in result.get("database_measurements") or [] if item.get("name") == "tpcb-like-c4"), None)
        if measurement:
            values = {
                "database.tpcb_c4_tps": (_number(_nested(measurement, "metrics", "transactions_per_second")), "TPS"),
                "database.tpcb_c4_latency_ms": (_number(_nested(measurement, "metrics", "latency_average_ms")), "ms"),
                "database.tpcb_c4_failed": (_number(_nested(measurement, "metrics", "failed_transactions")), "count"),
            }
            for key, (value, unit) in values.items():
                if value is not None:
                    _put(evidence, key, _evidence_item(value, unit, run=run, source="database"))
    elif suite == "web":
        measurement = next((item for item in result.get("web_measurements") or [] if item.get("name") == "https-api-c16"), None)
        if measurement:
            values = {
                "web.https_api_c16_rps": (_number(_nested(measurement, "metrics", "requests_per_second")), "req/s"),
                "web.https_api_c16_p95_ms": (_number(_nested(measurement, "metrics", "latency_percentiles_ms", "p95")), "ms"),
                "web.https_api_c16_success_pct": (_number(_nested(measurement, "metrics", "success_percent")), "%"),
            }
            for key, (value, unit) in values.items():
                if value is not None:
                    _put(evidence, key, _evidence_item(value, unit, run=run, source="web"))


def _public_evidence(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def _evaluate_scenario(scenario: dict[str, Any], evidence: dict[str, dict[str, Any]], level: str) -> dict[str, Any]:
    definition = SCENARIO_REQUIREMENTS[scenario["id"]]
    checks: list[dict[str, Any]] = []
    for key, label, operator, thresholds, unit in definition.get("rules", []):
        observed = evidence.get(key)
        threshold = float(thresholds[level])
        if observed is None:
            checks.append({
                "key": key,
                "label": label,
                "status": "unavailable",
                "operator": operator,
                "threshold": threshold,
                "unit": unit,
                "evidence": None,
            })
            continue
        if observed["stale"]:
            status = "stale"
        else:
            value = float(observed["value"])
            passed = value >= threshold if operator == ">=" else value <= threshold if operator == "<=" else value == threshold
            status = "pass" if passed else "fail"
        checks.append({
            "key": key,
            "label": label,
            "status": status,
            "operator": operator,
            "threshold": threshold,
            "unit": unit,
            "evidence": _public_evidence(observed),
        })

    blockers = list(definition.get("blockers") or [])
    observed_checks = [item for item in checks if item["status"] in {"pass", "fail"}]
    missing_checks = [item for item in checks if item["status"] in {"unavailable", "stale"}]
    failed_checks = [item for item in checks if item["status"] == "fail"]
    coverage_denominator = len(checks) + len(blockers)
    coverage_percent = round(len(observed_checks) * 100 / coverage_denominator, 1) if coverage_denominator else 0.0
    measured_pass_percent = round(
        sum(item["status"] == "pass" for item in observed_checks) * 100 / len(observed_checks), 1
    ) if observed_checks else None

    if blockers or missing_checks or not checks:
        verdict = "insufficient"
    elif failed_checks:
        verdict = "below-requirement"
    elif definition.get("limitations"):
        verdict = "conditional-fit"
    else:
        verdict = "suitable"

    if verdict == "insufficient":
        recommendation = "More verified evidence is required before this target can be classified."
    elif verdict == "below-requirement":
        recommendation = f"The observed target does not meet every {REQUIREMENT_LEVELS[level]['label']} hard gate."
    elif verdict == "conditional-fit":
        recommendation = (
            f"The target meets all measured {REQUIREMENT_LEVELS[level]['label']} gates, "
            "but unimplemented capability evidence limits the claim."
        )
    else:
        recommendation = f"The target meets the complete {REQUIREMENT_LEVELS[level]['label']} requirement contract."

    run_ids = sorted({
        str(item["evidence"]["run_id"])
        for item in checks
        if item.get("evidence") and item["evidence"].get("run_id")
    })
    return {
        "id": scenario["id"],
        "label": scenario["label"],
        "level": level,
        "verdict": verdict,
        "coverage_percent": coverage_percent,
        "measured_pass_percent": measured_pass_percent,
        "checks": checks,
        "blockers": blockers,
        "limitations": list(definition.get("limitations") or []),
        "next_actions": list(definition.get("next_actions") or []),
        "recommendation": recommendation,
        "run_ids": run_ids,
    }


def _target_metadata(target_id: str, system: dict[str, Any]) -> dict[str, Any]:
    inventory = system.get("inventory") or {}
    provider = system.get("provider") or {}
    return {
        "id": target_id,
        "label": str(inventory.get("hostname") or target_id),
        "provider": {
            "name": str(provider.get("provider") or "Unknown"),
            "confidence": _number(provider.get("confidence")) or 0.0,
            "source": str(provider.get("source") or "unavailable"),
            "region": provider.get("region"),
            "zone": provider.get("zone"),
            "instance_type": provider.get("instance_type"),
        },
        "system": {
            "os": _nested(inventory, "os", "distribution") or _nested(inventory, "os", "system"),
            "cpu": _nested(inventory, "cpu", "model"),
            "logical_cores": _nested(inventory, "cpu", "logical_cores"),
            "memory_bytes": _nested(inventory, "memory", "total_bytes"),
        },
    }


def _provider_identity_verified(provider: dict[str, Any]) -> bool:
    source = str(provider.get("source") or "").lower()
    return (
        provider.get("name") != "Unknown"
        and float(provider.get("confidence") or 0) >= 0.5
        and "unverified" not in source
        and "declared" not in source
    )


def _run_topology_contract(run: dict[str, Any]) -> tuple[str, str]:
    if str(run.get("suite") or "") not in {"network", "database", "web"}:
        return "single-target", "single-target"
    topology = _nested(run, "result", "session", "topology")
    topology = topology if isinstance(topology, dict) else {}
    scope = str(topology.get("scope") or "undeclared")
    source = str(topology.get("source") or "unavailable")
    verification = topology.get("verification") if isinstance(topology.get("verification"), dict) else {}
    verification_status = str(verification.get("status") or "legacy")
    observed_scope = str(verification.get("observed_scope") or "undeclared")
    valid_scopes = {"same-host", "same-zone", "cross-zone", "cross-region", "public-internet"}
    if verification_status in {"confirmed", "derived"} and observed_scope in valid_scopes:
        return observed_scope, "independently-derived"
    if verification_status == "contradicted":
        return "undeclared", "contradicted"
    if source == "operator-declared" and scope in valid_scopes:
        return scope, "operator-declared"
    return "undeclared", "unavailable"


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _stability(values: list[float], median: float, p10: float, p90: float) -> tuple[float | None, str]:
    if len(values) < 3:
        return None, "insufficient-sampling"
    spread = abs(p90 - p10)
    if abs(median) < 1e-12:
        if spread == 0:
            return 0.0, "stable"
        return None, "variable"
    relative_spread = round(spread * 100 / abs(median), 2)
    if relative_spread <= 10:
        return relative_spread, "stable"
    if relative_spread <= 25:
        return relative_spread, "moderate"
    return relative_spread, "variable"


def _provider_observations(targets: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    excluded_targets: list[dict[str, str]] = []
    for target in targets:
        provider = target["provider"]
        if not any(_run_is_fresh(run) for run in target["_runs"]):
            excluded_targets.append({"target_id": target["id"], "reason": "No fresh valid benchmark evidence is available."})
            continue
        provider_name = str(provider.get("name") or "").strip()
        instance_type = str(provider.get("instance_type") or "").strip()
        if not provider_name or provider_name == "Unknown":
            excluded_targets.append({"target_id": target["id"], "reason": "Provider identity is unavailable."})
            continue
        if not instance_type:
            excluded_targets.append({"target_id": target["id"], "reason": "Product or SKU identity is unavailable."})
            continue
        region = str(provider.get("region") or "unspecified-region")
        operating_system = str(target["system"].get("os") or "unspecified-os")
        grouped.setdefault((provider_name, instance_type, region, operating_system), []).append(target)

    groups: list[dict[str, Any]] = []
    for index, (cohort_key, peers) in enumerate(sorted(grouped.items()), start=1):
        provider_name, instance_type, region, operating_system = cohort_key
        peer_ids = {peer["id"] for peer in peers}
        identity_verified = all(_provider_identity_verified(peer["provider"]) for peer in peers)
        runs_by_id: dict[str, dict[str, Any]] = {}
        metric_builders: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
        for peer in peers:
            for run in peer["_runs"]:
                if not _run_is_fresh(run):
                    continue
                run_id = str(run.get("id") or "")
                if not run_id:
                    continue
                run_targets = _run_targets(run)
                if run.get("suite") == "network" and not run_targets.issubset(peer_ids):
                    continue
                runs_by_id[run_id] = run
                participating_targets = sorted(run_targets & peer_ids)
                if not participating_targets:
                    continue
                run_evidence: dict[str, dict[str, Any]] = {}
                _extract_run_evidence(run_evidence, run)
                for metric_key, item in run_evidence.items():
                    metric_definition = COMPARISON_METRICS.get(metric_key)
                    if metric_definition is None or item.get("stale"):
                        continue
                    profile = str(item.get("profile") or "")
                    methodology = str(item.get("methodology_version") or "")
                    unit = str(item.get("unit") or "")
                    topology_scope, topology_evidence = _run_topology_contract(run)
                    contract_key = (metric_key, profile, methodology, unit, topology_scope, topology_evidence)
                    builder = metric_builders.setdefault(contract_key, {
                        "values": [],
                        "run_ids": [],
                        "target_ids": set(),
                        "windows": set(),
                        "observed_at": [],
                        "_seen_runs": set(),
                    })
                    if run_id in builder["_seen_runs"]:
                        builder["target_ids"].update(participating_targets)
                        continue
                    builder["_seen_runs"].add(run_id)
                    builder["values"].append(float(item["value"]))
                    builder["run_ids"].append(run_id)
                    builder["target_ids"].update(participating_targets)
                    observed_at = str(item.get("observed_at") or "")
                    if observed_at:
                        builder["observed_at"].append(observed_at)
                        builder["windows"].add(observed_at[:10])

        metric_cohorts: list[dict[str, Any]] = []
        for contract_key, builder in sorted(metric_builders.items()):
            metric_key, profile, methodology, unit, topology_scope, topology_evidence = contract_key
            values = builder["values"]
            median = _percentile(values, 0.5)
            p10 = _percentile(values, 0.1)
            p90 = _percentile(values, 0.9)
            direction = COMPARISON_METRICS[metric_key]["direction"]
            target_count = len(builder["target_ids"])
            window_count = len(builder["windows"])
            reasons: list[str] = []
            if not identity_verified:
                reasons.append("Provider identity is not independently verified.")
            if len(values) < COMPARISON_MIN_SAMPLES:
                reasons.append(f"At least {COMPARISON_MIN_SAMPLES} samples are required.")
            if target_count < COMPARISON_MIN_TARGETS:
                reasons.append(f"At least {COMPARISON_MIN_TARGETS} targets are required.")
            if window_count < COMPARISON_MIN_WINDOWS:
                reasons.append(f"At least {COMPARISON_MIN_WINDOWS} UTC-day windows are required.")
            if topology_evidence == "contradicted":
                reasons.append("Paired benchmark topology evidence contradicts the operator declaration.")
            elif topology_scope == "undeclared":
                reasons.append("Paired benchmark topology is not declared.")
            relative_spread, stability = _stability(values, median, p10, p90)
            metric_cohorts.append({
                "contract_id": "|".join(contract_key),
                "key": metric_key,
                "label": COMPARISON_METRICS[metric_key]["label"],
                "suite": metric_key.split(".", 1)[0],
                "direction": direction,
                "unit": unit,
                "profile": profile,
                "methodology_version": methodology,
                "topology_scope": topology_scope,
                "topology_evidence": topology_evidence,
                "status": "comparable" if not reasons else "observational",
                "reasons": reasons,
                "sample_count": len(values),
                "target_count": target_count,
                "window_count": window_count,
                "windows": sorted(builder["windows"]),
                "run_ids": sorted(builder["run_ids"]),
                "latest_observed_at": max(builder["observed_at"]) if builder["observed_at"] else None,
                "statistics": {
                    "median": median,
                    "p10": p10,
                    "p90": p90,
                    "minimum": min(values),
                    "maximum": max(values),
                    "best": max(values) if direction == "higher" else min(values),
                    "worst": min(values) if direction == "higher" else max(values),
                    "relative_spread_percent": relative_spread,
                    "stability": stability,
                },
            })

        windows = sorted({str(run.get("finished_at") or run.get("started_at"))[:10] for run in runs_by_id.values()})
        comparable_suites = {item["suite"] for item in metric_cohorts if item["status"] == "comparable"}
        criteria = [
            {"label": "Verified provider identity", "satisfied": identity_verified},
            {"label": "At least three same-cohort targets", "satisfied": len(peer_ids) >= COMPARISON_MIN_TARGETS},
            {"label": "At least three UTC-day windows", "satisfied": len(windows) >= COMPARISON_MIN_WINDOWS},
            {
                "label": "Comparable compute, memory, storage, and network cohorts",
                "satisfied": {"compute", "memory", "storage", "network"}.issubset(comparable_suites),
            },
            {"label": "Security, reliability, control-plane, and cost evidence", "satisfied": False},
        ]
        if {"compute", "memory", "storage", "network"}.issubset(comparable_suites):
            comparison_status = "sampling-ready"
        elif comparable_suites:
            comparison_status = "partial"
        else:
            comparison_status = "observational"
        groups.append({
            "id": f"cohort-{index:03d}",
            "provider": provider_name,
            "instance_type": instance_type,
            "region": region,
            "operating_system": operating_system,
            "scope": "exact-provider-sku-region-os",
            "comparison_status": comparison_status,
            "rating_status": "not-rated",
            "target_ids": sorted(peer_ids),
            "target_count": len(peer_ids),
            "windows": windows,
            "window_count": len(windows),
            "observed_suites": sorted({str(run.get("suite")) for run in runs_by_id.values()}),
            "criteria": criteria,
            "gaps": [item["label"] for item in criteria if not item["satisfied"]],
            "metric_cohorts": metric_cohorts,
        })

    return {
        "version": PROVIDER_OBSERVATION_VERSION,
        "rating_status": "not-rated",
        "window_definition": "UTC calendar day derived from the completed run timestamp",
        "minimum_comparable_sampling": {
            "samples": COMPARISON_MIN_SAMPLES,
            "targets": COMPARISON_MIN_TARGETS,
            "windows": COMPARISON_MIN_WINDOWS,
        },
        "policy": {
            "exact_profile_and_methodology": True,
            "exact_pair_topology": True,
            "exact_pair_topology_evidence": True,
            "cross_sku_aggregation": False,
            "cross_region_aggregation": False,
            "cross_os_aggregation": False,
            "provider_ranking": False,
        },
        "groups": groups,
        "excluded_targets": excluded_targets,
    }


def evaluate_suitability(
    runs: list[dict[str, Any]],
    local_system: dict[str, Any],
    agent_lookup: Callable[[str], dict[str, Any] | None],
) -> dict[str, Any]:
    completed = [run for run in runs if run.get("status") == "completed" and isinstance(run.get("result"), dict)]
    target_ids = {"controller"}
    for run in completed:
        target_ids.update(_run_targets(run))

    targets: list[dict[str, Any]] = []
    systems: dict[str, dict[str, Any]] = {"controller": local_system}
    for target_id in sorted(target_ids):
        if target_id == "controller":
            system = local_system
            observed_at = datetime.now(timezone.utc).isoformat()
        else:
            agent = agent_lookup(target_id) or {}
            system = agent.get("system") or {}
            observed_at = agent.get("last_seen_at")
        systems[target_id] = system
        evidence: dict[str, dict[str, Any]] = {}
        _inventory_evidence(evidence, system, str(observed_at) if observed_at else None)
        rejected: list[dict[str, str]] = []
        target_runs: list[dict[str, Any]] = []
        for run in completed:
            if target_id not in _run_targets(run):
                continue
            valid, reason = _run_valid(run)
            if not valid:
                rejected.append({"run_id": str(run.get("id")), "reason": str(reason)})
                continue
            target_runs.append(run)
            _extract_run_evidence(evidence, run)

        levels = {
            level: [_evaluate_scenario(scenario, evidence, level) for scenario in SCENARIOS]
            for level in REQUIREMENT_LEVELS
        }
        metadata = _target_metadata(target_id, system)
        metadata.update({
            "scope": "single-target-observation",
            "evidence": {key: _public_evidence(value) for key, value in sorted(evidence.items())},
            "evidence_summary": {
                "accepted_runs": len(target_runs),
                "rejected_runs": rejected,
                "suites": sorted({str(run.get("suite")) for run in target_runs}),
                "freshness_days": EVIDENCE_MAX_AGE_DAYS,
            },
            "levels": levels,
            "_runs": target_runs,
        })
        targets.append(metadata)

    provider_observations = _provider_observations(targets)
    group_by_target = {
        target_id: group
        for group in provider_observations["groups"]
        for target_id in group["target_ids"]
    }
    for target in targets:
        group = group_by_target.get(target["id"])
        if group is None:
            criteria = [
                {"label": "Verified provider identity", "satisfied": False},
                {"label": "At least three same-cohort targets", "satisfied": False},
                {"label": "At least three UTC-day windows", "satisfied": False},
                {"label": "Comparable compute, memory, storage, and network cohorts", "satisfied": False},
                {"label": "Security, reliability, control-plane, and cost evidence", "satisfied": False},
            ]
            same_product_targets = 0
            measurement_windows = 0
            observed_suites: list[str] = []
        else:
            criteria = group["criteria"]
            same_product_targets = group["target_count"]
            measurement_windows = group["window_count"]
            observed_suites = group["observed_suites"]
        target["provider_assessment"] = {
            "status": "not-rated",
            "claim": "Instance observation only; this is not a provider-wide rating.",
            "cohort_id": group["id"] if group else None,
            "same_product_targets": same_product_targets,
            "measurement_windows": measurement_windows,
            "observed_suites": observed_suites,
            "criteria": criteria,
            "gaps": [item["label"] for item in criteria if not item["satisfied"]],
        }

    for target in targets:
        target.pop("_runs", None)

    return {
        "engine_version": SUITABILITY_ENGINE_VERSION,
        "requirements_version": REQUIREMENTS_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": {
            "missing_evidence_is_zero": False,
            "composite_provider_score": False,
            "target_scoped": True,
            "max_evidence_age_days": EVIDENCE_MAX_AGE_DAYS,
        },
        "levels": REQUIREMENT_LEVELS,
        "targets": targets,
        "provider_observations": provider_observations,
    }
