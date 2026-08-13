from __future__ import annotations

import ipaddress
import json
import re
import time
import uuid
from typing import Any

from .database import Database
from .profiles import NETWORK_PROFILES
from .runner import JobContext, RunStopped
from .topology import enrich_pairing_session


ALLOWED_PORT_MIN = 5201
ALLOWED_PORT_MAX = 5210
ALLOWED_STREAMS = {1, 4, 8, 16}
ALLOWED_UDP_RATE_MIN = 100_000
ALLOWED_UDP_RATE_MAX = 1_000_000_000
ALLOWED_LATENCY_COUNT_MAX = 100
ALLOWED_LATENCY_INTERVAL_MS_MIN = 100
ALLOWED_LATENCY_INTERVAL_MS_MAX = 1_000
ALLOWED_LATENCY_TIMEOUT_MS_MIN = 100
ALLOWED_LATENCY_TIMEOUT_MS_MAX = 5_000


class NetworkError(RuntimeError):
    def __init__(self, message: str):
        super().__init__(message)
        self.partial_result: dict[str, Any] | None = None


def _address(agent: dict[str, Any]) -> str:
    address = str(agent.get("endpoint", {}).get("address", "")).strip()
    if not address:
        raise NetworkError(f"Agent {agent['id']} did not advertise a peer-reachable address.")
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError as exc:
        raise NetworkError(f"Agent {agent['id']} advertised an invalid IP address.") from exc
    if parsed.is_loopback or parsed.is_unspecified or parsed.is_multicast or parsed.is_link_local:
        raise NetworkError(f"Agent {agent['id']} must advertise a non-loopback unicast address.")
    return address


