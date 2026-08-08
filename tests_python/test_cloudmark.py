from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloudmark.agent import join_session
from cloudmark.benchmarks import _metrics
from cloudmark.bootstrap import create_plan
from cloudmark.database import Database
from cloudmark.inventory import collect_inventory
from cloudmark.profiles import ASSESSMENT_DOMAINS, NETWORK_PROFILES, SCENARIOS, STORAGE_PROFILES
from cloudmark.provider import _declared_manifest


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

    def test_pairing_is_ready_only_after_two_agents_join(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.create_session("session_test", "pair", "hash", "2099-01-01T00:00:00+00:00")
            database.add_agent("agent_a", "session_test", "a", "target", {})
            self.assertEqual(database.get_session("session_test")["status"], "waiting")
            database.add_agent("agent_b", "session_test", "b", "generator", {})
            self.assertEqual(database.get_session("session_test")["status"], "ready")

    def test_profiles_enforce_network_direction_policy(self) -> None:
        self.assertIn("disk-quick", STORAGE_PROFILES)
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


if __name__ == "__main__":
    unittest.main()
