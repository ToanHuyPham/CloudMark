import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloudmark.server import CloudMarkController
from cloudmark.agent import AgentWorker
from cloudmark.remote import remote_default_timeout, remote_total_steps
from tests_python import test_gpu_environment as fixtures


class GpuDispatchTests(unittest.TestCase):
    def test_controller_confirmation_index_timeout_and_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = CloudMarkController(Path(directory))
            request = {"suite": "gpu", "profile": "gpu-transfer-quick", "device_index": 0}
            with patch("cloudmark.server._opencl_probe", return_value=fixtures.GpuEnvironmentTests._opencl_payload()), patch.object(
                controller, "_execute_run"
            ):
                for extra in ({}, {"confirm_load": "true"}, {"confirm_load": True, "device_index": True},
                              {"confirm_load": True, "timeout_seconds": 91}):
                    with self.subTest(extra=extra), self.assertRaises(ValueError):
                        controller.submit_run({**request, **extra})
                run = controller.submit_run({**request, "confirm_load": True})
                self.assertEqual(run["request"]["timeout_seconds"], 90)
                self.assertEqual(run["methodology_version"], "gpu-transfer-v1")
                with self.assertRaisesRegex(ValueError, "already"):
                    controller.submit_run({"suite": "compute", "profile": "compute-quick", "confirm_load": True})

    def test_agent_routes_gpu_with_confirmation_and_device_index(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = AgentWorker("http://127.0.0.1:8787", "agent", "token", workspace=Path(directory))
            task = {"id": "task_gpu", "run_id": "run_gpu", "kind": "benchmark-gpu", "payload": {
                "suite": "gpu", "profile": "gpu-transfer-quick", "device_index": 1,
                "load_confirmed": True, "protocol_version": "remote-agent-v1", "timeout_seconds": 90,
            }}
            with patch.object(worker, "_benchmark_evidence", return_value={}), patch.object(
                worker, "_api", return_value={"task_status": "running"}
            ), patch("cloudmark.agent.run_gpu_transfer", return_value={"suite": "gpu"}) as execute:
                result = worker._execute(task)
                self.assertEqual(result["benchmark"]["suite"], "gpu")
                self.assertEqual(execute.call_args.args, (1,))
                self.assertTrue(execute.call_args.kwargs["confirm_load"])
            task["payload"]["load_confirmed"] = False
            with self.assertRaisesRegex(ValueError, "load confirmation"):
                worker._execute(task)
        self.assertEqual(remote_default_timeout("gpu", "gpu-transfer-quick"), 90)
        self.assertEqual(remote_total_steps("gpu", "gpu-transfer-quick"), 1)
