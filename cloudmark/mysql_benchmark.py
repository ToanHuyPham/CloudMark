from __future__ import annotations

import re
import secrets
from typing import Any

from .database import Database
from .distributed import DistributedError, create_task, peer_address, validate_pair, wait_task
from .profiles import DATABASE_PROFILES
from .runner import JobContext, RunStopped


MYSQL_PORT = 57306
MYSQL_GENERATOR_CPU_LIMIT_PERCENT = 90.0


class MySQLBenchmarkError(RuntimeError):
    def __init__(self, message: str):
        super().__init__(message)
        self.partial_result: dict[str, Any] | None = None


def parse_sysbench_mysql_output(stdout: str, stderr: str = "") -> dict[str, Any]:
    combined = f"{stdout}\n{stderr}"

    def integer(pattern: str) -> int | None:
        match = re.search(pattern, combined, re.IGNORECASE | re.DOTALL)
        return int(match.group(1)) if match else None

    def number(pattern: str) -> float | None:
        match = re.search(pattern, combined, re.IGNORECASE | re.DOTALL)
        return float(match.group(1)) if match else None

    transactions = integer(r"transactions:\s*(\d+)\s*\([\d.]+\s+per sec\.\)")
    transactions_per_second = number(r"transactions:\s*\d+\s*\(([\d.]+)\s+per sec\.\)")
    queries = integer(r"queries:\s*(\d+)\s*\([\d.]+\s+per sec\.\)")
    queries_per_second = number(r"queries:\s*\d+\s*\(([\d.]+)\s+per sec\.\)")
    errors = integer(r"ignored errors:\s*(\d+)\s*\([\d.]+\s+per sec\.\)")
    reconnects = integer(r"reconnects:\s*(\d+)\s*\([\d.]+\s+per sec\.\)")
    elapsed_seconds = number(r"total time:\s*([\d.]+)s")
    latency_minimum = number(r"Latency\s*\(ms\):.*?min:\s*([\d.]+)")
    latency_average = number(r"Latency\s*\(ms\):.*?avg:\s*([\d.]+)")
    latency_maximum = number(r"Latency\s*\(ms\):.*?max:\s*([\d.]+)")
    percentile_match = re.search(
        r"Latency\s*\(ms\):.*?([\d.]+)(?:th|st|nd|rd) percentile:\s*([\d.]+)",
        combined,
        re.IGNORECASE | re.DOTALL,
    )
    if None in {
        transactions,
        transactions_per_second,
        queries,
        queries_per_second,
        elapsed_seconds,
        latency_minimum,
        latency_average,
        latency_maximum,
    } or percentile_match is None:
        raise MySQLBenchmarkError("Sysbench MySQL output did not contain the required OLTP summary.")
    percentile = float(percentile_match.group(1))
    if percentile != 99.0:
        raise MySQLBenchmarkError("Sysbench MySQL output did not contain the fixed P99 latency contract.")
    progress = [
        {
            "elapsed_seconds": int(match.group(1)),
            "threads": int(match.group(2)),
            "transactions_per_second": float(match.group(3)),
            "queries_per_second": float(match.group(4)),
            "latency_p99_ms": float(match.group(5)),
            "errors_per_second": float(match.group(6)),
            "reconnects_per_second": float(match.group(7)),
        }
        for match in re.finditer(
            r"\[\s*(\d+)s\s*\]\s*thds:\s*(\d+)\s+tps:\s*([\d.]+)\s+qps:\s*([\d.]+).*?"
            r"lat\s*\(ms,99%\):\s*([\d.]+)\s+err/s:\s*([\d.]+)\s+reconn/s:\s*([\d.]+)",
            combined,
            re.IGNORECASE,
        )
    ]
    return {
        "transactions": transactions,
        "transactions_per_second": transactions_per_second,
        "queries": queries,
        "queries_per_second": queries_per_second,
        "ignored_errors": int(errors or 0),
        "reconnects": int(reconnects or 0),
        "elapsed_seconds": elapsed_seconds,
        "latency_ms": {
            "minimum": latency_minimum,
            "average": latency_average,
            "p99": float(percentile_match.group(2)),
            "maximum": latency_maximum,
        },
        "progress": progress,
    }


def mysql_total_steps(profile_name: str) -> int:
    return len(DATABASE_PROFILES[profile_name]["jobs"]) + 4


def mysql_default_timeout(profile_name: str) -> int:
    profile = DATABASE_PROFILES[profile_name]
    job_seconds = sum(int(job["duration"]) + int(job.get("warmup", 0)) + 45 for job in profile["jobs"])
    return 360 + job_seconds


