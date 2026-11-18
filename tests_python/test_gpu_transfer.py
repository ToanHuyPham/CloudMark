import copy
import io
import json
import hashlib
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from cloudmark.__main__ import main
from cloudmark.gpu_transfer import BUFFER_BYTES, ITERATIONS, plan_gpu_transfer, validate_transfer_result
from tests_python import test_gpu_environment as gpu_fixtures


class GpuTransferPlanTests(unittest.TestCase):
    def test_result_validation_rederives_rates_and_rejects_forged_contracts(self):
        plan = plan_gpu_transfer(gpu_fixtures.GpuEnvironmentTests._opencl_payload(), 0)
        digest = hashlib.sha256(hashlib.shake_256(b"cloudmark-gpu-transfer-v1").digest(BUFFER_BYTES)).hexdigest()
        payload = {
            "methodology_version": "gpu-transfer-v1", "plan": plan,
            "host_available_bytes": 1024**3, "cleanup_verified": True,
            "measurements": [{"name": name, "bytes_per_iteration": BUFFER_BYTES, "iterations": ITERATIONS,
                              "sha256": digest, "integrity_verified": True, "samples_ns": [1000000] * ITERATIONS,
                              "bandwidth_bytes_per_second": -1} for name in plan["operations"]],
        }
        result = validate_transfer_result(payload, 0)
        self.assertEqual(result["measurements"][0]["bandwidth_bytes_per_second"], BUFFER_BYTES * 1000)
        self.assertEqual(payload["measurements"][0]["bandwidth_bytes_per_second"], -1)
        for field, value in (("device_allocation_bytes", 1), ("interpretation", "fabricated"),
                             ("host_headroom_verified", True)):
            invalid = copy.deepcopy(payload)
            invalid["plan"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_transfer_result(invalid, 0)
        invalid = copy.deepcopy(payload)
        invalid["plan"]["device"]["available"] = False
        with self.assertRaises(ValueError):
            validate_transfer_result(invalid, 0)
        invalid = copy.deepcopy(payload)
        for measurement in invalid["measurements"]:
            measurement["samples_ns"] = [10**10] * ITERATIONS
        with self.assertRaises(ValueError):
            validate_transfer_result(invalid, 0)

    def test_fixed_plan_has_no_execution_or_free_memory_claim(self):
        probe = gpu_fixtures.GpuEnvironmentTests._opencl_payload()
        original = copy.deepcopy(probe)
        plan = plan_gpu_transfer(probe, 0)
        self.assertEqual(plan["device_allocation_bytes"], 2 * BUFFER_BYTES)
        self.assertFalse(plan["executor_available"])
        self.assertFalse(plan["device_free_memory_verified"])
        self.assertFalse(plan["policy"]["execution_authorized"])
        self.assertEqual(probe, original)
        probe["devices"][0]["unified_memory"] = True
        self.assertEqual(plan_gpu_transfer(probe, 0)["interpretation"], "shared-memory-copy")

    def test_rejects_ambiguous_selection_and_incomplete_capacity(self):
        for index in (True, -1, 16, 1, "0"):
            with self.subTest(index=index), self.assertRaises(ValueError):
                plan_gpu_transfer(gpu_fixtures.GpuEnvironmentTests._opencl_payload(), index)
        for field, value in (
            ("available", False), ("unified_memory", None), ("name", None),
            ("global_memory_bytes", 64 * 1024**2), ("max_allocation_bytes", BUFFER_BYTES - 1),
            ("global_memory_bytes", True), ("address_bits", 16),
        ):
            probe = gpu_fixtures.GpuEnvironmentTests._opencl_payload()
            probe["devices"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                plan_gpu_transfer(probe, 0)

    def test_cli_only_probes_and_reports_plan(self):
        output = io.StringIO()
        with patch("sys.argv", ["cloudmark", "gpu-transfer-plan", "--device-index", "0"]), patch(
            "cloudmark.__main__._opencl_probe", return_value=gpu_fixtures.GpuEnvironmentTests._opencl_payload()
        ), redirect_stdout(output):
            main()
        result = json.loads(output.getvalue())
        self.assertEqual(result["evidence_status"], "planned-not-measured")
        self.assertFalse(result["policy"]["load_generated"])


if __name__ == "__main__":
    unittest.main()
