from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cloudmark.provider as provider


class FakeResponse:
    def __init__(self, payload: bytes, headers: dict[str, str] | None = None) -> None:
        self.payload = payload
        self.headers = headers or {}
        self.read_limit: int | None = None

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        self.read_limit = limit
        return self.payload


class ProviderDetectionTests(unittest.TestCase):
    def test_metadata_request_bypasses_proxies_and_bounds_payload(self) -> None:
        with patch("cloudmark.provider.urllib.request.build_opener") as build:
            provider._opener()
        proxy_handler = build.call_args.args[0]
        self.assertEqual(proxy_handler.proxies, {})

        response = FakeResponse(b'{"ok":true}', {"Metadata-Flavor": "Google"})
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(provider, "_opener", return_value=opener):
            result = provider._request(
                "http://metadata.example/identity",
                method="PUT",
                headers={"Metadata": "true"},
                timeout=0.2,
            )
        self.assertEqual(result, (b'{"ok":true}', {"Metadata-Flavor": "Google"}))
        self.assertEqual(response.read_limit, provider.METADATA_MAX_BYTES + 1)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://metadata.example/identity")
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(request.get_header("Metadata"), "true")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 0.2)

        oversized = FakeResponse(b"x" * (provider.METADATA_MAX_BYTES + 1))
        opener.open.return_value = oversized
        with patch.object(provider, "_opener", return_value=opener):
            self.assertIsNone(provider._request("http://metadata.example/oversized"))
        opener.open.side_effect = ValueError("invalid request header")
        with patch.object(provider, "_opener", return_value=opener):
            self.assertIsNone(provider._request("http://metadata.example/failure"))

    def test_aws_requires_safe_token_and_complete_identity(self) -> None:
        identity = {
            "region": "ap-southeast-1",
            "availabilityZone": "ap-southeast-1a",
            "instanceType": "m7i.large",
        }
        with patch.object(
            provider,
            "_request",
            side_effect=[(b"safe-token-123456", {}), (json.dumps(identity).encode(), {})],
        ) as request:
            detected = provider._aws()
        self.assertEqual(detected["provider"], "AWS")
        self.assertEqual(detected["region"], "ap-southeast-1")
        self.assertEqual(
            request.call_args_list[1].kwargs["headers"],
            {"X-aws-ec2-metadata-token": "safe-token-123456"},
        )

        with patch.object(provider, "_request", return_value=(b"unsafe\ntoken", {})) as request:
            self.assertIsNone(provider._aws())
        self.assertEqual(request.call_count, 1)

        with patch.object(
            provider,
            "_request",
            side_effect=[(b"safe-token-123456", {}), (b"[]", {})],
        ):
            self.assertIsNone(provider._aws())
        with patch.object(
            provider,
            "_request",
            side_effect=[(b"safe-token-123456", {}), (b'{"region":"ap-southeast-1"}', {})],
        ):
            self.assertIsNone(provider._aws())

    def test_azure_requires_compute_identity_and_keeps_zone_optional(self) -> None:
        payload = {"compute": {"location": "southeastasia", "zone": "1", "vmSize": "Standard_D4s_v5"}}
        with patch.object(provider, "_request", return_value=(json.dumps(payload).encode(), {})):
            detected = provider._azure()
        self.assertEqual(detected["provider"], "Microsoft Azure")
        self.assertEqual(detected["instance_type"], "Standard_D4s_v5")

        payload["compute"]["zone"] = ""
        with patch.object(provider, "_request", return_value=(json.dumps(payload).encode(), {})):
            self.assertIsNone(provider._azure()["zone"])
        with patch.object(provider, "_request", return_value=(b"[]", {})):
            self.assertIsNone(provider._azure())
        with patch.object(provider, "_request", return_value=(b'{"compute":{}}', {})):
            self.assertIsNone(provider._azure())

    def test_gcp_requires_flavor_header_and_complete_paths(self) -> None:
        payload = {
            "zone": "projects/123/zones/asia-east1-c",
            "machineType": "projects/123/machineTypes/e2-standard-4",
        }
        with patch.object(
            provider,
            "_request",
            return_value=(json.dumps(payload).encode(), {"Metadata-Flavor": "Google"}),
        ):
            detected = provider._gcp()
        self.assertEqual(detected["provider"], "Google Cloud")
        self.assertEqual(detected["region"], "asia-east1")
        self.assertEqual(detected["zone"], "asia-east1-c")
        self.assertEqual(detected["instance_type"], "e2-standard-4")

        with patch.object(provider, "_request", return_value=(json.dumps(payload).encode(), {})):
            self.assertIsNone(provider._gcp())
        with patch.object(provider, "_request", return_value=(b"[]", {"Metadata-Flavor": "Google"})):
            self.assertIsNone(provider._gcp())
        with patch.object(
            provider,
            "_request",
            return_value=(b'{"zone":"projects/123/zones/asia-east1-c"}', {"Metadata-Flavor": "Google"}),
        ):
            self.assertIsNone(provider._gcp())

    def test_declared_manifest_is_bounded_unverified_and_path_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "provider.json"
            manifest.write_text(
                json.dumps({
                    "provider": "  Regional   Cloud  ",
                    "operator": ["invalid-type"],
                    "region": "hcm-1",
                    "zone": "hcm-1a",
                    "instance_type": "standard-4-8",
                    "cloud_stack": "OpenStack",
                }),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CLOUDMARK_PROVIDER_MANIFEST": str(manifest)}, clear=True), patch.object(
                provider,
                "_provider_manifest_candidates",
                return_value=[manifest],
            ):
                detected = provider._declared_manifest()
        self.assertEqual(detected["provider"], "Regional Cloud")
        self.assertIsNone(detected["operator"])
        self.assertIn("unverified", detected["source"].lower())
        self.assertEqual(detected["evidence"], ["Local declared provider manifest"])
        self.assertNotIn(str(manifest), json.dumps(detected))

        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "provider.json"
            manifest.write_text("[]", encoding="utf-8")
            with patch.dict(os.environ, {"CLOUDMARK_PROVIDER_MANIFEST": str(manifest)}, clear=True), patch.object(
                provider,
                "_provider_manifest_candidates",
                return_value=[manifest],
            ):
                self.assertIsNone(provider._declared_manifest())
            manifest.write_text(json.dumps({"provider": "x" * 161}), encoding="utf-8")
            with patch.dict(os.environ, {"CLOUDMARK_PROVIDER_MANIFEST": str(manifest)}, clear=True), patch.object(
                provider,
                "_provider_manifest_candidates",
                return_value=[manifest],
            ):
                self.assertIsNone(provider._declared_manifest())

    def test_detection_order_and_unknown_fallback_are_explicit(self) -> None:
        azure = {"provider": "Microsoft Azure", "confidence": 0.99}
        with patch.object(provider, "_aws", return_value=None) as aws, patch.object(
            provider,
            "_azure",
            return_value=azure,
        ) as azure_detector, patch.object(provider, "_gcp") as gcp, patch.object(provider, "_declared_manifest") as declared:
            self.assertIs(provider.detect_provider(), azure)
        aws.assert_called_once_with()
        azure_detector.assert_called_once_with()
        gcp.assert_not_called()
        declared.assert_not_called()

        with patch.object(provider, "_aws", return_value=None), patch.object(
            provider,
            "_azure",
            return_value=None,
        ), patch.object(provider, "_gcp", return_value=None), patch.object(
            provider,
            "_declared_manifest",
            return_value=None,
        ):
            unknown = provider.detect_provider()
        self.assertEqual(unknown["provider"], "Unknown")
        self.assertEqual(unknown["confidence"], 0.0)
        self.assertEqual(unknown["evidence"], [])


if __name__ == "__main__":
    unittest.main()
