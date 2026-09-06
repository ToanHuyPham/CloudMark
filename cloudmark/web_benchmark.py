from __future__ import annotations

import re
import math
from typing import Any

from .database import Database
from .distributed import DistributedError, create_task, peer_address, validate_pair, wait_task
from .profiles import WEB_PROFILES
from .runner import JobContext, RunStopped


WEB_HTTP_PORT = 58080
WEB_HTTPS_PORT = 58443
WEB_APP_PORT = 58081
WEB_ALLOWED_PORTS = {WEB_HTTP_PORT, WEB_HTTPS_PORT}
WEB_ALLOWED_CONCURRENCY = {1, 4, 8, 16, 64}
WEB_ALLOWED_PATHS = {"/health", "/api/v1/record", "/api/v2/dynamic", "/assets/256k.bin"}
WEB_ALLOWED_SCHEMES = {"http", "https"}
WEB_MAX_DURATION = 60
WEB_REQUEST_LIMIT = 100_000_000
WEB_GENERATOR_CPU_LIMIT_PERCENT = 90.0
H2LOAD_LOG_MAX_BYTES = 8 * 1024 * 1024
H2LOAD_LOG_MAX_ROWS = 25_000


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


def parse_h2load_request_log(
    text: str,
    *,
    expected_requests: int,
    truncated: bool = False,
) -> dict[str, Any]:
    latencies_ms: list[float] = []
    failed_statuses = 0
    invalid_rows = 0
    lines = text.splitlines()
    for line in lines[:H2LOAD_LOG_MAX_ROWS]:
        fields = line.split("\t")
        if len(fields) < 3:
            invalid_rows += 1
            continue
        try:
            float(fields[0])
            status = int(fields[1])
            duration_us = float(fields[2])
        except ValueError:
            invalid_rows += 1
            continue
        if not math.isfinite(duration_us) or duration_us < 0 or duration_us > 3_600_000_000:
            invalid_rows += 1
            continue
        if 200 <= status < 400:
            latencies_ms.append(duration_us / 1000)
        else:
            failed_statuses += 1
    parser_truncated = truncated or len(lines) > H2LOAD_LOG_MAX_ROWS
    parsed_rows = len(latencies_ms) + failed_statuses
    complete = (
        not parser_truncated
        and invalid_rows == 0
        and parsed_rows == expected_requests
        and len(lines) == expected_requests
    )
    result: dict[str, Any] = {
        "status": "complete" if complete else ("partial" if parsed_rows else "unavailable"),
        "source": "h2load-per-request-log",
        "expected_requests": expected_requests,
        "parsed_rows": parsed_rows,
        "successful_latency_samples": len(latencies_ms),
        "failed_status_rows": failed_statuses,
        "invalid_rows": invalid_rows,
        "truncated": parser_truncated,
    }
    if latencies_ms:
        ordered = sorted(latencies_ms)

        def percentile(value: float) -> float:
            index = max(0, math.ceil(value / 100 * len(ordered)) - 1)
            return round(ordered[index], 6)

        result["latency_percentiles_ms"] = {
            "p50": percentile(50),
            "p95": percentile(95),
            "p99": percentile(99),
            "maximum": round(ordered[-1], 6),
        }
    if not complete:
        result["reason"] = "The bounded h2load request log did not exactly match the fixed request contract."
    return result


