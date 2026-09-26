from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cloudmark.cost import CostObservationError, build_cost_observation
from cloudmark.database import Database
from cloudmark.server import CloudMarkController


def target_system(provider: str = "Test Provider") -> dict[str, object]:
    return {
        "inventory": {
            "hostname": "cost-target",
            "os": {"system": "Linux", "distribution": "Ubuntu", "architecture": "x86_64"},
        },
        "provider": {
            "provider": provider,
            "confidence": 0.99,
            "source": "provider-metadata",
            "instance_type": "standard-4",
            "region": "region-a",
            "zone": "zone-a",
        },
    }


def cost_request(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "target_id": "controller",
        "amount": "0.125000",
        "currency": "usd",
        "billing_unit": "hour",
        "commitment": "on-demand",
        "tax_included": False,
        "observed_at": "2026-09-26T08:00:00+07:00",
        "source_type": "provider-public-url",
        "source_reference": "https://provider.example/pricing?region=region-a",
    }
    request.update(overrides)
    return request


class CostObservationTests(unittest.TestCase):
    def test_contract_normalizes_exact_price_time_target_and_claim_boundary(self) -> None:
        observation = build_cost_observation(
            "cost_001",
            cost_request(),
            "controller",
            target_system(),
            now=datetime(2026, 9, 26, 2, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(observation["version"], "cost-observation-v1")
        self.assertEqual(observation["observed_at"], "2026-09-26T01:00:00+00:00")
        self.assertEqual(observation["observed_at_source"], "operator-supplied")
        self.assertEqual(observation["price"]["amount"], "0.125")
        self.assertEqual(observation["price"]["currency"], "USD")
        self.assertEqual(observation["target"]["provider"], "Test Provider")
        self.assertEqual(observation["evidence_status"], "operator-declared-unverified")
        self.assertFalse(observation["source"]["fetched_by_cloudmark"])
        self.assertFalse(observation["policy"]["provider_rating_input"])
        self.assertFalse(observation["policy"]["price_performance_calculated"])

    def test_contract_rejects_ambiguous_unsafe_or_unidentified_inputs(self) -> None:
        now = datetime(2026, 9, 26, 2, 0, tzinfo=timezone.utc)
        invalid_requests = [
            cost_request(amount=0.125),
            cost_request(amount="0"),
            cost_request(amount="1.1234567"),
            cost_request(currency="US"),
            cost_request(billing_unit="minute"),
            cost_request(commitment="free-tier"),
            cost_request(tax_included="unknown"),
            cost_request(observed_at="2026-09-26T01:00:00"),
            cost_request(observed_at=(now + timedelta(days=2)).isoformat()),
            cost_request(source_reference="http://provider.example/pricing"),
            cost_request(source_reference="https://user:secret@provider.example/pricing"),
            cost_request(source_reference="https://provider.example/pricing#private"),
            cost_request(source_reference="https://[invalid"),
            {**cost_request(), "score": 99},
        ]
        for request in invalid_requests:
            with self.subTest(request=request), self.assertRaises(CostObservationError):
                build_cost_observation("cost_invalid", request, "controller", target_system(), now=now)
        with self.assertRaisesRegex(CostObservationError, "provider and product/SKU"):
            build_cost_observation("cost_unknown", cost_request(), "controller", target_system("Unknown"), now=now)
        zulu = build_cost_observation(
            "cost_zulu",
            cost_request(observed_at="2026-09-26T01:00:00Z"),
            "controller",
            target_system(),
            now=now,
        )
        self.assertEqual(zulu["observed_at"], "2026-09-26T01:00:00+00:00")

    def test_database_round_trips_immutable_cost_observations_in_time_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "cloudmark.sqlite3")
            for identifier, observed_at in (
                ("cost_old", "2026-09-25T01:00:00+00:00"),
                ("cost_new", "2026-09-26T01:00:00+00:00"),
            ):
                observation = build_cost_observation(
                    identifier,
                    cost_request(observed_at=observed_at),
                    "controller",
                    target_system(),
                    now=datetime(2026, 9, 26, 2, 0, tzinfo=timezone.utc),
                )
                database.create_cost_observation(observation)
            stored = database.list_cost_observations()
            self.assertEqual([item["id"] for item in stored], ["cost_new", "cost_old"])
            self.assertEqual(stored[0]["source"]["type"], "provider-public-url")
            self.assertNotIn("score", stored[0])
            self.assertEqual([item["id"] for item in database.list_cost_observations(1)], ["cost_new"])

    def test_controller_records_cost_for_current_target_without_enabling_rating(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = CloudMarkController(Path(directory))
            controller._inventory = target_system()["inventory"]
            controller._provider = target_system()["provider"]
            observation = controller.create_cost_observation(cost_request(observed_at=None))
            report = controller.cost_observation_report()
            self.assertEqual(report["version"], "cost-observation-v1")
            self.assertEqual(report["items"][0]["id"], observation["id"])
            self.assertEqual(report["rating_status"], "not-rated")
            self.assertFalse(report["provider_rating_input"])
            self.assertEqual(observation["observed_at_source"], "controller-receipt-time")
            controller.database.create_session(
                "session_cost",
                "cost target",
                "hash",
                "2099-01-01T00:00:00+00:00",
            )
            controller.database.add_agent(
                "agent_cost",
                "session_cost",
                "cost-agent",
                "target",
                target_system(),
            )
            remote = controller.create_cost_observation(cost_request(
                target_id="agent_cost",
                source_type="operator-reference",
                source_reference="redacted-quote-2026-09",
                observed_at=None,
            ))
            self.assertEqual(remote["target"]["id"], "agent_cost")
            self.assertEqual(remote["source"]["type"], "operator-reference")
            cost_gate = next(
                item
                for item in controller.dashboard()["suitability"]["targets"][0]["provider_assessment"]["criteria"]
                if item["label"] == "Security, reliability, control-plane, and cost evidence"
            )
            self.assertFalse(cost_gate["satisfied"])
            with self.assertRaisesRegex(LookupError, "target was not found"):
                controller.create_cost_observation(cost_request(target_id="missing-agent"))


if __name__ == "__main__":
    unittest.main()
