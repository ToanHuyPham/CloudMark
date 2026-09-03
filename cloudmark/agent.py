from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import __version__
from .benchmarks import run_storage
from .compute import run_system_benchmark
from .database_benchmark import (
    DATABASE_ALLOWED_CLIENTS,
    DATABASE_ALLOWED_THREADS,
    DATABASE_MAX_DURATION,
    DATABASE_MAX_SCALE,
    DATABASE_PORT_MAX,
    DATABASE_PORT_MIN,
    DATABASE_TAIL_JOB_TIMEOUT_SECONDS,
    DATABASE_TAIL_LOG_MAX_BYTES,
    DATABASE_TAIL_MAX_TOTAL_TRANSACTIONS,
    DATABASE_TAIL_TRANSACTIONS_PER_CLIENT,
    DatabaseBenchmarkError,
    parse_pgbench_latency_log,
    parse_pgbench_output,
    parse_pgbench_row_counts,
)
from .inventory import collect_inventory
from .network import (
    ALLOWED_LATENCY_COUNT_MAX,
    ALLOWED_LATENCY_INTERVAL_MS_MAX,
    ALLOWED_LATENCY_INTERVAL_MS_MIN,
    ALLOWED_LATENCY_TIMEOUT_MS_MAX,
    ALLOWED_LATENCY_TIMEOUT_MS_MIN,
    ALLOWED_PORT_MAX,
    ALLOWED_PORT_MIN,
    ALLOWED_STREAMS,
    ALLOWED_UDP_RATE_MAX,
    ALLOWED_UDP_RATE_MIN,
    NetworkError,
    parse_iperf_json,
    parse_ping_output,
)
from .profiles import COMPUTE_PROFILES, MEMORY_PROFILES, STORAGE_PROFILES
from .provider import detect_provider
from .remote import REMOTE_METHODOLOGY_VERSION
from .runner import CancellationToken, JobContext, RunCancelled, RunTimedOut
from .tooling import find_postgres_binary, find_redis_binary, find_web_binary, tool_version, web_tool_version
from .redis_benchmark import REDIS_PORT, RedisBenchmarkError, parse_redis_benchmark_csv
from .web_benchmark import (
    WEB_ALLOWED_CONCURRENCY,
    WEB_ALLOWED_PATHS,
    WEB_ALLOWED_PORTS,
    WEB_ALLOWED_SCHEMES,
    WEB_APP_PORT,
    WEB_HTTP_PORT,
    WEB_HTTPS_PORT,
    WEB_MAX_DURATION,
    WEB_REQUEST_LIMIT,
    WebBenchmarkError,
    parse_ab_output,
    parse_curl_protocol_output,
)
from .web_fixture import WEB_FIXTURE_BIND, WEB_FIXTURE_DYNAMIC_PATH


SERVICE_CONTROLLER_CONTACT_TIMEOUT_SECONDS = 20
PATH_PROBE_MAX_HOPS = 8
DNS_PROBE_NAME = "example.com."
DNS_PROBE_RECORD_TYPES = ("A", "AAAA")
DNS_PROBE_TIMEOUT_SECONDS = 5
RESOLVER_CONFIG_MAX_BYTES = 65_536
NETWORK_INTERFACE_PATTERN = re.compile(r"^[A-Za-z0-9_.:@-]{1,64}$")
NETWORK_QUEUE_MAX_INDEX = 127
NETWORK_QUEUE_MAX_STAT_LINES = 4096
NETWORK_STEERING_MAX_IRQS = 256
NETWORK_STEERING_MAX_MASK_BYTES = 4096
NETWORK_RSS_MAX_ENTRIES = 4096
NETWORK_RSS_MAX_LINES = 4096
NETWORK_QUEUE_METRIC_ALIASES = {
    "bytes": "bytes",
    "packets": "packets",
    "cnt": "packets",
    "drop": "dropped",
    "drops": "dropped",
    "dropped": "dropped",
    "error": "errors",
    "errors": "errors",
}
NETWORK_QUEUE_STAT_PATTERNS = (
    re.compile(
        r"^(rx|tx)_queue_(\d+)_(?:(rx|tx)_)?"
        r"(bytes|packets|cnt|drop|drops|dropped|error|errors)$"
    ),
    re.compile(r"^queue_(\d+)_(rx|tx)_(bytes|packets|cnt|drop|drops|dropped|error|errors)$"),
    re.compile(r"^(rx|tx)(\d+)_(bytes|packets|cnt|drop|drops|dropped|error|errors)$"),
)
IPV4_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
IPV4_DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)
IPV4_SHARED_NETWORK = ipaddress.ip_network("100.64.0.0/10")
IPV6_UNIQUE_LOCAL_NETWORK = ipaddress.ip_network("fc00::/7")
IPV6_DOCUMENTATION_NETWORK = ipaddress.ip_network("2001:db8::/32")
NETWORK_OFFLOAD_FEATURES = {
    "rx-checksumming",
    "tx-checksumming",
    "scatter-gather",
    "tcp-segmentation-offload",
    "generic-segmentation-offload",
    "generic-receive-offload",
    "large-receive-offload",
    "rx-vlan-offload",
    "tx-vlan-offload",
    "ntuple-filters",
    "receive-hashing",
}
RESOLVER_NUMERIC_OPTIONS = {"ndots", "timeout", "attempts"}
RESOLVER_BOOLEAN_OPTIONS = {
    "rotate",
    "single-request",
    "single-request-reopen",
    "use-vc",
    "trust-ad",
}


def _linux_cpu_snapshot(pid: int, *, proc_root: Path = Path("/proc")) -> dict[str, int] | None:
    try:
        cpu_line = (proc_root / "stat").read_text(encoding="utf-8").splitlines()[0]
        cpu_values = [int(value) for value in cpu_line.split()[1:9]]
        process_line = (proc_root / str(pid) / "stat").read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError, IndexError):
        return None
    closing_parenthesis = process_line.rfind(")")
    if closing_parenthesis < 0 or len(cpu_values) < 8:
        return None
    process_fields = process_line[closing_parenthesis + 2 :].split()
    try:
        process_ticks = int(process_fields[11]) + int(process_fields[12])
    except (ValueError, IndexError):
        return None
    total = sum(cpu_values)
    idle = cpu_values[3] + cpu_values[4]
    return {
        "total": total,
        "busy": total - idle,
        "steal": cpu_values[7],
        "process": process_ticks,
    }


def _linux_cpu_interval(before: dict[str, int], after: dict[str, int]) -> dict[str, float] | None:
    total_delta = after["total"] - before["total"]
    busy_delta = after["busy"] - before["busy"]
    steal_delta = after["steal"] - before["steal"]
    process_delta = after["process"] - before["process"]
    if total_delta <= 0 or min(busy_delta, steal_delta, process_delta) < 0:
        return None
    logical_cpus = max(1, os.cpu_count() or 1)
    return {
        "host_utilization_percent": round(min(100.0, busy_delta / total_delta * 100), 6),
        "host_steal_percent": round(min(100.0, steal_delta / total_delta * 100), 6),
        "process_cpu_percent_of_one_core": round(
            process_delta / total_delta * logical_cpus * 100,
            6,
        ),
    }


def _generator_cpu_evidence(samples: list[dict[str, float]]) -> dict[str, Any]:
    if not samples:
        return {
            "status": "unavailable",
            "source": "linux-procfs",
            "sample_count": 0,
            "reason": "Bounded Generator process CPU samples were unavailable.",
        }
    process_values = [item["process_cpu_percent_of_one_core"] for item in samples]
    host_values = [item["host_utilization_percent"] for item in samples]
    steal_values = [item["host_steal_percent"] for item in samples]
    return {
        "status": "observed",
        "source": "linux-procfs",
        "sample_count": len(samples),
        "peak_process_cpu_percent_of_one_core": round(max(process_values), 6),
        "mean_process_cpu_percent_of_one_core": round(sum(process_values) / len(process_values), 6),
        "peak_host_utilization_percent": round(max(host_values), 6),
        "mean_host_utilization_percent": round(sum(host_values) / len(host_values), 6),
        "peak_host_steal_percent": round(max(steal_values), 6),
        "sampling_interval_seconds": 1,
        "process_scope": "apachebench-load-generator",
    }


def _address_class(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    """Return an explicit routing class without inferring path ownership."""
    if address.is_unspecified:
        return "unspecified"
    if address.is_loopback:
        return "loopback"
    if address.is_multicast:
        return "multicast"
    if address.is_link_local:
        return "link-local"
    if isinstance(address, ipaddress.IPv4Address):
        if address in IPV4_SHARED_NETWORK:
            return "shared-address-space"
        if any(address in network for network in IPV4_DOCUMENTATION_NETWORKS):
            return "documentation"
        if any(address in network for network in IPV4_PRIVATE_NETWORKS):
            return "private"
    else:
        if address in IPV6_DOCUMENTATION_NETWORK:
            return "documentation"
        if address in IPV6_UNIQUE_LOCAL_NETWORK:
            return "unique-local"
    if address.is_global:
        return "global-unicast"
    if address.is_reserved:
        return "reserved"
    return "non-global-unicast"


def _address_value_class(value: Any) -> str | None:
    try:
        return _address_class(ipaddress.ip_address(str(value)))
    except ValueError:
        return None


def _parse_tracepath(stdout: str, destination: ipaddress.IPv4Address | ipaddress.IPv6Address) -> dict[str, Any]:
    """Normalize at most one numeric tracepath observation per bounded hop."""
    observations: dict[int, dict[str, Any]] = {}
    for line in stdout.splitlines():
        match = re.match(r"^\s*(\d+)\??:\s*(.*)$", line)
        if not match:
            continue
        hop = int(match.group(1))
        if not 1 <= hop <= PATH_PROBE_MAX_HOPS:
            continue
        body = match.group(2).strip()
        parsed_address: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
        for token in body.split():
            candidate = token.strip("[](),")
            try:
                parsed_address = ipaddress.ip_address(candidate)
                break
            except ValueError:
                continue
        rtt_match = re.search(r"(?<![\w.])(\d+(?:\.\d+)?)\s*ms\b", body, re.IGNORECASE)
        reached = bool(re.search(r"\breached\b", body, re.IGNORECASE)) and parsed_address == destination
        observation: dict[str, Any] = {
            "hop": hop,
            "state": "observed" if parsed_address is not None else "no-reply",
            "address": str(parsed_address) if parsed_address is not None else None,
            "address_class": _address_class(parsed_address) if parsed_address is not None else None,
            "rtt_ms": round(float(rtt_match.group(1)), 6) if rtt_match else None,
            "reached_destination": reached,
        }
        previous = observations.get(hop)
        if previous is None or (previous["state"] == "no-reply" and observation["state"] == "observed") or reached:
            observations[hop] = observation
    hops = [observations[hop] for hop in sorted(observations)[:PATH_PROBE_MAX_HOPS]]
    reached_destination = any(item["reached_destination"] for item in hops)
    status = "observed" if reached_destination else "partial" if hops else "unavailable"
    evidence: dict[str, Any] = {
        "status": status,
        "tool": "tracepath",
        "max_hops": PATH_PROBE_MAX_HOPS,
        "destination_address_class": _address_class(destination),
        "hops": hops,
        "reached_destination": reached_destination,
        "public_internet_traversal_proven": False,
        "limitation": "Observed IP hops do not prove administrative ownership or public Internet transit.",
    }
    if status == "unavailable":
        evidence["reason"] = "tracepath returned no parseable bounded hop observations."
    elif status == "partial":
        evidence["reason"] = "Bounded tracepath observations did not reach the paired destination."
    return evidence


def _parse_resolver_config(text: str, *, truncated: bool = False) -> dict[str, Any]:
    """Normalize resolv.conf without persisting search-domain names."""
    nameservers: list[dict[str, Any]] = []
    search_domain_count = 0
    options: dict[str, int | bool] = {}
    invalid_nameservers = 0
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].split(";", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        directive = fields[0].lower()
        if directive == "nameserver" and len(fields) >= 2:
            try:
                address = ipaddress.ip_address(fields[1].split("%", 1)[0])
            except ValueError:
                invalid_nameservers += 1
                continue
            nameservers.append(
                {
                    "address": fields[1][:128],
                    "address_family": f"ipv{address.version}",
                    "address_class": _address_class(address),
                }
            )
        elif directive in {"search", "domain"}:
            search_domain_count += len(fields[1:])
        elif directive == "options":
            for token in fields[1:]:
                name, separator, value = token.partition(":")
                name = name.lower()
                if name in RESOLVER_BOOLEAN_OPTIONS and not separator:
                    options[name.replace("-", "_")] = True
                elif name in RESOLVER_NUMERIC_OPTIONS and separator and value.isdigit():
                    options[name] = min(int(value), 1_000_000)
    status = "observed" if nameservers else "partial" if text.strip() else "unavailable"
    result: dict[str, Any] = {
        "status": status,
        "source": "etc-resolv-conf",
        "nameservers": nameservers[:16],
        "nameserver_count": len(nameservers),
        "search_domain_count": search_domain_count,
        "options": options,
        "invalid_nameserver_count": invalid_nameservers,
        "truncated": truncated or len(nameservers) > 16,
        "search_domain_names_persisted": False,
    }
    if status == "unavailable":
        result["reason"] = "resolv.conf did not expose resolver configuration."
    elif result["truncated"] or invalid_nameservers:
        result["status"] = "partial"
        result["reason"] = "Resolver configuration was truncated or contained an invalid nameserver entry."
    return result


def _parse_dig_response(
    stdout: str,
    stderr: str,
    *,
    record_type: str,
    returncode: int,
    elapsed_ms: float,
) -> dict[str, Any]:
    """Normalize one fixed dig query without retaining returned addresses."""
    header = re.search(r"status:\s*([A-Z0-9_-]+)", stdout, re.IGNORECASE)
    dns_status = header.group(1).upper() if header else None
    address_classes: list[str] = []
    answer_count = 0
    for line in stdout.splitlines():
        if not line or line.startswith(";"):
            continue
        fields = line.split()
        if len(fields) < 5 or fields[-2].upper() != record_type:
            continue
        answer_count += 1
        try:
            answer = ipaddress.ip_address(fields[-1])
        except ValueError:
            continue
        classification = _address_class(answer)
        if classification not in address_classes:
            address_classes.append(classification)
    combined_error = f"{stderr}\n{stdout}".lower()
    if returncode != 0:
        status = (
            "timeout"
            if "timed out" in combined_error or "no servers could be reached" in combined_error
            else "error"
        )
    elif dns_status == "NOERROR" and answer_count:
        status = "resolved"
    elif dns_status == "NOERROR":
        status = "no-data"
    elif dns_status == "NXDOMAIN":
        status = "negative"
    elif dns_status:
        status = "response-error"
    else:
        status = "error"
    result: dict[str, Any] = {
        "record_type": record_type,
        "status": status,
        "dns_status": dns_status,
        "elapsed_ms": round(elapsed_ms, 3),
        "answer_count": answer_count,
        "answer_address_classes": address_classes,
        "answer_addresses_persisted": False,
    }
    if status in {"timeout", "error", "response-error"}:
        result["reason"] = (
            stderr.strip() or "The fixed resolver query did not return a successful DNS response."
        )[:256]
    return result


def _resolver_evidence() -> dict[str, Any]:
    """Collect one bounded diagnostic observation through the system resolver."""
    observed_at = datetime.now(timezone.utc).isoformat()
    try:
        with Path("/etc/resolv.conf").open("r", encoding="utf-8", errors="replace") as handle:
            config_text = handle.read(RESOLVER_CONFIG_MAX_BYTES + 1)
        configuration = _parse_resolver_config(
            config_text[:RESOLVER_CONFIG_MAX_BYTES],
            truncated=len(config_text) > RESOLVER_CONFIG_MAX_BYTES,
        )
    except OSError:
        configuration = {
            "status": "unavailable",
            "source": "etc-resolv-conf",
            "nameservers": [],
            "nameserver_count": 0,
            "search_domain_count": 0,
            "options": {},
            "search_domain_names_persisted": False,
            "reason": "resolv.conf could not be read.",
        }
    dig = shutil.which("dig")
    queries: list[dict[str, Any]] = []
    if dig:
        environment = os.environ.copy()
        environment["LC_ALL"] = "C"
        for record_type in DNS_PROBE_RECORD_TYPES:
            command = [
                dig,
                "+tries=1",
                "+time=2",
                "+nocmd",
                "+noquestion",
                "+noauthority",
                "+noadditional",
                "+comments",
                "+answer",
                DNS_PROBE_NAME,
                record_type,
            ]
            started = time.monotonic()
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=DNS_PROBE_TIMEOUT_SECONDS,
                    check=False,
                    shell=False,
                    env=environment,
                )
                queries.append(
                    _parse_dig_response(
                        result.stdout,
                        result.stderr,
                        record_type=record_type,
                        returncode=result.returncode,
                        elapsed_ms=(time.monotonic() - started) * 1000,
                    )
                )
            except subprocess.TimeoutExpired:
                queries.append(
                    {
                        "record_type": record_type,
                        "status": "timeout",
                        "dns_status": None,
                        "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                        "answer_count": 0,
                        "answer_address_classes": [],
                        "answer_addresses_persisted": False,
                        "reason": "The fixed resolver query exceeded its guarded timeout.",
                    }
                )
    observed_queries = [item for item in queries if item["status"] in {"resolved", "no-data", "negative"}]
    if configuration.get("status") == "observed" and len(observed_queries) == len(DNS_PROBE_RECORD_TYPES):
        status = "complete"
    elif configuration.get("status") in {"observed", "partial"} or observed_queries:
        status = "partial"
    else:
        status = "unavailable"
    evidence: dict[str, Any] = {
        "status": status,
        "scope": "agent-system-resolver-diagnostic",
        "observed_at": observed_at,
        "query_name": DNS_PROBE_NAME,
        "query_name_policy": "fixed-iana-reserved-example-domain",
        "configuration": configuration,
        "queries": queries,
        "tool": {"name": "dig" if dig else None, "timeout_seconds": DNS_PROBE_TIMEOUT_SECONDS},
        "cache_state": "unknown",
        "provider_dns_service_attributed": False,
        "limitations": [
            "The system resolver may use a local stub, cache, split DNS, or an upstream service not visible to CloudMark.",
            "A single bounded lookup is diagnostic evidence, not a provider DNS latency or availability benchmark.",
        ],
    }
    if not dig:
        evidence["reason"] = "dig is not installed; only resolver configuration was observed."
    elif status != "complete":
        evidence["reason"] = "Resolver configuration or one of the fixed query outcomes was incomplete."
    return evidence


