from __future__ import annotations

import ipaddress
import time
import uuid
from typing import Any

from .database import Database
from .runner import JobContext
from .topology import enrich_pairing_session


class DistributedError(RuntimeError):
    pass


def peer_address(agent: dict[str, Any]) -> str:
    address = str(agent.get("endpoint", {}).get("address", "")).strip()
    if not address:
        raise DistributedError(f"Agent {agent['id']} did not advertise a peer-reachable address.")
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError as exc:
        raise DistributedError(f"Agent {agent['id']} advertised an invalid IP address.") from exc
    if parsed.is_loopback or parsed.is_unspecified or parsed.is_multicast or parsed.is_link_local:
        raise DistributedError(f"Agent {agent['id']} must advertise a non-loopback unicast address.")
    return address


def validate_pair(
    database: Database,
    session_id: str,
    *,
    target_capabilities: tuple[str, ...],
    generator_capabilities: tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    session = database.get_session(session_id)
    if not session:
        raise ValueError("Distributed run requires an existing pairing session.")
    session = enrich_pairing_session(session)
    target = next((item for item in session["agents"] if item["role"] == "target"), None)
    generator = next((item for item in session["agents"] if item["role"] == "generator"), None)
    if not target or not generator:
        raise ValueError("Distributed run requires one target Agent and one generator Agent.")
    for agent, required in ((target, target_capabilities), (generator, generator_capabilities)):
        if agent.get("status") != "online":
            raise ValueError(f"Agent {agent['name']} is offline. Start its persistent worker before running the profile.")
        capabilities = agent.get("system", {}).get("inventory", {}).get("capabilities", {})
        missing = [capability for capability in required if not capabilities.get(capability)]
        if missing:
            raise ValueError(f"Agent {agent['name']} is missing required capabilities: {', '.join(missing)}.")
        peer_address(agent)
    return session, target, generator


def create_task(
    database: Database,
    run_id: str,
    session_id: str,
    agent_id: str,
    kind: str,
    payload: dict[str, Any],
    *,
    ephemeral_secret: dict[str, str] | None = None,
) -> str:
    task_id = f"task_{uuid.uuid4().hex[:16]}"
    database.create_agent_task(
        task_id,
        run_id,
        session_id,
        agent_id,
        kind,
        payload,
        ephemeral_secret=ephemeral_secret,
    )
    return task_id


def wait_task(
    database: Database,
    task_id: str,
    *,
    timeout_seconds: float,
    context: JobContext | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    while True:
        if context is not None:
            context.checkpoint()
        task = database.get_agent_task(task_id)
        if not task:
            raise DistributedError(f"Distributed task {task_id} disappeared.")
        if task["status"] == "completed":
            return task
        if task["status"] in {"failed", "cancelled"}:
            raise DistributedError(task.get("error") or f"Distributed task {task_id} {task['status']}.")
        if time.monotonic() - started > timeout_seconds:
            raise DistributedError(f"Agent did not complete {task['kind']} within {timeout_seconds:.0f} seconds.")
        time.sleep(0.25)