def parse_h2load_output(
    stdout: str,
    request_log: dict[str, Any],
    *,
    expected_requests: int,
) -> dict[str, Any]:
    finished = re.search(
        r"finished in\s+([\d.]+)(us|ms|s|min|h),\s*([\d.]+)\s*req/s,\s*([\d.]+)(B|KB|MB|GB)/s",
        stdout,
        re.IGNORECASE,
    )
    requests = re.search(
        r"requests:\s*(\d+)\s+total,\s*(\d+)\s+started,\s*(\d+)\s+done,\s*"
        r"(\d+)\s+succeeded,\s*(\d+)\s+failed,\s*(\d+)\s+errored(?:,\s*(\d+)\s+timeout)?",
        stdout,
        re.IGNORECASE,
    )
    statuses = re.search(
        r"status codes:\s*(\d+)\s+2xx,\s*(\d+)\s+3xx,\s*(\d+)\s+4xx,\s*(\d+)\s+5xx",
        stdout,
        re.IGNORECASE,
    )
    traffic = re.search(
        r"traffic:\s*(\d+)\s+bytes total,\s*(\d+)\s+bytes headers.*?,\s*(\d+)\s+bytes data",
        stdout,
        re.IGNORECASE,
    )
    if not finished or not requests or not statuses or not traffic:
        raise WebBenchmarkError("h2load output did not contain the required HTTP/2 summary.")
    duration_value = float(finished.group(1))
    duration_seconds = duration_value * {"us": 0.000001, "ms": 0.001, "s": 1, "min": 60, "h": 3600}[
        finished.group(2).lower()
    ]
    transfer_bps = float(finished.group(4)) * {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3}[
        finished.group(5).lower()
    ]
    total, started, done, succeeded, failed, errored = (int(requests.group(index)) for index in range(1, 7))
    timeout = int(requests.group(7) or 0)
    if total != expected_requests:
        raise WebBenchmarkError("h2load request total did not match the fixed profile contract.")
    return {
        "protocol": "h2",
        "request_total": total,
        "request_started": started,
        "request_done": done,
        "request_succeeded": succeeded,
        "request_failed": failed,
        "request_errored": errored,
        "request_timeout": timeout,
        "success_percent": round(succeeded / total * 100, 6) if total else 0.0,
        "status_codes": {
            "2xx": int(statuses.group(1)),
            "3xx": int(statuses.group(2)),
            "4xx": int(statuses.group(3)),
            "5xx": int(statuses.group(4)),
        },
        "time_taken_seconds": round(duration_seconds, 6),
        "requests_per_second": float(finished.group(3)),
        "transfer_bytes_per_second": round(transfer_bps, 3),
        "traffic": {
            "total_bytes": int(traffic.group(1)),
            "header_bytes": int(traffic.group(2)),
            "data_bytes": int(traffic.group(3)),
        },
        "request_latency": request_log,
    }


def parse_curl_protocol_output(stdout: str) -> dict[str, Any]:
    parts = stdout.strip().split("\t")
    if len(parts) != 6:
        raise WebBenchmarkError("curl did not return the fixed HTTP protocol evidence fields.")
    protocol, response_code, connect, tls, start_transfer, total = parts
    try:
        code = int(response_code)
        timings = {
            "connect_ms": round(float(connect) * 1000, 3),
            "tls_handshake_ms": round(float(tls) * 1000, 3),
            "time_to_first_byte_ms": round(float(start_transfer) * 1000, 3),
            "total_ms": round(float(total) * 1000, 3),
        }
    except ValueError as exc:
        raise WebBenchmarkError("curl returned invalid HTTP protocol timing evidence.") from exc
    normalized_protocol = protocol.strip().lower()
    return {
        "status": "observed",
        "negotiated_protocol": normalized_protocol,
        "http2_negotiated": normalized_protocol in {"2", "2.0"},
        "response_code": code,
        "request_successful": 200 <= code < 300,
        **timings,
        "scope": "single-fixed-protocol-negotiation-request",
        "performance_claim": False,
    }


