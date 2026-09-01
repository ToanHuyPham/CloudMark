from __future__ import annotations

import argparse
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


WEB_FIXTURE_BIND = "127.0.0.1"
WEB_FIXTURE_PORT = 58081
WEB_FIXTURE_DYNAMIC_PATH = "/api/v2/dynamic"


def build_dynamic_payload() -> bytes:
    """Build a stable 1 KiB response through a real application code path."""
    record = {
        "service": "cloudmark-python-fixture",
        "status": "ok",
        "record_id": 42,
        "categories": ["compute", "network", "storage"],
    }
    serialized = json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(serialized).hexdigest()
    prefix = (
        b'{"digest":"'
        + digest.encode("ascii")
        + b'","payload":"'
    )
    suffix = b'"}\n'
    payload = prefix + (b"x" * (1024 - len(prefix) - len(suffix))) + suffix
    if len(payload) != 1024:
        raise RuntimeError("CloudMark failed to construct its fixed dynamic payload.")
    return payload


class CloudMarkFixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CloudMarkFixture/2"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path == "/ready":
            payload = b"ready\n"
            content_type = "text/plain"
        elif self.path == WEB_FIXTURE_DYNAMIC_PATH:
            payload = build_dynamic_payload()
            content_type = "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-CloudMark-Fixture", "web-http-v2")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CloudMark fixed Web v2 application fixture")
    parser.add_argument("--bind", required=True)
    parser.add_argument("--port", required=True, type=int)
    arguments = parser.parse_args(argv)
    if arguments.bind != WEB_FIXTURE_BIND or arguments.port != WEB_FIXTURE_PORT:
        parser.error("The fixture accepts only CloudMark's fixed loopback endpoint.")
    server = ThreadingHTTPServer((arguments.bind, arguments.port), CloudMarkFixtureHandler)
    server.daemon_threads = True
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
