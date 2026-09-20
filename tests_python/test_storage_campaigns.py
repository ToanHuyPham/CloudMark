from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from cloudmark.profiles import STORAGE_PROFILES
from cloudmark.runner import CancellationToken
from cloudmark.server import CloudMarkController
from cloudmark.storage_campaigns import (
    STORAGE_CAMPAIGN_VERSION,
    build_storage_campaign_contract,
    project_storage_campaign,
)


def target_system() -> dict[str, object]:
    return {
        "inventory": {
            "hostname": "storage-target",
            "os": {"system": "Linux", "release": "6.8", "architecture": "x86_64"},
            "capabilities": {"fio": True, "storage_environment_linux": True},
        },
        "provider": {
            "provider": "Test Provider",
            "source": "provider-metadata",
            "instance_type": "storage-4",
            "region": "test-region",
            "zone": "test-zone-a",
        },
    }


def storage_environment(scheduler: str = "mq-deadline") -> dict[str, object]:
    return {
        "methodology_version": "storage-environment-v1",
        "evidence_status": "complete",
        "mount": {
            "status": "observed",
            "filesystem_type": "ext4",
            "source_class": "block-device",
            "mount_options": ["relatime", "rw"],
        },
        "block_device": {
            "status": "observed",
            "device_type": "virtio-block",
            "scheduler": {"status": "observed", "selected": scheduler},
            "rotational": False,
            "logical_block_size_bytes": 512,
            "physical_block_size_bytes": 4096,
            "write_cache": "write back",
            "stacked_slave_count": 0,
        },
    }


def fio_measurement(job: dict[str, object]) -> dict[str, object]:
    rw = str(job["rw"])
    read_active = rw in {"read", "randread", "rw", "randrw"}
    write_active = rw in {"write", "randwrite", "rw", "randrw"}
    series_direction = "read" if read_active else "write"

    def direction(active: bool) -> dict[str, object]:
        return {
            "io_bytes": 1_048_576 if active else 0,
            "iops": 1000.0 if active else 0,
            "bandwidth_bytes_per_second": 4_194_304 if active else 0,
            "p50_ms": 1.0 if active else None,
            "p90_ms": 2.0 if active else None,
            "p95_ms": 3.0 if active else None,
            "p99_ms": 4.0 if active else None,
            "p999_ms": 5.0 if active else None,
        }

    return {
        "name": job["name"],
        "workload": dict(job),
        "runtime_seconds": float(job["runtime"]),
        "read": direction(read_active),
        "write": direction(write_active),
        "cpu": {"user_percent": 1.0, "system_percent": 1.0},
        "time_series": {
            "interval_ms": 1000,
            "bandwidth": [{"elapsed_ms": 1000, "value": 4096, "direction": series_direction}],
            "iops": [{"elapsed_ms": 1000, "value": 1000, "direction": series_direction}],
            "latency": [{"elapsed_ms": 1000, "value": 1.0, "direction": series_direction}],
        },
    }


def storage_run(
    run_id: str,
    day: int,
    *,
    campaign_id: str | None = None,
    scheduler: str = "mq-deadline",
    status: str = "completed",
) -> dict[str, object]:
    request: dict[str, object] = {"suite": "storage", "profile": "disk-standard"}
    if campaign_id:
        request.update({
            "campaign_id": campaign_id,
            "campaign_contract_version": STORAGE_CAMPAIGN_VERSION,
            "campaign_target_id": "controller",
            "confirm_write": True,
            "confirm_campaign_window": True,
            "campaign_window_day": f"2026-09-{day:02d}",
            "campaign_window_number": day - 9,
            "campaign_attempt_number": day - 9,
        })
    return {
        "id": run_id,
        "suite": "storage",
        "profile": "disk-standard",
        "status": status,
        "methodology_version": "storage-v1",
        "tool_version": "fio-3.39",
        "started_at": f"2026-09-{day:02d}T01:00:00+00:00",
        "finished_at": f"2026-09-{day:02d}T01:10:00+00:00" if status == "completed" else None,
        "request": request,
        "result": {
            "suite": "storage",
            "profile": "disk-standard",
            "profile_version": "1.1",
            "methodology_version": "storage-v1",
            "tool": {"name": "fio", "version": "fio-3.39"},
            "target_evidence": target_system(),
            "preflight": {
                "profile_version": "1.1",
                "methodology_version": "storage-v1",
                "job_count": len(STORAGE_PROFILES["disk-standard"]["jobs"]),
                "destructive": False,
                "raw_device": False,
            },
            "storage_environment": storage_environment(scheduler),
            "safety": {"test_file_removed": True, "fio_logs_removed": True},
            "jobs": [fio_measurement(job) for job in STORAGE_PROFILES["disk-standard"]["jobs"]],
        },
    }


