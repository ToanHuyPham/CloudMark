from __future__ import annotations

import math
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
DATABASE_TAIL_TRANSACTIONS_PER_CLIENT = 1_000
DATABASE_TAIL_MAX_TOTAL_TRANSACTIONS = 16_000
DATABASE_TAIL_LOG_MAX_BYTES = 8 * 1024 * 1024
DATABASE_TAIL_LOG_MAX_ROWS = 20_000
DATABASE_TAIL_JOB_TIMEOUT_SECONDS = 120
DATABASE_GENERATOR_CPU_LIMIT_PERCENT = 90.0


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


def parse_pgbench_latency_log(
    text: str,
    *,
    expected_transactions: int,
    truncated: bool = False,
) -> dict[str, Any]:
    latencies_ms: list[float] = []
    invalid_rows = 0
    lines = text.splitlines()
    for line in lines[:DATABASE_TAIL_LOG_MAX_ROWS]:
        fields = line.split()
        if len(fields) < 3 or not fields[0].isdigit() or not fields[1].isdigit():
            invalid_rows += 1
            continue
        try:
            latency_us = float(fields[2])
        except ValueError:
            invalid_rows += 1
            continue
        if not math.isfinite(latency_us) or latency_us < 0 or latency_us > 3_600_000_000:
            invalid_rows += 1
            continue
        latencies_ms.append(latency_us / 1000)
    parser_truncated = truncated or len(lines) > DATABASE_TAIL_LOG_MAX_ROWS
    if not latencies_ms:
        return {
            "status": "unavailable",
            "source": "pgbench-per-transaction-log",
            "sample_count": 0,
            "expected_transactions": expected_transactions,
            "invalid_rows": invalid_rows,
            "truncated": parser_truncated,
            "reason": "No bounded pgbench transaction-latency rows were parsed.",
        }
    ordered = sorted(latencies_ms)

    def percentile(value: float) -> float:
        index = max(0, math.ceil(value / 100 * len(ordered)) - 1)
        return round(ordered[index], 6)

    complete = (
        not parser_truncated
        and invalid_rows == 0
        and len(ordered) == expected_transactions
    )
    result: dict[str, Any] = {
        "status": "complete" if complete else "partial",
        "source": "pgbench-per-transaction-log",
        "sampling": "all-transactions-fixed-count",
        "sample_count": len(ordered),
        "expected_transactions": expected_transactions,
        "invalid_rows": invalid_rows,
        "truncated": parser_truncated,
        "latency_percentiles_ms": {
            "p50": percentile(50),
            "p95": percentile(95),
            "p99": percentile(99),
            "p99_9": percentile(99.9),
            "maximum": round(ordered[-1], 6),
        },
    }
    if not complete:
        result["reason"] = "The parsed transaction log did not exactly match the fixed transaction contract."
    return result


def parse_pgbench_row_counts(stdout: str) -> dict[str, int]:
    line = next((value.strip() for value in stdout.splitlines() if value.strip()), "")
    parts = line.split("|")
    if len(parts) != 4 or any(not re.fullmatch(r"\d+", value.strip()) for value in parts):
        raise DatabaseBenchmarkError("PostgreSQL recovery verification returned invalid row counts.")
    values = [int(value.strip()) for value in parts]
    return {
        "accounts": values[0],
        "branches": values[1],
        "tellers": values[2],
        "history": values[3],
    }


def parse_postgres_checkpoint_stats(stdout: str, *, server_version_num: int) -> dict[str, Any]:
    line = next((value.strip() for value in stdout.splitlines() if value.strip()), "")
    parts = [value.strip() for value in line.split("|")]
    if len(parts) != 5:
        raise DatabaseBenchmarkError("PostgreSQL checkpoint statistics returned an invalid field count.")
    try:
        timed = int(parts[0])
        requested = int(parts[1])
        write_time_ms = float(parts[2])
        sync_time_ms = float(parts[3])
        buffers_written = int(parts[4])
    except ValueError as exc:
        raise DatabaseBenchmarkError("PostgreSQL checkpoint statistics returned invalid numeric evidence.") from exc
    values = (timed, requested, write_time_ms, sync_time_ms, buffers_written)
    if any(not math.isfinite(float(value)) or value < 0 for value in values):
        raise DatabaseBenchmarkError("PostgreSQL checkpoint statistics returned out-of-range evidence.")
    return {
        "source_view": "pg_stat_checkpointer" if server_version_num >= 170_000 else "pg_stat_bgwriter",
        "server_version_num": server_version_num,
        "checkpoints_timed": timed,
        "checkpoints_requested": requested,
        "write_time_ms": write_time_ms,
        "sync_time_ms": sync_time_ms,
        "buffers_written": buffers_written,
    }


