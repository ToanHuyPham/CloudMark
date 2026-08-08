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
from .database import Database
from .inventory import collect_inventory
from .profiles import all_profiles
from .provider import detect_provider


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


class CloudMarkController:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database = Database(self.data_dir / "cloudmark.sqlite3")
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
            "profiles": all_profiles(),
            "policy": {
                "cloud_to_controller_network_test": False,
                "provider_internal_peer_test": True,
                "raw_device_test": False,
            },
        }

    def submit_run(self, request: dict[str, Any]) -> dict[str, Any]:
        suite = str(request.get("suite", ""))
        profile = str(request.get("profile", ""))
        if suite not in {"inventory", "storage"}:
            raise ValueError("This release supports inventory and storage runs. The distributed network executor is not enabled yet.")
        if suite == "storage":
            if not request.get("confirm_write"):
                raise ValueError("Storage test requires confirm_write=true because it writes a temporary test file.")
            storage_preflight(profile, self.benchmark_dir)
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        self.database.create_run(run_id, suite, profile or "default", request)
        thread = threading.Thread(target=self._execute_run, args=(run_id, request), daemon=True)
        thread.start()
        return self.database.get_run(run_id) or {"id": run_id, "status": "queued"}

    def _execute_run(self, run_id: str, request: dict[str, Any]) -> None:
        self.database.update_run(run_id, status="running")
        try:
            if request["suite"] == "inventory":
                result = self.system(refresh=True)
            else:
                result = run_storage(str(request["profile"]), self.benchmark_dir, run_id)
            self.database.update_run(run_id, status="completed", result=result)
        except (BenchmarkError, OSError, ValueError, json.JSONDecodeError) as exc:
            self.database.update_run(run_id, status="failed", error=str(exc))

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
        self.database.add_agent(
            agent_id,
            session_id,
            str(request.get("name", agent_id)),
            role,
            request.get("system") or {},
        )
        return {"agent_id": agent_id, "session": self.database.get_session(session_id)}


class Handler(BaseHTTPRequestHandler):
    server_version = "CloudMark/0.1"

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
        if length > 1024 * 1024:
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

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        origin = self._origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-CloudMark-Token")
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
            if not self._authorized():
                self._send(401, {"error": "Missing or invalid X-CloudMark-Token"})
                return
            if path == "/api/v1/runs":
                self._send(202, self.controller.submit_run(body))
            elif path == "/api/v1/sessions":
                self._send(201, self.controller.create_session(str(body.get("label", ""))))
            else:
                self._send(404, {"error": "Not found"})
        except PermissionError as exc:
            self._send(403, {"error": str(exc)})
        except (ValueError, BenchmarkError, json.JSONDecodeError) as exc:
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
