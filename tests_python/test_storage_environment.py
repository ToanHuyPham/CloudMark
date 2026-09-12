from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from cloudmark.storage_environment import (
    STORAGE_ENVIRONMENT_VERSION,
    _collect_block_device_from_path,
    collect_storage_environment,
    parse_mountinfo,
    select_workspace_mount,
)
from cloudmark.suitability import _run_storage_contract, evaluate_suitability


class StorageEnvironmentTests(unittest.TestCase):
    def test_mountinfo_parser_selects_longest_match_and_filters_options(self) -> None:
        entries = parse_mountinfo(
            "36 25 8:1 / / rw,relatime shared:1 - ext4 /dev/vda1 rw,data=ordered,secret=value\n"
            "37 36 8:1 /data /mnt/data\\040space rw,noatime,nodev - ext4 /dev/vda1 rw,commit=5\n"
        )
        selected = select_workspace_mount(entries, "/mnt/data space/cloudmark", "8:1")
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected["mount_point"], "/mnt/data space")
        self.assertEqual(selected["filesystem_type"], "ext4")
        self.assertEqual(selected["mount_options"], ["commit=5", "noatime", "nodev", "rw"])
        self.assertNotIn("secret=value", selected["mount_options"])

    def test_sysfs_collector_normalizes_queue_geometry_without_serials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            partition = Path(directory) / "devices" / "pci0000-00" / "block" / "vda" / "vda1"
            queue = partition.parent / "queue"
            device = partition.parent / "device"
            slaves = partition.parent / "slaves"
            partition.mkdir(parents=True)
            queue.mkdir()
            device.mkdir()
            slaves.mkdir()
            (slaves / "dm-0").mkdir()
            values = {
                "scheduler": "[mq-deadline] none\n",
                "rotational": "0\n",
                "logical_block_size": "512\n",
                "physical_block_size": "4096\n",
                "minimum_io_size": "4096\n",
                "optimal_io_size": "0\n",
                "read_ahead_kb": "128\n",
                "nr_requests": "256\n",
                "discard_max_bytes": "4294966784\n",
                "write_cache": "write back\n",
                "zoned": "none\n",
            }
            for name, value in values.items():
                (queue / name).write_text(value, encoding="utf-8")
            (device / "vendor").write_text("Cloud Vendor\n", encoding="utf-8")
            (device / "model").write_text("Virtual Disk\n", encoding="utf-8")
            (device / "serial").write_text("must-not-be-read\n", encoding="utf-8")
            result = _collect_block_device_from_path(partition)
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["kernel_name"], "vda")
        self.assertEqual(result["partition_name"], "vda1")
        self.assertEqual(result["device_type"], "virtio-block")
        self.assertEqual(result["scheduler"]["selected"], "mq-deadline")
        self.assertEqual(result["logical_block_size_bytes"], 512)
        self.assertEqual(result["physical_block_size_bytes"], 4096)
        self.assertFalse(result["rotational"])
        self.assertEqual(result["stacked_slave_devices"], ["dm-0"])
        self.assertNotIn("serial", result)
        self.assertNotIn("must-not-be-read", json.dumps(result))

    def test_network_filesystem_context_is_complete_without_block_device(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mountinfo = Path(directory) / "mountinfo"
            mountinfo.write_text("placeholder", encoding="utf-8")
            selected = {
                "major_minor": "0:45",
                "mount_point": "/srv/provider-data",
                "filesystem_type": "nfs4",
                "source": "198.51.100.25:/private/export",
                "mount_options": ["rw", "sync"],
            }
            with patch("cloudmark.storage_environment.platform.system", return_value="Linux"), patch(
                "cloudmark.storage_environment.os.major", return_value=0, create=True
            ), patch("cloudmark.storage_environment.os.minor", return_value=45, create=True), patch(
                "cloudmark.storage_environment.select_workspace_mount", return_value=selected
            ):
                result = collect_storage_environment(Path(directory), mountinfo_path=mountinfo)
        serialized = json.dumps(result)
        self.assertEqual(result["evidence_status"], "complete")
        self.assertEqual(result["mount"]["source_class"], "network-filesystem")
        self.assertFalse(result["policy"]["raw_source_persisted"])
        self.assertNotIn("198.51.100.25", serialized)
        self.assertNotIn("private/export", serialized)

    def test_non_linux_context_is_explicitly_unavailable(self) -> None:
        with patch("cloudmark.storage_environment.platform.system", return_value="Windows"):
            result = collect_storage_environment(Path("C:/cloudmark"))
        self.assertEqual(result["evidence_status"], "unavailable")
        self.assertEqual(result["platform"], "Windows")
        self.assertTrue(result["policy"]["read_only"])
        self.assertFalse(result["policy"]["physical_device_claim"])

    def test_collection_failure_remains_unavailable_and_does_not_escape(self) -> None:
        with patch("cloudmark.storage_environment._collect_storage_environment", side_effect=KeyError("bad host data")), patch(
            "cloudmark.storage_environment.platform.system", return_value="Linux"
        ):
            result = collect_storage_environment(Path("/workspace"))
        self.assertEqual(result["evidence_status"], "unavailable")
        self.assertIn("failed closed", result["mount"]["reason"])

    def test_provider_storage_contract_requires_and_separates_exact_context(self) -> None:
        def run(scheduler: str) -> dict[str, object]:
            return {
                "suite": "storage",
                "result": {
                    "tool": {"name": "fio", "version": "fio-3.39"},
                    "storage_environment": {
                        "methodology_version": STORAGE_ENVIRONMENT_VERSION,
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
                    },
                },
            }

        deadline, deadline_verified = _run_storage_contract(run("mq-deadline"))
        none, none_verified = _run_storage_contract(run("none"))
        missing, missing_verified = _run_storage_contract({"suite": "storage", "result": {}})
        self.assertTrue(deadline_verified)
        self.assertTrue(none_verified)
        self.assertNotEqual(deadline, none)
        self.assertIn("scheduler=mq-deadline", deadline)
        self.assertFalse(missing_verified)
        self.assertIn("environment=unknown", missing)

    def test_provider_observations_do_not_merge_different_storage_schedulers(self) -> None:
        observed_at = datetime.now(timezone.utc).isoformat()
        system = {
            "inventory": {
                "hostname": "storage-target",
                "os": {"system": "Linux", "distribution": "Test Linux"},
                "cpu": {"model": "Test", "logical_cores": 4},
                "memory": {"total_bytes": 8 * 1024**3},
                "capabilities": {},
            },
            "provider": {
                "provider": "Test Provider",
                "confidence": 0.9,
                "source": "provider-metadata",
                "region": "test-region",
                "instance_type": "storage-4",
            },
        }

        def benchmark(run_id: str, scheduler: str, rate: float) -> dict[str, object]:
            return {
                "id": run_id,
                "suite": "storage",
                "profile": "disk-standard",
                "status": "completed",
                "finished_at": observed_at,
                "methodology_version": "storage-v1",
                "request": {"agent_id": "agent_storage"},
                "result": {
                    "methodology_version": "storage-v1",
                    "tool": {"name": "fio", "version": "fio-3.39"},
                    "safety": {"test_file_removed": True},
                    "jobs": [{
                        "name": "sequential-read",
                        "read": {"bandwidth_bytes_per_second": rate},
                        "write": {},
                    }],
                    "storage_environment": {
                        "methodology_version": STORAGE_ENVIRONMENT_VERSION,
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
                    },
                },
            }

        agent = {"last_seen_at": observed_at, "system": system}
        report = evaluate_suitability(
            [benchmark("run_deadline", "mq-deadline", 200_000_000), benchmark("run_none", "none", 210_000_000)],
            system,
            lambda agent_id: agent if agent_id == "agent_storage" else None,
        )
        metrics = [
            item
            for item in report["provider_observations"]["groups"][0]["metric_cohorts"]
            if item["key"] == "storage.sequential_read_bps"
        ]
        self.assertEqual(report["provider_observations"]["version"], "provider-observations-v5")
        self.assertEqual(len(metrics), 2)
        self.assertEqual({item["sample_count"] for item in metrics}, {1})
        self.assertTrue(all("storage filesystem" not in " ".join(item["reasons"]).lower() for item in metrics))


if __name__ == "__main__":
    unittest.main()
