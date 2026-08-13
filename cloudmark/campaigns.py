from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from .profiles import NETWORK_PROFILES


NETWORK_CAMPAIGN_VERSION = "network-campaign-v1"
NETWORK_CAMPAIGN_PROFILE = "network-peer-standard"
NETWORK_CAMPAIGN_MIN_WINDOWS = 3
NETWORK_CAMPAIGN_MAX_WINDOWS = 30


def _topology_identity(session: dict[str, Any]) -> dict[str, str]:
    topology = session.get("topology") or {}
    verification = topology.get("verification") or {}
    return {
        "scope": str(topology.get("scope") or "undeclared"),
        "source": str(topology.get("source") or "unavailable"),
        "evidence_class": str(verification.get("status") or "unavailable"),
    }


def _participant_identity(agent: dict[str, Any]) -> dict[str, Any]:
    system = agent.get("system") or {}
    provider = system.get("provider") or {}
    inventory = system.get("inventory") or {}
    operating_system = inventory.get("os") or {}
    return {
        "id": str(agent.get("id") or ""),
        "name": str(agent.get("name") or ""),
        "role": str(agent.get("role") or ""),
        "provider": str(provider.get("provider") or provider.get("name") or "Unknown"),
        "instance_type": provider.get("instance_type"),
        "region": provider.get("region"),
        "zone": provider.get("zone"),
        "operating_system": operating_system.get("system"),
        "operating_system_release": operating_system.get("release"),
    }


def build_network_campaign_contract(
    session: dict[str, Any],
    profile_name: str,
    target_windows: int,
) -> dict[str, Any]:
    if profile_name != NETWORK_CAMPAIGN_PROFILE:
        raise ValueError("Network campaigns require the Provider Internal Network standard profile.")
    if not NETWORK_CAMPAIGN_MIN_WINDOWS <= target_windows <= NETWORK_CAMPAIGN_MAX_WINDOWS:
        raise ValueError(
            f"target_windows must be between {NETWORK_CAMPAIGN_MIN_WINDOWS} and {NETWORK_CAMPAIGN_MAX_WINDOWS}."
        )
    participants = [
        _participant_identity(agent)
        for agent in session.get("agents") or []
        if agent.get("role") in {"target", "generator"}
    ]
    roles = {participant["role"] for participant in participants}
    if roles != {"target", "generator"} or len(participants) != 2:
        raise ValueError("A network campaign requires exactly one target and one generator Agent.")
    profile = NETWORK_PROFILES[profile_name]
    return {
        "version": NETWORK_CAMPAIGN_VERSION,
        "suite": "network",
        "session_id": str(session.get("id") or ""),
        "profile": profile_name,
        "profile_version": str(profile["profile_version"]),
        "methodology_version": str(profile["methodology_version"]),
        "topology": _topology_identity(session),
        "participants": sorted(participants, key=lambda item: item["role"]),
        "window_policy": {
            "dispatch": "manual-confirmation-only",
            "calendar": "UTC",
            "maximum_valid_windows_per_day": 1,
            "target_distinct_utc_days": target_windows,
        },
        "claims": {
            "pair_temporal_sampling": True,
            "provider_rating_enabled": False,
            "minimum_provider_target_count_satisfied": False,
        },
    }


def campaign_contract_matches_session(campaign: dict[str, Any], session: dict[str, Any]) -> bool:
    contract = campaign.get("contract") or {}
    participants = sorted(
        (
            _participant_identity(agent)
            for agent in session.get("agents") or []
            if agent.get("role") in {"target", "generator"}
        ),
        key=lambda item: item["role"],
    )
    contract_participants = sorted(
        (
            dict(item)
            for item in contract.get("participants") or []
        ),
        key=lambda item: str(item.get("role") or ""),
    )
    return (
        str(session.get("id") or "") == str(contract.get("session_id") or "")
        and len(participants) == 2
        and participants == contract_participants
        and _topology_identity(session) == contract.get("topology")
    )


def _valid_window_day(value: Any) -> str | None:
    text = str(value or "")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%d") if parsed.strftime("%Y-%m-%d") == text else None


