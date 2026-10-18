"""UEFN Link App: one window that connects UEFN to OpenHands agents.

Double-click it. It runs a small local web app on 127.0.0.1, opens your
browser, and gives you four things:

  1. Live status    -- is UEFN reachable, is an agent backend running?
  2. Uploads        -- drag in textures, videos, docs, concept art, anything.
  3. Instructions   -- type what you want changed, in plain language.
  4. Launch         -- starts an agent that edits the open UEFN project live.

Everything runs on your machine, so the agent's execution environment is the
same machine as the editor. That is the whole trick: UEFN's MCP server is
loopback-only, so the agent has to be local too.

Stdlib only. No install, no dependencies.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_NAME = "UEFN Link"
APP_VERSION = "1.2.0"
DEFAULT_APP_PORT = 8765
UEFN_MCP_URL = os.environ.get("UEFN_MCP_URL", "http://127.0.0.1:8000/mcp")
# UEFN's embedded MCP server owns 8000 by default. The agent backend must not
# default to the same port or the two would compete for it and the status lights
# would read the wrong service.
DEFAULT_BACKEND_PORT = 3000
BACKEND_URL = os.environ.get(
    "LOCAL_AGENT_SERVER_URL", f"http://127.0.0.1:{DEFAULT_BACKEND_PORT}"
)
CLOUD_CANVAS_URL = "https://app.all-hands.dev/canvas/conversations"

HOME_DIR = os.path.expanduser("~")
WORKSPACE_DIR = os.environ.get("UEFN_AGENT_WORKSPACE", os.path.join(HOME_DIR, "UEFN-Agent"))
UPLOAD_DIR = os.path.join(WORKSPACE_DIR, "uploads")

# Written by `openhands login`, which authenticates against the user's personal
# OpenHands Cloud account and stores the resulting agent spec (model, proxy base
# URL, minted key).
AGENT_SETTINGS_PATH = os.path.join(HOME_DIR, ".openhands", "agent_settings.json")

# Used when nothing more specific is configured. This is the personal Cloud
# default model; the proxy URL is what OpenHands Cloud issues keys for.
PERSONAL_LLM_MODEL = "openhands/deepseek-v4.1-flash"
PERSONAL_LLM_BASE_URL = "https://llm-proxy.app.all-hands.dev/"

# UEFN's per-user editor preferences. Editor Preferences > Model Context
# Protocol writes here, and it is plain INI, so Auto Start Server and the port
# can be set without touching the GUI. Section and key names come from Epic's
# Unreal MCP documentation. Unknown keys are ignored by the engine, so writing
# them is harmless on a build that predates the feature.
MCP_INI_SECTION = "[/Script/ModelContextProtocol.ModelContextProtocolSettings]"
MCP_INI_KEYS = {
    "bAutoStartServer": "True",
    "ServerPortNumber": "8000",
    "ServerUrlPath": "/mcp",
}

# Project-level beta flags, stored in the project's .uefnproject file. These are
# the two checkboxes the setup steps ask for: Python Editor Scripting and the
# UEFN MCP Toolsets beta.
UEFN_PROJECT_FLAGS = (
    ("pythonExperimental", "bEnablePythonForProject"),
    ("toolsets", "bEnableToolsetsForProject"),
)

# Model presets, so a working configuration is a paste away rather than a
# hand-edited environment variable. No keys ship with the app.
LLM_PRESETS = [
    {
        "id": "openhands",
        "label": "OpenHands Cloud (personal account)",
        "model": PERSONAL_LLM_MODEL,
        "base_url": PERSONAL_LLM_BASE_URL,
        "key_hint": "Run `openhands login`, or paste a Cloud key",
    },
    {
        "id": "anthropic",
        "label": "Anthropic (Claude)",
        "model": "anthropic/claude-sonnet-4-5-20250929",
        "base_url": "https://api.anthropic.com",
        "key_hint": "sk-ant-...",
    },
    {
        "id": "openai",
        "label": "OpenAI",
        "model": "openai/gpt-5.1",
        "base_url": "https://api.openai.com/v1",
        "key_hint": "sk-...",
    },
]

# Uploads can be large (videos, level files). Cap generously but not without end.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

UEFN_CONTEXT = """\
You are working on a LIVE Unreal Editor for Fortnite (UEFN) project through its
embedded Unreal MCP server, using the `uefn-link` client available in this
workspace.

Hard rules:
- UEFN's MCP server is loopback-only and unauthenticated. Never bind, tunnel,
  or expose it. Reach it only at 127.0.0.1.
- UEFN MCP is a beta and tool names change. Resolve tools against the live list
  (`uefn-link tools`, `uefn-link capabilities`) instead of assuming names.
- ALWAYS compile Verse after writing it (`uefn-link verse build`). An
  uncompiled write proves nothing.
- Only properties visible in the UEFN interface can be written. Read back with
  `uefn-link device properties`, and run `uefn-link validate` after a batch.
- New assets (textures, meshes) need `uefn-link session cycle --restart` to
  appear. Verse edits only need `uefn-link session cycle`.
- Coordinates: Python-facing toolsets use XYZ, UEFN uses left-up-forward. Use
  the built-in conversion; do not hand-adjust numbers.

