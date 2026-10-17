"""Tests for the UEFN <-> Agent Canvas bridge.

The bridge's whole job is telling you whether an agent can reach UEFN, so the
tests cover both answers: a reachable editor and a missing one.
"""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from tests.fake_uefn import FakeUEFNServer  # noqa: E402
from uefn_openhands_bridge import main, probe_mcp  # noqa: E402


class BackendHandler(BaseHTTPRequestHandler):
    """Stands in for a local Agent Canvas backend."""

    def log_message(self, *args):  # keep test output clean
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/server_info":
            if self.headers.get("X-Session-API-Key") == "test-key":
                self._send(200, {"status": "ok", "version": "1.0"})
            else:
                self._send(401, {"error": "unauthorized"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if self.path == "/api/conversations":
            self._send(200, {"id": "conv-abc123"})
        else:
            self._send(404, {"error": "not found"})


class FakeBackend:
    def __init__(self):
        self.server = HTTPServer(("127.0.0.1", 0), BackendHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


class ProbeTests(unittest.TestCase):
    def test_probe_succeeds_against_real_editor(self):
        with FakeUEFNServer() as editor:
            ok, detail = probe_mcp(f"http://127.0.0.1:{editor.port}/mcp")
            self.assertTrue(ok, detail)

    def test_probe_fails_when_nothing_listening(self):
        # Port 1 is reserved and never has a listener.
        ok, detail = probe_mcp("http://127.0.0.1:1/mcp")
        self.assertFalse(ok)
        self.assertTrue(detail)


class CheckCommandTests(unittest.TestCase):
    def test_check_reports_success_with_editor_and_backend(self):
        with FakeUEFNServer() as editor, FakeBackend() as backend:
            os.environ["LOCAL_BACKEND_API_KEY"] = "test-key"
            try:
                code = main([
                    "--uefn-url", f"http://127.0.0.1:{editor.port}/mcp",
                    "--backend-url", backend.url,
                    "check",
                ])
            finally:
                os.environ.pop("LOCAL_BACKEND_API_KEY", None)
            self.assertEqual(code, 0)

    def test_check_fails_when_editor_missing(self):
        with FakeBackend() as backend:
            code = main([
                "--uefn-url", "http://127.0.0.1:1/mcp",
                "--backend-url", backend.url,
                "check",
            ])
            self.assertEqual(code, 1)

    def test_check_fails_when_backend_missing(self):
        """The key case: UEFN is up but no local backend can reach it."""
        with FakeUEFNServer() as editor:
            code = main([
                "--uefn-url", f"http://127.0.0.1:{editor.port}/mcp",
                "--backend-url", "http://127.0.0.1:1",
                "check",
            ])
            self.assertEqual(code, 1)


class StartCommandTests(unittest.TestCase):
    def test_start_creates_conversation(self):
        with FakeUEFNServer() as editor, FakeBackend() as backend:
            os.environ["LOCAL_BACKEND_API_KEY"] = "test-key"
            try:
                code = main([
                    "--uefn-url", f"http://127.0.0.1:{editor.port}/mcp",
                    "--backend-url", backend.url,
                    "start", "Add a Print to OnBegin",
                ])
            finally:
                os.environ.pop("LOCAL_BACKEND_API_KEY", None)
            self.assertEqual(code, 0)

    def test_start_refuses_when_uefn_unreachable(self):
        with FakeBackend() as backend:
            os.environ["LOCAL_BACKEND_API_KEY"] = "test-key"
            try:
                code = main([
                    "--uefn-url", "http://127.0.0.1:1/mcp",
                    "--backend-url", backend.url,
                    "start", "do something",
                ])
            finally:
                os.environ.pop("LOCAL_BACKEND_API_KEY", None)
            self.assertEqual(code, 1)

    def test_start_refuses_without_key(self):
        os.environ.pop("LOCAL_BACKEND_API_KEY", None)
        os.environ["OH_SESSION_API_KEY_PATH"] = "/nonexistent/key.txt"
        try:
            code = main(["start", "do something"])
        finally:
            os.environ.pop("OH_SESSION_API_KEY_PATH", None)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
