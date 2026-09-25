from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

import cloudmark.bootstrap as bootstrap
from cloudmark.bootstrap import BootstrapPlan, create_plan, detect_manager, execute_plan


class BootstrapTests(unittest.TestCase):
    def test_detect_manager_uses_supported_platform_order(self) -> None:
        with patch.object(bootstrap.os, "name", "nt"), patch.object(
            bootstrap.shutil,
            "which",
            return_value="C:/Program Files/winget.exe",
        ):
            self.assertEqual(detect_manager(), "winget")

        with patch.object(bootstrap.os, "name", "nt"), patch.object(
            bootstrap.shutil,
            "which",
            return_value=None,
        ):
            self.assertEqual(detect_manager(), "windows-bundle")

        scenarios = (
            ({"apt-get": "/usr/bin/apt-get", "zypper": "/usr/bin/zypper"}, "apt"),
            ({"zypper": "/usr/bin/zypper", "dnf": "/usr/bin/dnf"}, "zypper"),
            ({"dnf": "/usr/bin/dnf", "yum": "/usr/bin/yum"}, "dnf"),
            ({"yum": "/usr/bin/yum"}, "dnf"),
            ({}, "unsupported"),
        )
        for available, expected in scenarios:
            with self.subTest(expected=expected, available=available), patch.object(
                bootstrap.os,
                "name",
                "posix",
            ), patch.object(bootstrap.shutil, "which", side_effect=available.get):
                self.assertEqual(detect_manager(), expected)

    def test_create_plan_deduplicates_apt_packs_and_packages(self) -> None:
        with patch.object(bootstrap, "detect_manager", return_value="apt"):
            plan = create_plan(["storage", "storage", "network"])

        self.assertEqual(plan.packs, ["base", "storage", "network"])
        self.assertEqual(len(plan.packages), len(set(plan.packages)))
        self.assertEqual(plan.commands[0], ["apt-get", "update"])
        self.assertEqual(plan.commands[1], ["apt-get", "install", "-y", *plan.packages])
        self.assertTrue(plan.requires_admin)
        self.assertEqual(plan.notes, [])
        self.assertEqual(
            plan.as_dict(),
            {
                "manager": "apt",
                "packs": plan.packs,
                "packages": plan.packages,
                "commands": plan.commands,
                "requires_admin": True,
                "notes": [],
            },
        )

    def test_create_plan_builds_dnf_and_zypper_commands(self) -> None:
        with patch.object(bootstrap, "detect_manager", return_value="dnf"):
            dnf = create_plan(["compute", "database"])
        self.assertEqual(dnf.commands, [["dnf", "install", "-y", *dnf.packages]])
        self.assertEqual(dnf.packages.count("sysbench"), 1)

        with patch.object(bootstrap, "detect_manager", return_value="zypper"):
            zypper = create_plan(["storage"])
        self.assertEqual(zypper.commands[0], ["zypper", "--non-interactive", "refresh"])
        self.assertEqual(
            zypper.commands[1],
            ["zypper", "--non-interactive", "install", *zypper.packages],
        )
        self.assertIn("SUSE registration", zypper.notes[0])

    def test_create_plan_rejects_unknown_packs_and_labels_manual_platforms(self) -> None:
        with patch.object(bootstrap, "detect_manager", return_value="apt"):
            with self.assertRaisesRegex(ValueError, "Unknown bootstrap pack: destructive"):
                create_plan(["destructive"])

        for manager, note_fragment in (
            ("winget", "not yet automatic"),
            ("windows-bundle", "signed CloudMark Windows tool bundle"),
            ("unsupported", "No supported package manager detected"),
        ):
            with self.subTest(manager=manager), patch.object(
                bootstrap,
                "detect_manager",
                return_value=manager,
            ), patch.object(bootstrap.platform, "platform", return_value="TestOS"):
                plan = create_plan(["storage"])
            self.assertEqual(plan.commands, [])
            self.assertIn(note_fragment, plan.notes[0])

    def test_execute_plan_rejects_empty_plans_and_non_root_linux(self) -> None:
        empty = BootstrapPlan("unsupported", ["base"], [], [], True, ["manual installation required"])
        with patch.object(bootstrap.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "no executable package-manager commands"):
                execute_plan(empty)
        run.assert_not_called()

        plan = BootstrapPlan("apt", ["base"], ["curl"], [["apt-get", "update"]], True, [])
        with patch.object(bootstrap.os, "name", "posix"), patch.object(
            bootstrap.os,
            "geteuid",
            return_value=1000,
            create=True,
        ), patch.object(bootstrap.subprocess, "run") as run:
            with self.assertRaisesRegex(PermissionError, "requires root"):
                execute_plan(plan)
        run.assert_not_called()

    def test_execute_plan_uses_argument_arrays_and_stops_on_failure(self) -> None:
        commands = [["manager", "refresh"], ["manager", "install", "tool"], ["manager", "verify"]]
        plan = BootstrapPlan("test", ["base"], ["tool"], commands, True, [])
        with patch.object(bootstrap.os, "name", "nt"), patch.object(
            bootstrap.subprocess,
            "run",
            side_effect=[SimpleNamespace(returncode=0), SimpleNamespace(returncode=17)],
        ) as run:
            with self.assertRaisesRegex(RuntimeError, "manager install tool"):
                execute_plan(plan)
        self.assertEqual(
            run.call_args_list,
            [call(commands[0], check=False), call(commands[1], check=False)],
        )

        successful = BootstrapPlan("test", ["base"], ["tool"], [commands[0]], True, [])
        with patch.object(bootstrap.os, "name", "nt"), patch.object(
            bootstrap.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0),
        ):
            self.assertEqual(
                execute_plan(successful),
                [{"command": commands[0], "returncode": 0}],
            )


if __name__ == "__main__":
    unittest.main()
