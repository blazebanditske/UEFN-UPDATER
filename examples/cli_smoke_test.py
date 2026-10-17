"""Smoke-test the installed CLI against a live fake editor over real HTTP."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.fake_uefn import FakeUEFNServer  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

COMMANDS = [
    ["doctor"],
    ["tools", "--grep", "verse"],
    ["capabilities"],
    ["verse", "build"],
    ["entity", "list"],
    ["device", "list"],
    ["session", "status"],
    ["validate"],
]

with FakeUEFNServer() as srv:
    env = dict(os.environ, PYTHONPATH=ROOT)
    for args in COMMANDS:
        proc = subprocess.run(
            [sys.executable, "-m", "uefn_link.cli", "--port", str(srv.port), *args],
            capture_output=True, text=True, env=env, cwd=ROOT,
        )
        print(f"=== uefn-link {' '.join(args)}  (exit {proc.returncode}) ===")
        print((proc.stdout or proc.stderr).strip()[:700])
        print()
