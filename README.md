# uefn-link

**Want the easy version?** There is a double-clickable app:
[docs/APP.md](docs/APP.md). One file, no install, drag-and-drop uploads, and a
single button that sends instructions to UEFN.

**Connecting this to an OpenHands agent?** See
[docs/CONNECT-UEFN-TO-OPENHANDS.md](docs/CONNECT-UEFN-TO-OPENHANDS.md) for the
full step-by-step guide, including how to drive a live UEFN session from
Agent Canvas or the app.all-hands.dev UI.

An agent-controlled link to **Unreal Editor for Fortnite (UEFN)**. It reads and
writes Verse, builds it, drives the Scene Graph, configures Creative devices,
runs play sessions, and executes Python inside the editor for asset work such as
importing and updating textures.

The transport is UEFN's own embedded **Unreal MCP** server. There is no plugin
to install on the editor side and no screen-scraping: UEFN exposes typed tools
over local HTTP, and this project is a client for them.

```
agent / CLI / script
        │  JSON-RPC over HTTP (loopback)
        ▼
UEFN editor process ── Unreal MCP server ── toolsets
                                          ├─ Verse (read/write/compile)
                                          ├─ Verse Scene Graph (entities, components)
                                          ├─ Creative Devices (@editable properties)
                                          ├─ Sessions (Play in Creator)
                                          └─ Python editor scripting (assets)
```

## What it can do

| Task | Command | Notes |
| --- | --- | --- |
| Read/write/search Verse | `uefn-link verse read\|write\|grep` | Gameplay logic. Sandboxed to your project. |
| Compile Verse | `uefn-link verse build` | The only reliable correctness check for generated code. |
| Inspect the level | `uefn-link entity list` | Every entity and its components. |
| Build the level | `uefn-link entity create\|add-component\|transform\|move` | Creates real, editable entities. |
| Place devices | `uefn-link device place` | Real devices you can select in the outliner. |
| Tune devices | `uefn-link device set\|properties\|bind` | Any `@editable` property; event bindings. |
| Run a play session | `uefn-link session start\|stop\|push\|cycle` | Play in Creator (PIC), not PIE. |
| Read runtime logs | `uefn-link session log` | Where Verse `Print` and runtime errors land. |
| Import/update textures | `uefn-link python import-texture\|reimport-texture` | Via the Unreal Python API. |
| Any editor action | `uefn-link call <Tool> --args '{...}'` | Escape hatch for any advertised tool. |
| Diagnose the setup | `uefn-link doctor` | Reports the first blocker and its fix. |

## Setup

**Connecting this to an OpenHands agent?** See
[docs/CONNECT-UEFN-TO-OPENHANDS.md](docs/CONNECT-UEFN-TO-OPENHANDS.md) for the
full step-by-step guide, including how to drive a live UEFN session from
Agent Canvas or the app.all-hands.dev UI.

Three steps, two of which are in UEFN.

### 1. In UEFN, enable the two settings

Open your project, then **Project Settings** and enable **both**:

- **Python Editor Scripting**
- **UEFN MCP Toolsets** (under Beta Access)

Both are required. UEFN only publishes toolsets at startup, so **restart the
editor** after changing either one.

Optionally set the **Server Port Number** in Editor Preferences if port `8000`
is already taken.

### 2. Generate the client config

UEFN writes an `.mcp.json` into your project root. The CLI and library read it
automatically, so you do not have to keep the port in sync by hand.

### 3. Install this client

```bash
pip install -e .
uefn-link doctor
```

`doctor` walks the failure modes in order and tells you which one you hit:

```
endpoint   127.0.0.1:8000/mcp  (.mcp.json)
[ok  ] port_reachable   MCP port answered
[ok  ] handshake        MCP initialize succeeded
[ok  ] tools            41 tools advertised

capability groups
  verse      8/9
  entity     7/9
  device     6/8
  session    6/8
  python     2/2
  project    2/2

link ready
```

If a group shows fewer operations than expected, that is normal: UEFN exposes
only a subset of the Unreal Engine toolsets, and the set changes between
releases. `uefn-link capabilities` prints exactly which operations resolve on
*your* build, and `uefn-link tools` lists the raw tool names.

## Usage

### From an agent or a script

```python
from uefn_link import UEFNLink

link = UEFNLink().connect()          # finds the editor via .mcp.json

# Fix a bug or add a mechanic in Verse, then prove it compiles.
result = link.verse.apply_and_build("Verse/round_scorer.verse", code)
if not result["build_ok"]:
    print(result["compiled"])        # compiler errors
    raise SystemExit(1)

# Update a texture without losing references.
link.python.reimport_texture("/Game/Textures/T_Rock")

# Tune a device and confirm the write actually took.
link.device.set_property("Timer_1", "Duration", 60.0)
assert link.device.verify_property("Timer_1", "Duration", 60.0)

# Move something, in either axis convention.
link.entity.move("Rock_1", [0, 0, 500])
```

### From the command line