def _parse_ethtool_driver(stdout: str) -> dict[str, Any]:
    allowed = {
        "driver",
        "version",
        "firmware-version",
        "bus-info",
        "supports-statistics",
        "supports-test",
        "supports-eeprom-access",
        "supports-register-dump",
        "supports-priv-flags",
    }
    values: dict[str, str | bool] = {}
    for line in stdout.splitlines():
        key, separator, raw_value = line.partition(":")
        normalized_key = key.strip().lower()
        value = raw_value.strip()
        if not separator or normalized_key not in allowed or not value:
            continue
        output_key = normalized_key.replace("-", "_")
        if normalized_key.startswith("supports-") and value.lower() in {"yes", "no"}:
            values[output_key] = value.lower() == "yes"
        else:
            values[output_key] = value[:256]
    return values


def _parse_ethtool_features(stdout: str) -> dict[str, dict[str, bool]]:
    features: dict[str, dict[str, bool]] = {}
    pattern = re.compile(r"^([a-z0-9-]+):\s+(on|off)(?:\s+\[(fixed)\])?$")
    for line in stdout.splitlines():
        match = pattern.match(line.strip().lower())
        if not match or match.group(1) not in NETWORK_OFFLOAD_FEATURES:
            continue
        features[match.group(1)] = {
            "enabled": match.group(2) == "on",
            "fixed": match.group(3) == "fixed",
        }
    return features


def _queue_stat_identity(name: str) -> tuple[int, str] | None:
    for index, pattern in enumerate(NETWORK_QUEUE_STAT_PATTERNS):
        match = pattern.fullmatch(name)
        if not match:
            continue
        if index == 0:
            direction, queue_text, nested_direction, metric = match.groups()
            if nested_direction and nested_direction != direction:
                return None
        elif index == 1:
            queue_text, direction, metric = match.groups()
        else:
            direction, queue_text, metric = match.groups()
        queue = int(queue_text)
        if queue > NETWORK_QUEUE_MAX_INDEX:
            return None
        return queue, f"{direction}_{NETWORK_QUEUE_METRIC_ALIASES[metric]}"
    return None


def _parse_ethtool_queue_statistics(stdout: str) -> dict[str, Any]:
    """Normalize a bounded subset of common driver per-queue counter names."""
    lines = stdout.splitlines()
    queues: dict[int, dict[str, int]] = {}
    numeric_statistics = 0
    unclassified_statistics = 0
    duplicate_counters = 0
    for line in lines[:NETWORK_QUEUE_MAX_STAT_LINES]:
        key, separator, raw_value = line.partition(":")
        if not separator:
            continue
        value_text = raw_value.strip()
        if not re.fullmatch(r"\d+", value_text):
            continue
        numeric_statistics += 1
        identity = _queue_stat_identity(key.strip().lower())
        if identity is None:
            unclassified_statistics += 1
            continue
        queue, field = identity
        counters = queues.setdefault(queue, {})
        if field in counters:
            duplicate_counters += 1
            continue
        counters[field] = int(value_text)
    if not queues:
        return {
            "status": "unavailable",
            "source": "ethtool-nic-statistics",
            "queues": [],
            "numeric_statistics": numeric_statistics,
            "unclassified_statistics": unclassified_statistics,
            "truncated": len(lines) > NETWORK_QUEUE_MAX_STAT_LINES,
            "reason": "ethtool did not expose a recognized bounded per-queue counter name.",
        }
    normalized = [
        {"queue": queue, "counters": dict(sorted(counters.items()))}
        for queue, counters in sorted(queues.items())
    ]
    incomplete = duplicate_counters > 0 or len(lines) > NETWORK_QUEUE_MAX_STAT_LINES
    result: dict[str, Any] = {
        "status": "partial" if incomplete else "observed",
        "source": "ethtool-nic-statistics",
        "queues": normalized,
        "queue_count": len(normalized),
        "parsed_counters": sum(len(item["counters"]) for item in normalized),
        "numeric_statistics": numeric_statistics,
        "unclassified_statistics": unclassified_statistics,
        "duplicate_counters": duplicate_counters,
        "maximum_queue_index": NETWORK_QUEUE_MAX_INDEX,
        "truncated": len(lines) > NETWORK_QUEUE_MAX_STAT_LINES,
    }
    if incomplete:
        result["reason"] = "Duplicate normalized fields or output truncation made the queue snapshot partial."
    return result