def validate_mysql_run(
    database: Database,
    session_id: str,
    profile_name: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    profile = DATABASE_PROFILES.get(profile_name)
    if not profile or profile.get("engine") != "mysql":
        raise ValueError(f"Unknown MySQL/MariaDB profile: {profile_name}")
    return validate_pair(
        database,
        session_id,
        target_capabilities=("mysql_server", "mysql_client", "mysql_admin", "mysql_initializer"),
        generator_capabilities=("sysbench_mysql", "procfs_process_cpu"),
    )


def mysql_analysis(result: dict[str, Any]) -> dict[str, Any]:
    measurements = [item for item in result.get("mysql_measurements") or [] if isinstance(item, dict)]
    cpu = [item.get("generator_cpu") or {} for item in measurements]
    observed = [item for item in cpu if item.get("status") == "observed"]
    peaks = [
        float(item["peak_process_cpu_percent_of_one_core"])
        for item in observed
        if isinstance(item.get("peak_process_cpu_percent_of_one_core"), (int, float))
    ]
    if not measurements or len(observed) != len(measurements) or not peaks:
        generator_status = "unknown"
    elif max(peaks) >= MYSQL_GENERATOR_CPU_LIMIT_PERCENT:
        generator_status = "constrained"
    else:
        generator_status = "adequate"
    durability = (result.get("server") or {}).get("durability") or {}
    durability_observed = (
        durability.get("status") == "observed-runtime"
        and durability.get("innodb_flush_log_at_trx_commit") == 1
        and durability.get("innodb_doublewrite") is True
        and durability.get("binary_log") == "disabled"
    )
    prepared = (result.get("preparation") or {}).get("status") == "complete"
    client_cleanup = (result.get("client_cleanup") or {}).get("cleanup_verified") is True
    service_cleanup = (result.get("cleanup") or {}).get("cleanup_verified") is True
    reason_codes: list[str] = []
    if generator_status != "adequate":
        reason_codes.append(f"generator-headroom-{generator_status}")
    if not durability_observed:
        reason_codes.append("innodb-durability-evidence-incomplete")
    if not prepared:
        reason_codes.append("sysbench-preparation-evidence-incomplete")
    if not client_cleanup:
        reason_codes.append("sysbench-client-cleanup-unverified")
    if not service_cleanup:
        reason_codes.append("ephemeral-cleanup-unverified")
    return {
        "generator_headroom": {
            "status": generator_status,
            "peak_process_cpu_percent_of_one_core": max(peaks, default=None),
            "observed_measurements": len(observed),
            "required_measurements": len(measurements),
            "limit_percent_of_one_core": MYSQL_GENERATOR_CPU_LIMIT_PERCENT,
        },
        "durability": {
            "status": "observed" if durability_observed else "unavailable",
            "innodb_flush_log_at_trx_commit": durability.get("innodb_flush_log_at_trx_commit"),
            "innodb_doublewrite": durability.get("innodb_doublewrite"),
            "binary_log": durability.get("binary_log", "disabled"),
        },
        "validity": {"comparison_eligible": not reason_codes, "reason_codes": reason_codes},
        "scored": False,
    }


def run_mysql(
    database: Database,
    run_id: str,
    session_id: str,
    profile_name: str,
    *,
    context: JobContext,
) -> dict[str, Any]:
    session, target, generator = validate_mysql_run(database, session_id, profile_name)
    profile = DATABASE_PROFILES[profile_name]
    target_address = peer_address(target)
    generator_address = peer_address(generator)
    password = secrets.token_urlsafe(32)
    total_steps = mysql_total_steps(profile_name)
    secret = {"mysql_password": password}
    result: dict[str, Any] = {
        "suite": "database",
        "engine": "mysql",
        "profile": profile_name,
        "profile_version": profile["profile_version"],
        "methodology_version": profile["methodology_version"],
        "session": {
            "id": session["id"],
            "label": session["label"],
            "topology": session.get("topology") or {"scope": "undeclared", "source": "unavailable"},
        },
        "target": {"id": target["id"], "name": target["name"], "address": target_address},
        "generator": {"id": generator["id"], "name": generator["name"], "address": generator_address},
        "policy": {
            "controller_in_data_path": False,
            "ephemeral_data_directory": True,
            "authentication": "memory-only-per-run-secret",
            "arbitrary_sql_allowed": False,
            "fixed_sysbench_scripts": True,
            "port": MYSQL_PORT,
        },
        "preparation": {"status": "pending"},
        "mysql_measurements": [],
        "client_cleanup": {"status": "pending"},
        "cleanup": {"status": "pending"},
    }
    server_task: str | None = None
    prepared = False
    client_cleanup_scheduled = False
    service_cleanup_scheduled = False
    try:
        context.report("preparing-mysql", f"Initialize isolated MySQL/MariaDB on {target['name']}")
        server_task = create_task(
            database,
            run_id,
            session_id,
            target["id"],
            "mysql-service-start",
            {
                "listen_address": target_address,
                "allowed_client_address": generator_address,
                "port": MYSQL_PORT,
                "max_connections": max(int(job["threads"]) for job in profile["jobs"]) + 16,
                "estimated_dataset_bytes": int(profile["estimated_dataset_bytes"]),
                "deadline_seconds": mysql_default_timeout(profile_name),
                "methodology_version": profile["methodology_version"],
                "ephemeral_secret_required": True,
                "run_completed_steps": 0,
                "run_total_steps": total_steps,
            },
            ephemeral_secret=secret,
        )
        result["server"] = (
            wait_task(database, server_task, timeout_seconds=240, context=context).get("result") or {}
        )
        context.complete_step("mysql-ready", None, partial_result=result)

        preparation_task = create_task(
            database,
            run_id,
            session_id,
            generator["id"],
            "mysql-client",
            {
                "action": "prepare",
                "target_address": target_address,
                "port": MYSQL_PORT,
                "tables": int(profile["tables"]),
                "table_size": int(profile["table_size"]),
                "ephemeral_secret_required": True,
                "run_completed_steps": 1,
                "run_total_steps": total_steps,
            },
            ephemeral_secret=secret,
        )
        result["preparation"] = (
            wait_task(database, preparation_task, timeout_seconds=300, context=context).get("result") or {}
        )
        prepared = result["preparation"].get("status") == "complete"
        if not prepared:
            raise MySQLBenchmarkError("Sysbench did not confirm dataset preparation.")
        context.complete_step("mysql-dataset-ready", None, partial_result=result)

        for job_index, job in enumerate(profile["jobs"]):
            context.report("measuring-mysql", str(job["name"]))
            client_task = create_task(
                database,
                run_id,
                session_id,
                generator["id"],
                "mysql-client",
                {
                    "action": "run",
                    "target_address": target_address,
                    "port": MYSQL_PORT,
                    "tables": int(profile["tables"]),
                    "table_size": int(profile["table_size"]),
                    "workload": job["workload"],
                    "threads": int(job["threads"]),
                    "duration_seconds": int(job["duration"]),
                    "warmup_seconds": int(job.get("warmup", 0)),
                    "ephemeral_secret_required": True,
                    "run_completed_steps": job_index + 2,
                    "run_total_steps": total_steps,
                },
                ephemeral_secret=secret,
            )
            completed = wait_task(
                database,
                client_task,
                timeout_seconds=int(job["duration"]) + int(job.get("warmup", 0)) + 45,
                context=context,
            )
            measurement = (completed.get("result") or {}).get("sysbench_mysql")
            if not isinstance(measurement, dict):
                raise MySQLBenchmarkError("Sysbench MySQL client returned invalid evidence.")
            result["mysql_measurements"].append({"name": job["name"], **measurement})
            result["analysis"] = mysql_analysis(result)
            context.complete_step("mysql-measurement-complete", None, partial_result=result)

        client_cleanup_scheduled = True
        cleanup_task = create_task(
            database,
            run_id,
            session_id,
            generator["id"],
            "mysql-client",
            {
                "action": "cleanup",
                "target_address": target_address,
                "port": MYSQL_PORT,
                "tables": int(profile["tables"]),
                "table_size": int(profile["table_size"]),
                "ephemeral_secret_required": True,
                "run_completed_steps": len(profile["jobs"]) + 2,
                "run_total_steps": total_steps,
            },
            ephemeral_secret=secret,
        )
        result["client_cleanup"] = (
            wait_task(database, cleanup_task, timeout_seconds=120, context=None).get("result") or {}
        )
        context.complete_step("mysql-client-cleanup-complete", None, partial_result=result)

        service_cleanup_scheduled = True
        stop_task = create_task(
            database,
            run_id,
            session_id,
            target["id"],
            "mysql-service-stop",
            {"server_task_id": server_task},
        )
        result["cleanup"] = wait_task(database, stop_task, timeout_seconds=60, context=None).get("result") or {}
        result["analysis"] = mysql_analysis(result)
        context.complete_step("mysql-service-cleanup-complete", None, partial_result=result)
    except (RunStopped, DistributedError, MySQLBenchmarkError) as exc:
        if prepared and not client_cleanup_scheduled:
            client_cleanup_scheduled = True
            try:
                cleanup_task = create_task(
                    database,
                    run_id,
                    session_id,
                    generator["id"],
                    "mysql-client",
                    {
                        "action": "cleanup",
                        "target_address": target_address,
                        "port": MYSQL_PORT,
                        "tables": int(profile["tables"]),
                        "table_size": int(profile["table_size"]),
                        "ephemeral_secret_required": True,
                        "run_completed_steps": len(result["mysql_measurements"]) + 2,
                        "run_total_steps": total_steps,
                    },
                    ephemeral_secret=secret,
                )
                result["client_cleanup"] = (
                    wait_task(database, cleanup_task, timeout_seconds=60, context=None).get("result") or {}
                )
            except DistributedError:
                pass
        if server_task and not service_cleanup_scheduled:
            service_cleanup_scheduled = True
            try:
                stop_task = create_task(
                    database,
                    run_id,
                    session_id,
                    target["id"],
                    "mysql-service-stop",
                    {"server_task_id": server_task},
                )
                result["cleanup"] = (
                    wait_task(database, stop_task, timeout_seconds=45, context=None).get("result") or {}
                )
            except DistributedError:
                pass
        result["analysis"] = mysql_analysis(result)
        exc.partial_result = result
        raise
    return result