Begin by running `uefn-link doctor` and reporting the result in one line.
"""


# --- small helpers ---------------------------------------------------------
def sanitize_filename(name: str) -> str:
    """Reduce an uploaded name to something safe to join onto a directory."""
    base = os.path.basename(name or "").replace("\\", "/").split("/")[-1]
    base = SAFE_NAME.sub("_", base).strip("._-")
    return base[:120] or "upload"


def free_port(preferred: int) -> int:
    """Return `preferred` if free, else an OS-assigned port."""
    for candidate in (preferred, 0):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", candidate))
                return probe.getsockname()[1]
            except OSError:
                continue
    return preferred


def port_in_use(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def http_json(url, payload=None, headers=None, timeout=8):
    """JSON request helper. Returns (status_or_None, parsed_or_text)."""
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode()
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError:
                return response.status, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:300]
    except Exception as exc:
        return None, str(exc)


def probe_uefn(url: str) -> tuple[bool, str]:
    """Handshake with UEFN's MCP server. True means the editor is live."""
    status, body = http_json(
        url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "uefn-link-app", "version": APP_VERSION},
            },
        },
        {"Accept": "application/json, text/event-stream"},
    )
    if status is None:
        return False, "nothing listening"
    if status != 200:
        return False, f"HTTP {status}"
    return True, "connected"


def backend_key() -> str | None:
    """Read the local backend's session key. Never logged or returned to the UI."""
    if os.environ.get("LOCAL_BACKEND_API_KEY"):
        return os.environ["LOCAL_BACKEND_API_KEY"]
    path = os.environ.get(
        "OH_SESSION_API_KEY_PATH",
        os.path.join(HOME_DIR, ".openhands", "agent-canvas", "api-key.txt"),
    )
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return None
    return None


def probe_backend() -> tuple[bool, str]:
    key = backend_key()
    status, _ = http_json(
        f"{BACKEND_URL}/server_info",
        headers={"X-Session-API-Key": key} if key else {},
        timeout=4,
    )
    if status == 200:
        return True, "running"
    if status == 401:
        return False, "running but key rejected"
    return False, "not running"


def which_any(*names) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def npm_global_prefix() -> str | None:
    """Where npm puts global binaries, so the app can find agent-canvas even
    when the shell PATH has not been reloaded since the install."""
    try:
        proc = subprocess.run(
            ["npm", "prefix", "-g"], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    prefix = proc.stdout.strip()
    return prefix or None


def find_agent_canvas() -> str | None:
    """Locate agent-canvas on PATH, or in the usual global install locations."""
    found = which_any("agent-canvas", "agent-canvas.cmd")
    if found:
        return found
    prefix = npm_global_prefix()
    if prefix:
        for candidate in (
            os.path.join(prefix, "bin", "agent-canvas"),
            os.path.join(prefix, "agent-canvas.cmd"),
            os.path.join(prefix, "agent-canvas"),
        ):
            if os.path.isfile(candidate):
                return candidate
    return None


def environment_report() -> dict:
    """Everything the app can learn about this machine without changing it."""
    node = which_any("node", "node.exe")
    npm = which_any("npm", "npm.cmd")
    canvas = find_agent_canvas()
    return {
        "node": bool(node),
        "node_path": node,
        "npm": bool(npm),
        "uv": bool(which_any("uv", "uv.exe", "uvx", "uvx.exe")),
        "agent_canvas": bool(canvas),
        "agent_canvas_path": canvas,
        "openhands_cli": has_openhands_cli(),
        "uefn_ini": uefn_ini_path(),
        "uefn_project": find_uefn_project(),
        "platform": sys.platform,
    }


def install_backend_dependencies(progress=None) -> tuple[bool, str]:
    """Install agent-canvas globally with npm. This is the one download the app
    performs, and only when the user asks for it.

    Node and npm are prerequisites; the app never installs those, because a
    silent Node install is a much bigger intervention than fetching one package.
    """
    def say(message):
        if progress:
            progress(message)

    if find_agent_canvas():
        return True, "agent-canvas is already installed"

    if not which_any("npm", "npm.cmd"):
        return False, (
            "npm was not found. Install Node.js 24 or later from "
            "https://nodejs.org, reopen this app, then try again."
        )

    prefix = npm_global_prefix()
    if prefix and not os.access(prefix, os.W_OK):
        return False, (
            f"npm's global folder ({prefix}) is not writable by this user, so "
            "a global install cannot succeed. Either run this app as "
            "Administrator, or point npm at a folder you own:\n"
            f"    npm config set prefix \"{os.path.join(HOME_DIR, 'npm-global')}\"\n"
            "then reopen this app and press Fix again."
        )

    say("Installing agent-canvas (this downloads a few MB)…")
    try:
        proc = subprocess.run(
            ["npm", "install", "-g", "@openhands/agent-canvas"],
            capture_output=True, text=True, timeout=900,
        )
    except subprocess.TimeoutExpired:
        return False, "npm install timed out after 15 minutes"
    except OSError as exc:
        return False, f"could not run npm: {exc}"

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        return False, f"npm install failed: {tail}"

    if not find_agent_canvas():
        return False, (
            "Installed, but agent-canvas is not on PATH yet. Restart this app "
            "so it picks up the new PATH entry."
        )
    return True, "agent-canvas installed"


def uefn_ini_path() -> str | None:
    """Path to UEFN's per-user editor preferences, if it exists.

    Windows is the only platform UEFN ships on, but the other paths are cheap
    and make the app testable and honest on any machine.
    """
    candidates = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(
            local, "UnrealEditorFortnite", "Saved", "Config",
            "WindowsEditor", "EditorPerProjectUserSettings.ini",
        ))
    candidates.append(os.path.join(
        HOME_DIR, ".config", "UnrealEditorFortnite", "Saved", "Config",
        "WindowsEditor", "EditorPerProjectUserSettings.ini",
    ))
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0] if candidates else None


