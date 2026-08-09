from __future__ import annotations

import ipaddress
import json
import time
import uuid
from typing import Any

from .database import Database
from .profiles import NETWORK_PROFILES
from .runner import JobContext, RunStopped


NETWORK_METHODOLOGY_VERSION = "network-v1"
ALLOWED_PORT_MIN = 5201
ALLOWED_PORT_MAX = 5210
ALLOWED_STREAMS = {1, 4, 8, 16}


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
    agents = session["agents"]
    target = next((item for item in agents if item["role"] == "target"), None)
    generator = next((item for item in agents if item["role"] == "generator"), None)
    if not target or not generator:
        raise ValueError("Network run requires one target agent and one generator agent.")
    for agent in (target, generator):
        if agent.get("status") != "online":
            raise ValueError(f"Agent {agent['name']} is offline. Start its persistent worker before running the profile.")
        if not agent.get("system", {}).get("inventory", {}).get("capabilities", {}).get("iperf3"):
            raise ValueError(f"Agent {agent['name']} does not report the iperf3 capability.")
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


def _iperf_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    end = payload.get("end") or {}
    sent = end.get("sum_sent") or {}
    received = end.get("sum_received") or {}
    cpu = end.get("cpu_utilization_percent") or {}
    return {
        "sent_bits_per_second": sent.get("bits_per_second"),
        "received_bits_per_second": received.get("bits_per_second"),
        "sent_bytes": sent.get("bytes"),
        "received_bytes": received.get("bytes"),
        "retransmits": sent.get("retransmits"),
        "sender_cpu_percent": cpu.get("host_total"),
        "receiver_cpu_percent": cpu.get("remote_total"),
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
) -> dict[str, Any]:
    if streams not in ALLOWED_STREAMS:
        raise NetworkError("Requested TCP stream count is outside the allow-list.")
    if not ALLOWED_PORT_MIN <= port <= ALLOWED_PORT_MAX:
        raise NetworkError("Requested iperf3 port is outside the CloudMark port range.")
    receiver_address = _address(receiver)
    label = f"{sender['name']} to {receiver['name']} · {streams} stream{'s' if streams != 1 else ''}"
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
    return {
        "direction": f"{sender['id']}-to-{receiver['id']}",
        "sender": {"id": sender["id"], "name": sender["name"], "role": sender["role"]},
        "receiver": {
            "id": receiver["id"],
            "name": receiver["name"],
            "role": receiver["role"],
            "address": receiver_address,
        },
        "protocol": "tcp",
        "streams": streams,
        "duration_seconds": duration_seconds,
        "port": port,
        "metrics": _iperf_metrics(client_payload),
        "tool": {"name": "iperf3", "version": iperf_version},
        "raw": {"client": client_payload, "server": server_payload},
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
    result: dict[str, Any] = {
        "suite": "network",
        "profile": profile_name,
        "profile_version": profile["profile_version"],
        "methodology_version": profile["methodology_version"],
        "session": {"id": session["id"], "label": session["label"]},
        "policy": {
            "controller_in_data_path": False,
            "arbitrary_destination_allowed": False,
            "tcp_only": True,
            "port_range": [ALLOWED_PORT_MIN, ALLOWED_PORT_MAX],
        },
        "measurements": [],
    }
    directions = [(generator, target), (target, generator)]
    index = 0
    try:
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
                context.complete_step("network-measurement-complete", None, partial_result=result)
                index += 1
    except (RunStopped, NetworkError) as exc:
        exc.partial_result = result
        raise
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