def _attempt_contract_status(run: dict[str, Any], contract: dict[str, Any]) -> tuple[bool, str]:
    request = run.get("request") or {}
    result = run.get("result") or {}
    if request.get("campaign_contract_version") != contract.get("version"):
        return False, "campaign-contract-version-mismatch"
    if request.get("session_id") != contract.get("session_id") or request.get("profile") != contract.get("profile"):
        return False, "campaign-contract-target-mismatch"
    if run.get("status") != "completed":
        return False, f"run-{run.get('status') or 'unknown'}"
    if result.get("profile_version") != contract.get("profile_version"):
        return False, "profile-version-mismatch"
    if result.get("methodology_version") != contract.get("methodology_version"):
        return False, "methodology-version-mismatch"
    result_session_id = str(((result.get("session") or {}).get("id") or ""))
    if result_session_id and result_session_id != str(contract.get("session_id") or ""):
        return False, "result-session-mismatch"
    if ((result.get("analysis") or {}).get("validity") or {}).get("comparison_eligible") is not True:
        return False, "comparison-validity-incomplete"
    if _valid_window_day(request.get("campaign_window_day")) is None:
        return False, "invalid-utc-window"
    return True, "valid-window"


def project_network_campaign(
    campaign: dict[str, Any],
    runs: list[dict[str, Any]],
    *,
    session: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = current.strftime("%Y-%m-%d")
    contract = campaign.get("contract") or {}
    attempts = [
        run
        for run in runs
        if str((run.get("request") or {}).get("campaign_id") or "") == str(campaign.get("id") or "")
    ]
    attempts.sort(key=lambda item: str(item.get("started_at") or item.get("finished_at") or item.get("id") or ""))
    attempt_summaries: list[dict[str, Any]] = []
    valid_days: dict[str, str] = {}
    active_run_id: str | None = None
    failed_attempts = 0
    for run in attempts:
        valid, reason = _attempt_contract_status(run, contract)
        request = run.get("request") or {}
        window_day = _valid_window_day(request.get("campaign_window_day"))
        if valid and window_day is not None:
            valid_days.setdefault(window_day, str(run.get("id") or ""))
        if run.get("status") in {"queued", "running"}:
            active_run_id = str(run.get("id") or "")
        elif run.get("status") in {"failed", "cancelled"}:
            failed_attempts += 1
        attempt_summaries.append({
            "run_id": str(run.get("id") or ""),
            "status": str(run.get("status") or "unknown"),
            "window_day": window_day,
            "attempt_number": request.get("campaign_attempt_number"),
            "window_number": request.get("campaign_window_number"),
            "valid_window": valid,
            "reason_code": reason,
        })
    target_windows = int(campaign.get("target_windows") or NETWORK_CAMPAIGN_MIN_WINDOWS)
    valid_window_count = len(valid_days)
    stored_status = str(campaign.get("status") or "active")
    effective_status = "completed" if valid_window_count >= target_windows else stored_status
    session_matches = campaign_contract_matches_session(campaign, session) if session else False
    if effective_status == "completed":
        eligible = False
        reason_code = "campaign-complete"
        earliest_at = None
    elif stored_status != "active":
        eligible = False
        reason_code = "campaign-not-active"
        earliest_at = None
    elif active_run_id:
        eligible = False
        reason_code = "campaign-run-active"
        earliest_at = None
    elif today in valid_days:
        eligible = False
        reason_code = "utc-window-already-complete"
        tomorrow = datetime(current.year, current.month, current.day, tzinfo=timezone.utc) + timedelta(days=1)
        earliest_at = tomorrow.isoformat()
    elif session is None:
        eligible = False
        reason_code = "campaign-session-unavailable"
        earliest_at = None
    elif not session_matches:
        eligible = False
        reason_code = "campaign-session-contract-mismatch"
        earliest_at = None
    elif session.get("status") != "ready":
        eligible = False
        reason_code = "campaign-agents-not-ready"
        earliest_at = None
    else:
        eligible = True
        reason_code = "ready-for-manual-dispatch"
        earliest_at = None
    result = deepcopy(campaign)
    result.update({
        "status": effective_status,
        "contract_version": contract.get("version"),
        "profile": contract.get("profile"),
        "profile_version": contract.get("profile_version"),
        "methodology_version": contract.get("methodology_version"),
        "session_id": contract.get("session_id"),
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
        "attempts": attempt_summaries,
        "evidence_status": "complete" if effective_status == "completed" else "partial",
        "claim": "Temporal evidence for one fixed Agent pair; this is not a provider-wide rating.",
    })
    return result