def postgres_checkpoint_result(baseline: dict[str, Any], post_load: dict[str, Any]) -> dict[str, Any]:
    required = (
        "checkpoints_timed",
        "checkpoints_requested",
        "write_time_ms",
        "sync_time_ms",
        "buffers_written",
    )
    same_contract = (
        baseline.get("status") == "complete"
        and post_load.get("status") == "complete"
        and baseline.get("server_version_num") == post_load.get("server_version_num")
        and baseline.get("source_view") == post_load.get("source_view")
    )
    deltas: dict[str, int | float] = {}
    if same_contract:
        for key in required:
            before = baseline.get(key)
            after = post_load.get(key)
            if not isinstance(before, (int, float)) or not isinstance(after, (int, float)) or after < before:
                same_contract = False
                break
            deltas[key] = after - before
    duration = post_load.get("forced_checkpoint_duration_seconds")
    duration_valid = isinstance(duration, (int, float)) and math.isfinite(float(duration)) and duration >= 0
    requested_checkpoint_observed = deltas.get("checkpoints_requested", 0) >= 1
    complete = same_contract and duration_valid and requested_checkpoint_observed
    reasons: list[str] = []
    if not same_contract:
        reasons.append("checkpoint-counter-contract-incomplete-or-reset")
    if not duration_valid:
        reasons.append("forced-checkpoint-duration-unavailable")
    if same_contract and not requested_checkpoint_observed:
        reasons.append("forced-checkpoint-counter-not-observed")
    return {
        "status": "complete" if complete else "partial",
        "type": "forced-checkpoint-isolation",
        "baseline": baseline,
        "post_load": post_load,
        "deltas": deltas,
        "forced_checkpoint_duration_seconds": duration if duration_valid else None,
        "requested_checkpoint_observed": requested_checkpoint_observed,
        "reason_codes": reasons,
    }


