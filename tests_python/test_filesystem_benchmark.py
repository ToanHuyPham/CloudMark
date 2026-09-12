from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cloudmark.agent import AgentWorker
from cloudmark.benchmarks import BenchmarkError, run_storage, storage_preflight
from cloudmark.database import Database
from cloudmark.filesystem_benchmark import (
    deterministic_payload,
    latency_percentiles_ms,
)
from cloudmark.profiles import STORAGE_PROFILES
from cloudmark.remote import remote_default_timeout, remote_total_steps, validate_remote_agent
from cloudmark.runner import CancellationToken, JobContext, RunCancelled
from cloudmark.server import _dashboard_run_summaries
from cloudmark.suitability import _extract_run_evidence


class FilesystemBenchmarkTests(unittest.TestCase):
    @staticmethod
    def _tiny_profile() -> dict[str, object]:
        return {
            **STORAGE_PROFILES["disk-filesystem"],
            "estimated_minutes": 1,
            "file_size_mib": 1,
            "file_count": 8,
            "file_bytes": 512,
            "directory_count": 2,
            "durable_file_count": 2,
        }

    def test_payload_and_percentiles_are_deterministic(self) -> None:
        first = deterministic_payload(7, 512)
        self.assertEqual(first, deterministic_payload(7, 512))
        self.assertNotEqual(first, deterministic_payload(8, 512))
        self.assertEqual(len(first), 512)
        percentiles = latency_percentiles_ms([5_000_000, 1_000_000, 4_000_000, 2_000_000, 3_000_000])
        self.assertEqual(percentiles["minimum"], 1.0)
        self.assertEqual(percentiles["p50"], 3.0)
        self.assertEqual(percentiles["p95"], 5.0)
        self.assertEqual(percentiles["p99"], 5.0)

    def test_preflight_does_not_require_fio_and_preserves_reserve(self) -> None:
        usage = SimpleNamespace(total=20 * 1024**3, free=10 * 1024**3)
        with tempfile.TemporaryDirectory() as directory, patch(
            "cloudmark.filesystem_benchmark.shutil.disk_usage", return_value=usage
        ), patch("cloudmark.benchmarks.shutil.which", return_value=None):
            result = storage_preflight("disk-filesystem", Path(directory))
        self.assertEqual(result["executor"], "python-standard-library")
        self.assertEqual(result["reserve_bytes"], 1024**3)
        self.assertFalse(result["raw_device"])
        self.assertNotIn("fio", result)

    def test_preflight_refuses_mutated_operation_or_understated_workspace_contract(self) -> None:
        mutated = self._tiny_profile()
        mutated["jobs"] = [*mutated["jobs"][:-1], {"name": "caller-operation", "operation": "delete"}]
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            STORAGE_PROFILES,
            {"disk-filesystem": mutated},
        ):
            with self.assertRaisesRegex(BenchmarkError, "fixed executor contract"):
                storage_preflight("disk-filesystem", Path(directory))
        understated = self._tiny_profile()
        understated["file_count"] = 4096
        understated["file_bytes"] = 16_384
        understated["directory_count"] = 64
        understated["durable_file_count"] = 256
        understated["file_size_mib"] = 1
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            STORAGE_PROFILES,
            {"disk-filesystem": understated},
        ):
            with self.assertRaisesRegex(BenchmarkError, "workspace allowance"):
                storage_preflight("disk-filesystem", Path(directory))

    def test_runner_collects_integrity_durability_latency_and_cleanup(self) -> None:
        updates: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            STORAGE_PROFILES,
            {"disk-filesystem": self._tiny_profile()},
        ):
            workspace = Path(directory)
            context = JobContext(
                "run_filesystem_unit",
                total_steps=8,
                timeout_seconds=30,
                on_progress=updates.append,
            )
            result = run_storage("disk-filesystem", workspace, "run_filesystem_unit", context=context)
            self.assertFalse((workspace / "filesystem-benchmarks" / "run_filesystem_unit").exists())
        self.assertEqual(result["methodology_version"], "storage-filesystem-v1")
        self.assertEqual(len(result["filesystem_operations"]), 6)
        read_verify = next(
            item for item in result["filesystem_operations"] if item["name"] == "small-file-read-verify"
        )
        durable = next(
            item for item in result["filesystem_operations"] if item["name"] == "durable-create-fsync"
        )
        self.assertEqual(read_verify["integrity"]["status"], "verified")
        self.assertEqual(read_verify["integrity"]["mismatches"], 0)
        self.assertEqual(durable["durability"]["file_fsync_count"], 2)
        self.assertEqual(durable["integrity"]["status"], "verified")
        self.assertTrue(result["safety"]["workspace_removed"])
        self.assertEqual(context.completed_steps, 8)
        self.assertEqual(updates[-1]["progress"], 1.0)

    def test_cancelled_runner_removes_partial_workspace(self) -> None:
        token = CancellationToken()
        token.cancel()
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            STORAGE_PROFILES,
            {"disk-filesystem": self._tiny_profile()},
        ):
            workspace = Path(directory)
            context = JobContext(
                "run_filesystem_cancelled",
                total_steps=8,
                timeout_seconds=30,
                token=token,
            )
            with self.assertRaises(RunCancelled) as stopped:
                run_storage("disk-filesystem", workspace, "run_filesystem_cancelled", context=context)
            self.assertTrue(stopped.exception.partial_result["safety"]["workspace_removed"])
            self.assertFalse((workspace / "filesystem-benchmarks" / "run_filesystem_cancelled").exists())

    def test_runner_refuses_and_preserves_unknown_residual_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            STORAGE_PROFILES,
            {"disk-filesystem": self._tiny_profile()},
        ):
            workspace = Path(directory)
            residual = workspace / "filesystem-benchmarks" / "run_residual"
            residual.mkdir(parents=True)
            marker = residual / "operator-file.txt"
            marker.write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(BenchmarkError, "residual state"):
                run_storage("disk-filesystem", workspace, "run_residual")
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_runner_fails_closed_when_cleanup_cannot_be_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            STORAGE_PROFILES,
            {"disk-filesystem": self._tiny_profile()},
        ), patch("cloudmark.filesystem_benchmark.shutil.rmtree", side_effect=OSError("cleanup blocked")):
            with self.assertRaisesRegex(BenchmarkError, "cleanup could not be verified") as failure:
                run_storage("disk-filesystem", Path(directory), "run_cleanup_failure")
        self.assertFalse(failure.exception.partial_result["safety"]["workspace_removed"])

    def test_remote_agent_accepts_native_profile_without_fio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "cloudmark.sqlite3")
            database.create_session("session_filesystem", "filesystem", "hash", "2099-01-01T00:00:00+00:00")
            database.add_agent(
                "agent_filesystem",
                "session_filesystem",
                "filesystem-target",
                "target",
                {
                    "inventory": {
                        "hostname": "filesystem-target",
                        "os": {"system": "Linux"},
                        "capabilities": {"filesystem_metadata_benchmark": True, "fio": False},
                    }
                },
                endpoint={"address": "private-target"},
            )
            database.heartbeat_agent(
                "agent_filesystem",
                {
                    "inventory": {
                        "hostname": "filesystem-target",
                        "os": {"system": "Linux"},
                        "capabilities": {"filesystem_metadata_benchmark": True, "fio": False},
                    }
                },
            )
            agent = validate_remote_agent(database, "agent_filesystem", "storage", "disk-filesystem")
        self.assertEqual(agent["id"], "agent_filesystem")
        self.assertEqual(remote_total_steps("storage", "disk-filesystem"), 8)
        self.assertEqual(remote_default_timeout("storage", "disk-filesystem"), 540)

    def test_agent_routes_native_profile_through_versioned_storage_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            worker = AgentWorker(
                "http://127.0.0.1:8787",
                "agent_filesystem",
                "token",
                workspace=Path(directory),
            )

            def controller_reply(_suffix: str, _data: dict[str, object], **_: object) -> dict[str, object]:
                return {"task_status": "running", "cancel_requested": False}

            def fake_storage(
                profile_name: str,
                workspace: Path,
                run_id: str,
                *,
                context: JobContext,
            ) -> dict[str, object]:
                self.assertEqual(profile_name, "disk-filesystem")
                self.assertEqual(workspace, Path(directory).resolve())
                self.assertEqual(run_id, "run_filesystem_remote")
                context.report("benchmarking-filesystem", "small-file-create")
                return {
                    "suite": "storage",
                    "profile": profile_name,
                    "tool": {"name": "cloudmark-filesystem-bench", "version": "test"},
                }

            task = {
                "id": "task_filesystem_remote",
                "run_id": "run_filesystem_remote",
                "kind": "benchmark-storage",
                "payload": {
                    "suite": "storage",
                    "profile": "disk-filesystem",
                    "timeout_seconds": 540,
                    "load_confirmed": True,
                    "protocol_version": "remote-agent-v1",
                },
            }
            with patch.object(worker, "_api", side_effect=controller_reply), patch.object(
                worker,
                "_benchmark_evidence",
                return_value={"inventory": {"hostname": "filesystem-target"}, "provider": {}},
            ), patch("cloudmark.agent.run_storage", side_effect=fake_storage):
                result = worker._execute(task)
        self.assertEqual(result["benchmark"]["profile"], "disk-filesystem")
        self.assertEqual(result["protocol_version"], "remote-agent-v1")

    def test_provider_evidence_extracts_filesystem_metrics(self) -> None:
        observed_at = datetime.now(timezone.utc).isoformat()
        evidence: dict[str, dict[str, object]] = {}
        _extract_run_evidence(
            evidence,
            {
                "id": "run_filesystem_evidence",
                "suite": "storage",
                "profile": "disk-filesystem",
                "status": "completed",
                "finished_at": observed_at,
                "result": {
                    "methodology_version": "storage-filesystem-v1",
                    "filesystem_operations": [
                        {"name": "small-file-create", "operations_per_second": 1200.0},
                        {"name": "small-file-stat", "operations_per_second": 2400.0},
                        {"name": "small-file-read-verify", "latency_ms": {"p99": 0.8}},
                        {"name": "durable-create-fsync", "operations_per_second": 175.0},
                    ],
                    "safety": {"test_file_removed": True},
                },
            },
        )
        self.assertEqual(evidence["storage.small_file_create_ops"]["value"], 1200.0)
        self.assertEqual(evidence["storage.small_file_read_verify_p99_ms"]["value"], 0.8)
        self.assertEqual(evidence["storage.durable_create_fsync_ops"]["value"], 175.0)

    def test_dashboard_retains_latest_result_for_each_storage_profile(self) -> None:
        summaries = _dashboard_run_summaries([
            {
                "id": "run_filesystem",
                "suite": "storage",
                "profile": "disk-filesystem",
                "status": "completed",
                "request": {},
                "result": {"filesystem_operations": [{"name": "small-file-create"}]},
            },
            {
                "id": "run_quick",
                "suite": "storage",
                "profile": "disk-quick",
                "status": "completed",
                "request": {},
                "result": {"jobs": [{"name": "sequential-read"}]},
            },
        ])
        self.assertIn("result", summaries[0])
        self.assertIn("result", summaries[1])


if __name__ == "__main__":
    unittest.main()
