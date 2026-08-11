from __future__ import annotations

import re
from typing import Any

from .database import Database
from .distributed import DistributedError, create_task, peer_address, validate_pair, wait_task
from .profiles import WEB_PROFILES
from .runner import JobContext, RunStopped


WEB_HTTP_PORT = 58080
WEB_HTTPS_PORT = 58443
WEB_ALLOWED_PORTS = {WEB_HTTP_PORT, WEB_HTTPS_PORT}
WEB_ALLOWED_CONCURRENCY = {1, 4, 8, 16, 64}
WEB_ALLOWED_PATHS = {"/health", "/api/v1/record", "/assets/256k.bin"}
WEB_ALLOWED_SCHEMES = {"http", "https"}
WEB_MAX_DURATION = 60
WEB_REQUEST_LIMIT = 100_000_000


class WebBenchmarkError(RuntimeError):
    def __init__(self, message: str):
        super().__init__(message)
        self.partial_result: dict[str, Any] | None = None


def parse_ab_output(stdout: str, stderr: str = "") -> dict[str, Any]:
    combined = f"{stdout}\n{stderr}"

    def number(label: str) -> float | None:
        match = re.search(rf"^{re.escape(label)}:\s*([\d.]+)", combined, re.IGNORECASE | re.MULTILINE)
        return float(match.group(1)) if match else None

    def text(label: str) -> str | None:
        match = re.search(rf"^{re.escape(label)}:\s*(.+?)\s*$", combined, re.IGNORECASE | re.MULTILINE)
        return match.group(1).strip() if match else None

    complete = number("Complete requests")
    failed = number("Failed requests")
    elapsed = number("Time taken for tests")
    requests_per_second = number("Requests per second")
    transfer_rate_kib = number("Transfer rate")
    latency_match = re.search(
        r"^Time per request:\s*([\d.]+)\s*\[ms\]\s*\(mean\)\s*$",
        combined,
        re.IGNORECASE | re.MULTILINE,
    )
    percentiles = {
        int(match.group(1)): float(match.group(2))
        for match in re.finditer(r"^\s*(50|66|75|80|90|95|98|99|100)%\s+([\d.]+)", combined, re.MULTILINE)
    }
    if (
        complete is None
        or failed is None
        or elapsed is None
        or requests_per_second is None
        or latency_match is None
        or not all(value in percentiles for value in (50, 90, 95, 99, 100))
    ):
        raise WebBenchmarkError("ApacheBench output did not contain the required request and latency summary.")

    connection_times: dict[str, dict[str, float]] = {}
    for match in re.finditer(
        r"^(Connect|Processing|Waiting|Total):\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$",
        combined,
        re.IGNORECASE | re.MULTILINE,
    ):
        connection_times[match.group(1).lower()] = {
            "min_ms": float(match.group(2)),
            "mean_ms": float(match.group(3)),
            "stddev_ms": float(match.group(4)),
            "median_ms": float(match.group(5)),
            "max_ms": float(match.group(6)),
        }

    failed_breakdown_match = re.search(
        r"\(Connect:\s*(\d+),\s*Receive:\s*(\d+),\s*Length:\s*(\d+),\s*Exceptions:\s*(\d+)\)",
        combined,
        re.IGNORECASE,
    )
    failed_count = int(failed)
    non_2xx = int(number("Non-2xx responses") or 0)
    successful = max(0, int(complete) - failed_count - non_2xx)
    tls_raw = text("SSL/TLS Protocol")
    tls_parts = [item.strip() for item in tls_raw.split(",")] if tls_raw else []
    return {
        "complete_requests": int(complete),
        "failed_requests": failed_count,
        "non_2xx_responses": non_2xx,
        "successful_requests": successful,
        "success_percent": round(successful / complete * 100, 6) if complete else 0.0,
        "time_taken_seconds": elapsed,
        "requests_per_second": requests_per_second,
        "time_per_request_ms": float(latency_match.group(1)),
        "transfer_rate_kib_per_second": transfer_rate_kib,
        "document_length_bytes": int(number("Document Length") or 0),
        "total_transferred_bytes": int(number("Total transferred") or 0),
        "body_transferred_bytes": int(number("HTML transferred") or 0),
        "keep_alive_requests": int(number("Keep-Alive requests") or 0),
        "latency_percentiles_ms": {
            "p50": percentiles[50],
            "p90": percentiles[90],
            "p95": percentiles[95],
            "p99": percentiles[99],
            "p100": percentiles[100],
        },
        "connection_times": connection_times,
        "failure_breakdown": (
            {
                "connect": int(failed_breakdown_match.group(1)),
                "receive": int(failed_breakdown_match.group(2)),
                "length": int(failed_breakdown_match.group(3)),
                "exceptions": int(failed_breakdown_match.group(4)),
            }
            if failed_breakdown_match
            else None
        ),
        "server_software": text("Server Software"),
        "tls": {
            "status": "measured" if tls_raw else "not-applicable",
            "protocol": tls_parts[0] if tls_parts else None,
            "cipher": tls_parts[1] if len(tls_parts) > 1 else None,
            "raw": tls_raw,
        },
    }


