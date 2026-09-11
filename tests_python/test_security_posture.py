from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cloudmark.agent import AgentWorker
from cloudmark.database import Database
from cloudmark.remote import REMOTE_METHODOLOGY_VERSION, validate_remote_agent
from cloudmark.runner import JobContext
from cloudmark.server import CloudMarkController
from cloudmark.suitability import _run_valid
from cloudmark.security_posture import (
    SECURITY_CONTROL_MAX_BYTES,
    SECURITY_POSTURE_VERSION,
    SecurityPostureError,
    collect_linux_security_posture,
    run_security_posture,
    security_posture_preflight,
)


class SecurityPostureTests(unittest.TestCase):
    @staticmethod
    def _write(root: Path, relative: str, value: str | bytes) -> None:
        path = root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, bytes):
            path.write_bytes(value)
        else:
            path.write_text(value, encoding="utf-8")

    def test_collects_fixed_linux_controls_without_scoring(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "proc/sys/kernel/randomize_va_space", "2\n")
            self._write(root, "proc/sys/kernel/yama/ptrace_scope", "1\n")
            self._write(root, "proc/sys/fs/protected_hardlinks", "1\n")
            self._write(root, "proc/sys/fs/protected_symlinks", "1\n")
            self._write(root, "proc/sys/kernel/perf_event_paranoid", "3\n")
            self._write(root, "sys/kernel/security/lsm", "lockdown,capability,yama,apparmor\n")
            self._write(root, "sys/module/apparmor/parameters/enabled", "Y\n")
            self._write(root, "sys/kernel/security/lockdown", "none [integrity] confidentiality\n")
            self._write(root, "sys/fs/cgroup/cgroup.controllers", "cpu memory io pids\n")
            self._write(root, "proc/sys/kernel/modules_disabled", "1\n")
            self._write(root, "proc/sys/kernel/kexec_load_disabled", "1\n")
            self._write(root, "proc/sys/kernel/unprivileged_userns_clone", "0\n")
            self._write(root, "proc/sys/user/max_user_namespaces", "0\n")
            self._write(root, "proc/sys/vm/mmap_min_addr", "65536\n")
            self._write(root, "proc/sys/fs/suid_dumpable", "0\n")
            self._write(root, "proc/sys/kernel/sysrq", "176\n")
            self._write(root, "proc/sys/net/ipv4/tcp_syncookies", "1\n")
            self._write(root, "proc/sys/net/ipv4/conf/all/accept_redirects", "0\n")
            self._write(root, "proc/sys/net/ipv4/conf/all/rp_filter", "1\n")
            result = collect_linux_security_posture(root, platform_name="Linux")
        self.assertEqual(result["methodology_version"], SECURITY_POSTURE_VERSION)
        self.assertEqual(result["evidence_status"], "partial")
        self.assertEqual(result["controls"]["aslr"]["classification"], "full")
        self.assertEqual(result["controls"]["ptrace_scope"]["classification"], "restricted")
        self.assertTrue(result["controls"]["apparmor"]["enabled"])
        self.assertEqual(result["controls"]["kernel_lockdown"]["mode"], "integrity")
        self.assertEqual(result["controls"]["cgroup_v2"]["controller_count"], 4)
        self.assertEqual(result["controls"]["kernel_modules"]["classification"], "loading-permanently-disabled")
        self.assertEqual(result["controls"]["unprivileged_user_namespaces"]["classification"], "disabled")
        self.assertEqual(result["controls"]["minimum_mmap_address"]["value"], 65536)
        self.assertEqual(result["controls"]["magic_sysrq"]["classification"], "restricted-bitmask")
        self.assertEqual(result["controls"]["ipv4_reverse_path_filter"]["classification"], "strict")
        self.assertFalse(result["policy"]["security_score"])
        self.assertFalse(result["policy"]["missing_evidence_is_zero"])

    def test_redacts_core_handler_and_secure_boot_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sensitive_handler = "|/usr/lib/systemd/systemd-coredump secret-argument\n"
            self._write(root, "proc/sys/kernel/core_pattern", sensitive_handler)
            self._write(
                root,
                "sys/firmware/efi/efivars/SecureBoot-12345678-secret-guid",
                b"\x07\x00\x00\x00\x01",
            )
            result = collect_linux_security_posture(root, platform_name="Linux")
        core = result["controls"]["core_pattern"]
        secure_boot = result["controls"]["secure_boot"]
        self.assertEqual(core["classification"], "pipe-handler")
        self.assertFalse(core["raw_pattern_persisted"])
        self.assertNotIn("systemd-coredump", str(result))
        self.assertTrue(secure_boot["enabled"])
        self.assertFalse(secure_boot["variable_identifier_persisted"])
        self.assertNotIn("secret-guid", str(result))

    def test_rejects_oversized_and_malformed_control_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "proc/sys/kernel/randomize_va_space", "9\n")
            self._write(
                root,
                "proc/sys/kernel/core_pattern",
                "x" * (SECURITY_CONTROL_MAX_BYTES + 1),
            )
            result = collect_linux_security_posture(root, platform_name="Linux")
        self.assertEqual(result["controls"]["aslr"]["status"], "unavailable")
        self.assertEqual(result["controls"]["core_pattern"]["status"], "unavailable")
        self.assertIn("read bound", result["controls"]["core_pattern"]["reason"])

    def test_mount_hardening_keeps_flags_but_redacts_source_devices_and_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mountinfo = (
                "36 29 0:32 / /dev/shm rw,nosuid,nodev,noexec,relatime - tmpfs secret-shm-device rw,size=1024k\n"
                "37 29 8:1 /home /home rw,nosuid,nodev,relatime - ext4 secret-home-device rw,data=ordered\n"
                "38 29 8:2 /private /srv/private rw,relatime - ext4 secret-private-device rw\n"
            )
            self._write(root, "proc/self/mountinfo", mountinfo)
            result = collect_linux_security_posture(root, platform_name="Linux")
        evidence = result["controls"]["mount_hardening"]
        self.assertEqual(evidence["status"], "observed")
        self.assertTrue(evidence["mounts"]["/dev/shm"]["noexec"])
        self.assertTrue(evidence["mounts"]["/home"]["nosuid"])
        self.assertFalse(evidence["mounts"]["/home"]["noexec"])
        self.assertFalse(evidence["source_devices_persisted"])
        self.assertFalse(evidence["raw_mount_options_persisted"])
        self.assertNotIn("secret-shm-device", str(result))
        self.assertNotIn("secret-home-device", str(result))
        self.assertNotIn("/srv/private", str(result))

    def test_non_linux_platform_returns_unavailable_without_reading_host_state(self) -> None:
        result = collect_linux_security_posture(Path("Z:/does-not-exist"), platform_name="Windows")
        self.assertEqual(result["platform"], "Windows")
        self.assertEqual(result["evidence_status"], "unavailable")
        self.assertEqual(result["observed_controls"], 0)
        self.assertTrue(all(item["status"] == "unavailable" for item in result["controls"].values()))

    def test_security_run_envelope_is_read_only_versioned_and_unscored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "proc/sys/kernel/randomize_va_space", "2\n")
            context = JobContext("security", total_steps=1, timeout_seconds=30)
            result = run_security_posture(
                "linux-security-posture",
                context=context,
                root=root,
                platform_name="Linux",
            )
        self.assertEqual(result["suite"], "security")
        self.assertEqual(result["profile_version"], "2.0")
        self.assertEqual(result["methodology_version"], SECURITY_POSTURE_VERSION)
        self.assertTrue(result["policy"]["read_only"])
        self.assertFalse(result["analysis"]["scored"])
        self.assertEqual(context.completed_steps, 1)
        with self.assertRaises(SecurityPostureError):
            security_posture_preflight("linux-security-posture", platform_name="Windows")

    def test_agent_accepts_only_the_exact_remote_security_contract(self) -> None:
        worker = AgentWorker("http://127.0.0.1:8787", "agent", "token")
        task = {"id": "task_security123", "run_id": "run_security", "kind": "benchmark-security"}
        payload = {
            "suite": "security",
            "profile": "linux-security-posture",
            "timeout_seconds": 120,
            "load_confirmed": False,
            "read_only": True,
            "protocol_version": REMOTE_METHODOLOGY_VERSION,
        }
        benchmark = {
            "suite": "security",
            "profile": "linux-security-posture",
            "profile_version": "2.0",
            "methodology_version": SECURITY_POSTURE_VERSION,
            "tool": {"name": "cloudmark-security-posture", "version": SECURITY_POSTURE_VERSION},
        }
        with patch.object(worker, "_benchmark_evidence", return_value={"inventory": {}}), patch(
            "cloudmark.agent.run_security_posture", return_value=benchmark
        ):
            result = worker._run_benchmark(task, payload)
        self.assertEqual(result["benchmark"], benchmark)
        self.assertEqual(result["protocol_version"], REMOTE_METHODOLOGY_VERSION)
        with self.assertRaisesRegex(ValueError, "read-only"):
            worker._run_benchmark(task, {**payload, "read_only": False})

    def test_controller_dispatches_and_attributes_remote_security_posture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = CloudMarkController(Path(directory))
            controller.database.create_session(
                "session_security", "security", "hash", "2099-01-01T00:00:00+00:00"
            )
            system = {
                "inventory": {
                    "hostname": "security-target",
                    "os": {"system": "Linux", "distribution": "Test Linux"},
                    "capabilities": {"security_posture_linux": True},
                },
                "provider": {"provider": "Test Provider", "source": "test"},
            }
            controller.database.add_agent(
                "security_agent",
                "session_security",
                "security-target",
                "target",
                system,
                endpoint={"address": "10.0.0.10"},
            )
            agent = validate_remote_agent(
                controller.database,
                "security_agent",
                "security",
                "linux-security-posture",
            )
            self.assertEqual(agent["id"], "security_agent")
            submitted = controller.submit_run({
                "suite": "security",
                "profile": "linux-security-posture",
                "agent_id": "security_agent",
            })
            task = None
            deadline = time.time() + 3
            while time.time() < deadline and task is None:
                task = controller.database.claim_agent_task("security_agent")
                if task is None:
                    time.sleep(0.01)
            self.assertIsNotNone(task)
            assert task is not None
            self.assertEqual(task["kind"], "benchmark-security")
            self.assertTrue(task["payload"]["read_only"])
            self.assertFalse(task["payload"]["load_confirmed"])
            controller.finish_agent_task(
                "security_agent",
                task["id"],
                {
                    "status": "completed",
                    "result": {
                        "benchmark": {
                            "suite": "security",
                            "profile": "linux-security-posture",
                            "profile_version": "2.0",
                            "methodology_version": SECURITY_POSTURE_VERSION,
                            "security_posture": {
                                "evidence_status": "partial",
                                "observed_controls": 20,
                                "total_controls": 31,
                            },
                            "analysis": {"scored": False},
                            "tool": {
                                "name": "cloudmark-security-posture",
                                "version": SECURITY_POSTURE_VERSION,
                            },
                        },
                        "evidence": system,
                        "protocol_version": REMOTE_METHODOLOGY_VERSION,
                        "agent_version": "0.5.0",
                    },
                },
            )
            deadline = time.time() + 3
            run = controller.database.get_run(submitted["id"])
            while time.time() < deadline and run["status"] not in {"completed", "failed", "cancelled"}:
                time.sleep(0.01)
                run = controller.database.get_run(submitted["id"])
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["request"]["execution"], "remote-agent")
            self.assertEqual(run["result"]["execution"]["agent"]["id"], "security_agent")
            self.assertEqual(run["result"]["methodology_version"], SECURITY_POSTURE_VERSION)

    def test_suitability_accepts_only_the_installed_security_methodology(self) -> None:
        run = {
            "suite": "security",
            "profile": "linux-security-posture",
            "status": "completed",
            "methodology_version": SECURITY_POSTURE_VERSION,
            "result": {
                "methodology_version": SECURITY_POSTURE_VERSION,
                "security_posture": {"evidence_status": "partial"},
            },
        }
        self.assertEqual(_run_valid(run), (True, None))
        run["result"]["methodology_version"] = "linux-security-posture-unknown"
        valid, reason = _run_valid(run)
        self.assertFalse(valid)
        self.assertIn("methodology", str(reason).lower())


if __name__ == "__main__":
    unittest.main()
