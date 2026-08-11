from __future__ import annotations

import json
import os
import tempfile
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cloudmark.agent import AgentBenchmarkFailure, AgentWorker, join_session
from cloudmark.benchmarks import _metrics, _parse_fio_log, run_storage
from cloudmark.bootstrap import create_plan
from cloudmark.compute import ComputeError, parse_sysbench_cpu, run_system_benchmark, system_preflight
from cloudmark.database import Database
from cloudmark.database_benchmark import (
    DatabaseBenchmarkError,
    database_total_steps,
    parse_pgbench_output,
    run_database,
    validate_database_run,
)
from cloudmark.distributed import DistributedError
from cloudmark.inventory import collect_inventory
from cloudmark.network import (
    ALLOWED_UDP_RATE_MAX,
    NetworkError,
    _iperf_metrics,
    _udp_metrics,
    network_total_steps,
    parse_ping_output,
    run_network,
    validate_network_run,
)
from cloudmark.profiles import (
    ASSESSMENT_DOMAINS,
    COMPUTE_PROFILES,
    DATABASE_PROFILES,
    MEMORY_PROFILES,
    NETWORK_PROFILES,
    SCENARIOS,
    STORAGE_PROFILES,
)
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

    def test_agent_task_progress_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.create_session("session_test", "remote", "hash", "2099-01-01T00:00:00+00:00")
            database.add_agent("agent_a", "session_test", "a", "target", {}, "agent-hash")
            database.create_run("run_remote", "compute", "compute-quick", {"suite": "compute"}, total_steps=3)
            database.create_agent_task(
                "task_remote",
                "run_remote",
                "session_test",
                "agent_a",
                "benchmark-compute",
                {"profile": "compute-quick"},
            )
            self.assertIsNotNone(database.claim_agent_task("agent_a"))
            updated = database.update_agent_task_progress(
                "task_remote",
                "agent_a",
                progress=1 / 3,
                phase="benchmarking",
                current_job="integer-all-cores",
                completed_steps=1,
                total_steps=3,
                result={"compute_jobs": [{"name": "integer-single"}]},
            )
            self.assertIsNotNone(updated)
            task = database.get_agent_task("task_remote")
            self.assertAlmostEqual(task["progress"], 1 / 3)
            self.assertEqual(task["current_job"], "integer-all-cores")
            self.assertEqual(task["result"]["compute_jobs"][0]["name"], "integer-single")

    def test_remote_agent_progress_receives_controller_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = CloudMarkController(Path(directory))
            controller.database.create_session("session_test", "remote", "hash", "2099-01-01T00:00:00+00:00")
            controller.database.add_agent("agent_a", "session_test", "a", "target", {}, "agent-hash")
            controller.database.create_run("run_remote", "compute", "compute-quick", {"suite": "compute"}, total_steps=3)
            controller.database.update_run("run_remote", status="running", phase="starting")
            controller.database.create_agent_task(
                "task_remote", "run_remote", "session_test", "agent_a", "benchmark-compute", {}
            )
            controller.database.claim_agent_task("agent_a")
            controller.database.request_cancel("run_remote")
            response = controller.progress_agent_task(
                "agent_a",
                "task_remote",
                {
                    "progress": 0.2,
                    "phase": "benchmarking",
                    "current_job": "integer-single",
                    "completed_steps": 0,
                    "total_steps": 3,
                },
            )
            self.assertTrue(response["cancel_requested"])

    def test_agent_executes_only_versioned_remote_benchmark_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            worker = AgentWorker(
                "http://127.0.0.1:8787",
                "agent_a",
                "token",
                workspace=Path(directory),
            )
            progress: list[dict[str, object]] = []

            def controller_reply(suffix: str, data: dict[str, object], **_: object) -> dict[str, object]:
                progress.append({"suffix": suffix, **data})
                return {"task_status": "running", "cancel_requested": False}

            def fake_compute(*_: object, context: JobContext, **__: object) -> dict[str, object]:
                context.report("benchmarking", "integer-single", partial_result={"compute_jobs": []})
                return {"suite": "compute", "tool": {"name": "sysbench", "version": "sysbench 1.0.20"}}

            task = {
                "id": "task_remote",
                "run_id": "run_remote",
                "kind": "benchmark-compute",
                "payload": {
                    "suite": "compute",
                    "profile": "compute-quick",
                    "timeout_seconds": 300,
                    "load_confirmed": True,
                    "protocol_version": "remote-agent-v1",
                },
            }
            with patch.object(worker, "_api", side_effect=controller_reply), patch.object(
                worker,
                "_benchmark_evidence",
                return_value={"inventory": {"hostname": "remote-a"}, "provider": {"provider": "Test"}},
            ), patch("cloudmark.agent.run_system_benchmark", side_effect=fake_compute):
                result = worker._execute(task)
            self.assertEqual(result["benchmark"]["suite"], "compute")
            self.assertEqual(result["evidence"]["inventory"]["hostname"], "remote-a")
            self.assertTrue(any(str(item["suffix"]).endswith("/progress") for item in progress))
            task["payload"] = {**task["payload"], "load_confirmed": False}
            with self.assertRaisesRegex(ValueError, "load confirmation"):
                worker._execute(task)

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

    def test_ping_parser_supports_linux_and_english_windows_summaries(self) -> None:
        linux = parse_ping_output(
            "20 packets transmitted, 19 received, 5% packet loss, time 1918ms\n"
            "rtt min/avg/max/mdev = 0.410/0.522/0.710/0.081 ms\n"
        )
        windows = parse_ping_output(
            "Packets: Sent = 20, Received = 18, Lost = 2 (10% loss),\n"
            "Minimum = 1ms, Maximum = 4ms, Average = 2ms\n"
        )
        self.assertEqual(linux["received"], 19)
        self.assertEqual(linux["average_ms"], 0.522)
        self.assertEqual(windows["loss_percent"], 10.0)
        self.assertEqual(windows["average_ms"], 2.0)

    def test_agent_builds_guarded_udp_and_bidirectional_commands(self) -> None:
        worker = AgentWorker("http://127.0.0.1:8787", "agent", "token")
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"start": {"version": "iperf 3.17"}, "end": {}}),
            stderr="",
        )
        with patch.object(worker, "_iperf", return_value="iperf3"), patch(
            "cloudmark.agent.subprocess.run", return_value=completed
        ) as run:
            worker._run_client(
                {
                    "target_address": "10.0.0.11",
                    "port": 5201,
                    "duration_seconds": 15,
                    "streams": 1,
                    "protocol": "udp",
                    "target_rate_bps": 250_000_000,
                }
            )
            udp_command = run.call_args.args[0]
            self.assertIn("--udp", udp_command)
            self.assertEqual(udp_command[udp_command.index("--bitrate") + 1], "250000000")
            worker._run_client(
                {
                    "target_address": "10.0.0.11",
                    "port": 5202,
                    "duration_seconds": 15,
                    "streams": 4,
                    "protocol": "tcp",
                    "bidirectional": True,
                }
            )
            self.assertIn("--bidir", run.call_args.args[0])
        with self.assertRaisesRegex(NetworkError, "UDP rate"):
            worker._run_client(
                {
                    "target_address": "10.0.0.11",
                    "port": 5201,
                    "streams": 1,
                    "protocol": "udp",
                    "target_rate_bps": ALLOWED_UDP_RATE_MAX + 1,
                }
            )

    def test_agent_builds_guarded_latency_command_and_parses_result(self) -> None:
        worker = AgentWorker("http://127.0.0.1:8787", "agent", "token")
        completed = SimpleNamespace(
            returncode=0,
            stdout=(
                "20 packets transmitted, 20 received, 0% packet loss, time 1911ms\n"
                "rtt min/avg/max/mdev = 0.300/0.450/0.800/0.100 ms\n"
            ),
            stderr="",
        )
        with patch("cloudmark.agent.shutil.which", return_value="ping"), patch(
            "cloudmark.agent.subprocess.run", return_value=completed
        ) as run:
            result = worker._run_latency(
                {"target_address": "10.0.0.11", "count": 20, "interval_ms": 100, "timeout_ms": 1000}
            )
        self.assertEqual(result["latency"]["average_ms"], 0.45)
        self.assertIn("10.0.0.11", run.call_args.args[0])

    def test_network_metric_normalization_keeps_tcp_rtt_udp_and_bidirectional_fields(self) -> None:
        payload = {
            "end": {
                "sum_sent": {"bits_per_second": 900, "bytes": 9000, "retransmits": 2},
                "sum_received": {
                    "bits_per_second": 850,
                    "bytes": 8500,
                    "jitter_ms": 0.35,
                    "lost_packets": 2,
                    "packets": 100,
                    "lost_percent": 2.0,
                    "out_of_order": 0,
                },
                "sum_sent_bidir_reverse": {"bits_per_second": 700, "bytes": 7000},
                "sum_received_bidir_reverse": {"bits_per_second": 650, "bytes": 6500},
                "streams": [{"sender": {"mean_rtt": 1250, "min_rtt": 900, "max_rtt": 1800}}],
            }
        }
        self.assertEqual(_iperf_metrics(payload)["tcp_rtt_mean_ms"], 1.25)
        self.assertEqual(_iperf_metrics(payload, reverse=True)["received_bits_per_second"], 650)
        self.assertEqual(_udp_metrics(payload, 1_000)["lost_percent"], 2.0)

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

    def test_standard_network_orchestrator_captures_all_v2_measurement_classes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.create_session("session_test", "pair", "hash", "2099-01-01T00:00:00+00:00")
            system = {"inventory": {"capabilities": {"iperf3": True}}}
            database.add_agent("agent_a", "session_test", "target", "target", system, endpoint={"address": "10.0.0.10"})
            database.add_agent("agent_b", "session_test", "generator", "generator", system, endpoint={"address": "10.0.0.11"})
            total_steps = network_total_steps("network-peer-standard")
            database.create_run(
                "run_network_v2",
                "network",
                "network-peer-standard",
                {"suite": "network"},
                total_steps=total_steps,
            )
            done = threading.Event()

            def complete_tasks() -> None:
                while not done.is_set():
                    handled = False
                    for agent_id in ("agent_a", "agent_b"):
                        task = database.claim_agent_task(agent_id)
                        if not task:
                            continue
                        handled = True
                        payload = task["payload"]
                        if task["kind"] == "network-server-start":
                            result = {"ready": True}
                        elif task["kind"] == "network-latency":
                            result = {
                                "latency": {
                                    "transmitted": 20,
                                    "received": 20,
                                    "loss_percent": 0.0,
                                    "minimum_ms": 0.3,
                                    "average_ms": 0.5,
                                    "maximum_ms": 0.9,
                                    "deviation_ms": 0.1,
                                },
                                "tool": {"name": "ping", "version": None},
                            }
                        else:
                            end = {
                                "sum_sent": {"bits_per_second": 1_100_000_000, "bytes": 1_000_000, "retransmits": 2},
                                "sum_received": {"bits_per_second": 1_000_000_000, "bytes": 990_000},
                                "streams": [{"sender": {"mean_rtt": 1000, "min_rtt": 700, "max_rtt": 1600}}],
                            }
                            if payload.get("protocol") == "udp":
                                end["sum_received"].update(
                                    {"jitter_ms": 0.4, "lost_packets": 1, "packets": 1000, "lost_percent": 0.1}
                                )
                            if payload.get("bidirectional"):
                                end.update(
                                    {
                                        "sum_sent_bidir_reverse": {"bits_per_second": 800_000_000, "bytes": 800_000},
                                        "sum_received_bidir_reverse": {"bits_per_second": 750_000_000, "bytes": 750_000},
                                    }
                                )
                            result = {"iperf": {"start": {"version": "iperf 3.17"}, "end": end}}
                        database.finish_agent_task(task["id"], agent_id, status="completed", result=result)
                    if not handled:
                        time.sleep(0.005)

            worker = threading.Thread(target=complete_tasks, daemon=True)
            worker.start()
            try:
                context = JobContext("run_network_v2", total_steps=total_steps, timeout_seconds=60)
                result = run_network(
                    database,
                    "run_network_v2",
                    "session_test",
                    "network-peer-standard",
                    context=context,
                )
            finally:
                done.set()
                worker.join(timeout=2)
            self.assertEqual(total_steps, 17)
            self.assertEqual(len(result["latency_measurements"]), 2)
            self.assertEqual(len(result["measurements"]), 8)
            self.assertEqual(len(result["udp_measurements"]), 6)
            self.assertEqual(len(result["bidirectional_measurements"]), 1)
            self.assertTrue(all(item["target_rate_bps"] <= ALLOWED_UDP_RATE_MAX for item in result["udp_measurements"]))
            self.assertFalse(result["policy"]["controller_in_data_path"])
            self.assertFalse(result["analysis"]["scored"])

    def test_profiles_enforce_network_direction_policy(self) -> None:
        self.assertIn("compute-quick", COMPUTE_PROFILES)
        self.assertIn("compute-standard", COMPUTE_PROFILES)
        self.assertIn("memory-quick", MEMORY_PROFILES)
        self.assertIn("memory-standard", MEMORY_PROFILES)
        self.assertTrue(all(profile["methodology_version"] == "compute-v1" for profile in COMPUTE_PROFILES.values()))
        self.assertTrue(all(profile["methodology_version"] == "memory-v1" for profile in MEMORY_PROFILES.values()))
        self.assertIn("disk-quick", STORAGE_PROFILES)
        self.assertIn("disk-database", STORAGE_PROFILES)
        self.assertIn("disk-throughput", STORAGE_PROFILES)
        self.assertIn("disk-sustained", STORAGE_PROFILES)
        self.assertTrue(all(profile["methodology_version"] == "storage-v1" for profile in STORAGE_PROFILES.values()))
        profile = NETWORK_PROFILES["network-peer-standard"]
        self.assertFalse(profile["cloud_to_controller"])
        self.assertEqual(profile["requires_agents"], 2)
        self.assertEqual(profile["methodology_version"], "network-v2")
        self.assertEqual(network_total_steps("network-peer-quick"), 4)
        self.assertEqual(network_total_steps("network-peer-standard"), 17)

    def test_pgbench_parser_preserves_transactions_latency_failures_and_progress(self) -> None:
        stdout = """
transaction type: <builtin: TPC-B (sort of)>
number of transactions actually processed: 12345
number of failed transactions: 3 (0.024%)
latency average = 4.250 ms
initial connection time = 12.500 ms
tps = 941.176470 (without initial connection time)
"""
        stderr = "progress: 1.0 s, 900.0 tps, lat 4.100 ms stddev 1.250, 1 failed\n"
        metrics = parse_pgbench_output(stdout, stderr)
        self.assertEqual(metrics["transactions_processed"], 12345)
        self.assertEqual(metrics["failed_transactions"], 3)
        self.assertEqual(metrics["transactions_per_second"], 941.17647)
        self.assertEqual(metrics["latency_average_ms"], 4.25)
        self.assertEqual(metrics["progress"][0]["failed"], 1)
        self.assertEqual(metrics["tail_latency_status"], "unavailable")

    def test_database_run_requires_postgresql_capabilities_on_the_correct_roles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.create_session("session_db", "database", "hash", "2099-01-01T00:00:00+00:00")
            target_system = {
                "inventory": {
                    "capabilities": {"postgres": True, "initdb": True, "pgbench": True, "pg_isready": True}
                }
            }
            generator_system = {"inventory": {"capabilities": {"pgbench": True}}}
            database.add_agent(
                "agent_target", "session_db", "target", "target", target_system, endpoint={"address": "10.0.0.10"}
            )
            database.add_agent(
                "agent_generator",
                "session_db",
                "generator",
                "generator",
                generator_system,
                endpoint={"address": "10.0.0.11"},
            )
            _, target, generator = validate_database_run(database, "session_db", "postgres-peer-quick")
            self.assertEqual(target["id"], "agent_target")
            self.assertEqual(generator["id"], "agent_generator")

    def test_agent_builds_only_allowlisted_pgbench_commands(self) -> None:
        worker = AgentWorker("http://127.0.0.1:8787", "agent", "token")
        summary = """
number of transactions actually processed: 1000
number of failed transactions: 0 (0.000%)
latency average = 2.500 ms
initial connection time = 4.000 ms
tps = 400.000000 (without initial connection time)
"""
        with patch.object(worker, "_postgres_tool", return_value="pgbench"), patch.object(
            worker, "_guarded_task_process", side_effect=[(0, "warmup", ""), (0, summary, "")]
        ) as process, patch("cloudmark.agent.tool_version", return_value="pgbench 16.2"):
            result = worker._run_database_client(
                "task_abc123",
                {
                    "target_address": "10.0.0.10",
                    "port": 55432,
                    "workload": "tpcb-like",
                    "clients": 4,
                    "threads": 2,
                    "duration_seconds": 30,
                    "warmup_seconds": 3,
                    "connect_per_transaction": False,
                    "run_completed_steps": 1,
                    "run_total_steps": 5,
                },
            )
        measured_command = process.call_args_list[1].args[1]
        self.assertIn("-b", measured_command)
        self.assertEqual(measured_command[measured_command.index("-b") + 1], "tpcb-like")
        self.assertNotIn("-C", measured_command)
        self.assertEqual(result["pgbench"]["metrics"]["transactions_per_second"], 400.0)
        with self.assertRaisesRegex(DatabaseBenchmarkError, "workload"):
            worker._run_database_client(
                "task_abc123",
                {
                    "target_address": "10.0.0.10",
                    "port": 55432,
                    "workload": "custom-sql",
                    "clients": 4,
                    "threads": 2,
                    "duration_seconds": 30,
                    "run_completed_steps": 1,
                    "run_total_steps": 5,
                },
            )

    def test_agent_refuses_database_cleanup_outside_its_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "agent"
            outside = Path(directory) / "not-a-database-service"
            outside.mkdir()
            worker = AgentWorker("http://127.0.0.1:8787", "agent", "token", workspace=workspace)
            with self.assertRaisesRegex(DatabaseBenchmarkError, "outside"):
                worker._remove_database_root(outside)
            self.assertTrue(outside.exists())

    def test_agent_database_watchdog_cleans_up_after_controller_contact_loss(self) -> None:
        worker = AgentWorker("http://127.0.0.1:8787", "agent", "token")
        worker.active_database_servers["task_deadbeef"] = SimpleNamespace(deadline=time.monotonic() + 300)
        worker.last_controller_contact = time.monotonic() - 21
        with patch.object(worker, "_stop_database_server") as stop:
            worker._cleanup_expired()
        stop.assert_called_once_with("task_deadbeef")

    def test_guarded_database_process_stops_immediately_on_controller_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            worker = AgentWorker(
                "http://127.0.0.1:8787", "agent", "token", workspace=Path(directory) / "agent"
            )
            with patch.object(
                worker,
                "_api",
                return_value={"cancel_requested": True, "task_status": "running"},
            ):
                started = time.monotonic()
                with self.assertRaisesRegex(AgentBenchmarkFailure, "cancelled"):
                    worker._guarded_task_process(
                        "task_deadbeef",
                        [sys.executable, "-c", "import time; time.sleep(30)"],
                        environment=os.environ.copy(),
                        expected_duration=30,
                        phase="database-initialization",
                        current_job="test",
                        completed_steps=0,
                        total_steps=2,
                    )
            self.assertLess(time.monotonic() - started, 5)

    def test_database_orchestrator_records_measurements_and_verified_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.create_session("session_db", "database", "hash", "2099-01-01T00:00:00+00:00")
            target_system = {
                "inventory": {
                    "capabilities": {"postgres": True, "initdb": True, "pgbench": True, "pg_isready": True}
                }
            }
            generator_system = {"inventory": {"capabilities": {"pgbench": True}}}
            database.add_agent(
                "agent_target", "session_db", "target", "target", target_system, endpoint={"address": "10.0.0.10"}
            )
            database.add_agent(
                "agent_generator",
                "session_db",
                "generator",
                "generator",
                generator_system,
                endpoint={"address": "10.0.0.11"},
            )
            total_steps = database_total_steps("postgres-peer-quick")
            database.create_run(
                "run_database",
                "database",
                "postgres-peer-quick",
                {"suite": "database"},
                total_steps=total_steps,
            )
            done = threading.Event()

            def complete_tasks() -> None:
                while not done.is_set():
                    handled = False
                    for agent_id in ("agent_target", "agent_generator"):
                        task = database.claim_agent_task(agent_id)
                        if not task:
                            continue
                        handled = True
                        if task["kind"] == "database-server-start":
                            result = {
                                "ready": True,
                                "engine": "postgresql",
                                "scale_factor": 10,
                                "tools": {"postgres": "postgres 16.2", "pgbench": "pgbench 16.2"},
                            }
                        elif task["kind"] == "database-client":
                            result = {
                                "pgbench": {
                                    "workload": task["payload"]["workload"],
                                    "clients": task["payload"]["clients"],
                                    "threads": task["payload"]["threads"],
                                    "duration_seconds": task["payload"]["duration_seconds"],
                                    "metrics": {"transactions_per_second": 1000.0, "latency_average_ms": 4.0},
                                    "tool": {"name": "pgbench", "version": "pgbench 16.2"},
                                }
                            }
                        else:
                            result = {"status": "completed", "cleanup_verified": True}
                        database.finish_agent_task(task["id"], agent_id, status="completed", result=result)
                    if not handled:
                        time.sleep(0.005)

            worker = threading.Thread(target=complete_tasks, daemon=True)
            worker.start()
            try:
                context = JobContext("run_database", total_steps=total_steps, timeout_seconds=30)
                result = run_database(
                    database,
                    "run_database",
                    "session_db",
                    "postgres-peer-quick",
                    context=context,
                )
            finally:
                done.set()
                worker.join(timeout=2)
            self.assertEqual(total_steps, 5)
            self.assertEqual(len(result["database_measurements"]), 3)
            self.assertTrue(result["cleanup"]["cleanup_verified"])
            self.assertFalse(result["policy"]["controller_in_data_path"])

    def test_controller_admits_only_confirmed_database_pair_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = CloudMarkController(Path(directory))
            controller.database.create_session("session_db", "database", "hash", "2099-01-01T00:00:00+00:00")
            target_system = {
                "inventory": {
                    "capabilities": {"postgres": True, "initdb": True, "pgbench": True, "pg_isready": True}
                }
            }
            generator_system = {"inventory": {"capabilities": {"pgbench": True}}}
            controller.database.add_agent(
                "agent_target", "session_db", "target", "target", target_system, endpoint={"address": "10.0.0.10"}
            )
            controller.database.add_agent(
                "agent_generator",
                "session_db",
                "generator",
                "generator",
                generator_system,
                endpoint={"address": "10.0.0.11"},
            )
            request = {
                "suite": "database",
                "profile": "postgres-peer-quick",
                "session_id": "session_db",
            }
            with self.assertRaisesRegex(ValueError, "confirm_database_load"):
                controller.submit_run(request)
            with patch.object(controller, "_execute_run"):
                run = controller.submit_run({**request, "confirm_database_load": True})
            stored = controller.database.get_run(run["id"])
            self.assertEqual(stored["total_steps"], 5)
            self.assertEqual(stored["methodology_version"], "database-postgresql-v1")
            self.assertEqual(stored["tool_version"], "postgresql/pgbench-agent")

    def test_database_orchestrator_schedules_cleanup_after_client_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.create_session("session_db", "database", "hash", "2099-01-01T00:00:00+00:00")
            target_system = {
                "inventory": {
                    "capabilities": {"postgres": True, "initdb": True, "pgbench": True, "pg_isready": True}
                }
            }
            generator_system = {"inventory": {"capabilities": {"pgbench": True}}}
            database.add_agent(
                "agent_target", "session_db", "target", "target", target_system, endpoint={"address": "10.0.0.10"}
            )
            database.add_agent(
                "agent_generator",
                "session_db",
                "generator",
                "generator",
                generator_system,
                endpoint={"address": "10.0.0.11"},
            )
            total_steps = database_total_steps("postgres-peer-quick")
            database.create_run(
                "run_database_failure",
                "database",
                "postgres-peer-quick",
                {"suite": "database"},
                total_steps=total_steps,
            )
            done = threading.Event()
            cleanup_seen = threading.Event()

            def complete_tasks() -> None:
                while not done.is_set():
                    handled = False
                    for agent_id in ("agent_target", "agent_generator"):
                        task = database.claim_agent_task(agent_id)
                        if not task:
                            continue
                        handled = True
                        if task["kind"] == "database-server-start":
                            database.finish_agent_task(task["id"], agent_id, status="completed", result={"ready": True})
                        elif task["kind"] == "database-client":
                            database.finish_agent_task(task["id"], agent_id, status="failed", error="simulated client failure")
                        else:
                            cleanup_seen.set()
                            database.finish_agent_task(
                                task["id"],
                                agent_id,
                                status="completed",
                                result={"status": "completed", "cleanup_verified": True},
                            )
                    if not handled:
                        time.sleep(0.005)

            worker = threading.Thread(target=complete_tasks, daemon=True)
            worker.start()
            try:
                context = JobContext("run_database_failure", total_steps=total_steps, timeout_seconds=30)
                with self.assertRaisesRegex(DistributedError, "simulated client failure") as captured:
                    run_database(
                        database,
                        "run_database_failure",
                        "session_db",
                        "postgres-peer-quick",
                        context=context,
                    )
            finally:
                done.set()
                worker.join(timeout=2)
            self.assertTrue(cleanup_seen.is_set())
            self.assertTrue(captured.exception.partial_result["cleanup"]["cleanup_verified"])

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
        self.assertEqual(domains["database"], "partial")
        self.assertEqual(domains["reliability"], "roadmap")

    def test_bootstrap_includes_base_pack(self) -> None:
        plan = create_plan(["storage"])
        self.assertEqual(plan.packs[0], "base")
        self.assertIn("storage", plan.packs)

    def test_bootstrap_compute_and_memory_packs_include_required_tools(self) -> None:
        with patch("cloudmark.bootstrap.detect_manager", return_value="apt"):
            plan = create_plan(["compute", "memory"])
        self.assertIn("sysbench", plan.packages)
        self.assertIn("gcc", plan.packages)
        self.assertIn("libgomp1", plan.packages)

    def test_bootstrap_database_pack_includes_postgresql_and_pgbench_packages(self) -> None:
        with patch("cloudmark.bootstrap.detect_manager", return_value="apt"):
            plan = create_plan(["database"])
        self.assertIn("postgresql", plan.packages)
        self.assertIn("postgresql-contrib", plan.packages)
        self.assertIn("database", plan.packs)

    def test_database_profiles_are_versioned_and_bounded(self) -> None:
        self.assertEqual(database_total_steps("postgres-peer-quick"), 5)
        self.assertEqual(database_total_steps("postgres-peer-standard"), 9)
        for profile in DATABASE_PROFILES.values():
            self.assertEqual(profile["methodology_version"], "database-postgresql-v1")
            self.assertLessEqual(profile["scale_factor"], 100)
            self.assertTrue(all(job["duration"] <= 60 for job in profile["jobs"]))

    def test_memory_preflight_rejects_unsupported_platforms_before_compilation(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch("cloudmark.compute.sys.platform", "win32"):
            with self.assertRaisesRegex(ComputeError, "currently supports Linux"):
                system_preflight("memory", "memory-quick", Path(directory))

    def test_sysbench_cpu_parser_keeps_rate_latency_and_stability(self) -> None:
        output = """
[ 1s ] thds: 1 eps: 1000.00 lat (ms,95%): 1.20
[ 2s ] thds: 1 eps: 1100.00 lat (ms,95%): 1.10
CPU speed:
    events per second: 1050.50
General statistics:
    total time: 2.0010s
    total number of events: 2102
Latency (ms):
    min: 0.80
    avg: 0.95
    max: 1.80
    95th percentile: 1.20
"""
        result = parse_sysbench_cpu(output)
        self.assertEqual(result["events"], 2102)
        self.assertEqual(result["events_per_second"], 1050.5)
        self.assertEqual(result["latency"]["p95_ms"], 1.2)
        self.assertEqual(len(result["time_series"]), 2)
        self.assertGreater(result["stability"]["cv_percent"], 0)

    def test_compute_runner_records_versioned_jobs_and_scaling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = """
[ 1s ] thds: 1 eps: 1000.00 lat (ms,95%): 1.00
events per second: 1000.00
total time: 15.0000s
total number of events: 15000
min: 0.50
avg: 0.75
max: 1.50
95th percentile: 1.00
"""
            preflight = {
                "suite": "compute",
                "profile": "compute-quick",
                "profile_version": "1.0",
                "methodology_version": "compute-v1",
                "workspace": directory,
                "logical_cores": 4,
                "job_count": 3,
                "estimated_seconds": 106,
                "default_timeout_seconds": 286,
                "requires_admin": False,
                "writes_benchmark_data": False,
                "tool": "sysbench",
                "tool_name": "sysbench",
                "tool_version": "sysbench 1.0.20",
            }
            context = JobContext("run_compute", total_steps=3, timeout_seconds=30)
            with patch("cloudmark.compute.system_preflight", return_value=preflight), patch.object(
                context,
                "run_process",
                side_effect=[ProcessResult(("sysbench",), 0, output, "", 0.01) for _ in range(6)],
            ) as run_process:
                result = run_system_benchmark(
                    "compute",
                    "compute-quick",
                    Path(directory),
                    "run_compute",
                    context=context,
                )
            self.assertEqual(result["methodology_version"], "compute-v1")
            self.assertEqual(result["tool"]["version"], "sysbench 1.0.20")
            self.assertEqual(len(result["compute_jobs"]), 3)
            self.assertEqual(result["compute_jobs"][1]["threads"], 4)
            self.assertEqual(result["scaling"]["all_core_threads"], 4)
            self.assertEqual(context.completed_steps, 3)
            commands = [call.args[0] for call in run_process.call_args_list]
            self.assertEqual(len(commands), 6)
            self.assertIn("--time=3", commands[0])
            self.assertIn("--time=15", commands[1])
            self.assertFalse(any(arg.startswith("--warmup-time=") for command in commands for arg in command))
            self.assertIsNotNone(result["compute_jobs"][0]["raw"]["warmup"])

    def test_memory_runner_records_native_bandwidth_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = json.dumps(
                {
                    "benchmark_version": "1.0",
                    "kernel": "copy",
                    "threads": 4,
                    "array_bytes": 134217728,
                    "allocated_bytes": 402653184,
                    "iterations": 10,
                    "elapsed_seconds": 20.0,
                    "bytes_processed": 2684354560,
                    "bandwidth_bytes_per_second": 134217728,
                    "checksum": 10.0,
                }
            )
            preflight = {
                "suite": "memory",
                "profile": "memory-quick",
                "profile_version": "1.0",
                "methodology_version": "memory-v1",
                "workspace": directory,
                "logical_cores": 4,
                "job_count": 5,
                "estimated_seconds": 90,
                "default_timeout_seconds": 270,
                "requires_admin": False,
                "writes_benchmark_data": False,
                "tool": "cloudmark-memory-bench",
                "tool_name": "cloudmark-memory-bench",
                "tool_version": "1.0",
                "compiler_version": "gcc 13.2",
                "array_bytes": 134217728,
                "allocated_bytes": 402653184,
            }
            context = JobContext("run_memory", total_steps=5, timeout_seconds=30)
            with patch("cloudmark.compute.system_preflight", return_value=preflight), patch.object(
                context,
                "run_process",
                side_effect=[ProcessResult(("cloudmark-memory-bench",), 0, payload, "", 0.01) for _ in range(5)],
            ):
                result = run_system_benchmark(
                    "memory",
                    "memory-quick",
                    Path(directory),
                    "run_memory",
                    context=context,
                )
            self.assertEqual(result["methodology_version"], "memory-v1")
            self.assertEqual(result["tool"]["compiler_version"], "gcc 13.2")
            self.assertEqual(len(result["memory_jobs"]), 5)
            self.assertEqual(result["memory_jobs"][0]["metrics"]["bandwidth_bytes_per_second"], 134217728)
            self.assertEqual(context.completed_steps, 5)

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

    def test_controller_prevents_overlapping_local_saturation_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = CloudMarkController(Path(directory))
            controller.database.create_run("run_storage_active", "storage", "disk-quick", {"suite": "storage"})
            with self.assertRaisesRegex(ValueError, "already queued"):
                controller.submit_run(
                    {"suite": "compute", "profile": "compute-quick", "confirm_load": True}
                )

    def test_controller_dispatches_and_attributes_remote_compute_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = CloudMarkController(Path(directory))
            controller.database.create_session("session_remote", "provider", "hash", "2099-01-01T00:00:00+00:00")
            system = {
                "inventory": {
                    "hostname": "provider-vm-a",
                    "os": {"system": "Linux"},
                    "capabilities": {"sysbench": True, "gcc": True, "fio": True},
                },
                "provider": {"provider": "Regional Cloud", "source": "declared-manifest"},
            }
            controller.database.add_agent(
                "agent_remote",
                "session_remote",
                "provider-vm-a",
                "target",
                system,
                "agent-hash",
            )
            submitted = controller.submit_run(
                {
                    "suite": "compute",
                    "profile": "compute-quick",
                    "agent_id": "agent_remote",
                    "confirm_load": True,
                }
            )
            task = None
            deadline = time.time() + 3
            while time.time() < deadline and task is None:
                task = controller.database.claim_agent_task("agent_remote")
                if task is None:
                    time.sleep(0.01)
            self.assertIsNotNone(task)
            controller.progress_agent_task(
                "agent_remote",
                task["id"],
                {
                    "progress": 1 / 3,
                    "phase": "benchmarking",
                    "current_job": "integer-all-cores",
                    "completed_steps": 1,
                    "total_steps": 3,
                    "result": {"suite": "compute", "compute_jobs": [{"name": "integer-single"}]},
                },
            )
            controller.finish_agent_task(
                "agent_remote",
                task["id"],
                {
                    "status": "completed",
                    "result": {
                        "benchmark": {
                            "suite": "compute",
                            "profile": "compute-quick",
                            "profile_version": "1.0",
                            "methodology_version": "compute-v1",
                            "tool": {"name": "sysbench", "version": "sysbench 1.0.20"},
                            "compute_jobs": [{"name": "integer-single"}],
                        },
                        "evidence": system,
                        "protocol_version": "remote-agent-v1",
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
            self.assertEqual(run["result"]["execution"]["agent"]["id"], "agent_remote")
            self.assertEqual(run["result"]["target_evidence"]["inventory"]["hostname"], "provider-vm-a")
            self.assertEqual(run["tool_version"], "sysbench 1.0.20")

    def test_http_api_exposes_v050_dashboard_and_cancel_endpoint(self) -> None:
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
                self.assertEqual(dashboard["version"], "0.5.0")
                self.assertIn("compute-quick", dashboard["profiles"]["compute"])
                self.assertIn("memory-quick", dashboard["profiles"]["memory"])
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
                progress_request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/api/v1/agents/{joined['agent_id']}/tasks/task_agent_api/progress",
                    data=json.dumps(
                        {
                            "progress": 0.5,
                            "phase": "measuring-network",
                            "current_job": "peer-tcp",
                            "completed_steps": 1,
                            "total_steps": 2,
                        }
                    ).encode(),
                    method="POST",
                    headers={"Content-Type": "application/json", "X-CloudMark-Agent-Token": joined["agent_token"]},
                )
                with urllib.request.urlopen(progress_request, timeout=5) as response:
                    progress_payload = json.load(response)
                self.assertTrue(progress_payload["accepted"])
                self.assertFalse(progress_payload["cancel_requested"])
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
