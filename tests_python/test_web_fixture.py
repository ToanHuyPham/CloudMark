from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

import cloudmark.web_fixture as fixture


class FakeSocket:
    def __init__(self, request: bytes) -> None:
        self.request = io.BytesIO(request)
        self.response = io.BytesIO()

    def makefile(self, mode: str, _buffering: int | None = None):
        if "r" in mode:
            return self.request
        return self.response

    def sendall(self, value: bytes) -> None:
        self.response.write(value)


class FakeHttpServer:
    server_name = "127.0.0.1"
    server_port = fixture.WEB_FIXTURE_PORT


def handle(request: bytes) -> tuple[int, dict[str, str], bytes]:
    connection = FakeSocket(request)
    fixture.CloudMarkFixtureHandler(connection, ("127.0.0.1", 12345), FakeHttpServer())
    head, body = connection.response.getvalue().split(b"\r\n\r\n", 1)
    lines = head.decode("iso-8859-1").split("\r\n")
    status = int(lines[0].split()[1])
    headers = {
        name.lower(): value.strip()
        for line in lines[1:]
        for name, value in [line.split(":", 1)]
    }
    return status, headers, body


class WebFixtureTests(unittest.TestCase):
    def test_ready_endpoint_has_exact_non_cacheable_contract(self) -> None:
        status, headers, body = handle(b"GET /ready HTTP/1.1\r\nHost: localhost\r\n\r\n")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"ready\n")
        self.assertEqual(headers["content-type"], "text/plain")
        self.assertEqual(headers["content-length"], str(len(body)))
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(headers["x-cloudmark-fixture"], "web-http-v2")
        self.assertEqual(headers["server"], "CloudMarkFixture/2")

    def test_dynamic_endpoint_rebuilds_exact_valid_one_kib_json(self) -> None:
        first = handle(b"GET /api/v2/dynamic HTTP/1.1\r\nHost: localhost\r\n\r\n")
        second = handle(b"GET /api/v2/dynamic HTTP/1.1\r\nHost: localhost\r\n\r\n")
        self.assertEqual(first, second)
        status, headers, body = first
        self.assertEqual(status, 200)
        self.assertEqual(len(body), 1024)
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(int(headers["content-length"]), 1024)
        payload = json.loads(body)
        self.assertEqual(len(payload["digest"]), 64)
        self.assertGreater(len(payload["payload"]), 900)

    def test_unknown_query_and_unsupported_method_do_not_reach_dynamic_route(self) -> None:
        query_status, _, _ = handle(b"GET /api/v2/dynamic?unexpected=1 HTTP/1.1\r\nHost: localhost\r\n\r\n")
        post_status, _, _ = handle(b"POST /api/v2/dynamic HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n")
        self.assertEqual(query_status, 404)
        self.assertEqual(post_status, 501)

    def test_main_rejects_non_fixed_endpoint_and_closes_server(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            fixture.main(["--bind", "0.0.0.0", "--port", str(fixture.WEB_FIXTURE_PORT)])
        self.assertEqual(stopped.exception.code, 2)

        server = unittest.mock.MagicMock()
        server.serve_forever.side_effect = KeyboardInterrupt
        with patch.object(fixture, "ThreadingHTTPServer", return_value=server) as constructor:
            result = fixture.main([
                "--bind",
                fixture.WEB_FIXTURE_BIND,
                "--port",
                str(fixture.WEB_FIXTURE_PORT),
            ])
        self.assertEqual(result, 0)
        constructor.assert_called_once_with(
            (fixture.WEB_FIXTURE_BIND, fixture.WEB_FIXTURE_PORT),
            fixture.CloudMarkFixtureHandler,
        )
        self.assertTrue(server.daemon_threads)
        server.serve_forever.assert_called_once_with(poll_interval=0.25)
        server.server_close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
