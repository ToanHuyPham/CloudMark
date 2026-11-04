from __future__ import annotations

import json
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout

from cloudmark.gpu_environment import (
    collect_gpu_environment,
    parse_nvidia_smi_inventory,
    parse_opencl_probe,
    parse_windows_cim_inventory,
)
from cloudmark.gpu_opencl_probe import main as opencl_probe_main
from cloudmark.inventory import collect_inventory
from cloudmark.profiles import ASSESSMENT_DOMAINS


class GpuEnvironmentTests(unittest.TestCase):
    @staticmethod
    def _device(
        drm_root: Path,
        card: int,
        *,
        vendor: str,
        device_id: str,
        driver: str,
        pci_address: str,
        vram_bytes: int | None = None,
    ) -> Path:
        device = drm_root / f"card{card}" / "device"
        device.mkdir(parents=True)
        (device / "vendor").write_text(f"{vendor}\n", encoding="utf-8")
        (device / "device").write_text(f"{device_id}\n", encoding="utf-8")
        (device / "subsystem_vendor").write_text(f"{vendor}\n", encoding="utf-8")
        (device / "subsystem_device").write_text("0x0001\n", encoding="utf-8")
        (device / "class").write_text("0x030000\n", encoding="utf-8")
        (device / "numa_node").write_text("0\n", encoding="utf-8")
        (device / "sriov_totalvfs").write_text("0\n", encoding="utf-8")
        (device / "sriov_numvfs").write_text("0\n", encoding="utf-8")
        (device / "uevent").write_text(
            f"DRIVER={driver}\nPCI_SLOT_NAME={pci_address}\nUNSAFE_IDENTIFIER=secret\n",
            encoding="utf-8",
        )
        (device / "iommu_group").mkdir()
        if vram_bytes is not None:
            (device / "mem_info_vram_total").write_text(f"{vram_bytes}\n", encoding="utf-8")
        return device

    @staticmethod
    def _without_tools(name: str) -> None:
        del name
        return None

    def test_collects_bounded_linux_gpu_identity_without_raw_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            drm_root = Path(directory) / "drm"
            drm_root.mkdir()
            self._device(
                drm_root,
                0,
                vendor="0x1002",
                device_id="0x744c",
                driver="amdgpu",
                pci_address="0000:41:00.0",
                vram_bytes=24 * 1024**3,
            )
            with patch("cloudmark.gpu_environment.platform.system", return_value="Linux"), patch(
                "cloudmark.gpu_environment.shutil.which",
                side_effect=self._without_tools,
            ):
                evidence = collect_gpu_environment(drm_root=drm_root, opencl_loader="")
        self.assertEqual(evidence["methodology_version"], "gpu-environment-v3")
        self.assertEqual(evidence["evidence_status"], "complete")
        self.assertEqual(evidence["detection_status"], "detected")
        self.assertEqual(evidence["device_count"], 1)
        device = evidence["devices"][0]
        self.assertEqual(device["vendor_name"], "AMD")
        self.assertEqual(device["driver"], "amdgpu")
        self.assertEqual(device["pci_address"], "0000:41:00.0")
        self.assertEqual(device["vram_total_bytes"], 24 * 1024**3)
        self.assertEqual(device["numa_node"], 0)
        self.assertTrue(device["iommu_group_visible"])
        self.assertFalse(evidence["policy"]["compute_performance_measured"])
        serialized = json.dumps(evidence)
        self.assertNotIn("UNSAFE_IDENTIFIER", serialized)
        self.assertNotIn("secret", serialized)
        self.assertFalse(evidence["policy"]["gpu_uuid_persisted"])

    def test_nvidia_management_query_enriches_name_vram_and_driver_without_uuid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            drm_root = Path(directory) / "drm"
            drm_root.mkdir()
            self._device(
                drm_root,
                0,
                vendor="0x10de",
                device_id="0x27b8",
                driver="nvidia",
                pci_address="0000:00:04.0",
            )
            completed = subprocess.CompletedProcess(
                [],
                0,
                "0, NVIDIA L4, 23034, 550.54.15, 00000000:00:04.0\n",
                "",
            )
            with patch("cloudmark.gpu_environment.platform.system", return_value="Linux"), patch(
                "cloudmark.gpu_environment.shutil.which",
                side_effect=lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None,
            ), patch("cloudmark.gpu_environment.subprocess.run", return_value=completed) as run:
                evidence = collect_gpu_environment(drm_root=drm_root, opencl_loader="")
        device = evidence["devices"][0]
        self.assertEqual(device["product_name"], "NVIDIA L4")
        self.assertEqual(device["driver_version"], "550.54.15")
        self.assertEqual(device["vram_total_bytes"], 23034 * 1024 * 1024)
        self.assertEqual(
            run.call_args.args[0],
            [
                "/usr/bin/nvidia-smi",
                "--query-gpu=index,name,memory.total,driver_version,pci.bus_id",
                "--format=csv,noheader,nounits",
            ],
        )
        self.assertNotIn("uuid", device)
        self.assertFalse(evidence["policy"]["gpu_uuid_persisted"])

    def test_nvidia_parser_rejects_malformed_oversized_and_unsafe_rows(self) -> None:
        self.assertEqual(parse_nvidia_smi_inventory("invalid"), [])
        self.assertEqual(
            parse_nvidia_smi_inventory("0, GPU, not-a-number, 550, 00000000:00:04.0"),
            [],
        )
        self.assertEqual(
            parse_nvidia_smi_inventory("0, GPU\x00name, 1024, 550, 00000000:00:04.0"),
            [],
        )
        self.assertEqual(parse_nvidia_smi_inventory("x" * 70_000), [])

    def test_complete_empty_linux_scan_is_not_detected_not_a_zero_score(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            drm_root = Path(directory) / "drm"
            drm_root.mkdir()
            with patch("cloudmark.gpu_environment.platform.system", return_value="Linux"), patch(
                "cloudmark.gpu_environment.shutil.which",
                side_effect=self._without_tools,
            ):
                evidence = collect_gpu_environment(drm_root=drm_root, opencl_loader="")
        self.assertEqual(evidence["evidence_status"], "complete")
        self.assertEqual(evidence["detection_status"], "not-detected")
        self.assertEqual(evidence["device_count"], 0)
        self.assertFalse(evidence["policy"]["physical_host_gpu_claim"])

    def test_truncated_or_malformed_linux_devices_remain_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            drm_root = Path(directory) / "drm"
            drm_root.mkdir()
            for index in range(17):
                device = self._device(
                    drm_root,
                    index,
                    vendor="0x8086",
                    device_id="0x56a0",
                    driver="i915",
                    pci_address=f"0000:{index:02x}:00.0",
                )
                if index == 0:
                    (device / "vendor").write_text("malformed\n", encoding="utf-8")
            with patch("cloudmark.gpu_environment.platform.system", return_value="Linux"), patch(
                "cloudmark.gpu_environment.shutil.which",
                side_effect=self._without_tools,
            ):
                evidence = collect_gpu_environment(drm_root=drm_root, opencl_loader="")
        self.assertEqual(evidence["evidence_status"], "partial")
        self.assertEqual(evidence["device_count"], 16)
        self.assertTrue(evidence["truncated"])
        self.assertEqual(evidence["devices"][0]["status"], "partial")

    def test_non_linux_is_explicitly_unavailable_and_inventory_attaches_contract(self) -> None:
        with patch("cloudmark.gpu_environment.platform.system", return_value="Darwin"), patch(
            "cloudmark.gpu_environment.shutil.which",
            side_effect=self._without_tools,
        ), patch("cloudmark.gpu_environment.subprocess.run") as run:
            unavailable = collect_gpu_environment(opencl_loader="")
        self.assertEqual(unavailable["platform"], "Darwin")
        self.assertEqual(unavailable["evidence_status"], "unavailable")
        self.assertEqual(unavailable["detection_status"], "unavailable")
        run.assert_not_called()

        fixture = {
            **unavailable,
            "platform": "Linux",
            "evidence_status": "complete",
            "detection_status": "not-detected",
        }
        with patch("cloudmark.inventory.collect_gpu_environment", return_value=fixture):
            inventory = collect_inventory(Path.cwd())
        self.assertEqual(inventory["gpu"]["environment"], fixture)
        self.assertTrue(inventory["capabilities"]["gpu_environment_linux"])
        self.assertTrue(inventory["capabilities"]["gpu_environment"])
        domains = {item["id"]: item["status"] for item in ASSESSMENT_DOMAINS}
        self.assertEqual(domains["gpu"], "partial")

    def test_windows_cim_query_normalizes_pci_identity_and_redacts_pnp_instance(self) -> None:
        pnp_identifier = "PCI\\VEN_10DE&DEV_27B8&SUBSYS_16DA10DE&REV_A1\\SECRET-INSTANCE"
        payload = json.dumps(
            [
                {
                    "Name": "NVIDIA L4",
                    "DriverVersion": "32.0.15.5054",
                    "AdapterRAM": 4_293_918_720,
                    "PNPDeviceID": pnp_identifier,
                }
            ]
        )
        completed = subprocess.CompletedProcess([], 0, payload, "")
        with patch("cloudmark.gpu_environment.platform.system", return_value="Windows"), patch(
            "cloudmark.gpu_environment.shutil.which",
            side_effect=self._without_tools,
        ), patch("cloudmark.gpu_environment.subprocess.run", return_value=completed) as run:
            evidence = collect_gpu_environment(powershell="powershell.exe", opencl_loader="")
        self.assertEqual(evidence["evidence_status"], "complete")
        self.assertEqual(evidence["detection_status"], "detected")
        device = evidence["devices"][0]
        self.assertEqual(device["vendor_name"], "NVIDIA")
        self.assertEqual(device["vendor_id"], "0x10de")
        self.assertEqual(device["device_id"], "0x27b8")
        self.assertEqual(device["subsystem_vendor_id"], "0x10de")
        self.assertEqual(device["subsystem_device_id"], "0x16da")
        self.assertEqual(device["product_name"], "NVIDIA L4")
        self.assertEqual(device["driver_version"], "32.0.15.5054")
        self.assertEqual(device["memory_capacity_source"], "windows-cim-adapterram-uint32")
        self.assertTrue(device["memory_capacity_may_be_truncated"])
        self.assertTrue(evidence["policy"]["windows_cim_queried"])
        self.assertFalse(evidence["policy"]["windows_pnp_instance_identifier_persisted"])
        self.assertNotIn("SECRET-INSTANCE", json.dumps(evidence))
        command = run.call_args.args[0]
        self.assertEqual(command[:5], ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command"])
        self.assertIn("Get-CimInstance Win32_VideoController", command[-1])

    def test_windows_parser_bounds_shape_capacity_and_labels(self) -> None:
        self.assertEqual(parse_windows_cim_inventory("{}"), None)
        self.assertEqual(parse_windows_cim_inventory("not-json"), None)
        self.assertEqual(parse_windows_cim_inventory("x" * 70_000), None)
        self.assertEqual(parse_windows_cim_inventory(json.dumps([{}] * 17)), None)
        malformed_capacity = parse_windows_cim_inventory(
            json.dumps(
                [
                    {
                        "Name": "Virtual Adapter",
                        "DriverVersion": "1.0",
                        "AdapterRAM": 2**40,
                        "PNPDeviceID": "ROOT\\BASICDISPLAY",
                    }
                ]
            )
        )
        self.assertIsNotNone(malformed_capacity)
        assert malformed_capacity is not None
        self.assertIsNone(malformed_capacity[0]["vram_total_bytes"])
        self.assertEqual(malformed_capacity[0]["vendor_name"], "Unknown")
        self.assertFalse(malformed_capacity[0]["memory_capacity_may_be_truncated"])

    def test_windows_query_failure_and_empty_inventory_are_distinct(self) -> None:
        with patch("cloudmark.gpu_environment.platform.system", return_value="Windows"), patch(
            "cloudmark.gpu_environment.shutil.which",
            side_effect=self._without_tools,
        ), patch(
            "cloudmark.gpu_environment.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, "", "failure"),
        ):
            failed = collect_gpu_environment(powershell="powershell.exe", opencl_loader="")
        self.assertEqual(failed["evidence_status"], "unavailable")
        self.assertEqual(failed["detection_status"], "unavailable")

        with patch("cloudmark.gpu_environment.platform.system", return_value="Windows"), patch(
            "cloudmark.gpu_environment.shutil.which",
            side_effect=self._without_tools,
        ), patch(
            "cloudmark.gpu_environment.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "[]", "Access denied"),
        ):
            misleading_empty = collect_gpu_environment(powershell="powershell.exe", opencl_loader="")
        self.assertEqual(misleading_empty["evidence_status"], "unavailable")
        self.assertEqual(misleading_empty["detection_status"], "unavailable")

        with patch("cloudmark.gpu_environment.platform.system", return_value="Windows"), patch(
            "cloudmark.gpu_environment.shutil.which",
            side_effect=self._without_tools,
        ), patch(
            "cloudmark.gpu_environment.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "[]", ""),
        ):
            empty = collect_gpu_environment(powershell="powershell.exe", opencl_loader="")
        self.assertEqual(empty["evidence_status"], "complete")
        self.assertEqual(empty["detection_status"], "not-detected")
        self.assertEqual(empty["device_count"], 0)

    @staticmethod
    def _opencl_payload() -> dict[str, object]:
        return {
            "methodology_version": "gpu-opencl-probe-v1",
            "status": "complete",
            "platform_count": 1,
            "device_count": 1,
            "platforms": [
                {
                    "index": 0,
                    "status": "observed",
                    "name": "Test OpenCL",
                    "vendor": "Test Vendor",
                    "version": "OpenCL 3.0",
                }
            ],
            "devices": [
                {
                    "index": 0,
                    "platform_index": 0,
                    "platform_device_index": 0,
                    "status": "observed",
                    "name": "Test GPU",
                    "vendor": "Test Vendor",
                    "driver_version": "1.2.3",
                    "device_version": "OpenCL 3.0 Test",
                    "opencl_c_version": "OpenCL C 1.2",
                    "global_memory_bytes": 8 * 1024**3,
                    "max_allocation_bytes": 2 * 1024**3,
                    "address_bits": 64,
                    "available": True,
                    "compiler_available": True,
                    "unified_memory": False,
                }
            ],
            "policy": {
                "read_only": True,
                "context_created": False,
                "queue_created": False,
                "buffer_allocated": False,
                "kernel_compiled": False,
                "load_generated": False,
                "extension_list_persisted": False,
            },
        }

    def test_opencl_probe_parser_enforces_bounded_no_load_contract(self) -> None:
        parsed = parse_opencl_probe(json.dumps(self._opencl_payload()))
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["device_count"], 1)
        self.assertEqual(parsed["devices"][0]["global_memory_bytes"], 8 * 1024**3)
        self.assertFalse(parsed["policy"]["context_created"])
        self.assertFalse(parsed["policy"]["load_generated"])

        wrong_policy = self._opencl_payload()
        wrong_policy["policy"] = {**wrong_policy["policy"], "buffer_allocated": True}
        self.assertIsNone(parse_opencl_probe(json.dumps(wrong_policy)))
        wrong_count = self._opencl_payload()
        wrong_count["device_count"] = 2
        self.assertIsNone(parse_opencl_probe(json.dumps(wrong_count)))
        impossible_allocation = self._opencl_payload()
        impossible_allocation["devices"][0]["max_allocation_bytes"] = 16 * 1024**3
        self.assertIsNone(parse_opencl_probe(json.dumps(impossible_allocation)))
        false_status = self._opencl_payload()
        false_status["status"] = "partial"
        self.assertIsNone(parse_opencl_probe(json.dumps(false_status)))
        self.assertIsNone(parse_opencl_probe("x" * 70_000))

    def test_opencl_probe_runs_in_fixed_subprocess_and_attaches_inventory(self) -> None:
        completed = subprocess.CompletedProcess([], 0, json.dumps(self._opencl_payload()), "")
        with patch("cloudmark.gpu_environment.platform.system", return_value="Darwin"), patch(
            "cloudmark.gpu_environment.shutil.which",
            side_effect=self._without_tools,
        ), patch("cloudmark.gpu_environment.subprocess.run", return_value=completed) as run:
            evidence = collect_gpu_environment(opencl_loader="OpenCL")
        self.assertEqual(evidence["opencl"]["status"], "complete")
        self.assertEqual(evidence["opencl"]["device_count"], 1)
        self.assertTrue(evidence["runtime_tools"]["opencl_loader"])
        self.assertEqual(run.call_args.args[0][1:], ["-m", "cloudmark.gpu_opencl_probe"])
        self.assertTrue(evidence["policy"]["opencl_probe_subprocess_isolated"])

    def test_opencl_probe_failure_is_unavailable_and_worker_output_is_json(self) -> None:
        with patch("cloudmark.gpu_environment.platform.system", return_value="Darwin"), patch(
            "cloudmark.gpu_environment.shutil.which",
            side_effect=self._without_tools,
        ), patch(
            "cloudmark.gpu_environment.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "{}", ""),
        ):
            evidence = collect_gpu_environment(opencl_loader="OpenCL")
        self.assertEqual(evidence["opencl"]["status"], "unavailable")
        self.assertEqual(evidence["opencl"]["reason_code"], "opencl-probe-unavailable")

        output = io.StringIO()
        fixture = self._opencl_payload()
        with patch("cloudmark.gpu_opencl_probe.collect_opencl_probe", return_value=fixture), redirect_stdout(output):
            opencl_probe_main()
        self.assertEqual(json.loads(output.getvalue()), fixture)


if __name__ == "__main__":
    unittest.main()