```bash
uefn-link verse read Verse/game_manager.verse
uefn-link verse write Verse/new_mechanic.verse --from-file ./new_mechanic.verse --build
uefn-link entity move Rock_1 0 0 500
uefn-link device set Timer_1 Duration 60
uefn-link session cycle --restart      # restart so new assets are visible
uefn-link session log                  # read what the client printed
```

Add `--json` to any command for machine-readable output. Exit codes: `0` ok,
`1` error, `2` tool not found.

### Connection settings

Resolved in this order:

1. `--host` / `--port` / `--path` flags
2. `UEFN_MCP_URL` (for example `http://127.0.0.1:8000/mcp`)
3. `UEFN_MCP_HOST`, `UEFN_MCP_PORT`, `UEFN_MCP_PATH`
4. an `.mcp.json` found while walking up from the current directory
5. `127.0.0.1:8000/mcp`

## The UEFN-side scaffold

`uefn_project/` holds the files that go **into** your project:

- `.mcp.json` — the client config UEFN generates, kept as a reference copy.
- `tools/init_unreal.py` — a UEFN startup hook. Copy it to your project's
  `Content/Python/` folder. It verifies Python is live and logs the project
  content root, which is what you need when several islands are open.
- `tools/texture_pipeline.py` — import, reimport, and retexture assets using the
  Unreal Python API. Run it in-editor with **Tools > Execute Python Script**.
- `Verse/round_scorer.verse` — the shape to follow for a device-driven mechanic:
  a `creative_device` subclass with `@editable` fields and device event
  subscriptions.

## Honest limits

These are properties of UEFN, not of this client. Knowing them up front saves a
lot of confusion.

- **Python and the toolsets are a beta.** Epic documents that the API changes
  between releases. This client is *discovery-first* for that reason: it reads
  the live tool list and resolves operations against it, so a renamed tool
  still resolves through fallbacks, and a genuinely missing one produces a clear
  message instead of a crash. If something stops resolving, run
  `uefn-link tools` and compare.
- **Play sessions are Play in Creator, not Play in Editor.** There is a separate
  client process, so "start a session" means waiting on a client, not just
  flipping a flag.
- **New assets need a session restart.** Pushing changes delivers Verse edits,
  but a texture or mesh added since the session started is not visible until you
  relaunch. `session cycle --restart` does that; `session cycle` alone pushes.
- **Only UI-visible properties are allow-listed.** A write to a property that
  is not exposed in the UEFN interface can appear to succeed and then fail
  project validation. That is why `device.verify_property` exists and why
  `validate` is worth running after a batch of edits.
- **Coordinates disagree between layers.** The Python-facing toolsets use XYZ
  while UEFN uses left-up-forward, which Epic lists as a known issue. This
  client converts explicitly (`--luf` on the CLI, `luf=True` in the library)
  rather than silently guessing.
- **Tool calls can hitch the editor.** Epic lists editor hangs during MCP calls
  as a known issue. Long operations therefore get long timeouts (a session start
  or Verse build waits up to 10 minutes) instead of failing early.
- **Some actions have no scripting surface.** Launch Session and the toolbar
  actions are clicked by a human. Where Epic provides a tool, this client uses
  it; where it does not, there is nothing to call.
- **Loopback only, no authentication.** The editor's server is not safe to
  expose beyond your machine, and this client refuses non-local hosts outright.
- **Some things are still yours to do.** Enabling the two project settings,
  approving the agent, and opening the project when UEFN stops at the hub are
  manual steps by design.

## Tests

```bash
python -m pytest -q
```

The suite runs a real HTTP server that speaks the MCP subset UEFN exposes
(`tests/fake_uefn.py`) on a real socket. The transport, SSE parsing, JSON-RPC
handling, pagination, tool discovery, every domain, the CLI, and the doctor are
all exercised against it — the editor is the only simulated part.

`examples/cli_smoke_test.py` runs the CLI as a subprocess against the same
server. `examples/live_mechanic_workflow.py` is the end-to-end workflow to run
against a live editor.

## Layout

```
uefn_link/
  mcp/          JSON-RPC, SSE, HTTP transport, discovery-first client, name hints
  domains/      verse, entity, device, session, python, project
  coords.py     LUF <-> XYZ conversion
  config.py     connection settings discovery
  doctor.py     ordered preflight checks
  cli.py        command-line interface
uefn_project/   files that live inside your UEFN project
examples/       runnable workflows
tests/          real-HTTP end-to-end tests
```

## References

- [UEFN MCP](https://dev.epicgames.com/documentation/fortnite/uefn-mcp) — Epic's documentation for the embedded server this client talks to.
- [Python Tools in UEFN](https://dev.epicgames.com/documentation/fortnite/python-tools-in-uefn) — asset pipeline rules and validation caveats.
- [Unreal MCP in Unreal Editor](https://dev.epicgames.com/documentation/unreal-engine/unreal-mcp-in-unreal-editor) — the shared architecture and its limitations.
- [Unreal MCP is now available in UEFN](https://www.fortnite.com/news/unreal-mcp-is-now-available-in-uefn) — the release announcement.
