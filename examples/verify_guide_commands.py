"""Verify every exact command quoted in the connection guide actually runs.

Parses the guide for fenced ``uefn-link`` invocations and executes each one
against the fake editor, so the documentation cannot drift from the CLI.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tests.fake_uefn import FakeUEFNServer  # noqa: E402

GUIDE = os.path.join(ROOT, "docs", "CONNECT-UEFN-TO-OPENHANDS.md")

# Commands in the guide that are illustrative, not runnable as written.
SKIP_MARKERS = (
    "openhands mcp add",       # needs the real openhands CLI
    "agent-canvas",            # needs the real launcher
    "cloudflared",             # needs a tunnel
    "curl ", "uv tool", "pip install", "openssl", "powershell",
)


def extract_commands(text):
    """Pull uefn-link invocations out of shell code fences."""
    found = []
    for block in re.findall(r"```(?:bash|shell)?\n(.*?)```", text, re.S):
        for raw in block.splitlines():
            line = raw.strip()
            if not line.startswith("uefn-link"):
                continue
            # Strip trailing inline comments so they are not passed as argv.
            line = re.sub(r"\s+#.*$", "", line).strip()
            if any(marker in line for marker in SKIP_MARKERS):
                continue
            found.append(line)
    return found


def main():
    with open(GUIDE, encoding="utf-8") as handle:
        guide = handle.read()

    commands = extract_commands(guide)
    # De-duplicate while preserving order.
    seen, ordered = set(), []
    for cmd in commands:
        if cmd not in seen:
            seen.add(cmd)
            ordered.append(cmd)

    print(f"found {len(ordered)} runnable uefn-link commands in the guide\n")

    failures = 0
    with FakeUEFNServer() as srv:
        env = dict(os.environ, PYTHONPATH=ROOT, UEFN_MCP_PORT=str(srv.port))
        for cmd in ordered:
            argv = cmd.split()
            # Point the file-based write at a real local file.
            if "--from-file" in argv:
                idx = argv.index("--from-file")
                if argv[idx + 1] in ("./probe.verse", "probe.verse"):
                    argv[idx + 1] = os.path.join(
                        ROOT, "uefn_project", "Verse", "probe.verse"
                    )
            proc = subprocess.run(
                [sys.executable, "-m", "uefn_link.cli", *argv[1:]],
                capture_output=True, text=True, env=env, cwd=ROOT,
            )
            status = "ok " if proc.returncode == 0 else f"FAIL({proc.returncode})"
            if proc.returncode != 0:
                failures += 1
            print(f"[{status}] {cmd}")
            if proc.returncode != 0:
                print("        " + (proc.stderr or proc.stdout).strip()[:200])

    print(f"\n{failures} failing command(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