def _web_analysis(result: dict[str, Any]) -> dict[str, Any]:
    methodology = str(result.get("methodology_version", ""))
    http2_required = methodology == "web-http2-load-v1"
    http1_measurements = [item for item in (result.get("web_measurements") or []) if isinstance(item, dict)]
    http2_measurements = [item for item in (result.get("http2_measurements") or []) if isinstance(item, dict)]
    measurements = http2_measurements if http2_required else http1_measurements
    cpu_evidence = [
        item.get("generator_cpu") or {}
        for item in measurements
    ]
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
    process_capacity_peaks = [
        peak / max(1, int(measurement.get("threads", 1)))
        for measurement, peak in zip(measurements, process_peaks)
    ] if http2_required and len(process_peaks) == len(measurements) else process_peaks
    if (
        not measurements
        or len(observed_cpu) != len(measurements)
        or len(process_peaks) != len(measurements)
        or (http2_required and len(host_peaks) != len(measurements))
    ):
        generator_status = "unknown"
        generator_reasons = ["generator-cpu-evidence-incomplete"]
    elif max(process_capacity_peaks) >= WEB_GENERATOR_CPU_LIMIT_PERCENT or (
        http2_required and host_peaks and max(host_peaks) >= WEB_GENERATOR_CPU_LIMIT_PERCENT
    ):
        generator_status = "constrained"
        generator_reasons = [
            "h2load-declared-thread-or-host-cpu-at-or-above-limit"
            if http2_required
            else "apachebench-process-cpu-at-or-above-limit"
        ]
    else:
        generator_status = "adequate"
        generator_reasons = []

    curve_groups: dict[tuple[str, str, bool], list[dict[str, Any]]] = {}
    for item in http1_measurements:
        key = (str(item.get("scheme", "")), str(item.get("path", "")), item.get("keep_alive") is True)
        curve_groups.setdefault(key, []).append(item)
    concurrency_curves: list[dict[str, Any]] = []
    for (scheme, path, keep_alive), values in sorted(curve_groups.items()):
        points = sorted(
            (
                {
                    "concurrency": int(item.get("concurrency", 0)),
                    "requests_per_second": float((item.get("metrics") or {}).get("requests_per_second", 0)),
                }
                for item in values
                if isinstance(item.get("concurrency"), int)
                and isinstance((item.get("metrics") or {}).get("requests_per_second"), (int, float))
            ),
            key=lambda item: item["concurrency"],
        )
        if not points:
            continue
        first = points[0]
        last = points[-1]
        gain = (
            round((last["requests_per_second"] / first["requests_per_second"] - 1) * 100, 6)
            if len(points) > 1 and first["requests_per_second"] > 0
            else None
        )
        concurrency_curves.append({
            "scheme": scheme,
            "path": path,
            "keep_alive": keep_alive,
            "points": points,
            "lowest_to_highest_gain_percent": gain,
        })

    protocol_observations = [
        item for item in (result.get("protocol_observations") or []) if isinstance(item, dict)
    ]
    http2_observed = any(
        item.get("status") == "observed"
        and item.get("http2_negotiated") is True
        and item.get("request_successful") is True
        for item in protocol_observations
    )
    dynamic_measurements = [item for item in measurements if item.get("path") == "/api/v2/dynamic"]
    application = ((result.get("server") or {}).get("application") or {})
    reverse_proxy_observed = (
        application.get("status") == "observed"
        and application.get("reverse_proxy") is True
        and bool(dynamic_measurements)
        and all(float((item.get("metrics") or {}).get("success_percent", 0)) > 0 for item in dynamic_measurements)
    )
    cleanup_verified = (result.get("cleanup") or {}).get("cleanup_verified") is True
    v2_required = methodology == "web-http-v2"
    http2_load_complete = bool(http2_measurements) and all(
        (item.get("metrics") or {}).get("protocol") == "h2"
        and int((item.get("metrics") or {}).get("request_failed", -1)) == 0
        and int((item.get("metrics") or {}).get("request_errored", -1)) == 0
        and ((item.get("metrics") or {}).get("request_latency") or {}).get("status") == "complete"
        and item.get("client_log_cleanup_verified") is True
        for item in http2_measurements
    )
    reason_codes: list[str] = []
    if v2_required and generator_status != "adequate":
        reason_codes.append(f"generator-headroom-{generator_status}")
    if v2_required and not reverse_proxy_observed:
        reason_codes.append("dynamic-reverse-proxy-evidence-incomplete")
    if v2_required and not http2_observed:
        reason_codes.append("http2-negotiation-evidence-incomplete")
    if http2_required and generator_status != "adequate":
        reason_codes.append(f"generator-headroom-{generator_status}")
    if http2_required and not reverse_proxy_observed:
        reason_codes.append("dynamic-reverse-proxy-evidence-incomplete")
    if http2_required and not http2_load_complete:
        reason_codes.append("http2-load-or-request-log-evidence-incomplete")
    if not cleanup_verified:
        reason_codes.append("ephemeral-cleanup-unverified")
    comparison_eligible = (
        cleanup_verified
        and (
            (not v2_required or (generator_status == "adequate" and reverse_proxy_observed and http2_observed))
            and (
                not http2_required
                or (generator_status == "adequate" and reverse_proxy_observed and http2_load_complete)
            )
        )
    )
    return {
        "generator_headroom": {
            "status": generator_status,
            "peak_process_cpu_percent_of_one_core": max(process_peaks, default=None),
            "peak_process_cpu_percent_of_declared_thread_capacity": max(
                process_capacity_peaks, default=None
            ) if http2_required else None,
            "peak_host_utilization_percent": max(host_peaks, default=None),
            "observed_measurements": len(observed_cpu),
            "required_measurements": len(measurements),
            "limit_percent_of_one_core": WEB_GENERATOR_CPU_LIMIT_PERCENT,
            "basis": (
                "declared-native-thread-capacity-and-host"
                if http2_required
                else "single-process-one-core"
            ),
            "reason_codes": generator_reasons,
        },
        "concurrency_curves": concurrency_curves,
        "protocol_evidence": {
            "status": "observed" if http2_observed else "unavailable",
            "http2_negotiated": http2_observed,
            "performance_claim": False,
            "observations": protocol_observations,
        },
        "dynamic_reverse_proxy": {
            "status": "observed" if reverse_proxy_observed else "unavailable",
            "application_runtime": application.get("runtime"),
            "measurement_count": len(dynamic_measurements),
        },
        "http2_load": {
            "status": "complete" if http2_load_complete else (
                "partial" if http2_measurements else "unavailable"
            ),
            "required": http2_required,
            "measurement_count": len(http2_measurements),
            "request_log_contract": "all-requests-bounded",
        },
        "validity": {
            "generator_headroom_required": v2_required or http2_required,
            "dynamic_reverse_proxy_required": v2_required or http2_required,
            "http2_negotiation_required": v2_required,
            "http2_load_required": http2_required,
            "cleanup_required": True,
            "comparison_eligible": comparison_eligible,
            "reason_codes": reason_codes,
        },
        "scored": False,
    }
