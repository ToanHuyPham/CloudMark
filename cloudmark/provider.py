from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


METADATA_MAX_BYTES = 64 * 1024
METADATA_TEXT_MAX_CHARS = 160
AWS_TOKEN_MAX_CHARS = 4096


def _bounded_text(value: Any, maximum: int = METADATA_TEXT_MAX_CHARS) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        return None
    return normalized


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
            payload = response.read(METADATA_MAX_BYTES + 1)
            if len(payload) > METADATA_MAX_BYTES:
                return None
            return payload, dict(response.headers.items())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def _aws() -> dict[str, Any] | None:
    token_response = _request(
        "http://169.254.169.254/latest/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
    )
    if not token_response:
        return None
    try:
        token = token_response[0].decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    if (
        not 1 <= len(token) <= AWS_TOKEN_MAX_CHARS
        or any(ord(character) < 33 or ord(character) > 126 for character in token)
    ):
        return None
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
    if not isinstance(value, dict):
        return None
    region = _bounded_text(value.get("region"))
    zone = _bounded_text(value.get("availabilityZone"))
    instance_type = _bounded_text(value.get("instanceType"))
    if not region or not zone or not instance_type:
        return None
    return {
        "provider": "AWS",
        "confidence": 0.99,
        "source": "IMDSv2",
        "region": region,
        "zone": zone,
        "instance_type": instance_type,
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
        document = json.loads(result[0])
    except json.JSONDecodeError:
        return None
    if not isinstance(document, dict) or not isinstance(document.get("compute"), dict):
        return None
    value = document["compute"]
    region = _bounded_text(value.get("location"))
    zone = _bounded_text(value.get("zone"))
    instance_type = _bounded_text(value.get("vmSize"))
    if not region or not instance_type:
        return None
    return {
        "provider": "Microsoft Azure",
        "confidence": 0.99,
        "source": "Azure IMDS",
        "region": region,
        "zone": zone,
        "instance_type": instance_type,
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
    if not isinstance(value, dict):
        return None
    zone_path = _bounded_text(value.get("zone"), 512)
    machine_type_path = _bounded_text(value.get("machineType"), 512)
    if not zone_path or not machine_type_path:
        return None
    zone = _bounded_text(zone_path.rsplit("/", 1)[-1])
    machine_type = _bounded_text(machine_type_path.rsplit("/", 1)[-1])
    if not zone or not machine_type:
        return None
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


def _provider_manifest_candidates() -> list[Path]:
    candidates: list[Path] = []
    if os.environ.get("CLOUDMARK_PROVIDER_MANIFEST"):
        candidates.append(Path(os.environ["CLOUDMARK_PROVIDER_MANIFEST"]))
    if os.name == "nt":
        program_data = os.environ.get("PROGRAMDATA")
        if program_data:
            candidates.append(Path(program_data) / "CloudMark" / "provider.json")
    else:
        candidates.append(Path("/etc/cloudmark/provider.json"))
    return candidates


def _declared_manifest() -> dict[str, Any] | None:
    for path in _provider_manifest_candidates():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict):
            continue
        provider = _bounded_text(value.get("provider"))
        if not provider:
            continue
        return {
            "provider": provider,
            "operator": _bounded_text(value.get("operator")),
            "confidence": 0.70,
            "source": "Declared provider manifest (unverified)",
            "region": _bounded_text(value.get("region")),
            "zone": _bounded_text(value.get("zone")),
            "instance_type": _bounded_text(value.get("instance_type")),
            "cloud_stack": _bounded_text(value.get("cloud_stack")),
            "evidence": ["Local declared provider manifest"],
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
