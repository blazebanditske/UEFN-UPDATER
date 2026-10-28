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
from urllib.parse import urlparse

APP_NAME = "UEFN Link"
APP_VERSION = "1.4.0"
DEFAULT_APP_PORT = 8765
UEFN_MCP_URL = os.environ.get("UEFN_MCP_URL", "http://127.0.0.1:8000/mcp")
# UEFN's embedded MCP server owns 8000 by default. The agent backend must not
# default to the same port or the two would compete for it and the status lights
# would read the wrong service.
DEFAULT_BACKEND_PORT = 3010
BACKEND_URL = os.environ.get(
    "LOCAL_AGENT_SERVER_URL", f"http://127.0.0.1:{DEFAULT_BACKEND_PORT}"
)
CLOUD_CANVAS_URL = "https://app.all-hands.dev/canvas/conversations"

# agent-canvas binds agent-server and automation to FIXED internal ports, and
# refuses to start if either is taken -- whatever ingress port it is given. An
# already-running agent-canvas therefore blocks this app's own launch on
# 18000/18001, not on 3010. These are the ports to check for that condition.
# (Verified in @openhands/agent-canvas 1.33.0: config/defaults.json pins
# ports.agentServer/automation, and scripts/dev-with-automation.mjs calls
# assertPortsFree before starting anything.)
AGENT_CANVAS_INTERNAL_PORTS = (18000, 18001)

# Where a running backend might be listening. The app probes these to adopt one
# instead of spawning a second agent-canvas that cannot start. 3010 is this
# app's own default; 3000 is agent-canvas's documented local UI port; 8000 is
# agent-canvas's bare default -- which UEFN's MCP server also uses, so a match
# there is only trusted when /server_info identifies it as a backend; 18000 is
# the agent-server's own port, reachable when a stack is up but its ingress is
# not.
BACKEND_CANDIDATE_PORTS = (DEFAULT_BACKEND_PORT, 3000, 8000, 18000)

# Probe budget per candidate, in seconds. This runs on the status path, which
# the page polls every 10s, and AGENTS.md forbids a slow call there. A real
# backend answers /server_info in tens of ms, so the ports that plausibly host
# one get a little slack; 8000 and 18000 get less, because neither UEFN nor a
# bare agent-server answers this route and waiting longer only slows the page.
# A port that is closed is ruled out by port_in_use first and never waits.
BACKEND_PROBE_TIMEOUTS = {
    DEFAULT_BACKEND_PORT: 0.6,
    3000: 0.6,
    8000: 0.25,
    18000: 0.25,
}
BACKEND_DEFAULT_PROBE_TIMEOUT = 1.0

# Hard ceiling on the whole search, in seconds. The per-port budgets above are
# only a guide; this is the guarantee. /api/status polls every 10s and callers
# time out at 10s, while UEFN's own probe alone can take 8s, so discovery gets a
# small fixed slice and stops when it is spent. A real backend answers in tens
# of ms, so this never cuts a live candidate short in practice.
BACKEND_DISCOVERY_BUDGET = 1.0

HOME_DIR = os.path.expanduser("~")
# Windows is the only platform UEFN ships on, so it is the platform that
# matters. Kept as a constant so tests can exercise both branches anywhere.
IS_WINDOWS = os.name == "nt"
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


def port_in_use(port: int, timeout: float = 0.25) -> bool:
    """True when something accepts a TCP connection on loopback `port`.

    Kept short: this gates discovery on the status path, and a closed port is
    answered (or refused) immediately. A filtered port costs the full timeout,
    which is the price of not waiting on a probe that can never succeed.
    """
    with socket.socket() as probe:
        probe.settimeout(timeout)
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


def probe_backend(url: str | None = None, timeout: float = 4) -> tuple[bool, str]:
    """Is an agent-server answering at `url` (default BACKEND_URL)?

    /server_info is the discriminator, and the reason a backend can be told
    apart from UEFN's MCP server even when both sit on port 8000: the editor
    never answers this route with 200.
    """
    key = backend_key()
    status, _ = http_json(
        f"{url or BACKEND_URL}/server_info",
        headers={"X-Session-API-Key": key} if key else {},
        timeout=timeout,
    )
    if status == 200:
        return True, "running"
    if status == 401:
        return False, "running but key rejected"
    return False, "not running"


def discover_backend(ports=None, probe=None) -> dict | None:
    """Find an agent backend already running on this machine.

    This is the fix for the one-backend-per-machine rule. agent-canvas pins its
    internal ports (18000/18001) and aborts if they are taken, so spawning a
    second copy fails no matter what -- but a backend that is already up is
    perfectly usable, because agent-canvas's ingress routes /server_info and
    /api to the agent-server behind it. So the app looks before it launches.

    A URL set through LOCAL_AGENT_SERVER_URL is never second-guessed: the user
    said where the backend is, and guessing over that would be rude.
    """
    if os.environ.get("LOCAL_AGENT_SERVER_URL"):
        return None

    candidate_ports = BACKEND_CANDIDATE_PORTS if ports is None else ports
    deadline = time.monotonic() + BACKEND_DISCOVERY_BUDGET

    for port in candidate_ports:
        if time.monotonic() >= deadline:
            break  # the budget is the guarantee; see BACKEND_DISCOVERY_BUDGET
        if not port_in_use(port):
            continue  # nothing there; cheaper and more certain than a probe
        url = f"http://127.0.0.1:{port}"
        if url.rstrip("/") == BACKEND_URL.rstrip("/"):
            continue  # already covered by the direct probe
        budget = min(
            BACKEND_PROBE_TIMEOUTS.get(port, BACKEND_DEFAULT_PROBE_TIMEOUT),
            max(0.05, deadline - time.monotonic()),
        )
        check = probe or (lambda u, t=budget: probe_backend(u, timeout=t))
        try:
            ok, detail = check(url)
        except Exception:
            # A probe must never break the page, and a candidate we cannot
            # reach is simply not a candidate.
            continue
        if ok:
            return {"url": url, "port": port, "detail": detail}
    return None


def agent_canvas_running_elsewhere() -> bool:
    """True when agent-canvas's fixed internal ports are held.

    That means another agent-canvas (or something shaped like one) is up, and a
    launch of ours cannot succeed -- so the app can say so immediately instead
    of spawning a doomed process and waiting out the timeout.
    """
    return any(port_in_use(port) for port in AGENT_CANVAS_INTERNAL_PORTS)