def _database_analysis(result: dict[str, Any]) -> dict[str, Any]:
    measurements = [item for item in (result.get("database_measurements") or []) if isinstance(item, dict)]
    timed_measurements = [
        item for item in measurements if not int(item.get("transactions_per_client") or 0)
    ]
    cpu_evidence = [item.get("generator_cpu") or {} for item in timed_measurements]
    observed_cpu = [item for item in cpu_evidence if item.get("status") == "observed"]
    process_peaks = [
        float(item["peak_process_cpu_percent_of_one_core"])
        for item in observed_cpu
        if isinstance(item.get("peak_process_cpu_percent_of_one_core"), (int, float))
    ]
    host_peaks = [
        float(item["peak_host_utilization_percent"])
        for item in observed_cpu
        if isinstance(item.get("peak_host_utilization_percent"), (int, float))
    ]
    if not timed_measurements or len(observed_cpu) != len(timed_measurements) or not process_peaks:
        generator_status = "unknown"
        generator_reasons = ["generator-cpu-evidence-incomplete"]
    elif max(process_peaks) >= DATABASE_GENERATOR_CPU_LIMIT_PERCENT:
        generator_status = "constrained"
        generator_reasons = ["pgbench-process-cpu-at-or-above-limit"]
    else:
        generator_status = "adequate"
        generator_reasons = []

    tail_measurements = [
        item for item in measurements if int(item.get("transactions_per_client") or 0) > 0
    ]
    complete_tail = [
        item for item in tail_measurements
        if ((item.get("metrics") or {}).get("transaction_latency") or {}).get("status") == "complete"
        and item.get("client_log_cleanup_verified") is True
    ]
    tail_status = "complete" if tail_measurements and len(complete_tail) == len(tail_measurements) else (
        "partial" if tail_measurements else "unavailable"
    )
    cleanup_verified = (result.get("cleanup") or {}).get("cleanup_verified") is True
    methodology = str(result.get("methodology_version", ""))
    v2_required = methodology == "database-postgresql-v2"
    recovery_required = methodology == "database-postgresql-recovery-v1"
    checkpoint_required = methodology == "database-postgresql-checkpoint-v1"
    generator_required = v2_required or checkpoint_required
    recovery = result.get("recovery") or {}
    if recovery.get("status") == "not-requested":
        recovery = {}
    recovery_complete = (
        recovery.get("status") == "complete"
        and recovery.get("verification", {}).get("row_counts_match") is True
        and recovery.get("cleanup_verified") is True
    )
    checkpoint = result.get("checkpoint") or {}
    if checkpoint.get("status") == "not-requested":
        checkpoint = {}
    checkpoint_duration = checkpoint.get("forced_checkpoint_duration_seconds")
    checkpoint_requested_delta = (checkpoint.get("deltas") or {}).get("checkpoints_requested")
    checkpoint_source = (checkpoint.get("post_load") or {}).get("source_view")
    checkpoint_complete = (
        checkpoint.get("status") == "complete"
        and checkpoint.get("requested_checkpoint_observed") is True
        and isinstance(checkpoint_duration, (int, float))
        and math.isfinite(float(checkpoint_duration))
        and checkpoint_duration >= 0
        and isinstance(checkpoint_requested_delta, (int, float))
        and checkpoint_requested_delta >= 1
        and checkpoint_source in {"pg_stat_bgwriter", "pg_stat_checkpointer"}
    )
    reason_codes: list[str] = []
    if generator_required and generator_status != "adequate":
        reason_codes.append(f"generator-headroom-{generator_status}")
    if v2_required and tail_status != "complete":
        reason_codes.append("transaction-tail-latency-evidence-incomplete")
    if recovery_required and not recovery_complete:
        reason_codes.append("logical-backup-restore-evidence-incomplete")
    if checkpoint_required and not checkpoint_complete:
        reason_codes.append("forced-checkpoint-evidence-incomplete")
    if not cleanup_verified:
        reason_codes.append("ephemeral-cleanup-unverified")
    comparison_eligible = cleanup_verified and (
        (not generator_required or generator_status == "adequate")
        and (not v2_required or tail_status == "complete")
        and (not recovery_required or recovery_complete)
        and (not checkpoint_required or checkpoint_complete)
    )
    return {
        "generator_headroom": {
            "status": generator_status,
            "peak_process_cpu_percent_of_one_core": max(process_peaks, default=None),
            "peak_host_utilization_percent": max(host_peaks, default=None),
            "observed_measurements": len(observed_cpu),
            "required_measurements": len(timed_measurements),
            "limit_percent_of_one_core": DATABASE_GENERATOR_CPU_LIMIT_PERCENT,
            "reason_codes": generator_reasons,
        },
        "transaction_tail_latency": {
            "status": tail_status,
            "measurement_count": len(tail_measurements),
            "complete_measurement_count": len(complete_tail),
            "sampling": "all-transactions-fixed-count",
        },
        "logical_recovery": {
            "status": "complete" if recovery_complete else (
                "partial" if recovery else "unavailable"
            ),
            "required": recovery_required,
            "backup_duration_seconds": recovery.get("backup_duration_seconds"),
            "restore_duration_seconds": recovery.get("restore_duration_seconds"),
            "backup_bytes": recovery.get("backup_bytes"),
        },
        "checkpoint_isolation": {
            "status": "complete" if checkpoint_complete else (
                "partial" if checkpoint else "unavailable"
            ),
            "required": checkpoint_required,
            "forced_checkpoint_duration_seconds": checkpoint_duration,
            "deltas": checkpoint.get("deltas") or {},
            "source_view": checkpoint_source,
        },
        "validity": {
            "generator_headroom_required": generator_required,
            "transaction_tail_latency_required": v2_required,
            "cleanup_required": True,
            "logical_recovery_required": recovery_required,
            "checkpoint_isolation_required": checkpoint_required,
            "comparison_eligible": comparison_eligible,
            "reason_codes": reason_codes,
        },
        "scored": False,
    }


