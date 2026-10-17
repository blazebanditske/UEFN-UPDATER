"""End-to-end example: what a full agent session against UEFN looks like.

Run it against a live editor::

    python examples/live_mechanic_workflow.py

It is written to be safe on a real project: it reads before it writes, it
compiles after writing, and it does not start a play session on its own.
"""
from __future__ import annotations

import sys

from uefn_link import UEFNLink, UEFNLinkError
from uefn_link.doctor import diagnose

VERSE_PATH = "Verse/agent_mechanic.verse"

MECHANIC = '''using { /Fortnite.com/Devices }
using { /Verse.org/Simulation }

# Written by an agent through the UEFN MCP link.
agent_mechanic := class(creative_device):

    @editable
    Message : string = "agent was here"

    OnBegin<override>()<suspends>:void=
        Print(Message)
'''


def main() -> int:
    report = diagnose()
    if not report.get("ok"):
        print("Link is not ready. Run `uefn-link doctor` for details.")
        return 1

    try:
        link = UEFNLink().connect()
    except UEFNLinkError as exc:
        print(f"Could not connect: {exc}")
        return 1

    print(f"connected to {link.transport.url} with {link.tool_count()} tools\n")

    # 1. Inventory before changing anything.
    print("--- existing Verse files ---")
    print(link.verse.list_files())

    # 2. Write a mechanic and compile it. The build result is the correctness
    #    signal; a write on its own proves nothing about the code.
    print("\n--- writing and building ---")
    result = link.verse.apply_and_build(VERSE_PATH, MECHANIC)
    print(f"build_ok={result['build_ok']}")
    print(result["compiled"])
    if not result["build_ok"]:
        print("Build failed; stopping before touching the level.")
        return 1

    # 3. Inspect the scene graph.
    print("\n--- entities in the level ---")
    print(link.entity.list_entities())

    # 4. Confirm the devices available in this project.
    print("\n--- placeable devices ---")
    print(link.device.list_assets())

    # 5. Validate, because UEFN rejects non-allow-listed property writes at
    #    validation time even when the write appeared to succeed.
    print("\n--- validation ---")
    print(link.project.validate())

    # 6. Session status only; starting a session is the creator's call.
    print("\n--- session ---")
    print(link.session.status())

    return 0


if __name__ == "__main__":
    sys.exit(main())