def validate_network_run(
    database: Database,
    session_id: str,
    profile_name: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if profile_name not in NETWORK_PROFILES:
        raise ValueError(f"Unknown network profile: {profile_name}")
    session = database.get_session(session_id)
    if not session:
        raise ValueError("Network run requires an existing pairing session.")
    session = enrich_pairing_session(session)
    agents = session["agents"]
    target = next((item for item in agents if item["role"] == "target"), None)
    generator = next((item for item in agents if item["role"] == "generator"), None)
    if not target or not generator:
        raise ValueError("Network run requires one target agent and one generator agent.")
    for agent in (target, generator):
        capabilities = agent.get("system", {}).get("inventory", {}).get("capabilities", {})
        if agent.get("status") != "online":
            raise ValueError(f"Agent {agent['name']} is offline. Start its persistent worker before running the profile.")
        if not capabilities.get("iperf3"):
            raise ValueError(f"Agent {agent['name']} does not report the iperf3 capability.")
        if str(NETWORK_PROFILES[profile_name]["methodology_version"]) == "network-v4":
            missing = [
                capability
                for capability in ("iproute2", "ethtool", "tcp_congestion_control")
                if not capabilities.get(capability)
            ]
            if missing:
                raise ValueError(
                    f"Agent {agent['name']} is missing Network v4 read-only evidence capabilities: "
                    + ", ".join(missing)
                    + ". Restart the Agent after installing the network pack."
                )
        _address(agent)
    return session, target, generator


def _wait_task(database: Database, task_id: str, context: JobContext, timeout_seconds: float) -> dict[str, Any]:
    started = time.monotonic()
    while True:
        context.checkpoint()
        task = database.get_agent_task(task_id)
        if not task:
            raise NetworkError(f"Distributed task {task_id} disappeared.")
        if task["status"] == "completed":
            return task
        if task["status"] == "failed":
            raise NetworkError(task.get("error") or f"Distributed task {task_id} failed.")
        if time.monotonic() - started > timeout_seconds:
            raise NetworkError(f"Agent did not complete {task['kind']} within {timeout_seconds:.0f} seconds.")
        time.sleep(0.25)


def _task(
    database: Database,
    run_id: str,
    session_id: str,
    agent_id: str,
    kind: str,
    payload: dict[str, Any],
) -> str:
    task_id = f"task_{uuid.uuid4().hex[:16]}"
    database.create_agent_task(task_id, run_id, session_id, agent_id, kind, payload)
    return task_id


def _tcp_rtt_metrics(payload: dict[str, Any], *, reverse: bool = False) -> dict[str, Any]:
    end = payload.get("end") or {}
    values: list[dict[str, float]] = []
    for stream in end.get("streams") or []:
        sender = stream.get("sender") or {}
        stream_reverse = bool(sender.get("reverse", stream.get("reverse", False)))
        if stream_reverse != reverse:
            continue
        if isinstance(sender.get("mean_rtt"), (int, float)):
            values.append(sender)
    if not values:
        return {
            "tcp_rtt_mean_ms": None,
            "tcp_rtt_min_ms": None,
            "tcp_rtt_max_ms": None,
            "tcp_rtt_streams": 0,
        }
    means = [float(item["mean_rtt"]) / 1000 for item in values]
    minimums = [float(item.get("min_rtt", item["mean_rtt"])) / 1000 for item in values]
    maximums = [float(item.get("max_rtt", item["mean_rtt"])) / 1000 for item in values]
    return {
        "tcp_rtt_mean_ms": round(sum(means) / len(means), 6),
        "tcp_rtt_min_ms": round(min(minimums), 6),
        "tcp_rtt_max_ms": round(max(maximums), 6),
        "tcp_rtt_streams": len(values),
    }


def _iperf_metrics(payload: dict[str, Any], *, reverse: bool = False) -> dict[str, Any]:
    end = payload.get("end") or {}
    suffix = "_bidir_reverse" if reverse else ""
    sent = end.get(f"sum_sent{suffix}") or {}
    received = end.get(f"sum_received{suffix}") or {}
    cpu = end.get("cpu_utilization_percent") or {}
    metrics = {
        "sent_bits_per_second": sent.get("bits_per_second"),
        "received_bits_per_second": received.get("bits_per_second"),
        "sent_bytes": sent.get("bytes"),
        "received_bytes": received.get("bytes"),
        "retransmits": sent.get("retransmits"),
        "sender_cpu_percent": cpu.get("host_total"),
        "receiver_cpu_percent": cpu.get("remote_total"),
    }
    metrics.update(_tcp_rtt_metrics(payload, reverse=reverse))
    return metrics


def _udp_metrics(payload: dict[str, Any], target_rate_bps: int) -> dict[str, Any]:
    end = payload.get("end") or {}
    sent = end.get("sum_sent") or {}
    received = end.get("sum_received") or end.get("sum") or {}
    cpu = end.get("cpu_utilization_percent") or {}
    packets = received.get("packets")
    lost_packets = received.get("lost_packets")
    return {
        "target_bits_per_second": target_rate_bps,
        "sent_bits_per_second": sent.get("bits_per_second"),
        "received_bits_per_second": received.get("bits_per_second"),
        "sent_bytes": sent.get("bytes"),
        "received_bytes": received.get("bytes"),
        "jitter_ms": received.get("jitter_ms"),
        "lost_packets": lost_packets,
        "packets": packets,
        "lost_percent": received.get("lost_percent"),
        "out_of_order": received.get("out_of_order"),
        "sender_cpu_percent": cpu.get("host_total"),
        "receiver_cpu_percent": cpu.get("remote_total"),
    }


def parse_ping_output(stdout: str) -> dict[str, Any]:
    packet_match = re.search(
        r"(?P<sent>\d+) packets transmitted,\s*(?P<received>\d+)(?: packets)? received,.*?"
        r"(?P<loss>[\d.]+)% packet loss",
        stdout,
        re.IGNORECASE | re.DOTALL,
    )
    windows_packet_match = re.search(
        r"Sent\s*=\s*(?P<sent>\d+),\s*Received\s*=\s*(?P<received>\d+),\s*"
        r"Lost\s*=\s*\d+\s*\((?P<loss>[\d.]+)%\s*loss\)",
        stdout,
        re.IGNORECASE,
    )
    packet_match = packet_match or windows_packet_match
    if not packet_match:
        raise NetworkError("ping output did not contain a supported packet summary.")

    rtt_match = re.search(
        r"(?:rtt|round-trip).*?=\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)\s*ms",
        stdout,
        re.IGNORECASE,
    )
    windows_rtt_match = re.search(
        r"Minimum\s*=\s*([\d.]+)ms,\s*Maximum\s*=\s*([\d.]+)ms,\s*Average\s*=\s*([\d.]+)ms",
        stdout,
        re.IGNORECASE,
    )
    if rtt_match:
        minimum, average, maximum, deviation = [float(value) for value in rtt_match.groups()]
    elif windows_rtt_match:
        minimum, maximum, average = [float(value) for value in windows_rtt_match.groups()]
        deviation = None
    else:
        raise NetworkError("ping output did not contain a supported latency summary.")

    return {
        "transmitted": int(packet_match.group("sent")),
        "received": int(packet_match.group("received")),
        "loss_percent": float(packet_match.group("loss")),
        "minimum_ms": minimum,
        "average_ms": average,
        "maximum_ms": maximum,
        "deviation_ms": deviation,
    }


def network_total_steps(profile_name: str) -> int:
    profile = NETWORK_PROFILES[profile_name]
    directions = len(profile["directions"])
    total = directions * len(profile["tcp_streams"])
    if profile.get("latency"):
        total += directions
    if profile.get("path_probe"):
        total += directions
    total += directions * len(profile.get("udp_rate_fractions") or [])
    if profile.get("bidirectional_streams"):
        total += 1
    return total


def network_default_timeout(profile_name: str) -> int:
    profile = NETWORK_PROFILES[profile_name]
    directions = len(profile["directions"])
    tcp_seconds = directions * len(profile["tcp_streams"]) * (int(profile["duration_seconds"]) + 60)
    udp_seconds = directions * len(profile.get("udp_rate_fractions") or []) * (
        int(profile.get("udp_duration_seconds", profile["duration_seconds"])) + 60
    )
    bidirectional_seconds = 0
    if profile.get("bidirectional_streams"):
        bidirectional_seconds = int(profile.get("bidirectional_duration_seconds", profile["duration_seconds"])) + 60
    latency_seconds = 0
    latency = profile.get("latency")
    if latency:
        latency_seconds = directions * min(
            120,
            int(latency["count"])
            * (int(latency["interval_ms"]) + int(latency["timeout_ms"]))
            // 1000
            + 20,
        )
    path_seconds = directions * 90 if profile.get("path_probe") else 0
    return tcp_seconds + udp_seconds + bidirectional_seconds + latency_seconds + path_seconds + 120


def _path_measurement(
    database: Database,
    context: JobContext,
    *,
    run_id: str,
    session_id: str,
    sender: dict[str, Any],
    receiver: dict[str, Any],
) -> dict[str, Any]:
    receiver_address = _address(receiver)
    label = f"{sender['name']} to {receiver['name']} - route and MTU evidence"
    context.report("collecting-network-path", label)
    task_id = _task(
        database,
        run_id,
        session_id,
        sender["id"],
        "network-path-probe",
        {"target_address": receiver_address},
    )
    task = _wait_task(database, task_id, context, 90)
    evidence = task.get("result") or {}
    if not isinstance(evidence, dict):
        raise NetworkError("Peer path probe returned an invalid result.")
    return {
        "direction": f"{sender['id']}-to-{receiver['id']}",
        "sender": {"id": sender["id"], "name": sender["name"], "role": sender["role"]},
        "receiver": {
            "id": receiver["id"],
            "name": receiver["name"],
            "role": receiver["role"],
            "address": receiver_address,
        },
        "evidence": evidence,
    }


def _latency_measurement(
    database: Database,
    context: JobContext,
    *,
    run_id: str,
    session_id: str,
    sender: dict[str, Any],
    receiver: dict[str, Any],
    count: int,
    interval_ms: int,
    timeout_ms: int,
) -> dict[str, Any]:
    receiver_address = _address(receiver)
    label = f"{sender['name']} to {receiver['name']} - idle latency"
    context.report("measuring-idle-latency", label)
    task_id = _task(
        database,
        run_id,
        session_id,
        sender["id"],
        "network-latency",
        {
            "target_address": receiver_address,
            "count": count,
            "interval_ms": interval_ms,
            "timeout_ms": timeout_ms,
        },
    )
    task_timeout = min(120, count * (interval_ms + timeout_ms) / 1000 + 20)
    task = _wait_task(database, task_id, context, task_timeout)
    task_result = task.get("result") or {}
    metrics = task_result.get("latency")
    if not isinstance(metrics, dict):
        raise NetworkError("Peer latency task returned an invalid result.")
    return {
        "direction": f"{sender['id']}-to-{receiver['id']}",
        "sender": {"id": sender["id"], "name": sender["name"], "role": sender["role"]},
        "receiver": {
            "id": receiver["id"],
            "name": receiver["name"],
            "role": receiver["role"],
            "address": receiver_address,
        },
        "protocol": "icmp",
        "count": count,
        "interval_ms": interval_ms,
        "timeout_ms": timeout_ms,
        "metrics": metrics,
        "tool": task_result.get("tool") or {"name": "ping", "version": None},
        "raw": task_result.get("raw"),
    }


def _measurement(
    database: Database,
    context: JobContext,
    *,
    run_id: str,
    session_id: str,
    sender: dict[str, Any],
    receiver: dict[str, Any],
    streams: int,
    duration_seconds: int,
    port: int,
    protocol: str = "tcp",
    target_rate_bps: int | None = None,
    bidirectional: bool = False,
) -> dict[str, Any]:
    if streams not in ALLOWED_STREAMS:
        raise NetworkError("Requested TCP stream count is outside the allow-list.")
    if not ALLOWED_PORT_MIN <= port <= ALLOWED_PORT_MAX:
        raise NetworkError("Requested iperf3 port is outside the CloudMark port range.")
    if protocol not in {"tcp", "udp"}:
        raise NetworkError("Requested network protocol is outside the allow-list.")
    if bidirectional and protocol != "tcp":
        raise NetworkError("Simultaneous bidirectional mode is supported only for TCP.")
    if protocol == "udp":
        if target_rate_bps is None or not ALLOWED_UDP_RATE_MIN <= target_rate_bps <= ALLOWED_UDP_RATE_MAX:
            raise NetworkError("Requested UDP target rate is outside the allow-list.")
        if streams != 1:
            raise NetworkError("Guarded UDP measurements require exactly one stream.")
    receiver_address = _address(receiver)
    mode = (
        "simultaneous bidirectional TCP"
        if bidirectional
        else f"{protocol.upper()} {streams} stream{'s' if streams != 1 else ''}"
    )
    label = f"{sender['name']} to {receiver['name']} - {mode}"
    context.report("starting-peer-server", label)
    server_task = _task(
        database,
        run_id,
        session_id,
        receiver["id"],
        "network-server-start",
        {
            "port": port,
            "address_family": "ipv6" if ipaddress.ip_address(receiver_address).version == 6 else "ipv4",
            "deadline_seconds": duration_seconds + 45,
        },
    )
    _wait_task(database, server_task, context, 30)

    context.report("measuring-network", label)
    client_task = _task(
        database,
        run_id,
        session_id,
        sender["id"],
        "network-client",
        {
            "target_address": receiver_address,
            "port": port,
            "duration_seconds": duration_seconds,
            "streams": streams,
            "protocol": protocol,
            "target_rate_bps": target_rate_bps,
            "bidirectional": bidirectional,
        },
    )
    client = _wait_task(database, client_task, context, duration_seconds + 45)

    collect_task = _task(
        database,
        run_id,
        session_id,
        receiver["id"],
        "network-server-collect",
        {"server_task_id": server_task, "timeout_seconds": 15},
    )
    server = _wait_task(database, collect_task, context, 25)
    client_payload = (client.get("result") or {}).get("iperf") or {}
    server_payload = (server.get("result") or {}).get("iperf") or {}
    iperf_version = (client_payload.get("start") or {}).get("version")
    normalized_metrics: dict[str, Any]
    if protocol == "udp":
        normalized_metrics = _udp_metrics(client_payload, int(target_rate_bps or 0))
    elif bidirectional:
        normalized_metrics = {
            "forward": _iperf_metrics(client_payload),
            "reverse": _iperf_metrics(client_payload, reverse=True),
        }
    else:
        normalized_metrics = _iperf_metrics(client_payload)
    measurement = {
        "direction": f"{sender['id']}-to-{receiver['id']}",
        "sender": {"id": sender["id"], "name": sender["name"], "role": sender["role"]},
        "receiver": {
            "id": receiver["id"],
            "name": receiver["name"],
            "role": receiver["role"],
            "address": receiver_address,
        },
        "protocol": protocol,
        "kind": "tcp-bidirectional" if bidirectional else f"{protocol}-throughput",
        "streams": streams,
        "duration_seconds": duration_seconds,
        "port": port,
        "metrics": normalized_metrics,
        "tool": {"name": "iperf3", "version": iperf_version},
        "raw": {"client": client_payload, "server": server_payload},
    }
    if target_rate_bps is not None:
        measurement["target_rate_bps"] = target_rate_bps
    if bidirectional:
        measurement["bidirectional"] = True
    return measurement


def _network_analysis(result: dict[str, Any]) -> dict[str, Any]:
    latency_by_direction = {item["direction"]: item for item in result["latency_measurements"]}
    tcp_by_direction: dict[str, list[dict[str, Any]]] = {}
    for item in result["measurements"]:
        tcp_by_direction.setdefault(item["direction"], []).append(item)
    udp_by_direction: dict[str, list[dict[str, Any]]] = {}
    for item in result["udp_measurements"]:
        udp_by_direction.setdefault(item["direction"], []).append(item)

    cpu_limit = float((result.get("validity_policy") or {}).get("generator_cpu_limit_percent", 90))
    scaling_cpu_floor = float((result.get("validity_policy") or {}).get("generator_scaling_cpu_floor_percent", 85))
    scaling_gain_floor = float((result.get("validity_policy") or {}).get("generator_scaling_gain_floor_percent", 5))
    directions: list[dict[str, Any]] = []
    headroom_statuses: list[str] = []
    for direction in sorted(set(latency_by_direction) | set(tcp_by_direction) | set(udp_by_direction)):
        idle = (latency_by_direction.get(direction) or {}).get("metrics") or {}
        tcp_items = tcp_by_direction.get(direction) or []
        loaded_candidates = [
            item
            for item in tcp_items
            if isinstance((item.get("metrics") or {}).get("tcp_rtt_mean_ms"), (int, float))
        ]
        loaded = max(loaded_candidates, key=lambda item: int(item.get("streams", 0)), default=None)
        loaded_rtt = (loaded or {}).get("metrics", {}).get("tcp_rtt_mean_ms")
        idle_rtt = idle.get("average_ms")
        inflation_ms = None
        inflation_percent = None
        if isinstance(idle_rtt, (int, float)) and isinstance(loaded_rtt, (int, float)):
            inflation_ms = round(float(loaded_rtt) - float(idle_rtt), 6)
            if float(idle_rtt) > 0:
                inflation_percent = round((float(loaded_rtt) / float(idle_rtt) - 1) * 100, 3)
        udp_items = udp_by_direction.get(direction) or []
        highest_udp = max(udp_items, key=lambda item: int(item.get("target_rate_bps", 0)), default=None)
        generator_cpu_values: list[float] = []
        for item in tcp_items:
            metrics = item.get("metrics") or {}
            generator_cpu = (
                metrics.get("sender_cpu_percent")
                if (item.get("sender") or {}).get("role") == "generator"
                else metrics.get("receiver_cpu_percent")
                if (item.get("receiver") or {}).get("role") == "generator"
                else None
            )
            if isinstance(generator_cpu, (int, float)):
                generator_cpu_values.append(float(generator_cpu))
        peak_generator_cpu = max(generator_cpu_values, default=None)
        ordered_tcp = sorted(tcp_items, key=lambda item: int(item.get("streams", 0)))
        scaling_gain_percent = None
        if len(ordered_tcp) >= 2:
            low_rate = float((ordered_tcp[0].get("metrics") or {}).get("received_bits_per_second") or 0)
            high_rate = float((ordered_tcp[-1].get("metrics") or {}).get("received_bits_per_second") or 0)
            if low_rate > 0:
                scaling_gain_percent = round((high_rate / low_rate - 1) * 100, 3)
        headroom_reasons: list[str] = []
        if peak_generator_cpu is None:
            headroom_status = "unknown"
            headroom_reasons.append("generator-cpu-unavailable")
        elif peak_generator_cpu >= cpu_limit:
            headroom_status = "constrained"
            headroom_reasons.append("generator-cpu-at-or-above-limit")
        elif (
            peak_generator_cpu >= scaling_cpu_floor
            and scaling_gain_percent is not None
            and scaling_gain_percent < scaling_gain_floor
        ):
            headroom_status = "constrained"
            headroom_reasons.append("stream-scaling-stalled-near-generator-cpu-limit")
        else:
            headroom_status = "adequate"
        headroom_statuses.append(headroom_status)
        directions.append(
            {
                "direction": direction,
                "idle_icmp_average_ms": idle_rtt,
                "idle_icmp_loss_percent": idle.get("loss_percent"),
                "loaded_tcp_rtt_mean_ms": loaded_rtt,
                "loaded_tcp_streams": (loaded or {}).get("streams"),
                "latency_inflation_ms": inflation_ms,
                "latency_inflation_percent": inflation_percent,
                "peak_tcp_received_bits_per_second": max(
                    [float((item.get("metrics") or {}).get("received_bits_per_second") or 0) for item in tcp_items],
                    default=0,
                ),
                "highest_udp_target_bits_per_second": (highest_udp or {}).get("target_rate_bps"),
                "highest_udp_loss_percent": (highest_udp or {}).get("metrics", {}).get("lost_percent"),
                "highest_udp_jitter_ms": (highest_udp or {}).get("metrics", {}).get("jitter_ms"),
                "generator_headroom": {
                    "status": headroom_status,
                    "peak_cpu_percent": peak_generator_cpu,
                    "stream_scaling_gain_percent": scaling_gain_percent,
                    "reason_codes": headroom_reasons,
                },
            }
        )
    path_items = result.get("path_measurements") or []
    path_statuses = [str((item.get("evidence") or {}).get("status", "unavailable")) for item in path_items]
    if len(path_statuses) >= 2 and all(status == "complete" for status in path_statuses):
        route_status = "complete"
    elif any(status in {"complete", "partial"} for status in path_statuses):
        route_status = "partial"
    else:
        route_status = "unavailable"
    nic_direction_statuses: list[str] = []
    for item in path_items:
        evidence = item.get("evidence") or {}
        interface = evidence.get("interface") or {}
        evidence_statuses = [
            str((interface.get("driver") or {}).get("status", "unavailable")),
            str((interface.get("offloads") or {}).get("status", "unavailable")),
            str(((evidence.get("tcp") or {}).get("congestion_control") or {}).get("status", "unavailable")),
        ]
        if all(status == "observed" for status in evidence_statuses):
            nic_direction_statuses.append("complete")
        elif "observed" in evidence_statuses:
            nic_direction_statuses.append("partial")
        else:
            nic_direction_statuses.append("unavailable")
    if len(nic_direction_statuses) >= 2 and all(status == "complete" for status in nic_direction_statuses):
        nic_status = "complete"
    elif any(status in {"complete", "partial"} for status in nic_direction_statuses):
        nic_status = "partial"
    else:
        nic_status = "unavailable"
    if len(headroom_statuses) >= 2 and all(status == "adequate" for status in headroom_statuses):
        generator_status = "adequate"
    elif "constrained" in headroom_statuses:
        generator_status = "constrained"
    else:
        generator_status = "unknown"
    validity_reasons: list[str] = []
    if route_status != "complete":
        validity_reasons.append("route-interface-mtu-evidence-incomplete")
    if generator_status != "adequate":
        validity_reasons.append(f"generator-headroom-{generator_status}")
    nic_evidence_required = str(result.get("methodology_version", "")) == "network-v4"
    if nic_evidence_required and nic_status != "complete":
        validity_reasons.append("nic-offload-and-tcp-control-evidence-incomplete")
    comparison_eligible = (
        route_status == "complete"
        and generator_status == "adequate"
        and (not nic_evidence_required or nic_status == "complete")
    )
    return {
        "directions": directions,
        "latency_comparison": "Idle ICMP average versus iperf3 TCP_INFO mean RTT under throughput load; protocols differ.",
        "validity": {
            "route_evidence_status": route_status,
            "nic_evidence_status": nic_status,
            "nic_evidence_required": nic_evidence_required,
            "generator_headroom_status": generator_status,
            "comparison_eligible": comparison_eligible,
            "reason_codes": validity_reasons,
        },
        "scored": False,
    }


def run_network(
    database: Database,
    run_id: str,
    session_id: str,
    profile_name: str,
    *,
    context: JobContext,
) -> dict[str, Any]:
    session, target, generator = validate_network_run(database, session_id, profile_name)
    profile = NETWORK_PROFILES[profile_name]
    duration = int(profile["duration_seconds"])
    streams_list = [int(value) for value in profile["tcp_streams"]]
    udp_fractions = [float(value) for value in profile.get("udp_rate_fractions") or []]
    latency = profile.get("latency")
    bidirectional_streams = profile.get("bidirectional_streams")
    path_probe = profile.get("path_probe") is True
    result: dict[str, Any] = {
        "suite": "network",
        "profile": profile_name,
        "profile_version": profile["profile_version"],
        "methodology_version": profile["methodology_version"],
        "session": {
            "id": session["id"],
            "label": session["label"],
            "topology": session.get("topology") or {"scope": "undeclared", "source": "unavailable"},
        },
        "policy": {
            "controller_in_data_path": False,
            "arbitrary_destination_allowed": False,
            "tcp_only": not bool(udp_fractions),
            "udp_rate_cap_bits_per_second": ALLOWED_UDP_RATE_MAX,
            "simultaneous_bidirectional": bool(bidirectional_streams),
            "route_interface_mtu_evidence": path_probe,
            "read_only_nic_and_tcp_control_evidence": path_probe,
            "port_range": [ALLOWED_PORT_MIN, ALLOWED_PORT_MAX],
        },
        "validity_policy": {
            "generator_cpu_limit_percent": profile.get("generator_cpu_limit_percent", 90),
            "generator_scaling_cpu_floor_percent": profile.get("generator_scaling_cpu_floor_percent", 85),
            "generator_scaling_gain_floor_percent": profile.get("generator_scaling_gain_floor_percent", 5),
        },
        "path_measurements": [],
        "latency_measurements": [],
        "measurements": [],
        "udp_measurements": [],
        "bidirectional_measurements": [],
    }
    directions = [(generator, target), (target, generator)]
    index = 0
    try:
        if path_probe:
            for sender, receiver in directions:
                measurement = _path_measurement(
                    database,
                    context,
                    run_id=run_id,
                    session_id=session_id,
                    sender=sender,
                    receiver=receiver,
                )
                result["path_measurements"].append(measurement)
                result["analysis"] = _network_analysis(result)
                context.complete_step("network-path-evidence-complete", None, partial_result=result)

        if latency:
            for sender, receiver in directions:
                measurement = _latency_measurement(
                    database,
                    context,
                    run_id=run_id,
                    session_id=session_id,
                    sender=sender,
                    receiver=receiver,
                    count=int(latency["count"]),
                    interval_ms=int(latency["interval_ms"]),
                    timeout_ms=int(latency["timeout_ms"]),
                )
                result["latency_measurements"].append(measurement)
                result["analysis"] = _network_analysis(result)
                context.complete_step("latency-measurement-complete", None, partial_result=result)

        for sender, receiver in directions:
            for streams in streams_list:
                measurement = _measurement(
                    database,
                    context,
                    run_id=run_id,
                    session_id=session_id,
                    sender=sender,
                    receiver=receiver,
                    streams=streams,
                    duration_seconds=duration,
                    port=ALLOWED_PORT_MIN + (index % (ALLOWED_PORT_MAX - ALLOWED_PORT_MIN + 1)),
                )
                result["measurements"].append(measurement)
                if "tool" not in result:
                    result["tool"] = measurement["tool"]
                result["analysis"] = _network_analysis(result)
                context.complete_step("tcp-measurement-complete", None, partial_result=result)
                index += 1

        if udp_fractions:
            udp_min = int(profile["udp_min_rate_bps"])
            udp_max = min(int(profile["udp_max_rate_bps"]), ALLOWED_UDP_RATE_MAX)
            udp_duration = int(profile["udp_duration_seconds"])
            for sender, receiver in directions:
                direction = f"{sender['id']}-to-{receiver['id']}"
                tcp_peak = max(
                    [
                        float((item.get("metrics") or {}).get("received_bits_per_second") or 0)
                        for item in result["measurements"]
                        if item["direction"] == direction
                    ],
                    default=0,
                )
                if tcp_peak <= 0:
                    raise NetworkError(f"Cannot derive guarded UDP rates because {direction} has no valid TCP baseline.")
                for fraction in udp_fractions:
                    target_rate = int(round(tcp_peak * fraction / 1_000_000) * 1_000_000)
                    target_rate = max(udp_min, min(udp_max, target_rate))
                    measurement = _measurement(
                        database,
                        context,
                        run_id=run_id,
                        session_id=session_id,
                        sender=sender,
                        receiver=receiver,
                        streams=1,
                        duration_seconds=udp_duration,
                        port=ALLOWED_PORT_MIN + (index % (ALLOWED_PORT_MAX - ALLOWED_PORT_MIN + 1)),
                        protocol="udp",
                        target_rate_bps=target_rate,
                    )
                    measurement["rate_fraction_of_tcp_peak"] = fraction
                    measurement["tcp_peak_bits_per_second"] = tcp_peak
                    result["udp_measurements"].append(measurement)
                    result["analysis"] = _network_analysis(result)
                    context.complete_step("udp-measurement-complete", None, partial_result=result)
                    index += 1

        if bidirectional_streams:
            measurement = _measurement(
                database,
                context,
                run_id=run_id,
                session_id=session_id,
                sender=generator,
                receiver=target,
                streams=int(bidirectional_streams),
                duration_seconds=int(profile["bidirectional_duration_seconds"]),
                port=ALLOWED_PORT_MIN + (index % (ALLOWED_PORT_MAX - ALLOWED_PORT_MIN + 1)),
                bidirectional=True,
            )
            result["bidirectional_measurements"].append(measurement)
            result["analysis"] = _network_analysis(result)
            context.complete_step("bidirectional-measurement-complete", None, partial_result=result)
    except (RunStopped, NetworkError) as exc:
        result["analysis"] = _network_analysis(result)
        exc.partial_result = result
        raise
    result["analysis"] = _network_analysis(result)
    return result


def parse_iperf_json(stdout: str) -> dict[str, Any]:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise NetworkError("iperf3 returned invalid JSON output.") from exc
    if not isinstance(value, dict):
        raise NetworkError("iperf3 returned an unexpected result shape.")
    if value.get("error"):
        raise NetworkError(f"iperf3 reported: {value['error']}")
    return value
