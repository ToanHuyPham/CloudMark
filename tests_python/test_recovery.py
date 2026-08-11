from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKUP_SCRIPT = REPOSITORY_ROOT / "scripts" / "runtime_backup.py"
RESTORE_SCRIPT = REPOSITORY_ROOT / "scripts" / "runtime_restore.py"


class RuntimeRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repository"
        self.runtime = self.repository / ".cloudmark"
        self.runtime.mkdir(parents=True)
        with closing(sqlite3.connect(self.runtime / "cloudmark.sqlite3")) as connection:
            connection.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, status TEXT NOT NULL)")
            connection.execute("INSERT INTO runs VALUES ('run_test', 'completed')")
            connection.commit()
        (self.runtime / "controller.token").write_text("FAKE-TOKEN", encoding="ascii")
        ssh = self.runtime / "ssh"
        ssh.mkdir()
        (ssh / "fake-private-key").write_text("FAKE-PRIVATE-KEY", encoding="ascii")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def run_script(self, script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *map(str, arguments)],
            check=False,
            capture_output=True,
            text=True,
        )

    def only_snapshot(self, destination: Path) -> Path:
        snapshots = list(destination.glob("CloudMark-runtime-*"))
        self.assertEqual(len(snapshots), 1)
        return snapshots[0]

    def create_evidence_snapshot(self, destination: Path) -> Path:
        result = self.run_script(
            BACKUP_SCRIPT,
            "--source",
            self.runtime,
            "--destination-root",
            destination,
            "--repository-root",
            self.repository,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.only_snapshot(destination)

    def test_evidence_snapshot_is_consistent_and_excludes_secrets(self) -> None:
        destination = self.root / "evidence-backups"
        snapshot = self.create_evidence_snapshot(destination)
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["format"], "cloudmark-runtime-snapshot-v1")
        self.assertEqual(manifest["database_integrity"], "ok")
        self.assertFalse(manifest["contains_secrets"])
        self.assertFalse((snapshot / "controller.token").exists())
        self.assertFalse((snapshot / "ssh").exists())
        with closing(sqlite3.connect(snapshot / "cloudmark.sqlite3")) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)

    def test_secret_snapshot_requires_acknowledgement(self) -> None:
        denied_destination = self.root / "denied-secret-backups"
        denied = self.run_script(
            BACKUP_SCRIPT,
            "--source",
            self.runtime,
            "--destination-root",
            denied_destination,
            "--repository-root",
            self.repository,
            "--include-secrets",
        )
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("acknowledge-sensitive-backup", denied.stderr)

        destination = self.root / "secret-backups"
        allowed = self.run_script(
            BACKUP_SCRIPT,
            "--source",
            self.runtime,
            "--destination-root",
            destination,
            "--repository-root",
            self.repository,
            "--include-secrets",
            "--acknowledge-sensitive-backup",
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        snapshot = self.only_snapshot(destination)
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["contains_secrets"])
        self.assertTrue((snapshot / "controller.token").is_file())
        self.assertTrue((snapshot / "ssh" / "fake-private-key").is_file())

    def test_backup_destination_inside_repository_is_refused(self) -> None:
        result = self.run_script(
            BACKUP_SCRIPT,
            "--source",
            self.runtime,
            "--destination-root",
            self.repository / "unsafe-backups",
            "--repository-root",
            self.repository,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the repository", result.stderr)

    def test_restore_validates_snapshot_and_preserves_existing_runtime(self) -> None:
        snapshot = self.create_evidence_snapshot(self.root / "restore-source")
        target = self.root / "restored-runtime"
        first = self.run_script(
            RESTORE_SCRIPT,
            "--backup",
            snapshot,
            "--target",
            target,
            "--controller-port",
            "0",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue((target / "cloudmark.sqlite3").is_file())
        self.assertFalse((target / "controller.token").exists())

        (target / "sentinel.txt").write_text("preserve", encoding="ascii")
        refused = self.run_script(
            RESTORE_SCRIPT,
            "--backup",
            snapshot,
            "--target",
            target,
            "--controller-port",
            "0",
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertTrue((target / "sentinel.txt").is_file())

        replaced = self.run_script(
            RESTORE_SCRIPT,
            "--backup",
            snapshot,
            "--target",
            target,
            "--controller-port",
            "0",
            "--replace",
        )
        self.assertEqual(replaced.returncode, 0, replaced.stderr)
        preserved = list(self.root.glob("restored-runtime.before-restore-*"))
        self.assertEqual(len(preserved), 1)
        self.assertTrue((preserved[0] / "sentinel.txt").is_file())
        with closing(sqlite3.connect(target / "cloudmark.sqlite3")) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
