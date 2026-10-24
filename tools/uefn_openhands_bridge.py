"""Bridge UEFN to Agent Canvas so conversations can edit your project live.

Run this **on the machine running UEFN**. It answers the one question that
decides whether an agent can touch your project at all -- "can the agent's
execution environment reach UEFN's loopback port?" -- and then starts a
conversation on a backend that can.

    python tools/uefn_openhands_bridge.py check
    python tools/uefn_openhands_bridge.py register
    python tools/uefn_openhands_bridge.py start "Add a Print to OnBegin and compile"

Why this exists
---------------

UEFN's MCP server binds loopback, has no authentication, and is documented as
unsafe to expose. OpenHands Cloud sandboxes are remote, so their ``127.0.0.1``
is the sandbox, not your PC. An agent running there therefore cannot reach the
editor, no matter how the URL is written.

The supported arrangement is a backend **on the UEFN machine**. This script
verifies that arrangement and starts conversations against it. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

DEFAULT_UEFN_URL = "http://127.0.0.1:8000/mcp"
DEFAULT_BACKEND = "http://127.0.0.1:8000"

UEFN_CONTEXT = """\
You are working on a live Unreal Editor for Fortnite (UEFN) project through its
embedded Unreal MCP server. The `uefn-link` client is installed in this
workspace and speaks that server.

Ground rules for this project:
- UEFN's MCP server is loopback-only and unauthenticated. Never bind, tunnel, or
  expose it. Reach it at 127.0.0.1 only.
- UEFN MCP is a beta; tool names change between releases. Resolve tools against
  the live list (`uefn-link tools`, `uefn-link capabilities`) rather than
  assuming a name. Run `uefn-link doctor` first if anything looks wrong.
- After writing Verse, ALWAYS compile it (`uefn-link verse build`). A write that
  was not compiled proves nothing.
- Only properties visible in the UEFN interface can be written. Read back with
  `uefn-link device properties` and run `uefn-link validate` after a batch.
- New assets (textures, meshes) need a session restart to become visible:
  `uefn-link session cycle --restart`. Verse edits only need `session cycle`.
- Coordinates: Python-facing toolsets use XYZ, UEFN uses left-up-forward. Use
  the built-in conversion rather than adjusting numbers by hand.

Start by running `uefn-link doctor` and reporting the result.
"""


def http_json(url, payload=None, headers=None, timeout=10):
    """Minimal JSON request helper. Returns (status, body_dict_or_text)."""
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
        return exc.code, exc.read().decode()[:400]
    except Exception as exc:  # connection refused, DNS, timeout
        return None, str(exc)


def probe_mcp(url):
    """Ask the MCP endpoint to initialize. Confirms it is really UEFN's server."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "uefn-openhands-bridge", "version": "1.0"},
        },
    }
    status, body = http_json(url, payload, {"Accept": "application/json, text/event-stream"})
    if status is None:
        return False, body
    if status != 200:
        return False, f"HTTP {status}: {body}"
    text = body if isinstance(body, str) else json.dumps(body)
    return True, text[:200]


def session_key():
    """Locate the local backend's API key without printing it."""
    if os.environ.get("LOCAL_BACKEND_API_KEY"):
        return os.environ["LOCAL_BACKEND_API_KEY"]
    path = os.environ.get(
        "OH_SESSION_API_KEY_PATH",
        os.path.expanduser("~/.openhands/agent-canvas/api-key.txt"),
    )
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    return None


def cmd_check(args):
    """Report whether an agent in this environment can reach UEFN."""
    print("UEFN MCP endpoint")
    ok, detail = probe_mcp(args.uefn_url)
    print(f"  url       {args.uefn_url}")
    print(f"  reachable {'yes' if ok else 'NO'}")
    if not ok:
        print(f"  detail    {detail}")
        print("\n  Fix: open UEFN with your project, enable Python Editor Scripting")
        print("  and UEFN MCP Toolsets, then restart the editor. See")
        print("  docs/CONNECT-UEFN-TO-OPENHANDS.md for the full checklist.")
        return 1
    print("  handshake ok")

    print("\nAgent backend (must run on THIS machine)")
    key = session_key()
    status, body = http_json(
        f"{args.backend_url}/server_info",
        headers={"X-Session-API-Key": key} if key else {},
    )
    if status == 200:
        print(f"  url       {args.backend_url}")
        print("  reachable yes")
        print("\n  verdict   an agent on this backend CAN edit your project")
        return 0

    print(f"  url       {args.backend_url}")
    print("  reachable NO")
    print(f"  detail    {body}")
    print("\n  Fix: start a local backend on this machine:")
    print("           agent-canvas                  # UI + backend")
    print("           agent-canvas --backend-only --port 3010   # backend only")
    print("\n  Note: a conversation on the OpenHands Cloud backend CANNOT reach")
    print("  UEFN. Its 127.0.0.1 is the cloud sandbox, not this machine.")
    return 1


