from __future__ import annotations

import ipaddress
import json
import os
import tempfile
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

from cloudmark.agent import (
    AgentBenchmarkFailure,
    AgentWorker,
    _address_class,
    _collect_sysfs_queue_steering,
    _link_counters,
    _parse_ethtool_driver,
    _parse_ethtool_features,
    _parse_ethtool_queue_statistics,
    _parse_ethtool_rss_indirection,
    _parse_dig_response,
    _parse_resolver_config,
    _parse_tracepath,
    _resolver_evidence,
    _steering_evidence,
    _tcp_congestion_control,
    join_session,
)
from cloudmark.benchmarks import _metrics, _parse_fio_log, run_storage
from cloudmark.bootstrap import create_plan
from cloudmark.campaigns import build_network_campaign_contract, project_network_campaign
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
    _network_analysis,
    _queue_counter_delta,
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
    WEB_PROFILES,
)
from cloudmark.provider import _declared_manifest
from cloudmark.runner import CancellationToken, JobContext, ProcessResult, RunCancelled, RunTimedOut
from cloudmark.server import CloudMarkController, Handler, Server, _dashboard_run_summaries, _json_bytes
from cloudmark.suitability import SCENARIO_REQUIREMENTS, _run_valid, evaluate_suitability
from cloudmark.topology import assess_pairing_topology
from cloudmark.web_benchmark import (
    WebBenchmarkError,
    parse_ab_output,
    run_web,
    validate_web_run,
    web_total_steps,
)


