from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloudmark.container_environment import collect_container_environment


class ContainerEnvironmentTests(unittest.TestCase):
    @staticmethod
    def _roots(directory: str) -> tuple[Path, Path]:
        root = Path(directory)
        proc = root / "proc"
        filesystem = root / "rootfs"
        (proc / "1").mkdir(parents=True)
        (proc / "self").mkdir(parents=True)
        filesystem.mkdir()
        return proc, filesystem

    @staticmethod
    def _write_sources(proc: Path, cgroup: str, filesystem: str = "ext4") -> None:
        (proc / "1" / "cgroup").write_text(cgroup, encoding="utf-8")
        (proc / "self" / "cgroup").write_text(cgroup, encoding="utf-8")
        (proc / "self" / "mountinfo").write_text(
            f"24 23 0:20 / / rw,relatime - {filesystem} root rw\n",
            encoding="utf-8",
        )

    def test_detects_bounded_docker_containerd_and_kubernetes_hints_without_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            proc, filesystem = self._roots(directory)
            identifier = "0123456789abcdef0123456789abcdef"
            self._write_sources(
                proc,
                f"0::/kubepods.slice/kubepods-burstable.slice/cri-containerd-{identifier}.scope\n",
                "overlay",
            )
            (filesystem / ".dockerenv").write_text("", encoding="utf-8")
            with patch("cloudmark.container_environment.platform.system", return_value="Linux"):
                evidence = collect_container_environment(proc_root=proc, filesystem_root=filesystem)
        self.assertEqual(evidence["evidence_status"], "complete")
        self.assertEqual(evidence["container_status"], "detected")
        self.assertEqual(evidence["runtime_hints"], ["containerd", "docker"])
        self.assertEqual(evidence["orchestrator_hints"], ["kubernetes"])
        self.assertEqual(evidence["cgroup_version"], "v2")
        self.assertTrue(evidence["root_filesystem_overlay_like"])
        self.assertFalse(evidence["policy"]["container_identifier_persisted"])
        self.assertNotIn(identifier, json.dumps(evidence))

    def test_distinguishes_podman_bare_and_overlay_only_contexts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            proc, filesystem = self._roots(directory)
            self._write_sources(proc, "5:cpu,cpuacct:/libpod-abcdef.scope\n", "btrfs")
            (filesystem / "run").mkdir()
            (filesystem / "run" / ".containerenv").write_text("", encoding="utf-8")
            with patch("cloudmark.container_environment.platform.system", return_value="Linux"):
                podman = collect_container_environment(proc_root=proc, filesystem_root=filesystem)
            self.assertEqual(podman["runtime_hints"], ["podman"])
            self.assertEqual(podman["cgroup_version"], "v1")
            self.assertEqual(podman["container_status"], "detected")

            self._write_sources(proc, "0::/user.slice/user-1000.slice/session-1.scope\n", "ext4")
            (filesystem / "run" / ".containerenv").unlink()
            with patch("cloudmark.container_environment.platform.system", return_value="Linux"):
                bare = collect_container_environment(proc_root=proc, filesystem_root=filesystem)
            self.assertEqual(bare["container_status"], "not-detected")
            self.assertFalse(bare["policy"]["absence_proves_host_execution"])

            self._write_sources(proc, "0::/user.slice/user-1000.slice/session-1.scope\n", "overlay")
            with patch("cloudmark.container_environment.platform.system", return_value="Linux"):
                overlay = collect_container_environment(proc_root=proc, filesystem_root=filesystem)
            self.assertEqual(overlay["container_status"], "suspected")
            self.assertFalse(overlay["container_detected"])

    def test_missing_truncated_and_non_linux_sources_remain_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            proc, filesystem = self._roots(directory)
            oversized = f"0::/docker/{'a' * 5000}\n"
            (proc / "1" / "cgroup").write_text(oversized, encoding="utf-8")
            (proc / "self" / "cgroup").write_text("0::/\n", encoding="utf-8")
            with patch("cloudmark.container_environment.platform.system", return_value="Linux"):
                partial = collect_container_environment(proc_root=proc, filesystem_root=filesystem)
            self.assertEqual(partial["evidence_status"], "partial")
            self.assertEqual(partial["container_status"], "unavailable")
            self.assertTrue(partial["sources"]["pid1_cgroup"]["truncated"])
            self.assertEqual(partial["runtime_hints"], [])
            self.assertFalse(partial["policy"]["raw_cgroup_path_persisted"])

            with patch("cloudmark.container_environment.platform.system", return_value="Windows"):
                unavailable = collect_container_environment(proc_root=proc, filesystem_root=filesystem)
            self.assertEqual(unavailable["evidence_status"], "unavailable")
            self.assertEqual(unavailable["container_status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