def cmd_register(args):
    """Register the UEFN MCP server with the local OpenHands CLI."""
    try:
        subprocess.run(["openhands", "--version"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        print("The `openhands` CLI is not on PATH.")
        print("Install it with:  uv tool install openhands --python 3.12")
        print("\nOr add the server in the Agent Canvas UI:")
        print("  Customize > MCP Servers > Add custom server")
        print("    Server Type: SHTTP")
        print("    Name:        uefn")
        print(f"    URL:         {args.uefn_url}")
        return 1

    proc = subprocess.run(
        ["openhands", "mcp", "add", "uefn", "--transport", "http", args.uefn_url],
        capture_output=True, text=True,
    )
    print(proc.stdout.strip() or proc.stderr.strip())
    if proc.returncode == 0:
        print("\nRegistered. MCP servers apply to NEW conversations only.")
        print("Verify with:  openhands mcp list")
    return proc.returncode


def cmd_start(args):
    """Start a conversation on the local backend, preloaded with UEFN context."""
    key = session_key()
    if not key:
        print("No local backend API key found.")
        print("Start the backend first (`agent-canvas --backend-only --port 3010`), or set")
        print("LOCAL_BACKEND_API_KEY / OH_SESSION_API_KEY_PATH.")
        return 1

    ok, detail = probe_mcp(args.uefn_url)
    if not ok:
        print(f"UEFN is not reachable at {args.uefn_url}: {detail}")
        print("Run `check` for the full diagnosis.")
        return 1

    payload = {
        "agent": {
            "kind": "Agent",
            "tools": [
                {"name": "terminal"},
                {"name": "file_editor"},
                {"name": "task_tracker"},
            ],
        },
        "workspace": {"kind": "LocalWorkspace", "working_dir": args.working_dir},
        "initial_message": {
            "content": [{"text": f"{UEFN_CONTEXT}\nTask: {args.task}"}],
            "run": True,
        },
    }
    status, body = http_json(
        f"{args.backend_url}/api/conversations",
        payload,
        {"X-Session-API-Key": key},
        timeout=60,
    )
    if status not in (200, 201) or not isinstance(body, dict):
        print(f"Could not start conversation (HTTP {status}).")
        print(body)
        return 1

    conversation_id = body.get("id") or body.get("conversation_id")
    print("Conversation started.")
    print(f"  id        {conversation_id}")
    print(f"  open at   {args.backend_url}/conversations/{conversation_id}")
    print("\nThe agent has UEFN context and starts with `uefn-link doctor`.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Connect UEFN to Agent Canvas so agents can edit your project live.",
    )
    parser.add_argument(
        "--uefn-url", default=os.environ.get("UEFN_MCP_URL", DEFAULT_UEFN_URL)
    )
    parser.add_argument(
        "--backend-url",
        default=os.environ.get("LOCAL_AGENT_SERVER_URL", DEFAULT_BACKEND),
        help="local Agent Canvas backend (must run on the UEFN machine)",
    )
    parser.add_argument("--working-dir", default=os.getcwd())

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="verify UEFN and the backend are reachable")
    sub.add_parser("register", help="add the UEFN MCP server to OpenHands")
    start = sub.add_parser("start", help="start a UEFN-aware conversation")
    start.add_argument(
        "task", nargs="?", default="Run uefn-link doctor and report the result."
    )

    args = parser.parse_args(argv)
    if args.command == "check":
        return cmd_check(args)
    if args.command == "register":
        return cmd_register(args)
    return cmd_start(args)


if __name__ == "__main__":
    sys.exit(main())