def which_any(*names) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def resolve_command(name: str) -> str:
    """Return a path subprocess can actually launch for `name`.

    On Windows, npm/node/uvx are `.cmd`/`.bat` shims. shutil.which finds them,
    but handing the bare string to subprocess raises `[WinError 2] The system
    cannot find the file specified`, because CreateProcess only appends `.exe`
    and will not execute a batch file by bare name. Resolve to the real path
    (which keeps the extension CreateProcess needs) before spawning.
    """
    suffixes = [""]
    if IS_WINDOWS:
        suffixes += [".cmd", ".exe", ".bat"]
    return which_any(*(name + suffix for suffix in suffixes)) or name


def npm_command() -> str:
    """Resolved npm. Empty string means npm is genuinely not installed.

    Prefer npm.cmd on Windows. Node also ships npm.ps1, and PowerShell refuses
    to load it under its default Restricted policy; the app must never pick that
    one, or a spawn fails for a reason that has nothing to do with npm.
    """
    if IS_WINDOWS:
        return which_any("npm.cmd", "npm.exe", "npm") or ""
    return which_any("npm") or ""


# PowerShell refuses to load npm.ps1 under its default Restricted policy, so a
# command that works in Command Prompt dies in PowerShell before npm ever runs.
# These markers identify that specific wall in npm's output.
POWERSHELL_BLOCK_MARKERS = (
    "running scripts is disabled",
    "pssecurityexception",
    "unauthorizedaccess",
    "cannot be loaded because running scripts",
)


def npm_failure_code(output: str) -> str:
    """Pick the guide that explains an npm failure.

    The common Windows case is not npm's fault at all: PowerShell will not load
    its npm.ps1 shim, so the install never starts. Recognizing that lets the app
    say "use Command Prompt" instead of printing PowerShell's raw security text.
    """
    lowered = (output or "").lower()
    if any(marker in lowered for marker in POWERSHELL_BLOCK_MARKERS):
        return "npm_powershell_blocked"
    return "npm_failed"


# npm prefix -g answers with the same path for the life of the process, but
# launching npm costs seconds on Windows (it is a shell script there). The
# status page polls, so resolving it on every request made the page crawl and
# could blow a caller's timeout. Resolve once and remember it.
_NPM_PREFIX_CACHE = {}


