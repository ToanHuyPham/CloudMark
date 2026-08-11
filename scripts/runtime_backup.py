from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a consistent CloudMark runtime snapshot outside the repository."
    )
    parser.add_argument("--source", type=Path, default=Path(".cloudmark"))
    parser.add_argument("--destination-root", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--include-secrets", action="store_true")
    parser.add_argument("--acknowledge-sensitive-backup", action="store_true")
    return parser.parse_args()


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def git_value(repository_root: Path, *arguments: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository_root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def sqlite_snapshot(source: Path, destination: Path) -> None:
    source_uri = f"file:{source.as_posix()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True, timeout=30)) as source_db:
        with closing(sqlite3.connect(destination)) as destination_db:
            source_db.backup(destination_db)
            integrity = destination_db.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise RuntimeError(f"SQLite integrity check failed: {integrity}")


def main() -> int:
    args = parse_args()
    repository_root = args.repository_root.resolve()
    source = args.source.resolve()
    destination_root = args.destination_root.resolve()

    if not source.is_dir():
        raise SystemExit(f"Runtime directory does not exist: {source}")
    database = source / "cloudmark.sqlite3"
    if not database.is_file():
        raise SystemExit(f"CloudMark database does not exist: {database}")
    if is_relative_to(destination_root, repository_root):
        raise SystemExit("Backup destination must be outside the repository.")
    if args.include_secrets and not args.acknowledge_sensitive_backup:
        raise SystemExit(
            "Secret backup requires --acknowledge-sensitive-backup. "
            "Store the result on encrypted media."
        )

    destination_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    snapshot = destination_root / f"CloudMark-runtime-{timestamp}"
    snapshot.mkdir()

    try:
        sqlite_snapshot(database, snapshot / "cloudmark.sqlite3")

        copied_secrets: list[str] = []
        if args.include_secrets:
            token = source / "controller.token"
            if token.is_file():
                shutil.copy2(token, snapshot / token.name)
                copied_secrets.append(token.name)
            ssh_directory = source / "ssh"
            if ssh_directory.is_dir():
                shutil.copytree(ssh_directory, snapshot / "ssh")
                copied_secrets.append("ssh/")

        manifest = {
            "format": "cloudmark-runtime-snapshot-v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "cloudmark_version": "0.5.0",
            "database": "cloudmark.sqlite3",
            "database_integrity": "ok",
            "contains_secrets": bool(copied_secrets),
            "secret_items": copied_secrets,
            "excluded": [
                "agent-workspace/",
                "cloudmark.sqlite3-wal",
                "cloudmark.sqlite3-shm",
            ],
            "git_commit": git_value(repository_root, "rev-parse", "HEAD"),
            "git_branch": git_value(repository_root, "branch", "--show-current"),
            "git_remote": git_value(repository_root, "remote", "get-url", "origin"),
        }
        (snapshot / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
    except Exception:
        shutil.rmtree(snapshot, ignore_errors=True)
        raise

    print(snapshot)
    if copied_secrets:
        print("SENSITIVE: encrypt this snapshot at rest before copying or sharing it.")
    else:
        print("Evidence-only snapshot created; Controller token and SSH material were excluded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