def set_ini_keys(text: str, section: str, keys: dict) -> tuple[str, bool]:
    """Return `text` with `keys` set under `section`, and whether it changed.

    Only the named keys are touched; everything else in the file is preserved
    byte for byte, because this file holds the user's other editor settings.
    """
    changed = False
    if section in text:
        head, rest = text.split(section, 1)
        # A section body ends at the next section header.
        body, sep, tail = rest.partition("\n[")
        for key, value in keys.items():
            pattern = rf"^{re.escape(key)}=.*$"
            if re.search(pattern, body, re.M):
                new_body = re.sub(pattern, f"{key}={value}", body, flags=re.M)
            else:
                new_body = body.rstrip("\n") + f"\n{key}={value}\n"
            if new_body != body:
                changed = True
                body = new_body
        text = head + section + body + (sep + tail if sep else "")
    else:
        text = text.rstrip("\n") + "\n\n" + section + "\n"
        for key, value in keys.items():
            text += f"{key}={value}\n"
        changed = True
    return text, changed


def read_ini_keys(path: str, section: str, keys) -> dict:
    """Current values for `keys` under `section`, for display."""
    found = {key: None for key in keys}
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return found
    if section not in text:
        return found
    body = text.split(section, 1)[1].partition("\n[")[0]
    for key in keys:
        match = re.search(rf"^{re.escape(key)}=(.*)$", body, re.M)
        if match:
            found[key] = match.group(1).strip()
    return found


def configure_uefn_autostart() -> tuple[bool, str]:
    """Enable Auto Start Server so the editor serves MCP on every launch.

    Without this the server only runs if the user starts it by hand each
    session, which is the "nothing listening" state.
    """
    path = uefn_ini_path()
    if not path or not os.path.exists(path):
        return False, (
            "UEFN's preferences file was not found. Open UEFN once so it "
            "writes its settings, then try again."
        )
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        return False, f"could not read UEFN preferences: {exc}"

    updated, changed = set_ini_keys(text, MCP_INI_SECTION, MCP_INI_KEYS)
    if not changed:
        return True, "Auto Start Server was already on"

    backup = path + ".uefn-link.bak"
    try:
        if not os.path.exists(backup):
            shutil.copy2(path, backup)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(updated)
    except OSError as exc:
        return False, f"could not write UEFN preferences: {exc}"
    return True, "Auto Start Server enabled (takes effect next UEFN start)"


def find_uefn_project() -> str | None:
    """Best guess at the UEFN project folder the user is working on."""
    explicit = os.environ.get("UEFN_PROJECT_PATH")
    if explicit and os.path.isdir(explicit):
        return explicit
    candidates = []
    for env in ("USERPROFILE", "HOME"):
        base = os.environ.get(env)
        if base:
            candidates.append(os.path.join(base, "Documents", "Fortnite Projects"))
    for root in candidates:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if os.path.isfile(os.path.join(path, ".uefnproject")):
                return path
    return None


