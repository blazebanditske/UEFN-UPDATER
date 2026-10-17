"""Tests for the UEFN Link app.

Drives the real HTTP API over a real socket, so routing, upload handling, and
the status/start decisions are exercised end to end.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests.fake_uefn import FakeUEFNServer  # noqa: E402
from tests.test_bridge import FakeBackend  # noqa: E402
import uefn_link_app as app  # noqa: E402


class AppServer:
    """Run the app's HTTP server on a real port for the duration of a test."""

    def __init__(self, workspace):
        app.WORKSPACE_DIR = workspace
        app.UPLOAD_DIR = os.path.join(workspace, "uploads")
        os.makedirs(app.UPLOAD_DIR, exist_ok=True)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.AppHandler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def get(self, path):
        try:
            with urllib.request.urlopen(f"{self.url}{path}", timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def post(self, path, payload=None, headers=None, raw=None):
        data = raw if raw is not None else (
            json.dumps(payload).encode() if payload is not None else b""
        )
        request = urllib.request.Request(f"{self.url}{path}", data=data, method="POST")
        request.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            request.add_header(k, v)
        try:
            with urllib.request.urlopen(request, timeout=20) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())


class HelperTests(unittest.TestCase):
    def test_sanitize_strips_path_traversal(self):
        self.assertEqual(app.sanitize_filename("../../etc/passwd"), "passwd")
        self.assertEqual(app.sanitize_filename("C:\\Users\\x\\art.png"), "art.png")

    def test_sanitize_keeps_extension(self):
        self.assertEqual(app.sanitize_filename("my texture!.png"), "my_texture_.png")

    def test_sanitize_handles_empty(self):
        self.assertEqual(app.sanitize_filename(""), "upload")
        self.assertEqual(app.sanitize_filename("..."), "upload")

    def test_free_port_returns_usable_port(self):
        port = app.free_port(0)
        self.assertGreater(port, 0)


