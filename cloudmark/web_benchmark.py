from __future__ import annotations

import re
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
    measurements = [item for item in (result.get("web_measurements") or []) if isinstance(item, dict)]
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
    if not measurements or len(observed_cpu) != len(measurements) or not process_peaks:
        generator_status = "unknown"
        generator_reasons = ["generator-cpu-evidence-incomplete"]
    elif max(process_peaks) >= WEB_GENERATOR_CPU_LIMIT_PERCENT:
        generator_status = "constrained"
        generator_reasons = ["apachebench-process-cpu-at-or-above-limit"]
    else:
        generator_status = "adequate"
        generator_reasons = []

    curve_groups: dict[tuple[str, str, bool], list[dict[str, Any]]] = {}
    for item in measurements:
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
    dynamic_measurements = [
        item for item in measurements if item.get("path") == "/api/v2/dynamic"
    ]
    application = ((result.get("server") or {}).get("application") or {})
    reverse_proxy_observed = (
        application.get("status") == "observed"
        and application.get("reverse_proxy") is True
        and bool(dynamic_measurements)
        and all(float((item.get("metrics") or {}).get("success_percent", 0)) > 0 for item in dynamic_measurements)
    )
    cleanup_verified = (result.get("cleanup") or {}).get("cleanup_verified") is True
    methodology = str(result.get("methodology_version", ""))
    v2_required = methodology == "web-http-v2"
    reason_codes: list[str] = []
    if v2_required and generator_status != "adequate":
        reason_codes.append(f"generator-headroom-{generator_status}")
    if v2_required and not reverse_proxy_observed:
        reason_codes.append("dynamic-reverse-proxy-evidence-incomplete")
    if v2_required and not http2_observed:
        reason_codes.append("http2-negotiation-evidence-incomplete")
    if not cleanup_verified:
        reason_codes.append("ephemeral-cleanup-unverified")
    comparison_eligible = (
        cleanup_verified
        and (
            not v2_required
            or (generator_status == "adequate" and reverse_proxy_observed and http2_observed)
        )
    )
    return {
        "generator_headroom": {
            "status": generator_status,
            "peak_process_cpu_percent_of_one_core": max(process_peaks, default=None),
            "peak_host_utilization_percent": max(host_peaks, default=None),
            "observed_measurements": len(observed_cpu),
            "required_measurements": len(measurements),
            "limit_percent_of_one_core": WEB_GENERATOR_CPU_LIMIT_PERCENT,
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
        "validity": {
            "generator_headroom_required": v2_required,
            "dynamic_reverse_proxy_required": v2_required,
            "http2_negotiation_required": v2_required,
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
    jobs = sum(int(job["duration"]) + int(job.get("warmup", 0)) + 45 for job in profile["jobs"])
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
    if profile["methodology_version"] == "web-http-v2":
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
            "dynamic_reverse_proxy": profile["methodology_version"] == "web-http-v2",
            "http2_protocol_probe": bool(profile.get("protocol_probes")),
            "http2_performance_measured": False,
            "request_limit": WEB_REQUEST_LIMIT,
            "ports": [WEB_HTTP_PORT, WEB_HTTPS_PORT],
        },
        "web_measurements": [],
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
                    "methodology_version": profile["methodology_version"],
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
