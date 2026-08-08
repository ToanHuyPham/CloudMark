from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
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
                expires_at TEXT NOT NULL
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
            "CREATE INDEX IF NOT EXISTS idx_runs_status_started ON runs(status, started_at)",
            "CREATE INDEX IF NOT EXISTS idx_agents_session_id ON agents(session_id)",
        ]
        with self._lock, self._connection() as connection:
            for statement in statements:
                connection.execute(statement)
            connection.execute("PRAGMA optimize")

    def create_run(self, run_id: str, suite: str, profile: str, request: dict[str, Any]) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO runs(id, suite, profile, status, request_json) VALUES (?, ?, ?, 'queued', ?)",
                (run_id, suite, profile, json.dumps(request, ensure_ascii=False)),
            )

    def update_run(
        self,
        run_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        started_at = utc_now() if status == "running" else None
        finished_at = utc_now() if status in {"completed", "failed", "cancelled"} else None
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE runs
                SET status = ?,
                    started_at = COALESCE(?, started_at),
                    finished_at = COALESCE(?, finished_at),
                    result_json = COALESCE(?, result_json),
                    error = ?
                WHERE id = ?
                """,
                (
                    status,
                    started_at,
                    finished_at,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error,
                    run_id,
                ),
            )

    @staticmethod
    def _run_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["request"] = json.loads(item.pop("request_json"))
        result_json = item.pop("result_json")
        item["result"] = json.loads(result_json) if result_json else None
        return item

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._run_row(row) if row else None

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY COALESCE(started_at, '') DESC, rowid DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [self._run_row(row) for row in rows]

    def create_session(
        self,
        session_id: str,
        label: str,
        join_token_hash: str,
        expires_at: str,
    ) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO sessions(id, label, join_token_hash, status, created_at, expires_at)
                VALUES (?, ?, ?, 'waiting', ?, ?)
                """,
                (session_id, label, join_token_hash, utc_now(), expires_at),
            )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            session = connection.execute(
                "SELECT id, label, status, created_at, expires_at FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not session:
                return None
            agents = connection.execute(
                "SELECT id, name, role, status, joined_at, system_json FROM agents WHERE session_id = ?",
                (session_id,),
            ).fetchall()
        result = dict(session)
        result["agents"] = []
        for row in agents:
            item = dict(row)
            item["system"] = json.loads(item.pop("system_json"))
            result["agents"].append(item)
        return result

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
    ) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO agents(id, session_id, name, role, status, joined_at, system_json)
                VALUES (?, ?, ?, ?, 'online', ?, ?)
                """,
                (
                    agent_id,
                    session_id,
                    name,
                    role,
                    utc_now(),
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
