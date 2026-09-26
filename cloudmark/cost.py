from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse


COST_OBSERVATION_VERSION = "cost-observation-v1"
COST_OBSERVATION_MAX_SOURCE_BYTES = 512
COST_OBSERVATION_MAX_FUTURE_SKEW = timedelta(days=1)
COST_BILLING_UNITS = {"hour", "month", "year", "one-time"}
COST_COMMITMENTS = {"on-demand", "spot", "reserved", "contract", "unknown"}
COST_SOURCE_TYPES = {"provider-public-url", "operator-reference"}
COST_AMOUNT_PATTERN = re.compile(r"^\d{1,12}(?:\.\d{1,6})?$")
COST_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


class CostObservationError(ValueError):
    pass


def _bounded_text(value: Any, label: str, *, maximum_bytes: int = 160) -> str:
    if not isinstance(value, str):
        raise CostObservationError(f"{label} must be a string.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise CostObservationError(f"{label} contains a control character.")
    normalized = " ".join(value.split())
    if not normalized or len(normalized.encode("utf-8")) > maximum_bytes:
        raise CostObservationError(f"{label} is empty or exceeds the bounded length.")
    return normalized


def _price_amount(value: Any) -> str:
    if not isinstance(value, str) or not COST_AMOUNT_PATTERN.fullmatch(value):
        raise CostObservationError("amount must be a positive decimal string with at most six fractional digits.")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise CostObservationError("amount is not a valid decimal value.") from exc
    if amount <= 0:
        raise CostObservationError("amount must be greater than zero.")
    normalized = format(amount.normalize(), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def _observed_at(value: Any, now: datetime) -> str:
    if value is None or value == "":
        return now.astimezone(timezone.utc).isoformat()
    if not isinstance(value, str) or len(value) > 64:
        raise CostObservationError("observed_at must be a bounded timezone-aware ISO 8601 timestamp.")
    try:
        observed = datetime.fromisoformat(f"{value[:-1]}+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise CostObservationError("observed_at must be a timezone-aware ISO 8601 timestamp.") from exc
    if observed.tzinfo is None:
        raise CostObservationError("observed_at must include a timezone offset.")
    observed = observed.astimezone(timezone.utc)
    if observed > now.astimezone(timezone.utc) + COST_OBSERVATION_MAX_FUTURE_SKEW:
        raise CostObservationError("observed_at is too far in the future.")
    return observed.isoformat()


def _source_reference(source_type: str, value: Any) -> str:
    reference = _bounded_text(value, "source_reference", maximum_bytes=COST_OBSERVATION_MAX_SOURCE_BYTES)
    if source_type != "provider-public-url":
        return reference
    if any(character.isspace() for character in reference):
        raise CostObservationError("provider-public-url cannot contain whitespace.")
    try:
        parsed = urlparse(reference)
        hostname = parsed.hostname
    except ValueError as exc:
        raise CostObservationError("provider-public-url is malformed.") from exc
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise CostObservationError(
            "provider-public-url requires an HTTPS URL without credentials or a fragment."
        )
    return reference


def _target_identity(target_id: str, system: dict[str, Any]) -> dict[str, Any]:
    inventory = system.get("inventory") if isinstance(system.get("inventory"), dict) else {}
    provider = system.get("provider") if isinstance(system.get("provider"), dict) else {}
    operating_system = inventory.get("os") if isinstance(inventory.get("os"), dict) else {}
    provider_name = _bounded_text(provider.get("provider") or provider.get("name") or "Unknown", "provider")
    instance_type = _bounded_text(provider.get("instance_type") or "unavailable", "instance_type")
    if provider_name == "Unknown" or instance_type == "unavailable":
        raise CostObservationError("Cost evidence requires a provider and product/SKU identity on the selected target.")
    raw_confidence = provider.get("confidence")
    confidence = (
        float(raw_confidence)
        if isinstance(raw_confidence, (int, float))
        and not isinstance(raw_confidence, bool)
        and math.isfinite(float(raw_confidence))
        and 0 <= float(raw_confidence) <= 1
        else 0.0
    )
    return {
        "id": target_id,
        "hostname": _bounded_text(inventory.get("hostname") or target_id, "hostname"),
        "provider": provider_name,
        "provider_source": _bounded_text(provider.get("source") or "unavailable", "provider_source"),
        "provider_confidence": confidence,
        "instance_type": instance_type,
        "region": _bounded_text(provider.get("region") or "unspecified-region", "region"),
        "zone": _bounded_text(provider.get("zone") or "unspecified-zone", "zone"),
        "operating_system": _bounded_text(
            operating_system.get("distribution") or operating_system.get("system") or "unspecified-os",
            "operating_system",
        ),
        "architecture": _bounded_text(operating_system.get("architecture") or "unspecified-architecture", "architecture"),
    }


def build_cost_observation(
    observation_id: str,
    request: dict[str, Any],
    target_id: str,
    system: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    allowed_fields = {
        "target_id",
        "amount",
        "currency",
        "billing_unit",
        "commitment",
        "tax_included",
        "observed_at",
        "source_type",
        "source_reference",
    }
    unknown_fields = sorted(set(request) - allowed_fields)
    if unknown_fields:
        raise CostObservationError(f"Unknown cost observation fields: {', '.join(unknown_fields)}.")
    required_fields = {
        "amount",
        "currency",
        "billing_unit",
        "commitment",
        "tax_included",
        "source_type",
        "source_reference",
    }
    missing_fields = sorted(required_fields - set(request))
    if missing_fields:
        raise CostObservationError(f"Missing cost observation fields: {', '.join(missing_fields)}.")
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        raise CostObservationError("Controller time must be timezone-aware.")
    currency = str(request.get("currency") or "").strip().upper()
    if not COST_CURRENCY_PATTERN.fullmatch(currency):
        raise CostObservationError("currency must be a three-letter ISO-style code.")
    billing_unit = str(request.get("billing_unit") or "").strip()
    if billing_unit not in COST_BILLING_UNITS:
        raise CostObservationError("billing_unit is outside the fixed cost observation policy.")
    commitment = str(request.get("commitment") or "unknown").strip()
    if commitment not in COST_COMMITMENTS:
        raise CostObservationError("commitment is outside the fixed cost observation policy.")
    source_type = str(request.get("source_type") or "").strip()
    if source_type not in COST_SOURCE_TYPES:
        raise CostObservationError("source_type is outside the fixed cost observation policy.")
    tax_included = request.get("tax_included")
    if tax_included is not None and not isinstance(tax_included, bool):
        raise CostObservationError("tax_included must be true, false, or null.")
    return {
        "id": observation_id,
        "version": COST_OBSERVATION_VERSION,
        "created_at": current_time.astimezone(timezone.utc).isoformat(),
        "observed_at": _observed_at(request.get("observed_at"), current_time),
        "observed_at_source": (
            "controller-receipt-time"
            if request.get("observed_at") is None or request.get("observed_at") == ""
            else "operator-supplied"
        ),
        "target": _target_identity(target_id, system),
        "price": {
            "amount": _price_amount(request.get("amount")),
            "currency": currency,
            "billing_unit": billing_unit,
            "commitment": commitment,
            "tax_included": tax_included,
        },
        "source": {
            "type": source_type,
            "reference": _source_reference(source_type, request.get("source_reference")),
            "fetched_by_cloudmark": False,
            "document_persisted": False,
        },
        "evidence_status": "operator-declared-unverified",
        "claim": "Timestamped operator-supplied price context; not independently verified by CloudMark.",
        "policy": {
            "immutable": True,
            "provider_rating_input": False,
            "price_performance_calculated": False,
            "missing_terms_inferred": False,
        },
    }
