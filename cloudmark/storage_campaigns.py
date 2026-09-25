from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from .profiles import STORAGE_PROFILES
from .suitability import storage_run_contract


STORAGE_CAMPAIGN_VERSION = "storage-campaign-v1"
STORAGE_CAMPAIGN_MIN_WINDOWS = 3
STORAGE_CAMPAIGN_MAX_WINDOWS = 30


def _text(value: Any, fallback: str = "unavailable") -> str:
    normalized = " ".join(str(value or "").split())
    return normalized[:240] if normalized else fallback


def storage_target_id(run: dict[str, Any]) -> str:
    request = run.get("request") if isinstance(run.get("request"), dict) else {}
    return str(request.get("agent_id") or "controller")


def storage_target_identity(target_id: str, system: dict[str, Any]) -> dict[str, Any]:
    inventory = system.get("inventory") if isinstance(system.get("inventory"), dict) else {}
    provider = system.get("provider") if isinstance(system.get("provider"), dict) else {}
    operating_system = inventory.get("os") if isinstance(inventory.get("os"), dict) else {}
    return {
        "id": target_id,
        "hostname": _text(inventory.get("hostname")),
        "provider": _text(provider.get("provider") or provider.get("name"), "Unknown"),
        "provider_source": _text(provider.get("source")),
        "instance_type": _text(provider.get("instance_type")),
        "region": _text(provider.get("region")),
        "zone": _text(provider.get("zone")),
        "operating_system": _text(operating_system.get("system")),
        "operating_system_release": _text(operating_system.get("release")),
        "architecture": _text(operating_system.get("architecture")),
    }


def _utc_timestamp(value: Any) -> datetime | None:
    try:
        observed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if observed.tzinfo is None:
        return None
    return observed.astimezone(timezone.utc)


def _utc_day(value: Any) -> str | None:
    observed = _utc_timestamp(value)
    return observed.strftime("%Y-%m-%d") if observed else None


def _declared_utc_day(value: Any) -> str | None:
    text = str(value or "")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%d") if parsed.strftime("%Y-%m-%d") == text else None