def filesystem_run(run_id: str, day: int) -> dict[str, object]:
    profile = STORAGE_PROFILES["disk-filesystem"]
    operations: list[dict[str, object]] = []
    for job in profile["jobs"]:
        name = str(job["name"])
        durable = name == "durable-create-fsync"
        count = int(profile["durable_file_count"] if durable else profile["file_count"])
        bytes_processed = (
            count * int(profile["file_bytes"])
            if name in {"small-file-create", "small-file-read-verify", "durable-create-fsync"}
            else 0
        )
        integrity = (
            {
                "status": "verified",
                "algorithm": "SHA-256",
                "verified_files": count,
                "mismatches": 0,
            }
            if name in {"small-file-read-verify", "durable-create-fsync"}
            else {"status": "not-applicable"}
        )
        durability = (
            {
                "status": "per-file-fsync",
                "file_fsync_count": count,
                "directory_fsync": {"status": "observed", "duration_ms": 1.0},
            }
            if durable
            else {"status": "not-applicable"}
        )
        operations.append({
            "name": name,
            "operation": job["operation"],
            "operation_count": count,
            "elapsed_seconds": 1.0,
            "operations_per_second": float(count),
            "latency_ms": {"minimum": 0.1, "p50": 0.2, "p95": 0.3, "p99": 0.4, "maximum": 0.5},
            "bytes_processed": bytes_processed,
            "integrity": integrity,
            "durability": durability,
            "cache_scope": "fixed-test-scope",
        })
    tool_version = "storage-filesystem-v1; Python 3.13.7"
    return {
        "id": run_id,
        "suite": "storage",
        "profile": "disk-filesystem",
        "status": "completed",
        "methodology_version": "storage-filesystem-v1",
        "tool_version": tool_version,
        "started_at": f"2026-09-{day:02d}T01:00:00+00:00",
        "finished_at": f"2026-09-{day:02d}T01:10:00+00:00",
        "request": {"suite": "storage", "profile": "disk-filesystem"},
        "result": {
            "suite": "storage",
            "profile": "disk-filesystem",
            "profile_version": "1.0",
            "methodology_version": "storage-filesystem-v1",
            "tool": {"name": "cloudmark-filesystem-bench", "version": tool_version},
            "target_evidence": target_system(),
            "preflight": {
                "profile_version": "1.0",
                "methodology_version": "storage-filesystem-v1",
                "job_count": len(profile["jobs"]),
                "destructive": False,
                "raw_device": False,
            },
            "storage_environment": storage_environment(),
            "measurement_contract": {
                "worker_model": "single-process-sequential",
                "payload": f"deterministic-{profile['file_bytes']}-byte-sha256-verified",
                "latency_clock": "python-perf-counter-ns",
                "cache_control": "observed-not-flushed",
                "claim_scope": "guest-filesystem-and-python-runtime",
            },
            "filesystem_operations": operations,
            "safety": {"test_file_removed": True, "workspace_removed": True},
        },
    }