class PageTests(unittest.TestCase):
    def test_page_renders_and_substitutes(self):
        page = app.build_page()
        self.assertIn("UEFN Link", page)
        self.assertNotIn("__APP_NAME__", page)
        self.assertNotIn("__BACKEND__", page)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_index_serves_html(self):
        with AppServer(self.tmp) as srv:
            with urllib.request.urlopen(f"{srv.url}/", timeout=10) as r:
                body = r.read().decode()
            self.assertEqual(r.status, 200)
            self.assertIn("UEFN Link", body)

    def test_status_reports_unreachable_when_editor_closed(self):
        original = app.UEFN_MCP_URL
        app.UEFN_MCP_URL = "http://127.0.0.1:1/mcp"
        try:
            with AppServer(self.tmp) as srv:
                _, data = srv.get("/api/status")
                self.assertFalse(data["uefn"]["ok"])
                self.assertIn("workspace", data)
        finally:
            app.UEFN_MCP_URL = original

    def test_status_reports_connected_when_editor_live(self):
        with FakeUEFNServer() as editor:
            original = app.UEFN_MCP_URL
            app.UEFN_MCP_URL = f"http://127.0.0.1:{editor.port}/mcp"
            try:
                with AppServer(self.tmp) as srv:
                    _, data = srv.get("/api/status")
                    self.assertTrue(data["uefn"]["ok"])
                    self.assertEqual(data["uefn"]["detail"], "connected")
            finally:
                app.UEFN_MCP_URL = original

    def test_upload_stores_file(self):
        with AppServer(self.tmp) as srv:
            status, data = srv.post(
                "/api/upload", raw=b"hello texture",
                headers={"X-Filename": "rock.png"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(data["saved"], "rock.png")
            self.assertEqual(data["bytes"], 13)
            with open(os.path.join(app.UPLOAD_DIR, "rock.png"), "rb") as fh:
                self.assertEqual(fh.read(), b"hello texture")

    def test_upload_does_not_overwrite(self):
        with AppServer(self.tmp) as srv:
            srv.post("/api/upload", raw=b"one", headers={"X-Filename": "a.txt"})
            _, data = srv.post("/api/upload", raw=b"two", headers={"X-Filename": "a.txt"})
            self.assertEqual(data["saved"], "a-1.txt")

    def test_upload_neutralizes_traversal_in_filename(self):
        with AppServer(self.tmp) as srv:
            _, data = srv.post(
                "/api/upload", raw=b"x", headers={"X-Filename": "../../evil.sh"}
            )
            self.assertEqual(data["saved"], "evil.sh")
            self.assertTrue(os.path.exists(os.path.join(app.UPLOAD_DIR, "evil.sh")))
            self.assertFalse(os.path.exists(os.path.join(self.tmp, "..", "evil.sh")))

    def test_upload_requires_filename(self):
        with AppServer(self.tmp) as srv:
            status, data = srv.post("/api/upload", raw=b"x")
            self.assertEqual(status, 400)

    def test_clear_removes_uploads(self):
        with AppServer(self.tmp) as srv:
            srv.post("/api/upload", raw=b"x", headers={"X-Filename": "a.txt"})
            status, data = srv.post("/api/clear")
            self.assertEqual(status, 200)
            self.assertEqual(data["removed"], 1)
            self.assertEqual(data["uploads"], [])

    def test_start_requires_task(self):
        with AppServer(self.tmp) as srv:
            status, data = srv.post("/api/start", {"task": "   "})
            self.assertEqual(status, 400)
            self.assertIn("Describe", data["error"])

    def test_start_refuses_when_editor_unreachable(self):
        original = app.UEFN_MCP_URL
        app.UEFN_MCP_URL = "http://127.0.0.1:1/mcp"
        try:
            with AppServer(self.tmp) as srv:
                status, data = srv.post("/api/start", {"task": "do a thing"})
                self.assertEqual(status, 400)
                self.assertIn("UEFN is not reachable", data["error"])
                self.assertIn("fix", data)
        finally:
            app.UEFN_MCP_URL = original

    def test_start_refuses_when_backend_down(self):
        with FakeUEFNServer() as editor:
            original_url, original_back = app.UEFN_MCP_URL, app.BACKEND_URL
            app.UEFN_MCP_URL = f"http://127.0.0.1:{editor.port}/mcp"
            app.BACKEND_URL = "http://127.0.0.1:1"
            try:
                with AppServer(self.tmp) as srv:
                    status, data = srv.post("/api/start", {"task": "do a thing"})
                    self.assertEqual(status, 400)
                    self.assertIn("backend is not available", data["error"])
            finally:
                app.UEFN_MCP_URL, app.BACKEND_URL = original_url, original_back

    def test_start_succeeds_and_passes_uploads(self):
        with FakeUEFNServer() as editor, FakeBackend() as backend:
            original_url, original_back = app.UEFN_MCP_URL, app.BACKEND_URL
            app.UEFN_MCP_URL = f"http://127.0.0.1:{editor.port}/mcp"
            app.BACKEND_URL = backend.url
            os.environ["LOCAL_BACKEND_API_KEY"] = "test-key"
            try:
                with AppServer(self.tmp) as srv:
                    srv.post("/api/upload", raw=b"art", headers={"X-Filename": "concept.png"})
                    status, data = srv.post("/api/start", {"task": "use the concept art"})
                    self.assertEqual(status, 200)
                    self.assertTrue(data["ok"])
                    self.assertEqual(data["uploads_used"], ["concept.png"])
                    self.assertIn("conv-abc123", data["url"])
            finally:
                app.UEFN_MCP_URL, app.BACKEND_URL = original_url, original_back
                os.environ.pop("LOCAL_BACKEND_API_KEY", None)

    def test_unknown_route_404(self):
        with AppServer(self.tmp) as srv:
            status, data = srv.get("/api/nope")
            self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
