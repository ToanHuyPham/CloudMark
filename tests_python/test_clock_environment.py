from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloudmark.clock_environment import collect_clock_environment


class ClockEnvironmentTests(unittest.TestCase):
    @staticmethod
    def _paths(directory: str) -> tuple[Path, Path]:
        root = Path(directory)
        clocksource = root / "clocksource0"
        clocksource.mkdir()
        timens = root / "timens_offsets"
        return clocksource, timens

    def test_collects_bounded_clocksource_namespace_and_systemd_assertions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clocksource, timens = self._paths(directory)
            (clocksource / "current_clocksource").write_text("kvm-clock\n", encoding="utf-8")
            (clocksource / "available_clocksource").write_text("kvm-clock tsc hpet\n", encoding="utf-8")
            timens.write_text("monotonic 0 0\nboottime 12 345\n", encoding="utf-8")
            completed = [
                subprocess.CompletedProcess([], 0, "yes\n", ""),
                subprocess.CompletedProcess([], 0, "no\n", ""),
            ]
            with patch("cloudmark.clock_environment.platform.system", return_value="Linux"), patch(
                "cloudmark.clock_environment.subprocess.run",
                side_effect=completed,
            ) as run:
                evidence = collect_clock_environment(
                    clocksource_root=clocksource,
                    timens_offsets_path=timens,
                    timedatectl="/usr/bin/timedatectl",
                )
        self.assertEqual(evidence["evidence_status"], "complete")
        self.assertEqual(evidence["clocksource"]["current"], "kvm-clock")
        self.assertEqual(evidence["clocksource"]["available"], ["kvm-clock", "tsc", "hpet"])
        self.assertTrue(evidence["time_namespace"]["nonzero_offset_observed"])
        self.assertEqual(evidence["time_namespace"]["offsets"][1]["clock"], "boottime")
        self.assertTrue(evidence["system_time"]["ntp_service_active_assertion"])
        self.assertFalse(evidence["system_time"]["system_clock_synchronized_assertion"])
        self.assertFalse(evidence["policy"]["cloudmark_ntp_validation_performed"])
        self.assertFalse(evidence["policy"]["offset_measured"])
        self.assertEqual(
            run.call_args_list[0].args[0],
            ["/usr/bin/timedatectl", "--no-pager", "show", "--property=NTP", "--value"],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["/usr/bin/timedatectl", "--no-pager", "show", "--property=NTPSynchronized", "--value"],
        )
        self.assertNotIn(str(clocksource), json.dumps(evidence))

    def test_malformed_truncated_and_command_failures_remain_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clocksource, timens = self._paths(directory)
            (clocksource / "current_clocksource").write_text("tsc\n", encoding="utf-8")
            (clocksource / "available_clocksource").write_text("tsc " + "a" * 5000, encoding="utf-8")
            timens.write_text("realtime 0 0\n", encoding="utf-8")
            with patch("cloudmark.clock_environment.platform.system", return_value="Linux"), patch(
                "cloudmark.clock_environment.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["timedatectl"], 3),
            ):
                evidence = collect_clock_environment(
                    clocksource_root=clocksource,
                    timens_offsets_path=timens,
                    timedatectl="timedatectl",
                )
        self.assertEqual(evidence["evidence_status"], "partial")
        self.assertEqual(evidence["clocksource"]["status"], "unavailable")
        self.assertTrue(evidence["clocksource"]["truncated"])
        self.assertEqual(evidence["time_namespace"]["status"], "unavailable")
        self.assertEqual(evidence["system_time"]["status"], "partial")

    def test_non_linux_retains_portable_clock_semantics_without_sync_claim(self) -> None:
        with patch("cloudmark.clock_environment.platform.system", return_value="Windows"), patch(
            "cloudmark.clock_environment.subprocess.run",
        ) as run:
            evidence = collect_clock_environment()
        self.assertEqual(evidence["evidence_status"], "partial")
        self.assertEqual(evidence["clocksource"]["status"], "unavailable")
        self.assertEqual(evidence["system_time"]["status"], "unavailable")
        self.assertEqual(evidence["python_clocks"]["monotonic"]["status"], "observed")
        self.assertFalse(evidence["policy"]["network_request_performed"])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