def _bounded_text(path: Path, maximum_bytes: int) -> tuple[str | None, bool]:
    """Read a small text control file without allowing an unbounded evidence payload."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            value = handle.read(maximum_bytes + 1)
    except OSError:
        return None, False
    return value[:maximum_bytes].strip(), len(value) > maximum_bytes


def _parse_cpu_mask(value: str) -> dict[str, Any] | None:
    normalized = value.strip().lower()
    if not normalized or len(normalized.encode("utf-8")) > NETWORK_STEERING_MAX_MASK_BYTES:
        return None
    compact = normalized.replace(",", "")
    if not compact or not re.fullmatch(r"[0-9a-f]+", compact):
        return None
    return {
        "mask": normalized,
        "cpu_count": int(compact, 16).bit_count(),
    }


def _parse_cpu_list(value: str) -> dict[str, Any] | None:
    normalized = value.strip().lower()
    if not normalized or len(normalized.encode("utf-8")) > NETWORK_STEERING_MAX_MASK_BYTES:
        return None
    if not re.fullmatch(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*", normalized):
        return None
    cpus: set[int] = set()
    for part in normalized.split(","):
        bounds = part.split("-", 1)
        first = int(bounds[0])
        last = int(bounds[-1])
        if first > last or last > 8191:
            return None
        cpus.update(range(first, last + 1))
    return {
        "cpu_list": normalized,
        "cpu_count": len(cpus),
    }


def _parse_ethtool_rss_indirection(stdout: str) -> dict[str, Any]:
    """Normalize an RSS indirection table without retaining the NIC hash key."""
    lines = stdout.splitlines()
    entries: list[int] = []
    hash_functions: dict[str, bool] = {}
    in_table = False
    in_hash_functions = False
    incomplete = len(lines) > NETWORK_RSS_MAX_LINES
    for line in lines[:NETWORK_RSS_MAX_LINES]:
        lowered = line.strip().lower()
        if "indirection table" in lowered:
            in_table = True
            in_hash_functions = False
            continue
        if lowered.startswith("rss hash key"):
            in_table = False
            in_hash_functions = False
            continue
        if lowered.startswith("rss hash function"):
            in_table = False
            in_hash_functions = True
            continue
        if in_table:
            match = re.fullmatch(r"\s*\d+:\s+([0-9\s]+)", line)
            if not match:
                continue
            for queue_text in match.group(1).split():
                queue = int(queue_text)
                if queue > NETWORK_QUEUE_MAX_INDEX:
                    incomplete = True
                    continue
                if len(entries) >= NETWORK_RSS_MAX_ENTRIES:
                    incomplete = True
                    break
                entries.append(queue)
        elif in_hash_functions:
            match = re.fullmatch(r"\s*([a-z0-9_-]+):\s+(on|off)\s*", lowered)
            if match and match.group(1) in {"toeplitz", "xor", "crc32"}:
                hash_functions[match.group(1)] = match.group(2) == "on"
    if not entries:
        return {
            "status": "unavailable",
            "source": "ethtool-rss-indirection",
            "table_entry_count": 0,
            "active_queue_count": 0,
            "queue_distribution": [],
            "hash_functions": hash_functions,
            "hash_key_persisted": False,
            "truncated": incomplete,
            "reason": "ethtool did not expose a bounded RSS indirection table.",
        }
    counts: dict[int, int] = {}
    for queue in entries:
        counts[queue] = counts.get(queue, 0) + 1
    distribution = [
        {
            "queue": queue,
            "entries": count,
            "share_percent": round(count / len(entries) * 100, 6),
        }
        for queue, count in sorted(counts.items())
    ]
    busiest = max(distribution, key=lambda item: item["entries"])
    result: dict[str, Any] = {
        "status": "partial" if incomplete else "observed",
        "source": "ethtool-rss-indirection",
        "table_entry_count": len(entries),
        "active_queue_count": len(distribution),
        "queue_distribution": distribution,
        "busiest_queue": busiest["queue"],
        "busiest_queue_percent": busiest["share_percent"],
        "hash_functions": hash_functions,
        "hash_key_persisted": False,
        "maximum_queue_index": NETWORK_QUEUE_MAX_INDEX,
        "maximum_table_entries": NETWORK_RSS_MAX_ENTRIES,
        "truncated": incomplete,
    }
    if incomplete:
        result["reason"] = "The RSS table exceeded a bounded parser limit or contained an out-of-policy queue index."
    return result


def _collect_sysfs_queue_steering(
    interface_name: str,
    *,
    sys_class_net: Path = Path("/sys/class/net"),
    proc_irq: Path = Path("/proc/irq"),
) -> dict[str, Any]:
    """Collect bounded Linux RPS/XPS and MSI IRQ affinity from a route-derived NIC."""
    unavailable = {
        "status": "unavailable",
        "queues": [],
        "total_queue_count": 0,
        "configured_queue_count": 0,
    }
    if (
        not NETWORK_INTERFACE_PATTERN.fullmatch(interface_name)
        or interface_name in {".", ".."}
    ):
        return {
            "status": "unavailable",
            "source": "linux-sysfs-procfs",
            "rps": {**unavailable, "source": "linux-sysfs-rps", "reason": "The interface name is outside policy."},
            "xps": {**unavailable, "source": "linux-sysfs-xps", "reason": "The interface name is outside policy."},
            "irq_affinity": {
                "status": "unavailable",
                "source": "linux-procfs-msi-affinity",
                "msi_irq_count": 0,
                "observed_affinity_count": 0,
                "distinct_affinity_count": 0,
                "affinities": [],
                "reason": "The interface name is outside policy.",
            },
        }
    interface_path = sys_class_net / interface_name
    queue_path = interface_path / "queues"
    queue_entries: list[Path] = []
    queue_truncated = False
    try:
        for item in queue_path.iterdir():
            match = re.fullmatch(r"(?:rx|tx)-(\d+)", item.name)
            if not match:
                continue
            if int(match.group(1)) > NETWORK_QUEUE_MAX_INDEX:
                queue_truncated = True
                continue
            if len(queue_entries) >= 2 * (NETWORK_QUEUE_MAX_INDEX + 1):
                queue_truncated = True
                continue
            queue_entries.append(item)
    except OSError:
        queue_entries = []
    queue_entries.sort(
        key=lambda item: (item.name.split("-", 1)[0], int(item.name.split("-", 1)[1]))
    )

    def steering_rows(direction: str, filename: str, source: str) -> dict[str, Any]:
        matching = [item for item in queue_entries if item.name.startswith(f"{direction}-")]
        rows: list[dict[str, Any]] = []
        unreadable = 0
        for item in matching:
            queue = int(item.name.split("-", 1)[1])
            mask_text, mask_truncated = _bounded_text(item / filename, NETWORK_STEERING_MAX_MASK_BYTES)
            parsed_mask = _parse_cpu_mask(mask_text or "")
            if parsed_mask is None or mask_truncated:
                unreadable += 1
                continue
            row: dict[str, Any] = {
                "queue": queue,
                **parsed_mask,
                "configured": parsed_mask["cpu_count"] > 0,
            }
            if direction == "rx":
                flow_text, flow_truncated = _bounded_text(item / "rps_flow_cnt", 64)
                if flow_text is not None and not flow_truncated and re.fullmatch(r"\d+", flow_text):
                    row["flow_count"] = int(flow_text)
            rows.append(row)
        if not matching:
            return {
                "status": "unavailable",
                "source": source,
                "queues": [],
                "total_queue_count": 0,
                "configured_queue_count": 0,
                "reason": f"No {direction.upper()} queue directories were exposed for the route-derived interface.",
            }
        status = "observed" if len(rows) == len(matching) and not queue_truncated else "partial"
        result: dict[str, Any] = {
            "status": status,
            "source": source,
            "queues": rows,
            "total_queue_count": len(matching),
            "configured_queue_count": sum(1 for item in rows if item["configured"]),
            "unreadable_queue_count": unreadable,
            "truncated": queue_truncated,
        }
        if status == "partial":
            result["reason"] = "One or more bounded queue steering files were unavailable, invalid, or truncated."
        return result

    rps = steering_rows("rx", "rps_cpus", "linux-sysfs-rps")
    xps = steering_rows("tx", "xps_cpus", "linux-sysfs-xps")
    msi_path = interface_path / "device" / "msi_irqs"
    irq_values: list[int] = []
    irq_truncated = False
    try:
        for item in msi_path.iterdir():
            if not re.fullmatch(r"\d+", item.name):
                continue
            if len(irq_values) >= NETWORK_STEERING_MAX_IRQS:
                irq_truncated = True
                continue
            irq_values.append(int(item.name))
    except OSError:
        irq_values = []
    irq_values.sort()
    affinities: list[dict[str, Any]] = []
    for irq in irq_values:
        affinity_text, affinity_truncated = _bounded_text(
            proc_irq / str(irq) / "smp_affinity_list",
            NETWORK_STEERING_MAX_MASK_BYTES,
        )
        parsed_affinity = _parse_cpu_list(affinity_text or "")
        if parsed_affinity is not None and not affinity_truncated:
            affinities.append({"irq": irq, **parsed_affinity})
    if not irq_values:
        irq_affinity: dict[str, Any] = {
            "status": "unavailable",
            "source": "linux-procfs-msi-affinity",
            "msi_irq_count": 0,
            "observed_affinity_count": 0,
            "distinct_affinity_count": 0,
            "affinities": [],
            "truncated": False,
            "reason": "The route-derived interface did not expose MSI IRQ entries.",
        }
    else:
        irq_status = "observed" if len(affinities) == len(irq_values) and not irq_truncated else "partial"
        irq_affinity = {
            "status": irq_status,
            "source": "linux-procfs-msi-affinity",
            "msi_irq_count": len(irq_values),
            "observed_affinity_count": len(affinities),
            "distinct_affinity_count": len({item["cpu_list"] for item in affinities}),
            "affinities": affinities,
            "truncated": irq_truncated,
        }
        if irq_status == "partial":
            irq_affinity["reason"] = "One or more bounded MSI IRQ affinity files were unavailable, invalid, or truncated."
    component_statuses = [rps["status"], xps["status"], irq_affinity["status"]]
    if all(status == "observed" for status in component_statuses):
        status = "complete"
    elif any(status in {"observed", "partial"} for status in component_statuses):
        status = "partial"
    else:
        status = "unavailable"
    return {
        "status": status,
        "source": "linux-sysfs-procfs",
        "rps": rps,
        "xps": xps,
        "irq_affinity": irq_affinity,
        "bounds": {
            "maximum_queues": NETWORK_QUEUE_MAX_INDEX + 1,
            "maximum_msi_irqs": NETWORK_STEERING_MAX_IRQS,
            "maximum_control_file_bytes": NETWORK_STEERING_MAX_MASK_BYTES,
        },
    }


def _steering_evidence(
    interface_name: str,
    *,
    ethtool: str | None,
    environment: dict[str, str],
) -> dict[str, Any]:
    sysfs = _collect_sysfs_queue_steering(interface_name)
    rss: dict[str, Any] = {
        "status": "unavailable",
        "source": "ethtool-rss-indirection",
        "table_entry_count": 0,
        "active_queue_count": 0,
        "queue_distribution": [],
        "hash_key_persisted": False,
        "reason": "ethtool is not installed or the interface name is outside policy.",
    }
    if ethtool and NETWORK_INTERFACE_PATTERN.fullmatch(interface_name) and interface_name not in {".", ".."}:
        try:
            rss_result = subprocess.run(
                [ethtool, "-x", interface_name],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                shell=False,
                env=environment,
            )
            if rss_result.returncode == 0:
                rss = _parse_ethtool_rss_indirection(rss_result.stdout)
            else:
                rss["reason"] = (
                    rss_result.stderr.strip()[:256]
                    or "ethtool did not expose an RSS indirection table for the route-derived interface."
                )
        except subprocess.TimeoutExpired:
            rss["reason"] = "The bounded ethtool RSS query timed out."
    component_statuses = [
        str(rss.get("status", "unavailable")),
        str((sysfs.get("rps") or {}).get("status", "unavailable")),
        str((sysfs.get("xps") or {}).get("status", "unavailable")),
        str((sysfs.get("irq_affinity") or {}).get("status", "unavailable")),
    ]
    if all(status == "observed" for status in component_statuses):
        status = "complete"
    elif any(status in {"observed", "partial"} for status in component_statuses):
        status = "partial"
    else:
        status = "unavailable"
    return {
        "status": status,
        "scope": "route-derived-interface-queue-steering-and-irq-affinity",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "interface": interface_name,
        "rss": rss,
        "rps": sysfs["rps"],
        "xps": sysfs["xps"],
        "irq_affinity": sysfs["irq_affinity"],
        "policy": {
            "read_only": True,
            "network_configuration_changed": False,
            "rss_hash_key_persisted": False,
            "maximum_rss_entries": NETWORK_RSS_MAX_ENTRIES,
            **(sysfs.get("bounds") or {}),
        },
        "limitations": [
            "Guest-visible RSS, RPS, XPS, and IRQ affinity do not prove physical-host NIC or provider-fabric configuration.",
            "Missing virtual-NIC controls remain unavailable evidence and do not become a zero score.",
        ],
    }


def _tcp_congestion_control() -> dict[str, Any]:
    try:
        algorithm = Path("/proc/sys/net/ipv4/tcp_congestion_control").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        algorithm = ""
    if not algorithm:
        return {
            "status": "unavailable",
            "algorithm": None,
            "source": "linux-procfs",
            "reason": "The active TCP congestion-control algorithm is not exposed by procfs.",
        }
    return {
        "status": "observed",
        "algorithm": algorithm[:64],
        "source": "linux-procfs",
    }


def _link_counters(link: dict[str, Any] | None) -> dict[str, Any]:
    statistics = (link or {}).get("stats64") or (link or {}).get("stats") or {}
    values: dict[str, int] = {}
    for direction in ("rx", "tx"):
        direction_values = statistics.get(direction) if isinstance(statistics, dict) else None
        if not isinstance(direction_values, dict):
            continue
        for field in ("bytes", "packets", "errors", "dropped"):
            value = direction_values.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                values[f"{direction}_{field}"] = value
    expected = {
        f"{direction}_{field}"
        for direction in ("rx", "tx")
        for field in ("bytes", "packets", "errors", "dropped")
    }
    if expected.issubset(values):
        return {"status": "observed", **values, "source": "iproute2-link-stats"}
    if values:
        return {
            "status": "partial",
            **values,
            "source": "iproute2-link-stats",
            "reason": "iproute2 did not expose every required interface counter.",
        }
    return {
        "status": "unavailable",
        "source": "iproute2-link-stats",
        "reason": "iproute2 did not expose interface counters in structured output.",
    }


def _validate_controller(controller: str, allow_http: bool) -> str:
    base = controller.rstrip("/")
    parsed = urlparse(base)
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Controller must be a valid HTTP or HTTPS base URL.")
    if parsed.scheme != "https" and parsed.hostname not in local_hosts and not allow_http:
        raise ValueError("Remote controller must use HTTPS. Add --allow-http only on a trusted, isolated private network.")
    return base


def _default_address(inventory: dict[str, Any]) -> str | None:
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for item in inventory.get("network", {}).get("addresses", []):
        try:
            address = ipaddress.ip_address(str(item.get("address", "")))
        except ValueError:
            continue
        if address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local:
            continue
        candidates.append(address)
    private = next((item for item in candidates if item.is_private), None)
    return str(private or (candidates[0] if candidates else "")) or None


def _request_json(
    url: str,
    *,
    data: dict[str, Any] | None = None,
    agent_token: str | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if agent_token:
        headers["X-CloudMark-Agent-Token"] = agent_token
    request = urllib.request.Request(
        url,
        data=json.dumps(data or {}).encode("utf-8") if data is not None else None,
        method="POST" if data is not None else "GET",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"Controller request failed ({exc.code}): {detail}") from exc


def join_session(
    controller: str,
    session_id: str,
    join_token: str,
    role: str,
    name: str | None = None,
    *,
    advertise_address: str | None = None,
    allow_http: bool = False,
) -> dict[str, Any]:
    base = _validate_controller(controller, allow_http)
    inventory = collect_inventory(Path.cwd())
    address = advertise_address or _default_address(inventory)
    if address:
        try:
            parsed_address = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError("--advertise-address must be an IPv4 or IPv6 address.") from exc
        if parsed_address.is_loopback or parsed_address.is_unspecified or parsed_address.is_multicast or parsed_address.is_link_local:
            raise ValueError("--advertise-address must be a peer-reachable unicast address.")
    body = {
        "join_token": join_token,
        "role": role,
        "name": name or socket.gethostname(),
        "endpoint": {"address": address} if address else {},
        "system": {"inventory": inventory, "provider": detect_provider()},
    }
    return _request_json(f"{base}/api/v1/sessions/{session_id}/join", data=body)


@dataclass
class ActiveServer:
    process: subprocess.Popen[str]
    deadline: float


@dataclass
class ActiveDatabaseServer:
    process: subprocess.Popen[str]
    deadline: float
    root: Path
    data_dir: Path
    log_path: Path
    log_handle: Any
    postgres_version: str | None
    pgbench_version: str | None
    scale_factor: int
    port: int
    settings: dict[str, Any]


@dataclass
class ActiveWebServer:
    process: subprocess.Popen[str]
    deadline: float
    root: Path
    config_path: Path
    log_path: Path
    log_handle: Any
    nginx: str
    nginx_version: str | None
    openssl_version: str | None
    http_port: int
    https_port: int
    application_process: subprocess.Popen[str] | None
    application_log_path: Path | None
    application_log_handle: Any | None
    methodology_version: str

@dataclass
class ActiveRedisServer:
    process: subprocess.Popen[str]
    deadline: float
    root: Path
    log_path: Path
    log_handle: Any
    version: str | None
    port: int


class AgentBenchmarkFailure(RuntimeError):
    def __init__(self, message: str, *, status: str, result: dict[str, Any] | None):
        super().__init__(message)
        self.status = status
        self.result = result


class AgentWorker:
    def __init__(
        self,
        controller: str,
        agent_id: str,
        agent_token: str,
        *,
        allow_http: bool = False,
        poll_seconds: float = 1.0,
        workspace: Path = Path(".cloudmark/agent-workspace"),
    ) -> None:
        self.controller = _validate_controller(controller, allow_http)
        self.agent_id = agent_id
        self.agent_token = agent_token
        self.poll_seconds = max(0.25, min(poll_seconds, 10.0))
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.active_servers: dict[str, ActiveServer] = {}
        self.active_database_servers: dict[str, ActiveDatabaseServer] = {}
        self.active_web_servers: dict[str, ActiveWebServer] = {}
        self.active_redis_servers: dict[str, ActiveRedisServer] = {}
        self.pending_completions: dict[str, dict[str, Any]] = {}
        self.last_controller_contact = time.monotonic()

    def _api(self, suffix: str, data: dict[str, Any], *, timeout: float = 45) -> dict[str, Any]:
        response = _request_json(
            f"{self.controller}/api/v1/agents/{self.agent_id}/{suffix}",
            data=data,
            agent_token=self.agent_token,
            timeout=timeout,
        )
        self.last_controller_contact = time.monotonic()
        return response

    @staticmethod
    def _iperf() -> str:
        executable = shutil.which("iperf3")
        if not executable:
            raise NetworkError("iperf3 is not installed or is not on PATH. Run CloudMark bootstrap with the network pack.")
        return executable

    @staticmethod
    def _port(value: Any) -> int:
        port = int(value)
        if not ALLOWED_PORT_MIN <= port <= ALLOWED_PORT_MAX:
            raise NetworkError("Task port is outside the CloudMark allow-list.")
        return port

    def _start_server(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        port = self._port(payload.get("port"))
        deadline_seconds = max(15, min(int(payload.get("deadline_seconds", 60)), 120))
        family = str(payload.get("address_family", "ipv4"))
        if family not in {"ipv4", "ipv6"}:
            raise NetworkError("Task address family is outside the CloudMark allow-list.")
        process = subprocess.Popen(
            [
                self._iperf(),
                "--server",
                "--one-off",
                "--json",
                "--version6" if family == "ipv6" else "--version4",
                "--port",
                str(port),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        time.sleep(0.15)
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise NetworkError(stderr.strip() or stdout.strip() or "iperf3 server exited before becoming ready.")
        self.active_servers[task_id] = ActiveServer(process, time.monotonic() + deadline_seconds)
        return {"ready": True, "port": port, "pid": process.pid}

    @staticmethod
    def _peer_address(value: Any) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
        address = str(value or "")
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise NetworkError("Task target_address is not an IP address.") from exc
        if parsed.is_loopback or parsed.is_unspecified or parsed.is_multicast or parsed.is_link_local:
            raise NetworkError("Task target_address is not a peer-reachable unicast address.")
        return parsed

    def _run_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        address = str(payload.get("target_address", ""))
        parsed = self._peer_address(address)
        port = self._port(payload.get("port"))
        duration = max(1, min(int(payload.get("duration_seconds", 10)), 60))
        streams = int(payload.get("streams", 1))
        if streams not in ALLOWED_STREAMS:
            raise NetworkError("Task stream count is outside the CloudMark allow-list.")
        protocol = str(payload.get("protocol", "tcp"))
        if protocol not in {"tcp", "udp"}:
            raise NetworkError("Task protocol is outside the CloudMark allow-list.")
        bidirectional = payload.get("bidirectional") is True
        if bidirectional and protocol != "tcp":
            raise NetworkError("Agent refused bidirectional mode for a non-TCP task.")
        target_rate_bps: int | None = None
        if protocol == "udp":
            if streams != 1:
                raise NetworkError("Agent requires exactly one stream for guarded UDP tasks.")
            target_rate_bps = int(payload.get("target_rate_bps", 0))
            if not ALLOWED_UDP_RATE_MIN <= target_rate_bps <= ALLOWED_UDP_RATE_MAX:
                raise NetworkError("Task UDP rate is outside the CloudMark allow-list.")
        command = [
            self._iperf(),
            "--client",
            address,
            "--version6" if parsed.version == 6 else "--version4",
            "--port",
            str(port),
            "--time",
            str(duration),
            "--parallel",
            str(streams),
            "--json",
        ]
        if protocol == "udp":
            command.extend(["--udp", "--bitrate", str(target_rate_bps)])
        if bidirectional:
            command.append("--bidir")
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=duration + 20,
                check=False,
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired as exc:
            raise NetworkError("iperf3 client exceeded its guarded task timeout.") from exc
        if result.returncode != 0:
            raise NetworkError(result.stderr.strip() or result.stdout.strip() or "iperf3 client failed.")
        return {
            "iperf": parse_iperf_json(result.stdout),
            "command": {
                "duration_seconds": duration,
                "streams": streams,
                "protocol": protocol,
                "target_rate_bps": target_rate_bps,
                "bidirectional": bidirectional,
            },
        }

    def _run_latency(self, payload: dict[str, Any]) -> dict[str, Any]:
        address = str(payload.get("target_address", ""))
        parsed = self._peer_address(address)
        count = int(payload.get("count", 20))
        interval_ms = int(payload.get("interval_ms", 100))
        timeout_ms = int(payload.get("timeout_ms", 1000))
        if not 1 <= count <= ALLOWED_LATENCY_COUNT_MAX:
            raise NetworkError("Task ping count is outside the CloudMark allow-list.")
        if not ALLOWED_LATENCY_INTERVAL_MS_MIN <= interval_ms <= ALLOWED_LATENCY_INTERVAL_MS_MAX:
            raise NetworkError("Task ping interval is outside the CloudMark allow-list.")
        if not ALLOWED_LATENCY_TIMEOUT_MS_MIN <= timeout_ms <= ALLOWED_LATENCY_TIMEOUT_MS_MAX:
            raise NetworkError("Task ping timeout is outside the CloudMark allow-list.")
        executable = shutil.which("ping")
        if not executable:
            raise NetworkError("ping is not installed or is not on PATH.")
        if os.name == "nt":
            command = [
                executable,
                "-n",
                str(count),
                "-w",
                str(timeout_ms),
                "-6" if parsed.version == 6 else "-4",
                address,
            ]
        else:
            command = [
                executable,
                "-n",
                "-c",
                str(count),
                "-i",
                f"{interval_ms / 1000:.3f}",
                "-W",
                str(max(1, (timeout_ms + 999) // 1000)),
                "-6" if parsed.version == 6 else "-4",
                address,
            ]
        environment = os.environ.copy()
        if os.name != "nt":
            environment["LC_ALL"] = "C"
        guarded_timeout = min(120, count * (interval_ms + timeout_ms) / 1000 + 10)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=guarded_timeout,
                check=False,
                shell=False,
                env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired as exc:
            raise NetworkError("ping exceeded its guarded task timeout.") from exc
        if result.returncode not in {0, 1}:
            raise NetworkError(result.stderr.strip() or result.stdout.strip() or "ping failed.")
        metrics = parse_ping_output(result.stdout)
        if metrics["received"] <= 0:
            raise NetworkError("Peer latency task received no replies.")
        return {
            "latency": metrics,
            "tool": {"name": "ping", "version": None},
            "raw": {"stdout": result.stdout},
            "command": {"count": count, "interval_ms": interval_ms, "timeout_ms": timeout_ms},
        }

    def _run_path_probe(self, payload: dict[str, Any]) -> dict[str, Any]:
        address = str(payload.get("target_address", ""))
        parsed = self._peer_address(address)
        collect_resolver = payload.get("resolver_probe") is True
        collect_steering = payload.get("steering_probe") is True
        policy = {
            "passive_route_lookup": True,
            "path_probe_max_hops": PATH_PROBE_MAX_HOPS,
            "arbitrary_arguments_allowed": False,
            "read_only_nic_evidence": True,
            "network_configuration_changed": False,
            "public_internet_traversal_inferred": False,
            "fixed_resolver_diagnostic": collect_resolver,
            "bounded_queue_steering_and_irq_evidence": collect_steering,
        }
        if os.name == "nt":
            return {
                "status": "unavailable",
                "reason": "Route and MTU evidence is not implemented for Windows Agents.",
                "address_family": f"ipv{parsed.version}",
                "policy": policy,
            }
        ip_tool = shutil.which("ip")
        if not ip_tool:
            return {
                "status": "unavailable",
                "reason": "The ip route tool is not installed or is not on PATH.",
                "address_family": f"ipv{parsed.version}",
                "policy": policy,
            }
        environment = os.environ.copy()
        environment["LC_ALL"] = "C"
        route_command = [ip_tool, f"-{parsed.version}", "-j", "route", "get", address]
        try:
            route_result = subprocess.run(
                route_command,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                shell=False,
                env=environment,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "unavailable",
                "reason": "The bounded route lookup timed out.",
                "address_family": f"ipv{parsed.version}",
                "policy": policy,
            }
        route: dict[str, Any] | None = None
        try:
            route_items = json.loads(route_result.stdout) if route_result.returncode == 0 else None
        except json.JSONDecodeError:
            route_items = None
        if isinstance(route_items, list) and route_items and isinstance(route_items[0], dict):
            route = route_items[0]
        if route is None:
            plain_route_command = [ip_tool, f"-{parsed.version}", "route", "get", address]
            try:
                plain_route_result = subprocess.run(
                    plain_route_command,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                    shell=False,
                    env=environment,
                )
            except subprocess.TimeoutExpired:
                plain_route_result = None
            if plain_route_result is not None and plain_route_result.returncode == 0:
                tokens = plain_route_result.stdout.split()

                def token_after(name: str) -> str | None:
                    try:
                        return tokens[tokens.index(name) + 1]
                    except (ValueError, IndexError):
                        return None

                route = {
                    "dst": tokens[0] if tokens else address,
                    "gateway": token_after("via"),
                    "dev": token_after("dev"),
                    "prefsrc": token_after("src"),
                }
        interface_name = str((route or {}).get("dev", "")).strip()
        if not route or not interface_name:
            return {
                "status": "unavailable",
                "reason": route_result.stderr.strip() or "The route lookup did not identify an egress interface.",
                "address_family": f"ipv{parsed.version}",
                "policy": policy,
            }
        link_command = [ip_tool, "-s", "-j", "link", "show", "dev", interface_name]
        link_result: subprocess.CompletedProcess[str] | None = None
        try:
            link_result = subprocess.run(
                link_command,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                shell=False,
                env=environment,
            )
        except subprocess.TimeoutExpired:
            pass
        link: dict[str, Any] | None = None
        if link_result is not None and link_result.returncode == 0:
            try:
                link_items = json.loads(link_result.stdout)
                if isinstance(link_items, list) and link_items and isinstance(link_items[0], dict):
                    link = link_items[0]
            except json.JSONDecodeError:
                link = None
        if link is None:
            plain_link_command = [ip_tool, "link", "show", "dev", interface_name]
            try:
                plain_link_result = subprocess.run(
                    plain_link_command,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                    shell=False,
                    env=environment,
                )
            except subprocess.TimeoutExpired:
                plain_link_result = None
            if plain_link_result is not None and plain_link_result.returncode == 0:
                mtu_match = re.search(r"\bmtu\s+(\d+)\b", plain_link_result.stdout)
                state_match = re.search(r"\bstate\s+(\S+)", plain_link_result.stdout)
                link = {
                    "ifname": interface_name,
                    "mtu": int(mtu_match.group(1)) if mtu_match else None,
                    "operstate": state_match.group(1) if state_match else None,
                }
        link_counters = _link_counters(link)
        link_counters["observed_at"] = datetime.now(timezone.utc).isoformat()
        path_mtu: dict[str, Any] = {"status": "unavailable", "value_bytes": None, "source": None}
        path_trace: dict[str, Any] = {
            "status": "unavailable",
            "tool": None,
            "max_hops": PATH_PROBE_MAX_HOPS,
            "destination_address_class": _address_class(parsed),
            "hops": [],
            "reached_destination": False,
            "public_internet_traversal_proven": False,
            "limitation": "Observed IP hops do not prove administrative ownership or public Internet transit.",
            "reason": "tracepath is not installed or is not on PATH.",
        }
        trace_tool = shutil.which("tracepath")
        if trace_tool:
            trace_command = [trace_tool, "-n", "-m", str(PATH_PROBE_MAX_HOPS), address]
            try:
                trace_result = subprocess.run(
                    trace_command,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                    shell=False,
                    env=environment,
                )
                match = re.search(r"\bpmtu\s+(\d+)\b", trace_result.stdout, re.IGNORECASE)
                if match:
                    path_mtu = {
                        "status": "observed",
                        "value_bytes": int(match.group(1)),
                        "source": "tracepath",
                    }
                path_trace = _parse_tracepath(trace_result.stdout, parsed)
            except subprocess.TimeoutExpired:
                path_trace["tool"] = "tracepath"
                path_trace["reason"] = "The bounded tracepath query timed out."
        driver_evidence: dict[str, Any] = {
            "status": "unavailable",
            "reason": "ethtool is not installed or the egress interface is outside the fixed interface-name policy.",
        }
        offload_evidence: dict[str, Any] = {
            "status": "unavailable",
            "features": {},
            "reason": "ethtool is not installed or the egress interface is outside the fixed interface-name policy.",
        }
        queue_counter_evidence: dict[str, Any] = {
            "status": "unavailable",
            "source": "ethtool-nic-statistics",
            "queues": [],
            "reason": "ethtool is not installed or the egress interface is outside the fixed interface-name policy.",
        }
        ethtool = shutil.which("ethtool")
        if ethtool and NETWORK_INTERFACE_PATTERN.fullmatch(interface_name):
            try:
                driver_result = subprocess.run(
                    [ethtool, "-i", interface_name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                    shell=False,
                    env=environment,
                )
                driver = _parse_ethtool_driver(driver_result.stdout) if driver_result.returncode == 0 else {}
                if driver.get("driver"):
                    driver_evidence = {"status": "observed", **driver}
                else:
                    driver_evidence["reason"] = (
                        driver_result.stderr.strip()[:256] or "ethtool did not expose driver identity."
                    )
            except subprocess.TimeoutExpired:
                driver_evidence["reason"] = "The bounded ethtool driver query timed out."
            try:
                feature_result = subprocess.run(
                    [ethtool, "-k", interface_name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                    shell=False,
                    env=environment,
                )
                features = _parse_ethtool_features(feature_result.stdout) if feature_result.returncode == 0 else {}
                if features:
                    offload_evidence = {"status": "observed", "features": features}
                else:
                    offload_evidence["reason"] = (
                        feature_result.stderr.strip()[:256] or "ethtool did not expose selected offload features."
                    )
            except subprocess.TimeoutExpired:
                offload_evidence["reason"] = "The bounded ethtool feature query timed out."
            try:
                queue_result = subprocess.run(
                    [ethtool, "-S", interface_name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                    shell=False,
                    env=environment,
                )
                if queue_result.returncode == 0:
                    queue_counter_evidence = _parse_ethtool_queue_statistics(queue_result.stdout)
                    queue_counter_evidence["observed_at"] = datetime.now(timezone.utc).isoformat()
                else:
                    queue_counter_evidence["reason"] = (
                        queue_result.stderr.strip()[:256]
                        or "ethtool did not expose NIC statistics for the route-derived interface."
                    )
            except subprocess.TimeoutExpired:
                queue_counter_evidence["reason"] = "The bounded ethtool statistics query timed out."
        interface_mtu = (link or {}).get("mtu")
        status = "complete" if isinstance(interface_mtu, int) else "partial"
        evidence = {
            "status": status,
            "address_family": f"ipv{parsed.version}",
            "route": {
                "destination": route.get("dst"),
                "gateway": route.get("gateway"),
                "gateway_address_class": _address_value_class(route.get("gateway")) if route.get("gateway") else None,
                "source": route.get("prefsrc") or route.get("src"),
                "interface": interface_name,
            },
            "interface": {
                "name": interface_name,
                "mtu_bytes": interface_mtu,
                "state": (link or {}).get("operstate"),
                "link_type": (link or {}).get("link_type"),
                "driver": driver_evidence,
                "offloads": offload_evidence,
                "counters": link_counters,
                "queue_counters": queue_counter_evidence,
            },
            "tcp": {"congestion_control": _tcp_congestion_control()},
            "path_mtu": path_mtu,
            "path_trace": path_trace,
            "tool": {
                "route": "iproute2",
                "path_mtu": "tracepath" if trace_tool else None,
                "nic": "ethtool" if ethtool else None,
            },
            "policy": policy,
        }
        if collect_resolver:
            evidence["resolver"] = _resolver_evidence()
        if collect_steering:
            evidence["steering"] = _steering_evidence(
                interface_name,
                ethtool=ethtool,
                environment=environment,
            )
        return evidence

    def _collect_server(self, payload: dict[str, Any]) -> dict[str, Any]:
        server_task_id = str(payload.get("server_task_id", ""))
        active = self.active_servers.pop(server_task_id, None)
        if not active:
            raise NetworkError("The requested iperf3 server is not active on this agent.")
        timeout = max(1, min(int(payload.get("timeout_seconds", 15)), 30))
        try:
            stdout, stderr = active.process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            active.process.kill()
            active.process.communicate()
            raise NetworkError("iperf3 server did not finish after the peer measurement.") from exc
        if active.process.returncode != 0:
            raise NetworkError(stderr.strip() or stdout.strip() or "iperf3 server failed.")
        return {"iperf": parse_iperf_json(stdout)}

    @staticmethod
    def _postgres_tool(name: str) -> str:
        executable = find_postgres_binary(name)
        if not executable:
            raise DatabaseBenchmarkError(
                f"{name} is not installed or discoverable. Run CloudMark bootstrap with the database pack."
            )
        return executable

    @staticmethod
    def _database_port(value: Any) -> int:
        port = int(value)
        if not DATABASE_PORT_MIN <= port <= DATABASE_PORT_MAX:
            raise DatabaseBenchmarkError("Database task port is outside the CloudMark allow-list.")
        return port

    def _database_root(self, task_id: str) -> Path:
        if not task_id.startswith("task_") or not task_id.removeprefix("task_").isalnum():
            raise DatabaseBenchmarkError("Database service task ID is invalid.")
        base = (self.workspace / "database-services").resolve()
        root = (base / task_id).resolve()
        try:
            root.relative_to(base)
        except ValueError as exc:
            raise DatabaseBenchmarkError("Database service path escaped the Agent workspace.") from exc
        return root

    def _database_client_log_root(self, task_id: str) -> Path:
        if not task_id.startswith("task_") or not task_id.removeprefix("task_").isalnum():
            raise DatabaseBenchmarkError("Database client task ID is invalid.")
        base = (self.workspace / "database-client-logs").resolve()
        root = (base / task_id).resolve()
        try:
            root.relative_to(base)
        except ValueError as exc:
            raise DatabaseBenchmarkError("Database client log path escaped the Agent workspace.") from exc
        return root

    def _remove_database_client_log_root(self, root: Path) -> bool:
        base = (self.workspace / "database-client-logs").resolve()
        resolved = root.resolve()
        try:
            relative = resolved.relative_to(base)
        except ValueError as exc:
            raise DatabaseBenchmarkError("Agent refused cleanup outside its database client-log workspace.") from exc
        if not relative.parts:
            raise DatabaseBenchmarkError("Agent refused cleanup of the database client-log root.")
        if resolved.exists():
            shutil.rmtree(resolved)
        return not resolved.exists()

    @staticmethod
    def _read_pgbench_latency_logs(root: Path, *, expected_transactions: int) -> dict[str, Any]:
        remaining = DATABASE_TAIL_LOG_MAX_BYTES
        chunks: list[bytes] = []
        truncated = False
        try:
            log_paths = sorted(
                item for item in root.iterdir()
                if item.is_file() and re.fullmatch(r"pgbench_log\.\d+(?:\.\d+)?", item.name)
            )
        except OSError:
            log_paths = []
        for path in log_paths:
            if remaining <= 0:
                truncated = True
                break
            try:
                with path.open("rb") as handle:
                    chunk = handle.read(remaining + 1)
            except OSError:
                truncated = True
                continue
            if len(chunk) > remaining:
                chunks.append(chunk[:remaining])
                truncated = True
                remaining = 0
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return parse_pgbench_latency_log(
            b"\n".join(chunks).decode("utf-8", errors="replace"),
            expected_transactions=expected_transactions,
            truncated=truncated,
        )

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str], timeout: float = 10) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    @staticmethod
    def _tail_text(path: Path, limit: int = 16_384) -> str:
        if not path.is_file():
            return ""
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            return handle.read().decode(errors="replace")

    def _service_control_update(
        self,
        task_id: str,
        *,
        phase: str,
        current_job: str,
        completed_steps: int,
        total_steps: int,
    ) -> None:
        try:
            control = self._api(
                f"tasks/{task_id}/progress",
                {
                    "progress": min(0.99, completed_steps / max(1, total_steps)),
                    "phase": phase,
                    "current_job": current_job,
                    "completed_steps": completed_steps,
                    "total_steps": total_steps,
                },
                timeout=5,
            )
        except (RuntimeError, urllib.error.URLError, TimeoutError, OSError) as exc:
            if time.monotonic() - self.last_controller_contact > SERVICE_CONTROLLER_CONTACT_TIMEOUT_SECONDS:
                raise AgentBenchmarkFailure(
                    "Service stopped after losing Controller contact.", status="failed", result=None
                ) from exc
            return
        if control.get("cancel_requested") or control.get("task_status") != "running":
            raise AgentBenchmarkFailure("Service cancelled by the Controller.", status="cancelled", result=None)

    def _remove_database_root(self, root: Path) -> bool:
        base = (self.workspace / "database-services").resolve()
        resolved = root.resolve()
        try:
            relative = resolved.relative_to(base)
        except ValueError as exc:
            raise DatabaseBenchmarkError("Agent refused cleanup outside its database service workspace.") from exc
        if not relative.parts:
            raise DatabaseBenchmarkError("Agent refused cleanup of the database service root.")
        if resolved.exists():
            shutil.rmtree(resolved)
        return not resolved.exists()

    def _stop_database_server(self, server_task_id: str) -> dict[str, Any]:
        active = self.active_database_servers.get(server_task_id)
        if not active:
            root = self._database_root(server_task_id)
            return {
                "status": "already-absent",
                "cleanup_verified": not root.exists(),
                "server_task_id": server_task_id,
            }
        self._terminate_process(active.process, timeout=15)
        active.log_handle.close()
        log_tail = self._tail_text(active.log_path)
        cleaned = self._remove_database_root(active.root)
        self.active_database_servers.pop(server_task_id, None)
        return {
            "status": "completed",
            "cleanup_verified": cleaned,
            "server_task_id": server_task_id,
            "server_returncode": active.process.returncode,
            "postgres_log_tail": log_tail,
        }

    def _start_database_server(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.active_database_servers or self.active_web_servers:
            raise DatabaseBenchmarkError("Agent already has an active paired service.")
        service_root = self.workspace / "database-services"
        if service_root.is_dir() and any(item.is_dir() for item in service_root.iterdir()):
            raise DatabaseBenchmarkError(
                "Agent found a residual database service directory. Verify that no CloudMark PostgreSQL process "
                "is running, then follow the recovery runbook before retrying."
            )
        if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
            raise DatabaseBenchmarkError("PostgreSQL assessment must run from a non-root Agent account.")
        listen_address = str(payload.get("listen_address", ""))
        allowed_client = str(payload.get("allowed_client_address", ""))
        parsed_listen = self._peer_address(listen_address)
        parsed_client = self._peer_address(allowed_client)
        port = self._database_port(payload.get("port"))
        methodology_version = str(payload.get("methodology_version") or "database-postgresql-v1")
        if methodology_version not in {"database-postgresql-v1", "database-postgresql-v2"}:
            raise DatabaseBenchmarkError("Database service methodology is outside the installed contract.")
        scale_factor = int(payload.get("scale_factor", 0))
        max_connections = int(payload.get("max_connections", 0))
        deadline_seconds = int(payload.get("deadline_seconds", 0))
        completed_steps = int(payload.get("run_completed_steps", -1))
        total_steps = int(payload.get("run_total_steps", 0))
        if not 1 <= scale_factor <= DATABASE_MAX_SCALE:
            raise DatabaseBenchmarkError("Database scale factor is outside the CloudMark allow-list.")
        if not 11 <= max_connections <= 64:
            raise DatabaseBenchmarkError("Database max_connections is outside the CloudMark allow-list.")
        if not 120 <= deadline_seconds <= 3_600:
            raise DatabaseBenchmarkError("Database service deadline is outside the CloudMark allow-list.")
        if completed_steps != 0 or not 2 <= total_steps <= 64:
            raise DatabaseBenchmarkError("Database setup progress metadata is invalid.")

        root = self._database_root(task_id)
        data_dir = root / "cluster"
        log_path = root / "postgres.log"
        estimated_bytes = scale_factor * 20 * 1024 * 1024
        disk = shutil.disk_usage(self.workspace)
        reserve = max(1024**3, int(disk.total * 0.05))
        if disk.free < estimated_bytes + reserve:
            raise DatabaseBenchmarkError("Insufficient free space for the ephemeral database dataset and safety reserve.")
        root.mkdir(parents=True, exist_ok=False)

        initdb = self._postgres_tool("initdb")
        postgres = self._postgres_tool("postgres")
        pgbench = self._postgres_tool("pgbench")
        pg_isready = self._postgres_tool("pg_isready")
        environment = os.environ.copy()
        environment["LC_ALL"] = "C"
        log_handle: Any | None = None
        process: subprocess.Popen[str] | None = None
        try:
            init_code, init_stdout, init_stderr = self._guarded_service_process(
                task_id,
                [
                    initdb,
                    "-D",
                    str(data_dir),
                    "-U",
                    "cloudmark",
                    "--auth-local=trust",
                    "--auth-host=trust",
                ],
                environment=environment,
                expected_duration=120,
                phase="database-initialization",
                current_job="initdb",
                completed_steps=completed_steps,
                total_steps=total_steps,
            )
            if init_code != 0:
                raise DatabaseBenchmarkError(init_stderr.strip() or init_stdout.strip() or "initdb failed.")
            prefix = 128 if parsed_client.version == 6 else 32
            (data_dir / "pg_hba.conf").write_text(
                "local all cloudmark trust\n"
                "host postgres cloudmark 127.0.0.1/32 trust\n"
                "host cloudmark_restore cloudmark 127.0.0.1/32 trust\n"
                "host postgres cloudmark ::1/128 trust\n"
                f"host postgres cloudmark {allowed_client}/{prefix} trust\n",
                encoding="utf-8",
            )
            settings = {
                "fsync": "on",
                "full_page_writes": "on",
                "synchronous_commit": "on",
                "shared_buffers": "128MB",
                "max_connections": max_connections,
                "unix_socket_directories": "",
            }
            listen_value = f"{listen_address},127.0.0.1" if parsed_listen.version == 4 else f"{listen_address},::1"
            command = [postgres, "-D", str(data_dir), "-h", listen_value, "-p", str(port)]
            for key, value in settings.items():
                command.extend(["-c", f"{key}={value}"])
            log_handle = log_path.open("w", encoding="utf-8")
            process = subprocess.Popen(
                command,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                shell=False,
                env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            ready_deadline = time.monotonic() + 30
            next_control_update = time.monotonic()
            while time.monotonic() < ready_deadline:
                if process.poll() is not None:
                    raise DatabaseBenchmarkError("PostgreSQL exited before becoming ready.")
                if time.monotonic() >= next_control_update:
                    self._service_control_update(
                        task_id,
                        phase="database-initialization",
                        current_job="postgres-readiness",
                        completed_steps=completed_steps,
                        total_steps=total_steps,
                    )
                    next_control_update = time.monotonic() + 2
                ready = subprocess.run(
                    [pg_isready, "-h", "127.0.0.1", "-p", str(port), "-U", "cloudmark", "-d", "postgres"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                    shell=False,
                    env=environment,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                if ready.returncode == 0:
                    break
                time.sleep(0.25)
            else:
                raise DatabaseBenchmarkError("PostgreSQL did not become ready within 30 seconds.")

            prepare_code, prepare_stdout, prepare_stderr = self._guarded_service_process(
                task_id,
                [
                    pgbench,
                    "-i",
                    "-s",
                    str(scale_factor),
                    "-h",
                    "127.0.0.1",
                    "-p",
                    str(port),
                    "-U",
                    "cloudmark",
                    "postgres",
                ],
                environment=environment,
                expected_duration=240,
                phase="database-initialization",
                current_job=f"pgbench-scale-{scale_factor}",
                completed_steps=completed_steps,
                total_steps=total_steps,
            )
            if prepare_code != 0:
                raise DatabaseBenchmarkError(
                    prepare_stderr.strip() or prepare_stdout.strip() or "pgbench initialization failed."
                )
            active = ActiveDatabaseServer(
                process=process,
                deadline=time.monotonic() + deadline_seconds,
                root=root,
                data_dir=data_dir,
                log_path=log_path,
                log_handle=log_handle,
                postgres_version=tool_version(postgres),
                pgbench_version=tool_version(pgbench),
                scale_factor=scale_factor,
                port=port,
                settings=settings,
            )
            self.active_database_servers[task_id] = active
            return {
                "ready": True,
                "engine": "postgresql",
                "port": port,
                "scale_factor": scale_factor,
                "estimated_dataset_bytes": estimated_bytes,
                "durability": settings,
                "methodology_version": methodology_version,
                "tools": {"postgres": active.postgres_version, "pgbench": active.pgbench_version},
            }
        except BaseException:
            if process is not None:
                self._terminate_process(process)
            if log_handle is not None:
                log_handle.close()
            self._remove_database_root(root)
            raise

    def _guarded_service_process(
        self,
        task_id: str,
        command: list[str],
        *,
        environment: dict[str, str],
        expected_duration: int,
        phase: str,
        current_job: str,
        completed_steps: int,
        total_steps: int,
        cpu_samples: list[dict[str, float]] | None = None,
        working_directory: Path | None = None,
    ) -> tuple[int, str, str]:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            env=environment,
            cwd=str(working_directory) if working_directory is not None else None,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            started = time.monotonic()
            last_contact = started
            next_update = started
            previous_cpu = _linux_cpu_snapshot(process.pid) if cpu_samples is not None else None
            next_cpu_sample = started + 1
            while process.poll() is None:
                now = time.monotonic()
                if now - started > expected_duration + 20:
                    raise AgentBenchmarkFailure(
                        "Service task exceeded its guarded timeout.", status="failed", result=None
                    )
                if now >= next_update:
                    elapsed_fraction = min(0.99, max(0.0, (now - started) / max(1, expected_duration)))
                    try:
                        response = self._api(
                            f"tasks/{task_id}/progress",
                            {
                                "progress": min(0.99, (completed_steps + elapsed_fraction) / max(1, total_steps)),
                                "phase": phase,
                                "current_job": current_job,
                                "completed_steps": completed_steps,
                                "total_steps": total_steps,
                            },
                            timeout=5,
                        )
                        last_contact = time.monotonic()
                    except (RuntimeError, urllib.error.URLError, TimeoutError, OSError):
                        if time.monotonic() - last_contact > SERVICE_CONTROLLER_CONTACT_TIMEOUT_SECONDS:
                            raise AgentBenchmarkFailure(
                                "Service task stopped after losing Controller contact.", status="failed", result=None
                            )
                    else:
                        if response.get("cancel_requested") or response.get("task_status") != "running":
                            raise AgentBenchmarkFailure(
                                "Service task cancelled by the Controller.", status="cancelled", result=None
                            )
                    next_update = time.monotonic() + 2
                if cpu_samples is not None and now >= next_cpu_sample:
                    current_cpu = _linux_cpu_snapshot(process.pid)
                    if previous_cpu is not None and current_cpu is not None:
                        interval = _linux_cpu_interval(previous_cpu, current_cpu)
                        if interval is not None and len(cpu_samples) < 120:
                            cpu_samples.append(interval)
                    previous_cpu = current_cpu
                    next_cpu_sample = now + 1
                time.sleep(0.2)
            stdout, stderr = process.communicate()
            if cpu_samples is not None and previous_cpu is not None:
                current_cpu = _linux_cpu_snapshot(process.pid)
                if current_cpu is not None:
                    interval = _linux_cpu_interval(previous_cpu, current_cpu)
                    if interval is not None and len(cpu_samples) < 120:
                        cpu_samples.append(interval)
            return int(process.returncode or 0), stdout, stderr
        except BaseException:
            self._terminate_process(process)
            process.communicate()
            raise

    def _run_database_client(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        address = str(payload.get("target_address", ""))
        self._peer_address(address)
        port = self._database_port(payload.get("port"))
        workload = str(payload.get("workload", ""))
        if workload not in {"select-only", "tpcb-like"}:
            raise DatabaseBenchmarkError("Database workload is outside the CloudMark allow-list.")
        clients = int(payload.get("clients", 0))
        threads = int(payload.get("threads", 0))
        duration = int(payload.get("duration_seconds", 0))
        warmup = int(payload.get("warmup_seconds", 0))
        transactions_per_client = int(payload.get("transactions_per_client", 0))
        methodology_version = str(payload.get("methodology_version") or "database-postgresql-v1")
        if methodology_version not in {"database-postgresql-v1", "database-postgresql-v2"}:
            raise DatabaseBenchmarkError("Database client methodology is outside the installed contract.")
        if clients not in DATABASE_ALLOWED_CLIENTS or threads not in DATABASE_ALLOWED_THREADS or threads > clients:
            raise DatabaseBenchmarkError("Database concurrency is outside the CloudMark allow-list.")
        if not 0 <= warmup <= 10:
            raise DatabaseBenchmarkError("Database duration is outside the CloudMark allow-list.")
        if transactions_per_client:
            if (
                methodology_version != "database-postgresql-v2"
                or duration != 0
                or transactions_per_client != DATABASE_TAIL_TRANSACTIONS_PER_CLIENT
                or clients * transactions_per_client > DATABASE_TAIL_MAX_TOTAL_TRANSACTIONS
            ):
                raise DatabaseBenchmarkError("Database fixed-transaction workload is outside the v2 contract.")
            expected_duration = DATABASE_TAIL_JOB_TIMEOUT_SECONDS
        else:
            if not 1 <= duration <= DATABASE_MAX_DURATION:
                raise DatabaseBenchmarkError("Database duration is outside the CloudMark allow-list.")
            expected_duration = duration
        connect_per_transaction = payload.get("connect_per_transaction") is True
        completed_steps = int(payload.get("run_completed_steps", 0))
        total_steps = int(payload.get("run_total_steps", 1))
        if not 0 <= completed_steps < total_steps <= 64:
            raise DatabaseBenchmarkError("Database progress metadata is invalid.")

        pgbench = self._postgres_tool("pgbench")
        environment = os.environ.copy()
        environment["LC_ALL"] = "C"
        environment["PGCONNECT_TIMEOUT"] = "5"

        def command(seconds: int, progress: bool, *, fixed_transactions: bool = False) -> list[str]:
            value = [
                pgbench,
                "-h",
                address,
                "-p",
                str(port),
                "-U",
                "cloudmark",
                "-c",
                str(clients),
                "-j",
                str(threads),
                "-b",
                workload,
            ]
            if fixed_transactions:
                value.extend(["-t", str(transactions_per_client), "--log"])
            else:
                value.extend(["-T", str(seconds)])
            if progress:
                value.extend(["-P", "1"])
            if connect_per_transaction:
                value.append("-C")
            value.append("postgres")
            return value

        if warmup:
            code, stdout, stderr = self._guarded_service_process(
                task_id,
                command(warmup, False),
                environment=environment,
                expected_duration=warmup,
                phase="database-warmup",
                current_job=workload,
                completed_steps=completed_steps,
                total_steps=total_steps,
            )
            if code != 0:
                raise DatabaseBenchmarkError(stderr.strip() or stdout.strip() or "pgbench warm-up failed.")
        log_root: Path | None = None
        transaction_latency: dict[str, Any] = {
            "status": "not-applicable",
            "source": "pgbench-per-transaction-log",
        }
        client_log_cleanup_verified: bool | None = None
        if transactions_per_client:
            log_root = self._database_client_log_root(task_id)
            if log_root.exists():
                raise DatabaseBenchmarkError("Database client found a residual transaction-log directory.")
            log_root.parent.mkdir(parents=True, exist_ok=True)
            disk = shutil.disk_usage(log_root.parent)
            reserve = max(512 * 1024 * 1024, int(disk.total * 0.05))
            if disk.free < reserve + DATABASE_TAIL_LOG_MAX_BYTES:
                raise DatabaseBenchmarkError("Insufficient free space for bounded transaction logs and reserve.")
            log_root.mkdir(exist_ok=False)
        cpu_samples: list[dict[str, float]] = []
        measured_arguments: dict[str, Any] = {
            "environment": environment,
            "expected_duration": expected_duration,
            "phase": "measuring-database",
            "current_job": workload,
            "completed_steps": completed_steps,
            "total_steps": total_steps,
        }
        if methodology_version == "database-postgresql-v2":
            measured_arguments["cpu_samples"] = cpu_samples
        if log_root is not None:
            measured_arguments["working_directory"] = log_root
        try:
            code, stdout, stderr = self._guarded_service_process(
                task_id,
                command(duration, True, fixed_transactions=bool(transactions_per_client)),
                **measured_arguments,
            )
            if log_root is not None:
                transaction_latency = self._read_pgbench_latency_logs(
                    log_root,
                    expected_transactions=clients * transactions_per_client,
                )
        finally:
            if log_root is not None:
                client_log_cleanup_verified = self._remove_database_client_log_root(log_root)
        if code != 0:
            raise DatabaseBenchmarkError(stderr.strip() or stdout.strip() or "pgbench workload failed.")
        metrics = parse_pgbench_output(stdout, stderr)
        metrics["transaction_latency"] = transaction_latency
        metrics["tail_latency_status"] = transaction_latency["status"]
        return {
            "pgbench": {
                "workload": workload,
                "clients": clients,
                "threads": threads,
                "duration_seconds": duration,
                "warmup_seconds": warmup,
                "connect_per_transaction": connect_per_transaction,
                "transactions_per_client": transactions_per_client,
                "methodology_version": methodology_version,
                "metrics": metrics,
                "generator_cpu": _generator_cpu_evidence(cpu_samples),
                "client_log_cleanup_verified": client_log_cleanup_verified,
                "tool": {"name": "pgbench", "version": tool_version(pgbench)},
                "raw": {"stdout": stdout, "stderr": stderr},
            }
        }

    def _run_database_recovery(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        server_task_id = str(payload.get("server_task_id", ""))
        active = self.active_database_servers.get(server_task_id)
        if not active:
            raise DatabaseBenchmarkError("Database recovery requires the active CloudMark PostgreSQL service.")
        if str(payload.get("methodology_version", "")) != "database-postgresql-recovery-v1":
            raise DatabaseBenchmarkError("Database recovery methodology is outside the installed contract.")
        completed_steps = int(payload.get("run_completed_steps", 0))
        total_steps = int(payload.get("run_total_steps", 1))
        if not 0 <= completed_steps < total_steps <= 64:
            raise DatabaseBenchmarkError("Database recovery progress metadata is invalid.")
        recovery_root = (active.root / "logical-recovery").resolve()
        try:
            recovery_root.relative_to(active.root.resolve())
        except ValueError as exc:
            raise DatabaseBenchmarkError("Database recovery path escaped the active service workspace.") from exc
        if recovery_root.exists():
            raise DatabaseBenchmarkError("Database recovery found a residual recovery directory.")
        estimated_dataset_bytes = active.scale_factor * 20 * 1024 * 1024
        disk = shutil.disk_usage(active.root)
        reserve = max(1024**3, int(disk.total * 0.05))
        if disk.free < reserve + estimated_dataset_bytes * 2:
            raise DatabaseBenchmarkError("Insufficient free space for logical backup, restore, and reserve.")
        recovery_root.mkdir(exist_ok=False)
        backup_path = recovery_root / "cloudmark.dump"
        restore_database = "cloudmark_restore"
        environment = os.environ.copy()
        environment["LC_ALL"] = "C"
        environment["PGCONNECT_TIMEOUT"] = "5"
        pg_dump = self._postgres_tool("pg_dump")
        pg_restore = self._postgres_tool("pg_restore")
        createdb = self._postgres_tool("createdb")
        dropdb = self._postgres_tool("dropdb")
        psql = self._postgres_tool("psql")
        base_connection = ["-h", "127.0.0.1", "-p", str(active.port), "-U", "cloudmark"]
        count_query = (
            "SELECT (SELECT count(*) FROM pgbench_accounts),"
            "(SELECT count(*) FROM pgbench_branches),"
            "(SELECT count(*) FROM pgbench_tellers),"
            "(SELECT count(*) FROM pgbench_history);"
        )
        restored_database_created = False
        dropped_restore = False

        def guarded(command: list[str], *, stage: str, timeout: int) -> tuple[str, str, float]:
            started = time.monotonic()
            code, stdout, stderr = self._guarded_service_process(
                task_id,
                command,
                environment=environment,
                expected_duration=timeout,
                phase="database-recovery",
                current_job=stage,
                completed_steps=completed_steps,
                total_steps=total_steps,
            )
            elapsed = round(time.monotonic() - started, 6)
            if code != 0:
                raise DatabaseBenchmarkError(
                    stderr.strip() or stdout.strip() or f"PostgreSQL recovery stage failed: {stage}."
                )
            return stdout, stderr, elapsed

        try:
            source_stdout, _, verify_source_seconds = guarded(
                [
                    psql,
                    *base_connection,
                    "-d",
                    "postgres",
                    "-At",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-c",
                    count_query,
                ],
                stage="source-row-counts",
                timeout=30,
            )
            source_counts = parse_pgbench_row_counts(source_stdout)
            _, _, backup_seconds = guarded(
                [
                    pg_dump,
                    *base_connection,
                    "-d",
                    "postgres",
                    "-Fc",
                    "-Z",
                    "0",
                    "-f",
                    str(backup_path),
                ],
                stage="logical-backup",
                timeout=300,
            )
            if not backup_path.is_file():
                raise DatabaseBenchmarkError("PostgreSQL logical backup did not create its fixed artifact.")
            backup_bytes = backup_path.stat().st_size
            if backup_bytes <= 0 or backup_bytes > estimated_dataset_bytes * 2:
                raise DatabaseBenchmarkError("PostgreSQL logical backup size is outside the bounded contract.")
            guarded(
                [createdb, *base_connection, "-T", "template0", restore_database],
                stage="create-restore-database",
                timeout=30,
            )
            restored_database_created = True
            _, _, restore_seconds = guarded(
                [
                    pg_restore,
                    *base_connection,
                    "-d",
                    restore_database,
                    "--exit-on-error",
                    "--no-owner",
                    "--no-privileges",
                    str(backup_path),
                ],
                stage="logical-restore",
                timeout=300,
            )
            restored_stdout, _, verify_restore_seconds = guarded(
                [
                    psql,
                    *base_connection,
                    "-d",
                    restore_database,
                    "-At",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-c",
                    count_query,
                ],
                stage="restored-row-counts",
                timeout=30,
            )
            restored_counts = parse_pgbench_row_counts(restored_stdout)
            row_counts_match = source_counts == restored_counts
            expected_shape = {
                "accounts": active.scale_factor * 100_000,
                "branches": active.scale_factor,
                "tellers": active.scale_factor * 10,
            }
            expected_shape_match = all(source_counts[key] == value for key, value in expected_shape.items())
        finally:
            if restored_database_created:
                try:
                    drop_result = subprocess.run(
                        [dropdb, *base_connection, "--if-exists", restore_database],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                        shell=False,
                        env=environment,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                    dropped_restore = drop_result.returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    dropped_restore = False
            recovery_root_removed = self._remove_database_root(recovery_root)
        cleanup_verified = dropped_restore and recovery_root_removed
        return {
            "status": "complete" if row_counts_match and expected_shape_match and cleanup_verified else "partial",
            "type": "logical-backup-restore",
            "backup_format": "pg_dump-custom-uncompressed",
            "backup_duration_seconds": backup_seconds,
            "restore_duration_seconds": restore_seconds,
            "source_verification_seconds": verify_source_seconds,
            "restored_verification_seconds": verify_restore_seconds,
            "backup_bytes": backup_bytes,
            "verification": {
                "source_row_counts": source_counts,
                "restored_row_counts": restored_counts,
                "row_counts_match": row_counts_match,
                "expected_scale_shape_match": expected_shape_match,
            },
            "cleanup_verified": cleanup_verified,
            "restore_database_removed": dropped_restore,
            "backup_artifact_removed": recovery_root_removed,
            "tools": {
                "pg_dump": tool_version(pg_dump),
                "pg_restore": tool_version(pg_restore),
                "psql": tool_version(psql),
            },
        }

    def _stop_database_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        server_task_id = str(payload.get("server_task_id", ""))
        if not server_task_id:
            raise DatabaseBenchmarkError("Database cleanup task is missing its service ID.")
        return self._stop_database_server(server_task_id)

    @staticmethod
    def _redis_password(payload: dict[str, Any]) -> str:
        secret=payload.get("_ephemeral_secret") or {}; password=secret.get("redis_password")
        if not isinstance(password,str) or not 16<=len(password)<=512: raise RedisBenchmarkError("Redis task is missing its ephemeral credential.")
        return password

    def _redis_root(self, task_id:str)->Path:
        if not task_id.startswith("task_") or not task_id.removeprefix("task_").isalnum(): raise RedisBenchmarkError("Redis task ID is invalid.")
        base=(self.workspace/"redis-services").resolve(); root=(base/task_id).resolve()
        try: root.relative_to(base)
        except ValueError as exc: raise RedisBenchmarkError("Redis path escaped the Agent workspace.") from exc
        return root

    def _stop_redis_server(self, server_task_id:str)->dict[str,Any]:
        active=self.active_redis_servers.get(server_task_id)
        if not active:
            root=self._redis_root(server_task_id); return {"status":"already-absent","cleanup_verified":not root.exists()}
        self._terminate_process(active.process); active.log_handle.close(); tail=self._tail_text(active.log_path); shutil.rmtree(active.root); cleaned=not active.root.exists(); self.active_redis_servers.pop(server_task_id,None)
        return {"status":"completed","cleanup_verified":cleaned,"server_returncode":active.process.returncode,"redis_log_tail":tail}

    def _start_redis_server(self, task_id:str,payload:dict[str,Any])->dict[str,Any]:
        if self.active_redis_servers or self.active_database_servers or self.active_web_servers: raise RedisBenchmarkError("Agent already has an active paired service.")
        if os.name=="nt": raise RedisBenchmarkError("Redis assessment currently requires a Linux Agent.")
        address=str(payload.get("listen_address","")); self._peer_address(address); port=int(payload.get("port",0)); deadline=int(payload.get("deadline_seconds",0)); password=self._redis_password(payload)
        if port!=REDIS_PORT or not 120<=deadline<=3600: raise RedisBenchmarkError("Redis service bounds do not match the methodology.")
        root=self._redis_root(task_id)
        if root.exists(): raise RedisBenchmarkError("Redis service found residual state.")
        root.mkdir(parents=True); config=root/"redis.conf"; log=root/"redis.log"
        config.write_text(f"bind {address}\nprotected-mode yes\nport {port}\ndir {root}\ndbfilename cloudmark.rdb\nappendonly yes\nappendfilename cloudmark.aof\nappendfsync everysec\nsave \"\"\nrequirepass {password}\n",encoding="utf-8")
        executable=find_redis_binary("redis-server")
        if not executable: shutil.rmtree(root); raise RedisBenchmarkError("redis-server is unavailable.")
        handle=log.open("w",encoding="utf-8"); process=subprocess.Popen([executable,str(config)],stdout=handle,stderr=subprocess.STDOUT,text=True,shell=False)
        cli=find_redis_binary("redis-cli"); ready=False
        try:
            for _ in range(40):
                if process.poll() is not None: break
                check=subprocess.run([cli,"-h",address,"-p",str(port),"-a",password,"PING"],capture_output=True,text=True,timeout=3,check=False,shell=False) if cli else None
                if check and check.returncode==0 and check.stdout.strip()=="PONG": ready=True; break
                time.sleep(.25)
            if not ready: raise RedisBenchmarkError("Authenticated Redis service did not become ready.")
            active=ActiveRedisServer(process,time.monotonic()+deadline,root,log,handle,tool_version(executable),port); self.active_redis_servers[task_id]=active
            return {"ready":True,"engine":"redis","port":port,"authentication":"memory-only-per-run-secret","persistence":{"appendonly":True,"appendfsync":"everysec"},"tool":{"name":"redis-server","version":active.version}}
        except BaseException:
            self._terminate_process(process); handle.close(); shutil.rmtree(root,ignore_errors=True); raise

    def _run_redis_client(self,task_id:str,payload:dict[str,Any])->dict[str,Any]:
        address=str(payload.get("target_address","")); self._peer_address(address); password=self._redis_password(payload); port=int(payload.get("port",0)); operation=str(payload.get("operation","")).lower(); clients=int(payload.get("clients",0)); pipeline=int(payload.get("pipeline",0)); size=int(payload.get("value_bytes",0)); requests=int(payload.get("requests",0))
        if port!=REDIS_PORT or operation not in {"get","set"} or clients not in {1,16,64} or pipeline not in {1,16} or size not in {64,1024} or not 1<=requests<=50000: raise RedisBenchmarkError("Redis workload is outside the fixed contract.")
        executable=find_redis_binary("redis-benchmark")
        if not executable: raise RedisBenchmarkError("redis-benchmark is unavailable.")
        samples=[]; command=[executable,"-h",address,"-p",str(port),"-a",password,"-n",str(requests),"-c",str(clients),"-P",str(pipeline),"-d",str(size),"-t",operation,"--csv"]
        code,stdout,stderr=self._guarded_service_process(task_id,command,environment={**os.environ,"LC_ALL":"C"},expected_duration=60,phase="measuring-redis",current_job=operation,completed_steps=int(payload.get("run_completed_steps",0)),total_steps=int(payload.get("run_total_steps",1)),cpu_samples=samples)
        if code!=0: raise RedisBenchmarkError(stderr.strip()[:256] or "redis-benchmark failed.")
        return {"redis_benchmark":{"operation":operation,"clients":clients,"pipeline":pipeline,"value_bytes":size,"requests":requests,"metrics":parse_redis_benchmark_csv(stdout),"generator_cpu":_generator_cpu_evidence(samples),"tool":{"name":"redis-benchmark","version":tool_version(executable)}}}

    @staticmethod
    def _web_tool(name: str) -> str:
        executable = find_web_binary(name)
        if not executable:
            raise WebBenchmarkError(
                f"{name} is not installed or discoverable. Run CloudMark bootstrap with the web pack."
            )
        return executable

    @staticmethod
    def _web_port(value: Any) -> int:
        port = int(value)
        if port not in WEB_ALLOWED_PORTS:
            raise WebBenchmarkError("Web task port is outside the CloudMark allow-list.")
        return port

    def _web_root(self, task_id: str) -> Path:
        if not task_id.startswith("task_") or not task_id.removeprefix("task_").isalnum():
            raise WebBenchmarkError("Web service task ID is invalid.")
        base = (self.workspace / "web-services").resolve()
        root = (base / task_id).resolve()
        try:
            root.relative_to(base)
        except ValueError as exc:
            raise WebBenchmarkError("Web service path escaped the Agent workspace.") from exc
        return root

    @staticmethod
    def _nginx_path(path: Path) -> str:
        return str(path.resolve()).replace("\\", "/").replace('"', '\\"')

    def _remove_web_root(self, root: Path) -> bool:
        base = (self.workspace / "web-services").resolve()
        resolved = root.resolve()
        try:
            relative = resolved.relative_to(base)
        except ValueError as exc:
            raise WebBenchmarkError("Agent refused cleanup outside its web service workspace.") from exc
        if not relative.parts:
            raise WebBenchmarkError("Agent refused cleanup of the web service root.")
        if resolved.exists():
            shutil.rmtree(resolved)
        return not resolved.exists()

    def _stop_web_server(self, server_task_id: str) -> dict[str, Any]:
        active = self.active_web_servers.get(server_task_id)
        if not active:
            root = self._web_root(server_task_id)
            return {
                "status": "already-absent",
                "cleanup_verified": not root.exists(),
                "server_task_id": server_task_id,
            }
        graceful_returncode: int | None = None
        if active.process.poll() is None:
            try:
                graceful = subprocess.run(
                    [
                        active.nginx,
                        "-p",
                        f"{self._nginx_path(active.root)}/",
                        "-c",
                        str(active.config_path),
                        "-s",
                        "quit",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                    shell=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                graceful_returncode = graceful.returncode
            except (OSError, subprocess.TimeoutExpired):
                graceful_returncode = -1
            try:
                active.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._terminate_process(active.process, timeout=5)
        if active.application_process is not None and active.application_process.poll() is None:
            self._terminate_process(active.application_process, timeout=5)
        active.log_handle.close()
        if active.application_log_handle is not None:
            active.application_log_handle.close()
        log_tail = self._tail_text(active.log_path)
        application_log_tail = (
            self._tail_text(active.application_log_path)
            if active.application_log_path is not None
            else None
        )
        cleaned = self._remove_web_root(active.root)
        self.active_web_servers.pop(server_task_id, None)
        return {
            "status": "completed",
            "cleanup_verified": cleaned,
            "server_task_id": server_task_id,
            "server_returncode": active.process.returncode,
            "graceful_stop_returncode": graceful_returncode,
            "nginx_log_tail": log_tail,
            "application_returncode": (
                active.application_process.returncode if active.application_process is not None else None
            ),
            "application_log_tail": application_log_tail,
        }

    def _start_web_server(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.active_web_servers or self.active_database_servers:
            raise WebBenchmarkError("Agent already has an active paired service.")
        service_root = self.workspace / "web-services"
        if service_root.is_dir() and any(item.is_dir() for item in service_root.iterdir()):
            raise WebBenchmarkError(
                "Agent found a residual web service directory. Verify that no CloudMark Nginx process is "
                "running, then follow the recovery runbook before retrying."
            )
        if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
            raise WebBenchmarkError("Web assessment must run from a non-root Agent account.")

        listen_address = str(payload.get("listen_address", ""))
        allowed_client = str(payload.get("allowed_client_address", ""))
        parsed_listen = self._peer_address(listen_address)
        self._peer_address(allowed_client)
        http_port = self._web_port(payload.get("http_port"))
        https_port = self._web_port(payload.get("https_port"))
        methodology_version = str(payload.get("methodology_version") or "web-http-v1")
        if methodology_version not in {"web-http-v1", "web-http-v2"}:
            raise WebBenchmarkError("Web service methodology is outside the installed contract.")
        if http_port != WEB_HTTP_PORT or https_port != WEB_HTTPS_PORT:
            raise WebBenchmarkError("Web service ports do not match the methodology contract.")
        deadline_seconds = int(payload.get("deadline_seconds", 0))
        completed_steps = int(payload.get("run_completed_steps", -1))
        total_steps = int(payload.get("run_total_steps", 0))
        if not 120 <= deadline_seconds <= 3_600:
            raise WebBenchmarkError("Web service deadline is outside the CloudMark allow-list.")
        if completed_steps != 0 or not 2 <= total_steps <= 64:
            raise WebBenchmarkError("Web setup progress metadata is invalid.")

        root = self._web_root(task_id)
        www = root / "www"
        assets = www / "assets"
        config_path = root / "nginx.conf"
        certificate_config = root / "openssl.cnf"
        certificate_path = root / "certificate.pem"
        key_path = root / "certificate.key"
        log_path = root / "nginx-process.log"
        application_log_path = root / "application-process.log"
        error_log_path = root / "nginx-error.log"
        pid_path = root / "nginx.pid"
        disk = shutil.disk_usage(self.workspace)
        reserve = max(512 * 1024 * 1024, int(disk.total * 0.05))
        if disk.free < reserve + 2 * 1024 * 1024:
            raise WebBenchmarkError("Insufficient free space for the ephemeral web service and safety reserve.")
        assets.mkdir(parents=True, exist_ok=False)

        nginx = self._web_tool("nginx")
        openssl = self._web_tool("openssl")
        environment = os.environ.copy()
        environment["LC_ALL"] = "C"
        log_handle: Any | None = None
        application_log_handle: Any | None = None
        process: subprocess.Popen[str] | None = None
        application_process: subprocess.Popen[str] | None = None
        try:
            health = b"ok\n"
            api_prefix = b'{"status":"ok","service":"cloudmark","payload":"'
            api_suffix = b'"}\n'
            api_payload = api_prefix + (b"x" * (1024 - len(api_prefix) - len(api_suffix))) + api_suffix
            if len(api_payload) != 1024:
                raise WebBenchmarkError("CloudMark failed to construct the fixed 1 KiB API payload.")
            (www / "health.txt").write_bytes(health)
            (www / "api.json").write_bytes(api_payload)
            (assets / "256k.bin").write_bytes(bytes(range(256)) * 1024)

            certificate_config.write_text(
                "[req]\n"
                "prompt = no\n"
                "distinguished_name = dn\n"
                "x509_extensions = v3_req\n"
                "[dn]\n"
                "CN = cloudmark.invalid\n"
                "[v3_req]\n"
                f"subjectAltName = IP:{listen_address}\n"
                "keyUsage = critical,digitalSignature,keyEncipherment\n"
                "extendedKeyUsage = serverAuth\n",
                encoding="utf-8",
            )
            certificate_code, certificate_stdout, certificate_stderr = self._guarded_service_process(
                task_id,
                [
                    openssl,
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-sha256",
                    "-days",
                    "1",
                    "-nodes",
                    "-keyout",
                    str(key_path),
                    "-out",
                    str(certificate_path),
                    "-config",
                    str(certificate_config),
                    "-extensions",
                    "v3_req",
                ],
                environment=environment,
                expected_duration=30,
                phase="web-service-initialization",
                current_job="ephemeral-certificate",
                completed_steps=completed_steps,
                total_steps=total_steps,
            )
            if certificate_code != 0:
                raise WebBenchmarkError(
                    certificate_stderr.strip()
                    or certificate_stdout.strip()
                    or "OpenSSL certificate generation failed."
                )

            listen_host = f"[{listen_address}]" if parsed_listen.version == 6 else listen_address
            v2_enabled = methodology_version == "web-http-v2"
            upstream_config = (
                "  upstream cloudmark_dynamic_app {\n"
                f"    server {WEB_FIXTURE_BIND}:{WEB_APP_PORT};\n"
                "    keepalive 64;\n"
                "  }\n"
                if v2_enabled
                else ""
            )
            dynamic_location = (
                f"    location = {WEB_FIXTURE_DYNAMIC_PATH} {{\n"
                "      proxy_http_version 1.1;\n"
                "      proxy_set_header Connection \"\";\n"
                "      proxy_set_header Host cloudmark.invalid;\n"
                "      proxy_pass http://cloudmark_dynamic_app;\n"
                "    }\n"
                if v2_enabled
                else ""
            )
            tls_listener = "ssl http2" if v2_enabled else "ssl"
            config_path.write_text(
                "worker_processes auto;\n"
                f'pid "{self._nginx_path(pid_path)}";\n'
                f'error_log "{self._nginx_path(error_log_path)}" notice;\n'
                "events { worker_connections 4096; }\n"
                "http {\n"
                f"{upstream_config}"
                "  access_log off;\n"
                "  default_type application/octet-stream;\n"
                "  sendfile on;\n"
                "  tcp_nopush on;\n"
                "  gzip off;\n"
                "  keepalive_timeout 15s;\n"
                "  keepalive_requests 10000;\n"
                "  server_tokens on;\n"
                "  server {\n"
                f"    listen {listen_host}:{http_port};\n"
                f"    listen {listen_host}:{https_port} {tls_listener};\n"
                "    server_name cloudmark.invalid;\n"
                f"    allow {allowed_client};\n"
                f"    allow {listen_address};\n"
                "    deny all;\n"
                "    ssl_protocols TLSv1.2;\n"
                "    ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256;\n"
                "    ssl_prefer_server_ciphers on;\n"
                "    ssl_session_cache off;\n"
                "    ssl_session_tickets off;\n"
                f'    ssl_certificate "{self._nginx_path(certificate_path)}";\n'
                f'    ssl_certificate_key "{self._nginx_path(key_path)}";\n'
                f'    add_header X-CloudMark-Methodology "{methodology_version}";\n'
                "    location = /health {\n"
                "      default_type text/plain;\n"
                f'      alias "{self._nginx_path(www / "health.txt")}";\n'
                "    }\n"
                "    location = /api/v1/record {\n"
                "      default_type application/json;\n"
                "      add_header Cache-Control no-store;\n"
                f'      alias "{self._nginx_path(www / "api.json")}";\n'
                "    }\n"
                "    location = /assets/256k.bin {\n"
                f'      alias "{self._nginx_path(assets / "256k.bin")}";\n'
                "    }\n"
                f"{dynamic_location}"
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            prefix = f"{self._nginx_path(root)}/"
            test_code, test_stdout, test_stderr = self._guarded_service_process(
                task_id,
                [nginx, "-t", "-p", prefix, "-c", str(config_path)],
                environment=environment,
                expected_duration=10,
                phase="web-service-initialization",
                current_job="nginx-config-test",
                completed_steps=completed_steps,
                total_steps=total_steps,
            )
            if test_code != 0:
                raise WebBenchmarkError(test_stderr.strip() or test_stdout.strip() or "Nginx config test failed.")

            if v2_enabled:
                application_log_handle = application_log_path.open("w", encoding="utf-8")
                application_process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "cloudmark.web_fixture",
                        "--bind",
                        WEB_FIXTURE_BIND,
                        "--port",
                        str(WEB_APP_PORT),
                    ],
                    stdout=application_log_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    shell=False,
                    env=environment,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                application_deadline = time.monotonic() + 20
                while time.monotonic() < application_deadline:
                    if application_process.poll() is not None:
                        raise WebBenchmarkError("The bundled dynamic application exited before becoming ready.")
                    try:
                        with socket.create_connection((WEB_FIXTURE_BIND, WEB_APP_PORT), timeout=1):
                            break
                    except OSError:
                        self._service_control_update(
                            task_id,
                            phase="web-service-initialization",
                            current_job="dynamic-application-readiness",
                            completed_steps=completed_steps,
                            total_steps=total_steps,
                        )
                        time.sleep(0.25)
                else:
                    raise WebBenchmarkError("The bundled dynamic application did not become ready within 20 seconds.")

            log_handle = log_path.open("w", encoding="utf-8")
            process = subprocess.Popen(
                [nginx, "-p", prefix, "-c", str(config_path), "-g", "daemon off;"],
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                shell=False,
                env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            ready_deadline = time.monotonic() + 20
            next_control_update = time.monotonic()
            pending_ports = {http_port, https_port}
            while time.monotonic() < ready_deadline and pending_ports:
                if process.poll() is not None:
                    raise WebBenchmarkError("Nginx exited before becoming ready.")
                if time.monotonic() >= next_control_update:
                    self._service_control_update(
                        task_id,
                        phase="web-service-initialization",
                        current_job="nginx-readiness",
                        completed_steps=completed_steps,
                        total_steps=total_steps,
                    )
                    next_control_update = time.monotonic() + 2
                for port in list(pending_ports):
                    try:
                        with socket.create_connection((listen_address, port), timeout=1):
                            pending_ports.remove(port)
                    except OSError:
                        pass
                if pending_ports:
                    time.sleep(0.25)
            if pending_ports:
                raise WebBenchmarkError("Nginx HTTP/TLS listeners did not become ready within 20 seconds.")

            active = ActiveWebServer(
                process=process,
                deadline=time.monotonic() + deadline_seconds,
                root=root,
                config_path=config_path,
                log_path=log_path,
                log_handle=log_handle,
                nginx=nginx,
                nginx_version=web_tool_version("nginx", nginx),
                openssl_version=web_tool_version("openssl", openssl),
                http_port=http_port,
                https_port=https_port,
                application_process=application_process,
                application_log_path=application_log_path if v2_enabled else None,
                application_log_handle=application_log_handle,
                methodology_version=methodology_version,
            )
            self.active_web_servers[task_id] = active
            return {
                "ready": True,
                "engine": "nginx",
                "ports": {"http": http_port, "https": https_port},
                "tls": {
                    "protocol": "TLSv1.2",
                    "cipher": "ECDHE-RSA-AES128-GCM-SHA256",
                    "certificate": "ephemeral-self-signed",
                },
                "payloads": {"health_bytes": len(health), "api_bytes": len(api_payload), "asset_bytes": 262144},
                "application": {
                    "status": "observed" if v2_enabled else "not-applicable",
                    "runtime": "python-standard-library" if v2_enabled else None,
                    "listen_scope": "loopback-only" if v2_enabled else None,
                    "port": WEB_APP_PORT if v2_enabled else None,
                    "path": WEB_FIXTURE_DYNAMIC_PATH if v2_enabled else None,
                    "response_bytes": 1024 if v2_enabled else None,
                    "reverse_proxy": v2_enabled,
                },
                "access_policy": {"paired_generator_only": True, "allowed_client_address": allowed_client},
                "tools": {
                    "nginx": active.nginx_version,
                    "openssl": active.openssl_version,
                    "python": platform.python_version() if v2_enabled else None,
                },
            }
        except BaseException:
            if process is not None:
                self._terminate_process(process)
            if application_process is not None:
                self._terminate_process(application_process)
            if log_handle is not None:
                log_handle.close()
            if application_log_handle is not None:
                application_log_handle.close()
            self._remove_web_root(root)
            raise

    def _run_web_client(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        address = str(payload.get("target_address", ""))
        parsed_address = self._peer_address(address)
        scheme = str(payload.get("scheme", ""))
        if scheme not in WEB_ALLOWED_SCHEMES:
            raise WebBenchmarkError("Web scheme is outside the CloudMark allow-list.")
        port = self._web_port(payload.get("port"))
        expected_port = WEB_HTTPS_PORT if scheme == "https" else WEB_HTTP_PORT
        if port != expected_port:
            raise WebBenchmarkError("Web port does not match the requested scheme.")
        path = str(payload.get("path", ""))
        if path not in WEB_ALLOWED_PATHS:
            raise WebBenchmarkError("Web path is outside the CloudMark allow-list.")
        methodology_version = str(payload.get("methodology_version") or "web-http-v1")
        if methodology_version not in {"web-http-v1", "web-http-v2"}:
            raise WebBenchmarkError("Web client methodology is outside the installed contract.")
        if path == WEB_FIXTURE_DYNAMIC_PATH and methodology_version != "web-http-v2":
            raise WebBenchmarkError("The dynamic application path requires the Web v2 methodology.")
        concurrency = int(payload.get("concurrency", 0))
        duration = int(payload.get("duration_seconds", 0))
        warmup = int(payload.get("warmup_seconds", 0))
        if concurrency not in WEB_ALLOWED_CONCURRENCY:
            raise WebBenchmarkError("Web concurrency is outside the CloudMark allow-list.")
        if not 1 <= duration <= WEB_MAX_DURATION or not 0 <= warmup <= 10:
            raise WebBenchmarkError("Web duration is outside the CloudMark allow-list.")
        keep_alive = payload.get("keep_alive") is True
        completed_steps = int(payload.get("run_completed_steps", 0))
        total_steps = int(payload.get("run_total_steps", 1))
        if not 0 <= completed_steps < total_steps <= 64:
            raise WebBenchmarkError("Web progress metadata is invalid.")

        ab = self._web_tool("ab")
        environment = os.environ.copy()
        environment["LC_ALL"] = "C"
        host = f"[{address}]" if parsed_address.version == 6 else address
        url = f"{scheme}://{host}:{port}{path}"

        def command(seconds: int) -> list[str]:
            value = [
                ab,
                "-n",
                str(WEB_REQUEST_LIMIT),
                "-c",
                str(concurrency),
                "-t",
                str(seconds),
                "-s",
                "5",
                "-q",
                "-r",
            ]
            if keep_alive:
                value.append("-k")
            if scheme == "https":
                value.extend(["-f", "TLS1.2"])
            value.append(url)
            return value

        if warmup:
            code, stdout, stderr = self._guarded_service_process(
                task_id,
                command(warmup),
                environment=environment,
                expected_duration=warmup,
                phase="web-warmup",
                current_job=path,
                completed_steps=completed_steps,
                total_steps=total_steps,
            )
            if code != 0:
                raise WebBenchmarkError(stderr.strip() or stdout.strip() or "ApacheBench warm-up failed.")
        cpu_samples: list[dict[str, float]] = []
        measured_arguments = {
            "environment": environment,
            "expected_duration": duration,
            "phase": "measuring-web",
            "current_job": path,
            "completed_steps": completed_steps,
            "total_steps": total_steps,
        }
        if methodology_version == "web-http-v2":
            measured_arguments["cpu_samples"] = cpu_samples
        code, stdout, stderr = self._guarded_service_process(
            task_id,
            command(duration),
            **measured_arguments,
        )
        if code != 0:
            raise WebBenchmarkError(stderr.strip() or stdout.strip() or "ApacheBench workload failed.")
        return {
            "apachebench": {
                "scheme": scheme,
                "path": path,
                "concurrency": concurrency,
                "duration_seconds": duration,
                "warmup_seconds": warmup,
                "keep_alive": keep_alive,
                "methodology_version": methodology_version,
                "metrics": parse_ab_output(stdout, stderr),
                "generator_cpu": _generator_cpu_evidence(cpu_samples),
                "tool": {"name": "ab", "version": web_tool_version("ab", ab)},
                "raw": {"stdout": stdout, "stderr": stderr},
            }
        }

    def _run_web_protocol_probe(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        address = str(payload.get("target_address", ""))
        parsed_address = self._peer_address(address)
        if str(payload.get("methodology_version", "")) != "web-http-v2":
            raise WebBenchmarkError("HTTP/2 protocol evidence requires the Web v2 methodology.")
        if str(payload.get("scheme", "")) != "https":
            raise WebBenchmarkError("The HTTP/2 protocol probe permits only HTTPS.")
        port = self._web_port(payload.get("port"))
        if port != WEB_HTTPS_PORT:
            raise WebBenchmarkError("The HTTP/2 protocol probe port is outside its fixed contract.")
        path = str(payload.get("path", ""))
        if path != WEB_FIXTURE_DYNAMIC_PATH:
            raise WebBenchmarkError("The HTTP/2 protocol probe path is outside its fixed contract.")
        completed_steps = int(payload.get("run_completed_steps", 0))
        total_steps = int(payload.get("run_total_steps", 1))
        if not 0 <= completed_steps < total_steps <= 64:
            raise WebBenchmarkError("Web protocol progress metadata is invalid.")
        curl = self._web_tool("curl")
        host = f"[{address}]" if parsed_address.version == 6 else address
        url = f"https://{host}:{port}{path}"
        write_out = "%{http_version}\t%{response_code}\t%{time_connect}\t%{time_appconnect}\t%{time_starttransfer}\t%{time_total}\n"
        command = [
            curl,
            "--http2",
            "--insecure",
            "--silent",
            "--show-error",
            "--max-time",
            "10",
            "--tlsv1.2",
            "--tls-max",
            "1.2",
            "--output",
            os.devnull,
            "--write-out",
            write_out,
            url,
        ]
        environment = os.environ.copy()
        environment["LC_ALL"] = "C"
        code, stdout, stderr = self._guarded_service_process(
            task_id,
            command,
            environment=environment,
            expected_duration=10,
            phase="observing-web-protocol",
            current_job="https-http2-negotiation",
            completed_steps=completed_steps,
            total_steps=total_steps,
        )
        if code != 0:
            raise WebBenchmarkError(stderr.strip() or stdout.strip() or "The fixed HTTP/2 protocol probe failed.")
        return {
            "protocol": {
                **parse_curl_protocol_output(stdout),
                "scheme": "https",
                "path": path,
                "tool": {"name": "curl", "version": web_tool_version("curl", curl)},
                "raw": {"stdout": stdout, "stderr": stderr},
            }
        }

    def _stop_web_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        server_task_id = str(payload.get("server_task_id", ""))
        if not server_task_id:
            raise WebBenchmarkError("Web cleanup task is missing its service ID.")
        return self._stop_web_server(server_task_id)

    def _benchmark_evidence(self) -> dict[str, Any]:
        return {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "inventory": collect_inventory(self.workspace),
            "provider": detect_provider(),
        }

    def _run_benchmark(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        kind = str(task.get("kind", ""))
        suite = kind.removeprefix("benchmark-")
        expected_kind = f"benchmark-{suite}"
        if kind != expected_kind or suite not in {"compute", "memory", "storage"}:
            raise ValueError("Agent refused an unsupported benchmark task kind.")
        if payload.get("protocol_version") != REMOTE_METHODOLOGY_VERSION:
            raise ValueError("Agent refused an incompatible remote benchmark protocol version.")
        if payload.get("suite") != suite or payload.get("load_confirmed") is not True:
            raise ValueError("Agent refused a benchmark task without an exact suite and load confirmation.")
        profile_name = str(payload.get("profile", ""))
        profiles = {"compute": COMPUTE_PROFILES, "memory": MEMORY_PROFILES, "storage": STORAGE_PROFILES}[suite]
        if profile_name not in profiles:
            raise ValueError(f"Agent refused unknown {suite} profile: {profile_name}")
        try:
            timeout_seconds = int(payload.get("timeout_seconds", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("Agent benchmark timeout is invalid.") from exc
        if not 30 <= timeout_seconds <= 43_200:
            raise ValueError("Agent benchmark timeout is outside the 30–43200 second safety range.")

        total_steps = len(profiles[profile_name]["jobs"]) + (2 if suite == "storage" else 0)
        token = CancellationToken()
        latest: dict[str, Any] = {
            "progress": 0.0,
            "phase": "preflight",
            "current_job": None,
            "completed_steps": 0,
            "total_steps": total_steps,
            "result": None,
        }
        latest_lock = threading.Lock()
        stopped = threading.Event()
        contact_lock = threading.Lock()
        last_contact = [time.monotonic()]
        task_id = str(task["id"])
        evidence: dict[str, Any] = {}

        def send_progress() -> None:
            with latest_lock:
                update = dict(latest)
            try:
                response = self._api(f"tasks/{task_id}/progress", update, timeout=5)
            except (RuntimeError, urllib.error.URLError, TimeoutError, OSError):
                with contact_lock:
                    disconnected_for = time.monotonic() - last_contact[0]
                if disconnected_for > 20:
                    token.cancel()
                return
            with contact_lock:
                last_contact[0] = time.monotonic()
            if response.get("cancel_requested") or response.get("task_status") != "running":
                token.cancel()

        def monitor_control() -> None:
            while not stopped.wait(1.0):
                send_progress()

        def report(update: dict[str, Any]) -> None:
            with latest_lock:
                latest.clear()
                latest.update(update)
            send_progress()

        monitor = threading.Thread(target=monitor_control, daemon=True, name=f"cloudmark-control-{task_id}")
        monitor.start()
        context = JobContext(
            str(task.get("run_id") or task_id),
            total_steps=total_steps,
            timeout_seconds=timeout_seconds,
            token=token,
            on_progress=report,
        )
        try:
            evidence = self._benchmark_evidence()
            if suite == "storage":
                benchmark = run_storage(profile_name, self.workspace, str(task.get("run_id") or task_id), context=context)
            else:
                benchmark = run_system_benchmark(
                    suite,
                    profile_name,
                    self.workspace,
                    str(task.get("run_id") or task_id),
                    context=context,
                )
            return {
                "benchmark": benchmark,
                "evidence": evidence,
                "protocol_version": REMOTE_METHODOLOGY_VERSION,
                "agent_version": __version__,
            }
        except Exception as exc:
            with latest_lock:
                partial = latest.get("result")
            result = (
                {
                    "benchmark": partial,
                    "evidence": evidence,
                    "protocol_version": REMOTE_METHODOLOGY_VERSION,
                    "agent_version": __version__,
                }
                if isinstance(partial, dict)
                else None
            )
            status = "cancelled" if isinstance(exc, RunCancelled) else "failed"
            if isinstance(exc, RunTimedOut):
                status = "failed"
            raise AgentBenchmarkFailure(str(exc), status=status, result=result) from exc
        finally:
            stopped.set()
            # Progress requests use a five-second transport timeout. Give the
            # monitor enough time to leave cleanly before posting the terminal
            # task result, avoiding a late heartbeat after completion.
            monitor.join(timeout=6)

    def _execute(self, task: dict[str, Any]) -> dict[str, Any]:
        kind = str(task.get("kind", ""))
        payload = dict(task.get("payload") or {})
        if task.get("ephemeral_secret") is not None:
            payload["_ephemeral_secret"] = task["ephemeral_secret"]
        if kind == "network-server-start":
            return self._start_server(str(task["id"]), payload)
        if kind == "network-client":
            return self._run_client(payload)
        if kind == "network-latency":
            return self._run_latency(payload)
        if kind == "network-path-probe":
            return self._run_path_probe(payload)
        if kind == "network-server-collect":
            return self._collect_server(payload)
        if kind == "database-server-start":
            return self._start_database_server(str(task["id"]), payload)
        if kind == "database-client":
            return self._run_database_client(str(task["id"]), payload)
        if kind == "database-recovery-drill":
            return self._run_database_recovery(str(task["id"]), payload)
        if kind == "database-server-stop":
            return self._stop_database_task(payload)
        if kind == "redis-service-start": return self._start_redis_server(str(task["id"]),payload)
        if kind == "redis-client": return self._run_redis_client(str(task["id"]),payload)
        if kind == "redis-service-stop": return self._stop_redis_server(str(payload.get("server_task_id","")))
        if kind == "web-service-start":
            return self._start_web_server(str(task["id"]), payload)
        if kind == "web-client":
            return self._run_web_client(str(task["id"]), payload)
        if kind == "web-protocol-probe":
            return self._run_web_protocol_probe(str(task["id"]), payload)
        if kind == "web-service-stop":
            return self._stop_web_task(payload)
        if kind in {"benchmark-compute", "benchmark-memory", "benchmark-storage"}:
            return self._run_benchmark(task, payload)
        raise NetworkError(f"Agent refused unsupported task kind: {kind}")

    def _cleanup_expired(self) -> None:
        now = time.monotonic()
        for task_id, active in list(self.active_servers.items()):
            if now < active.deadline:
                continue
            if active.process.poll() is None:
                active.process.kill()
            active.process.communicate()
            self.active_servers.pop(task_id, None)
        controller_contact_expired = (
            now - self.last_controller_contact > SERVICE_CONTROLLER_CONTACT_TIMEOUT_SECONDS
        )
        for task_id, active in list(self.active_database_servers.items()):
            if now < active.deadline and not controller_contact_expired:
                continue
            self._stop_database_server(task_id)
        for task_id, active in list(self.active_web_servers.items()):
            if now < active.deadline and not controller_contact_expired:
                continue
            self._stop_web_server(task_id)
        for task_id, active in list(self.active_redis_servers.items()):
            if now < active.deadline and not controller_contact_expired: continue
            self._stop_redis_server(task_id)

    def once(self) -> bool:
        self._cleanup_expired()
        if self.pending_completions:
            task_id = next(iter(self.pending_completions))
            self._api(f"tasks/{task_id}/result", self.pending_completions[task_id])
            self.pending_completions.pop(task_id, None)
            return True
        self._api("heartbeat", {})
        response = self._api("tasks/next", {})
        task = response.get("task")
        if not task:
            return False
        try:
            result = self._execute(task)
            completion = {"status": "completed", "result": result}
        except AgentBenchmarkFailure as exc:
            completion = {"status": exc.status, "error": str(exc)}
            if exc.result is not None:
                completion["result"] = exc.result
        except (DatabaseBenchmarkError, NetworkError, RedisBenchmarkError, WebBenchmarkError, OSError, ValueError) as exc:
            completion = {"status": "failed", "error": str(exc)}
        task_id = str(task["id"])
        self.pending_completions[task_id] = completion
        self._api(f"tasks/{task_id}/result", completion)
        self.pending_completions.pop(task_id, None)
        return True

    def run_forever(self) -> None:
        fingerprint = hashlib.sha256(self.agent_token.encode()).hexdigest()[:10]
        print(f"CloudMark agent {self.agent_id} connected (credential {fingerprint}).")
        print("The agent accepts only versioned CloudMark benchmark tasks; arbitrary shell commands are refused.")
        try:
            while True:
                try:
                    handled = self.once()
                except (RuntimeError, urllib.error.URLError, TimeoutError) as exc:
                    print(f"[agent] controller unavailable: {exc}")
                    handled = False
                if not handled:
                    time.sleep(self.poll_seconds)
        except KeyboardInterrupt:
            pass
        finally:
            for active in self.active_servers.values():
                if active.process.poll() is None:
                    active.process.kill()
                active.process.communicate()
            self.active_servers.clear()
            for task_id in list(self.active_database_servers):
                self._stop_database_server(task_id)
            for task_id in list(self.active_web_servers):
                self._stop_web_server(task_id)
            for task_id in list(self.active_redis_servers):
                self._stop_redis_server(task_id)


def join_and_work(
    controller: str,
    session_id: str,
    join_token: str,
    role: str,
    name: str | None = None,
    *,
    advertise_address: str | None = None,
    allow_http: bool = False,
    workspace: Path = Path(".cloudmark/agent-workspace"),
) -> None:
    joined = join_session(
        controller,
        session_id,
        join_token,
        role,
        name,
        advertise_address=advertise_address,
        allow_http=allow_http,
    )
    AgentWorker(
        controller,
        str(joined["agent_id"]),
        str(joined["agent_token"]),
        allow_http=allow_http,
        workspace=workspace,
    ).run_forever()