def web_total_steps(profile_name: str) -> int:
    return len(WEB_PROFILES[profile_name]["jobs"]) + 2


def web_default_timeout(profile_name: str) -> int:
    profile = WEB_PROFILES[profile_name]
    jobs = sum(int(job["duration"]) + int(job.get("warmup", 0)) + 45 for job in profile["jobs"])
    return 180 + jobs + 90


def validate_web_run(
    database: Database,
    session_id: str,
    profile_name: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if profile_name not in WEB_PROFILES:
        raise ValueError(f"Unknown web profile: {profile_name}")
    return validate_pair(
        database,
        session_id,
        target_capabilities=("nginx", "openssl"),
        generator_capabilities=("ab",),
    )


def run_web(
    database: Database,
    run_id: str,
    session_id: str,
    profile_name: str,
    *,
    context: JobContext,
) -> dict[str, Any]:
    session, target, generator = validate_web_run(database, session_id, profile_name)
    profile = WEB_PROFILES[profile_name]
    target_address = peer_address(target)
    generator_address = peer_address(generator)
    total_steps = web_total_steps(profile_name)
    result: dict[str, Any] = {
        "suite": "web",
        "engine": "nginx",
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
            "ephemeral_service": True,
            "arbitrary_url_allowed": False,
            "tls_certificate": "ephemeral-self-signed",
            "tls_protocol": "TLSv1.2",
            "request_limit": WEB_REQUEST_LIMIT,
            "ports": [WEB_HTTP_PORT, WEB_HTTPS_PORT],
        },
        "web_measurements": [],
        "cleanup": {"status": "pending"},
    }
    server_task: str | None = None
    cleanup_scheduled = False
    try:
        context.report("preparing-web-service", f"Start isolated Nginx service on {target['name']}")
        server_task = create_task(
            database,
            run_id,
            session_id,
            target["id"],
            "web-service-start",
            {
                "listen_address": target_address,
                "allowed_client_address": generator_address,
                "http_port": int(profile["http_port"]),
                "https_port": int(profile["https_port"]),
                "deadline_seconds": web_default_timeout(profile_name),
                "run_completed_steps": 0,
                "run_total_steps": total_steps,
            },
        )
        started = wait_task(database, server_task, timeout_seconds=120, context=context)
        result["server"] = started.get("result") or {}
        context.complete_step("web-service-ready", None, partial_result=result)

        for job_index, job in enumerate(profile["jobs"]):
            context.report("measuring-web", str(job["name"]), partial_result=result)
            client_task = create_task(
                database,
                run_id,
                session_id,
                generator["id"],
                "web-client",
                {
                    "target_address": target_address,
                    "scheme": job["scheme"],
                    "port": int(profile["https_port"] if job["scheme"] == "https" else profile["http_port"]),
                    "path": job["path"],
                    "concurrency": int(job["concurrency"]),
                    "duration_seconds": int(job["duration"]),
                    "warmup_seconds": int(job.get("warmup", 0)),
                    "keep_alive": bool(job["keep_alive"]),
                    "run_completed_steps": job_index + 1,
                    "run_total_steps": total_steps,
                },
            )
            completed = wait_task(
                database,
                client_task,
                timeout_seconds=int(job["duration"]) + int(job.get("warmup", 0)) + 45,
                context=context,
            )
            payload = completed.get("result") or {}
            measurement = payload.get("apachebench")
            if not isinstance(measurement, dict):
                raise WebBenchmarkError("Web client returned an invalid result.")
            result["web_measurements"].append({"name": job["name"], **measurement})
            context.complete_step("web-measurement-complete", None, partial_result=result)

        context.report("cleaning-web-service", f"Remove isolated Nginx service on {target['name']}")
        cleanup_task = create_task(
            database,
            run_id,
            session_id,
            target["id"],
            "web-service-stop",
            {"server_task_id": server_task},
        )
        cleanup_scheduled = True
        cleaned = wait_task(database, cleanup_task, timeout_seconds=45, context=None)
        result["cleanup"] = cleaned.get("result") or {"status": "completed"}
        context.complete_step("web-cleanup-complete", None, partial_result=result)
    except (RunStopped, DistributedError, WebBenchmarkError) as exc:
        if server_task and not cleanup_scheduled:
            cleanup_task = create_task(
                database,
                run_id,
                session_id,
                target["id"],
                "web-service-stop",
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
