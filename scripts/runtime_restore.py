from __future__ import annotations

import argparse
import json
import shutil
import socket
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Restore a CloudMark runtime snapshot safely.")
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--target", type=Path, default=Path(".cloudmark"))
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--controller-host", default="127.0.0.1")
    parser.add_argument("--controller-port", type=int, default=8787)
    return parser.parse_args()


def controller_is_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def validate_snapshot(backup: Path) -> dict[str, object]:
    manifest_path = backup / "manifest.json"
    database = backup / "cloudmark.sqlite3"
    if not manifest_path.is_file() or not database.is_file():
        raise SystemExit("Snapshot must contain manifest.json and cloudmark.sqlite3.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "cloudmark-runtime-snapshot-v1":
        raise SystemExit("Unsupported or invalid CloudMark runtime snapshot format.")
    source_uri = f"file:{database.as_posix()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or integrity[0] != "ok":
        raise SystemExit(f"Snapshot database integrity check failed: {integrity}")
    return manifest


def main() -> int:
    args = parse_args()
    backup = args.backup.resolve()
    target = args.target.resolve()

    if controller_is_reachable(args.controller_host, args.controller_port):
        raise SystemExit(
            f"A process is reachable on {args.controller_host}:{args.controller_port}. "
            "Stop the CloudMark Controller before restoring."
        )
    if not backup.is_dir():
        raise SystemExit(f"Snapshot directory does not exist: {backup}")

    manifest = validate_snapshot(backup)
    existing_items = list(target.iterdir()) if target.is_dir() else []
    recovery_path: Optional[Path] = None
    if existing_items and not args.replace:
        raise SystemExit(
            f"Target runtime is not empty: {target}. Use --replace to move it aside safely."
        )
    if existing_items:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
        recovery_path = target.with_name(f"{target.name}.before-restore-{timestamp}")
        target.rename(recovery_path)

    target.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(backup / "cloudmark.sqlite3", target / "cloudmark.sqlite3")
        token = backup / "controller.token"
        if token.is_file():
            shutil.copy2(token, target / token.name)
        ssh_directory = backup / "ssh"
        if ssh_directory.is_dir():
            shutil.copytree(ssh_directory, target / "ssh")
        (target / "restore-manifest.json").write_text(
            json.dumps(
                {
                    "restored_at": datetime.now(timezone.utc).isoformat(),
                    "source_snapshot": str(backup),
                    "snapshot_manifest": manifest,
                    "previous_runtime": str(recovery_path) if recovery_path else None,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        if recovery_path is not None and recovery_path.exists():
            recovery_path.rename(target)
        raise

    print(target)
    if recovery_path is not None:
        print(f"Previous runtime preserved at: {recovery_path}")
    if not (target / "controller.token").exists():
        print("No Controller token was restored; CloudMark will create a new local token.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
