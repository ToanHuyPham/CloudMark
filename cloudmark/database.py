from __future__ import annotations

import hmac
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._ephemeral_task_secrets: dict[str, dict[str, str]] = {}
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                suite TEXT NOT NULL,
                profile TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                request_json TEXT NOT NULL,
                campaign_id TEXT,
                result_json TEXT,
                error TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                join_token_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                topology_json TEXT NOT NULL DEFAULT '{}'
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                status TEXT NOT NULL,
                joined_at TEXT NOT NULL,
                system_json TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS agent_tasks (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                claimed_at TEXT,
                finished_at TEXT,
                progress REAL NOT NULL DEFAULT 0,
                phase TEXT,
                current_job TEXT,
                completed_steps INTEGER NOT NULL DEFAULT 0,
                total_steps INTEGER NOT NULL DEFAULT 0,
                heartbeat_at TEXT,
                FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                FOREIGN KEY(agent_id) REFERENCES agents(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS campaigns (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                target_windows INTEGER NOT NULL,
                contract_json TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_runs_status_started ON runs(status, started_at)",
            "CREATE INDEX IF NOT EXISTS idx_agents_session_id ON agents(session_id)",
            "CREATE INDEX IF NOT EXISTS idx_agent_tasks_next ON agent_tasks(agent_id, status, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_agent_tasks_run ON agent_tasks(run_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_campaigns_created ON campaigns(created_at)",
        ]
        with self._lock, self._connection() as connection:
            for statement in statements:
                connection.execute(statement)
            run_columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)").fetchall()}
            migrations = {
                "progress": "REAL NOT NULL DEFAULT 0",
                "phase": "TEXT",
                "current_job": "TEXT",
                "completed_steps": "INTEGER NOT NULL DEFAULT 0",
                "total_steps": "INTEGER NOT NULL DEFAULT 0",
                "heartbeat_at": "TEXT",
                "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
                "runner_version": "TEXT",
                "methodology_version": "TEXT",
                "tool_version": "TEXT",
                "campaign_id": "TEXT",
            }
            for column, definition in migrations.items():
                if column not in run_columns:
                    connection.execute(f"ALTER TABLE runs ADD COLUMN {column} {definition}")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_campaign_id ON runs(campaign_id, started_at)"
            )
            agent_columns = {row[1] for row in connection.execute("PRAGMA table_info(agents)").fetchall()}
            agent_migrations = {
                "token_hash": "TEXT NOT NULL DEFAULT ''",
                "last_seen_at": "TEXT",
                "endpoint_json": "TEXT NOT NULL DEFAULT '{}'",
            }
            for column, definition in agent_migrations.items():
                if column not in agent_columns:
                    connection.execute(f"ALTER TABLE agents ADD COLUMN {column} {definition}")
            session_columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)").fetchall()}
            session_migrations = {
                "topology_json": "TEXT NOT NULL DEFAULT '{}'",
            }
            for column, definition in session_migrations.items():
                if column not in session_columns:
                    connection.execute(f"ALTER TABLE sessions ADD COLUMN {column} {definition}")
            task_columns = {row[1] for row in connection.execute("PRAGMA table_info(agent_tasks)").fetchall()}
            task_migrations = {
                "progress": "REAL NOT NULL DEFAULT 0",
                "phase": "TEXT",
                "current_job": "TEXT",
                "completed_steps": "INTEGER NOT NULL DEFAULT 0",
                "total_steps": "INTEGER NOT NULL DEFAULT 0",
                "heartbeat_at": "TEXT",
            }
            for column, definition in task_migrations.items():
                if column not in task_columns:
                    connection.execute(f"ALTER TABLE agent_tasks ADD COLUMN {column} {definition}")
            connection.execute("PRAGMA optimize")

    def create_run(
        self,
        run_id: str,
        suite: str,
        profile: str,
        request: dict[str, Any],
        *,
        total_steps: int = 1,
        runner_version: str | None = None,
        methodology_version: str | None = None,
        tool_version: str | None = None,
    ) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO runs(
                    id, suite, profile, status, request_json, progress, phase,
                    completed_steps, total_steps, runner_version,
                    methodology_version, tool_version, campaign_id
                )
                VALUES (?, ?, ?, 'queued', ?, 0, 'queued', 0, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    suite,
                    profile,
                    json.dumps(request, ensure_ascii=False),
                    max(1, total_steps),
                    runner_version,
                    methodology_version,
                    tool_version,
                    str(request.get("campaign_id") or "") or None,
                ),
            )

    def update_run(
        self,
        run_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        progress: float | None = None,
        phase: str | None = None,
        current_job: str | None = None,
        tool_version: str | None = None,
    ) -> None:
        started_at = utc_now() if status == "running" else None
        finished_at = utc_now() if status in {"completed", "failed", "cancelled"} else None
        if status == "completed" and progress is None:
            progress = 1.0
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE runs
                SET status = ?,
                    started_at = COALESCE(?, started_at),
                    finished_at = COALESCE(?, finished_at),
                    result_json = COALESCE(?, result_json),
                    error = ?,
                    progress = COALESCE(?, progress),
                    phase = COALESCE(?, phase),
                    current_job = ?,
                    heartbeat_at = ?,
                    tool_version = COALESCE(?, tool_version)
                WHERE id = ?
                """,
                (
                    status,
                    started_at,
                    finished_at,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error,
                    min(1.0, max(0.0, progress)) if progress is not None else None,
                    phase,
                    current_job,
                    utc_now(),
                    tool_version,
                    run_id,
                ),
            )

    def update_run_progress(
        self,
        run_id: str,
        *,
        progress: float,
        phase: str,
        current_job: str | None,
        completed_steps: int,
        total_steps: int,
        result: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE runs
                SET progress = ?, phase = ?, current_job = ?,
                    completed_steps = ?, total_steps = ?, heartbeat_at = ?,
                    result_json = COALESCE(?, result_json)
                WHERE id = ?
                """,
                (
                    min(1.0, max(0.0, progress)),
                    phase,
                    current_job,
                    max(0, completed_steps),
                    max(1, total_steps),
                    utc_now(),
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    run_id,
                ),
            )

    def request_cancel(self, run_id: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE runs
                SET cancel_requested = 1, heartbeat_at = ?
                WHERE id = ? AND status IN ('queued', 'running')
                """,
                (utc_now(), run_id),
            )
        return cursor.rowcount > 0

    def is_cancel_requested(self, run_id: str) -> bool:
        with self._connection() as connection:
            row = connection.execute("SELECT cancel_requested FROM runs WHERE id = ?", (run_id,)).fetchone()
        return bool(row[0]) if row else False

    def recover_incomplete_runs(self) -> int:
        now = utc_now()
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE runs
                SET status = 'failed', finished_at = ?, heartbeat_at = ?,
                    phase = 'interrupted', current_job = NULL,
                    error = 'Controller restarted before the run reached a terminal state.'
                WHERE status IN ('queued', 'running')
                """,
                (now, now),
            )
            connection.execute(
                """
                UPDATE agent_tasks SET status = 'cancelled', finished_at = ?,
                    error = 'Controller restarted before the distributed task completed.'
                WHERE status IN ('queued', 'running')
                """,
                (now,),
            )
            self._ephemeral_task_secrets.clear()
        return cursor.rowcount

    @staticmethod
    def _run_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["request"] = json.loads(item.pop("request_json"))
        result_json = item.pop("result_json")
        item["result"] = json.loads(result_json) if result_json else None
        item["cancel_requested"] = bool(item.get("cancel_requested", 0))
        return item

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._run_row(row) if row else None

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY COALESCE(started_at, '') DESC, rowid DESC LIMIT ?",
                (max(1, min(limit, 5000)),),
            ).fetchall()
        return [self._run_row(row) for row in rows]

    def list_campaign_runs(self, campaign_id: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as connection:
            if campaign_id is None:
                rows = connection.execute(
                    "SELECT * FROM runs WHERE campaign_id IS NOT NULL "
                    "ORDER BY COALESCE(started_at, '') ASC, rowid ASC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM runs WHERE campaign_id = ? "
                    "ORDER BY COALESCE(started_at, '') ASC, rowid ASC",
                    (campaign_id,),
                ).fetchall()
        return [self._run_row(row) for row in rows]

    def create_campaign(
        self,
        campaign_id: str,
        label: str,
        target_windows: int,
        contract: dict[str, Any],
    ) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO campaigns(
                    id, label, status, created_at, target_windows, contract_json
                ) VALUES (?, ?, 'active', ?, ?, ?)
                """,
                (
                    campaign_id,
                    label,
                    utc_now(),
                    target_windows,
                    json.dumps(contract, ensure_ascii=False),
                ),
            )

    @staticmethod
    def _campaign_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["contract"] = json.loads(item.pop("contract_json"))
        return item

    def get_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        return self._campaign_row(row) if row else None

    def list_campaigns(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM campaigns ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [self._campaign_row(row) for row in rows]

    def create_session(
        self,
        session_id: str,
        label: str,
        join_token_hash: str,
        expires_at: str,
        topology: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO sessions(
                    id, label, join_token_hash, status, created_at, expires_at,
                    topology_json
                )
                VALUES (?, ?, ?, 'waiting', ?, ?, ?)
                """,
                (
                    session_id,
                    label,
                    join_token_hash,
                    utc_now(),
                    expires_at,
                    json.dumps(topology or {}, ensure_ascii=False),
                ),
            )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            session = connection.execute(
                """
                SELECT id, label, status, created_at, expires_at, topology_json
                FROM sessions WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
            if not session:
                return None
            agents = connection.execute(
                """
                SELECT id, name, role, status, joined_at, last_seen_at,
                       endpoint_json, system_json
                FROM agents WHERE session_id = ? ORDER BY joined_at
                """,
                (session_id,),
            ).fetchall()
        result = dict(session)
        topology = json.loads(result.pop("topology_json"))
        result["topology"] = topology if isinstance(topology, dict) and topology else {
            "scope": "undeclared",
            "source": "unavailable",
        }
        result["agents"] = []
        online_cutoff = datetime.now(timezone.utc) - timedelta(seconds=30)
        for row in agents:
            item = dict(row)
            item["system"] = json.loads(item.pop("system_json"))
            item["endpoint"] = json.loads(item.pop("endpoint_json"))
            last_seen = item.get("last_seen_at")
            try:
                if not last_seen or datetime.fromisoformat(str(last_seen)) < online_cutoff:
                    item["status"] = "offline"
            except ValueError:
                item["status"] = "offline"
            result["agents"].append(item)
        roles_online = {item["role"] for item in result["agents"] if item["status"] == "online"}
        result["status"] = "ready" if {"target", "generator"}.issubset(roles_online) else "waiting"
        return result

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id FROM sessions ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [session for row in rows if (session := self.get_session(str(row[0]))) is not None]

    def get_session_token_hash(self, session_id: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT join_token_hash FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return str(row[0]) if row else None

    def add_agent(
        self,
        agent_id: str,
        session_id: str,
        name: str,
        role: str,
        system: dict[str, Any],
        token_hash: str = "",
        endpoint: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO agents(
                    id, session_id, name, role, status, joined_at, last_seen_at,
                    token_hash, endpoint_json, system_json
                )
                VALUES (?, ?, ?, ?, 'online', ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    session_id,
                    name,
                    role,
                    now,
                    now,
                    token_hash,
                    json.dumps(endpoint or {}, ensure_ascii=False),
                    json.dumps(system, ensure_ascii=False),
                ),
            )
            connection.execute(
                """
                UPDATE sessions
                SET status = CASE
                    WHEN (SELECT COUNT(*) FROM agents WHERE session_id = ?) >= 2
                    THEN 'ready'
                    ELSE 'waiting'
                END
                WHERE id = ?
                """,
                (session_id, session_id),
            )

    def authenticate_agent(self, agent_id: str, token_hash: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT token_hash FROM agents WHERE id = ?",
                (agent_id,),
            ).fetchone()
        return bool(row and row[0] and hmac.compare_digest(str(row[0]), token_hash))

    def heartbeat_agent(self, agent_id: str, system: dict[str, Any] | None = None) -> dict[str, Any] | None:
        now = utc_now()
        with self._lock, self._connection() as connection:
            if system is None:
                connection.execute(
                    "UPDATE agents SET status = 'online', last_seen_at = ? WHERE id = ?",
                    (now, agent_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE agents SET status = 'online', last_seen_at = ?, system_json = ?
                    WHERE id = ?
                    """,
                    (now, json.dumps(system, ensure_ascii=False), agent_id),
                )
            row = connection.execute(
                "SELECT id, session_id, name, role, status, last_seen_at FROM agents WHERE id = ?",
                (agent_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM agents WHERE id = ?",
                (agent_id,),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["system"] = json.loads(item.pop("system_json"))
        item["endpoint"] = json.loads(item.pop("endpoint_json"))
        item.pop("token_hash", None)
        return item

    def create_agent_task(
        self,
        task_id: str,
        run_id: str,
        session_id: str,
        agent_id: str,
        kind: str,
        payload: dict[str, Any],
        *,
        ephemeral_secret: dict[str, str] | None = None,
    ) -> None:
        if ephemeral_secret is not None:
            if not ephemeral_secret or len(ephemeral_secret) > 8:
                raise ValueError("Ephemeral task secret must contain between one and eight fields.")
            for key, value in ephemeral_secret.items():
                if not key.isidentifier() or len(key) > 64 or not isinstance(value, str) or not 16 <= len(value) <= 512:
                    raise ValueError("Ephemeral task secret fields are outside the bounded contract.")
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO agent_tasks(
                    id, run_id, session_id, agent_id, kind, status,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    task_id,
                    run_id,
                    session_id,
                    agent_id,
                    kind,
                    json.dumps(payload, ensure_ascii=False),
                    utc_now(),
                ),
            )
            if ephemeral_secret is not None:
                self._ephemeral_task_secrets[task_id] = dict(ephemeral_secret)

    @staticmethod
    def _task_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        result_json = item.pop("result_json")
        item["result"] = json.loads(result_json) if result_json else None
        return item

    def claim_agent_task(self, agent_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM agent_tasks
                WHERE agent_id = ? AND status = 'queued'
                ORDER BY created_at LIMIT 1
                """,
                (agent_id,),
            ).fetchone()
            if not row:
                return None
            cursor = connection.execute(
                """
                UPDATE agent_tasks SET status = 'running', claimed_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (utc_now(), row["id"]),
            )
            if cursor.rowcount != 1:
                return None
            updated = connection.execute(
                "SELECT * FROM agent_tasks WHERE id = ?",
                (row["id"],),
            ).fetchone()
            task = self._task_row(updated)
            secret = self._ephemeral_task_secrets.get(str(row["id"]))
            if secret is not None:
                task["ephemeral_secret"] = dict(secret)
            return task

    def finish_agent_task(
        self,
        task_id: str,
        agent_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("Agent task status must be completed, failed, or cancelled.")
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_tasks
                SET status = ?, result_json = COALESCE(?, result_json), error = ?, finished_at = ?
                WHERE id = ? AND agent_id = ? AND status = 'running'
                """,
                (
                    status,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error,
                    utc_now(),
                    task_id,
                    agent_id,
                ),
            )
            if cursor.rowcount == 1:
                self._ephemeral_task_secrets.pop(task_id, None)
                return True
            existing = connection.execute(
                "SELECT status FROM agent_tasks WHERE id = ? AND agent_id = ?",
                (task_id, agent_id),
            ).fetchone()
            terminal = bool(existing and existing[0] in {"completed", "failed", "cancelled"})
            if terminal:
                self._ephemeral_task_secrets.pop(task_id, None)
            return terminal

    def update_agent_task_progress(
        self,
        task_id: str,
        agent_id: str,
        *,
        progress: float,
        phase: str | None,
        current_job: str | None,
        completed_steps: int,
        total_steps: int,
        result: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        now = utc_now()
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_tasks
                SET progress = ?, phase = ?, current_job = ?, completed_steps = ?,
                    total_steps = ?, heartbeat_at = ?, result_json = COALESCE(?, result_json)
                WHERE id = ? AND agent_id = ? AND status = 'running'
                """,
                (
                    min(1.0, max(0.0, progress)),
                    phase,
                    current_job,
                    max(0, completed_steps),
                    max(1, total_steps),
                    now,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    task_id,
                    agent_id,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute("SELECT * FROM agent_tasks WHERE id = ?", (task_id,)).fetchone()
        return self._task_row(row) if row else None

    def abort_agent_task(self, task_id: str, error: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_tasks
                SET status = 'cancelled', error = ?, finished_at = ?, heartbeat_at = ?
                WHERE id = ? AND status IN ('queued', 'running')
                """,
                (error, utc_now(), utc_now(), task_id),
            )
            if cursor.rowcount == 1:
                self._ephemeral_task_secrets.pop(task_id, None)
                return True
            return False

    def has_active_agent_task(self, agent_id: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM agent_tasks
                WHERE agent_id = ? AND status IN ('queued', 'running')
                LIMIT 1
                """,
                (agent_id,),
            ).fetchone()
        return row is not None

    def get_agent_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM agent_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        return self._task_row(row) if row else None

    def list_run_tasks(self, run_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_tasks WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        return [self._task_row(row) for row in rows]

    def cancel_queued_run_tasks(self, run_id: str) -> int:
        with self._lock, self._connection() as connection:
            task_ids = [
                str(row[0])
                for row in connection.execute(
                    "SELECT id FROM agent_tasks WHERE run_id = ? AND status = 'queued'",
                    (run_id,),
                ).fetchall()
            ]
            cursor = connection.execute(
                """
                UPDATE agent_tasks SET status = 'cancelled', finished_at = ?,
                    error = 'Parent run was cancelled before the task started.'
                WHERE run_id = ? AND status = 'queued'
                """,
                (utc_now(), run_id),
            )
            for task_id in task_ids:
                self._ephemeral_task_secrets.pop(task_id, None)
            return cursor.rowcount
