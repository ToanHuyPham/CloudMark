from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: float = 0.35,
) -> tuple[bytes, dict[str, str]] | None:
    try:
        request = urllib.request.Request(url, method=method, headers=headers or {})
        with _opener().open(request, timeout=timeout) as response:
            return response.read(64 * 1024), dict(response.headers.items())
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _aws() -> dict[str, Any] | None:
    token_response = _request(
        "http://169.254.169.254/latest/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
    )
    if not token_response:
        return None
    token = token_response[0].decode(errors="replace")
    identity = _request(
        "http://169.254.169.254/latest/dynamic/instance-identity/document",
        headers={"X-aws-ec2-metadata-token": token},
    )
    if not identity:
        return None
    try:
        value = json.loads(identity[0])
    except json.JSONDecodeError:
        return None
    return {
        "provider": "AWS",
        "confidence": 0.99,
        "source": "IMDSv2",
        "region": value.get("region"),
        "zone": value.get("availabilityZone"),
        "instance_type": value.get("instanceType"),
        "evidence": ["AWS IMDSv2 identity document"],
    }


def _azure() -> dict[str, Any] | None:
    result = _request(
        "http://169.254.169.254/metadata/instance?api-version=2025-04-07",
        headers={"Metadata": "true"},
    )
    if not result:
        return None
    try:
        value = json.loads(result[0]).get("compute", {})
    except json.JSONDecodeError:
        return None
    return {
        "provider": "Microsoft Azure",
        "confidence": 0.99,
        "source": "Azure IMDS",
        "region": value.get("location"),
        "zone": value.get("zone"),
        "instance_type": value.get("vmSize"),
        "evidence": ["Azure Instance Metadata Service"],
    }


def _gcp() -> dict[str, Any] | None:
    result = _request(
        "http://metadata.google.internal/computeMetadata/v1/instance/?recursive=true",
        headers={"Metadata-Flavor": "Google"},
    )
    if not result:
        return None
    headers = {key.lower(): value for key, value in result[1].items()}
    if headers.get("metadata-flavor", "").lower() != "google":
        return None
    try:
        value = json.loads(result[0])
    except json.JSONDecodeError:
        return None
    zone = str(value.get("zone", "")).rsplit("/", 1)[-1] or None
    machine_type = str(value.get("machineType", "")).rsplit("/", 1)[-1] or None
    region = zone.rsplit("-", 1)[0] if zone and "-" in zone else None
    return {
        "provider": "Google Cloud",
        "confidence": 0.99,
        "source": "GCE metadata",
        "region": region,
        "zone": zone,
        "instance_type": machine_type,
        "evidence": ["Google Compute Engine metadata flavor"],
    }


def _declared_manifest() -> dict[str, Any] | None:
    candidates = []
    if os.environ.get("CLOUDMARK_PROVIDER_MANIFEST"):
        candidates.append(Path(os.environ["CLOUDMARK_PROVIDER_MANIFEST"]))
    if os.name == "nt":
        program_data = os.environ.get("PROGRAMDATA")
        if program_data:
            candidates.append(Path(program_data) / "CloudMark" / "provider.json")
    else:
        candidates.append(Path("/etc/cloudmark/provider.json"))
    for path in candidates:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        provider = str(value.get("provider", "")).strip()
        if not provider:
            continue
        return {
            "provider": provider,
            "operator": value.get("operator"),
            "confidence": 0.70,
            "source": "Declared provider manifest (unverified)",
            "region": value.get("region"),
            "zone": value.get("zone"),
            "instance_type": value.get("instance_type"),
            "cloud_stack": value.get("cloud_stack"),
            "evidence": [f"Local manifest: {path}"],
        }
    return None


def detect_provider() -> dict[str, Any]:
    for detector in (_aws, _azure, _gcp, _declared_manifest):
        detected = detector()
        if detected:
            return detected
    return {
        "provider": "Unknown",
        "confidence": 0.0,
        "source": "No trusted metadata evidence",
        "region": None,
        "zone": None,
        "instance_type": None,
        "evidence": [],
    }