def database_total_steps(profile_name: str) -> int:
    profile = DATABASE_PROFILES[profile_name]
    if profile.get("engine") == "redis":
        from .redis_benchmark import redis_total_steps
        return redis_total_steps(profile_name)
    if profile.get("engine") == "mysql":
        from .mysql_benchmark import mysql_total_steps
        return mysql_total_steps(profile_name)
    return (
        len(profile["jobs"])
        + 2
        + (1 if profile.get("recovery_drill") else 0)
        + (2 if profile.get("checkpoint_drill") else 0)
    )


def database_default_timeout(profile_name: str) -> int:
    profile = DATABASE_PROFILES[profile_name]
    if profile.get("engine") == "redis":
        from .redis_benchmark import redis_default_timeout
        return redis_default_timeout(profile_name)
    if profile.get("engine") == "mysql":
        from .mysql_benchmark import mysql_default_timeout
        return mysql_default_timeout(profile_name)
    job_seconds = sum(
        int(job.get("timeout", int(job["duration"]) + 45)) + int(job.get("warmup", 0))
        for job in profile["jobs"]
    )
    recovery_seconds = int((profile.get("recovery_drill") or {}).get("timeout", 0)) + (
        60 if profile.get("recovery_drill") else 0
    )
    checkpoint_seconds = 2 * int((profile.get("checkpoint_drill") or {}).get("timeout", 0))
    return 300 + job_seconds + recovery_seconds + checkpoint_seconds + 120


