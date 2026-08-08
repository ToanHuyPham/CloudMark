from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .inventory import collect_inventory
from .provider import detect_provider


def join_session(
    controller: str,
    session_id: str,
    join_token: str,
    role: str,
    name: str | None = None,
    *,
    allow_http: bool = False,
) -> dict[str, Any]:
    base = controller.rstrip("/")
    parsed = urlparse(base)
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and parsed.hostname not in local_hosts and not allow_http:
        raise ValueError("Remote controller must use HTTPS. Add --allow-http only inside a trusted private lab network.")
    body = json.dumps(
        {
            "join_token": join_token,
            "role": role,
            "name": name or socket.gethostname(),
            "system": {"inventory": collect_inventory(Path.cwd()), "provider": detect_provider()},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/api/v1/sessions/{session_id}/join",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"Controller rejected the join request ({exc.code}): {detail}") from exc