def npm_global_prefix() -> str | None:
    """Where npm puts global binaries, so the app can find agent-canvas even
    when the shell PATH has not been reloaded since the install.

    Cached after the first successful answer. A miss is not cached, so the app
    still notices an install that happens while it is running.
    """
    if "value" in _NPM_PREFIX_CACHE:
        return _NPM_PREFIX_CACHE["value"]
    npm = npm_command()
    if not npm:
        return None
    try:
        proc = subprocess.run(
            [npm, "prefix", "-g"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    prefix = proc.stdout.strip()
    if not prefix:
        return None
    _NPM_PREFIX_CACHE["value"] = prefix
    return prefix


def find_agent_canvas() -> str | None:
    """Locate agent-canvas on PATH, or in the usual global install locations."""
    found = which_any("agent-canvas", "agent-canvas.cmd", "agent-canvas.exe")
    if found:
        return found
    prefix = npm_global_prefix()
    if prefix:
        for candidate in (
            os.path.join(prefix, "bin", "agent-canvas"),
            os.path.join(prefix, "agent-canvas.cmd"),
            os.path.join(prefix, "agent-canvas.exe"),
            os.path.join(prefix, "agent-canvas"),
        ):
            if os.path.isfile(candidate):
                return candidate
    return None


def environment_report() -> dict:
    """Everything the app can learn about this machine without changing it."""
    node = which_any("node", "node.exe")
    npm = npm_command()
    canvas = find_agent_canvas()
    return {
        "node": bool(node),
        "node_path": node,
        "npm": bool(npm),
        "npm_path": npm or None,
        "uv": bool(which_any("uv", "uv.exe", "uvx", "uvx.exe")),
        "agent_canvas": bool(canvas),
        "agent_canvas_path": canvas,
        "openhands_cli": has_openhands_cli(),
        "uefn_ini": uefn_ini_path(),
        "uefn_project": find_uefn_project(),
        "platform": sys.platform,
    }


# --- backend choice --------------------------------------------------------
# Which backend the app should use, persisted so the answer survives a restart.
# "auto" (default) probes for a running one, "own" always starts and uses this
# app's port, and "custom" pins a URL the user typed.
BACKEND_MODES = ("auto", "own", "custom")


def backend_settings_path() -> str:
    """Path to the saved backend choice.

    A function, not a constant: WORKSPACE_DIR is reassigned by main() and by
    tests, so resolving it at import time would read the wrong file.
    """
    return os.path.join(WORKSPACE_DIR, "backend.json")


def load_backend_choice() -> dict:
    """The saved backend choice, validated. Falls back to auto."""
    try:
        with open(backend_settings_path(), encoding="utf-8") as handle:
            saved = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"mode": "auto", "url": ""}
    if not isinstance(saved, dict):
        return {"mode": "auto", "url": ""}
    mode = saved.get("mode") if saved.get("mode") in BACKEND_MODES else "auto"
    url = saved.get("url") if isinstance(saved.get("url"), str) else ""
    if mode == "custom" and not url:
        mode = "auto"
    return {"mode": mode, "url": url}


def save_backend_choice(mode: str, url: str = "") -> tuple[bool, str]:
    """Persist the backend choice. Returns (ok, message)."""
    if mode not in BACKEND_MODES:
        return False, f"unknown mode: {mode}"
    url = (url or "").strip()
    if mode == "custom":
        if not url:
            return False, "a custom backend needs a URL"
        if urlparse(url).scheme not in ("http", "https"):
            return False, "the URL must start with http:// or https://"
    try:
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
        with open(backend_settings_path(), "w", encoding="utf-8") as handle:
            json.dump({"mode": mode, "url": url}, handle, indent=2)
    except OSError as exc:
        return False, f"could not save the choice: {exc}"
    return True, f"backend set to {mode}"


def effective_backend_url() -> str:
    """The backend URL the app should actually use, honoring the saved choice.

    LOCAL_AGENT_SERVER_URL wins over everything: it is an explicit instruction
    from whoever started the app, and silently overriding it would be wrong.
    """
    override = os.environ.get("LOCAL_AGENT_SERVER_URL")
    if override:
        return override
    choice = load_backend_choice()
    if choice["mode"] == "custom" and choice["url"]:
        return choice["url"].rstrip("/")
    if choice["mode"] == "auto":
        found = discover_backend()
        if found:
            return found["url"]
    return f"http://127.0.0.1:{DEFAULT_BACKEND_PORT}"


def active_backend() -> tuple[bool, str, str]:
    """Where the agent backend is, as (ok, detail, url).

    One place for the "is it up, and where" question, so the status row and the
    Start button can never disagree. Falls back to discovery in auto mode,
    which is what lets the app use an agent-canvas the user started themselves.
    """
    ok, detail = probe_backend()
    if ok:
        return True, detail, BACKEND_URL
    if load_backend_choice()["mode"] == "auto":
        found = discover_backend()
        if found:
            return True, f"running on port {found['port']}", found["url"]
    return False, detail, effective_backend_url()


# --- failure guides --------------------------------------------------------
def backend_port() -> int:
    """The ingress port the backend actually runs on.

    The app talks to BACKEND_URL, which honors LOCAL_AGENT_SERVER_URL. Any
    command the app tells the user to paste must name the same port, or the
    backend starts somewhere the app is not looking -- or, worse, on UEFN's own
    port 8000, which is the default agent-canvas picks when given no --port.
    """
    try:
        return urlparse(BACKEND_URL).port or DEFAULT_BACKEND_PORT
    except ValueError:
        return DEFAULT_BACKEND_PORT


def backend_start_command() -> str:
    """The exact command a user can paste to start the backend by hand.

    Always carries --port. Without it agent-canvas defaults to 8000 and
    collides with UEFN's embedded MCP server, which is the failure this
    command exists to fix.
    """
    return f"agent-canvas --backend-only --port {backend_port()}"


# Every failure the app can produce carries a step-by-step repair. These are
# written for someone who does not want to read a stack trace: numbered steps,
# the exact command to paste, and what "done" looks like. The UI renders these
# on the failed row, so a red dot always explains itself.
def _guide(title: str, *steps: str, done: str = "") -> dict:
    return {"title": title, "steps": [s for s in steps if s], "done": done}


# Guides quote the start command, which depends on the backend port. The port is
# fixed by BACKEND_URL at import, but LOCAL_AGENT_SERVER_URL can override it, so
# substitute at read time rather than freezing the value into the registry.
_START_CMD = "\x00START_CMD\x00"


GUIDES = {
    "npm_missing": _guide(
        "Node.js and npm are not installed",
        "Open https://nodejs.org in your browser.",
        "Download the LTS installer for Windows (.msi).",
        "Run the installer and accept the defaults. Keep the checkbox that "
        "adds Node to PATH.",
        "Close this app completely, then start it again.",
        "Press Fix everything.",
        done="Node.js and npm are on PATH — the app will detect them on restart.",
    ),
    "npm_unwritable": _guide(
        "npm's global folder is not writable",
        "Close this app.",
        "Open Command Prompt as administrator: press the Windows key, type "
        "cmd, right-click Command Prompt, and choose Run as administrator.",
        "Run:  npm config set prefix \"%APPDATA%\\npm\"",
        "Close the terminal, then start this app normally.",
        "Press Fix everything.",
        done="agent-canvas installs into your own AppData folder, so no admin "
             "rights are needed after this.",
    ),
    "npm_failed": _guide(
        "The agent-canvas install did not finish",
        "Check your internet connection.",
        "Open Command Prompt (press the Windows key, type cmd) — not "
        "PowerShell — and run:  npm install -g @openhands/agent-canvas",
        "If it prints a permission error, follow the 'not writable' steps.",
        "If PowerShell refuses to run npm, follow the 'PowerShell is "
        "blocking npm' steps instead.",
        "If it prints a network error, retry — npm downloads a few MB.",
        "When it succeeds, press Fix everything again.",
        done="A global agent-canvas binary exists and `agent-canvas --version` runs.",
    ),
    "npm_error": _guide(
        "npm could not be started",
        "Close this app completely.",
        "Start it again. The app re-resolves npm on every launch.",
        "Press Fix everything.",
        "If it still fails, open Command Prompt (not PowerShell) and run:  "
        "npm --version",
        "If that also fails, reinstall Node.js from https://nodejs.org.",
        done="`npm --version` prints a version number.",
    ),
    "npm_powershell_blocked": _guide(
        "PowerShell is blocking npm",
        "This is not an npm problem. PowerShell refuses to load npm's script "
        "because its execution policy is Restricted.",
        "Open Command Prompt instead: press the Windows key, type cmd, and "
        "press Enter. Do not use PowerShell or a PowerShell tab.",
        "In that window run:  npm install -g @openhands/agent-canvas",
        "When it finishes, close this app completely and start it again.",
        "Press Fix everything.",
        done="The Agent backend row turns green.",
    ),
    "backend_missing": _guide(
        "The agent backend is not installed",
        "Press the Fix button on the Agent backend row.",
        "If that fails, open Command Prompt (not PowerShell) and run:  "
        "npm install -g @openhands/agent-canvas",
        "Wait for it to finish — it downloads a few MB.",
        "Press Fix everything.",
        done="The Agent backend row turns green.",
    ),
    "backend_stopped": _guide(
        "The agent backend is installed but not running",
        "Press Fix everything. The app starts it for you.",
        "If it stays red, open Task Manager (Ctrl+Shift+Esc) and end any "
        "agent-canvas process, then press Fix everything again.",
        "If it still stays red, open a terminal and run:  " + _START_CMD,
        "Leave that terminal open while you use this app.",
        done="The Agent backend row turns green.",
    ),
    "backend_port_busy": _guide(
        "Another program is using the backend port",
        f"Something is holding port {DEFAULT_BACKEND_PORT} and it is not the "
        "agent backend, so the app cannot start one.",
        "Close any other window of this app.",
        "Open Task Manager (Ctrl+Shift+Esc) and end any agent-canvas process.",
        "To see what owns the port, open Command Prompt and run:  "
        f"netstat -ano | findstr :{DEFAULT_BACKEND_PORT}",
        "The last column is a PID. Find it on Task Manager's Details tab and "
        "end that program, or set LOCAL_AGENT_SERVER_URL to a free port.",
        "Press Fix everything.",
        done="The Agent backend row turns green.",
    ),
    "backend_timeout": _guide(
        "The backend started but did not answer in time",
        "Wait about a minute — the first start is the slowest.",
        "Press Re-check.",
        "If it is still red, open the log the app names in the message.",
        "Close any other window of this app, then press Fix everything.",
        done="The Agent backend row turns green.",
    ),
    "backend_error": _guide(
        "The backend could not be started",
        "Close any other window of this app.",
        "Press Fix everything.",
        "If it still fails, open a terminal and run:  " + _START_CMD,
        "Read the last few lines it prints — they name the cause.",
        done="The Agent backend row turns green.",
    ),
    "backend_port_conflict": _guide(
        "agent-canvas was started on a port UEFN already owns",
        "Close any agent-canvas window or terminal you opened by hand.",
        "Press Fix everything. The app starts the backend on its own port "
        f"({DEFAULT_BACKEND_PORT}), never on UEFN's 8000.",
        "Never run agent-canvas without --port: it defaults to 8000 and "
        "collides with UEFN's MCP server.",
        "If you must start it by hand, run:  " + _START_CMD,
        done="The Agent backend row turns green.",
    ),
    "backend_already_running": _guide(
        "Another agent-canvas is already running",
        "Only one agent-canvas can run at a time. It always claims the same two "
        "internal ports (18000 and 18001), so a second copy cannot start — no "
        "matter which port it is given.",
        "You do not have to close it. This app can use the one that is already "
        "running: press Fix everything, or set Backend to Automatic.",
        "If you would rather run this app's own backend, close the other "
        "agent-canvas first: end any agent-canvas process in Task Manager "
        "(Ctrl+Shift+Esc), or stop the terminal running `agent-canvas`.",
        "Then press Fix everything.",
        done="The Agent backend row turns green.",
    ),
    "uefn_ini_missing": _guide(
        "UEFN's settings file was not found",
        "Open UEFN once and let it finish loading a project.",
        "Close UEFN.",
        "Press Fix everything.",
        "Reopen UEFN with your project.",
        done="The app writes Auto Start Server into UEFN's preferences.",
    ),
    "uefn_ini_unwritable": _guide(
        "UEFN's settings file could not be written",
        "Close UEFN — it locks its preferences file while running.",
        "Press Fix everything.",
        "If it still fails, close this app and start it as Administrator.",
        "Reopen UEFN with your project.",
        done="Auto Start Server is on in UEFN's preferences.",
    ),
    "uefn_project_missing": _guide(
        "No UEFN project folder was found",
        "Find your project: in UEFN it is the folder holding your .uefnproject "
        "file, usually under Documents\\Fortnite Projects.",
        "Close this app.",
        "Open a terminal and point the app at that folder, then start it again:"
        "  set UEFN_PROJECT_PATH=C:\\Users\\you\\Documents\\Fortnite Projects\\YourIsland",
        "Press Fix everything.",
        done="The UEFN project flags step reports success.",
    ),
    "uefn_project_unreadable": _guide(
        "The project file could not be read",
        "Close UEFN — it may be writing the file right now.",
        "Press Fix everything.",
        "If it still fails, check the .uefnproject file is valid JSON:  "
        "open it in Notepad and look for obvious damage.",
        "Restore from the .uefn-link.bak copy the app made, if one exists.",
        done="The UEFN project flags step reports success.",
    ),
    "uefn_project_unwritable": _guide(
        "The project file could not be written",
        "Close UEFN.",
        "Check the project folder is not read-only.",
        "Press Fix everything.",
        "If it still fails, copy the project to a folder you own and set "
        "UEFN_PROJECT_PATH to the copy.",
        done="The UEFN project flags step reports success.",
    ),
    "uefn_not_connected": _guide(
        "UEFN is not answering",
        "In UEFN open Project Settings and turn on Python Editor Scripting "
        "AND UEFN MCP Toolsets (under Beta Access).",
        "Restart UEFN with your project open. Toolsets publish at startup only.",
        "Press Re-check.",
        "If the app wrote the settings for you, a restart is still required.",
        done="The Unreal Editor for Fortnite row turns green.",
    ),
    "model_missing": _guide(
        "No model key is set",
        "Press the Set key button on the Model row.",
        "Pick your provider from the dropdown.",
        "Paste your API key and press Save key.",
        "Or, to use OpenHands Cloud, run in a terminal:  openhands login",
        done="The Model row shows your model name and turns green.",
    ),
}


def guide_for(code: str) -> dict:
    """Look up the repair for a failure code, with the real start command filled in."""
    guide = GUIDES.get(code) or _guide(
        "Something went wrong", "Press Fix everything to retry.")
    command = backend_start_command()
    return {
        "title": guide["title"],
        "steps": [s.replace(_START_CMD, command) for s in guide["steps"]],
        "done": guide["done"],
    }


def backend_failure_code() -> str:
    """Why is the backend row red? Pick the guide that matches the machine.

    Distinguishes "not installed" from "installed but stopped", because the
    repair is different and the app can tell them apart for free. A held
    internal port means a second copy cannot start at all, which has its own
    repair (adopt it, or close it) rather than "press Fix".
    """
    if not find_agent_canvas():
        return "backend_missing"
    if agent_canvas_running_elsewhere():
        return "backend_already_running"
    return "backend_stopped"


def ok_result(detail: str, **extra) -> dict:
    """A step that worked. Shaped like fail_result so the UI has one code path."""
    return {"ok": True, "detail": detail, "guide": None, **extra}


def fail_result(code: str, detail: str, **extra) -> dict:
    """A step that failed, always carrying a step-by-step repair."""
    return {"ok": False, "detail": detail, "guide": guide_for(code),
            "code": code, **extra}


def install_backend_dependencies(progress=None) -> dict:
    """Install agent-canvas globally with npm. This is the one download the app
    performs, and only when the user asks for it.

    Node and npm are prerequisites; the app never installs those, because a
    silent Node install is a much bigger intervention than fetching one package.
    """
    def say(message):
        if progress:
            progress(message)

    if find_agent_canvas():
        return ok_result("agent-canvas is already installed")

    npm = npm_command()
    if not npm:
        return fail_result(
            "npm_missing",
            "npm was not found on this machine.",
        )

    prefix = npm_global_prefix()
    if prefix and not os.access(prefix, os.W_OK):
        return fail_result(
            "npm_unwritable",
            f"npm's global folder ({prefix}) is not writable by this user.",
        )

    say("Installing agent-canvas (this downloads a few MB)…")
    try:
        proc = subprocess.run(
            [npm, "install", "-g", "@openhands/agent-canvas"],
            capture_output=True, text=True, timeout=900,
        )
    except subprocess.TimeoutExpired:
        return fail_result(
            "npm_failed", "npm install timed out after 15 minutes.")
    except OSError as exc:
        # This is the WinError 2 path: npm resolved, but could not be executed.
        return fail_result("npm_error", f"could not run npm: {exc}")

    if proc.returncode != 0:
        combined = (proc.stderr or "") + (proc.stdout or "")
        tail = combined.strip()[-400:]
        return fail_result(
            npm_failure_code(combined), f"npm install failed: {tail}")

    if not find_agent_canvas():
        return fail_result(
            "npm_failed",
            "Installed, but agent-canvas is not on PATH yet. Restart this app "
            "so it picks up the new PATH entry.",
        )
    return ok_result("agent-canvas installed")


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


def configure_uefn_autostart() -> dict:
    """Enable Auto Start Server so the editor serves MCP on every launch.

    Without this the server only runs if the user starts it by hand each
    session, which is the "nothing listening" state.
    """
    path = uefn_ini_path()
    if not path or not os.path.exists(path):
        return fail_result(
            "uefn_ini_missing",
            "UEFN's preferences file was not found.",
        )
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        return fail_result(
            "uefn_ini_unwritable", f"could not read UEFN preferences: {exc}")

    updated, changed = set_ini_keys(text, MCP_INI_SECTION, MCP_INI_KEYS)
    if not changed:
        return ok_result("Auto Start Server was already on")

    backup = path + ".uefn-link.bak"
    try:
        if not os.path.exists(backup):
            shutil.copy2(path, backup)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(updated)
    except OSError as exc:
        return fail_result(
            "uefn_ini_unwritable", f"could not write UEFN preferences: {exc}")
    return ok_result("Auto Start Server enabled (takes effect next UEFN start)")


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


def enable_project_beta_flags(project: str | None = None) -> dict:
    """Set the Python and MCP Toolset beta flags in the project's .uefnproject.

    This mirrors the two checkboxes the manual setup asks for. It is additive:
    only those two keys are written, and a backup is kept.
    """
    project = project or find_uefn_project()
    if not project:
        return fail_result(
            "uefn_project_missing",
            "No UEFN project folder found.",
        )
    marker = os.path.join(project, ".uefnproject")
    if not os.path.isfile(marker):
        return fail_result(
            "uefn_project_missing", f"no .uefnproject in {project}")

    try:
        with open(marker, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return fail_result(
            "uefn_project_unreadable", f"could not read .uefnproject: {exc}")

    experimental = data.setdefault("dataSets", {}).setdefault("experimental", {})
    changed = False
    for group, key in UEFN_PROJECT_FLAGS:
        bucket = experimental.setdefault(group, {})
        if bucket.get(key) is not True:
            bucket[key] = True
            changed = True

    if not changed:
        return ok_result("Project flags were already on")

    backup = marker + ".uefn-link.bak"
    try:
        if not os.path.exists(backup):
            shutil.copy2(marker, backup)
        with open(marker, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent="\t", ensure_ascii=False)
    except OSError as exc:
        return fail_result(
            "uefn_project_unwritable", f"could not write .uefnproject: {exc}")
    return ok_result(
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


def backend_log_since(log_path: str, start: int, limit: int = 4000) -> str:
    """Output written to the backend log at or after `start`, capped to `limit`.

    Only this launch's output is considered, so a stale "port in use" line from
    an earlier attempt cannot mislabel a fresh failure.
    """
    try:
        with open(log_path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(start, size - limit))
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""


# How long to wait for a freshly spawned backend to answer, in seconds. Module
# level so tests can shrink it instead of sleeping through the real value.
BACKEND_START_TIMEOUT = 90


def start_backend(port: int | None = None) -> dict:
    """Start `agent-canvas --backend-only` and wait for it to answer.

    The editor's MCP server is loopback-only, so the agent has to run on this
    same machine. Returns as soon as the server responds, or after a timeout.

    Three things this does before spawning anything, because a second
    agent-canvas cannot start at all:

    1. A healthy backend on the app's URL is reused.
    2. In "auto" mode a backend already running elsewhere on the machine (a
       full `agent-canvas` the user launched themselves, say) is adopted -- its
       ingress serves the same API, so it is the backend, not a competitor.
    3. If agent-canvas's fixed internal ports (18000/18001) are held, it says
       so at once instead of spawning a process that is guaranteed to abort.
    """
    # Default to the port the app actually probes, so the process we spawn and
    # the URL we read can never disagree.
    port = backend_port() if port is None else port

    # Already up on our own URL? Do not start a second instance.
    ok, _ = probe_backend()
    if ok:
        return ok_result("already running")

    # Someone else's backend, fully usable. Adopting it is the whole point:
    # two agent-canvas processes cannot coexist, so the choice is this or
    # nothing, and this one already works.
    if load_backend_choice()["mode"] == "auto":
        found = discover_backend()
        if found:
            return ok_result(
                f"using the agent backend already running on port {found['port']}",
                adopted_url=found["url"],
            )

    canvas = find_agent_canvas()
    if not canvas:
        return fail_result(
            "backend_missing",
            "agent-canvas is not installed.",
        )

    if agent_canvas_running_elsewhere():
        # 18000/18001 are held, so agent-canvas would abort on startup no
        # matter which ingress port we pass. Reported immediately: spawning
        # would only waste the start timeout to reach the same conclusion.
        return fail_result(
            "backend_already_running",
            "an agent-canvas is already running (its internal ports "
            f"{AGENT_CANVAS_INTERNAL_PORTS[0]}/{AGENT_CANVAS_INTERNAL_PORTS[1]} "
            "are in use), but it is not answering on "
            f"{BACKEND_URL}.",
        )

    if port_in_use(port):
        # Something else owns the port and it is not a healthy backend we can
        # use. Starting anyway would collide on the internal ports too.
        return fail_result(
            "backend_port_busy",
            f"port {port} is in use by another process, and the agent backend "
            "is not answering on it.",
        )

    log_path = os.path.join(WORKSPACE_DIR, "backend.log")
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    # Record where this launch begins so the tail we read on failure is ours,
    # not output from a previous attempt.
    log_offset = os.path.getsize(log_path) if os.path.exists(log_path) else 0
    try:
        with open(log_path, "ab") as log:
            subprocess.Popen(
                [resolve_command(canvas), "--backend-only", "--port", str(port)],
                stdout=log,
                stderr=log,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
    except OSError as exc:
        return fail_result(
            "backend_error", f"could not launch agent-canvas: {exc}")

    deadline = time.monotonic() + BACKEND_START_TIMEOUT
    while time.monotonic() < deadline:
        ok, detail = probe_backend()
        if ok:
            return ok_result("started")
        time.sleep(2)

    # It died or hung. agent-canvas names the reason in the log: either its
    # fixed internal ports (18000/18001) are held by another instance, or the
    # ingress port is taken. Read this launch's output so the app can name the
    # repair instead of reporting a generic timeout.
    tail = backend_log_since(log_path, log_offset)
    lowered = tail.lower()
    if "already in use" in lowered or "already be running" in lowered:
        if any(str(p) in tail for p in AGENT_CANVAS_INTERNAL_PORTS):
            return fail_result(
                "backend_already_running",
                f"agent-canvas could not start: its internal ports "
                f"{AGENT_CANVAS_INTERNAL_PORTS[0]}/{AGENT_CANVAS_INTERNAL_PORTS[1]} "
                f"are held by another agent-canvas (see {log_path}).",
            )
        return fail_result(
            "backend_port_conflict",
            f"agent-canvas could not bind its ports — something is already "
            f"using them (see {log_path}).",
        )
    return fail_result(
        "backend_timeout",
        f"started but not answering after {BACKEND_START_TIMEOUT}s "
        f"(see {log_path})",
    )


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
        elif self.path == "/api/backend/use":
            self._backend_use()
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
        # Probe the three services at once. Run in sequence, their timeouts add
        # up (UEFN 8s + backend 4s) and a slow or absent service can push the
        # whole response past a caller's patience. Concurrently the reply costs
        # about the slowest single probe.
        results = {}

        def run(name, fn):
            try:
                results[name] = fn()
            except Exception as exc:  # a probe must never take down the page
                results[name] = (False, f"probe failed: {exc}")

        probes = [
            ("uefn", lambda: probe_uefn(UEFN_MCP_URL)),
            ("backend", active_backend),
            ("llm", llm_status),
        ]
        workers = [threading.Thread(target=run, args=(n, f)) for n, f in probes]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

        uefn_ok, uefn_msg = results["uefn"]
        llm_ok, llm_msg = results["llm"]

        # active_backend() probes the configured URL and, only when that is
        # dark, looks for a backend running elsewhere -- so the happy path costs
        # one probe and the page's 10s poll stays cheap. It runs on the pool
        # above for the same reason the others do: a dead service must not stall
        # the whole page.
        back_ok, back_msg, active_url = results["backend"]
        choice = load_backend_choice()
        found = (
            {"url": active_url, "port": urlparse(active_url).port}
            if back_ok and active_url.rstrip("/") != BACKEND_URL.rstrip("/")
            else None
        )

        self._json(200, {
            "uefn": {
                "ok": uefn_ok, "detail": uefn_msg, "url": UEFN_MCP_URL,
                "guide": None if uefn_ok else guide_for("uefn_not_connected"),
            },
            "backend": {
                "ok": back_ok, "detail": back_msg, "url": active_url,
                "guide": None if back_ok else guide_for(backend_failure_code()),
                "mode": choice["mode"],
                "custom_url": choice["url"],
                "discovered": found,
            },
            "llm": {
                "ok": llm_ok, "detail": llm_msg,
                "guide": None if llm_ok else guide_for("model_missing"),
            },
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
        self._json(200, start_backend())

    def _backend_install(self):
        self._json(200, install_backend_dependencies())

    def _backend_use(self):
        """Save which backend to use: auto, own, or a custom URL.

        The choice is persisted, so a user who has to point the app at an
        existing backend does it once rather than on every launch.
        """
        body = self._read_body() or b"{}"
        try:
            request = json.loads(body.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json(400, {"error": "malformed request"})
        mode = (request.get("mode") or "auto").strip()
        url = (request.get("url") or "").strip()
        ok, detail = save_backend_choice(mode, url)
        if not ok:
            return self._json(400, {"error": detail})
        self._json(200, {"ok": True, "detail": detail, "mode": mode, "url": url})

    def _uefn_fix(self):
        results = []
        ini = configure_uefn_autostart()
        results.append({"step": "autostart", **ini})
        flags = enable_project_beta_flags()
        results.append({"step": "project flags", **flags})
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
        """One call that does everything this machine can do by itself.

        Each step carries its own guide on failure, so a red dot always
        explains how to clear it.
        """
        steps = []

        install = install_backend_dependencies()
        steps.append({"step": "install backend", **install})

        backend = start_backend()
        steps.append({"step": "start backend", **backend})

        ini = configure_uefn_autostart()
        steps.append({"step": "UEFN auto-start", **ini})

        flags = enable_project_beta_flags()
        steps.append({"step": "UEFN project flags", **flags})

        uefn_ok, uefn_detail = probe_uefn(UEFN_MCP_URL)
        if uefn_ok:
            steps.append({"step": "UEFN connection", **ok_result(uefn_detail)})
        else:
            steps.append({
                "step": "UEFN connection",
                **fail_result(
                    "uefn_not_connected",
                    f"{uefn_detail} — restart UEFN with the project open",
                ),
            })

        llm_ok, llm_detail = llm_status()
        if llm_ok:
            steps.append({"step": "model credential", **ok_result(llm_detail)})
        else:
            steps.append({
                "step": "model credential",
                **fail_result(
                    "model_missing",
                    "needs your key — enter it below (the one step that is not "
                    "automatable)",
                    manual=True,
                ),
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
            [resolve_command("openhands"), "mcp", "add", "uefn",
             "--transport", "http", UEFN_MCP_URL],
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

        back_ok, back_detail, back_url = active_backend()
        if not back_ok:
            return self._json(400, {
                "error": f"The agent backend is not available ({back_detail}).",
                "fix": "Press 'Start backend' above, or run:  "
                       + backend_start_command(),
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
            f"{back_url}/api/conversations",
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
            "url": f"{back_url}/conversations/{conversation_id}",
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
/* Failure guides: a red dot always explains how to clear it. */
.step{padding:5px 0;font-size:13.5px}
.step .sh{font-weight:500;word-break:break-word}
.step.good .sh{color:var(--ok)}
.step.bad .sh{color:var(--bad)}
.guide{margin:8px 0 6px;padding:11px 13px;background:#0d1117;
border:1px solid var(--line);border-left:3px solid var(--warn);border-radius:8px}
.guide .gt{font-weight:600;margin-bottom:7px;font-size:13px}
.guide ol{margin:0;padding-left:20px;font-size:13px;color:var(--fg)}
.guide li{margin:3px 0}
.guide .gd{margin-top:8px;font-size:12.5px;color:var(--ok)}
select{background:#0f1216;color:var(--fg);border:1px solid var(--line);
border-radius:8px;padding:8px;font:inherit;width:100%}
</style></head><body><div class="wrap">
<h1>__APP_NAME__</h1>
<div class="sub">Connects Unreal Editor for Fortnite to OpenHands agents running on this machine.</div>

<div class="card"><h2>Status</h2>
<div class="row"><span class="dot" id="d-uefn"></span><span class="label">Unreal Editor for Fortnite</span><span class="meta" id="m-uefn">checking…</span><button class="ghost sm" id="f-uefn" onclick="fixUefn()">Fix</button></div>
<div class="row"><span class="dot" id="d-back"></span><span class="label">Agent backend (this machine)</span><span class="meta" id="m-back">checking…</span><button class="ghost sm" id="f-back" onclick="fixBackend()">Fix</button><button class="ghost sm" onclick="toggleBackendForm()">Choose</button></div>
<div class="row"><span class="dot" id="d-llm"></span><span class="label">Model</span><span class="meta" id="m-llm">checking…</span><button class="ghost sm" id="f-llm" onclick="toggleKeyForm()">Set key</button></div>
<div class="actions"><button onclick="fixAll()" id="btn-fixall">Fix everything</button>
<button class="ghost" onclick="refresh()">Re-check</button>
<button class="ghost" id="btn-reg" onclick="register()">Register UEFN with OpenHands</button></div>
<div class="note" id="workspace"></div>
<div id="statusguides"></div>
<div class="note" id="fixlog"></div>
</div>

<div class="card" id="backcard" style="display:none"><h2>Agent backend</h2>
<div class="note">agent-canvas can only run one copy at a time — it always claims the same two internal ports (18000 and 18001), so a second copy cannot start no matter which port it is given. Pick how this app should find its backend.</div>
<div class="actions" style="margin-top:10px">
<select id="back-mode" onchange="onBackendMode()">
<option value="auto">Automatic — use any backend already running (recommended)</option>
<option value="own">This app's own — start one on port __BACKEND_PORT__</option>
<option value="custom">Another URL — I will type it</option>
</select></div>
<input id="back-url" placeholder="http://127.0.0.1:3000" style="display:none;width:100%;margin-top:8px;padding:9px;border-radius:8px;border:1px solid var(--line);background:#0f1216;color:var(--fg)">
<div class="actions"><button onclick="saveBackendChoice()">Save</button>
<button class="ghost" onclick="toggleBackendForm()">Close</button></div>
<div class="note" id="backhint"></div>
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

<div class="card"><h2>Use the hosted OpenHands UI</h2>
<div class="note">You can drive this same local backend from <b>app.all-hands.dev</b> instead of the conversation list above. The backend keeps running on this machine, so UEFN stays local and nothing is exposed to the internet.</div>
<div class="actions">
<button class="ghost" onclick="window.open('__CLOUD_URL__','_blank')">Open app.all-hands.dev</button>
<button class="ghost" onclick="toggleCloudHelp()">How to connect it</button>
</div>
<div id="cloudhelp" style="display:none">
<div class="note" style="border-left:3px solid var(--warn);padding-left:12px;margin-top:10px">
<b>Do not add the UEFN MCP server to the Cloud backend.</b> Conversations on the Cloud backend run in a cloud sandbox, where <code>127.0.0.1</code> means the sandbox itself — not your PC. There is no UEFN there, and no URL makes it work. It shows up as a connection error or "0 tools", and retrying never fixes it.
</div>
<div class="note">Instead, point the hosted UI at the backend already running here:</div>
<ol style="font-size:13px;padding-left:22px;margin:8px 0">
<li>Open <b>app.all-hands.dev</b>, then the <b>backend switcher</b> in the top bar.</li>
<li><b>Manage Backends</b> → <b>Add Backend</b>.</li>
<li>Host: this machine's backend URL. Session API key: the key from <code>~/.openhands/agent-canvas/api-key.txt</code>.</li>
<li>Save, then switch to it. UEFN MCP is registered against this backend, so the tools come with it.</li>
</ol>
<div class="note">MCP servers, model settings, and secrets are <b>per backend</b>. If the agent cannot see UEFN after switching, you are on the wrong backend — switch back and re-run <b>Register UEFN with OpenHands</b>.</div>
<div class="note" id="cloudbackendurl"></div>
</div>
</div>
</div>
<script>
const $=id=>document.getElementById(id);
function setDot(el,state){el.className='dot '+state}
function fmt(b){if(b<1024)return b+' B';if(b<1048576)return (b/1024).toFixed(1)+' KB';
if(b<1073741824)return (b/1048576).toFixed(1)+' MB';return (b/1073741824).toFixed(2)+' GB'}
function show(kind,text){const m=$('msg');m.className='msg show '+kind;m.textContent=text}
function hide(){$('msg').className='msg'}
function toggleCloudHelp(){const b=$('cloudhelp');b.style.display=b.style.display==='none'?'':'none'}

async function refresh(){
  try{
    const s=await (await fetch('/api/status')).json();
    setDot($('d-uefn'),s.uefn.ok?'ok':'bad');
    $('m-uefn').textContent=s.uefn.ok?'connected':s.uefn.detail;
    $('f-uefn').style.display=s.uefn.ok?'none':'';
    setDot($('d-back'),s.backend.ok?'ok':'bad');
    $('m-back').textContent=s.backend.ok?'running'+((s.backend.discovered)?' (port '+s.backend.discovered.port+')':''):s.backend.detail;
    $('f-back').style.display=s.backend.ok?'none':'';
    setDot($('d-llm'),s.llm.ok?'ok':'bad');
    $('m-llm').textContent=s.llm.detail;
    $('f-llm').style.display=s.llm.ok?'none':'';
    $('btn-reg').style.display=s.cli?'':'none';
    $('workspace').textContent='Workspace: '+s.workspace+'  ·  v'+s.version;
    $('cloudbackendurl').innerHTML='Add this backend host: <code>'+s.backend.url+'</code>';
    syncBackendForm(s.backend);
    renderStatusGuides(s);
    if(!presets.length&&s.presets){presets=s.presets;fillPresets()}
    renderFiles(s.uploads);
  }catch(e){show('err','Cannot reach the app server. Is it still running?')}
}
// Show the repair steps for whatever is red, so a bad row explains itself
// without the user having to press Fix first.
function renderStatusGuides(s){
  const box=$('statusguides');box.innerHTML='';
  [['Unreal Editor for Fortnite',s.uefn],['Agent backend',s.backend],['Model',s.llm]]
    .forEach(([name,row])=>{
      if(row&&!row.ok&&row.guide){
        const head=document.createElement('div');
        head.className='note';head.style.marginTop='10px';
        head.textContent=name+' needs attention:';
        box.appendChild(head);box.appendChild(renderGuide(row.guide));
      }
    });
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
function toggleBackendForm(){
  const c=$('backcard');
  c.style.display=c.style.display==='none'?'':'none';
}
// Keep the form honest about what the app is actually doing, without
// stomping on a choice the user is part-way through making.
function syncBackendForm(back){
  if(!back)return;
  const sel=$('back-mode');
  if(!sel.dataset.touched){sel.value=back.mode||'auto'}
  $('back-url').value=back.custom_url||'';
  onBackendMode();
  const hint=$('backhint');
  if(back.discovered){
    hint.textContent='Using the backend already running on port '+back.discovered.port+'.';
  }else if(back.ok){
    hint.textContent='Using '+back.url+'.';
  }else{
    hint.textContent='No backend found. Automatic will use one if you start agent-canvas yourself.';
  }
}
function onBackendMode(){
  $('back-url').style.display=$('back-mode').value==='custom'?'':'none';
}
async function saveBackendChoice(){
  const mode=$('back-mode').value,url=$('back-url').value.trim();
  $('back-mode').dataset.touched='1';
  if(mode==='custom'&&!url){show('err','Type the backend URL first, e.g. http://127.0.0.1:3000');return}
  try{
    const r=await (await fetch('/api/backend/use',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({mode,url})})).json();
    if(r.ok){show('ok','Backend set. Checking…');toggleBackendForm()}
    else{show('err',r.error||'Could not save the choice.')}
  }catch(e){show('err','Could not save the choice: '+e)}
  delete $('back-mode').dataset.touched;
  refresh();
}
function logStep(name,ok,detail,guide){
  const el=$('fixlog');
  const row=document.createElement('div');
  row.className='step '+(ok?'good':'bad');
  const head=document.createElement('div');
  head.className='sh';
  head.textContent=(ok?'✓ ':'• ')+name+(detail?' — '+detail:'');
  row.appendChild(head);
  if(!ok&&guide)row.appendChild(renderGuide(guide));
  el.appendChild(row);
}
function renderGuide(guide){
  const box=document.createElement('div');
  box.className='guide';
  if(guide.title){
    const t=document.createElement('div');
    t.className='gt';t.textContent=guide.title;box.appendChild(t);
  }
  if(guide.steps&&guide.steps.length){
    const ol=document.createElement('ol');
    guide.steps.forEach(s=>{
      const li=document.createElement('li');li.textContent=s;ol.appendChild(li);
    });
    box.appendChild(ol);
  }
  if(guide.done){
    const d=document.createElement('div');
    d.className='gd';d.textContent='Done when: '+guide.done;box.appendChild(d);
  }
  return box;
}
function clearLog(){$('fixlog').innerHTML=''}
async function fixAll(){
  clearLog();const b=$('btn-fixall');b.disabled=true;b.textContent='Fixing…';
  show('ok','Working through every step this machine can do on its own…');
  try{
    const r=await (await fetch('/api/fix-all',{method:'POST'})).json();
    r.steps.forEach(s=>logStep(s.step,s.ok,s.detail,s.guide));
    if(r.ok){show('ok','Everything is green. You can send work to UEFN.')}
    else{show('ok','Done what I can. Still needed: '+r.needs.join(', ')+' — see the steps below.')}
  }catch(e){show('err','Fix-all failed: '+e)}
  b.disabled=false;b.textContent='Fix everything';
  refresh();
}
async function fixBackend(){
  clearLog();show('ok','Installing and starting the agent backend…');
  try{
    const i=await (await fetch('/api/backend/install',{method:'POST'})).json();
    logStep('install backend',i.ok,i.detail,i.guide);
    if(!i.ok){show('err',i.detail+' — follow the steps below.');refresh();return}
    const s=await (await fetch('/api/backend/start',{method:'POST'})).json();
    logStep('start backend',s.ok,s.detail,s.guide);
    show(s.ok?'ok':'err',s.ok?'Backend is running.':s.detail+' — follow the steps below.');
  }catch(e){show('err','Could not set up the backend: '+e)}
  refresh();
}
async function fixUefn(){
  clearLog();show('ok','Writing UEFN settings…');
  try{
    const r=await (await fetch('/api/uefn/fix',{method:'POST'})).json();
    r.results.forEach(s=>logStep(s.step,s.ok,s.detail,s.guide));
    show(r.ok?'ok':'err',r.ok
      ?'UEFN configured. Restart UEFN with your project open, then Re-check.'
      :'Some UEFN settings could not be written — follow the steps below.');
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
    return (
        PAGE.replace("__APP_NAME__", APP_NAME)
        .replace("__BACKEND__", effective_backend_url())
        .replace("__BACKEND_PORT__", str(DEFAULT_BACKEND_PORT))
        .replace("__CLOUD_URL__", CLOUD_CANVAS_URL)
    )


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
    back_ok, back_detail, back_url = active_backend()
    llm_ok, llm_detail = llm_status()

    print(f"{APP_NAME} {APP_VERSION}")
    print(f"  app        {url}")
    print(f"  workspace  {WORKSPACE_DIR}")
    print(f"  uefn       {'connected' if uefn_ok else 'NOT reachable (' + uefn_detail + ')'}")
    if back_ok:
        note = "" if back_url == BACKEND_URL else f"  [{back_url}]"
        print(f"  backend    running{note}")
    else:
        print(f"  backend    NOT running ({back_detail})")
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

    if back_ok and back_url != BACKEND_URL:
        print(f"  Using the agent backend already running at {back_url}.")
        print("  agent-canvas allows only one copy per machine, so this app")
        print("  reuses it rather than starting a second one.")
        print()

    print("  Leave this window open. Press Ctrl+C to quit.")
    print()

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    if not back_ok and not args.no_autostart:
        def autostart():
            print("  Starting the agent backend...")
            result = start_backend()
            print(f"  backend    {'running' if result['ok'] else 'FAILED (' + result['detail'] + ')'}")
            if not result["ok"]:
                guide = result.get("guide") or {}
                for index, line in enumerate(guide.get("steps", []), 1):
                    print(f"    {index}. {line}")
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