class StorageCampaignTests(unittest.TestCase):
    def test_contract_locks_baseline_target_profile_environment_and_tool(self) -> None:
        baseline = storage_run("run_baseline", 10)
        contract = build_storage_campaign_contract(baseline, target_system(), 3)
        self.assertEqual(contract["version"], STORAGE_CAMPAIGN_VERSION)
        self.assertEqual(contract["target"]["id"], "controller")
        self.assertEqual(contract["target"]["instance_type"], "storage-4")
        self.assertEqual(contract["profile_version"], "1.1")
        self.assertEqual(contract["methodology_version"], "storage-v1")
        self.assertEqual(contract["baseline"]["window_day"], "2026-09-10")
        self.assertIn("scheduler=mq-deadline", contract["storage_contract"])
        self.assertFalse(contract["claims"]["provider_rating_enabled"])

    def test_contract_accepts_only_complete_native_filesystem_measurements(self) -> None:
        baseline = filesystem_run("run_filesystem_baseline", 10)
        contract = build_storage_campaign_contract(baseline, target_system(), 3)
        self.assertEqual(contract["profile"], "disk-filesystem")
        self.assertEqual(contract["methodology_version"], "storage-filesystem-v1")
        incomplete = filesystem_run("run_filesystem_incomplete", 10)
        incomplete["result"]["filesystem_operations"][-1]["durability"]["file_fsync_count"] -= 1
        with self.assertRaisesRegex(ValueError, "measurement-contract-incomplete"):
            build_storage_campaign_contract(incomplete, target_system(), 3)

    def test_projection_counts_baseline_and_only_one_valid_run_per_utc_day(self) -> None:
        baseline = storage_run("run_baseline", 10)
        contract = build_storage_campaign_contract(baseline, target_system(), 3)
        campaign = {
            "id": "storage_campaign_test",
            "label": "Storage repeat",
            "status": "active",
            "target_windows": 3,
            "contract": contract,
        }
        first = storage_run("run_first", 11, campaign_id="storage_campaign_test")
        duplicate = storage_run("run_duplicate", 11, campaign_id="storage_campaign_test")
        view = project_storage_campaign(
            campaign,
            [baseline, first, duplicate],
            current_target=contract["target"],
            target_online=True,
            now=datetime(2026, 9, 11, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(view["progress"]["valid_windows"], 2)
        self.assertEqual(view["progress"]["valid_run_ids"], ["run_baseline", "run_first"])
        self.assertEqual(view["attempts"][1]["reason_code"], "duplicate-utc-window")
        self.assertFalse(view["next_window"]["eligible"])
        self.assertEqual(view["next_window"]["reason_code"], "utc-window-already-complete")
        final = storage_run("run_final", 12, campaign_id="storage_campaign_test")
        completed = project_storage_campaign(
            campaign,
            [baseline, first, final],
            current_target=contract["target"],
            target_online=True,
            now=datetime(2026, 9, 12, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["progress"]["valid_windows"], 3)
        self.assertEqual(completed["evidence_status"], "complete")

    def test_projection_rejects_storage_drift_target_drift_and_offline_target(self) -> None:
        baseline = storage_run("run_baseline", 10)
        contract = build_storage_campaign_contract(baseline, target_system(), 3)
        campaign = {
            "id": "storage_campaign_drift",
            "label": "Storage repeat",
            "status": "active",
            "target_windows": 3,
            "contract": contract,
        }
        drifted = storage_run(
            "run_drifted",
            11,
            campaign_id="storage_campaign_drift",
            scheduler="none",
        )
        drift_view = project_storage_campaign(
            campaign,
            [baseline, drifted],
            current_target=contract["target"],
            target_online=True,
            now=datetime(2026, 9, 12, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(drift_view["attempts"][0]["reason_code"], "storage-contract-mismatch")
        self.assertEqual(drift_view["progress"]["valid_windows"], 1)
        target_drifted = storage_run(
            "run_target_drifted",
            11,
            campaign_id="storage_campaign_drift",
        )
        target_drifted["result"]["target_evidence"]["provider"]["instance_type"] = "storage-8"
        target_drift_view = project_storage_campaign(
            campaign,
            [baseline, target_drifted],
            current_target=contract["target"],
            target_online=True,
            now=datetime(2026, 9, 12, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(target_drift_view["attempts"][0]["reason_code"], "target-evidence-mismatch")
        changed_target = {**contract["target"], "instance_type": "storage-8"}
        target_view = project_storage_campaign(
            campaign,
            [baseline],
            current_target=changed_target,
            target_online=True,
            now=datetime(2026, 9, 12, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(target_view["next_window"]["reason_code"], "campaign-target-contract-mismatch")
        offline = project_storage_campaign(
            campaign,
            [baseline],
            current_target=contract["target"],
            target_online=False,
            now=datetime(2026, 9, 12, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(offline["next_window"]["reason_code"], "campaign-target-offline")

    def test_controller_creates_without_load_and_dispatches_only_with_two_confirmations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = CloudMarkController(Path(directory))
            controller._inventory = target_system()["inventory"]
            controller._provider = target_system()["provider"]
            baseline = storage_run("run_campaign_baseline", 10)
            controller.database.create_run(
                "run_campaign_baseline",
                "storage",
                "disk-standard",
                baseline["request"],
                methodology_version="storage-v1",
                tool_version="fio-3.39",
            )
            controller.database.update_run("run_campaign_baseline", status="running")
            controller.database.update_run(
                "run_campaign_baseline",
                status="completed",
                result=baseline["result"],
            )
            with patch.object(controller, "system", return_value=target_system()):
                campaign = controller.create_storage_campaign({
                    "baseline_run_id": "run_campaign_baseline",
                    "target_windows": 3,
                })
            self.assertEqual(campaign["contract_version"], STORAGE_CAMPAIGN_VERSION)
            self.assertEqual(campaign["progress"]["valid_windows"], 1)
            self.assertEqual(controller.list_network_campaigns(), [])
            with self.assertRaisesRegex(ValueError, "confirm_write"):
                controller.start_storage_campaign_window(campaign["id"], {})
            eligible = {
                **campaign,
                "next_window": {
                    "eligible": True,
                    "reason_code": "ready-for-manual-dispatch",
                    "window_day": "2026-09-11",
                    "window_number": 2,
                    "attempt_number": 1,
                },
            }
            with patch.object(controller, "_project_storage_campaign", return_value=eligible), patch.object(
                controller,
                "_submit_run_locked",
                return_value={"id": "run_campaign_window", "status": "queued"},
            ) as submit, patch.object(controller, "get_storage_campaign", return_value=eligible):
                dispatched = controller.start_storage_campaign_window(
                    campaign["id"],
                    {"confirm_write": True, "confirm_campaign_window": True},
                )
            request = submit.call_args.args[0]
            self.assertEqual(dispatched["run"]["id"], "run_campaign_window")
            self.assertEqual(request["campaign_id"], campaign["id"])
            self.assertEqual(request["campaign_target_id"], "controller")
            self.assertTrue(request["confirm_write"])
            self.assertTrue(request["confirm_campaign_window"])
            self.assertEqual(request["campaign_window_number"], 2)

    def test_remote_campaign_dispatch_remains_bound_to_baseline_agent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = CloudMarkController(Path(directory))
            controller.database.create_session(
                "session_storage",
                "storage",
                "hash",
                "2099-01-01T00:00:00+00:00",
            )
            controller.database.add_agent(
                "agent_storage",
                "session_storage",
                "storage-target",
                "target",
                target_system(),
                endpoint={"address": "private-storage-target"},
            )
            baseline = storage_run("run_remote_baseline", 10)
            baseline["request"]["agent_id"] = "agent_storage"
            baseline["result"]["target_evidence"] = target_system()
            controller.database.create_run(
                "run_remote_baseline",
                "storage",
                "disk-standard",
                baseline["request"],
                methodology_version="storage-v1",
                tool_version="fio-3.39",
            )
            controller.database.update_run("run_remote_baseline", status="running")
            controller.database.update_run("run_remote_baseline", status="completed", result=baseline["result"])
            campaign = controller.create_storage_campaign({
                "baseline_run_id": "run_remote_baseline",
                "target_windows": 3,
            })
            self.assertEqual(campaign["target_id"], "agent_storage")
            eligible = {
                **campaign,
                "next_window": {
                    "eligible": True,
                    "reason_code": "ready-for-manual-dispatch",
                    "window_day": "2026-09-11",
                    "window_number": 2,
                    "attempt_number": 1,
                },
            }
            with patch.object(controller, "_project_storage_campaign", return_value=eligible), patch.object(
                controller,
                "_submit_run_locked",
                return_value={"id": "run_remote_window", "status": "queued"},
            ) as submit, patch.object(controller, "get_storage_campaign", return_value=eligible):
                controller.start_storage_campaign_window(
                    campaign["id"],
                    {"confirm_write": True, "confirm_campaign_window": True},
                )
            request = submit.call_args.args[0]
            self.assertEqual(request["agent_id"], "agent_storage")
            self.assertEqual(request["campaign_target_id"], "agent_storage")

    def test_contract_refuses_unverified_environment_and_invalid_baseline(self) -> None:
        baseline = storage_run("run_bad", 10)
        baseline["result"]["storage_environment"]["evidence_status"] = "unavailable"
        with self.assertRaisesRegex(ValueError, "campaign-eligible"):
            build_storage_campaign_contract(baseline, target_system(), 3)
        unclean = storage_run("run_unclean", 10)
        unclean["result"]["safety"]["test_file_removed"] = False
        with self.assertRaisesRegex(ValueError, "cleanup-not-verified"):
            build_storage_campaign_contract(unclean, target_system(), 3)
        incomplete = storage_run("run_incomplete", 10)
        incomplete["result"]["jobs"].pop()
        with self.assertRaisesRegex(ValueError, "measurement-contract-incomplete"):
            build_storage_campaign_contract(incomplete, target_system(), 3)

    def test_contract_requires_run_time_target_measurement_and_utc_evidence(self) -> None:
        missing_target = storage_run("run_missing_target", 10)
        missing_target["result"].pop("target_evidence")
        with self.assertRaisesRegex(ValueError, "target-evidence-unavailable"):
            build_storage_campaign_contract(missing_target, target_system(), 3)

        changed_target = storage_run("run_changed_target", 10)
        changed_target["result"]["target_evidence"]["provider"]["instance_type"] = "storage-8"
        with self.assertRaisesRegex(ValueError, "target-evidence-mismatch"):
            build_storage_campaign_contract(changed_target, target_system(), 3)

        naive_completion = storage_run("run_naive_completion", 10)
        naive_completion["finished_at"] = "2026-09-10T01:10:00"
        with self.assertRaisesRegex(ValueError, "completion-time-unavailable"):
            build_storage_campaign_contract(naive_completion, target_system(), 3)

        cross_midnight = storage_run("run_cross_midnight", 10)
        cross_midnight["started_at"] = "2026-09-10T23:59:00+00:00"
        cross_midnight["finished_at"] = "2026-09-11T00:01:00+00:00"
        with self.assertRaisesRegex(ValueError, "cross-utc-midnight"):
            build_storage_campaign_contract(cross_midnight, target_system(), 3)

        incomplete_measurement = storage_run("run_incomplete_measurement", 10)
        incomplete_measurement["result"]["jobs"][0]["read"]["p99_ms"] = None
        with self.assertRaisesRegex(ValueError, "measurement-contract-incomplete"):
            build_storage_campaign_contract(incomplete_measurement, target_system(), 3)

    def test_generic_run_submission_cannot_inject_campaign_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = CloudMarkController(Path(directory))
            with self.assertRaisesRegex(ValueError, "guarded campaign-window endpoint"):
                controller.submit_run({
                    "suite": "storage",
                    "profile": "disk-standard",
                    "confirm_write": True,
                    "campaign_id": "storage_campaign_bypass",
                    "campaign_contract_version": STORAGE_CAMPAIGN_VERSION,
                    "campaign_window_day": "2026-09-10",
                })
            with self.assertRaisesRegex(ValueError, "guarded campaign-window endpoint"):
                controller.submit_run({
                    "suite": "network",
                    "profile": "network-peer-standard",
                    "confirm_network_load": True,
                    "campaign_id": "network_campaign_bypass",
                    "campaign_contract_version": "network-campaign-v1",
                    "campaign_window_day": "2026-09-10",
                })

    def test_local_storage_completion_persists_run_time_target_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = CloudMarkController(Path(directory))
            controller._inventory = target_system()["inventory"]
            controller._provider = target_system()["provider"]
            request = {
                "suite": "storage",
                "profile": "disk-standard",
                "execution": "controller-host",
                "confirm_write": True,
                "timeout_seconds": 30,
            }
            controller.database.create_run(
                "run_local_target_evidence",
                "storage",
                "disk-standard",
                request,
                methodology_version="storage-v1",
                tool_version="fio-3.39",
            )
            result = storage_run("run_fixture", 10)["result"]
            result.pop("target_evidence")
            with patch("cloudmark.server.run_storage", return_value=result), patch.object(
                controller,
                "system",
                return_value=target_system(),
            ) as system:
                controller._execute_run(
                    "run_local_target_evidence",
                    request,
                    CancellationToken(),
                    len(STORAGE_PROFILES["disk-standard"]["jobs"]) + 2,
                )
            system.assert_called_with(refresh=True)
            stored = controller.database.get_run("run_local_target_evidence")
            self.assertEqual(stored["status"], "completed")
            self.assertEqual(
                stored["result"]["target_evidence"]["inventory"]["hostname"],
                "storage-target",
            )


if __name__ == "__main__":
    unittest.main()