class CloudMarkTests(unittest.TestCase):
    @staticmethod
    def _suitability_system(hostname: str = "target") -> dict[str, object]:
        return {
            "inventory": {
                "hostname": hostname,
                "os": {"system": "Linux", "distribution": "Ubuntu"},
                "cpu": {"model": "Test CPU", "logical_cores": 4},
                "memory": {"total_bytes": 8 * 1024**3},
                "capabilities": {"docker": False, "podman": False},
            },
            "provider": {
                "provider": "Test Provider",
                "confidence": 0.9,
                "source": "test",
                "instance_type": "standard-4",
            },
        }

    def test_suitability_keeps_missing_evidence_unknown_and_provider_unrated(self) -> None:
        report = evaluate_suitability([], self._suitability_system(), lambda _agent_id: None)
        self.assertFalse(report["policy"]["missing_evidence_is_zero"])
        self.assertFalse(report["policy"]["composite_provider_score"])
        target = report["targets"][0]
        self.assertEqual(target["id"], "controller")
        self.assertEqual(target["provider_assessment"]["status"], "not-rated")
        essential = {item["id"]: item for item in target["levels"]["essential"]}
        self.assertEqual(essential["dev-test"]["verdict"], "insufficient")
        self.assertTrue(any(check["status"] == "unavailable" for check in essential["dev-test"]["checks"]))

    def test_suitability_applies_versioned_hard_gates_with_run_provenance(self) -> None:
        completed_at = datetime.now(timezone.utc).isoformat()
        runs = [
            {
                "id": "run_compute_suitability",
                "suite": "compute",
                "profile": "compute-standard",
                "status": "completed",
                "finished_at": completed_at,
                "methodology_version": "compute-v1",
                "request": {},
                "result": {
                    "methodology_version": "compute-v1",
                    "compute_jobs": [
                        {"name": "integer-single", "metrics": {"events_per_second": 1400}},
                        {"name": "integer-sustained", "metrics": {"events_per_second": 3000}},
                    ],
                    "scaling": {"efficiency_percent": 55},
                },
            },
            {
                "id": "run_storage_suitability",
                "suite": "storage",
                "profile": "disk-standard",
                "status": "completed",
                "finished_at": completed_at,
                "methodology_version": "storage-v1",
                "request": {},
                "result": {
                    "methodology_version": "storage-v1",
                    "safety": {"test_file_removed": True},
                    "jobs": [
                        {
                            "name": "sequential-read",
                            "read": {"bandwidth_bytes_per_second": 250 * 1024**2, "iops": 250},
                            "write": {},
                        },
                        {
                            "name": "sequential-write",
                            "read": {},
                            "write": {"bandwidth_bytes_per_second": 180 * 1024**2, "iops": 180},
                        },
                        {
                            "name": "random-read-qd1",
                            "read": {"iops": 2500, "bandwidth_bytes_per_second": 10_240_000},
                            "write": {},
                        },
                    ],
                },
            },
        ]
        report = evaluate_suitability(runs, self._suitability_system(), lambda _agent_id: None)
        target = report["targets"][0]
        essential = {item["id"]: item for item in target["levels"]["essential"]}
        demanding = {item["id"]: item for item in target["levels"]["demanding"]}
        self.assertEqual(essential["dev-test"]["verdict"], "conditional-fit")
        self.assertEqual(demanding["dev-test"]["verdict"], "below-requirement")
        self.assertEqual(essential["dev-test"]["coverage_percent"], 100.0)
        self.assertEqual(
            set(essential["dev-test"]["run_ids"]),
            {"run_compute_suitability", "run_storage_suitability"},
        )

    def test_suitability_never_mixes_evidence_between_remote_targets(self) -> None:
        completed_at = datetime.now(timezone.utc).isoformat()
        runs = [
            {
                "id": "run_agent_a_compute",
                "suite": "compute",
                "profile": "compute-standard",
                "status": "completed",
                "finished_at": completed_at,
                "methodology_version": "compute-v1",
                "request": {"agent_id": "agent_a"},
                "result": {
                    "methodology_version": "compute-v1",
                    "compute_jobs": [{"name": "integer-sustained", "metrics": {"events_per_second": 3000}}],
                },
            },
            {
                "id": "run_agent_b_storage",
                "suite": "storage",
                "profile": "disk-standard",
                "status": "completed",
                "finished_at": completed_at,
                "methodology_version": "storage-v1",
                "request": {"agent_id": "agent_b"},
                "result": {
                    "methodology_version": "storage-v1",
                    "safety": {"test_file_removed": True},
                    "jobs": [{
                        "name": "sequential-write",
                        "read": {},
                        "write": {"bandwidth_bytes_per_second": 180 * 1024**2},
                    }],
                },
            },
        ]
        agents = {
            "agent_a": {"last_seen_at": completed_at, "system": self._suitability_system("agent-a")},
            "agent_b": {"last_seen_at": completed_at, "system": self._suitability_system("agent-b")},
        }
        report = evaluate_suitability(runs, self._suitability_system("controller"), agents.get)
        targets = {item["id"]: item for item in report["targets"]}
        self.assertIn("compute.sustained_eps", targets["agent_a"]["evidence"])
        self.assertNotIn("storage.sequential_write_bps", targets["agent_a"]["evidence"])
        self.assertIn("storage.sequential_write_bps", targets["agent_b"]["evidence"])
        self.assertNotIn("compute.sustained_eps", targets["agent_b"]["evidence"])

    def test_suitability_catalog_covers_every_usage_scenario(self) -> None:
        self.assertEqual(
            set(SCENARIO_REQUIREMENTS),
            {scenario["id"] for scenario in SCENARIOS},
        )

    def test_suitability_marks_old_evidence_stale_and_excludes_old_provider_windows(self) -> None:
        stale_at = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        run = {
            "id": "run_stale_compute",
            "suite": "compute",
            "profile": "compute-standard",
            "status": "completed",
            "finished_at": stale_at,
            "methodology_version": "compute-v1",
            "request": {},
            "result": {
                "methodology_version": "compute-v1",
                "compute_jobs": [
                    {"name": "integer-sustained", "metrics": {"events_per_second": 9000}},
                ],
            },
        }
        report = evaluate_suitability([run], self._suitability_system(), lambda _agent_id: None)
        target = report["targets"][0]
        evidence = target["evidence"]["compute.sustained_eps"]
        self.assertTrue(evidence["stale"])
        dev_test = next(item for item in target["levels"]["essential"] if item["id"] == "dev-test")
        sustained_check = next(item for item in dev_test["checks"] if item["key"] == "compute.sustained_eps")
        self.assertEqual(sustained_check["status"], "stale")
        self.assertEqual(dev_test["verdict"], "insufficient")
        self.assertEqual(target["provider_assessment"]["measurement_windows"], 0)
        self.assertNotIn("compute", target["provider_assessment"]["observed_suites"])

    def test_suitability_rejects_unverified_cleanup_and_declared_provider_identity(self) -> None:
        completed_at = datetime.now(timezone.utc).isoformat()
        system = self._suitability_system()
        system["provider"] = {
            "provider": "Operator Claimed Cloud",
            "confidence": 0.9,
            "source": "declared-manifest-unverified",
            "instance_type": "standard-4",
        }
        run = {
            "id": "run_storage_unclean",
            "suite": "storage",
            "profile": "disk-standard",
            "status": "completed",
            "finished_at": completed_at,
            "methodology_version": "storage-v1",
            "request": {},
            "result": {
                "methodology_version": "storage-v1",
                "safety": {"test_file_removed": False},
                "jobs": [{
                    "name": "sequential-write",
                    "read": {},
                    "write": {"bandwidth_bytes_per_second": 180 * 1024**2},
                }],
            },
        }
        report = evaluate_suitability([run], system, lambda _agent_id: None)
        target = report["targets"][0]
        self.assertNotIn("storage.sequential_write_bps", target["evidence"])
        self.assertEqual(target["evidence_summary"]["accepted_runs"], 0)
        self.assertIn("cleanup", target["evidence_summary"]["rejected_runs"][0]["reason"].lower())
        identity = next(
            item
            for item in target["provider_assessment"]["criteria"]
            if item["label"] == "Verified provider identity"
        )
        self.assertFalse(identity["satisfied"])

    def test_suitability_rejects_network_v3_without_comparison_validity(self) -> None:
        run = {
            "suite": "network",
            "profile": "network-peer-standard",
            "status": "completed",
            "methodology_version": "network-v3",
            "result": {
                "methodology_version": "network-v3",
                "analysis": {
                    "validity": {
                        "route_evidence_status": "complete",
                        "generator_headroom_status": "constrained",
                        "comparison_eligible": False,
                    }
                },
            },
        }
        valid, reason = _run_valid(run)
        self.assertFalse(valid)
        self.assertIn("generator headroom", reason.lower())

    def test_provider_observations_require_exact_repeated_sampling_contract(self) -> None:
        now = datetime.now(timezone.utc)
        agents: dict[str, dict[str, object]] = {}
        runs: list[dict[str, object]] = []
        rates = iter(range(1000, 1900, 100))
        for target_index in range(3):
            agent_id = f"agent_{target_index}"
            system = self._suitability_system(f"target-{target_index}")
            system["provider"]["region"] = "region-a"
            agents[agent_id] = {"last_seen_at": now.isoformat(), "system": system}
            for day_index in range(3):
                observed_at = (now - timedelta(days=day_index)).isoformat()
                runs.append({
                    "id": f"run_compute_{target_index}_{day_index}",
                    "suite": "compute",
                    "profile": "compute-standard",
                    "status": "completed",
                    "finished_at": observed_at,
                    "methodology_version": "compute-v1",
                    "request": {"agent_id": agent_id},
                    "result": {
                        "methodology_version": "compute-v1",
                        "compute_jobs": [{
                            "name": "integer-sustained",
                            "metrics": {"events_per_second": next(rates)},
                        }],
                    },
                })

        report = evaluate_suitability(runs, self._suitability_system("controller"), agents.get)
        observations = report["provider_observations"]
        self.assertEqual(observations["version"], "provider-observations-v3")
        self.assertTrue(observations["policy"]["exact_pair_topology"])
        self.assertTrue(observations["policy"]["exact_pair_topology_evidence"])
        self.assertFalse(observations["policy"]["provider_ranking"])
        group = observations["groups"][0]
        self.assertEqual(group["target_count"], 3)
        self.assertEqual(group["window_count"], 3)
        metric = next(item for item in group["metric_cohorts"] if item["key"] == "compute.sustained_eps")
        self.assertEqual(metric["status"], "comparable")
        self.assertEqual(metric["sample_count"], 9)
        self.assertEqual(metric["target_count"], 3)
        self.assertEqual(metric["window_count"], 3)
        self.assertEqual(metric["statistics"]["median"], 1400)
        self.assertEqual(metric["statistics"]["p10"], 1080)
        self.assertEqual(metric["statistics"]["p90"], 1720)
        self.assertEqual(metric["statistics"]["worst"], 1000)
        self.assertEqual(group["rating_status"], "not-rated")

    def test_provider_observations_do_not_merge_profiles_or_duplicate_peer_runs(self) -> None:
        completed_at = datetime.now(timezone.utc).isoformat()
        systems = {
            "agent_a": {"last_seen_at": completed_at, "system": self._suitability_system("agent-a")},
            "agent_b": {"last_seen_at": completed_at, "system": self._suitability_system("agent-b")},
        }
        runs = [
            {
                "id": "run_compute_quick",
                "suite": "compute",
                "profile": "compute-quick",
                "status": "completed",
                "finished_at": completed_at,
                "methodology_version": "compute-v1",
                "request": {"agent_id": "agent_a"},
                "result": {
                    "methodology_version": "compute-v1",
                    "compute_jobs": [{"name": "integer-single", "metrics": {"events_per_second": 1000}}],
                },
            },
            {
                "id": "run_compute_standard",
                "suite": "compute",
                "profile": "compute-standard",
                "status": "completed",
                "finished_at": completed_at,
                "methodology_version": "compute-v1",
                "request": {"agent_id": "agent_a"},
                "result": {
                    "methodology_version": "compute-v1",
                    "compute_jobs": [{"name": "integer-single", "metrics": {"events_per_second": 1100}}],
                },
            },
            {
                "id": "run_network_pair",
                "suite": "network",
                "profile": "network-peer-standard",
                "status": "completed",
                "finished_at": completed_at,
                "methodology_version": "network-v2",
                "request": {},
                "result": {
                    "methodology_version": "network-v2",
                    "measurements": [
                        {
                            "direction": "a-to-b",
                            "sender": {"id": "agent_a"},
                            "receiver": {"id": "agent_b"},
                            "metrics": {"received_bits_per_second": 500_000_000},
                        },
                        {
                            "direction": "b-to-a",
                            "sender": {"id": "agent_b"},
                            "receiver": {"id": "agent_a"},
                            "metrics": {"received_bits_per_second": 450_000_000},
                        },
                    ],
                },
            },
        ]
        report = evaluate_suitability(runs, self._suitability_system("controller"), systems.get)
        group = report["provider_observations"]["groups"][0]
        single_thread = [item for item in group["metric_cohorts"] if item["key"] == "compute.single_eps"]
        self.assertEqual({item["profile"] for item in single_thread}, {"compute-quick", "compute-standard"})
        self.assertTrue(all(item["sample_count"] == 1 for item in single_thread))
        network = next(item for item in group["metric_cohorts"] if item["key"] == "network.directional_floor_bps")
        self.assertEqual(network["sample_count"], 1)
        self.assertEqual(network["target_count"], 2)
        self.assertEqual(network["run_ids"], ["run_network_pair"])
        self.assertEqual(network["topology_scope"], "undeclared")
        self.assertIn("topology", " ".join(network["reasons"]).lower())

    def test_provider_observations_keep_paired_topologies_in_separate_contracts(self) -> None:
        completed_at = datetime.now(timezone.utc).isoformat()
        systems = {
            "agent_a": {"last_seen_at": completed_at, "system": self._suitability_system("agent-a")},
            "agent_b": {"last_seen_at": completed_at, "system": self._suitability_system("agent-b")},
        }

        def network_run(run_id: str, scope: str, rate: int) -> dict[str, object]:
            return {
                "id": run_id,
                "suite": "network",
                "profile": "network-peer-standard",
                "status": "completed",
                "finished_at": completed_at,
                "methodology_version": "network-v2",
                "request": {},
                "result": {
                    "methodology_version": "network-v2",
                    "session": {"topology": {"scope": scope, "source": "operator-declared"}},
                    "measurements": [
                        {
                            "direction": "a-to-b",
                            "sender": {"id": "agent_a"},
                            "receiver": {"id": "agent_b"},
                            "metrics": {"received_bits_per_second": rate},
                        },
                        {
                            "direction": "b-to-a",
                            "sender": {"id": "agent_b"},
                            "receiver": {"id": "agent_a"},
                            "metrics": {"received_bits_per_second": rate - 10_000_000},
                        },
                    ],
                },
            }

        report = evaluate_suitability(
            [
                network_run("run_same_zone", "same-zone", 500_000_000),
                network_run("run_cross_zone", "cross-zone", 350_000_000),
            ],
            self._suitability_system("controller"),
            systems.get,
        )
        metrics = [
            item
            for item in report["provider_observations"]["groups"][0]["metric_cohorts"]
            if item["key"] == "network.directional_floor_bps"
        ]
        self.assertEqual({item["topology_scope"] for item in metrics}, {"same-zone", "cross-zone"})
        self.assertEqual({item["topology_evidence"] for item in metrics}, {"operator-declared"})
        self.assertTrue(all(item["sample_count"] == 1 for item in metrics))

        contradicted = network_run("run_contradicted", "same-zone", 300_000_000)
        contradicted["result"]["session"]["topology"]["verification"] = {
            "status": "contradicted",
            "observed_scope": "cross-zone",
            "source": "provider-metadata",
        }
        contradicted_report = evaluate_suitability(
            [contradicted],
            self._suitability_system("controller"),
            systems.get,
        )
        contradicted_metric = next(
            item
            for item in contradicted_report["provider_observations"]["groups"][0]["metric_cohorts"]
            if item["key"] == "network.directional_floor_bps"
        )
        self.assertEqual(contradicted_metric["topology_scope"], "undeclared")
        self.assertEqual(contradicted_metric["topology_evidence"], "contradicted")
        self.assertEqual(contradicted_metric["status"], "observational")

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

    def test_database_round_trips_immutable_network_campaign_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            contract = {
                "version": "network-campaign-v1",
                "session_id": "session_campaign",
                "profile": "network-peer-standard",
            }
            database.create_campaign("campaign_test", "Repeated pair", 3, contract)
            campaign = database.get_campaign("campaign_test")
            self.assertEqual(campaign["status"], "active")
            self.assertEqual(campaign["target_windows"], 3)
            self.assertEqual(campaign["contract"], contract)
            self.assertEqual(database.list_campaigns()[0]["id"], "campaign_test")
            database.create_run(
                "run_campaign_indexed",
                "network",
                "network-peer-standard",
                {"suite": "network", "campaign_id": "campaign_test"},
            )
            self.assertEqual(database.list_campaign_runs("campaign_test")[0]["id"], "run_campaign_indexed")
            self.assertEqual(database.list_campaign_runs()[0]["campaign_id"], "campaign_test")

    def test_network_campaign_counts_only_one_comparable_run_per_utc_day(self) -> None:
        session = {
            "id": "session_campaign",
            "status": "ready",
            "topology": {
                "scope": "same-zone",
                "source": "operator-declared",
                "verification": {"status": "confirmed"},
            },
            "agents": [
                {
                    "id": "agent_target",
                    "name": "target-a",
                    "role": "target",
                    "system": {"inventory": {"os": {"system": "Linux", "release": "test"}}},
                },
                {
                    "id": "agent_generator",
                    "name": "generator-a",
                    "role": "generator",
                    "system": {"inventory": {"os": {"system": "Linux", "release": "test"}}},
                },
            ],
        }
        contract = build_network_campaign_contract(session, "network-peer-standard", 3)
        campaign = {
            "id": "campaign_test",
            "label": "Repeated pair",
            "status": "active",
            "created_at": "2026-08-11T00:00:00+00:00",
            "target_windows": 3,
            "contract": contract,
        }

        def run(run_id: str, day: str, *, status: str = "completed", eligible: bool = True) -> dict[str, object]:
            return {
                "id": run_id,
                "status": status,
                "request": {
                    "campaign_id": "campaign_test",
                    "campaign_contract_version": "network-campaign-v1",
                    "session_id": "session_campaign",
                    "profile": "network-peer-standard",
                    "campaign_window_day": day,
                    "campaign_window_number": 1,
                    "campaign_attempt_number": 1,
                },
                "result": {
                    "profile_version": contract["profile_version"],
                    "methodology_version": contract["methodology_version"],
                    "analysis": {"validity": {"comparison_eligible": eligible}},
                },
            }

        view = project_network_campaign(
            campaign,
            [
                run("run_valid", "2026-08-12"),
                run("run_duplicate", "2026-08-12"),
                run("run_failed", "2026-08-13", status="failed"),
                run("run_incomplete", "2026-08-11", eligible=False),
            ],
            session=session,
            now=datetime(2026, 8, 13, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(view["progress"]["valid_windows"], 1)
        self.assertEqual(view["progress"]["attempts"], 4)
        self.assertTrue(view["next_window"]["eligible"])
        self.assertEqual(view["next_window"]["reason_code"], "ready-for-manual-dispatch")

        today = project_network_campaign(
            campaign,
            [run("run_valid", "2026-08-12"), run("run_today", "2026-08-13")],
            session=session,
            now=datetime(2026, 8, 13, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(today["progress"]["valid_windows"], 2)
        self.assertFalse(today["next_window"]["eligible"])
        self.assertEqual(today["next_window"]["reason_code"], "utc-window-already-complete")

        changed_session = json.loads(json.dumps(session))
        changed_session["agents"][0]["name"] = "replacement-target"
        changed = project_network_campaign(
            campaign,
            [run("run_valid", "2026-08-12")],
            session=changed_session,
            now=datetime(2026, 8, 13, 12, tzinfo=timezone.utc),
        )
        self.assertFalse(changed["next_window"]["eligible"])
        self.assertEqual(changed["next_window"]["reason_code"], "campaign-session-contract-mismatch")

        superseded_campaign = json.loads(json.dumps(campaign))
        superseded_campaign["contract"]["profile_version"] = "6.0"
        superseded_campaign["contract"]["methodology_version"] = "network-v6"
        superseded = project_network_campaign(
            superseded_campaign,
            [],
            session=session,
            now=datetime(2026, 8, 13, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(superseded["status"], "superseded")
        self.assertFalse(superseded["next_window"]["eligible"])
        self.assertEqual(
            superseded["next_window"]["reason_code"],
            "campaign-profile-contract-superseded",
        )

    def test_controller_creates_and_manually_dispatches_network_campaign_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = CloudMarkController(Path(directory))
            controller.database.create_session(
                "session_campaign",
                "same-zone pair",
                "hash",
                "2099-01-01T00:00:00+00:00",
                {"scope": "same-zone", "source": "operator-declared"},
            )
            system = {
                "inventory": {
                    "os": {"system": "Linux", "release": "test"},
                    "capabilities": {
                        "iperf3": True,
                        "iproute2": True,
                        "tracepath": True,
                        "ethtool": True,
                        "tcp_congestion_control": True,
                    },
                },
                "provider": {
                    "provider": "Test Cloud",
                    "confidence": 0.99,
                    "source": "trusted test metadata",
                    "instance_type": "standard-4",
                    "region": "region-a",
                    "zone": "region-a-1",
                },
            }
            controller.database.add_agent(
                "agent_target", "session_campaign", "target-a", "target", system, endpoint={"address": "10.0.0.10"}
            )
            controller.database.add_agent(
                "agent_generator", "session_campaign", "generator-a", "generator", system, endpoint={"address": "10.0.0.11"}
            )
            campaign = controller.create_network_campaign({
                "label": "Three-day same-zone evidence",
                "session_id": "session_campaign",
                "profile": "network-peer-standard",
                "target_windows": 3,
            })
            self.assertEqual(campaign["contract_version"], "network-campaign-v1")
            self.assertEqual(campaign["contract"]["topology"]["evidence_class"], "confirmed")
            self.assertFalse(campaign["contract"]["claims"]["provider_rating_enabled"])
            with self.assertRaisesRegex(ValueError, "active repeated network campaign"):
                controller.create_network_campaign({
                    "session_id": "session_campaign",
                    "profile": "network-peer-standard",
                    "target_windows": 3,
                })
            with self.assertRaisesRegex(ValueError, "requires confirm_network_load"):
                controller.start_network_campaign_window(campaign["id"], {})

            with patch.object(
                controller,
                "_submit_run_locked",
                return_value={"id": "run_campaign", "status": "queued"},
            ) as submit:
                dispatched = controller.start_network_campaign_window(
                    campaign["id"],
                    {"confirm_network_load": True, "confirm_campaign_window": True},
                )
            self.assertEqual(dispatched["run"]["id"], "run_campaign")
            request = submit.call_args.args[0]
            self.assertEqual(request["campaign_id"], campaign["id"])
            self.assertEqual(request["profile"], "network-peer-standard")
            self.assertEqual(request["campaign_window_number"], 1)
            self.assertRegex(request["campaign_window_day"], r"^\d{4}-\d{2}-\d{2}$")

    def test_pairing_is_ready_only_after_two_agents_join(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.create_session("session_test", "pair", "hash", "2099-01-01T00:00:00+00:00")
            database.add_agent("agent_a", "session_test", "a", "target", {})
            self.assertEqual(database.get_session("session_test")["status"], "waiting")
            database.add_agent("agent_b", "session_test", "b", "generator", {})
            self.assertEqual(database.get_session("session_test")["status"], "ready")

    def test_pairing_persists_validated_topology_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = CloudMarkController(Path(directory))
            created = controller.create_session(
                "same-zone pair",
                {"scope": "same-zone", "source": "operator-declared"},
            )
            session = controller.database.get_session(created["id"])
            self.assertEqual(created["topology"]["scope"], "same-zone")
            self.assertEqual(created["topology"]["source"], "operator-declared")
            self.assertEqual(created["topology"]["verification"]["status"], "pending")
            self.assertEqual(session["topology"], {"scope": "same-zone", "source": "operator-declared"})
            with self.assertRaisesRegex(ValueError, "topology scope"):
                controller.create_session("invalid", {"scope": "nearby", "source": "operator-declared"})

    def test_pairing_topology_verification_distinguishes_claims_from_observations(self) -> None:
        def agent(role: str, region: str, zone: str, address: str) -> dict[str, object]:
            return {
                "role": role,
                "endpoint": {"address": address},
                "system": {
                    "provider": {
                        "provider": "Test Cloud",
                        "confidence": 0.99,
                        "source": "trusted test metadata",
                        "region": region,
                        "zone": zone,
                    }
                },
            }

        same_zone_agents = [
            agent("target", "region-a", "region-a-1", "10.0.0.10"),
            agent("generator", "region-a", "region-a-1", "10.0.0.11"),
        ]
        confirmed = assess_pairing_topology({
            "topology": {"scope": "same-zone", "source": "operator-declared"},
            "agents": same_zone_agents,
        })
        self.assertEqual(confirmed["verification"]["status"], "confirmed")
        self.assertEqual(confirmed["verification"]["observed_scope"], "same-zone")

        contradicted = assess_pairing_topology({
            "topology": {"scope": "cross-zone", "source": "operator-declared"},
            "agents": same_zone_agents,
        })
        self.assertEqual(contradicted["verification"]["status"], "contradicted")

        derived = assess_pairing_topology({
            "topology": {"scope": "undeclared", "source": "unavailable"},
            "agents": [
                agent("target", "region-a", "region-a-1", "10.0.0.10"),
                agent("generator", "region-b", "region-b-1", "10.1.0.11"),
            ],
        })
        self.assertEqual(derived["verification"]["status"], "derived")
        self.assertEqual(derived["verification"]["observed_scope"], "cross-region")

        with patch("cloudmark.topology._global_endpoint", return_value=True):
            public_addresses_only = assess_pairing_topology({
                "topology": {"scope": "undeclared", "source": "unavailable"},
                "agents": [
                    {"role": "target", "endpoint": {"address": "192.0.2.10"}, "system": {}},
                    {"role": "generator", "endpoint": {"address": "192.0.2.11"}, "system": {}},
                ],
            })
        self.assertEqual(public_addresses_only["verification"]["status"], "unavailable")
        self.assertIsNone(public_addresses_only["verification"]["observed_scope"])
        self.assertIn("does not prove", " ".join(public_addresses_only["verification"]["reasons"]))

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
            with self.assertRaisesRegex(ValueError, "standard Network read-only evidence capabilities:.*tracepath"):
                validate_network_run(database, "session_test", "network-peer-standard")

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

    def test_agent_collects_allow_listed_route_interface_and_path_mtu_evidence(self) -> None:
        worker = AgentWorker("http://127.0.0.1:8787", "agent", "token")
        responses = [
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps([{
                    "dst": "10.0.0.11",
                    "gateway": "10.0.0.1",
                    "dev": "ens4",
                    "prefsrc": "10.0.0.10",
                }]),
                stderr="",
            ),
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps([{
                    "ifname": "ens4",
                    "mtu": 1460,
                    "operstate": "UP",
                    "link_type": "ether",
                    "stats64": {
                        "rx": {"bytes": 1000, "packets": 100, "errors": 1, "dropped": 2},
                        "tx": {"bytes": 2000, "packets": 200, "errors": 3, "dropped": 4},
                    },
                }]),
                stderr="",
            ),
            SimpleNamespace(returncode=0, stdout=" 1?: [LOCALHOST] pmtu 1460\n 1: 10.0.0.11 0.250ms reached\n", stderr=""),
            SimpleNamespace(
                returncode=0,
                stdout="driver: gve\nversion: 1.0.0\nfirmware-version: test\nbus-info: 0000:00:04.0\n",
                stderr="",
            ),
            SimpleNamespace(
                returncode=0,
                stdout=(
                    "Features for ens4:\n"
                    "rx-checksumming: on\n"
                    "tx-checksumming: on\n"
                    "tcp-segmentation-offload: off [fixed]\n"
                    "generic-receive-offload: on\n"
                ),
                stderr="",
            ),
            SimpleNamespace(
                returncode=0,
                stdout=(
                    "NIC statistics:\n"
                    "rx_queue_0_packets: 100\n"
                    "rx_queue_0_bytes: 1000\n"
                    "tx_queue_0_packets: 200\n"
                    "tx_queue_0_bytes: 2000\n"
                ),
                stderr="",
            ),
        ]
        with patch("cloudmark.agent.os.name", "posix"), patch(
            "cloudmark.agent.shutil.which", side_effect=lambda name: name
        ), patch(
            "cloudmark.agent.Path.read_text", return_value="cubic\n"
        ), patch("cloudmark.agent.subprocess.run", side_effect=responses) as run:
            result = worker._run_path_probe({"target_address": "10.0.0.11"})
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["route"]["interface"], "ens4")
        self.assertEqual(result["interface"]["mtu_bytes"], 1460)
        self.assertEqual(result["path_mtu"]["value_bytes"], 1460)
        self.assertEqual(result["path_trace"]["status"], "observed")
        self.assertTrue(result["path_trace"]["reached_destination"])
        self.assertEqual(result["path_trace"]["destination_address_class"], "private")
        self.assertFalse(result["path_trace"]["public_internet_traversal_proven"])
        self.assertEqual(result["interface"]["driver"]["driver"], "gve")
        self.assertTrue(result["interface"]["offloads"]["features"]["rx-checksumming"]["enabled"])
        self.assertEqual(result["tcp"]["congestion_control"]["algorithm"], "cubic")
        self.assertEqual(result["interface"]["counters"]["rx_packets"], 100)
        self.assertEqual(result["interface"]["counters"]["tx_dropped"], 4)
        self.assertEqual(result["interface"]["queue_counters"]["queue_count"], 1)
        self.assertEqual(result["interface"]["queue_counters"]["queues"][0]["counters"]["tx_packets"], 200)
        self.assertFalse(result["policy"]["network_configuration_changed"])
        self.assertEqual(run.call_args_list[0].args[0], ["ip", "-4", "-j", "route", "get", "10.0.0.11"])
        self.assertEqual(run.call_args_list[1].args[0], ["ip", "-s", "-j", "link", "show", "dev", "ens4"])
        self.assertEqual(run.call_args_list[2].args[0], ["tracepath", "-n", "-m", "8", "10.0.0.11"])
        self.assertEqual(run.call_args_list[3].args[0], ["ethtool", "-i", "ens4"])
        self.assertEqual(run.call_args_list[4].args[0], ["ethtool", "-k", "ens4"])
        self.assertEqual(run.call_args_list[5].args[0], ["ethtool", "-S", "ens4"])

    def test_agent_classifies_and_bounds_numeric_path_trace_without_public_path_claim(self) -> None:
        destination = ipaddress.ip_address("198.51.100.10")
        trace = _parse_tracepath(
            """
 1?: [LOCALHOST] pmtu 1500
 1: 10.0.0.1 0.125ms
 2: no reply
 3: 198.51.100.10 1.750ms reached
 9: 203.0.113.9 9.000ms
""",
            destination,
        )
        self.assertEqual(_address_class(destination), "documentation")
        self.assertEqual(trace["status"], "observed")
        self.assertEqual([hop["hop"] for hop in trace["hops"]], [1, 2, 3])
        self.assertEqual(trace["hops"][0]["address_class"], "private")
        self.assertEqual(trace["hops"][1]["state"], "no-reply")
        self.assertTrue(trace["reached_destination"])
        self.assertFalse(trace["public_internet_traversal_proven"])

    def test_agent_redacts_search_domains_and_classifies_resolver_configuration(self) -> None:
        evidence = _parse_resolver_config(
            "nameserver 127.0.0.53\nnameserver 10.0.0.2\n"
            "search internal.example provider.private\n"
            "options ndots:5 timeout:2 rotate unknown:value\n"
        )
        self.assertEqual(evidence["status"], "observed")
        self.assertEqual(evidence["nameserver_count"], 2)
        self.assertEqual(evidence["nameservers"][0]["address_class"], "loopback")
        self.assertEqual(evidence["nameservers"][1]["address_class"], "private")
        self.assertEqual(evidence["search_domain_count"], 2)
        self.assertFalse(evidence["search_domain_names_persisted"])
        self.assertNotIn("internal.example", json.dumps(evidence))
        self.assertEqual(evidence["options"], {"ndots": 5, "timeout": 2, "rotate": True})

    def test_agent_normalizes_fixed_dns_outcomes_without_answer_addresses(self) -> None:
        resolved = _parse_dig_response(
            ";; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\n"
            "example.com. 300 IN A 192.0.2.10\n",
            "",
            record_type="A",
            returncode=0,
            elapsed_ms=4.3219,
        )
        timeout = _parse_dig_response(
            "",
            ";; no servers could be reached",
            record_type="AAAA",
            returncode=9,
            elapsed_ms=2001,
        )
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["answer_count"], 1)
        self.assertEqual(resolved["answer_address_classes"], ["documentation"])
        self.assertFalse(resolved["answer_addresses_persisted"])
        self.assertNotIn("192.0.2.10", json.dumps(resolved))
        self.assertEqual(timeout["status"], "timeout")

    def test_agent_resolver_probe_uses_only_fixed_name_types_and_deadlines(self) -> None:
        responses = [
            SimpleNamespace(
                returncode=0,
                stdout=(
                    ";; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\n"
                    "example.com. 300 IN A 192.0.2.10\n"
                ),
                stderr="",
            ),
            SimpleNamespace(
                returncode=0,
                stdout=(
                    ";; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 2\n"
                    "example.com. 300 IN AAAA 2001:db8::10\n"
                ),
                stderr="",
            ),
        ]
        with patch("cloudmark.agent.Path.open", mock_open(read_data="nameserver 10.0.0.2\n")), patch(
            "cloudmark.agent.shutil.which", return_value="/usr/bin/dig"
        ), patch("cloudmark.agent.subprocess.run", side_effect=responses) as run:
            evidence = _resolver_evidence()
        self.assertEqual(evidence["status"], "complete")
        self.assertEqual(len(evidence["queries"]), 2)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0][-2:], ["example.com.", "A"])
        self.assertEqual(run.call_args_list[1].args[0][-2:], ["example.com.", "AAAA"])
        self.assertTrue(all(call.kwargs["timeout"] == 5 for call in run.call_args_list))
        self.assertTrue(all(call.kwargs["shell"] is False for call in run.call_args_list))

    def test_agent_parses_bounded_ethtool_and_procfs_evidence(self) -> None:
        driver = _parse_ethtool_driver(
            "driver: virtio_net\nversion: 1.2.3\nsupports-statistics: yes\nunknown-field: ignored\n"
        )
        features = _parse_ethtool_features(
            "rx-checksumming: on\ntcp-segmentation-offload: off [fixed]\ntx-checksum-ipv4: on\n"
        )
        with patch("cloudmark.agent.Path.read_text", return_value="bbr\n"):
            congestion = _tcp_congestion_control()
        self.assertEqual(driver["driver"], "virtio_net")
        self.assertTrue(driver["supports_statistics"])
        self.assertNotIn("unknown_field", driver)
        self.assertTrue(features["rx-checksumming"]["enabled"])
        self.assertTrue(features["tcp-segmentation-offload"]["fixed"])
        self.assertNotIn("tx-checksum-ipv4", features)
        self.assertEqual(congestion["algorithm"], "bbr")

    def test_agent_normalizes_bounded_driver_per_queue_counters(self) -> None:
        evidence = _parse_ethtool_queue_statistics(
            """
NIC statistics:
rx_queue_0_packets: 100
rx_queue_0_bytes: 1000
tx_queue_0_packets: 80
queue_1_rx_cnt: 50
queue_1_tx_bytes: 700
rx2_drops: 3
queue_999_rx_packets: 999
rx_queue_0_xdp_packets: 1000
device_packets: 400
"""
        )
        self.assertEqual(evidence["status"], "observed")
        self.assertEqual(evidence["queue_count"], 3)
        self.assertEqual(evidence["queues"][0]["counters"]["rx_packets"], 100)
        self.assertEqual(evidence["queues"][1]["counters"]["rx_packets"], 50)
        self.assertEqual(evidence["queues"][2]["counters"]["rx_dropped"], 3)
        self.assertGreaterEqual(evidence["unclassified_statistics"], 3)
        duplicate = _parse_ethtool_queue_statistics(
            "rx_queue_0_packets: 100\nrx_queue_0_cnt: 100\n"
        )
        self.assertEqual(duplicate["status"], "partial")
        self.assertEqual(duplicate["duplicate_counters"], 1)
        unavailable = _parse_ethtool_queue_statistics("device_packets: 100\n")
        self.assertEqual(unavailable["status"], "unavailable")

    def test_agent_normalizes_rss_without_persisting_hash_key(self) -> None:
        evidence = _parse_ethtool_rss_indirection(
            """
RX flow hash indirection table for ens4 with 4 RX ring(s):
    0:      0     1     2     3     0     1     2     3
RSS hash key:
de:ad:be:ef:do:not:persist
RSS hash function:
    toeplitz: on
    xor: off
"""
        )
        self.assertEqual(evidence["status"], "observed")
        self.assertEqual(evidence["table_entry_count"], 8)
        self.assertEqual(evidence["active_queue_count"], 4)
        self.assertEqual(evidence["queue_distribution"][0]["share_percent"], 25.0)
        self.assertTrue(evidence["hash_functions"]["toeplitz"])
        self.assertFalse(evidence["hash_key_persisted"])
        self.assertNotIn("de:ad:be:ef", json.dumps(evidence))

    def test_agent_collects_bounded_sysfs_steering_and_msi_affinity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sys_net = root / "sys" / "class" / "net"
            proc_irq = root / "proc" / "irq"
            for queue in ("rx-0", "rx-1", "tx-0", "tx-1"):
                (sys_net / "ens4" / "queues" / queue).mkdir(parents=True)
            (sys_net / "ens4" / "queues" / "rx-0" / "rps_cpus").write_text("00000003\n", encoding="utf-8")
            (sys_net / "ens4" / "queues" / "rx-0" / "rps_flow_cnt").write_text("4096\n", encoding="utf-8")
            (sys_net / "ens4" / "queues" / "rx-1" / "rps_cpus").write_text("00000000\n", encoding="utf-8")
            (sys_net / "ens4" / "queues" / "rx-1" / "rps_flow_cnt").write_text("0\n", encoding="utf-8")
            (sys_net / "ens4" / "queues" / "tx-0" / "xps_cpus").write_text("0000000c\n", encoding="utf-8")
            (sys_net / "ens4" / "queues" / "tx-1" / "xps_cpus").write_text("00000000\n", encoding="utf-8")
            for irq, affinity in ((32, "0-1\n"), (33, "2-3\n")):
                (sys_net / "ens4" / "device" / "msi_irqs" / str(irq)).mkdir(parents=True)
                (proc_irq / str(irq)).mkdir(parents=True)
                (proc_irq / str(irq) / "smp_affinity_list").write_text(affinity, encoding="utf-8")
            evidence = _collect_sysfs_queue_steering(
                "ens4",
                sys_class_net=sys_net,
                proc_irq=proc_irq,
            )
        self.assertEqual(evidence["status"], "complete")
        self.assertEqual(evidence["rps"]["configured_queue_count"], 1)
        self.assertEqual(evidence["rps"]["queues"][0]["cpu_count"], 2)
        self.assertEqual(evidence["rps"]["queues"][0]["flow_count"], 4096)
        self.assertEqual(evidence["xps"]["configured_queue_count"], 1)
        self.assertEqual(evidence["irq_affinity"]["observed_affinity_count"], 2)
        self.assertEqual(evidence["irq_affinity"]["distinct_affinity_count"], 2)

    def test_agent_steering_probe_uses_only_read_only_ethtool_rss_query(self) -> None:
        sysfs = {
            "status": "complete",
            "rps": {"status": "observed", "queues": [], "total_queue_count": 0, "configured_queue_count": 0},
            "xps": {"status": "observed", "queues": [], "total_queue_count": 0, "configured_queue_count": 0},
            "irq_affinity": {
                "status": "observed",
                "msi_irq_count": 0,
                "observed_affinity_count": 0,
                "distinct_affinity_count": 0,
                "affinities": [],
            },
            "bounds": {},
        }
        response = SimpleNamespace(
            returncode=0,
            stdout="RX flow hash indirection table for ens4:\n0: 0 1\nRSS hash key:\nsecret\n",
            stderr="",
        )
        with patch("cloudmark.agent._collect_sysfs_queue_steering", return_value=sysfs), patch(
            "cloudmark.agent.subprocess.run", return_value=response
        ) as run:
            evidence = _steering_evidence("ens4", ethtool="/usr/sbin/ethtool", environment={"LC_ALL": "C"})
        self.assertEqual(run.call_args.args[0], ["/usr/sbin/ethtool", "-x", "ens4"])
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertFalse(evidence["policy"]["network_configuration_changed"])
        self.assertFalse(evidence["policy"]["rss_hash_key_persisted"])

    def test_controller_bounds_and_rederives_agent_steering_evidence(self) -> None:
        distribution = [
            {"queue": index % 2, "entries": 2, "share_percent": 50.0}
            for index in range(200)
        ]
        affinities = [
            {"irq": 32 + index, "cpu_list": "0-1", "cpu_count": 2}
            for index in range(300)
        ]
        analysis = _network_analysis({
            "methodology_version": "network-v9",
            "path_measurements": [{
                "direction": "agent-a-to-agent-b",
                "sender": {"id": "agent-a", "name": "Agent A", "role": "target"},
                "evidence": {
                    "status": "partial",
                    "steering": {
                        "status": "complete",
                        "interface": "ens4",
                        "rss": {
                            "status": "observed",
                            "table_entry_count": 400,
                            "active_queue_count": 2,
                            "queue_distribution": distribution,
                            "hash_functions": ["malformed"],
                            "hash_key": "must-not-pass-controller-normalization",
                        },
                        "rps": {"status": "observed", "queues": "malformed"},
                        "xps": "malformed",
                        "irq_affinity": {
                            "status": "observed",
                            "msi_irq_count": 300,
                            "observed_affinity_count": 300,
                            "affinities": affinities,
                        },
                    },
                },
            }],
            "post_path_measurements": [],
            "measurements": [],
            "latency_measurements": [],
            "udp_measurements": [],
            "validity_policy": {},
        })
        observation = analysis["steering_observations"][0]
        self.assertEqual(analysis["validity"]["steering_evidence_status"], "partial")
        self.assertEqual(len(observation["rss"]["queue_distribution"]), 128)
        self.assertEqual(len(observation["irq_affinity"]["affinities"]), 256)
        self.assertEqual(observation["rss"]["hash_functions"], {})
        self.assertNotIn("hash_key", observation["rss"])
        self.assertFalse(analysis["validity"]["steering_evidence_required"])

    def test_agent_normalizes_only_complete_nonnegative_link_counters(self) -> None:
        complete = _link_counters({
            "stats64": {
                "rx": {"bytes": 10, "packets": 2, "errors": 0, "dropped": 1},
                "tx": {"bytes": 20, "packets": 3, "errors": 0, "dropped": 0},
            }
        })
        partial = _link_counters({"stats": {"rx": {"bytes": 10, "packets": 2}}})
        invalid = _link_counters({"stats64": {"rx": {"bytes": -1}}})
        self.assertEqual(complete["status"], "observed")
        self.assertEqual(complete["rx_dropped"], 1)
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(invalid["status"], "unavailable")

    def test_network_queue_counter_delta_preserves_distribution_and_resets(self) -> None:
        def snapshot(stamp: str, q0: int, q1: int) -> dict[str, object]:
            return {
                "queue_counters": {
                    "status": "observed",
                    "observed_at": stamp,
                    "queues": [
                        {"queue": 0, "counters": {"rx_packets": q0, "rx_bytes": q0 * 100, "rx_dropped": 2}},
                        {"queue": 1, "counters": {"rx_packets": q1, "rx_bytes": q1 * 100, "rx_dropped": 1}},
                    ],
                }
            }

        complete = _queue_counter_delta(
            snapshot("2026-08-14T00:00:00+00:00", 100, 100),
            snapshot("2026-08-14T00:01:00+00:00", 900, 300),
        )
        self.assertEqual(complete["status"], "complete")
        self.assertEqual(complete["reported_queue_count"], 2)
        self.assertEqual(complete["rx_distribution"]["active_queues"], 2)
        self.assertEqual(complete["rx_distribution"]["busiest_queue"], 0)
        self.assertEqual(complete["rx_distribution"]["busiest_queue_percent"], 80.0)
        self.assertEqual(complete["total_dropped"], 0)

        reset = _queue_counter_delta(
            snapshot("2026-08-14T00:00:00+00:00", 100, 100),
            snapshot("2026-08-14T00:01:00+00:00", 90, 300),
        )
        self.assertEqual(reset["status"], "partial")
        self.assertIn("queue-0:rx_packets", reset["reset_fields"])

    def test_agent_reports_path_evidence_unavailable_without_iproute2(self) -> None:
        worker = AgentWorker("http://127.0.0.1:8787", "agent", "token")
        with patch("cloudmark.agent.os.name", "posix"), patch("cloudmark.agent.shutil.which", return_value=None):
            result = worker._run_path_probe({"target_address": "10.0.0.11"})
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("ip route", result["reason"])

    def test_agent_path_probe_falls_back_for_older_iproute2_without_json(self) -> None:
        worker = AgentWorker("http://127.0.0.1:8787", "agent", "token")
        responses = [
            SimpleNamespace(returncode=1, stdout="", stderr="Option -j is unknown"),
            SimpleNamespace(returncode=0, stdout="10.0.0.11 via 10.0.0.1 dev eth0 src 10.0.0.10\n", stderr=""),
            SimpleNamespace(returncode=1, stdout="", stderr="Option -j is unknown"),
            SimpleNamespace(returncode=0, stdout="2: eth0: <UP> mtu 1500 state UP mode DEFAULT\n", stderr=""),
            SimpleNamespace(returncode=0, stdout=" 1?: [LOCALHOST] pmtu 1500\n", stderr=""),
        ]
        with patch("cloudmark.agent.os.name", "posix"), patch(
            "cloudmark.agent.shutil.which", side_effect=lambda name: None if name == "ethtool" else name
        ), patch("cloudmark.agent.subprocess.run", side_effect=responses):
            result = worker._run_path_probe({"target_address": "10.0.0.11"})
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["route"]["interface"], "eth0")
        self.assertEqual(result["interface"]["mtu_bytes"], 1500)

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

    def test_network_analysis_rejects_generator_cpu_saturation(self) -> None:
        result = {
            "path_measurements": [
                {"evidence": {"status": "complete"}},
                {"evidence": {"status": "complete"}},
            ],
            "latency_measurements": [],
            "udp_measurements": [],
            "validity_policy": {
                "generator_cpu_limit_percent": 90,
                "generator_scaling_cpu_floor_percent": 85,
                "generator_scaling_gain_floor_percent": 5,
            },
            "measurements": [
                {
                    "direction": "generator-to-target",
                    "streams": 1,
                    "sender": {"role": "generator"},
                    "receiver": {"role": "target"},
                    "metrics": {"received_bits_per_second": 900, "sender_cpu_percent": 95},
                },
                {
                    "direction": "generator-to-target",
                    "streams": 16,
                    "sender": {"role": "generator"},
                    "receiver": {"role": "target"},
                    "metrics": {"received_bits_per_second": 920, "sender_cpu_percent": 96},
                },
            ],
        }
        analysis = _network_analysis(result)
        self.assertEqual(analysis["validity"]["generator_headroom_status"], "constrained")
        self.assertFalse(analysis["validity"]["comparison_eligible"])

    def test_network_v4_requires_complete_nic_and_tcp_control_evidence(self) -> None:
        result = {
            "methodology_version": "network-v4",
            "path_measurements": [
                {"evidence": {"status": "complete"}},
                {"evidence": {"status": "complete"}},
            ],
            "latency_measurements": [],
            "udp_measurements": [],
            "validity_policy": {},
            "measurements": [
                {
                    "direction": "generator-to-target",
                    "streams": 1,
                    "sender": {"role": "generator"},
                    "receiver": {"role": "target"},
                    "metrics": {"received_bits_per_second": 900, "sender_cpu_percent": 20},
                },
                {
                    "direction": "target-to-generator",
                    "streams": 1,
                    "sender": {"role": "target"},
                    "receiver": {"role": "generator"},
                    "metrics": {"received_bits_per_second": 900, "receiver_cpu_percent": 20},
                },
            ],
        }
        analysis = _network_analysis(result)
        self.assertEqual(analysis["validity"]["route_evidence_status"], "complete")
        self.assertEqual(analysis["validity"]["generator_headroom_status"], "adequate")
        self.assertEqual(analysis["validity"]["nic_evidence_status"], "unavailable")
        self.assertFalse(analysis["validity"]["comparison_eligible"])
        self.assertIn("nic-offload-and-tcp-control-evidence-incomplete", analysis["validity"]["reason_codes"])

        result["methodology_version"] = "network-v3"
        legacy_analysis = _network_analysis(result)
        self.assertFalse(legacy_analysis["validity"]["nic_evidence_required"])
        self.assertTrue(legacy_analysis["validity"]["comparison_eligible"])

    def test_network_v5_requires_complete_pre_post_interface_counters(self) -> None:
        counter_block = {
            "status": "observed",
            "rx_bytes": 100,
            "rx_packets": 10,
            "rx_errors": 0,
            "rx_dropped": 0,
            "tx_bytes": 100,
            "tx_packets": 10,
            "tx_errors": 0,
            "tx_dropped": 0,
        }
        interface = {
            "name": "ens4",
            "driver": {"status": "observed"},
            "offloads": {"status": "observed"},
            "counters": counter_block,
        }
        path = lambda direction: {
            "direction": direction,
            "evidence": {
                "status": "complete",
                "interface": interface,
                "tcp": {"congestion_control": {"status": "observed"}},
            },
        }
        result = {
            "methodology_version": "network-v5",
            "path_measurements": [path("a-to-b"), path("b-to-a")],
            "post_path_measurements": [],
            "latency_measurements": [],
            "udp_measurements": [],
            "validity_policy": {},
            "measurements": [
                {
                    "direction": "a-to-b",
                    "streams": 1,
                    "sender": {"role": "generator"},
                    "receiver": {"role": "target"},
                    "metrics": {"received_bits_per_second": 900, "sender_cpu_percent": 20},
                },
                {
                    "direction": "b-to-a",
                    "streams": 1,
                    "sender": {"role": "target"},
                    "receiver": {"role": "generator"},
                    "metrics": {"received_bits_per_second": 900, "receiver_cpu_percent": 20},
                },
            ],
        }
        analysis = _network_analysis(result)
        self.assertEqual(analysis["validity"]["nic_evidence_status"], "complete")
        self.assertEqual(analysis["validity"]["interface_counter_evidence_status"], "unavailable")
        self.assertFalse(analysis["validity"]["comparison_eligible"])
        self.assertIn("interface-counter-window-evidence-incomplete", analysis["validity"]["reason_codes"])

        result["methodology_version"] = "network-v4"
        legacy_analysis = _network_analysis(result)
        self.assertFalse(legacy_analysis["validity"]["interface_counter_evidence_required"])
        self.assertTrue(legacy_analysis["validity"]["comparison_eligible"])

    def test_network_v6_rejects_a_changed_boundary_route_without_claiming_public_transit(self) -> None:
        def path(direction: str, source: str, gateway: str, stamp: int, counter: int) -> dict[str, object]:
            return {
                "direction": direction,
                "evidence": {
                    "status": "complete",
                    "route": {"interface": "ens4", "source": source, "gateway": gateway},
                    "interface": {
                        "name": "ens4",
                        "driver": {"status": "observed"},
                        "offloads": {"status": "observed"},
                        "counters": {
                            "status": "observed",
                            "observed_at": f"2026-08-13T00:00:0{stamp}+00:00",
                            "rx_bytes": counter,
                            "rx_packets": counter,
                            "rx_errors": 0,
                            "rx_dropped": 0,
                            "tx_bytes": counter,
                            "tx_packets": counter,
                            "tx_errors": 0,
                            "tx_dropped": 0,
                        },
                    },
                    "tcp": {"congestion_control": {"status": "observed"}},
                    "path_trace": {
                        "status": "observed",
                        "reached_destination": True,
                        "hops": [{"state": "observed", "address": "198.51.100.10"}],
                        "public_internet_traversal_proven": False,
                    },
                },
            }

        result = {
            "methodology_version": "network-v6",
            "path_measurements": [
                path("a-to-b", "10.0.0.10", "10.0.0.1", 0, 100),
                path("b-to-a", "10.0.0.11", "10.0.0.1", 0, 100),
            ],
            "post_path_measurements": [
                path("a-to-b", "10.0.0.10", "10.0.0.254", 1, 200),
                path("b-to-a", "10.0.0.11", "10.0.0.1", 1, 200),
            ],
            "latency_measurements": [],
            "udp_measurements": [],
            "validity_policy": {},
            "measurements": [
                {
                    "direction": "a-to-b",
                    "streams": 1,
                    "sender": {"role": "generator"},
                    "receiver": {"role": "target"},
                    "metrics": {"received_bits_per_second": 900, "sender_cpu_percent": 20},
                },
                {
                    "direction": "b-to-a",
                    "streams": 1,
                    "sender": {"role": "target"},
                    "receiver": {"role": "generator"},
                    "metrics": {"received_bits_per_second": 900, "receiver_cpu_percent": 20},
                },
            ],
        }
        analysis = _network_analysis(result)
        self.assertEqual(analysis["validity"]["path_trace_evidence_status"], "complete")
        self.assertEqual(analysis["validity"]["route_stability_status"], "changed")
        self.assertFalse(analysis["validity"]["comparison_eligible"])
        self.assertIn("route-stability-evidence-changed", analysis["validity"]["reason_codes"])
        self.assertFalse(analysis["path_claims"]["public_internet_traversal_proven"])

        changed_boundary = result["post_path_measurements"][0]["evidence"]
        changed_boundary["route"]["gateway"] = "10.0.0.1"
        changed_boundary["path_trace"]["status"] = "partial"
        changed_boundary["path_trace"]["reached_destination"] = False
        incomplete_trace_analysis = _network_analysis(result)
        self.assertEqual(incomplete_trace_analysis["validity"]["route_stability_status"], "complete")
        self.assertEqual(incomplete_trace_analysis["validity"]["path_trace_evidence_status"], "partial")
        self.assertIn(
            "bounded-path-trace-evidence-incomplete",
            incomplete_trace_analysis["validity"]["reason_codes"],
        )

        changed_boundary["path_trace"]["status"] = "observed"
        changed_boundary["path_trace"]["reached_destination"] = True
        result["methodology_version"] = "network-v7"
        v7_analysis = _network_analysis(result)
        self.assertEqual(v7_analysis["validity"]["queue_counter_evidence_status"], "unavailable")
        self.assertFalse(v7_analysis["validity"]["queue_counter_evidence_required"])
        self.assertTrue(v7_analysis["validity"]["comparison_eligible"])

        result["methodology_version"] = "network-v5"
        legacy_analysis = _network_analysis(result)
        self.assertFalse(legacy_analysis["validity"]["route_stability_required"])
        self.assertTrue(legacy_analysis["validity"]["comparison_eligible"])

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

    def test_standard_network_orchestrator_captures_all_v9_measurement_classes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.create_session("session_test", "pair", "hash", "2099-01-01T00:00:00+00:00")
            system = {
                "inventory": {
                    "capabilities": {
                        "iperf3": True,
                        "iproute2": True,
                        "tracepath": True,
                        "ethtool": True,
                        "tcp_congestion_control": True,
                    }
                }
            }
            database.add_agent("agent_a", "session_test", "target", "target", system, endpoint={"address": "10.0.0.10"})
            database.add_agent("agent_b", "session_test", "generator", "generator", system, endpoint={"address": "10.0.0.11"})
            total_steps = network_total_steps("network-peer-standard")
            database.create_run(
                "run_network_v9",
                "network",
                "network-peer-standard",
                {"suite": "network"},
                total_steps=total_steps,
            )
            done = threading.Event()
            path_snapshots: dict[str, int] = {}

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
                        elif task["kind"] == "network-path-probe":
                            snapshot = path_snapshots.get(agent_id, 0)
                            path_snapshots[agent_id] = snapshot + 1
                            counter_offset = snapshot * 1000
                            result = {
                                "status": "complete",
                                "route": {
                                    "destination": payload["target_address"],
                                    "gateway": "10.0.0.1",
                                    "source": "10.0.0.10" if agent_id == "agent_a" else "10.0.0.11",
                                    "interface": "ens4",
                                },
                                "interface": {"name": "ens4", "mtu_bytes": 1460, "state": "UP"},
                                "tcp": {
                                    "congestion_control": {
                                        "status": "observed",
                                        "algorithm": "cubic",
                                        "source": "linux-procfs",
                                    }
                                },
                                "path_mtu": {"status": "observed", "value_bytes": 1460, "source": "tracepath"},
                                "path_trace": {
                                    "status": "observed",
                                    "tool": "tracepath",
                                    "max_hops": 8,
                                    "destination_address_class": "private",
                                    "hops": [
                                        {
                                            "hop": 1,
                                            "state": "observed",
                                            "address": payload["target_address"],
                                            "address_class": "private",
                                            "rtt_ms": 0.25,
                                            "reached_destination": True,
                                        }
                                    ],
                                    "reached_destination": True,
                                    "public_internet_traversal_proven": False,
                                },
                            }
                            if payload.get("resolver_probe") is True:
                                result["resolver"] = {
                                    "status": "complete",
                                    "scope": "agent-system-resolver-diagnostic",
                                    "observed_at": "2026-08-13T00:00:00+00:00",
                                    "query_name": "example.com.",
                                    "configuration": {
                                        "status": "observed",
                                        "nameservers": [
                                            {
                                                "address": "10.0.0.2",
                                                "address_family": "ipv4",
                                                "address_class": "private",
                                            }
                                        ],
                                        "nameserver_count": 1,
                                        "search_domain_count": 0,
                                        "options": {},
                                    },
                                    "queries": [
                                        {"record_type": "A", "status": "resolved", "elapsed_ms": 2.0},
                                        {"record_type": "AAAA", "status": "resolved", "elapsed_ms": 3.0},
                                    ],
                                    "cache_state": "unknown",
                                    "provider_dns_service_attributed": False,
                                }
                            if payload.get("steering_probe") is True:
                                result["steering"] = {
                                    "status": "complete",
                                    "observed_at": "2026-08-13T00:00:00+00:00",
                                    "interface": "ens4",
                                    "rss": {
                                        "status": "observed",
                                        "source": "ethtool-rss-indirection",
                                        "table_entry_count": 4,
                                        "active_queue_count": 2,
                                        "busiest_queue": 0,
                                        "busiest_queue_percent": 50.0,
                                        "queue_distribution": [
                                            {"queue": 0, "entries": 2, "share_percent": 50.0},
                                            {"queue": 1, "entries": 2, "share_percent": 50.0},
                                        ],
                                        "hash_functions": {"toeplitz": True},
                                        "hash_key_persisted": False,
                                    },
                                    "rps": {
                                        "status": "observed",
                                        "source": "linux-sysfs-rps",
                                        "total_queue_count": 2,
                                        "configured_queue_count": 1,
                                        "queues": [
                                            {"queue": 0, "mask": "3", "cpu_count": 2, "configured": True}
                                        ],
                                    },
                                    "xps": {
                                        "status": "observed",
                                        "source": "linux-sysfs-xps",
                                        "total_queue_count": 2,
                                        "configured_queue_count": 1,
                                        "queues": [
                                            {"queue": 0, "mask": "c", "cpu_count": 2, "configured": True}
                                        ],
                                    },
                                    "irq_affinity": {
                                        "status": "observed",
                                        "source": "linux-procfs-msi-affinity",
                                        "msi_irq_count": 2,
                                        "observed_affinity_count": 2,
                                        "distinct_affinity_count": 2,
                                        "affinities": [
                                            {"irq": 32, "cpu_list": "0-1", "cpu_count": 2},
                                            {"irq": 33, "cpu_list": "2-3", "cpu_count": 2},
                                        ],
                                    },
                                }
                            result["interface"].update({
                                "driver": {"status": "observed", "driver": "gve", "version": "1.0"},
                                "offloads": {
                                    "status": "observed",
                                    "features": {"generic-receive-offload": {"enabled": True, "fixed": False}},
                                },
                                "counters": {
                                    "status": "observed",
                                    "observed_at": f"2026-08-13T00:00:0{snapshot}+00:00",
                                    "rx_bytes": 10_000 + counter_offset,
                                    "rx_packets": 100 + counter_offset,
                                    "rx_errors": 1 + snapshot,
                                    "rx_dropped": 2 + snapshot * 2,
                                    "tx_bytes": 20_000 + counter_offset,
                                    "tx_packets": 200 + counter_offset,
                                    "tx_errors": 3 + snapshot,
                                    "tx_dropped": 4 + snapshot * 3,
                                },
                                "queue_counters": {
                                    "status": "observed",
                                    "observed_at": f"2026-08-13T00:00:0{snapshot}+00:00",
                                    "queues": [
                                        {
                                            "queue": 0,
                                            "counters": {
                                                "rx_packets": 100 + snapshot * 800,
                                                "rx_bytes": 10_000 + snapshot * 80_000,
                                                "rx_dropped": 1 + snapshot,
                                                "tx_packets": 200 + snapshot * 600,
                                                "tx_bytes": 20_000 + snapshot * 60_000,
                                            },
                                        },
                                        {
                                            "queue": 1,
                                            "counters": {
                                                "rx_packets": 50 + snapshot * 200,
                                                "rx_bytes": 5_000 + snapshot * 20_000,
                                                "rx_dropped": snapshot,
                                                "tx_packets": 75 + snapshot * 400,
                                                "tx_bytes": 7_500 + snapshot * 40_000,
                                            },
                                        },
                                    ],
                                },
                            })
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
                                "cpu_utilization_percent": {"host_total": 45.0, "remote_total": 40.0},
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
                context = JobContext("run_network_v9", total_steps=total_steps, timeout_seconds=60)
                result = run_network(
                    database,
                    "run_network_v9",
                    "session_test",
                    "network-peer-standard",
                    context=context,
                )
            finally:
                done.set()
                worker.join(timeout=2)
            self.assertEqual(total_steps, 21)
            self.assertEqual(len(result["path_measurements"]), 2)
            self.assertEqual(len(result["post_path_measurements"]), 2)
            self.assertEqual(len(result["latency_measurements"]), 2)
            self.assertEqual(len(result["measurements"]), 8)
            self.assertEqual(len(result["udp_measurements"]), 6)
            self.assertEqual(len(result["bidirectional_measurements"]), 1)
            self.assertTrue(all(item["target_rate_bps"] <= ALLOWED_UDP_RATE_MAX for item in result["udp_measurements"]))
            self.assertFalse(result["policy"]["controller_in_data_path"])
            self.assertFalse(result["analysis"]["scored"])
            self.assertTrue(result["analysis"]["validity"]["comparison_eligible"])
            self.assertEqual(result["analysis"]["validity"]["nic_evidence_status"], "complete")
            self.assertEqual(result["analysis"]["validity"]["interface_counter_evidence_status"], "complete")
            self.assertEqual(result["analysis"]["validity"]["queue_counter_evidence_status"], "complete")
            self.assertFalse(result["analysis"]["validity"]["queue_counter_evidence_required"])
            self.assertEqual(result["analysis"]["validity"]["resolver_evidence_status"], "complete")
            self.assertFalse(result["analysis"]["validity"]["resolver_evidence_required"])
            self.assertEqual(len(result["analysis"]["resolver_observations"]), 2)
            self.assertEqual(result["analysis"]["validity"]["steering_evidence_status"], "complete")
            self.assertFalse(result["analysis"]["validity"]["steering_evidence_required"])
            self.assertEqual(len(result["analysis"]["steering_observations"]), 2)
            self.assertTrue(result["policy"]["bounded_queue_steering_and_irq_evidence"])
            self.assertTrue(all(value == 2 for value in path_snapshots.values()))
            self.assertEqual(result["analysis"]["validity"]["path_trace_evidence_status"], "complete")
            self.assertEqual(result["analysis"]["validity"]["route_stability_status"], "complete")
            self.assertFalse(result["analysis"]["path_claims"]["public_internet_traversal_proven"])
            self.assertEqual(result["analysis"]["validity"]["generator_headroom_status"], "adequate")
            self.assertEqual(len(result["analysis"]["interface_counter_deltas"]), 2)
            self.assertTrue(all(item["total_dropped"] == 5 for item in result["analysis"]["interface_counter_deltas"]))
            self.assertEqual(len(result["analysis"]["queue_counter_deltas"]), 2)
            self.assertTrue(all(item["rx_distribution"]["active_queues"] == 2 for item in result["analysis"]["queue_counter_deltas"]))

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
        self.assertEqual(profile["methodology_version"], "network-v9")
        self.assertEqual(network_total_steps("network-peer-quick"), 4)
        self.assertEqual(network_total_steps("network-peer-standard"), 21)

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
            worker, "_guarded_service_process", side_effect=[(0, "warmup", ""), (0, summary, "")]
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

    def test_agent_web_watchdog_cleans_up_after_controller_contact_loss(self) -> None:
        worker = AgentWorker("http://127.0.0.1:8787", "agent", "token")
        worker.active_web_servers["task_webdeadbeef"] = SimpleNamespace(deadline=time.monotonic() + 300)
        worker.last_controller_contact = time.monotonic() - 21
        with patch.object(worker, "_stop_web_server") as stop:
            worker._cleanup_expired()
        stop.assert_called_once_with("task_webdeadbeef")

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
                    worker._guarded_service_process(
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

    @staticmethod
    def _apachebench_summary() -> str:
        return """
Server Software:        nginx/1.24.0
Server Hostname:        10.0.0.10
Server Port:            58443
SSL/TLS Protocol:       TLSv1.2,ECDHE-RSA-AES256-GCM-SHA384,2048,256
Document Path:          /api/v1/record
Document Length:        1024 bytes
Concurrency Level:      16
Time taken for tests:   20.000 seconds
Complete requests:      20000
Failed requests:        2
   (Connect: 1, Receive: 1, Length: 0, Exceptions: 0)
Non-2xx responses:      1
Keep-Alive requests:    19997
Total transferred:      22000000 bytes
HTML transferred:       20480000 bytes
Requests per second:    1000.00 [#/sec] (mean)
Time per request:       16.000 [ms] (mean)
Time per request:       1.000 [ms] (mean, across all concurrent requests)
Transfer rate:          1074.22 [Kbytes/sec] received

Connection Times (ms)
              min  mean[+/-sd] median   max
Connect:        1    2   1.0      2      10
Processing:     1   10   5.0      8      50
Waiting:        1    8   4.0      7      40
Total:          2   12   5.5     10      55

Percentage of the requests served within a certain time (ms)
  50%     10
  66%     12
  75%     14
  80%     16
  90%     20
  95%     25
  98%     35
  99%     40
 100%     55 (longest request)
"""

    def test_apachebench_parser_preserves_tail_latency_failures_tls_and_connection_times(self) -> None:
        metrics = parse_ab_output(self._apachebench_summary())
        self.assertEqual(metrics["complete_requests"], 20000)
        self.assertEqual(metrics["successful_requests"], 19997)
        self.assertEqual(metrics["failed_requests"], 2)
        self.assertEqual(metrics["non_2xx_responses"], 1)
        self.assertEqual(metrics["requests_per_second"], 1000.0)
        self.assertEqual(metrics["latency_percentiles_ms"]["p99"], 40.0)
        self.assertEqual(metrics["connection_times"]["connect"]["max_ms"], 10.0)
        self.assertEqual(metrics["tls"]["protocol"], "TLSv1.2")

    def test_web_run_requires_nginx_openssl_and_apachebench_on_the_correct_roles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.create_session("session_web", "web", "hash", "2099-01-01T00:00:00+00:00")
            target_system = {"inventory": {"capabilities": {"nginx": True, "openssl": True}}}
            generator_system = {"inventory": {"capabilities": {"ab": True}}}
            database.add_agent(
                "agent_target", "session_web", "target", "target", target_system, endpoint={"address": "10.0.0.10"}
            )
            database.add_agent(
                "agent_generator",
                "session_web",
                "generator",
                "generator",
                generator_system,
                endpoint={"address": "10.0.0.11"},
            )
            _, target, generator = validate_web_run(database, "session_web", "web-peer-quick")
            self.assertEqual(target["id"], "agent_target")
            self.assertEqual(generator["id"], "agent_generator")

    def test_agent_builds_only_allowlisted_apachebench_commands(self) -> None:
        worker = AgentWorker("http://127.0.0.1:8787", "agent", "token")
        with patch.object(worker, "_web_tool", return_value="ab"), patch.object(
            worker,
            "_guarded_service_process",
            side_effect=[(0, "warmup", ""), (0, self._apachebench_summary(), "")],
        ) as process, patch("cloudmark.agent.web_tool_version", return_value="ApacheBench, Version 2.4.62"):
            result = worker._run_web_client(
                "task_web123",
                {
                    "target_address": "10.0.0.10",
                    "scheme": "https",
                    "port": 58443,
                    "path": "/api/v1/record",
                    "concurrency": 16,
                    "duration_seconds": 20,
                    "warmup_seconds": 2,
                    "keep_alive": True,
                    "run_completed_steps": 1,
                    "run_total_steps": 7,
                },
            )
        measured_command = process.call_args_list[1].args[1]
        self.assertIn("-k", measured_command)
        self.assertIn("-f", measured_command)
        self.assertEqual(measured_command[measured_command.index("-f") + 1], "TLS1.2")
        self.assertEqual(measured_command[-1], "https://10.0.0.10:58443/api/v1/record")
        self.assertEqual(result["apachebench"]["metrics"]["requests_per_second"], 1000.0)
        with self.assertRaisesRegex(WebBenchmarkError, "path"):
            worker._run_web_client(
                "task_web123",
                {
                    "target_address": "10.0.0.10",
                    "scheme": "https",
                    "port": 58443,
                    "path": "/operator-supplied-target",
                    "concurrency": 16,
                    "duration_seconds": 20,
                    "run_completed_steps": 1,
                    "run_total_steps": 7,
                },
            )

    def test_agent_refuses_web_cleanup_outside_its_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "agent"
            outside = Path(directory) / "not-a-web-service"
            outside.mkdir()
            worker = AgentWorker("http://127.0.0.1:8787", "agent", "token", workspace=workspace)
            with self.assertRaisesRegex(WebBenchmarkError, "outside"):
                worker._remove_web_root(outside)
            self.assertTrue(outside.exists())

    def test_agent_web_service_config_restricts_peer_ports_tls_and_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            worker = AgentWorker(
                "http://127.0.0.1:8787", "agent", "token", workspace=Path(directory) / "agent"
            )
            process = MagicMock()
            process.poll.return_value = None
            process.returncode = 0
            connection = MagicMock()
            with patch.object(worker, "_web_tool", side_effect=lambda name: name), patch.object(
                worker, "_guarded_service_process", return_value=(0, "", "")
            ), patch.object(
                worker, "_service_control_update"
            ), patch("cloudmark.agent.web_tool_version", return_value="test-version"), patch(
                "cloudmark.agent.subprocess.Popen", return_value=process
            ), patch("cloudmark.agent.socket.create_connection", return_value=connection), patch(
                "cloudmark.agent.os.geteuid", return_value=1000, create=True
            ):
                result = worker._start_web_server(
                    "task_webconfig",
                    {
                        "listen_address": "10.0.0.10",
                        "allowed_client_address": "10.0.0.11",
                        "http_port": 58080,
                        "https_port": 58443,
                        "deadline_seconds": 300,
                        "run_completed_steps": 0,
                        "run_total_steps": 7,
                    },
                )
            config = worker.active_web_servers["task_webconfig"].config_path.read_text(encoding="utf-8")
            self.assertIn("listen 10.0.0.10:58080;", config)
            self.assertIn("listen 10.0.0.10:58443 ssl;", config)
            self.assertIn("allow 10.0.0.11;", config)
            self.assertIn("deny all;", config)
            self.assertIn("ssl_protocols TLSv1.2;", config)
            self.assertIn("ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256;", config)
            self.assertNotIn("0.0.0.0", config)
            self.assertEqual(result["payloads"]["api_bytes"], 1024)
            with patch("cloudmark.agent.subprocess.run", return_value=SimpleNamespace(returncode=0)):
                cleanup = worker._stop_web_server("task_webconfig")
            self.assertTrue(cleanup["cleanup_verified"])

    def test_web_orchestrator_records_measurements_and_verified_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.create_session("session_web", "web", "hash", "2099-01-01T00:00:00+00:00")
            target_system = {"inventory": {"capabilities": {"nginx": True, "openssl": True}}}
            generator_system = {"inventory": {"capabilities": {"ab": True}}}
            database.add_agent(
                "agent_target", "session_web", "target", "target", target_system, endpoint={"address": "10.0.0.10"}
            )
            database.add_agent(
                "agent_generator",
                "session_web",
                "generator",
                "generator",
                generator_system,
                endpoint={"address": "10.0.0.11"},
            )
            total_steps = web_total_steps("web-peer-quick")
            database.create_run(
                "run_web", "web", "web-peer-quick", {"suite": "web"}, total_steps=total_steps
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
                        if task["kind"] == "web-service-start":
                            result = {
                                "ready": True,
                                "engine": "nginx",
                                "tools": {"nginx": "nginx/1.24.0", "openssl": "OpenSSL 3.0"},
                            }
                        elif task["kind"] == "web-client":
                            result = {
                                "apachebench": {
                                    "scheme": task["payload"]["scheme"],
                                    "path": task["payload"]["path"],
                                    "concurrency": task["payload"]["concurrency"],
                                    "duration_seconds": task["payload"]["duration_seconds"],
                                    "metrics": {
                                        "requests_per_second": 1000.0,
                                        "latency_percentiles_ms": {"p95": 5.0, "p99": 10.0},
                                        "success_percent": 100.0,
                                    },
                                    "tool": {"name": "ab", "version": "ApacheBench 2.4"},
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
                context = JobContext("run_web", total_steps=total_steps, timeout_seconds=30)
                result = run_web(database, "run_web", "session_web", "web-peer-quick", context=context)
            finally:
                done.set()
                worker.join(timeout=2)
            self.assertEqual(total_steps, 7)
            self.assertEqual(len(result["web_measurements"]), 5)
            self.assertTrue(result["cleanup"]["cleanup_verified"])
            self.assertFalse(result["policy"]["controller_in_data_path"])

    def test_web_orchestrator_schedules_cleanup_after_client_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.create_session("session_web", "web", "hash", "2099-01-01T00:00:00+00:00")
            target_system = {"inventory": {"capabilities": {"nginx": True, "openssl": True}}}
            generator_system = {"inventory": {"capabilities": {"ab": True}}}
            database.add_agent(
                "agent_target", "session_web", "target", "target", target_system, endpoint={"address": "10.0.0.10"}
            )
            database.add_agent(
                "agent_generator",
                "session_web",
                "generator",
                "generator",
                generator_system,
                endpoint={"address": "10.0.0.11"},
            )
            total_steps = web_total_steps("web-peer-quick")
            database.create_run(
                "run_web_failure", "web", "web-peer-quick", {"suite": "web"}, total_steps=total_steps
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
                        if task["kind"] == "web-service-start":
                            database.finish_agent_task(task["id"], agent_id, status="completed", result={"ready": True})
                        elif task["kind"] == "web-client":
                            database.finish_agent_task(
                                task["id"], agent_id, status="failed", error="simulated web client failure"
                            )
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
                context = JobContext("run_web_failure", total_steps=total_steps, timeout_seconds=30)
                with self.assertRaisesRegex(DistributedError, "simulated web client failure") as captured:
                    run_web(database, "run_web_failure", "session_web", "web-peer-quick", context=context)
            finally:
                done.set()
                worker.join(timeout=2)
            self.assertTrue(cleanup_seen.is_set())
            self.assertTrue(captured.exception.partial_result["cleanup"]["cleanup_verified"])

    def test_controller_admits_only_confirmed_web_pair_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = CloudMarkController(Path(directory))
            controller.database.create_session("session_web", "web", "hash", "2099-01-01T00:00:00+00:00")
            target_system = {"inventory": {"capabilities": {"nginx": True, "openssl": True}}}
            generator_system = {"inventory": {"capabilities": {"ab": True}}}
            controller.database.add_agent(
                "agent_target", "session_web", "target", "target", target_system, endpoint={"address": "10.0.0.10"}
            )
            controller.database.add_agent(
                "agent_generator",
                "session_web",
                "generator",
                "generator",
                generator_system,
                endpoint={"address": "10.0.0.11"},
            )
            request = {"suite": "web", "profile": "web-peer-quick", "session_id": "session_web"}
            with self.assertRaisesRegex(ValueError, "confirm_web_load"):
                controller.submit_run(request)
            with patch.object(controller, "_execute_run"):
                run = controller.submit_run({**request, "confirm_web_load": True})
            stored = controller.database.get_run(run["id"])
            self.assertEqual(stored["total_steps"], 7)
            self.assertEqual(stored["methodology_version"], "web-http-v1")
            self.assertEqual(stored["tool_version"], "nginx/apachebench-agent")

    def test_scenario_coverage_does_not_overstate_executors(self) -> None:
        statuses = {scenario["id"]: scenario["status"] for scenario in SCENARIOS}
        self.assertEqual(statuses["storage-backup"], "available")
        self.assertEqual(statuses["database"], "partial")
        self.assertEqual(statuses["network"], "partial")
        self.assertEqual(statuses["web-app"], "partial")

    def test_assessment_catalog_covers_full_infrastructure_stack(self) -> None:
        domains = {domain["id"]: domain["status"] for domain in ASSESSMENT_DOMAINS}
        self.assertGreaterEqual(len(domains), 15)
        self.assertTrue({"compute", "memory", "storage", "network", "gpu", "web", "database"}.issubset(domains))
        self.assertTrue({"containers", "security", "reliability", "observability", "control-plane", "cost", "consistency"}.issubset(domains))
        self.assertEqual(domains["storage"], "available")
        self.assertEqual(domains["network"], "partial")
        self.assertEqual(domains["database"], "partial")
        self.assertEqual(domains["web"], "partial")
        self.assertEqual(domains["reliability"], "roadmap")

    def test_bootstrap_includes_base_pack(self) -> None:
        plan = create_plan(["storage"])
        self.assertEqual(plan.packs[0], "base")
        self.assertIn("storage", plan.packs)

    def test_bootstrap_network_pack_includes_route_ping_and_path_mtu_tools(self) -> None:
        with patch("cloudmark.bootstrap.detect_manager", return_value="apt"):
            plan = create_plan(["network"])
        self.assertIn("iperf3", plan.packages)
        self.assertIn("iproute2", plan.packages)
        self.assertIn("iputils-ping", plan.packages)
        self.assertIn("iputils-tracepath", plan.packages)
        self.assertIn("dnsutils", plan.packages)

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

    def test_bootstrap_web_pack_includes_nginx_apachebench_and_openssl(self) -> None:
        with patch("cloudmark.bootstrap.detect_manager", return_value="apt"):
            plan = create_plan(["web"])
        self.assertIn("nginx", plan.packages)
        self.assertIn("apache2-utils", plan.packages)
        self.assertIn("openssl", plan.packages)

    def test_web_profiles_are_versioned_and_bounded(self) -> None:
        self.assertEqual(web_total_steps("web-peer-quick"), 7)
        self.assertEqual(web_total_steps("web-peer-standard"), 11)
        for profile in WEB_PROFILES.values():
            self.assertEqual(profile["methodology_version"], "web-http-v1")
            self.assertTrue(all(job["duration"] <= 60 for job in profile["jobs"]))
            self.assertTrue(all(job["concurrency"] <= 64 for job in profile["jobs"]))

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
                self.assertIn("postgres-peer-quick", dashboard["profiles"]["database"])
                self.assertIn("web-peer-quick", dashboard["profiles"]["web"])
                self.assertIn("sessions", dashboard)
                self.assertEqual(dashboard["network_campaigns"], [])
                self.assertEqual(dashboard["suitability"]["engine_version"], "suitability-v1")
                self.assertFalse(dashboard["suitability"]["policy"]["missing_evidence_is_zero"])
                with urllib.request.urlopen(f"{base}/suitability", timeout=5) as response:
                    suitability = json.load(response)
                self.assertEqual(suitability["requirements_version"], "workload-requirements-1.0")
                with urllib.request.urlopen(f"{base}/provider-comparisons", timeout=5) as response:
                    provider_observations = json.load(response)
                self.assertEqual(provider_observations["version"], "provider-observations-v3")
                self.assertEqual(provider_observations["rating_status"], "not-rated")
                self.assertFalse(provider_observations["policy"]["provider_ranking"])
                with urllib.request.urlopen(f"{base}/network-campaigns", timeout=5) as response:
                    campaigns = json.load(response)
                self.assertEqual(campaigns["items"], [])
                controller.database.create_session(
                    "session_http_campaign",
                    "HTTP campaign pair",
                    "hash",
                    "2099-01-01T00:00:00+00:00",
                    {"scope": "same-zone", "source": "operator-declared"},
                )
                network_system = {
                    "inventory": {
                        "os": {"system": "Linux", "release": "test"},
                        "capabilities": {
                            "iperf3": True,
                            "iproute2": True,
                            "tracepath": True,
                            "ethtool": True,
                            "tcp_congestion_control": True,
                        },
                    }
                }
                controller.database.add_agent(
                    "agent_http_target",
                    "session_http_campaign",
                    "http-target",
                    "target",
                    network_system,
                    endpoint={"address": "10.0.0.20"},
                )
                controller.database.add_agent(
                    "agent_http_generator",
                    "session_http_campaign",
                    "http-generator",
                    "generator",
                    network_system,
                    endpoint={"address": "10.0.0.21"},
                )
                create_campaign = urllib.request.Request(
                    f"{base}/network-campaigns",
                    data=json.dumps({
                        "label": "HTTP repeated pair",
                        "session_id": "session_http_campaign",
                        "profile": "network-peer-standard",
                        "target_windows": 3,
                    }).encode("utf-8"),
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "X-CloudMark-Token": controller.token,
                    },
                )
                with urllib.request.urlopen(create_campaign, timeout=5) as response:
                    created_campaign = json.load(response)
                self.assertEqual(created_campaign["contract_version"], "network-campaign-v1")
                with urllib.request.urlopen(
                    f"{base}/network-campaigns/{created_campaign['id']}", timeout=5
                ) as response:
                    read_campaign = json.load(response)
                self.assertEqual(read_campaign["id"], created_campaign["id"])
                refused_dispatch = urllib.request.Request(
                    f"{base}/network-campaigns/{created_campaign['id']}/runs",
                    data=b"{}",
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "X-CloudMark-Token": controller.token,
                    },
                )
                with self.assertRaises(urllib.error.HTTPError) as dispatch_error:
                    urllib.request.urlopen(refused_dispatch, timeout=5)
                self.assertEqual(dispatch_error.exception.code, 400)
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

    def test_http_handler_stops_cleanly_when_client_disconnects_during_response(self) -> None:
        handler = object.__new__(Handler)
        handler.path = "/api/v1/health"
        handler._send = MagicMock(side_effect=ConnectionAbortedError("client closed socket"))
        handler.do_GET()
        handler._send.assert_called_once()

    def test_dashboard_summaries_preserve_latest_metrics_without_raw_evidence(self) -> None:
        points = [{"elapsed_ms": index * 1000, "value": index, "direction": "read"} for index in range(240)]
        runs = [
            {
                "id": "latest-storage",
                "suite": "storage",
                "status": "completed",
                "request": {},
                "result": {
                    "jobs": [
                        {"name": "first", "raw": {"stdout": "full"}, "time_series": {"bandwidth": points}},
                        {"name": "last", "raw": {"stdout": "full"}, "time_series": {"bandwidth": points}},
                    ]
                },
            },
            {
                "id": "older-storage",
                "suite": "storage",
                "status": "completed",
                "request": {},
                "result": {"jobs": [{"name": "old", "raw": {"stdout": "full"}}]},
            },
            {
                "id": "remote-storage",
                "suite": "storage",
                "status": "completed",
                "request": {"agent_id": "agent-a"},
                "result": {"jobs": [{"name": "remote", "raw": {"stdout": "full"}}]},
            },
        ]
        summaries = _dashboard_run_summaries(runs)
        self.assertNotIn("time_series", summaries[0]["result"]["jobs"][0])
        timeline = summaries[0]["result"]["jobs"][1]["time_series"]["bandwidth"]
        self.assertEqual(len(timeline), 90)
        self.assertEqual(timeline[0], points[0])
        self.assertEqual(timeline[-1], points[-1])
        self.assertNotIn("raw", summaries[0]["result"]["jobs"][1])
        self.assertNotIn("result", summaries[1])
        self.assertIn("result", summaries[2])
        self.assertIn("raw", runs[0]["result"]["jobs"][0])

    def test_api_json_is_compact_without_changing_unicode(self) -> None:
        body = _json_bytes({"label": "Đà Nẵng", "status": "ok"})
        self.assertEqual(json.loads(body), {"label": "Đà Nẵng", "status": "ok"})
        self.assertNotIn(b"\n", body)

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
