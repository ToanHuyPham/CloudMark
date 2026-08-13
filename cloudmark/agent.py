from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
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
    DatabaseBenchmarkError,
    parse_pgbench_output,
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
from .tooling import find_postgres_binary, find_web_binary, tool_version, web_tool_version
from .web_benchmark import (
    WEB_ALLOWED_CONCURRENCY,
    WEB_ALLOWED_PATHS,
    WEB_ALLOWED_PORTS,
    WEB_ALLOWED_SCHEMES,
    WEB_HTTP_PORT,
    WEB_HTTPS_PORT,
    WEB_MAX_DURATION,
    WEB_REQUEST_LIMIT,
    WebBenchmarkError,
    parse_ab_output,
)


SERVICE_CONTROLLER_CONTACT_TIMEOUT_SECONDS = 20
PATH_PROBE_MAX_HOPS = 8
NETWORK_INTERFACE_PATTERN = re.compile(r"^[A-Za-z0-9_.:@-]{1,64}$")
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
        policy = {
            "passive_route_lookup": True,
            "path_probe_max_hops": PATH_PROBE_MAX_HOPS,
            "arbitrary_arguments_allowed": False,
            "read_only_nic_evidence": True,
            "network_configuration_changed": False,
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
            except subprocess.TimeoutExpired:
                pass
        driver_evidence: dict[str, Any] = {
            "status": "unavailable",
            "reason": "ethtool is not installed or the egress interface is outside the fixed interface-name policy.",
        }
        offload_evidence: dict[str, Any] = {
            "status": "unavailable",
            "features": {},
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
        interface_mtu = (link or {}).get("mtu")
        status = "complete" if isinstance(interface_mtu, int) else "partial"
        return {
            "status": status,
            "address_family": f"ipv{parsed.version}",
            "route": {
                "destination": route.get("dst"),
                "gateway": route.get("gateway"),
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
            },
            "tcp": {"congestion_control": _tcp_congestion_control()},
            "path_mtu": path_mtu,
            "tool": {
                "route": "iproute2",
                "path_mtu": "tracepath" if trace_tool else None,
                "nic": "ethtool" if ethtool else None,
            },
            "policy": policy,
        }

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
    ) -> tuple[int, str, str]:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            started = time.monotonic()
            last_contact = started
            next_update = started
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
                time.sleep(0.2)
            stdout, stderr = process.communicate()
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
        if clients not in DATABASE_ALLOWED_CLIENTS or threads not in DATABASE_ALLOWED_THREADS or threads > clients:
            raise DatabaseBenchmarkError("Database concurrency is outside the CloudMark allow-list.")
        if not 1 <= duration <= DATABASE_MAX_DURATION or not 0 <= warmup <= 10:
            raise DatabaseBenchmarkError("Database duration is outside the CloudMark allow-list.")
        connect_per_transaction = payload.get("connect_per_transaction") is True
        completed_steps = int(payload.get("run_completed_steps", 0))
        total_steps = int(payload.get("run_total_steps", 1))
        if not 0 <= completed_steps < total_steps <= 64:
            raise DatabaseBenchmarkError("Database progress metadata is invalid.")

        pgbench = self._postgres_tool("pgbench")
        environment = os.environ.copy()
        environment["LC_ALL"] = "C"
        environment["PGCONNECT_TIMEOUT"] = "5"

        def command(seconds: int, progress: bool) -> list[str]:
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
                "-T",
                str(seconds),
                "-b",
                workload,
            ]
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
        code, stdout, stderr = self._guarded_service_process(
            task_id,
            command(duration, True),
            environment=environment,
            expected_duration=duration,
            phase="measuring-database",
            current_job=workload,
            completed_steps=completed_steps,
            total_steps=total_steps,
        )
        if code != 0:
            raise DatabaseBenchmarkError(stderr.strip() or stdout.strip() or "pgbench workload failed.")
        return {
            "pgbench": {
                "workload": workload,
                "clients": clients,
                "threads": threads,
                "duration_seconds": duration,
                "warmup_seconds": warmup,
                "connect_per_transaction": connect_per_transaction,
                "metrics": parse_pgbench_output(stdout, stderr),
                "tool": {"name": "pgbench", "version": tool_version(pgbench)},
                "raw": {"stdout": stdout, "stderr": stderr},
            }
        }

    def _stop_database_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        server_task_id = str(payload.get("server_task_id", ""))
        if not server_task_id:
            raise DatabaseBenchmarkError("Database cleanup task is missing its service ID.")
        return self._stop_database_server(server_task_id)

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
        active.log_handle.close()
        log_tail = self._tail_text(active.log_path)
        cleaned = self._remove_web_root(active.root)
        self.active_web_servers.pop(server_task_id, None)
        return {
            "status": "completed",
            "cleanup_verified": cleaned,
            "server_task_id": server_task_id,
            "server_returncode": active.process.returncode,
            "graceful_stop_returncode": graceful_returncode,
            "nginx_log_tail": log_tail,
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
        process: subprocess.Popen[str] | None = None
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
            config_path.write_text(
                "worker_processes auto;\n"
                f'pid "{self._nginx_path(pid_path)}";\n'
                f'error_log "{self._nginx_path(error_log_path)}" notice;\n'
                "events { worker_connections 4096; }\n"
                "http {\n"
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
                f"    listen {listen_host}:{https_port} ssl;\n"
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
                '    add_header X-CloudMark-Methodology "web-http-v1";\n'
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
                "access_policy": {"paired_generator_only": True, "allowed_client_address": allowed_client},
                "tools": {
                    "nginx": active.nginx_version,
                    "openssl": active.openssl_version,
                },
            }
        except BaseException:
            if process is not None:
                self._terminate_process(process)
            if log_handle is not None:
                log_handle.close()
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
        code, stdout, stderr = self._guarded_service_process(
            task_id,
            command(duration),
            environment=environment,
            expected_duration=duration,
            phase="measuring-web",
            current_job=path,
            completed_steps=completed_steps,
            total_steps=total_steps,
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
                "metrics": parse_ab_output(stdout, stderr),
                "tool": {"name": "ab", "version": web_tool_version("ab", ab)},
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
        payload = task.get("payload") or {}
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
        if kind == "database-server-stop":
            return self._stop_database_task(payload)
        if kind == "web-service-start":
            return self._start_web_server(str(task["id"]), payload)
        if kind == "web-client":
            return self._run_web_client(str(task["id"]), payload)
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
        except (DatabaseBenchmarkError, NetworkError, WebBenchmarkError, OSError, ValueError) as exc:
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
