from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import __version__
from .benchmarks import BenchmarkError, run_storage, storage_preflight
from .compute import ComputeError, run_system_benchmark, system_preflight
from .database import Database
from .inventory import collect_inventory
from .network import (
    NetworkError,
    network_default_timeout,
    network_total_steps,
    run_network,
    validate_network_run,
)
from .profiles import COMPUTE_PROFILES, MEMORY_PROFILES, NETWORK_PROFILES, STORAGE_PROFILES, all_profiles
from .provider import detect_provider
from .remote import RemoteError, remote_default_timeout, remote_total_steps, run_remote_benchmark, validate_remote_agent
from .runner import RUNNER_VERSION, CancellationToken, JobContext, RunCancelled, RunTimedOut


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


class CloudMarkController:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database = Database(self.data_dir / "cloudmark.sqlite3")
        self.database.recover_incomplete_runs()
        self.benchmark_dir = self.data_dir / "benchmark-workspace"
        self.token_path = self.data_dir / "controller.token"
        if self.token_path.exists():
            self.token = self.token_path.read_text(encoding="utf-8").strip()
        else:
            self.token = secrets.token_urlsafe(32)
            self.token_path.write_text(self.token, encoding="utf-8")
        try:
            os.chmod(self.token_path, 0o600)
        except OSError:
            pass
        self._inventory: dict[str, Any] | None = None
        self._provider: dict[str, Any] | None = None
        self._active_runs: dict[str, CancellationToken] = {}
        self._active_runs_lock = threading.RLock()
        self._submission_lock = threading.Lock()

    def system(self, refresh: bool = False) -> dict[str, Any]:
        if refresh or self._inventory is None:
            self._inventory = collect_inventory(self.data_dir)
        if refresh or self._provider is None:
            self._provider = detect_provider()
        return {"inventory": self._inventory, "provider": self._provider}

    def dashboard(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "system": self.system(),
            "runs": self.database.list_runs(10),
            "sessions": self.database.list_sessions(10),
            "profiles": all_profiles(),
            "policy": {
                "cloud_to_controller_network_test": False,
                "provider_internal_peer_test": True,
                "raw_device_test": False,
            },
        }

    def submit_run(self, request: dict[str, Any]) -> dict[str, Any]:
        with self._submission_lock:
            return self._submit_run_locked(request)

    def _submit_run_locked(self, request: dict[str, Any]) -> dict[str, Any]:
        request = dict(request)
        suite = str(request.get("suite", ""))
        profile = str(request.get("profile", ""))
        agent_id = str(request.get("agent_id", "")).strip()
        if suite not in {"inventory", "compute", "memory", "storage", "network"}:
            raise ValueError("Supported suites are inventory, compute, memory, storage, and network.")
        if agent_id and suite not in {"compute", "memory", "storage"}:
            raise ValueError("agent_id is supported only for compute, memory, and storage runs.")
        remote_agent: dict[str, Any] | None = None
        if suite in {"compute", "memory", "storage"}:
            if agent_id:
                remote_agent = validate_remote_agent(self.database, agent_id, suite, profile)
            active_local = next(
                (
                    run
                    for run in self.database.list_runs(200)
                    if run["suite"] in {"compute", "memory", "storage"}
                    and run["status"] in {"queued", "running"}
                    and str(run.get("request", {}).get("agent_id", "")).strip() == agent_id
                ),
                None,
            )
            if active_local:
                raise ValueError(
                    f"Benchmark {active_local['id']} is already {active_local['status']} on the selected target. "
                    "Wait for it to finish or cancel it before starting another saturation load there."
                )
            if remote_agent:
                conflicting_network = next(
                    (
                        run
                        for run in self.database.list_runs(200)
                        if run["suite"] == "network"
                        and run["status"] in {"queued", "running"}
                        and str(run.get("request", {}).get("session_id", "")) == str(remote_agent["session_id"])
                    ),
                    None,
                )
                if conflicting_network:
                    raise ValueError("The selected Agent session already has an active network assessment.")
            request["execution"] = "remote-agent" if remote_agent else "controller-host"
        if suite == "storage":
            if not request.get("confirm_write"):
                raise ValueError("Storage test requires confirm_write=true because it writes a temporary test file.")
            preflight = None if remote_agent else storage_preflight(profile, self.benchmark_dir)
            total_steps = remote_total_steps(suite, profile) if remote_agent else len(STORAGE_PROFILES[profile]["jobs"]) + 2
            methodology_version = str(STORAGE_PROFILES[profile]["methodology_version"])
            tool_version = "fio-agent" if remote_agent else str(preflight["fio_version"])
            default_timeout = remote_default_timeout(suite, profile) if remote_agent else int(preflight["default_timeout_seconds"])
        elif suite in {"compute", "memory"}:
            if not request.get("confirm_load"):
                raise ValueError(f"{suite.title()} test requires confirm_load=true because it intentionally saturates local resources.")
            preflight = None if remote_agent else system_preflight(suite, profile, self.benchmark_dir)
            profiles = COMPUTE_PROFILES if suite == "compute" else MEMORY_PROFILES
            total_steps = remote_total_steps(suite, profile) if remote_agent else len(profiles[profile]["jobs"])
            methodology_version = str(profiles[profile]["methodology_version"])
            tool_version = f"{'sysbench' if suite == 'compute' else 'cloudmark-memory-bench'}-agent" if remote_agent else str(preflight["tool_version"])
            default_timeout = remote_default_timeout(suite, profile) if remote_agent else int(preflight["default_timeout_seconds"])
        elif suite == "network":
            if not request.get("confirm_network_load"):
                raise ValueError("Network test requires confirm_network_load=true because it generates sustained peer traffic.")
            session_id = str(request.get("session_id", ""))
            _, target, generator = validate_network_run(self.database, session_id, profile)
            for network_agent in (target, generator):
                if self.database.has_active_agent_task(str(network_agent["id"])):
                    raise ValueError(f"Agent {network_agent['name']} already has an active task.")
            conflicting_remote = next(
                (
                    run
                    for run in self.database.list_runs(200)
                    if run["suite"] in {"compute", "memory", "storage"}
                    and run["status"] in {"queued", "running"}
                    and (
                        (agent := self.database.get_agent(str(run.get("request", {}).get("agent_id", ""))))
                        and str(agent["session_id"]) == session_id
                    )
                ),
                None,
            )
            if conflicting_remote:
                raise ValueError("A selected Agent in this session already has an active single-system assessment.")
            profile_config = NETWORK_PROFILES[profile]
            total_steps = network_total_steps(profile)
            methodology_version = str(profile_config["methodology_version"])
            tool_version = "iperf3/ping-agent" if methodology_version == "network-v2" else "iperf3-agent"
            default_timeout = network_default_timeout(profile)
        else:
            total_steps = 1
            methodology_version = "inventory-v1"
            tool_version = "native"
            default_timeout = 120
        try:
            timeout_seconds = int(request.get("timeout_seconds", default_timeout))
        except (TypeError, ValueError) as exc:
            raise ValueError("timeout_seconds must be an integer.") from exc
        if not 30 <= timeout_seconds <= 43_200:
            raise ValueError("timeout_seconds must be between 30 and 43200.")
        request["timeout_seconds"] = timeout_seconds
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        self.database.create_run(
            run_id,
            suite,
            profile or "default",
            request,
            total_steps=total_steps,
            runner_version=RUNNER_VERSION,
            methodology_version=methodology_version,
            tool_version=tool_version,
        )
        token = CancellationToken()
        with self._active_runs_lock:
            self._active_runs[run_id] = token
        thread = threading.Thread(
            target=self._execute_run,
            args=(run_id, request, token, total_steps),
            daemon=True,
            name=f"cloudmark-{run_id}",
        )
        thread.start()
        return self.database.get_run(run_id) or {"id": run_id, "status": "queued"}

    def _execute_run(
        self,
        run_id: str,
        request: dict[str, Any],
        token: CancellationToken,
        total_steps: int,
    ) -> None:
        self.database.update_run(run_id, status="running", phase="starting", progress=0)

        def update_progress(update: dict[str, Any]) -> None:
            if self.database.is_cancel_requested(run_id):
                token.cancel()
            self.database.update_run_progress(run_id, **update)

        context = JobContext(
            run_id,
            total_steps=total_steps,
            timeout_seconds=float(request["timeout_seconds"]),
            token=token,
            on_progress=update_progress,
        )
        try:
            if request["suite"] == "inventory":
                context.report("collecting", "system-inventory")
                result = self.system(refresh=True)
                context.complete_step("completed", None, partial_result=result)
            elif request["suite"] == "storage":
                if request.get("execution") == "remote-agent":
                    agent = self.database.get_agent(str(request["agent_id"]))
                    if not agent:
                        raise RemoteError("Selected Agent disappeared before execution.")
                    result = run_remote_benchmark(
                        self.database,
                        run_id,
                        agent,
                        "storage",
                        str(request["profile"]),
                        int(request["timeout_seconds"]),
                        context=context,
                    )
                else:
                    result = run_storage(str(request["profile"]), self.benchmark_dir, run_id, context=context)
            elif request["suite"] in {"compute", "memory"}:
                if request.get("execution") == "remote-agent":
                    agent = self.database.get_agent(str(request["agent_id"]))
                    if not agent:
                        raise RemoteError("Selected Agent disappeared before execution.")
                    result = run_remote_benchmark(
                        self.database,
                        run_id,
                        agent,
                        str(request["suite"]),
                        str(request["profile"]),
                        int(request["timeout_seconds"]),
                        context=context,
                    )
                else:
                    result = run_system_benchmark(
                        str(request["suite"]),
                        str(request["profile"]),
                        self.benchmark_dir,
                        run_id,
                        context=context,
                    )
            else:
                result = run_network(
                    self.database,
                    run_id,
                    str(request["session_id"]),
                    str(request["profile"]),
                    context=context,
                )
            result_tool = result.get("tool") if isinstance(result, dict) else None
            self.database.update_run(
                run_id,
                status="completed",
                result=result,
                phase="completed",
                progress=1,
                tool_version=str(result_tool.get("version")) if isinstance(result_tool, dict) and result_tool.get("version") else None,
            )
        except RunCancelled as exc:
            self.database.update_run(
                run_id,
                status="cancelled",
                result=exc.partial_result,
                error=str(exc),
                phase="cancelled",
            )
        except RunTimedOut as exc:
            self.database.update_run(
                run_id,
                status="failed",
                result=exc.partial_result,
                error=str(exc),
                phase="timed-out",
            )
        except NetworkError as exc:
            self.database.update_run(
                run_id,
                status="failed",
                result=exc.partial_result,
                error=str(exc),
                phase="failed",
            )
        except RemoteError as exc:
            self.database.update_run(
                run_id,
                status="failed",
                result=exc.partial_result,
                error=str(exc),
                phase="failed",
            )
        except (BenchmarkError, ComputeError, OSError, ValueError, json.JSONDecodeError) as exc:
            self.database.update_run(run_id, status="failed", error=str(exc), phase="failed")
        except Exception as exc:  # defensive runner boundary
            self.database.update_run(
                run_id,
                status="failed",
                error=f"Unexpected runner failure: {exc}",
                phase="failed",
            )
        finally:
            self.database.cancel_queued_run_tasks(run_id)
            with self._active_runs_lock:
                self._active_runs.pop(run_id, None)

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        run = self.database.get_run(run_id)
        if not run:
            raise LookupError("Run not found.")
        if run["status"] not in {"queued", "running"}:
            raise ValueError(f"Run is already {run['status']} and cannot be cancelled.")
        self.database.request_cancel(run_id)
        self.database.cancel_queued_run_tasks(run_id)
        with self._active_runs_lock:
            token = self._active_runs.get(run_id)
        if token:
            token.cancel()
        return self.database.get_run(run_id) or run

    def create_session(self, label: str) -> dict[str, Any]:
        session_id = f"session_{uuid.uuid4().hex[:12]}"
        join_token = secrets.token_urlsafe(24)
        expires = datetime.now(timezone.utc) + timedelta(minutes=30)
        self.database.create_session(
            session_id,
            label or "Provider internal test",
            hashlib.sha256(join_token.encode()).hexdigest(),
            expires.isoformat(),
        )
        return {"id": session_id, "join_token": join_token, "expires_at": expires.isoformat()}

    def join_session(self, session_id: str, request: dict[str, Any]) -> dict[str, Any]:
        session = self.database.get_session(session_id)
        if not session:
            raise PermissionError("Pairing session does not exist.")
        if datetime.now(timezone.utc) >= datetime.fromisoformat(session["expires_at"]):
            raise PermissionError("Pairing session has expired.")
        if len(session["agents"]) >= 8:
            raise PermissionError("Pairing session already has the maximum of 8 agents.")
        token = str(request.get("join_token", ""))
        expected = self.database.get_session_token_hash(session_id)
        if not expected or not secrets.compare_digest(hashlib.sha256(token.encode()).hexdigest(), expected):
            raise PermissionError("Invalid or expired join token.")
        role = str(request.get("role", "peer"))
        if role not in {"target", "generator", "replica", "peer"}:
            raise ValueError("Unsupported agent role.")
        agent_id = f"agent_{uuid.uuid4().hex[:12]}"
        agent_token = secrets.token_urlsafe(32)
        endpoint = request.get("endpoint") or {}
        if not isinstance(endpoint, dict):
            raise ValueError("Agent endpoint must be a JSON object.")
        system = request.get("system") or {}
        if not isinstance(system, dict):
            raise ValueError("Agent system evidence must be a JSON object.")
        self.database.add_agent(
            agent_id,
            session_id,
            str(request.get("name", agent_id)),
            role,
            system,
            hashlib.sha256(agent_token.encode()).hexdigest(),
            endpoint,
        )
        return {
            "agent_id": agent_id,
            "agent_token": agent_token,
            "session": self.database.get_session(session_id),
        }

    def authenticate_agent(self, agent_id: str, token: str) -> bool:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        return self.database.authenticate_agent(agent_id, token_hash)

    def heartbeat_agent(self, agent_id: str, request: dict[str, Any]) -> dict[str, Any]:
        system = request.get("system")
        if system is not None and not isinstance(system, dict):
            raise ValueError("Agent system evidence must be a JSON object.")
        agent = self.database.heartbeat_agent(agent_id, system)
        if not agent:
            raise LookupError("Agent not found.")
        return agent

    def next_agent_task(self, agent_id: str) -> dict[str, Any]:
        self.database.heartbeat_agent(agent_id)
        return {"task": self.database.claim_agent_task(agent_id)}

    def finish_agent_task(self, agent_id: str, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        status = str(request.get("status", ""))
        result = request.get("result")
        if result is not None and not isinstance(result, dict):
            raise ValueError("Agent task result must be a JSON object.")
        if not self.database.finish_agent_task(
            task_id,
            agent_id,
            status=status,
            result=result,
            error=str(request.get("error", "")) or None,
        ):
            raise LookupError("Running task not found for this agent.")
        return self.database.get_agent_task(task_id) or {"id": task_id, "status": status}

    def progress_agent_task(self, agent_id: str, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        task = self.database.get_agent_task(task_id)
        if not task or str(task.get("agent_id")) != agent_id:
            raise LookupError("Agent task not found.")
        self.database.heartbeat_agent(agent_id)
        if task["status"] != "running":
            return {"accepted": False, "task_status": task["status"], "cancel_requested": True}
        result = request.get("result")
        if result is not None and not isinstance(result, dict):
            raise ValueError("Agent task partial result must be a JSON object.")
        try:
            progress = float(request.get("progress", 0))
            completed_steps = int(request.get("completed_steps", 0))
            total_steps = int(request.get("total_steps", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("Agent task progress fields are invalid.") from exc
        phase = str(request.get("phase"))[:80] if request.get("phase") is not None else None
        current_job = str(request.get("current_job"))[:160] if request.get("current_job") is not None else None
        updated = self.database.update_agent_task_progress(
            task_id,
            agent_id,
            progress=progress,
            phase=phase,
            current_job=current_job,
            completed_steps=completed_steps,
            total_steps=total_steps,
            result=result,
        )
        if not updated:
            return {"accepted": False, "task_status": "stopped", "cancel_requested": True}
        run = self.database.get_run(str(updated["run_id"]))
        cancel_requested = not run or bool(run.get("cancel_requested")) or run.get("status") not in {"queued", "running"}
        if run and run.get("status") in {"queued", "running"}:
            self.database.update_run_progress(
                str(updated["run_id"]),
                progress=progress,
                phase=phase,
                current_job=current_job,
                completed_steps=completed_steps,
                total_steps=total_steps,
                result=result,
            )
        return {"accepted": True, "task_status": "running", "cancel_requested": cancel_requested}


class Handler(BaseHTTPRequestHandler):
    server_version = "CloudMark/0.5"

    @property
    def controller(self) -> CloudMarkController:
        return self.server.controller  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[api] {self.address_string()} {format % args}")

    def _origin(self) -> str | None:
        origin = self.headers.get("Origin")
        if not origin:
            return None
        parsed = urlparse(origin)
        try:
            port = parsed.port
        except ValueError:
            return None
        if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"} and port and 3000 <= port <= 3010:
            return origin
        return None

    def _send(self, status: int, value: Any) -> None:
        body = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        origin = self._origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 16 * 1024 * 1024:
            raise ValueError("Request body is too large.")
        if not length:
            return {}
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("JSON request body must be an object.")
        return value

    def _authorized(self) -> bool:
        token = self.headers.get("X-CloudMark-Token", "")
        return secrets.compare_digest(token, self.controller.token)

    def _agent_authorized(self, agent_id: str) -> bool:
        token = self.headers.get("X-CloudMark-Agent-Token", "")
        return bool(token and self.controller.authenticate_agent(agent_id, token))

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        origin = self._origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-CloudMark-Token, X-CloudMark-Agent-Token")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Vary", "Origin")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/api/v1/health":
                self._send(200, {"status": "ok", "version": __version__, "auth_required_for_writes": True})
            elif path == "/api/v1/system":
                refresh = parse_qs(parsed.query).get("refresh") == ["true"]
                self._send(200, self.controller.system(refresh=refresh))
            elif path == "/api/v1/dashboard":
                self._send(200, self.controller.dashboard())
            elif path == "/api/v1/profiles":
                self._send(200, all_profiles())
            elif path == "/api/v1/runs":
                self._send(200, {"items": self.controller.database.list_runs()})
            elif path.startswith("/api/v1/runs/"):
                run = self.controller.database.get_run(path.rsplit("/", 1)[-1])
                self._send(200 if run else 404, run or {"error": "Run not found"})
            elif path.startswith("/api/v1/sessions/"):
                session = self.controller.database.get_session(path.rsplit("/", 1)[-1])
                self._send(200 if session else 404, session or {"error": "Session not found"})
            else:
                self._send(404, {"error": "Not found"})
        except Exception as exc:  # defensive API boundary
            self._send(500, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        try:
            body = self._body()
            if path.endswith("/join") and path.startswith("/api/v1/sessions/"):
                session_id = path.split("/")[-2]
                self._send(200, self.controller.join_session(session_id, body))
                return
            if path.startswith("/api/v1/agents/"):
                parts = path.split("/")
                agent_id = parts[4] if len(parts) > 4 else ""
                if not self._agent_authorized(agent_id):
                    self._send(401, {"error": "Missing or invalid X-CloudMark-Agent-Token"})
                    return
                if path.endswith("/heartbeat"):
                    self._send(200, self.controller.heartbeat_agent(agent_id, body))
                elif path.endswith("/tasks/next"):
                    self._send(200, self.controller.next_agent_task(agent_id))
                elif "/tasks/" in path and path.endswith("/progress"):
                    task_id = parts[-2]
                    self._send(200, self.controller.progress_agent_task(agent_id, task_id, body))
                elif "/tasks/" in path and path.endswith("/result"):
                    task_id = parts[-2]
                    self._send(200, self.controller.finish_agent_task(agent_id, task_id, body))
                else:
                    self._send(404, {"error": "Not found"})
                return
            if not self._authorized():
                self._send(401, {"error": "Missing or invalid X-CloudMark-Token"})
                return
            if path == "/api/v1/runs":
                self._send(202, self.controller.submit_run(body))
            elif path.startswith("/api/v1/runs/") and path.endswith("/cancel"):
                run_id = path.split("/")[-2]
                self._send(202, self.controller.cancel_run(run_id))
            elif path == "/api/v1/sessions":
                self._send(201, self.controller.create_session(str(body.get("label", ""))))
            else:
                self._send(404, {"error": "Not found"})
        except PermissionError as exc:
            self._send(403, {"error": str(exc)})
        except LookupError as exc:
            self._send(404, {"error": str(exc)})
        except (ValueError, BenchmarkError, ComputeError, NetworkError, json.JSONDecodeError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:  # defensive API boundary
            self._send(500, {"error": str(exc)})


class Server(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], controller: CloudMarkController):
        super().__init__(address, Handler)
        self.controller = controller


def serve(host: str, port: int, data_dir: Path) -> None:
    controller = CloudMarkController(data_dir)
    server = Server((host, port), controller)
    print(f"CloudMark API: http://{host}:{port}/api/v1/health")
    print(f"Controller token: {controller.token}")
    print("Policy: cloud-to-controller network measurement is disabled.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
