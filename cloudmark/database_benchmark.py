from __future__ import annotations

import re
from typing import Any

from .database import Database
from .distributed import DistributedError, create_task, peer_address, validate_pair, wait_task
from .profiles import DATABASE_PROFILES
from .runner import JobContext, RunStopped


DATABASE_PORT_MIN = 55432
DATABASE_PORT_MAX = 55439
DATABASE_MAX_SCALE = 100
DATABASE_ALLOWED_CLIENTS = {1, 4, 16}
DATABASE_ALLOWED_THREADS = {1, 2, 4}
DATABASE_MAX_DURATION = 60


class DatabaseBenchmarkError(RuntimeError):
    def __init__(self, message: str):
        super().__init__(message)
        self.partial_result: dict[str, Any] | None = None


def parse_pgbench_output(stdout: str, stderr: str = "") -> dict[str, Any]:
    combined = f"{stdout}\n{stderr}"

    def number(pattern: str) -> float | None:
        match = re.search(pattern, combined, re.IGNORECASE)
        return float(match.group(1)) if match else None

    processed = number(r"number of transactions actually processed:\s*([\d.]+)")
    failed = number(r"number of failed transactions:\s*([\d.]+)")
    tps = number(r"tps\s*=\s*([\d.]+)")
    latency = number(r"latency average\s*=\s*([\d.]+)\s*ms")
    connection = number(r"initial connection time\s*=\s*([\d.]+)\s*ms")
    if processed is None or tps is None or latency is None:
        raise DatabaseBenchmarkError("pgbench output did not contain the required transaction summary.")
    progress = [
        {
            "elapsed_seconds": float(match.group(1)),
            "tps": float(match.group(2)),
            "latency_average_ms": float(match.group(3)),
            "latency_stddev_ms": float(match.group(4)) if match.group(4) else None,
            "failed": int(match.group(5) or 0),
        }
        for match in re.finditer(
            r"progress:\s*([\d.]+)\s*s,\s*([\d.]+)\s*tps,\s*lat\s*([\d.]+)\s*ms"
            r"(?:\s*stddev\s*([\d.]+))?(?:,\s*(\d+)\s*failed)?",
            combined,
            re.IGNORECASE,
        )
    ]
    return {
        "transactions_processed": int(processed),
        "failed_transactions": int(failed or 0),
        "transactions_per_second": tps,
        "latency_average_ms": latency,
        "initial_connection_time_ms": connection,
        "progress": progress,
        "tail_latency_status": "unavailable",
    }


def database_total_steps(profile_name: str) -> int:
    return len(DATABASE_PROFILES[profile_name]["jobs"]) + 2


def database_default_timeout(profile_name: str) -> int:
    profile = DATABASE_PROFILES[profile_name]
    job_seconds = sum(int(job["duration"]) + int(job.get("warmup", 0)) + 45 for job in profile["jobs"])
    return 300 + job_seconds + 120


def validate_database_run(
    database: Database,
    session_id: str,
    profile_name: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if profile_name not in DATABASE_PROFILES:
        raise ValueError(f"Unknown database profile: {profile_name}")
    return validate_pair(
        database,
        session_id,
        target_capabilities=("postgres", "initdb", "pgbench", "pg_isready"),
        generator_capabilities=("pgbench",),
    )


def run_database(
    database: Database,
    run_id: str,
    session_id: str,
    profile_name: str,
    *,
    context: JobContext,
) -> dict[str, Any]:
    session, target, generator = validate_database_run(database, session_id, profile_name)
    profile = DATABASE_PROFILES[profile_name]
    target_address = peer_address(target)
    generator_address = peer_address(generator)
    port = int(profile["port"])
    result: dict[str, Any] = {
        "suite": "database",
        "engine": "postgresql",
        "profile": profile_name,
        "profile_version": profile["profile_version"],
        "methodology_version": profile["methodology_version"],
        "session": {"id": session["id"], "label": session["label"]},
        "target": {"id": target["id"], "name": target["name"], "address": target_address},
        "generator": {"id": generator["id"], "name": generator["name"], "address": generator_address},
        "policy": {
            "controller_in_data_path": False,
            "ephemeral_cluster": True,
            "durability_enabled": True,
            "arbitrary_sql_allowed": False,
            "port_range": [DATABASE_PORT_MIN, DATABASE_PORT_MAX],
        },
        "database_measurements": [],
        "cleanup": {"status": "pending"},
    }
    server_task: str | None = None
    cleanup_scheduled = False
    try:
        context.report("preparing-database", f"Initialize PostgreSQL scale {profile['scale_factor']} on {target['name']}")
        server_task = create_task(
            database,
            run_id,
            session_id,
            target["id"],
            "database-server-start",
            {
                "listen_address": target_address,
                "allowed_client_address": generator_address,
                "port": port,
                "scale_factor": int(profile["scale_factor"]),
                "max_connections": max(int(job["clients"]) for job in profile["jobs"]) + 10,
                "deadline_seconds": database_default_timeout(profile_name),
                "run_completed_steps": 0,
                "run_total_steps": database_total_steps(profile_name),
            },
        )
        started = wait_task(database, server_task, timeout_seconds=300, context=context)
        result["server"] = started.get("result") or {}
        context.complete_step("database-ready", None, partial_result=result)

        for job_index, job in enumerate(profile["jobs"]):
            context.report("measuring-database", str(job["name"]))
            client_task = create_task(
                database,
                run_id,
                session_id,
                generator["id"],
                "database-client",
                {
                    "target_address": target_address,
                    "port": port,
                    "workload": job["workload"],
                    "clients": int(job["clients"]),
                    "threads": int(job["threads"]),
                    "duration_seconds": int(job["duration"]),
                    "warmup_seconds": int(job.get("warmup", 0)),
                    "connect_per_transaction": bool(job.get("connect_per_transaction", False)),
                    "run_completed_steps": job_index + 1,
                    "run_total_steps": database_total_steps(profile_name),
                },
            )
            completed = wait_task(
                database,
                client_task,
                timeout_seconds=int(job["duration"]) + int(job.get("warmup", 0)) + 45,
                context=context,
            )
            payload = completed.get("result") or {}
            measurement = payload.get("pgbench")
            if not isinstance(measurement, dict):
                raise DatabaseBenchmarkError("Database client returned an invalid result.")
            result["database_measurements"].append({"name": job["name"], **measurement})
            context.complete_step("database-measurement-complete", None, partial_result=result)

        context.report("cleaning-database", f"Remove ephemeral PostgreSQL cluster on {target['name']}")
        cleanup_task = create_task(
            database,
            run_id,
            session_id,
            target["id"],
            "database-server-stop",
            {"server_task_id": server_task},
        )
        cleanup_scheduled = True
        cleaned = wait_task(database, cleanup_task, timeout_seconds=45, context=None)
        result["cleanup"] = cleaned.get("result") or {"status": "completed"}
        context.complete_step("database-cleanup-complete", None, partial_result=result)
    except (RunStopped, DistributedError, DatabaseBenchmarkError) as exc:
        if server_task and not cleanup_scheduled:
            cleanup_task = create_task(
                database,
                run_id,
                session_id,
                target["id"],
                "database-server-stop",
                {"server_task_id": server_task},
            )
            cleanup_scheduled = True
            result["cleanup"] = {"status": "scheduled", "task_id": cleanup_task}
            try:
                cleaned = wait_task(database, cleanup_task, timeout_seconds=30, context=None)
                result["cleanup"] = cleaned.get("result") or {"status": "completed"}
            except DistributedError:
                pass
        exc.partial_result = result
        raise
    return result
