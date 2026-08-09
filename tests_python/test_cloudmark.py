from __future__ import annotations

import json
import tempfile
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from cloudmark.agent import AgentWorker, join_session
from cloudmark.benchmarks import _metrics, _parse_fio_log, run_storage
from cloudmark.bootstrap import create_plan
from cloudmark.database import Database
from cloudmark.inventory import collect_inventory
from cloudmark.network import NetworkError, run_network, validate_network_run
from cloudmark.profiles import ASSESSMENT_DOMAINS, NETWORK_PROFILES, SCENARIOS, STORAGE_PROFILES
from cloudmark.provider import _declared_manifest
from cloudmark.runner import CancellationToken, JobContext, ProcessResult, RunCancelled, RunTimedOut
from cloudmark.server import CloudMarkController, Server


class CloudMarkTests(unittest.TestCase):
    def test_storage_metrics_keep_tail_latency_percentiles(self) -> None:
        section = {
            "iops": 123,
            "bw_bytes": 456,
            "clat_ns": {
                "percentile": {
                    "50.000000": 1_000_000,
                    "90.000000": 2_000_000,
                    "95.000000": 3_000_000,
                    "99.000000": 4_000_000,
                    "99.900000": 5_000_000,
                }
            },
        }
        result = _metrics({"read": section, "write": {}}, {"name": "sample"})
        self.assertEqual(result["read"]["p50_ms"], 1.0)
        self.assertEqual(result["read"]["p90_ms"], 2.0)
        self.assertEqual(result["read"]["p999_ms"], 5.0)

    def test_inventory_has_core_sections(self) -> None:
        inventory = collect_inventory(Path.cwd())
        self.assertIn("cpu", inventory)
        self.assertIn("memory", inventory)
        self.assertIn("disks", inventory)
        self.assertGreaterEqual(inventory["cpu"]["logical_cores"], 1)

    def test_database_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.create_run("run_test", "inventory", "default", {"suite": "inventory"})
            database.update_run("run_test", status="completed", result={"ok": True})
            run = database.get_run("run_test")
            self.assertIsNotNone(run)
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["result"], {"ok": True})

    def test_database_tracks_progress_and_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.create_run(
                "run_progress",
                "storage",
                "disk-quick",
                {"suite": "storage"},
                total_steps=6,
                runner_version="1.0",
                methodology_version="storage-v1",
            )
            database.update_run("run_progress", status="running", phase="starting")
            database.update_run_progress(
                "run_progress",
                progress=0.5,
                phase="benchmarking",
                current_job="random-read-qd1",
                completed_steps=3,
                total_steps=6,
                result={"jobs": [{"name": "sequential-read"}]},
            )
            self.assertTrue(database.request_cancel("run_progress"))
            run = database.get_run("run_progress")
            self.assertEqual(run["phase"], "benchmarking")
            self.assertEqual(run["completed_steps"], 3)
            self.assertEqual(run["progress"], 0.5)
            self.assertTrue(run["cancel_requested"])
            self.assertEqual(run["result"]["jobs"][0]["name"], "sequential-read")

    def test_database_recovers_interrupted_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.create_run("run_stale", "inventory", "default", {"suite": "inventory"})
            self.assertEqual(database.recover_incomplete_runs(), 1)
            run = database.get_run("run_stale")
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["phase"], "interrupted")

    def test_pairing_is_ready_only_after_two_agents_join(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.create_session("session_test", "pair", "hash", "2099-01-01T00:00:00+00:00")
            database.add_agent("agent_a", "session_test", "a", "target", {})
            self.assertEqual(database.get_session("session_test")["status"], "waiting")
            database.add_agent("agent_b", "session_test", "b", "generator", {})
            self.assertEqual(database.get_session("session_test")["status"], "ready")

    def test_agent_task_queue_is_scoped_and_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.create_session("session_test", "pair", "join-hash", "2099-01-01T00:00:00+00:00")
            database.add_agent("agent_a", "session_test", "a", "target", {}, "agent-hash", {"address": "10.0.0.10"})
            database.create_run("run_network", "network", "network-peer-quick", {"suite": "network"})
            database.create_agent_task(
                "task_test",
                "run_network",
                "session_test",
                "agent_a",
                "network-server-start",
                {"port": 5201},
            )
            task = database.claim_agent_task("agent_a")
            self.assertEqual(task["kind"], "network-server-start")
            self.assertEqual(task["payload"], {"port": 5201})
            self.assertTrue(database.finish_agent_task("task_test", "agent_a", status="completed", result={"ready": True}))
            self.assertEqual(database.get_agent_task("task_test")["result"], {"ready": True})
            self.assertFalse(database.authenticate_agent("agent_a", "wrong-hash"))
            self.assertTrue(database.authenticate_agent("agent_a", "agent-hash"))
            database.create_agent_task(
                "task_cancel",
                "run_network",
                "session_test",
                "agent_a",
                "network-client",
                {"target_address": "10.0.0.11"},
            )
            self.assertEqual(database.cancel_queued_run_tasks("run_network"), 1)
            self.assertIsNone(database.claim_agent_task("agent_a"))

    def test_network_run_requires_roles_capability_and_peer_addresses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.create_session("session_test", "pair", "hash", "2099-01-01T00:00:00+00:00")
            system = {"inventory": {"capabilities": {"iperf3": True}}}
            database.add_agent("agent_a", "session_test", "a", "target", system, endpoint={"address": "10.0.0.10"})
            with self.assertRaises(ValueError):
                validate_network_run(database, "session_test", "network-peer-quick")
            database.add_agent("agent_b", "session_test", "b", "generator", system, endpoint={"address": "10.0.0.11"})
            session, target, generator = validate_network_run(database, "session_test", "network-peer-quick")
            self.assertEqual(session["status"], "ready")
            self.assertEqual(target["role"], "target")
            self.assertEqual(generator["role"], "generator")

    def test_agent_refuses_loopback_network_destination(self) -> None:
        worker = AgentWorker("http://127.0.0.1:8787", "agent", "token")
        with self.assertRaises(NetworkError):
            worker._run_client({"target_address": "127.0.0.1", "port": 5201, "duration_seconds": 1, "streams": 1})

    def test_network_orchestrator_records_both_directions_without_controller_traffic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.create_session("session_test", "pair", "hash", "2099-01-01T00:00:00+00:00")
            system = {"inventory": {"capabilities": {"iperf3": True}}}
            database.add_agent("agent_a", "session_test", "target", "target", system, endpoint={"address": "10.0.0.10"})
            database.add_agent("agent_b", "session_test", "generator", "generator", system, endpoint={"address": "10.0.0.11"})
            database.create_run("run_network", "network", "network-peer-quick", {"suite": "network"}, total_steps=4)
            done = threading.Event()

            def complete_tasks() -> None:
                iperf = {
                    "start": {"version": "iperf 3.17"},
                    "end": {
                        "sum_sent": {"bits_per_second": 1_100_000_000, "bytes": 1_000_000, "retransmits": 2},
                        "sum_received": {"bits_per_second": 1_000_000_000, "bytes": 990_000},
                    },
                }
                while not done.is_set():
                    handled = False
                    for agent_id in ("agent_a", "agent_b"):
                        task = database.claim_agent_task(agent_id)
                        if not task:
                            continue
                        handled = True
                        result = {"ready": True} if task["kind"] == "network-server-start" else {"iperf": iperf}
                        database.finish_agent_task(task["id"], agent_id, status="completed", result=result)
                    if not handled:
                        time.sleep(0.01)

            worker = threading.Thread(target=complete_tasks, daemon=True)
            worker.start()
            try:
                context = JobContext("run_network", total_steps=4, timeout_seconds=20)
                result = run_network(
                    database,
                    "run_network",
                    "session_test",
                    "network-peer-quick",
                    context=context,
                )
            finally:
                done.set()
                worker.join(timeout=2)
            self.assertEqual(len(result["measurements"]), 4)
            self.assertEqual({item["sender"]["id"] for item in result["measurements"]}, {"agent_a", "agent_b"})
            self.assertFalse(result["policy"]["controller_in_data_path"])
            self.assertEqual(result["tool"]["version"], "iperf 3.17")

    def test_profiles_enforce_network_direction_policy(self) -> None:
        self.assertIn("disk-quick", STORAGE_PROFILES)
        self.assertIn("disk-database", STORAGE_PROFILES)
        self.assertIn("disk-throughput", STORAGE_PROFILES)
        self.assertIn("disk-sustained", STORAGE_PROFILES)
        self.assertTrue(all(profile["methodology_version"] == "storage-v1" for profile in STORAGE_PROFILES.values()))
        profile = NETWORK_PROFILES["network-peer-standard"]
        self.assertFalse(profile["cloud_to_controller"])
        self.assertEqual(profile["requires_agents"], 2)

    def test_scenario_coverage_does_not_overstate_executors(self) -> None:
        statuses = {scenario["id"]: scenario["status"] for scenario in SCENARIOS}
        self.assertEqual(statuses["storage-backup"], "available")
        self.assertEqual(statuses["database"], "partial")
        self.assertEqual(statuses["network"], "partial")
        self.assertEqual(statuses["web-app"], "roadmap")

    def test_assessment_catalog_covers_full_infrastructure_stack(self) -> None:
        domains = {domain["id"]: domain["status"] for domain in ASSESSMENT_DOMAINS}
        self.assertGreaterEqual(len(domains), 15)
        self.assertTrue({"compute", "memory", "storage", "network", "gpu", "web", "database"}.issubset(domains))
        self.assertTrue({"containers", "security", "reliability", "observability", "control-plane", "cost", "consistency"}.issubset(domains))
        self.assertEqual(domains["storage"], "available")
        self.assertEqual(domains["network"], "partial")
        self.assertEqual(domains["reliability"], "roadmap")

    def test_bootstrap_includes_base_pack(self) -> None:
        plan = create_plan(["storage"])
        self.assertEqual(plan.packs[0], "base")
        self.assertIn("storage", plan.packs)

    def test_declared_provider_manifest_is_labelled_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "provider.json"
            manifest.write_text('{"provider":"Regional Cloud","region":"hcm-1"}', encoding="utf-8")
            with patch.dict("os.environ", {"CLOUDMARK_PROVIDER_MANIFEST": str(manifest)}):
                detected = _declared_manifest()
            self.assertIsNotNone(detected)
            self.assertEqual(detected["provider"], "Regional Cloud")
            self.assertIn("unverified", detected["source"].lower())

    def test_remote_agent_join_requires_https_by_default(self) -> None:
        with self.assertRaises(ValueError):
            join_session("http://198.51.100.10:8787", "session", "token", "peer")

    def test_runner_cancels_an_active_process(self) -> None:
        token = CancellationToken()
        context = JobContext("run_cancel", total_steps=1, timeout_seconds=10, token=token)
        timer = threading.Timer(0.2, token.cancel)
        started = time.monotonic()
        timer.start()
        try:
            with self.assertRaises(RunCancelled):
                context.run_process(
                    [
                        sys.executable,
                        "-c",
                        "import time; time.sleep(5)",
                    ],
                    label="cancellable-test",
                )
        finally:
            timer.cancel()
        self.assertLess(time.monotonic() - started, 3)

    def test_runner_enforces_overall_timeout(self) -> None:
        context = JobContext("run_timeout", total_steps=1, timeout_seconds=1)
        context.started -= 2
        with self.assertRaises(RunTimedOut):
            context.checkpoint()

    def test_controller_validates_timeout_and_accepts_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = CloudMarkController(Path(directory))
            with self.assertRaises(ValueError):
                controller.submit_run({"suite": "inventory", "timeout_seconds": 10})
            controller.database.create_run("run_cancel", "inventory", "default", {"suite": "inventory"})
            run = controller.cancel_run("run_cancel")
            self.assertTrue(run["cancel_requested"])

    def test_http_api_exposes_v030_dashboard_and_cancel_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = CloudMarkController(Path(directory))
            controller._inventory = {"hostname": "api-test"}
            controller._provider = {"provider": "Unknown", "confidence": 0, "source": "test"}
            controller.database.create_run("run_http", "inventory", "default", {"suite": "inventory"})
            server = Server(("127.0.0.1", 0), controller)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}/api/v1"
            try:
                with urllib.request.urlopen(f"{base}/dashboard", timeout=5) as response:
                    dashboard = json.load(response)
                self.assertEqual(dashboard["version"], "0.3.0")
                self.assertIn("disk-sustained", dashboard["profiles"]["storage"])
                self.assertIn("network-peer-quick", dashboard["profiles"]["network"])
                self.assertIn("sessions", dashboard)
                request = urllib.request.Request(
                    f"{base}/runs/run_http/cancel",
                    data=b"{}",
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "X-CloudMark-Token": controller.token,
                    },
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    cancelled = json.load(response)
                self.assertTrue(cancelled["cancel_requested"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_http_agent_queue_requires_its_own_credential(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = CloudMarkController(Path(directory))
            pairing = controller.create_session("agent api")
            joined = controller.join_session(
                pairing["id"],
                {
                    "join_token": pairing["join_token"],
                    "role": "target",
                    "name": "target-a",
                    "endpoint": {"address": "10.0.0.10"},
                    "system": {"inventory": {"capabilities": {"iperf3": True}}},
                },
            )
            controller.database.create_run("run_agent_api", "network", "network-peer-quick", {"suite": "network"})
            controller.database.create_agent_task(
                "task_agent_api",
                "run_agent_api",
                pairing["id"],
                joined["agent_id"],
                "network-server-start",
                {"port": 5201},
            )
            server = Server(("127.0.0.1", 0), controller)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/api/v1/agents/{joined['agent_id']}/tasks/next"
            try:
                rejected = urllib.request.Request(
                    url,
                    data=b"{}",
                    method="POST",
                    headers={"Content-Type": "application/json", "X-CloudMark-Agent-Token": "wrong"},
                )
                with self.assertRaises(urllib.error.HTTPError) as rejected_error:
                    urllib.request.urlopen(rejected, timeout=5)
                self.assertEqual(rejected_error.exception.code, 401)
                accepted = urllib.request.Request(
                    url,
                    data=b"{}",
                    method="POST",
                    headers={"Content-Type": "application/json", "X-CloudMark-Agent-Token": joined["agent_token"]},
                )
                with urllib.request.urlopen(accepted, timeout=5) as response:
                    payload = json.load(response)
                self.assertEqual(payload["task"]["id"], "task_agent_api")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_fio_time_series_parser_normalizes_units(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "sample_bw.1.log"
            log.write_text("1000, 2048, 0, 4096\n2000, 1024, 1, 4096\n", encoding="utf-8")
            points = _parse_fio_log(log, "bandwidth")
            self.assertEqual(points[0]["elapsed_ms"], 1000)
            self.assertEqual(points[0]["value"], 2_097_152)
            self.assertEqual(points[0]["direction"], "read")
            self.assertEqual(points[1]["direction"], "write")

    def test_storage_runner_persists_versioned_job_results_and_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            preflight = {
                "fio": "fio",
                "fio_version": "fio-3.39",
                "workspace": str(workspace),
                "file_size_bytes": 512 * 1024 * 1024,
                "free_bytes": 10 * 1024 * 1024 * 1024,
                "reserve_bytes": 1024 * 1024 * 1024,
                "estimated_seconds": 240,
                "default_timeout_seconds": 540,
                "job_count": 4,
                "profile_version": "1.1",
                "methodology_version": "storage-v1",
                "destructive": False,
                "raw_device": False,
            }
            fio_payload = json.dumps(
                {
                    "jobs": [
                        {
                            "job_runtime": 1000,
                            "read": {"iops": 100, "bw_bytes": 409600, "io_bytes": 409600},
                            "write": {},
                            "usr_cpu": 1.0,
                            "sys_cpu": 2.0,
                        }
                    ]
                }
            )
            process_results = [
                ProcessResult(("fio",), 0, "{}", "", 0.01),
                *[ProcessResult(("fio",), 0, fio_payload, "", 0.01) for _ in range(4)],
            ]
            updates: list[dict[str, object]] = []
            context = JobContext(
                "run_storage_test",
                total_steps=6,
                timeout_seconds=30,
                on_progress=updates.append,
            )
            with patch("cloudmark.benchmarks.storage_preflight", return_value=preflight), patch.object(
                context,
                "run_process",
                side_effect=process_results,
            ):
                result = run_storage("disk-quick", workspace, "run_storage_test", context=context)
            self.assertEqual(result["profile_version"], "1.1")
            self.assertEqual(result["methodology_version"], "storage-v1")
            self.assertEqual(result["tool"]["version"], "fio-3.39")
            self.assertEqual(len(result["jobs"]), 4)
            self.assertTrue(result["safety"]["test_file_removed"])
            self.assertEqual(context.completed_steps, 6)
            self.assertEqual(updates[-1]["progress"], 1.0)


if __name__ == "__main__":
    unittest.main()
