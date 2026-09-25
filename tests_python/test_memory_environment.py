from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloudmark.compute import ComputeError, _logical_cpu_boundary, _memory_allocation_boundary, system_preflight
from cloudmark.inventory import collect_inventory
from cloudmark.memory_environment import _parse_index_list, collect_memory_environment


class MemoryEnvironmentTests(unittest.TestCase):
    def test_cpu_affinity_caps_effective_threads_without_persisting_cpu_ids(self) -> None:
        with patch("cloudmark.compute.os.cpu_count", return_value=32), patch(
            "cloudmark.compute.os.sched_getaffinity",
            return_value={4, 6, 8, 10},
            create=True,
        ):
            boundary = _logical_cpu_boundary()
        self.assertEqual(boundary["host_logical_cores"], 32)
        self.assertEqual(boundary["affinity_logical_cores"], 4)
        self.assertEqual(boundary["effective_logical_cores"], 4)
        self.assertTrue(boundary["affinity_respected"])
        self.assertFalse(boundary["cpu_ids_persisted"])
        self.assertFalse(boundary["cgroup_cpu_quota_applied"])

    def test_cgroup_cpu_quota_caps_threads_and_includes_finite_ancestors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "self-cgroup"
            proc.write_text("0::/tenant/workload\n", encoding="utf-8")
            child = root / "tenant" / "workload"
            child.mkdir(parents=True)
            (child / "cpu.max").write_text("max 100000\n", encoding="utf-8")
            parent = root / "tenant"
            (parent / "cpu.max").write_text("150000 100000\n", encoding="utf-8")
            (root / "cpu.max").write_text("max 100000\n", encoding="utf-8")
            with patch("cloudmark.compute.os.cpu_count", return_value=32), patch(
                "cloudmark.compute.os.sched_getaffinity",
                return_value={0, 1, 2, 3},
                create=True,
            ):
                boundary = _logical_cpu_boundary(proc_cgroup_path=proc, cgroup_root=root)

            self.assertEqual(boundary["quota_capacity_cores"], 1.5)
            self.assertEqual(boundary["quota_thread_ceiling"], 2)
            self.assertEqual(boundary["effective_logical_cores"], 2)
            self.assertEqual(boundary["quota_limiting_ancestor_depth"], 1)
            self.assertTrue(boundary["cgroup_cpu_quota_applied"])
            self.assertTrue(boundary["cgroup_cpu_quota_verified"])

            (parent / "cpu.max").write_text("malformed\n", encoding="utf-8")
            with patch("cloudmark.compute.os.cpu_count", return_value=32):
                malformed = _logical_cpu_boundary(proc_cgroup_path=proc, cgroup_root=root)
            self.assertFalse(malformed["cgroup_cpu_quota_verified"])

    def test_cgroup_v2_and_v1_headroom_bound_host_available_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "self-cgroup"
            proc.write_text("0::/tenant/workload\n", encoding="utf-8")
            v2 = root / "tenant" / "workload"
            v2.mkdir(parents=True)
            (v2 / "memory.max").write_text(str(2 * 1024**3), encoding="utf-8")
            (v2 / "memory.current").write_text(str(512 * 1024**2), encoding="utf-8")
            boundary = _memory_allocation_boundary(8 * 1024**3, proc_cgroup_path=proc, cgroup_root=root)
            self.assertEqual(boundary["cgroup_version"], "v2")
            self.assertEqual(boundary["cgroup_headroom_bytes"], 1536 * 1024**2)
            self.assertEqual(boundary["effective_available_bytes"], 1536 * 1024**2)
            self.assertEqual(boundary["cgroup_limiting_ancestor_depth"], 0)
            self.assertFalse(boundary["cgroup_path_persisted"])

            (v2 / "memory.max").write_text("max", encoding="utf-8")
            parent = root / "tenant"
            (parent / "memory.max").write_text(str(1024**3), encoding="utf-8")
            (parent / "memory.current").write_text(str(256 * 1024**2), encoding="utf-8")
            inherited = _memory_allocation_boundary(8 * 1024**3, proc_cgroup_path=proc, cgroup_root=root)
            self.assertEqual(inherited["cgroup_headroom_bytes"], 768 * 1024**2)
            self.assertEqual(inherited["cgroup_limiting_ancestor_depth"], 1)

            proc.write_text("5:cpu,memory:/legacy\n", encoding="utf-8")
            v1 = root / "memory" / "legacy"
            v1.mkdir(parents=True)
            (v1 / "memory.limit_in_bytes").write_text(str(3 * 1024**3), encoding="utf-8")
            (v1 / "memory.usage_in_bytes").write_text(str(1024**3), encoding="utf-8")
            legacy = _memory_allocation_boundary(8 * 1024**3, proc_cgroup_path=proc, cgroup_root=root)
            self.assertEqual(legacy["cgroup_version"], "v1")
            self.assertEqual(legacy["effective_available_bytes"], 2 * 1024**3)

    def test_memory_preflight_fails_closed_when_finite_cgroup_usage_is_unavailable(self) -> None:
        boundary = {
            "status": "partial",
            "effective_available_bytes": None,
            "finite_cgroup_limit_detected": True,
            "cgroup_headroom_verified": False,
        }
        with tempfile.TemporaryDirectory() as directory, patch(
            "cloudmark.compute.sys.platform",
            "linux",
        ), patch("cloudmark.compute.os.cpu_count", return_value=4), patch(
            "cloudmark.compute._memory_available_bytes",
            return_value=8 * 1024**3,
        ), patch("cloudmark.compute._memory_allocation_boundary", return_value=boundary), patch(
            "cloudmark.compute._compile_memory_tool"
        ) as compiler:
            with self.assertRaisesRegex(ComputeError, "cgroup memory boundary"):
                system_preflight("memory", "memory-quick", Path(directory))
        compiler.assert_not_called()

    def test_index_list_parser_accepts_sparse_ranges_and_rejects_unsafe_values(self) -> None:
        self.assertEqual(
            _parse_index_list("0-3,8,10-11", maximum_index=32, maximum_count=16),
            [0, 1, 2, 3, 8, 10, 11],
        )
        self.assertEqual(_parse_index_list("", maximum_index=32, maximum_count=16), [])
        self.assertIsNone(_parse_index_list("3-1", maximum_index=32, maximum_count=16))
        self.assertIsNone(_parse_index_list("0-99", maximum_index=32, maximum_count=128))
        self.assertIsNone(_parse_index_list("0-16", maximum_index=32, maximum_count=16))
        self.assertIsNone(_parse_index_list("0,unsafe", maximum_index=32, maximum_count=16))

    def test_collects_complete_sparse_guest_numa_topology(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "online").write_text("0,2\n", encoding="utf-8")
            proc_meminfo = root / "proc-meminfo"
            proc_meminfo.write_text(
                "SwapTotal: 1048576 kB\nSwapFree: 786432 kB\n"
                "AnonHugePages: 262144 kB\nHugePages_Total: 8\nHugePages_Free: 6\n"
                "HugePages_Rsvd: 1\nHugePages_Surp: 0\nHugepagesize: 2048 kB\n"
                "Hugetlb: 16384 kB\nZswap: 1024 kB\nZswapped: 4096 kB\n",
                encoding="utf-8",
            )
            thp_root = root / "transparent-hugepage"
            thp_root.mkdir()
            (thp_root / "enabled").write_text("always [madvise] never\n", encoding="utf-8")
            (thp_root / "defrag").write_text("always defer [defer+madvise] madvise never\n", encoding="utf-8")
            zswap = root / "zswap-enabled"
            zswap.write_text("Y\n", encoding="utf-8")
            for node_id, cpus, total_kib, free_kib, distances in (
                (0, "0-3", 8_388_608, 4_194_304, "10 20"),
                (2, "4-7", 8_388_608, 3_145_728, "20 10"),
            ):
                node = root / f"node{node_id}"
                node.mkdir()
                (node / "cpulist").write_text(f"{cpus}\n", encoding="utf-8")
                (node / "meminfo").write_text(
                    f"Node {node_id} MemTotal: {total_kib} kB\nNode {node_id} MemFree: {free_kib} kB\n",
                    encoding="utf-8",
                )
                (node / "distance").write_text(f"{distances}\n", encoding="utf-8")

            with patch("cloudmark.memory_environment.platform.system", return_value="Linux"), patch(
                "cloudmark.memory_environment._page_size_bytes",
                return_value=4096,
            ):
                evidence = collect_memory_environment(
                    sysfs_root=root,
                    proc_meminfo_path=proc_meminfo,
                    transparent_hugepage_root=thp_root,
                    zswap_enabled_path=zswap,
                )

        self.assertEqual(evidence["evidence_status"], "complete")
        self.assertEqual(evidence["methodology_version"], "memory-environment-v2")
        self.assertEqual(evidence["online_node_ids"], [0, 2])
        self.assertEqual(evidence["node_count"], 2)
        self.assertTrue(evidence["numa_exposed"])
        self.assertEqual(evidence["nodes"][0]["cpu_count"], 4)
        self.assertEqual(evidence["nodes"][0]["memory_total_bytes"], 8_388_608 * 1024)
        self.assertEqual(
            evidence["nodes"][1]["distances"],
            [{"target_node": 0, "distance": 20}, {"target_node": 2, "distance": 10}],
        )
        self.assertFalse(evidence["remote_node_penalty_measured"])
        self.assertFalse(evidence["policy"]["physical_host_placement_claim"])
        self.assertEqual(evidence["paging"]["status"], "observed")
        self.assertEqual(evidence["paging"]["swap_used_bytes"], 256 * 1024**2)
        self.assertEqual(evidence["paging"]["swap_used_percent"], 25.0)
        self.assertEqual(evidence["paging"]["transparent_hugepage"]["enabled_policy"], "madvise")
        self.assertEqual(evidence["paging"]["transparent_hugepage"]["defrag_policy"], "defer_madvise")
        self.assertTrue(evidence["paging"]["zswap"]["enabled"])
        self.assertFalse(evidence["paging"]["pressure_measured"])

    def test_malformed_or_missing_node_fields_remain_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "online").write_text("0\n", encoding="utf-8")
            node = root / "node0"
            node.mkdir()
            (node / "cpulist").write_text("0-unsafe\n", encoding="utf-8")
            (node / "meminfo").write_text("Node 0 MemFree: 128 kB\n", encoding="utf-8")
            (node / "distance").write_text("invalid\n", encoding="utf-8")
            with patch("cloudmark.memory_environment.platform.system", return_value="Linux"):
                evidence = collect_memory_environment(sysfs_root=root)

        self.assertEqual(evidence["evidence_status"], "partial")
        self.assertEqual(evidence["nodes"][0]["status"], "partial")
        self.assertIsNone(evidence["nodes"][0]["cpu_list"])
        self.assertEqual(evidence["nodes"][0]["distances"], [])
        self.assertIn("incomplete", evidence["reason"])

    def test_unsupported_or_hidden_topology_is_unavailable(self) -> None:
        with patch("cloudmark.memory_environment.platform.system", return_value="Windows"):
            unsupported = collect_memory_environment(sysfs_root=Path("unused"))
        self.assertEqual(unsupported["evidence_status"], "unavailable")
        self.assertIn("Linux only", unsupported["reason"])

        with tempfile.TemporaryDirectory() as directory, patch(
            "cloudmark.memory_environment.platform.system",
            return_value="Linux",
        ):
            hidden = collect_memory_environment(sysfs_root=Path(directory) / "missing")
        self.assertEqual(hidden["evidence_status"], "unavailable")
        self.assertIn("sysfs directory", hidden["reason"])

    def test_inventory_and_memory_preflight_attach_the_same_evidence_contract(self) -> None:
        evidence = {
            "methodology_version": "memory-environment-v2",
            "evidence_status": "complete",
            "node_count": 1,
            "nodes": [],
        }
        with tempfile.TemporaryDirectory() as directory, patch(
            "cloudmark.inventory.collect_memory_environment",
            return_value=evidence,
        ), patch("cloudmark.inventory._cpu_model", return_value="Test CPU"), patch(
            "cloudmark.inventory._memory_bytes",
            return_value=8 * 1024**3,
        ), patch("cloudmark.inventory._virtualization", return_value={"type": "test"}), patch(
            "cloudmark.inventory._disks",
            return_value=[],
        ), patch("cloudmark.inventory._network_addresses", return_value=[]), patch(
            "cloudmark.inventory.find_web_binary",
            return_value=None,
        ), patch("cloudmark.inventory.find_postgres_binary", return_value=None), patch(
            "cloudmark.inventory.find_mysql_binary",
            return_value=None,
        ), patch("cloudmark.inventory.find_redis_binary", return_value=None), patch(
            "cloudmark.inventory.shutil.which",
            return_value=None,
        ):
            inventory = collect_inventory(Path(directory))
        self.assertIs(inventory["memory"]["environment"], evidence)

        with tempfile.TemporaryDirectory() as directory, patch(
            "cloudmark.compute.sys.platform",
            "linux",
        ), patch("cloudmark.compute.os.cpu_count", return_value=4), patch(
            "cloudmark.compute._memory_available_bytes",
            return_value=8 * 1024**3,
        ), patch(
            "cloudmark.compute._compile_memory_tool",
            return_value={"binary": "memory-tool", "compiler": "gcc", "compiler_version": "gcc test"},
        ), patch("cloudmark.compute.collect_memory_environment", return_value=evidence):
            preflight = system_preflight("memory", "memory-quick", Path(directory))
        self.assertIs(preflight["memory_environment"], evidence)
        self.assertFalse(preflight["writes_benchmark_data"])


if __name__ == "__main__":
    unittest.main()
