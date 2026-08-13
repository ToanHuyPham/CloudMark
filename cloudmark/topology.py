from __future__ import annotations

import ipaddress
from typing import Any


PAIRING_TOPOLOGY_SCOPES = {
    "undeclared",
    "same-host",
    "same-zone",
    "cross-zone",
    "cross-region",
    "public-internet",
}


def _trusted_provider(agent: dict[str, Any]) -> dict[str, Any] | None:
    provider = agent.get("system", {}).get("provider", {})
    source = str(provider.get("source") or "").lower()
    name = str(provider.get("provider") or "").strip()
    try:
        confidence = float(provider.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if not name or name.lower() == "unknown" or confidence < 0.5:
        return None
    if "unverified" in source or "declared" in source:
        return None
    return provider


def _global_endpoint(agent: dict[str, Any]) -> bool:
    address = str(agent.get("endpoint", {}).get("address") or "").strip()
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


def _independent_observation(agents: list[dict[str, Any]]) -> tuple[str | None, str, list[str]]:
    target = next((agent for agent in agents if agent.get("role") == "target"), None)
    generator = next((agent for agent in agents if agent.get("role") == "generator"), None)
    if not target or not generator:
        return None, "unavailable", ["Both Target and Generator Agents are required before topology can be verified."]

    target_provider = _trusted_provider(target)
    generator_provider = _trusted_provider(generator)
    if not target_provider or not generator_provider:
        if _global_endpoint(target) and _global_endpoint(generator):
            return None, "advertised-endpoint-classification", [
                "Both peer endpoints are globally routable, but address class alone does not prove public-Internet traversal."
            ]
        return None, "unavailable", ["Trusted provider metadata is unavailable on one or both Agents."]
    if str(target_provider.get("provider")).casefold() != str(generator_provider.get("provider")).casefold():
        return None, "unavailable", ["Provider metadata identifies different providers; the actual network path is not proven."]

    target_region = str(target_provider.get("region") or "").strip()
    generator_region = str(generator_provider.get("region") or "").strip()
    if not target_region or not generator_region:
        return None, "unavailable", ["Trusted provider metadata does not identify both regions."]
    if target_region.casefold() != generator_region.casefold():
        return "cross-region", "provider-metadata", ["Trusted metadata identifies different regions."]

    target_zone = str(target_provider.get("zone") or "").strip()
    generator_zone = str(generator_provider.get("zone") or "").strip()
    if not target_zone or not generator_zone:
        return None, "unavailable", ["Trusted provider metadata does not identify both zones."]
    if target_zone.casefold() != generator_zone.casefold():
        return "cross-zone", "provider-metadata", ["Trusted metadata identifies one region and different zones."]
    return "same-zone", "provider-metadata", ["Trusted metadata identifies the same provider, region, and zone."]


def assess_pairing_topology(session: dict[str, Any]) -> dict[str, Any]:
    declared = session.get("topology") if isinstance(session.get("topology"), dict) else {}
    scope = str(declared.get("scope") or "undeclared")
    source = str(declared.get("source") or ("unavailable" if scope == "undeclared" else "operator-declared"))
    if scope not in PAIRING_TOPOLOGY_SCOPES:
        scope = "undeclared"
        source = "unavailable"

    agents = session.get("agents") if isinstance(session.get("agents"), list) else []
    observed_scope, observed_source, reasons = _independent_observation(agents)
    if not any(agent.get("role") == "target" for agent in agents) or not any(
        agent.get("role") == "generator" for agent in agents
    ):
        status = "pending"
    elif observed_scope is None:
        status = "unavailable"
    elif scope == "undeclared":
        status = "derived"
    elif scope == "public-internet":
        status = "compatible"
        reasons.append("Placement metadata does not prove whether the benchmark path traverses the public Internet.")
    elif scope == observed_scope:
        status = "confirmed"
    elif scope == "same-host" and observed_scope == "same-zone":
        status = "compatible"
        reasons.append("Provider metadata cannot distinguish same-host from same-zone placement.")
    else:
        status = "contradicted"
        reasons.append(f"The declared {scope} scope conflicts with independently observed {observed_scope} evidence.")

    return {
        "scope": scope,
        "source": source,
        "verification": {
            "status": status,
            "observed_scope": observed_scope,
            "source": observed_source,
            "reasons": reasons,
        },
    }


def enrich_pairing_session(session: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(session)
    enriched["topology"] = assess_pairing_topology(session)
    return enriched
