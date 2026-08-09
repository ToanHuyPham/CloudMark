from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .inventory import collect_inventory
from .network import ALLOWED_PORT_MAX, ALLOWED_PORT_MIN, ALLOWED_STREAMS, NetworkError, parse_iperf_json
from .provider import detect_provider


def _validate_controller(controller: str, allow_http: bool) -> str:
    base = controller.rstrip("/")
    parsed = urlparse(base)
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Controller must be a valid HTTP or HTTPS base URL.")
    if parsed.scheme != "https" and parsed.hostname not in local_hosts and not allow_http:
        raise ValueError("Remote controller must use HTTPS. Add --allow-http only on a trusted, isolated private network.")
    return base


def _default_address(inventory: dict[str, Any]) -> str | None:
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for item in inventory.get("network", {}).get("addresses", []):
        try:
            address = ipaddress.ip_address(str(item.get("address", "")))
        except ValueError:
            continue
        if address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local:
            continue
        candidates.append(address)
    private = next((item for item in candidates if item.is_private), None)
    return str(private or (candidates[0] if candidates else "")) or None


def _request_json(
    url: str,
    *,
    data: dict[str, Any] | None = None,
    agent_token: str | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if agent_token:
        headers["X-CloudMark-Agent-Token"] = agent_token
    request = urllib.request.Request(
        url,
        data=json.dumps(data or {}).encode("utf-8") if data is not None else None,
        method="POST" if data is not None else "GET",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"Controller request failed ({exc.code}): {detail}") from exc


def join_session(
    controller: str,
    session_id: str,
    join_token: str,
    role: str,
    name: str | None = None,
    *,
    advertise_address: str | None = None,
    allow_http: bool = False,
) -> dict[str, Any]:
    base = _validate_controller(controller, allow_http)
    inventory = collect_inventory(Path.cwd())
    address = advertise_address or _default_address(inventory)
    if address:
        try:
            parsed_address = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError("--advertise-address must be an IPv4 or IPv6 address.") from exc
        if parsed_address.is_loopback or parsed_address.is_unspecified or parsed_address.is_multicast or parsed_address.is_link_local:
            raise ValueError("--advertise-address must be a peer-reachable unicast address.")
    body = {
        "join_token": join_token,
        "role": role,
        "name": name or socket.gethostname(),
        "endpoint": {"address": address} if address else {},
        "system": {"inventory": inventory, "provider": detect_provider()},
    }
    return _request_json(f"{base}/api/v1/sessions/{session_id}/join", data=body)


@dataclass
class ActiveServer:
    process: subprocess.Popen[str]
    deadline: float


class AgentWorker:
    def __init__(
        self,
        controller: str,
        agent_id: str,
        agent_token: str,
        *,
        allow_http: bool = False,
        poll_seconds: float = 1.0,
    ) -> None:
        self.controller = _validate_controller(controller, allow_http)
        self.agent_id = agent_id
        self.agent_token = agent_token
        self.poll_seconds = max(0.25, min(poll_seconds, 10.0))
        self.active_servers: dict[str, ActiveServer] = {}
        self.pending_completions: dict[str, dict[str, Any]] = {}

    def _api(self, suffix: str, data: dict[str, Any]) -> dict[str, Any]:
        return _request_json(
            f"{self.controller}/api/v1/agents/{self.agent_id}/{suffix}",
            data=data,
            agent_token=self.agent_token,
            timeout=45,
        )

    @staticmethod
    def _iperf() -> str:
        executable = shutil.which("iperf3")
        if not executable:
            raise NetworkError("iperf3 is not installed or is not on PATH. Run CloudMark bootstrap with the network pack.")
        return executable

    @staticmethod
    def _port(value: Any) -> int:
        port = int(value)
        if not ALLOWED_PORT_MIN <= port <= ALLOWED_PORT_MAX:
            raise NetworkError("Task port is outside the CloudMark allow-list.")
        return port

    def _start_server(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        port = self._port(payload.get("port"))
        deadline_seconds = max(15, min(int(payload.get("deadline_seconds", 60)), 120))
        family = str(payload.get("address_family", "ipv4"))
        if family not in {"ipv4", "ipv6"}:
            raise NetworkError("Task address family is outside the CloudMark allow-list.")
        process = subprocess.Popen(
            [
                self._iperf(),
                "--server",
                "--one-off",
                "--json",
                "--version6" if family == "ipv6" else "--version4",
                "--port",
                str(port),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        time.sleep(0.15)
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise NetworkError(stderr.strip() or stdout.strip() or "iperf3 server exited before becoming ready.")
        self.active_servers[task_id] = ActiveServer(process, time.monotonic() + deadline_seconds)
        return {"ready": True, "port": port, "pid": process.pid}

    def _run_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        address = str(payload.get("target_address", ""))
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise NetworkError("Task target_address is not an IP address.") from exc
        if parsed.is_loopback or parsed.is_unspecified or parsed.is_multicast or parsed.is_link_local:
            raise NetworkError("Task target_address is not a peer-reachable unicast address.")
        port = self._port(payload.get("port"))
        duration = max(1, min(int(payload.get("duration_seconds", 10)), 60))
        streams = int(payload.get("streams", 1))
        if streams not in ALLOWED_STREAMS:
            raise NetworkError("Task stream count is outside the CloudMark allow-list.")
        command = [
            self._iperf(),
            "--client",
            address,
            "--version6" if parsed.version == 6 else "--version4",
            "--port",
            str(port),
            "--time",
            str(duration),
            "--parallel",
            str(streams),
            "--json",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=duration + 20,
                check=False,
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired as exc:
            raise NetworkError("iperf3 client exceeded its guarded task timeout.") from exc
        if result.returncode != 0:
            raise NetworkError(result.stderr.strip() or result.stdout.strip() or "iperf3 client failed.")
        return {"iperf": parse_iperf_json(result.stdout), "command": {"duration_seconds": duration, "streams": streams}}

    def _collect_server(self, payload: dict[str, Any]) -> dict[str, Any]:
        server_task_id = str(payload.get("server_task_id", ""))
        active = self.active_servers.pop(server_task_id, None)
        if not active:
            raise NetworkError("The requested iperf3 server is not active on this agent.")
        timeout = max(1, min(int(payload.get("timeout_seconds", 15)), 30))
        try:
            stdout, stderr = active.process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            active.process.kill()
            active.process.communicate()
            raise NetworkError("iperf3 server did not finish after the peer measurement.") from exc
        if active.process.returncode != 0:
            raise NetworkError(stderr.strip() or stdout.strip() or "iperf3 server failed.")
        return {"iperf": parse_iperf_json(stdout)}

    def _execute(self, task: dict[str, Any]) -> dict[str, Any]:
        kind = str(task.get("kind", ""))
        payload = task.get("payload") or {}
        if kind == "network-server-start":
            return self._start_server(str(task["id"]), payload)
        if kind == "network-client":
            return self._run_client(payload)
        if kind == "network-server-collect":
            return self._collect_server(payload)
        raise NetworkError(f"Agent refused unsupported task kind: {kind}")

    def _cleanup_expired(self) -> None:
        now = time.monotonic()
        for task_id, active in list(self.active_servers.items()):
            if now < active.deadline:
                continue
            if active.process.poll() is None:
                active.process.kill()
            active.process.communicate()
            self.active_servers.pop(task_id, None)

    def once(self) -> bool:
        self._cleanup_expired()
        if self.pending_completions:
            task_id = next(iter(self.pending_completions))
            self._api(f"tasks/{task_id}/result", self.pending_completions[task_id])
            self.pending_completions.pop(task_id, None)
            return True
        self._api("heartbeat", {})
        response = self._api("tasks/next", {})
        task = response.get("task")
        if not task:
            return False
        try:
            result = self._execute(task)
            completion = {"status": "completed", "result": result}
        except (NetworkError, OSError, ValueError) as exc:
            completion = {"status": "failed", "error": str(exc)}
        task_id = str(task["id"])
        self.pending_completions[task_id] = completion
        self._api(f"tasks/{task_id}/result", completion)
        self.pending_completions.pop(task_id, None)
        return True

    def run_forever(self) -> None:
        fingerprint = hashlib.sha256(self.agent_token.encode()).hexdigest()[:10]
        print(f"CloudMark agent {self.agent_id} connected (credential {fingerprint}).")
        print("Network data flows only between paired agents; the controller is not a traffic endpoint.")
        try:
            while True:
                try:
                    handled = self.once()
                except (RuntimeError, urllib.error.URLError, TimeoutError) as exc:
                    print(f"[agent] controller unavailable: {exc}")
                    handled = False
                if not handled:
                    time.sleep(self.poll_seconds)
        except KeyboardInterrupt:
            pass
        finally:
            for active in self.active_servers.values():
                if active.process.poll() is None:
                    active.process.kill()
                active.process.communicate()
            self.active_servers.clear()


def join_and_work(
    controller: str,
    session_id: str,
    join_token: str,
    role: str,
    name: str | None = None,
    *,
    advertise_address: str | None = None,
    allow_http: bool = False,
) -> None:
    joined = join_session(
        controller,
        session_id,
        join_token,
        role,
        name,
        advertise_address=advertise_address,
        allow_http=allow_http,
    )
    AgentWorker(
        controller,
        str(joined["agent_id"]),
        str(joined["agent_token"]),
        allow_http=allow_http,
    ).run_forever()
