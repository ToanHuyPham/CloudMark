from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cloudmark.security_posture import (
    SECURITY_CONTROL_MAX_BYTES,
    SECURITY_POSTURE_VERSION,
    collect_linux_security_posture,
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
            result = collect_linux_security_posture(root, platform_name="Linux")
        self.assertEqual(result["methodology_version"], SECURITY_POSTURE_VERSION)
        self.assertEqual(result["evidence_status"], "partial")
        self.assertEqual(result["controls"]["aslr"]["classification"], "full")
        self.assertEqual(result["controls"]["ptrace_scope"]["classification"], "restricted")
        self.assertTrue(result["controls"]["apparmor"]["enabled"])
        self.assertEqual(result["controls"]["kernel_lockdown"]["mode"], "integrity")
        self.assertEqual(result["controls"]["cgroup_v2"]["controller_count"], 4)
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

    def test_non_linux_platform_returns_unavailable_without_reading_host_state(self) -> None:
        result = collect_linux_security_posture(Path("Z:/does-not-exist"), platform_name="Windows")
        self.assertEqual(result["platform"], "Windows")
        self.assertEqual(result["evidence_status"], "unavailable")
        self.assertEqual(result["observed_controls"], 0)
        self.assertTrue(all(item["status"] == "unavailable" for item in result["controls"].values()))


if __name__ == "__main__":
    unittest.main()