def validate_database_run(
    database: Database,
    session_id: str,
    profile_name: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if profile_name not in DATABASE_PROFILES:
        raise ValueError(f"Unknown database profile: {profile_name}")
    if DATABASE_PROFILES[profile_name].get("engine") == "redis":
        from .redis_benchmark import validate_redis_run
        return validate_redis_run(database, session_id, profile_name)
    if DATABASE_PROFILES[profile_name].get("engine") == "mysql":
        from .mysql_benchmark import validate_mysql_run
        return validate_mysql_run(database, session_id, profile_name)
    profile = DATABASE_PROFILES[profile_name]
    target_capabilities = ["postgres", "initdb", "pgbench", "pg_isready"]
    generator_capabilities = ["pgbench"]
    if profile["methodology_version"] == "database-postgresql-v2":
        generator_capabilities.extend(["pgbench_latency_log", "procfs_process_cpu"])
    if profile.get("checkpoint_drill"):
        target_capabilities.append("psql")
        generator_capabilities.append("procfs_process_cpu")
    if profile.get("recovery_drill"):
        target_capabilities.extend(["pg_dump", "pg_restore", "createdb", "dropdb", "psql"])
    return validate_pair(
        database,
        session_id,
        target_capabilities=tuple(target_capabilities),
        generator_capabilities=tuple(generator_capabilities),
    )


def run_database(
    database: Database,
    run_id: str,
    session_id: str,
    profile_name: str,
    *,
    context: JobContext,
) -> dict[str, Any]:
    if DATABASE_PROFILES[profile_name].get("engine") == "redis":
        from .redis_benchmark import run_redis
        return run_redis(database, run_id, session_id, profile_name, context=context)
    if DATABASE_PROFILES[profile_name].get("engine") == "mysql":
        from .mysql_benchmark import run_mysql
        return run_mysql(database, run_id, session_id, profile_name, context=context)
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
        "session": {
            "id": session["id"],
            "label": session["label"],
            "topology": session.get("topology") or {"scope": "undeclared", "source": "unavailable"},
        },
        "target": {"id": target["id"], "name": target["name"], "address": target_address},
        "generator": {"id": generator["id"], "name": generator["name"], "address": generator_address},
        "policy": {
            "controller_in_data_path": False,
            "ephemeral_cluster": True,
            "durability_enabled": True,
            "arbitrary_sql_allowed": False,
            "port_range": [DATABASE_PORT_MIN, DATABASE_PORT_MAX],
            "transaction_log_max_bytes": DATABASE_TAIL_LOG_MAX_BYTES,
            "transaction_log_max_rows": DATABASE_TAIL_LOG_MAX_ROWS,
        },
        "database_measurements": [],
        "recovery": {"status": "not-requested" if not profile.get("recovery_drill") else "pending"},
        "checkpoint": {"status": "not-requested" if not profile.get("checkpoint_drill") else "pending"},
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
                "methodology_version": profile["methodology_version"],
                "run_completed_steps": 0,
                "run_total_steps": database_total_steps(profile_name),
            },
        )
        started = wait_task(database, server_task, timeout_seconds=300, context=context)
        result["server"] = started.get("result") or {}
        context.complete_step("database-ready", None, partial_result=result)

        checkpoint_drill = profile.get("checkpoint_drill")
        checkpoint_baseline: dict[str, Any] | None = None
        if checkpoint_drill:
            context.report("preparing-checkpoint-baseline", "Force baseline checkpoint and capture counters")
            baseline_task = create_task(
                database,
                run_id,
                session_id,
                target["id"],
                "database-checkpoint-probe",
                {
                    "server_task_id": server_task,
                    "action": "baseline",
                    "methodology_version": profile["methodology_version"],
                    "run_completed_steps": 1,
                    "run_total_steps": database_total_steps(profile_name),
                },
            )
            checkpoint_baseline = (
                wait_task(
                    database,
                    baseline_task,
                    timeout_seconds=int(checkpoint_drill["timeout"]),
                    context=context,
                ).get("result")
                or {}
            )
            if checkpoint_baseline.get("status") != "complete":
                raise DatabaseBenchmarkError("PostgreSQL checkpoint baseline returned invalid evidence.")
            result["checkpoint"] = {"status": "baseline-complete", "baseline": checkpoint_baseline}
            context.complete_step("checkpoint-baseline-complete", None, partial_result=result)

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
                    "transactions_per_client": int(job.get("transactions_per_client", 0)),
                    "methodology_version": profile["methodology_version"],
                    "run_completed_steps": job_index + (2 if checkpoint_drill else 1),
                    "run_total_steps": database_total_steps(profile_name),
                },
            )
            completed = wait_task(
                database,
                client_task,
                timeout_seconds=int(job.get("timeout", int(job["duration"]) + 45)) + int(job.get("warmup", 0)),
                context=context,
            )
            payload = completed.get("result") or {}
            measurement = payload.get("pgbench")
            if not isinstance(measurement, dict):
                raise DatabaseBenchmarkError("Database client returned an invalid result.")
            result["database_measurements"].append({"name": job["name"], **measurement})
            result["analysis"] = _database_analysis(result)
            context.complete_step("database-measurement-complete", None, partial_result=result)

        if checkpoint_drill and checkpoint_baseline is not None:
            context.report("measuring-checkpoint", "Force post-load checkpoint and capture counters")
            post_task = create_task(
                database,
                run_id,
                session_id,
                target["id"],
                "database-checkpoint-probe",
                {
                    "server_task_id": server_task,
                    "action": "post-load",
                    "methodology_version": profile["methodology_version"],
                    "run_completed_steps": len(profile["jobs"]) + 2,
                    "run_total_steps": database_total_steps(profile_name),
                },
            )
            post_load = (
                wait_task(
                    database,
                    post_task,
                    timeout_seconds=int(checkpoint_drill["timeout"]),
                    context=context,
                ).get("result")
                or {}
            )
            result["checkpoint"] = postgres_checkpoint_result(checkpoint_baseline, post_load)
            result["analysis"] = _database_analysis(result)
            context.complete_step("checkpoint-isolation-complete", None, partial_result=result)

        recovery_drill = profile.get("recovery_drill")
        if recovery_drill:
            context.report("running-database-recovery", "Logical backup, restore, and row verification")
            recovery_task = create_task(
                database,
                run_id,
                session_id,
                target["id"],
                "database-recovery-drill",
                {
                    "server_task_id": server_task,
                    "methodology_version": profile["methodology_version"],
                    "run_completed_steps": len(profile["jobs"]) + 1,
                    "run_total_steps": database_total_steps(profile_name),
                },
            )
            recovered = wait_task(
                database,
                recovery_task,
                timeout_seconds=int(recovery_drill["timeout"]) + 45,
                context=context,
            )
            recovery_result = recovered.get("result") or {}
            if not isinstance(recovery_result, dict):
                raise DatabaseBenchmarkError("Database recovery drill returned an invalid result.")
            result["recovery"] = recovery_result
            result["analysis"] = _database_analysis(result)
            context.complete_step("database-recovery-complete", None, partial_result=result)

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
        result["analysis"] = _database_analysis(result)
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
        result["analysis"] = _database_analysis(result)
        exc.partial_result = result
        raise
    return result