def enable_project_beta_flags(project: str | None = None) -> tuple[bool, str]:
    """Set the Python and MCP Toolset beta flags in the project's .uefnproject.

    This mirrors the two checkboxes the manual setup asks for. It is additive:
    only those two keys are written, and a backup is kept.
    """
    project = project or find_uefn_project()
    if not project:
        return False, (
            "No UEFN project folder found. Set UEFN_PROJECT_PATH to the folder "
            "that contains your .uefnproject file."
        )
    marker = os.path.join(project, ".uefnproject")
    if not os.path.isfile(marker):
        return False, f"no .uefnproject in {project}"

    try:
        with open(marker, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"could not read .uefnproject: {exc}"

    experimental = data.setdefault("dataSets", {}).setdefault("experimental", {})
    changed = False
    for group, key in UEFN_PROJECT_FLAGS:
        bucket = experimental.setdefault(group, {})
        if bucket.get(key) is not True:
            bucket[key] = True
            changed = True

    if not changed:
        return True, "Project flags were already on"

    backup = marker + ".uefn-link.bak"
    try:
        if not os.path.exists(backup):
            shutil.copy2(marker, backup)
        with open(marker, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent="\t", ensure_ascii=False)
    except OSError as exc:
        return False, f"could not write .uefnproject: {exc}"
    return True, (
        "Enabled Python Editor Scripting and UEFN MCP Toolsets in the project. "
        "Restart UEFN for them to apply."
    )


def uefn_setup_report() -> dict:
    """What the app can tell about the editor side of the link, for display."""
    path = uefn_ini_path()
    ini = read_ini_keys(path, MCP_INI_SECTION, MCP_INI_KEYS.keys()) if path else {}
    project = find_uefn_project()
    flags = {}
    if project:
        try:
            with open(os.path.join(project, ".uefnproject"), encoding="utf-8") as fh:
                data = json.load(fh)
            experimental = (data.get("dataSets") or {}).get("experimental") or {}
            for group, key in UEFN_PROJECT_FLAGS:
                flags[key] = (experimental.get(group) or {}).get(key) is True
        except (OSError, json.JSONDecodeError):
            flags = {}
    return {
        "ini_path": path,
        "ini_exists": bool(path and os.path.exists(path)),
        "ini": ini,
        "project": project,
        "project_flags": flags,
    }


def save_llm_config(model: str, api_key: str, base_url: str) -> tuple[bool, str]:
    """Persist a model choice so the agent can actually run.

    Written to ~/.openhands/agent_settings.json, the same file `openhands
    login` uses, so the two paths agree and the app does not invent a second
    config format. The file is chmod 600 where the platform supports it.
    """
    model = (model or "").strip()
    api_key = (api_key or "").strip()
    base_url = (base_url or "").strip()
    if not api_key:
        return False, "an API key is required"
    if not model:
        return False, "a model name is required"

    spec = {}
    try:
        with open(AGENT_SETTINGS_PATH, encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            spec = loaded
    except (OSError, json.JSONDecodeError):
        spec = {}

    spec["llm"] = {
        "model": model,
        "api_key": api_key,
        "base_url": base_url or PERSONAL_LLM_BASE_URL,
    }
    try:
        os.makedirs(os.path.dirname(AGENT_SETTINGS_PATH), exist_ok=True)
        with open(AGENT_SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump(spec, handle, indent=2)
        try:
            os.chmod(AGENT_SETTINGS_PATH, 0o600)
        except OSError:
            pass
    except OSError as exc:
        return False, f"could not save the key: {exc}"
    return True, f"saved; the agent will use {model}"


def has_openhands_cli() -> bool:
    return shutil.which("openhands") is not None


def staging_summary() -> list[dict]:
    """List staged uploads with sizes."""
    if not os.path.isdir(UPLOAD_DIR):
        return []
    items = []
    for name in sorted(os.listdir(UPLOAD_DIR)):
        path = os.path.join(UPLOAD_DIR, name)
        if os.path.isfile(path):
            items.append({"name": name, "bytes": os.path.getsize(path)})
    return items


def has_agent_canvas() -> bool:
    """Kept as the single question the UI asks: is the backend installed?"""
    return find_agent_canvas() is not None


def resolve_llm() -> dict | None:
    """Work out which model/key the local agent should use.

    Order of preference:
      1. Explicit env override (LLM_MODEL / LLM_API_KEY / LLM_BASE_URL).
      2. ~/.openhands/agent_settings.json, written by `openhands login`. This is
         the user's personal OpenHands Cloud configuration.
      3. The personal Cloud defaults, which need a key from somewhere.

    Returns None when no usable key exists, so the caller can explain what is
    missing instead of starting a conversation that fails on its first call.
    """
    model = os.environ.get("LLM_MODEL")
    api_key = os.environ.get("LLM_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL")

    if not (model and api_key):
        try:
            with open(AGENT_SETTINGS_PATH, encoding="utf-8") as handle:
                spec = json.load(handle)
            llm = spec.get("llm") or {}
            model = model or llm.get("model")
            api_key = api_key or llm.get("api_key")
            base_url = base_url or llm.get("base_url")
        except (OSError, json.JSONDecodeError, AttributeError):
            pass

    if not api_key:
        return None

    return {
        "model": model or PERSONAL_LLM_MODEL,
        "api_key": api_key,
        "base_url": base_url or PERSONAL_LLM_BASE_URL,
    }


def llm_status() -> tuple[bool, str]:
    """Human-readable summary of the LLM configuration, without leaking the key."""
    llm = resolve_llm()
    if llm is None:
        return False, "no key — run: openhands login"
    return True, llm["model"]


def start_backend(port: int = DEFAULT_BACKEND_PORT) -> tuple[bool, str]:
    """Start `agent-canvas --backend-only` and wait for it to answer.

    The editor's MCP server is loopback-only, so the agent has to run on this
    same machine. Returns as soon as the server responds, or after a timeout.
    Starting when one is already healthy is a no-op, and a port held by an
    unrelated process is reported rather than worked around, because
    agent-canvas also claims fixed internal ports.
    """
    # Already up? Do not start a second instance. agent-canvas also binds
    # internal ports (agent-server 18000, automation 18001) that a running
    # instance already holds, so a second launch fails no matter which ingress
    # port it is given.
    ok, _ = probe_backend()
    if ok:
        return True, "already running"

    canvas = find_agent_canvas()
    if not canvas:
        return False, (
            "agent-canvas is not installed. Press 'Fix' on the backend row, or "
            "run:  npm install -g @openhands/agent-canvas"
        )

    if port_in_use(port):
        # Something else owns the port and it is not a healthy backend we can
        # use. Starting anyway would collide on the internal ports too.
        return False, (
            f"port {port} is in use by another process, and the agent backend "
            "is not answering on it. Stop whatever is using it (often a second "
            "agent-canvas), then try again."
        )

    log_path = os.path.join(WORKSPACE_DIR, "backend.log")
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    try:
        with open(log_path, "ab") as log:
            subprocess.Popen(
                [canvas, "--backend-only", "--port", str(port)],
                stdout=log,
                stderr=log,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
    except OSError as exc:
        return False, f"could not launch agent-canvas: {exc}"

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        ok, detail = probe_backend()
        if ok:
            return True, "started"
        time.sleep(2)
    return False, f"started but not answering after 90s (see {log_path})"


# --- server ----------------------------------------------------------------
class AppHandler(BaseHTTPRequestHandler):
    server_version = f"UEFNLinkApp/{APP_VERSION}"

    def log_message(self, *args):
        pass  # keep the console readable

    # -- responses
    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text):
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD_BYTES:
            return None
        remaining, chunks = length, []
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    # -- routes
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._html(build_page())
        elif self.path == "/api/status":
            self._status()
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/upload":
            self._upload()
        elif self.path == "/api/start":
            self._start()
        elif self.path == "/api/backend/start":
            self._backend_start()
        elif self.path == "/api/backend/install":
            self._backend_install()
        elif self.path == "/api/uefn/fix":
            self._uefn_fix()
        elif self.path == "/api/llm/save":
            self._llm_save()
        elif self.path == "/api/fix-all":
            self._fix_all()
        elif self.path == "/api/register":
            self._register()
        elif self.path == "/api/clear":
            self._clear()
        else:
            self._json(404, {"error": "not found"})

    # -- handlers
    def _status(self):
        uefn_ok, uefn_msg = probe_uefn(UEFN_MCP_URL)
        back_ok, back_msg = probe_backend()
        llm_ok, llm_msg = llm_status()
        self._json(200, {
            "uefn": {"ok": uefn_ok, "detail": uefn_msg, "url": UEFN_MCP_URL},
            "backend": {"ok": back_ok, "detail": back_msg, "url": BACKEND_URL},
            "llm": {"ok": llm_ok, "detail": llm_msg},
            "cli": has_openhands_cli(),
            "canvas": has_agent_canvas(),
            "workspace": WORKSPACE_DIR,
            "uploads": staging_summary(),
            "cloud_url": CLOUD_CANVAS_URL,
            "env": environment_report(),
            "uefn_setup": uefn_setup_report(),
            "presets": LLM_PRESETS,
            "version": APP_VERSION,
        })

    def _backend_start(self):
        ok, detail = start_backend()
        self._json(200 if ok else 500, {"ok": ok, "detail": detail})

    def _backend_install(self):
        ok, detail = install_backend_dependencies()
        self._json(200 if ok else 500, {"ok": ok, "detail": detail})

    def _uefn_fix(self):
        results = []
        ok_ini, ini_detail = configure_uefn_autostart()
        results.append({"step": "autostart", "ok": ok_ini, "detail": ini_detail})
        ok_flags, flags_detail = enable_project_beta_flags()
        results.append({"step": "project flags", "ok": ok_flags, "detail": flags_detail})
        self._json(200, {"ok": all(r["ok"] for r in results), "results": results})

    def _llm_save(self):
        body = self._read_body() or b"{}"
        try:
            request = json.loads(body.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json(400, {"error": "malformed request"})
        ok, detail = save_llm_config(
            request.get("model", ""),
            request.get("api_key", ""),
            request.get("base_url", ""),
        )
        # Never echo the key back.
        self._json(200 if ok else 400, {"ok": ok, "detail": detail})

    def _fix_all(self):
        """One call that does everything this machine can do by itself."""
        steps = []

        ok, detail = install_backend_dependencies()
        steps.append({"step": "install backend", "ok": ok, "detail": detail})

        ok, detail = start_backend()
        steps.append({"step": "start backend", "ok": ok, "detail": detail})

        ok_ini, ini_detail = configure_uefn_autostart()
        steps.append({"step": "UEFN auto-start", "ok": ok_ini, "detail": ini_detail})

        ok_flags, flags_detail = enable_project_beta_flags()
        steps.append({"step": "UEFN project flags", "ok": ok_flags, "detail": flags_detail})

        uefn_ok, uefn_detail = probe_uefn(UEFN_MCP_URL)
        steps.append({
            "step": "UEFN connection",
            "ok": uefn_ok,
            "detail": uefn_detail if uefn_ok else
                      f"{uefn_detail} — restart UEFN with the project open",
        })

        llm_ok, llm_detail = llm_status()
        steps.append({
            "step": "model credential",
            "ok": llm_ok,
            "detail": llm_detail if llm_ok else
                      "needs your key — enter it below (the one step that is not automatable)",
            "manual": not llm_ok,
        })

        self._json(200, {
            "ok": all(s["ok"] for s in steps),
            "steps": steps,
            "needs": [s["step"] for s in steps if not s["ok"]],
        })

    def _upload(self):
        raw_name = self.headers.get("X-Filename", "")
        if not raw_name.strip():
            return self._json(400, {"error": "missing filename"})
        name = sanitize_filename(raw_name)
        data = self._read_body()
        if data is None:
            return self._json(413, {"error": "file too large"})
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        # Keep the original extension but never overwrite silently.
        target = os.path.join(UPLOAD_DIR, name)
        stem, ext = os.path.splitext(name)
        counter = 1
        while os.path.exists(target):
            target = os.path.join(UPLOAD_DIR, f"{stem}-{counter}{ext}")
            counter += 1
        try:
            with open(target, "wb") as handle:
                handle.write(data)
        except OSError as exc:
            return self._json(500, {"error": f"could not save: {exc}"})
        self._json(200, {
            "saved": os.path.basename(target),
            "bytes": len(data),
            "uploads": staging_summary(),
        })

    def _register(self):
        if not has_openhands_cli():
            return self._json(400, {
                "error": "The `openhands` CLI was not found.",
                "fix": "Install it with:  uv tool install openhands --python 3.12",
            })
        proc = subprocess.run(
            ["openhands", "mcp", "add", "uefn", "--transport", "http", UEFN_MCP_URL],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            return self._json(500, {"error": (proc.stderr or proc.stdout).strip()[:400]})
        self._json(200, {"ok": True, "message": "Registered. New conversations only."})

    def _clear(self):
        removed = 0
        if os.path.isdir(UPLOAD_DIR):
            for name in os.listdir(UPLOAD_DIR):
                path = os.path.join(UPLOAD_DIR, name)
                if os.path.isfile(path):
                    try:
                        os.remove(path)
                        removed += 1
                    except OSError:
                        pass
        self._json(200, {"removed": removed, "uploads": staging_summary()})

    def _start(self):
        body = self._read_body() or b"{}"
        try:
            request = json.loads(body.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json(400, {"error": "malformed request"})

        task = (request.get("task") or "").strip()
        if not task:
            return self._json(400, {"error": "Describe what you want changed."})

        uefn_ok, uefn_detail = probe_uefn(UEFN_MCP_URL)
        if not uefn_ok:
            return self._json(400, {
                "error": f"UEFN is not reachable ({uefn_detail}).",
                "fix": "Open UEFN with your project, enable Python Editor Scripting "
                       "and UEFN MCP Toolsets, then restart the editor.",
            })

        back_ok, back_detail = probe_backend()
        if not back_ok:
            return self._json(400, {
                "error": f"The agent backend is not available ({back_detail}).",
                "fix": "Press 'Start backend' above, or run:  agent-canvas --backend-only",
            })

        llm = resolve_llm()
        if llm is None:
            return self._json(400, {
                "error": "No LLM configured for the agent backend.",
                "fix": "Run 'openhands login' to use your OpenHands Cloud account, "
                       "or set LLM_MODEL and LLM_API_KEY.",
            })

        uploads = [item["name"] for item in staging_summary()]
        context = UEFN_CONTEXT
        if uploads:
            context += (
                "\nThe user uploaded these files into `uploads/` in this workspace. "
                "Inspect them and use them as the source material:\n"
                + "\n".join(f"- uploads/{name}" for name in uploads)
                + "\n"
            )

        payload = {
            "agent": {
                "kind": "Agent",
                "llm": {
                    "model": llm["model"],
                    "api_key": llm["api_key"],
                    "base_url": llm["base_url"],
                },
                "tools": [
                    {"name": "terminal"},
                    {"name": "file_editor"},
                    {"name": "task_tracker"},
                ],
            },
            "workspace": {"kind": "LocalWorkspace", "working_dir": WORKSPACE_DIR},
            "initial_message": {
                "content": [{"text": f"{context}\nTask: {task}"}],
                "run": True,
            },
        }
        status, result = http_json(
            f"{BACKEND_URL}/api/conversations",
            payload,
            {"X-Session-API-Key": backend_key() or ""},
            timeout=60,
        )
        if status not in (200, 201) or not isinstance(result, dict):
            return self._json(500, {"error": f"Could not start (HTTP {status}).", "detail": str(result)[:300]})

        conversation_id = result.get("id") or result.get("conversation_id")
        self._json(200, {
            "ok": True,
            "id": conversation_id,
            "url": f"{BACKEND_URL}/conversations/{conversation_id}",
            "uploads_used": uploads,
        })


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__APP_NAME__</title>
<style>
:root{--bg:#0e1116;--panel:#161b22;--line:#242c37;--fg:#e6edf3;--dim:#8b949e;
--ok:#3fb950;--bad:#f85149;--warn:#d29922;--accent:#4493f8}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:22px;margin:0 0 4px;font-weight:600}
.sub{color:var(--dim);font-size:13px;margin-bottom:24px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:18px;margin-bottom:16px}
.card h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;
color:var(--dim);margin:0 0 14px;font-weight:600}
.row{display:flex;align-items:center;gap:10px;padding:7px 0}
.dot{width:9px;height:9px;border-radius:50%;flex:0 0 9px}
.dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}
.dot.warn{background:var(--warn)}
.label{flex:1}.meta{color:var(--dim);font-size:12.5px}
.drop{border:1.5px dashed var(--line);border-radius:10px;padding:28px 16px;
text-align:center;color:var(--dim);cursor:pointer;transition:.15s}
.drop:hover,.drop.over{border-color:var(--accent);color:var(--fg);
background:rgba(68,147,248,.06)}
.drop b{color:var(--fg)}
textarea{width:100%;min-height:96px;background:#0d1117;color:var(--fg);
border:1px solid var(--line);border-radius:8px;padding:11px;font:inherit;resize:vertical}
textarea:focus{outline:none;border-color:var(--accent)}
button{background:var(--accent);color:#fff;border:0;border-radius:8px;
padding:11px 18px;font:inherit;font-weight:600;cursor:pointer}
button:hover{filter:brightness(1.1)}button:disabled{opacity:.45;cursor:not-allowed}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--fg);
font-weight:500}
.actions{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}
ul.files{list-style:none;padding:0;margin:0}
ul.files li{display:flex;gap:10px;padding:6px 0;border-bottom:1px solid var(--line);
font-size:13.5px}ul.files li:last-child{border:0}
ul.files .sz{color:var(--dim);margin-left:auto;font-size:12.5px}
.msg{padding:11px 13px;border-radius:8px;margin-top:14px;font-size:13.5px;
display:none;white-space:pre-wrap;word-break:break-word}
.msg.show{display:block}
.msg.err{background:rgba(248,81,73,.12);border:1px solid rgba(248,81,73,.35)}
.msg.ok{background:rgba(63,185,80,.12);border:1px solid rgba(63,185,80,.35)}
code{background:#0d1117;padding:2px 6px;border-radius:5px;font-size:12.5px}
a{color:var(--accent)}
.note{color:var(--dim);font-size:12.5px;margin-top:12px}
button.sm{padding:4px 10px;font-size:12px;margin-left:10px}
.row{align-items:center}
select{background:#0f1216;color:var(--fg);border:1px solid var(--line);
border-radius:8px;padding:8px;font:inherit;width:100%}
</style></head><body><div class="wrap">
<h1>__APP_NAME__</h1>
<div class="sub">Connects Unreal Editor for Fortnite to OpenHands agents running on this machine.</div>

<div class="card"><h2>Status</h2>
<div class="row"><span class="dot" id="d-uefn"></span><span class="label">Unreal Editor for Fortnite</span><span class="meta" id="m-uefn">checking…</span><button class="ghost sm" id="f-uefn" onclick="fixUefn()">Fix</button></div>
<div class="row"><span class="dot" id="d-back"></span><span class="label">Agent backend (this machine)</span><span class="meta" id="m-back">checking…</span><button class="ghost sm" id="f-back" onclick="fixBackend()">Fix</button></div>
<div class="row"><span class="dot" id="d-llm"></span><span class="label">Model</span><span class="meta" id="m-llm">checking…</span><button class="ghost sm" id="f-llm" onclick="toggleKeyForm()">Set key</button></div>
<div class="actions"><button onclick="fixAll()" id="btn-fixall">Fix everything</button>
<button class="ghost" onclick="refresh()">Re-check</button>
<button class="ghost" id="btn-reg" onclick="register()">Register UEFN with OpenHands</button></div>
<div class="note" id="workspace"></div>
<div class="note" id="fixlog"></div>
</div>

<div class="card" id="keycard" style="display:none"><h2>Model credential</h2>
<div class="note">This is the one step the app cannot do for you: a model key is account-bound. Paste one below and it is saved to <code>~/.openhands/agent_settings.json</code> (chmod 600, never shown again).</div>
<div class="actions" style="margin-top:10px">
<select id="preset" onchange="applyPreset()"></select></div>
<input id="llm-model" placeholder="model name, e.g. openhands/deepseek-v4.1-flash" style="width:100%;margin-top:8px;padding:9px;border-radius:8px;border:1px solid var(--line);background:#0f1216;color:var(--fg)">
<input id="llm-base" placeholder="base URL (optional)" style="width:100%;margin-top:8px;padding:9px;border-radius:8px;border:1px solid var(--line);background:#0f1216;color:var(--fg)">
<input id="llm-key" type="password" placeholder="API key" style="width:100%;margin-top:8px;padding:9px;border-radius:8px;border:1px solid var(--line);background:#0f1216;color:var(--fg)">
<div class="actions"><button onclick="saveKey()">Save key</button>
<button class="ghost" onclick="toggleKeyForm()">Close</button></div>
<div class="note" id="keyhint"></div>
</div>

<div class="card"><h2>Upload anything</h2>
<div class="drop" id="drop"><b>Drop files here</b> or click to choose<br>
<span style="font-size:12.5px">textures · meshes · videos · images · docs · reference links · concept art</span></div>
<input type="file" id="picker" multiple style="display:none">
<ul class="files" id="files"></ul>
<div class="actions"><button class="ghost" onclick="clearAll()">Clear uploads</button></div>
</div>

<div class="card"><h2>What should the agent do?</h2>
<textarea id="task" placeholder="e.g. Replace the rock textures with the uploaded art, keep the same material references, then compile and tell me if it built clean."></textarea>
<div class="actions">
<button id="btn-go" onclick="go()">Send to UEFN</button>
<button class="ghost" onclick="window.open('__BACKEND__','_blank')">Open conversation list</button>
</div>
<div class="msg" id="msg"></div>
<div class="note">The agent edits the project open in UEFN. Watch the editor while it works.</div>
</div>
</div>
<script>
const $=id=>document.getElementById(id);
function setDot(el,state){el.className='dot '+state}
function fmt(b){if(b<1024)return b+' B';if(b<1048576)return (b/1024).toFixed(1)+' KB';
if(b<1073741824)return (b/1048576).toFixed(1)+' MB';return (b/1073741824).toFixed(2)+' GB'}
function show(kind,text){const m=$('msg');m.className='msg show '+kind;m.textContent=text}
function hide(){$('msg').className='msg'}

async function refresh(){
  try{
    const s=await (await fetch('/api/status')).json();
    setDot($('d-uefn'),s.uefn.ok?'ok':'bad');
    $('m-uefn').textContent=s.uefn.ok?'connected':s.uefn.detail;
    $('f-uefn').style.display=s.uefn.ok?'none':'';
    setDot($('d-back'),s.backend.ok?'ok':'bad');
    $('m-back').textContent=s.backend.ok?'running':s.backend.detail;
    $('f-back').style.display=s.backend.ok?'none':'';
    setDot($('d-llm'),s.llm.ok?'ok':'bad');
    $('m-llm').textContent=s.llm.detail;
    $('f-llm').style.display=s.llm.ok?'none':'';
    $('btn-reg').style.display=s.cli?'':'none';
    $('workspace').textContent='Workspace: '+s.workspace+'  ·  v'+s.version;
    if(!presets.length&&s.presets){presets=s.presets;fillPresets()}
    renderFiles(s.uploads);
  }catch(e){show('err','Cannot reach the app server. Is it still running?')}
}
let presets=[];
function fillPresets(){
  const sel=$('preset');sel.innerHTML='';
  presets.forEach((p,i)=>{
    const o=document.createElement('option');o.value=i;o.textContent=p.label;sel.appendChild(o);
  });
  if(presets.length)applyPreset();
}
function applyPreset(){
  const p=presets[$('preset').value];if(!p)return;
  $('llm-model').value=p.model||'';$('llm-base').value=p.base_url||'';
  $('keyhint').textContent='Expected key: '+(p.key_hint||'');
}
function toggleKeyForm(){
  const c=$('keycard');
  c.style.display=c.style.display==='none'?'':'none';
}
function logStep(name,ok,detail){
  const el=$('fixlog');
  const line=document.createElement('div');
  line.textContent=(ok?'✓ ':'• ')+name+(detail?' — '+detail:'');
  line.style.color=ok?'var(--ok,#5bd66f)':'var(--warn,#e8b84b)';
  el.appendChild(line);
}
function clearLog(){$('fixlog').innerHTML=''}
async function fixAll(){
  clearLog();const b=$('btn-fixall');b.disabled=true;b.textContent='Fixing…';
  show('ok','Working through every step this machine can do on its own…');
  try{
    const r=await (await fetch('/api/fix-all',{method:'POST'})).json();
    r.steps.forEach(s=>logStep(s.step,s.ok,s.detail));
    if(r.ok){show('ok','Everything is green. You can send work to UEFN.')}
    else{show('ok','Done what I can. Still needed: '+r.needs.join(', '))}
  }catch(e){show('err','Fix-all failed: '+e)}
  b.disabled=false;b.textContent='Fix everything';
  refresh();
}
async function fixBackend(){
  clearLog();show('ok','Installing and starting the agent backend…');
  try{
    const i=await (await fetch('/api/backend/install',{method:'POST'})).json();
    logStep('install backend',i.ok,i.detail);
    if(!i.ok){show('err',i.detail);refresh();return}
    const s=await (await fetch('/api/backend/start',{method:'POST'})).json();
    logStep('start backend',s.ok,s.detail);
    show(s.ok?'ok':'err',s.ok?'Backend is running.':s.detail);
  }catch(e){show('err','Could not set up the backend: '+e)}
  refresh();
}
async function fixUefn(){
  clearLog();show('ok','Writing UEFN settings…');
  try{
    const r=await (await fetch('/api/uefn/fix',{method:'POST'})).json();
    r.results.forEach(s=>logStep(s.step,s.ok,s.detail));
    show(r.ok?'ok':'err',r.ok
      ?'UEFN configured. Restart UEFN with your project open, then Re-check.'
      :'Some UEFN settings could not be written — see the log.');
  }catch(e){show('err','Could not write UEFN settings: '+e)}
  refresh();
}
async function saveKey(){
  const model=$('llm-model').value.trim(),key=$('llm-key').value.trim(),base=$('llm-base').value.trim();
  if(!key){show('err','Paste an API key first.');return}
  try{
    const r=await (await fetch('/api/llm/save',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({model,api_key:key,base_url:base})})).json();
    if(r.ok){
      $('llm-key').value='';
      show('ok','Key saved. The agent will use '+model+'.');
      toggleKeyForm();
    }else{show('err',r.detail||'Could not save the key.')}
  }catch(e){show('err','Could not save the key: '+e)}
  refresh();
}
function renderFiles(items){
  const ul=$('files');ul.innerHTML='';
  (items||[]).forEach(f=>{
    const li=document.createElement('li');
    li.innerHTML='<span></span><span class="sz">'+fmt(f.bytes)+'</span>';
    li.firstChild.textContent=f.name;ul.appendChild(li);
  });
  if(!items||!items.length){
    ul.innerHTML='<li style="color:var(--dim);border:0">No uploads staged yet.</li>';
  }
}
async function uploadOne(file){
  const r=await fetch('/api/upload',{method:'POST',headers:{'X-Filename':file.name},body:file});
  return r.json();
}
async function handleFiles(list){
  const files=[...list];if(!files.length)return;
  show('ok','Uploading '+files.length+' file(s)…');
  let done=0;
  for(const f of files){await uploadOne(f);done++}
  show('ok','Uploaded '+done+' file(s).');
  refresh();
}
const drop=$('drop'),picker=$('picker');
drop.onclick=()=>picker.click();
picker.onchange=e=>handleFiles(e.target.files);
['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev,e=>{
  e.preventDefault();drop.classList.add('over')}));
['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{
  e.preventDefault();drop.classList.remove('over')}));
