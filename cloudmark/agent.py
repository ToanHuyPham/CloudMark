from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import __version__
from .benchmarks import run_storage
from .compute import run_system_benchmark
from .inventory import collect_inventory
from .network import ALLOWED_PORT_MAX, ALLOWED_PORT_MIN, ALLOWED_STREAMS, NetworkError, parse_iperf_json
from .profiles import COMPUTE_PROFILES, MEMORY_PROFILES, STORAGE_PROFILES
from .provider import detect_provider
from .remote import REMOTE_METHODOLOGY_VERSION
from .runner import CancellationToken, JobContext, RunCancelled, RunTimedOut


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


class AgentBenchmarkFailure(RuntimeError):
    def __init__(self, message: str, *, status: str, result: dict[str, Any] | None):
        super().__init__(message)
        self.status = status
        self.result = result


class AgentWorker:
    def __init__(
        self,
        controller: str,
        agent_id: str,
        agent_token: str,
        *,
        allow_http: bool = False,
        poll_seconds: float = 1.0,
        workspace: Path = Path(".cloudmark/agent-workspace"),
    ) -> None:
        self.controller = _validate_controller(controller, allow_http)
        self.agent_id = agent_id
        self.agent_token = agent_token
        self.poll_seconds = max(0.25, min(poll_seconds, 10.0))
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.active_servers: dict[str, ActiveServer] = {}
        self.pending_completions: dict[str, dict[str, Any]] = {}

    def _api(self, suffix: str, data: dict[str, Any], *, timeout: float = 45) -> dict[str, Any]:
        return _request_json(
            f"{self.controller}/api/v1/agents/{self.agent_id}/{suffix}",
            data=data,
            agent_token=self.agent_token,
            timeout=timeout,
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

    def _benchmark_evidence(self) -> dict[str, Any]:
        return {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "inventory": collect_inventory(self.workspace),
            "provider": detect_provider(),
        }

    def _run_benchmark(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        kind = str(task.get("kind", ""))
        suite = kind.removeprefix("benchmark-")
        expected_kind = f"benchmark-{suite}"
        if kind != expected_kind or suite not in {"compute", "memory", "storage"}:
            raise ValueError("Agent refused an unsupported benchmark task kind.")
        if payload.get("protocol_version") != REMOTE_METHODOLOGY_VERSION:
            raise ValueError("Agent refused an incompatible remote benchmark protocol version.")
        if payload.get("suite") != suite or payload.get("load_confirmed") is not True:
            raise ValueError("Agent refused a benchmark task without an exact suite and load confirmation.")
        profile_name = str(payload.get("profile", ""))
        profiles = {"compute": COMPUTE_PROFILES, "memory": MEMORY_PROFILES, "storage": STORAGE_PROFILES}[suite]
        if profile_name not in profiles:
            raise ValueError(f"Agent refused unknown {suite} profile: {profile_name}")
        try:
            timeout_seconds = int(payload.get("timeout_seconds", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("Agent benchmark timeout is invalid.") from exc
        if not 30 <= timeout_seconds <= 43_200:
            raise ValueError("Agent benchmark timeout is outside the 30–43200 second safety range.")

        total_steps = len(profiles[profile_name]["jobs"]) + (2 if suite == "storage" else 0)
        token = CancellationToken()
        latest: dict[str, Any] = {
            "progress": 0.0,
            "phase": "preflight",
            "current_job": None,
            "completed_steps": 0,
            "total_steps": total_steps,
            "result": None,
        }
        latest_lock = threading.Lock()
        stopped = threading.Event()
        contact_lock = threading.Lock()
        last_contact = [time.monotonic()]
        task_id = str(task["id"])
        evidence: dict[str, Any] = {}

        def send_progress() -> None:
            with latest_lock:
                update = dict(latest)
            try:
                response = self._api(f"tasks/{task_id}/progress", update, timeout=5)
            except (RuntimeError, urllib.error.URLError, TimeoutError, OSError):
                with contact_lock:
                    disconnected_for = time.monotonic() - last_contact[0]
                if disconnected_for > 20:
                    token.cancel()
                return
            with contact_lock:
                last_contact[0] = time.monotonic()
            if response.get("cancel_requested") or response.get("task_status") != "running":
                token.cancel()

        def monitor_control() -> None:
            while not stopped.wait(1.0):
                send_progress()

        def report(update: dict[str, Any]) -> None:
            with latest_lock:
                latest.clear()
                latest.update(update)
            send_progress()

        monitor = threading.Thread(target=monitor_control, daemon=True, name=f"cloudmark-control-{task_id}")
        monitor.start()
        context = JobContext(
            str(task.get("run_id") or task_id),
            total_steps=total_steps,
            timeout_seconds=timeout_seconds,
            token=token,
            on_progress=report,
        )
        try:
            evidence = self._benchmark_evidence()
            if suite == "storage":
                benchmark = run_storage(profile_name, self.workspace, str(task.get("run_id") or task_id), context=context)
            else:
                benchmark = run_system_benchmark(
                    suite,
                    profile_name,
                    self.workspace,
                    str(task.get("run_id") or task_id),
                    context=context,
                )
            return {
                "benchmark": benchmark,
                "evidence": evidence,
                "protocol_version": REMOTE_METHODOLOGY_VERSION,
                "agent_version": __version__,
            }
        except Exception as exc:
            with latest_lock:
                partial = latest.get("result")
            result = (
                {
                    "benchmark": partial,
                    "evidence": evidence,
                    "protocol_version": REMOTE_METHODOLOGY_VERSION,
                    "agent_version": __version__,
                }
                if isinstance(partial, dict)
                else None
            )
            status = "cancelled" if isinstance(exc, RunCancelled) else "failed"
            if isinstance(exc, RunTimedOut):
                status = "failed"
            raise AgentBenchmarkFailure(str(exc), status=status, result=result) from exc
        finally:
            stopped.set()
            # Progress requests use a five-second transport timeout. Give the
            # monitor enough time to leave cleanly before posting the terminal
            # task result, avoiding a late heartbeat after completion.
            monitor.join(timeout=6)

    def _execute(self, task: dict[str, Any]) -> dict[str, Any]:
        kind = str(task.get("kind", ""))
        payload = task.get("payload") or {}
        if kind == "network-server-start":
            return self._start_server(str(task["id"]), payload)
        if kind == "network-client":
            return self._run_client(payload)
        if kind == "network-server-collect":
            return self._collect_server(payload)
        if kind in {"benchmark-compute", "benchmark-memory", "benchmark-storage"}:
            return self._run_benchmark(task, payload)
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
        except AgentBenchmarkFailure as exc:
            completion = {"status": exc.status, "error": str(exc)}
            if exc.result is not None:
                completion["result"] = exc.result
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
        print("The agent accepts only versioned CloudMark benchmark tasks; arbitrary shell commands are refused.")
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
    workspace: Path = Path(".cloudmark/agent-workspace"),
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
        workspace=workspace,
    ).run_forever()