def web_total_steps(profile_name: str) -> int:
    profile = WEB_PROFILES[profile_name]
    return len(profile["jobs"]) + len(profile.get("protocol_probes") or []) + 2


def web_default_timeout(profile_name: str) -> int:
    profile = WEB_PROFILES[profile_name]
    jobs = sum(
        int(job.get("duration", 45)) + int(job.get("warmup", 0)) + 45
        for job in profile["jobs"]
    )
    probes = len(profile.get("protocol_probes") or []) * 30
    return 180 + jobs + probes + 90


def validate_web_run(
    database: Database,
    session_id: str,
    profile_name: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if profile_name not in WEB_PROFILES:
        raise ValueError(f"Unknown web profile: {profile_name}")
    profile = WEB_PROFILES[profile_name]
    target_capabilities = ["nginx", "openssl"]
    generator_capabilities = ["ab"]
    if profile["methodology_version"] == "web-http2-load-v1":
        target_capabilities.append("nginx_http2")
        generator_capabilities = ["h2load", "h2load_request_log", "procfs_process_cpu"]
    elif profile["methodology_version"] == "web-http-v2":
        target_capabilities.append("nginx_http2")
        generator_capabilities.extend(["curl_http2", "procfs_process_cpu"])
    return validate_pair(
        database,
        session_id,
        target_capabilities=tuple(target_capabilities),
        generator_capabilities=tuple(generator_capabilities),
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
            "dynamic_reverse_proxy": profile["methodology_version"] in {"web-http-v2", "web-http2-load-v1"},
            "http2_protocol_probe": bool(profile.get("protocol_probes")),
            "http2_performance_measured": profile["methodology_version"] == "web-http2-load-v1",
            "request_limit": WEB_REQUEST_LIMIT,
            "ports": [WEB_HTTP_PORT, WEB_HTTPS_PORT],
        },
        "web_measurements": [],
        "http2_measurements": [],
        "protocol_observations": [],
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
                "methodology_version": profile["methodology_version"],
                "run_completed_steps": 0,
                "run_total_steps": total_steps,
            },
        )
        started = wait_task(database, server_task, timeout_seconds=120, context=context)
        result["server"] = started.get("result") or {}
        context.complete_step("web-service-ready", None, partial_result=result)

        for job_index, job in enumerate(profile["jobs"]):
            context.report("measuring-web", str(job["name"]), partial_result=result)
            if profile["methodology_version"] == "web-http2-load-v1":
                task_kind = "web-http2-client"
                task_payload = {
                    "target_address": target_address,
                    "scheme": "https",
                    "port": int(profile["https_port"]),
                    "path": job["path"],
                    "clients": int(job["clients"]),
                    "threads": int(job["threads"]),
                    "streams": int(job["streams"]),
                    "requests": int(job["requests"]),
                    "methodology_version": profile["methodology_version"],
                    "run_completed_steps": job_index + 1,
                    "run_total_steps": total_steps,
                }
                task_timeout = 120
                result_key = "h2load"
            else:
                task_kind = "web-client"
                task_payload = {
                    "target_address": target_address,
                    "scheme": job["scheme"],
                    "port": int(profile["https_port"] if job["scheme"] == "https" else profile["http_port"]),
                    "path": job["path"],
                    "concurrency": int(job["concurrency"]),
                    "duration_seconds": int(job["duration"]),
                    "warmup_seconds": int(job.get("warmup", 0)),
                    "keep_alive": bool(job["keep_alive"]),
                    "methodology_version": profile["methodology_version"],
                    "run_completed_steps": job_index + 1,
                    "run_total_steps": total_steps,
                }
                task_timeout = int(job["duration"]) + int(job.get("warmup", 0)) + 45
                result_key = "apachebench"
            client_task = create_task(
                database,
                run_id,
                session_id,
                generator["id"],
                task_kind,
                task_payload,
            )
            completed = wait_task(
                database,
                client_task,
                timeout_seconds=task_timeout,
                context=context,
            )
            payload = completed.get("result") or {}
            measurement = payload.get(result_key)
            if not isinstance(measurement, dict):
                raise WebBenchmarkError("Web client returned an invalid result.")
            collection = "http2_measurements" if result_key == "h2load" else "web_measurements"
            result[collection].append({"name": job["name"], **measurement})
            result["analysis"] = _web_analysis(result)
            context.complete_step("web-measurement-complete", None, partial_result=result)

        for probe_index, probe in enumerate(profile.get("protocol_probes") or []):
            context.report("observing-web-protocol", str(probe["name"]), partial_result=result)
            probe_task = create_task(
                database,
                run_id,
                session_id,
                generator["id"],
                "web-protocol-probe",
                {
                    "target_address": target_address,
                    "scheme": probe["scheme"],
                    "port": int(profile["https_port"]),
                    "path": probe["path"],
                    "methodology_version": profile["methodology_version"],
                    "run_completed_steps": len(profile["jobs"]) + probe_index + 1,
                    "run_total_steps": total_steps,
                },
            )
            completed = wait_task(database, probe_task, timeout_seconds=30, context=context)
            payload = completed.get("result") or {}
            observation = payload.get("protocol")
            if not isinstance(observation, dict):
                raise WebBenchmarkError("Web protocol probe returned an invalid result.")
            result["protocol_observations"].append({"name": probe["name"], **observation})
            result["analysis"] = _web_analysis(result)
            context.complete_step("web-protocol-observation-complete", None, partial_result=result)

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
        result["analysis"] = _web_analysis(result)
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
        result["analysis"] = _web_analysis(result)
        exc.partial_result = result
        raise
    return result
