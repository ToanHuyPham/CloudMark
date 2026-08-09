from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .database import Database
from .profiles import COMPUTE_PROFILES, MEMORY_PROFILES, STORAGE_PROFILES
from .runner import JobContext, RunCancelled, RunStopped


REMOTE_METHODOLOGY_VERSION = "remote-agent-v1"
AGENT_ONLINE_SECONDS = 30
TASK_HEARTBEAT_GRACE_SECONDS = 45


class RemoteError(RuntimeError):
    def __init__(self, message: str, partial_result: dict[str, Any] | None = None):
        super().__init__(message)
        self.partial_result = partial_result


def _profiles(suite: str) -> dict[str, dict[str, Any]]:
    if suite == "compute":
        return COMPUTE_PROFILES
    if suite == "memory":
        return MEMORY_PROFILES
    if suite == "storage":
        return STORAGE_PROFILES
    raise ValueError(f"Remote execution does not support suite: {suite}")


def remote_default_timeout(suite: str, profile_name: str) -> int:
    profiles = _profiles(suite)
    if profile_name not in profiles:
        raise ValueError(f"Unknown {suite} profile: {profile_name}")
    profile = profiles[profile_name]
    runtime = sum(int(job.get("runtime", 0)) + int(job.get("warmup", 0)) + int(job.get("ramp_time", 0)) for job in profile["jobs"])
    storage_overhead = 300 if suite == "storage" else 180
    return max(180, runtime + storage_overhead)


def remote_total_steps(suite: str, profile_name: str) -> int:
    profiles = _profiles(suite)
    if profile_name not in profiles:
        raise ValueError(f"Unknown {suite} profile: {profile_name}")
    return len(profiles[profile_name]["jobs"]) + (2 if suite == "storage" else 0)


def _fresh(timestamp: Any, seconds: int) -> bool:
    try:
        observed = datetime.fromisoformat(str(timestamp))
    except (TypeError, ValueError):
        return False
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed >= datetime.now(timezone.utc) - timedelta(seconds=seconds)


def validate_remote_agent(
    database: Database,
    agent_id: str,
    suite: str,
    profile_name: str,
) -> dict[str, Any]:
    _profiles(suite)
    remote_total_steps(suite, profile_name)
    agent = database.get_agent(agent_id)
    if not agent:
        raise ValueError("Remote benchmark requires an existing Agent.")
    if agent.get("status") != "online" or not _fresh(agent.get("last_seen_at"), AGENT_ONLINE_SECONDS):
        raise ValueError(f"Agent {agent.get('name', agent_id)} is offline. Start its persistent worker first.")
    inventory = agent.get("system", {}).get("inventory", {})
    capabilities = inventory.get("capabilities", {})
    required = {"compute": "sysbench", "memory": "gcc", "storage": "fio"}[suite]
    if not capabilities.get(required):
        raise ValueError(f"Agent {agent.get('name', agent_id)} does not report the {required} capability.")
    if suite == "memory" and inventory.get("os", {}).get("system") != "Linux":
        raise ValueError("The native memory executor currently requires a Linux Agent with GCC and OpenMP.")
    if database.has_active_agent_task(agent_id):
        raise ValueError(f"Agent {agent.get('name', agent_id)} already has an active task.")
    return agent


def _partial_from_task(task: dict[str, Any]) -> dict[str, Any] | None:
    payload = task.get("result")
    if not isinstance(payload, dict):
        return None
    benchmark = payload.get("benchmark")
    return benchmark if isinstance(benchmark, dict) else payload


def _attributed_result(
    task: dict[str, Any],
    agent: dict[str, Any],
    suite: str,
    profile_name: str,
) -> dict[str, Any]:
    payload = task.get("result") or {}
    if not isinstance(payload, dict) or not isinstance(payload.get("benchmark"), dict):
        raise RemoteError("Agent returned an invalid benchmark result envelope.")
    if payload.get("protocol_version") != REMOTE_METHODOLOGY_VERSION:
        raise RemoteError("Agent returned an incompatible remote benchmark protocol version.")
    result = dict(payload["benchmark"])
    expected_profile = _profiles(suite)[profile_name]
    if result.get("suite") != suite or result.get("profile") != profile_name:
        raise RemoteError("Agent result suite or profile does not match the dispatched task.")
    if result.get("profile_version") != expected_profile["profile_version"]:
        raise RemoteError("Agent result profile version does not match the Controller profile.")
    if result.get("methodology_version") != expected_profile["methodology_version"]:
        raise RemoteError("Agent result methodology version does not match the Controller methodology.")
    result["execution"] = {
        "mode": "remote-agent",
        "protocol_version": REMOTE_METHODOLOGY_VERSION,
        "agent_version": str(payload.get("agent_version", "unknown")),
        "agent": {
            "id": agent["id"],
            "name": agent["name"],
            "role": agent["role"],
            "session_id": agent["session_id"],
        },
    }
    evidence = payload.get("evidence")
    if isinstance(evidence, dict):
        result["target_evidence"] = evidence
    return result


def run_remote_benchmark(
    database: Database,
    run_id: str,
    agent: dict[str, Any],
    suite: str,
    profile_name: str,
    timeout_seconds: int,
    *,
    context: JobContext,
) -> dict[str, Any]:
    # Validation happens when the run is accepted, but another network or
    # benchmark task can win the queue before this worker starts executing.
    # Re-check immediately before creating the durable Agent task.
    if database.has_active_agent_task(str(agent["id"])):
        raise RemoteError(f"Agent {agent['name']} already has an active task.")
    task_id = f"task_{uuid.uuid4().hex[:16]}"
    database.create_agent_task(
        task_id,
        run_id,
        str(agent["session_id"]),
        str(agent["id"]),
        f"benchmark-{suite}",
        {
            "suite": suite,
            "profile": profile_name,
            "timeout_seconds": timeout_seconds,
            "load_confirmed": True,
            "protocol_version": REMOTE_METHODOLOGY_VERSION,
        },
    )
    try:
        while True:
            context.checkpoint()
            task = database.get_agent_task(task_id)
            if not task:
                raise RemoteError(f"Remote task {task_id} disappeared.")
            if task["status"] == "completed":
                return _attributed_result(task, agent, suite, profile_name)
            if task["status"] == "cancelled":
                stopped = RunCancelled(task.get("error") or "Remote benchmark was cancelled.")
                stopped.partial_result = _partial_from_task(task)
                raise stopped
            if task["status"] == "failed":
                raise RemoteError(task.get("error") or "Remote benchmark failed.", _partial_from_task(task))
            heartbeat = task.get("heartbeat_at") or task.get("claimed_at")
            if task["status"] == "running" and not _fresh(heartbeat, TASK_HEARTBEAT_GRACE_SECONDS):
                raise RemoteError(
                    f"Agent task heartbeat was absent for more than {TASK_HEARTBEAT_GRACE_SECONDS} seconds.",
                    _partial_from_task(task),
                )
            time.sleep(0.25)
    except BaseException as exc:
        current = database.get_agent_task(task_id)
        if isinstance(exc, RunStopped) and current:
            exc.partial_result = _partial_from_task(current)
        database.abort_agent_task(task_id, f"Controller stopped the remote task: {exc}")
        if isinstance(exc, RunStopped):
            raise
        if isinstance(exc, RemoteError):
            raise
        raise
