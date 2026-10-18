"""Tests for the UEFN Link app.

Drives the real HTTP API over a real socket, so routing, upload handling, and
the status/start decisions are exercised end to end.
"""
import json
import os
import shutil
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
from tests.test_bridge import BackendHandler, FakeBackend  # noqa: E402
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

    def test_backend_port_does_not_collide_with_uefn(self):
        """UEFN's MCP server defaults to 8000, so the backend must not use it."""
        self.assertNotEqual(app.DEFAULT_BACKEND_PORT, 8000)
        self.assertNotIn(":8000", app.BACKEND_URL)


class LlmResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.original = app.AGENT_SETTINGS_PATH
        for var in ("LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL"):
            os.environ.pop(var, None)

    def tearDown(self):
        app.AGENT_SETTINGS_PATH = self.original
        for var in ("LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL"):
            os.environ.pop(var, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_env_override_wins(self):
        os.environ["LLM_MODEL"] = "my/model"
        os.environ["LLM_API_KEY"] = "sk-env"
        app.AGENT_SETTINGS_PATH = os.path.join(self.tmp, "none.json")
        llm = app.resolve_llm()
        self.assertEqual(llm["model"], "my/model")
        self.assertEqual(llm["api_key"], "sk-env")

    def test_reads_agent_settings_from_login(self):
        path = os.path.join(self.tmp, "agent_settings.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"llm": {"model": "openhands/deepseek-v4.1-flash",
                               "api_key": "sk-login",
                               "base_url": "https://llm-proxy.app.all-hands.dev/"}}, fh)
        app.AGENT_SETTINGS_PATH = path
        llm = app.resolve_llm()
        self.assertEqual(llm["model"], "openhands/deepseek-v4.1-flash")
        self.assertEqual(llm["api_key"], "sk-login")

    def test_missing_key_returns_none(self):
        app.AGENT_SETTINGS_PATH = os.path.join(self.tmp, "none.json")
        self.assertIsNone(app.resolve_llm())

    def test_corrupt_settings_do_not_raise(self):
        path = os.path.join(self.tmp, "agent_settings.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        app.AGENT_SETTINGS_PATH = path
        self.assertIsNone(app.resolve_llm())

    def test_status_never_leaks_the_key(self):
        os.environ["LLM_API_KEY"] = "sk-super-secret"
        ok, detail = app.llm_status()
        self.assertTrue(ok)
        self.assertNotIn("sk-super-secret", detail)


class BackendStartTests(unittest.TestCase):
    def test_start_reports_missing_canvas(self):
        original_probe = app.probe_backend
        original_find = app.find_agent_canvas
        app.probe_backend = lambda: (False, "not running")
        app.find_agent_canvas = lambda: None
        try:
            ok, detail = app.start_backend()
            self.assertFalse(ok)
            self.assertIn("agent-canvas", detail)
        finally:
            app.probe_backend = original_probe
            app.find_agent_canvas = original_find

    def test_does_not_start_a_second_instance(self):
        """A healthy backend must be reused, never duplicated.

        Regression test: start_backend used to launch unconditionally, so a
        second agent-canvas failed on its fixed internal ports (18000/18001)
        and the app reported a start failure while a working backend was up.
        """
        calls = []
        original_probe = app.probe_backend
        original_find = app.find_agent_canvas
        original_popen = app.subprocess.Popen
        app.probe_backend = lambda: (True, "running")
        app.find_agent_canvas = lambda: "/usr/bin/agent-canvas"
        app.subprocess.Popen = lambda *a, **k: calls.append(a)
        try:
            ok, detail = app.start_backend()
            self.assertTrue(ok)
            self.assertIn("already", detail)
            self.assertEqual(calls, [], "must not spawn a second agent-canvas")
        finally:
            app.probe_backend = original_probe
            app.find_agent_canvas = original_find
            app.subprocess.Popen = original_popen

    def test_reports_a_port_held_by_something_unhealthy(self):
        original_probe = app.probe_backend
        original_find = app.find_agent_canvas
        original_inuse = app.port_in_use
        app.probe_backend = lambda: (False, "not running")
        app.find_agent_canvas = lambda: "/usr/bin/agent-canvas"
        app.port_in_use = lambda p: True
        try:
            ok, detail = app.start_backend()
            self.assertFalse(ok)
            self.assertIn("in use", detail)
        finally:
            app.probe_backend = original_probe
            app.find_agent_canvas = original_find
            app.port_in_use = original_inuse


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
            os.environ["LLM_API_KEY"] = "sk-test"
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
                os.environ.pop("LLM_API_KEY", None)

    def test_start_refuses_when_no_llm_configured(self):
        with FakeUEFNServer() as editor, FakeBackend() as backend:
            original_url, original_back = app.UEFN_MCP_URL, app.BACKEND_URL
            original_settings = app.AGENT_SETTINGS_PATH
            app.UEFN_MCP_URL = f"http://127.0.0.1:{editor.port}/mcp"
            app.BACKEND_URL = backend.url
            app.AGENT_SETTINGS_PATH = os.path.join(self.tmp, "missing.json")
            os.environ["LOCAL_BACKEND_API_KEY"] = "test-key"
            for var in ("LLM_API_KEY", "LLM_MODEL"):
                os.environ.pop(var, None)
            try:
                with AppServer(self.tmp) as srv:
                    status, data = srv.post("/api/start", {"task": "do a thing"})
                    self.assertEqual(status, 400)
                    self.assertIn("No LLM configured", data["error"])
            finally:
                app.UEFN_MCP_URL, app.BACKEND_URL = original_url, original_back
                app.AGENT_SETTINGS_PATH = original_settings
                os.environ.pop("LOCAL_BACKEND_API_KEY", None)

    def test_start_sends_llm_in_payload(self):
        """The backend rejects a conversation with no `llm`, so it must be sent."""
        with FakeUEFNServer() as editor, FakeBackend() as backend:
            original_url, original_back = app.UEFN_MCP_URL, app.BACKEND_URL
            app.UEFN_MCP_URL = f"http://127.0.0.1:{editor.port}/mcp"
            app.BACKEND_URL = backend.url
            os.environ["LOCAL_BACKEND_API_KEY"] = "test-key"
            os.environ["LLM_MODEL"] = "openhands/deepseek-v4.1-flash"
            os.environ["LLM_API_KEY"] = "sk-test"
            try:
                with AppServer(self.tmp) as srv:
                    srv.post("/api/start", {"task": "do a thing"})
                    payload = BackendHandler.last_payload
                    self.assertIn("llm", payload["agent"])
                    self.assertEqual(payload["agent"]["llm"]["model"],
                                     "openhands/deepseek-v4.1-flash")
                    self.assertEqual(payload["agent"]["llm"]["api_key"], "sk-test")
            finally:
                app.UEFN_MCP_URL, app.BACKEND_URL = original_url, original_back
                os.environ.pop("LOCAL_BACKEND_API_KEY", None)
                os.environ.pop("LLM_API_KEY", None)
                os.environ.pop("LLM_MODEL", None)

    def test_unknown_route_404(self):
        with AppServer(self.tmp) as srv:
            status, data = srv.get("/api/nope")
            self.assertEqual(status, 404)


class StartupTests(unittest.TestCase):
    """The window must come up without waiting on backend startup."""

    def test_ui_serves_while_autostart_is_blocked(self):
        """The window must answer even while backend startup is stuck.

        Regression test: autostart used to run before serve_forever(), so the
        window was unreachable for as long as the backend took to start -- up
        to 90s on a cold start -- and the CI smoke test timed out.
        """
        tmp = tempfile.mkdtemp()
        blocked = threading.Event()
        release = threading.Event()
        port = app.free_port(0)

        def blocking_start_backend(port=None):
            blocked.set()
            release.wait(timeout=15)  # stand in for a slow backend
            return True, "running"

        orig_probe, orig_start = app.probe_backend, app.start_backend
        app.probe_backend = lambda: (False, "not running")
        app.start_backend = blocking_start_backend
        main_thread = threading.Thread(
            target=app.main,
            args=(["--port", str(port), "--no-browser", "--workspace", tmp],),
            daemon=True)
        try:
            main_thread.start()
            # Autostart is now in flight and deliberately stuck.
            self.assertTrue(blocked.wait(timeout=10), "autostart never began")
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
                self.assertIn("UEFN Link", r.read().decode())
        finally:
            release.set()
            app.probe_backend, app.start_backend = orig_probe, orig_start
            shutil.rmtree(tmp, ignore_errors=True)

    def test_backend_start_is_not_inline_before_serving(self):
        """main() must start the server before it attempts autostart."""
        src = open(os.path.join(ROOT, "uefn_link_app.py")).read()
        body = src.split("def main(", 1)[1]
        self.assertIn("threading.Thread(target=autostart", body)
        # The autostart call must be inside a thread target, not run inline.
        self.assertNotIn("started, detail = start_backend()\n\n    try:", body)
        serve_at = body.index("server.serve_forever()")
        autostart_at = body.index("def autostart")
        self.assertLess(autostart_at, serve_at,
                        "autostart must be defined before the serve loop, "
                        "and run on its own thread")


class UefnConfigTests(unittest.TestCase):
    """The INI writer must be surgical: UEFN's prefs file holds everything else
    the user has set, and a bad write there is worse than no write."""

    SECTION = app.MCP_INI_SECTION

    def test_adds_section_to_empty_file(self):
        text, changed = app.set_ini_keys("", self.SECTION, {"bAutoStartServer": "True"})
        self.assertTrue(changed)
        self.assertIn(self.SECTION, text)
        self.assertIn("bAutoStartServer=True", text)

    def test_flips_existing_false_to_true(self):
        original = f"{self.SECTION}\nbAutoStartServer=False\nServerPortNumber=8000\n"
        text, changed = app.set_ini_keys(
            original, self.SECTION, {"bAutoStartServer": "True"})
        self.assertTrue(changed)
        self.assertIn("bAutoStartServer=True", text)
        self.assertNotIn("bAutoStartServer=False", text)

    def test_is_idempotent(self):
        original = f"{self.SECTION}\nbAutoStartServer=True\n"
        _, changed = app.set_ini_keys(original, self.SECTION, {"bAutoStartServer": "True"})
        self.assertFalse(changed)

    def test_preserves_other_sections_and_keys(self):
        original = (
            "[/Script/UnrealEd.SomethingElse]\nKeepMe=1\n\n"
            f"{self.SECTION}\nbAutoStartServer=False\n\n"
            "[/Script/ValkyrieEditor.ValkyrieEditorConfig]\nOther=2\n"
        )
        text, _ = app.set_ini_keys(original, self.SECTION, {"bAutoStartServer": "True"})
        self.assertIn("KeepMe=1", text)
        self.assertIn("Other=2", text)
        self.assertIn("bAutoStartServer=True", text)

    def test_does_not_touch_a_same_named_key_in_another_section(self):
        original = (
            "[/Script/Other.Settings]\nbAutoStartServer=False\n\n"
            f"{self.SECTION}\nbAutoStartServer=False\n"
        )
        text, _ = app.set_ini_keys(original, self.SECTION, {"bAutoStartServer": "True"})
        # The first section's value must survive untouched.
        self.assertIn("[/Script/Other.Settings]\nbAutoStartServer=False", text)
        self.assertIn("bAutoStartServer=True", text)

    def test_configure_writes_and_backs_up(self):
        ini = os.path.join(self.tmp, "EditorPerProjectUserSettings.ini")
        with open(ini, "w", encoding="utf-8") as fh:
            fh.write("[Unrelated]\nx=1\n")
        original = app.uefn_ini_path
        app.uefn_ini_path = lambda: ini
        try:
            ok, detail = app.configure_uefn_autostart()
            self.assertTrue(ok, detail)
            self.assertTrue(os.path.exists(ini + ".uefn-link.bak"))
            with open(ini, encoding="utf-8") as fh:
                body = fh.read()
            self.assertIn("bAutoStartServer=True", body)
            self.assertIn("[Unrelated]", body)
        finally:
            app.uefn_ini_path = original

    def test_configure_reports_missing_file(self):
        original = app.uefn_ini_path
        app.uefn_ini_path = lambda: os.path.join(self.tmp, "nope.ini")
        try:
            ok, detail = app.configure_uefn_autostart()
            self.assertFalse(ok)
            self.assertIn("not found", detail)
        finally:
            app.uefn_ini_path = original

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class ProjectFlagTests(unittest.TestCase):
    """The .uefnproject edit must be additive and reversible."""

    def _make_project(self, body):
        root = tempfile.mkdtemp()
        path = os.path.join(root, ".uefnproject")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(body, fh)
        return root, path

    def test_sets_both_beta_flags(self):
        root, path = self._make_project({"dataSets": {}})
        try:
            ok, detail = app.enable_project_beta_flags(root)
            self.assertTrue(ok, detail)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            exp = data["dataSets"]["experimental"]
            self.assertTrue(exp["pythonExperimental"]["bEnablePythonForProject"])
            self.assertTrue(exp["toolsets"]["bEnableToolsetsForProject"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_preserves_unrelated_project_data(self):
        root, path = self._make_project({
            "dataSets": {"other": {"keep": "me"}}, "topLevel": [1, 2, 3]})
        try:
            app.enable_project_beta_flags(root)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["dataSets"]["other"], {"keep": "me"})
            self.assertEqual(data["topLevel"], [1, 2, 3])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_second_run_is_a_no_op(self):
        root, _ = self._make_project({"dataSets": {}})
        try:
            app.enable_project_beta_flags(root)
            ok, detail = app.enable_project_beta_flags(root)
            self.assertTrue(ok)
            self.assertIn("already", detail)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_bad_json_is_reported_not_raised(self):
        root = tempfile.mkdtemp()
        try:
            with open(os.path.join(root, ".uefnproject"), "w", encoding="utf-8") as fh:
                fh.write("{not json")
            ok, detail = app.enable_project_beta_flags(root)
            self.assertFalse(ok)
            self.assertIn("could not read", detail)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class LlmSaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.original = app.AGENT_SETTINGS_PATH
        app.AGENT_SETTINGS_PATH = os.path.join(self.tmp, "agent_settings.json")

    def tearDown(self):
        app.AGENT_SETTINGS_PATH = self.original
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_saved_key_is_then_resolved(self):
        ok, _ = app.save_llm_config("anthropic/claude-sonnet-4-5-20250929", "sk-abc", "https://api.anthropic.com")
        self.assertTrue(ok)
        llm = app.resolve_llm()
        self.assertEqual(llm["api_key"], "sk-abc")
        self.assertEqual(llm["model"], "anthropic/claude-sonnet-4-5-20250929")

    @unittest.skipIf(os.name == "nt", "Windows has no POSIX mode bits")
    def test_key_file_is_owner_only(self):
        app.save_llm_config("m", "sk-secret", "")
        mode = os.stat(app.AGENT_SETTINGS_PATH).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_refuses_empty_key(self):
        ok, detail = app.save_llm_config("m", "  ", "")
        self.assertFalse(ok)
        self.assertIn("key", detail)

    def test_preserves_other_settings_in_the_file(self):
        with open(app.AGENT_SETTINGS_PATH, "w", encoding="utf-8") as fh:
            json.dump({"llm": {"model": "old"}, "other": {"keep": True}}, fh)
        app.save_llm_config("new/model", "sk-new", "")
        with open(app.AGENT_SETTINGS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["other"], {"keep": True})
        self.assertEqual(data["llm"]["model"], "new/model")


class FixAllTests(unittest.TestCase):
    """fix-all must keep going after a failure so the user sees every problem,
    and must never claim success while the credential is missing."""

    def test_reports_manual_step_when_no_key(self):
        tmp = tempfile.mkdtemp()
        patches = {
            "install_backend_dependencies": lambda progress=None: (True, "installed"),
            "start_backend": lambda port=None: (True, "started"),
            "configure_uefn_autostart": lambda: (True, "ini ok"),
            "enable_project_beta_flags": lambda project=None: (True, "flags ok"),
            "probe_uefn": lambda url: (True, "connected"),
            "llm_status": lambda: (False, "no key"),
            "WORKSPACE_DIR": tmp,
        }
        originals = {k: getattr(app, k) for k in patches}
        for k, v in patches.items():
            setattr(app, k, v)
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), app.AppHandler)
            port = server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/fix-all", method="POST", data=b"")
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.loads(resp.read().decode())
            server.shutdown()
            server.server_close()
            self.assertFalse(body["ok"])
            self.assertIn("model credential", body["needs"])
            manual = [s for s in body["steps"] if s["step"] == "model credential"][0]
            self.assertTrue(manual["manual"])
            # Every other step still ran and reported.
            self.assertGreaterEqual(len(body["steps"]), 6)
        finally:
            for k, v in originals.items():
                setattr(app, k, v)
            shutil.rmtree(tmp, ignore_errors=True)


class StatusPayloadTests(unittest.TestCase):
    def test_status_exposes_env_and_presets(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.AppHandler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=15) as r:
                body = json.loads(r.read().decode())
        finally:
            server.shutdown()
            server.server_close()
        self.assertIn("env", body)
        self.assertIn("uefn_setup", body)
        self.assertIn("presets", body)
        self.assertIn("node", body["env"])
        self.assertTrue(body["presets"])

    def test_page_has_the_fix_controls(self):
        page = app.build_page()
        self.assertIn("Fix everything", page)
        self.assertIn("/api/fix-all", page)
        self.assertIn("/api/llm/save", page)
        self.assertIn("/api/backend/install", page)
        self.assertIn("__APP_NAME__", app.PAGE)  # placeholder is in the template
        self.assertNotIn("__APP_NAME__", page)   # and substituted in the output


if __name__ == "__main__":
    unittest.main()
