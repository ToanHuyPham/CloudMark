from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cloudmark.__main__ as cli
from cloudmark.profiles import COMPUTE_PROFILES, STORAGE_PROFILES


class CloudMarkCliTests(unittest.TestCase):
    @staticmethod
    def invoke(*arguments: str) -> None:
        with patch.object(sys, "argv", ["cloudmark", *arguments]):
            cli.main()

    def test_version_and_inventory_emit_public_json(self) -> None:
        version_output = io.StringIO()
        with redirect_stdout(version_output), self.assertRaises(SystemExit) as stopped:
            self.invoke("--version")
        self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(version_output.getvalue().strip(), "0.5.0")

        output = io.StringIO()
        with patch.object(cli, "collect_inventory", return_value={"hostname": "test-host"}), patch.object(
            cli,
            "detect_provider",
            return_value={"provider": "Test Provider", "source": "test"},
        ), redirect_stdout(output):
            self.invoke("inventory")
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["inventory"]["hostname"], "test-host")
        self.assertEqual(payload["provider"]["provider"], "Test Provider")

    def test_doctor_and_bootstrap_keep_preview_separate_from_installation(self) -> None:
        plan = SimpleNamespace(as_dict=lambda: {"manager": "apt", "packs": ["base", "storage"]})
        with patch.object(cli, "create_plan", return_value=plan) as create, patch.object(
            cli,
            "execute_plan",
        ) as execute, patch.object(cli, "_print") as output:
            self.invoke("doctor", "--packs", "storage, storage")
            create.assert_called_once_with(["storage", "storage"])
            execute.assert_not_called()
            output.assert_called_once_with(plan.as_dict())

        with patch.object(cli, "create_plan", return_value=plan), patch.object(
            cli,
            "execute_plan",
            return_value=[{"command": ["apt-get", "update"], "returncode": 0}],
        ) as execute, patch.object(cli, "_print") as output:
            self.invoke("bootstrap", "--packs", "storage")
            execute.assert_not_called()
            self.assertEqual(output.call_count, 1)

        with patch.object(cli, "create_plan", return_value=plan), patch.object(
            cli,
            "execute_plan",
            return_value=[{"command": ["apt-get", "update"], "returncode": 0}],
        ) as execute, patch.object(cli, "_print") as output:
            self.invoke("bootstrap", "--packs", "storage", "--yes")
            execute.assert_called_once_with(plan)
            self.assertEqual(output.call_count, 2)

    def test_serve_join_and_agent_dispatch_exact_arguments(self) -> None:
        with patch.object(cli, "serve") as serve:
            self.invoke("serve", "--host", "127.0.0.2", "--port", "9876", "--data-dir", "runtime")
        serve.assert_called_once_with("127.0.0.2", 9876, Path("runtime"))

        with patch.object(cli, "join_session", return_value={"agent_id": "agent_test"}) as join, patch.object(
            cli,
            "_print",
        ) as output:
            self.invoke(
                "join",
                "--controller",
                "https://controller.example",
                "--session",
                "session_test",
                "--token",
                "synthetic-join-token",
                "--role",
                "peer",
                "--name",
                "diagnostic-peer",
                "--advertise-address",
                "192.0.2.10",
            )
        join.assert_called_once_with(
            "https://controller.example",
            "session_test",
            "synthetic-join-token",
            "peer",
            "diagnostic-peer",
            advertise_address="192.0.2.10",
            allow_http=False,
        )
        output.assert_called_once_with({"agent_id": "agent_test"})

        with patch.object(cli, "join_and_work") as worker:
            self.invoke(
                "agent",
                "--controller",
                "http://controller.internal",
                "--session",
                "session_test",
                "--token",
                "synthetic-agent-token",
                "--role",
                "target",
                "--workspace",
                "agent-workspace",
                "--allow-http",
            )
        worker.assert_called_once_with(
            "http://controller.internal",
            "session_test",
            "synthetic-agent-token",
            "target",
            None,
            advertise_address=None,
            allow_http=True,
            workspace=Path("agent-workspace"),
        )

    def test_run_requires_confirmation_and_rejects_zero_timeout(self) -> None:
        preflight = {"default_timeout_seconds": 60}
        with patch.object(cli, "system_preflight", return_value=preflight), patch.object(
            cli,
            "run_system_benchmark",
        ) as runner, patch.object(cli, "_print"):
            with self.assertRaisesRegex(SystemExit, "Add --yes"):
                self.invoke("run", "compute", "--profile", "compute-quick")
            runner.assert_not_called()

        with patch.object(cli, "system_preflight", return_value=preflight), patch.object(
            cli,
            "run_system_benchmark",
        ) as runner, patch.object(cli, "_print"):
            with self.assertRaisesRegex(SystemExit, "between 30 and 43200"):
                self.invoke(
                    "run",
                    "compute",
                    "--profile",
                    "compute-quick",
                    "--timeout-seconds",
                    "0",
                    "--yes",
                )
            runner.assert_not_called()

    def test_run_dispatches_compute_storage_and_read_only_security(self) -> None:
        stderr = io.StringIO()

        def compute_runner(suite, profile, workspace, run_id, *, context):
            self.assertEqual((suite, profile, workspace, run_id), ("compute", "compute-quick", Path("workspace"), "cli"))
            self.assertEqual(context.total_steps, len(COMPUTE_PROFILES["compute-quick"]["jobs"]))
            self.assertEqual(context.timeout_seconds, 90)
            context.complete_step("benchmarking", "integer-single")
            return {"suite": "compute", "profile": profile}

        with patch.object(cli, "system_preflight", return_value={"default_timeout_seconds": 60}), patch.object(
            cli,
            "run_system_benchmark",
            side_effect=compute_runner,
        ), patch.object(cli, "_print") as output, redirect_stderr(stderr):
            self.invoke(
                "run",
                "compute",
                "--profile",
                "compute-quick",
                "--workspace",
                "workspace",
                "--timeout-seconds",
                "90",
                "--yes",
            )
        self.assertEqual(output.call_count, 2)
        self.assertIn("integer-single", stderr.getvalue())

        def storage_runner(profile, workspace, run_id, *, context):
            self.assertEqual((profile, workspace, run_id), ("disk-quick", Path("workspace"), "cli"))
            self.assertEqual(context.total_steps, len(STORAGE_PROFILES["disk-quick"]["jobs"]) + 2)
            return {"suite": "storage", "profile": profile}

        with patch.object(cli, "storage_preflight", return_value={"default_timeout_seconds": 300}), patch.object(
            cli,
            "run_storage",
            side_effect=storage_runner,
        ), patch.object(cli, "_print"):
            self.invoke("run", "storage", "--profile", "disk-quick", "--workspace", "workspace", "--yes")

        def security_runner(profile, *, context):
            self.assertEqual(profile, "linux-security-posture")
            self.assertEqual(context.total_steps, 1)
            return {"suite": "security", "profile": profile}

        with patch.object(cli, "security_posture_preflight", return_value={"default_timeout_seconds": 30}), patch.object(
            cli,
            "run_security_posture",
            side_effect=security_runner,
        ), patch.object(cli, "_print"):
            self.invoke("run", "security", "--profile", "linux-security-posture")


if __name__ == "__main__":
    unittest.main()