def _finite_number(value: Any, *, positive: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = float(value)
    return math.isfinite(number) and (number > 0 if positive else number >= 0)


def _latency_contract(value: Any, names: tuple[str, ...]) -> bool:
    if not isinstance(value, dict):
        return False
    samples = [value.get(name) for name in names]
    return all(_finite_number(sample) for sample in samples) and all(
        float(left) <= float(right) for left, right in zip(samples, samples[1:])
    )


def _time_series_contract(value: Any, directions: tuple[str, ...]) -> bool:
    if not isinstance(value, dict) or value.get("interval_ms") != 1000:
        return False
    for name in ("bandwidth", "iops", "latency"):
        points = value.get(name)
        if not isinstance(points, list) or not points:
            return False
        if not all(
            isinstance(point, dict)
            and isinstance(point.get("elapsed_ms"), int)
            and not isinstance(point.get("elapsed_ms"), bool)
            and point["elapsed_ms"] >= 0
            and _finite_number(point.get("value"))
            and point.get("direction") in directions
            for point in points
        ):
            return False
    return True


def _fio_measurement_complete(measurement: dict[str, Any], expected: dict[str, Any]) -> bool:
    if measurement.get("workload") != expected or not _finite_number(measurement.get("runtime_seconds"), positive=True):
        return False
    rw = str(expected.get("rw") or "")
    directions = (
        ("read",)
        if rw in {"read", "randread"}
        else ("write",)
        if rw in {"write", "randwrite"}
        else ("read", "write")
    )
    for direction in directions:
        metrics = measurement.get(direction)
        if not isinstance(metrics, dict):
            return False
        if not all(
            _finite_number(metrics.get(name), positive=True)
            for name in ("io_bytes", "iops", "bandwidth_bytes_per_second")
        ):
            return False
        if not _latency_contract(metrics, ("p50_ms", "p90_ms", "p95_ms", "p99_ms", "p999_ms")):
            return False
    return _time_series_contract(measurement.get("time_series"), directions)


def _filesystem_measurement_complete(
    measurement: dict[str, Any],
    expected: dict[str, Any],
    profile: dict[str, Any],
) -> bool:
    name = str(expected.get("name") or "")
    durable = name == "durable-create-fsync"
    expected_count = int(profile["durable_file_count"] if durable else profile["file_count"])
    expected_bytes = (
        expected_count * int(profile["file_bytes"])
        if name in {"small-file-create", "small-file-read-verify", "durable-create-fsync"}
        else 0
    )
    if (
        measurement.get("operation") != expected.get("operation")
        or measurement.get("operation_count") != expected_count
        or measurement.get("bytes_processed") != expected_bytes
        or not _finite_number(measurement.get("elapsed_seconds"), positive=True)
        or not _finite_number(measurement.get("operations_per_second"), positive=True)
        or not _latency_contract(measurement.get("latency_ms"), ("minimum", "p50", "p95", "p99", "maximum"))
        or not isinstance(measurement.get("cache_scope"), str)
        or not measurement["cache_scope"]
    ):
        return False
    integrity = measurement.get("integrity") if isinstance(measurement.get("integrity"), dict) else {}
    if name in {"small-file-read-verify", "durable-create-fsync"} and not (
        integrity.get("status") == "verified"
        and integrity.get("algorithm") == "SHA-256"
        and integrity.get("verified_files") == expected_count
        and integrity.get("mismatches") == 0
    ):
        return False
    if durable:
        durability = measurement.get("durability") if isinstance(measurement.get("durability"), dict) else {}
        if not (
            durability.get("status") == "per-file-fsync"
            and durability.get("file_fsync_count") == expected_count
            and isinstance(durability.get("directory_fsync"), dict)
            and durability["directory_fsync"].get("status") == "observed"
        ):
            return False
    return True


def _measurement_contract_complete(result: dict[str, Any], profile: dict[str, Any]) -> bool:
    expected = profile.get("jobs") if isinstance(profile.get("jobs"), list) else []
    collection_name = "filesystem_operations" if profile.get("executor") == "native-filesystem" else "jobs"
    measurements = result.get(collection_name)
    if (
        not isinstance(measurements, list)
        or len(measurements) != len(expected)
        or not all(isinstance(item, dict) for item in measurements)
        or [item.get("name") for item in measurements] != [item.get("name") for item in expected]
    ):
        return False
    preflight = result.get("preflight") if isinstance(result.get("preflight"), dict) else {}
    if not (
        preflight.get("profile_version") == profile.get("profile_version")
        and preflight.get("methodology_version") == profile.get("methodology_version")
        and preflight.get("job_count") == len(expected)
        and preflight.get("destructive") is False
        and preflight.get("raw_device") is False
    ):
        return False
    if profile.get("executor") == "native-filesystem":
        measurement_contract = result.get("measurement_contract")
        if not isinstance(measurement_contract, dict) or measurement_contract != {
            "worker_model": "single-process-sequential",
            "payload": f"deterministic-{int(profile['file_bytes'])}-byte-sha256-verified",
            "latency_clock": "python-perf-counter-ns",
            "cache_control": "observed-not-flushed",
            "claim_scope": "guest-filesystem-and-python-runtime",
        }:
            return False
        return all(
            _filesystem_measurement_complete(measurement, job, profile)
            for measurement, job in zip(measurements, expected)
        )
    return all(_fio_measurement_complete(measurement, job) for measurement, job in zip(measurements, expected))


def _validate_storage_result(
    run: dict[str, Any],
    *,
    profile_name: str,
    profile_version: str,
    methodology_version: str,
    target_id: str,
    target_contract: dict[str, Any] | None = None,
    storage_contract: str | None = None,
) -> tuple[bool, str, str | None]:
    if str(run.get("suite") or "") != "storage" or str(run.get("profile") or "") != profile_name:
        return False, "campaign-profile-mismatch", None
    if storage_target_id(run) != target_id:
        return False, "campaign-target-mismatch", None
    if run.get("status") != "completed":
        return False, f"run-{run.get('status') or 'unknown'}", None
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    request = run.get("request") if isinstance(run.get("request"), dict) else {}
    if request.get("suite") != "storage" or request.get("profile") != profile_name:
        return False, "campaign-request-mismatch", None
    if result.get("suite") != "storage" or result.get("profile") != profile_name:
        return False, "campaign-result-mismatch", None
    if str(result.get("profile_version") or "") != profile_version:
        return False, "profile-version-mismatch", None
    if str(result.get("methodology_version") or "") != methodology_version:
        return False, "methodology-version-mismatch", None
    if str(run.get("methodology_version") or "") != methodology_version:
        return False, "run-methodology-version-mismatch", None
    tool = result.get("tool") if isinstance(result.get("tool"), dict) else {}
    if not tool.get("name") or not tool.get("version") or run.get("tool_version") != tool.get("version"):
        return False, "run-tool-version-mismatch", None
    target_evidence = result.get("target_evidence")
    if not isinstance(target_evidence, dict):
        return False, "target-evidence-unavailable", None
    observed_target = storage_target_identity(target_id, target_evidence)
    if target_contract is not None and observed_target != target_contract:
        return False, "target-evidence-mismatch", None
    safety = result.get("safety") if isinstance(result.get("safety"), dict) else {}
    if safety.get("test_file_removed") is not True:
        return False, "cleanup-not-verified", None
    profile = STORAGE_PROFILES.get(profile_name) or {}
    if not _measurement_contract_complete(result, profile):
        return False, "measurement-contract-incomplete", None
    if profile.get("executor") == "native-filesystem":
        if safety.get("workspace_removed") is not True:
            return False, "cleanup-not-verified", None
    elif safety.get("fio_logs_removed") is not True:
        return False, "cleanup-not-verified", None
    observed_contract, contract_verified = storage_run_contract(run)
    if not contract_verified:
        return False, "storage-contract-unverified", None
    if storage_contract is not None and observed_contract != storage_contract:
        return False, "storage-contract-mismatch", None
    started_at = _utc_timestamp(run.get("started_at"))
    finished_at = _utc_timestamp(run.get("finished_at"))
    if finished_at is None:
        return False, "completion-time-unavailable", None
    window_day = finished_at.strftime("%Y-%m-%d")
    if started_at is None:
        return False, "start-time-unavailable", window_day
    if finished_at < started_at:
        return False, "completion-before-start", window_day
    started_day = started_at.strftime("%Y-%m-%d")
    if started_day != window_day:
        return False, "cross-utc-midnight", window_day
    return True, "valid-window", window_day


def build_storage_campaign_contract(
    baseline_run: dict[str, Any],
    target_system: dict[str, Any],
    target_windows: int,
) -> dict[str, Any]:
    if not STORAGE_CAMPAIGN_MIN_WINDOWS <= target_windows <= STORAGE_CAMPAIGN_MAX_WINDOWS:
        raise ValueError(
            f"target_windows must be between {STORAGE_CAMPAIGN_MIN_WINDOWS} and {STORAGE_CAMPAIGN_MAX_WINDOWS}."
        )
    profile_name = str(baseline_run.get("profile") or "")
    profile = STORAGE_PROFILES.get(profile_name)
    if profile is None:
        raise ValueError("The baseline Run uses an unknown storage profile.")
    target_id = storage_target_id(baseline_run)
    baseline_result = baseline_run.get("result") if isinstance(baseline_run.get("result"), dict) else {}
    target_evidence = baseline_result.get("target_evidence")
    if not isinstance(target_evidence, dict):
        raise ValueError("The baseline storage Run is not campaign-eligible: target-evidence-unavailable.")
    target = storage_target_identity(target_id, target_evidence)
    if storage_target_identity(target_id, target_system) != target:
        raise ValueError("The baseline storage Run is not campaign-eligible: target-evidence-mismatch.")
    valid, reason, window_day = _validate_storage_result(
        baseline_run,
        profile_name=profile_name,
        profile_version=str(profile["profile_version"]),
        methodology_version=str(profile["methodology_version"]),
        target_id=target_id,
        target_contract=target,
    )
    if not valid or window_day is None:
        raise ValueError(f"The baseline storage Run is not campaign-eligible: {reason}.")
    storage_contract, contract_verified = storage_run_contract(baseline_run)
    if not contract_verified:
        raise ValueError("The baseline storage environment and executor contract is unavailable.")
    return {
        "version": STORAGE_CAMPAIGN_VERSION,
        "suite": "storage",
        "profile": profile_name,
        "profile_version": str(profile["profile_version"]),
        "methodology_version": str(profile["methodology_version"]),
        "target": target,
        "storage_contract": storage_contract,
        "baseline": {
            "run_id": str(baseline_run.get("id") or ""),
            "window_day": window_day,
        },
        "window_policy": {
            "dispatch": "manual-confirmation-only",
            "calendar": "UTC-completion-day",
            "maximum_valid_windows_per_day": 1,
            "target_distinct_utc_days": target_windows,
        },
        "claims": {
            "single_target_temporal_sampling": True,
            "provider_rating_enabled": False,
            "independent_target_sampling_satisfied": False,
        },
    }


def _attempt_contract_status(run: dict[str, Any], contract: dict[str, Any]) -> tuple[bool, str, str | None]:
    request = run.get("request") if isinstance(run.get("request"), dict) else {}
    if request.get("campaign_contract_version") != contract.get("version"):
        return False, "campaign-contract-version-mismatch", None
    if request.get("confirm_write") is not True or request.get("confirm_campaign_window") is not True:
        return False, "campaign-confirmation-missing", None
    if request.get("campaign_target_id") != (contract.get("target") or {}).get("id"):
        return False, "campaign-target-mismatch", None
    valid, reason, completion_day = _validate_storage_result(
        run,
        profile_name=str(contract.get("profile") or ""),
        profile_version=str(contract.get("profile_version") or ""),
        methodology_version=str(contract.get("methodology_version") or ""),
        target_id=str((contract.get("target") or {}).get("id") or ""),
        target_contract=contract.get("target") if isinstance(contract.get("target"), dict) else {},
        storage_contract=str(contract.get("storage_contract") or ""),
    )
    if not valid:
        return False, reason, completion_day
    declared_day = _declared_utc_day(request.get("campaign_window_day"))
    if declared_day is None:
        return False, "invalid-utc-window", completion_day
    if declared_day != completion_day:
        return False, "completion-window-mismatch", completion_day
    return True, "valid-window", completion_day


def project_storage_campaign(
    campaign: dict[str, Any],
    runs: list[dict[str, Any]],
    *,
    current_target: dict[str, Any] | None,
    target_online: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = current.strftime("%Y-%m-%d")
    contract = campaign.get("contract") if isinstance(campaign.get("contract"), dict) else {}
    campaign_id = str(campaign.get("id") or "")
    baseline_id = str((contract.get("baseline") or {}).get("run_id") or "")
    baseline_run = next((run for run in runs if str(run.get("id") or "") == baseline_id), None)
    baseline_valid = False
    baseline_reason = "baseline-run-unavailable"
    baseline_day: str | None = None
    if baseline_run is not None:
        baseline_valid, baseline_reason, baseline_day = _validate_storage_result(
            baseline_run,
            profile_name=str(contract.get("profile") or ""),
            profile_version=str(contract.get("profile_version") or ""),
            methodology_version=str(contract.get("methodology_version") or ""),
            target_id=str((contract.get("target") or {}).get("id") or ""),
            target_contract=contract.get("target") if isinstance(contract.get("target"), dict) else {},
            storage_contract=str(contract.get("storage_contract") or ""),
        )
        if baseline_day != (contract.get("baseline") or {}).get("window_day"):
            baseline_valid = False
            baseline_reason = "baseline-window-mismatch"
    valid_days: dict[str, str] = {}
    if baseline_valid and baseline_day:
        valid_days[baseline_day] = baseline_id
    attempts = [
        run
        for run in runs
        if str((run.get("request") or {}).get("campaign_id") or "") == campaign_id
    ]
    attempts.sort(key=lambda item: str(item.get("started_at") or item.get("finished_at") or item.get("id") or ""))
    summaries: list[dict[str, Any]] = []
    active_run_id: str | None = None
    failed_attempts = 0
    for run in attempts:
        valid, reason, completion_day = _attempt_contract_status(run, contract)
        counted = bool(valid and completion_day and completion_day not in valid_days)
        if counted and completion_day:
            valid_days[completion_day] = str(run.get("id") or "")
        elif valid and completion_day in valid_days:
            reason = "duplicate-utc-window"
        if run.get("status") in {"queued", "running"}:
            active_run_id = str(run.get("id") or "")
        elif run.get("status") in {"failed", "cancelled"}:
            failed_attempts += 1
        summaries.append({
            "run_id": str(run.get("id") or ""),
            "status": str(run.get("status") or "unknown"),
            "window_day": completion_day or _declared_utc_day((run.get("request") or {}).get("campaign_window_day")),
            "attempt_number": (run.get("request") or {}).get("campaign_attempt_number"),
            "window_number": (run.get("request") or {}).get("campaign_window_number"),
            "valid_window": counted,
            "reason_code": reason,
        })
    target_windows = int(campaign.get("target_windows") or STORAGE_CAMPAIGN_MIN_WINDOWS)
    valid_window_count = len(valid_days)
    installed = STORAGE_PROFILES.get(str(contract.get("profile") or "")) or {}
    profile_current = (
        str(installed.get("profile_version") or "") == str(contract.get("profile_version") or "")
        and str(installed.get("methodology_version") or "") == str(contract.get("methodology_version") or "")
    )
    target_matches = current_target == contract.get("target") if current_target is not None else False
    stored_status = str(campaign.get("status") or "active")
    if not baseline_valid:
        effective_status = "invalid"
    elif valid_window_count >= target_windows:
        effective_status = "completed"
    elif stored_status == "active" and not profile_current:
        effective_status = "superseded"
    else:
        effective_status = stored_status
    if effective_status == "invalid":
        eligible, reason_code, earliest_at = False, baseline_reason, None
    elif effective_status == "completed":
        eligible, reason_code, earliest_at = False, "campaign-complete", None
    elif effective_status == "superseded":
        eligible, reason_code, earliest_at = False, "campaign-profile-contract-superseded", None
    elif stored_status != "active":
        eligible, reason_code, earliest_at = False, "campaign-not-active", None
    elif active_run_id:
        eligible, reason_code, earliest_at = False, "campaign-run-active", None
    elif today in valid_days:
        tomorrow = datetime(current.year, current.month, current.day, tzinfo=timezone.utc) + timedelta(days=1)
        eligible, reason_code, earliest_at = False, "utc-window-already-complete", tomorrow.isoformat()
    elif not target_matches:
        eligible, reason_code, earliest_at = False, "campaign-target-contract-mismatch", None
    elif not target_online:
        eligible, reason_code, earliest_at = False, "campaign-target-offline", None
    else:
        eligible, reason_code, earliest_at = True, "ready-for-manual-dispatch", None
    result = deepcopy(campaign)
    result.update({
        "status": effective_status,
        "contract_version": contract.get("version"),
        "profile": contract.get("profile"),
        "profile_version": contract.get("profile_version"),
        "methodology_version": contract.get("methodology_version"),
        "target_id": (contract.get("target") or {}).get("id"),
        "baseline_run_id": baseline_id,
        "storage_contract": contract.get("storage_contract"),
        "progress": {
            "valid_windows": valid_window_count,
            "target_windows": target_windows,
            "remaining_windows": max(0, target_windows - valid_window_count),
            "distinct_utc_days": sorted(valid_days),
            "valid_run_ids": [valid_days[day] for day in sorted(valid_days)],
            "attempts": len(attempts),
            "failed_attempts": failed_attempts,
            "active_run_id": active_run_id,
        },
        "next_window": {
            "eligible": eligible,
            "reason_code": reason_code,
            "earliest_at": earliest_at,
            "window_number": min(target_windows, valid_window_count + 1),
            "attempt_number": len(attempts) + 1,
            "window_day": today,
        },
        "baseline": {
            "run_id": baseline_id,
            "window_day": baseline_day,
            "valid": baseline_valid,
            "reason_code": baseline_reason,
        },
        "attempts": summaries,
        "evidence_status": "complete" if effective_status == "completed" else "partial",
        "claim": "Temporal evidence for one fixed storage target and exact storage contract; this is not a provider-wide rating.",
    })
    return result