drop.addEventListener('drop',e=>handleFiles(e.dataTransfer.files));

async function go(){
  hide();const task=$('task').value.trim();
  if(!task){show('err','Describe what you want changed first.');return}
  $('btn-go').disabled=true;show('ok','Starting the agent…');
  try{
    const r=await fetch('/api/start',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({task})});
    const d=await r.json();
    if(d.ok){
      show('ok','Agent started'+(d.uploads_used.length?' with '+d.uploads_used.length+' upload(s)':'')+
        '.\\nOpening the conversation…');
      setTimeout(()=>window.open(d.url,'_blank'),700);
    }else{
      show('err',d.error+(d.fix?'\\n\\n'+d.fix:'')+(d.detail?'\\n\\n'+d.detail:''));
    }
  }catch(e){show('err','Request failed: '+e)}
  $('btn-go').disabled=false;
}
async function register(){
  hide();
  const r=await fetch('/api/register',{method:'POST'});const d=await r.json();
  if(d.ok){show('ok',d.message)}else{show('err',d.error+(d.fix?'\\n\\n'+d.fix:''))}
}
async function clearAll(){
  const r=await fetch('/api/clear',{method:'POST'});const d=await r.json();
  show('ok','Removed '+d.removed+' file(s).');refresh();
}
refresh();setInterval(refresh,10000);
</script></body></html>
"""


def build_page() -> str:
    return PAGE.replace("__APP_NAME__", APP_NAME).replace("__BACKEND__", BACKEND_URL)


def main(argv=None) -> int:
    global WORKSPACE_DIR, UPLOAD_DIR

    parser = argparse.ArgumentParser(description=f"{APP_NAME} - link UEFN to OpenHands agents.")
    parser.add_argument("--port", type=int, default=DEFAULT_APP_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--workspace", default=WORKSPACE_DIR)
    parser.add_argument(
        "--no-autostart",
        action="store_true",
        help="Do not start the agent backend automatically",
    )
    args = parser.parse_args(argv)

    WORKSPACE_DIR = args.workspace
    UPLOAD_DIR = os.path.join(WORKSPACE_DIR, "uploads")
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    port = free_port(args.port)
    server = ThreadingHTTPServer(("127.0.0.1", port), AppHandler)
    url = f"http://127.0.0.1:{port}"

    uefn_ok, uefn_detail = probe_uefn(UEFN_MCP_URL)
    back_ok, back_detail = probe_backend()
    llm_ok, llm_detail = llm_status()

    print(f"{APP_NAME} {APP_VERSION}")
    print(f"  app        {url}")
    print(f"  workspace  {WORKSPACE_DIR}")
    print(f"  uefn       {'connected' if uefn_ok else 'NOT reachable (' + uefn_detail + ')'}")
    print(f"  backend    {'running' if back_ok else 'NOT running (' + back_detail + ')'}")
    print(f"  model      {llm_detail}")
    print()

    if not uefn_ok:
        print("  UEFN is not reachable. Open UEFN with your project, enable")
        print("  Python Editor Scripting and UEFN MCP Toolsets, then restart it.")
        print()

    if not llm_ok:
        print("  No LLM key found. Run 'openhands login' to use your OpenHands")
        print("  Cloud account, or set LLM_MODEL and LLM_API_KEY.")
        print()

    print("  Leave this window open. Press Ctrl+C to quit.")
    print()

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    if not back_ok and not args.no_autostart:
        def autostart():
            print("  Starting the agent backend...")
            started, detail = start_backend()
            print(f"  backend    {'running' if started else 'FAILED (' + detail + ')'}")
            if not started:
                print("  Start it from the app window, or run:  agent-canvas --backend-only")
            print()

        threading.Thread(target=autostart, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
