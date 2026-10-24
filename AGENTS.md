# AGENTS.md

Repository notes for agents working on this codebase.

## What this is

A client that drives Unreal Editor for Fortnite through UEFN's embedded Unreal
MCP server. The editor exposes typed tools over loopback HTTP; this project
speaks that protocol and wraps it in a domain layer plus a CLI.

There is no editor-side plugin to write or maintain. If you find yourself
wanting to add one, re-check `uefn-link capabilities` first — Epic may already
expose the operation.

## Commands

```bash
python -m pytest -q                     # full suite (~35s; starts real HTTP servers)
python -m pytest tests/test_domains.py -q
pip install -e . && uefn-link doctor    # console script
python -m pyflakes uefn_link/ tests/ examples/   # keep this clean
python examples/cli_smoke_test.py       # CLI as a subprocess against a fake editor
```

## Architecture, and why

Three layers, each with one job:

1. **`uefn_link/mcp/`** — protocol. JSON-RPC message building (`jsonrpc.py`),
   SSE parsing (`sse.py`), the HTTP transport (`transport.py`), and the client
   (`client.py`). Stdlib only, deliberately: this must run under the Python that
   ships with UEFN as well as a system interpreter.
2. **`uefn_link/domains/`** — capability wrappers. Each domain maps a *logical*
   operation (`verse.compile`) onto a real tool call. Domains also hold the
   judgement calls: `VerseDomain._build_ok` decides what counts as a successful
   build, `SessionDomain.live_edit_cycle` encodes the push-vs-restart ordering.
3. **`uefn_link/cli.py`** — thin dispatch over the domains. Scriptable:
   `--json`, meaningful exit codes, no prompts.

### The one rule that matters

**Never hardcode a tool name in a domain.** UEFN MCP is a beta and Epic renames
and adds tools between releases. Domains ask `uefn_link/mcp/registry.py` for
candidate names plus fallback fragments, and `MCPClient.resolve` picks whatever
the editor actually advertises, trying exact names first and substring groups
second.

When you need to add an operation:

1. Add an entry to the right table in `registry.py` with several plausible exact
   names and at least one fragment group. Use *alternative* groups
   (`[("verse","compil"), ("verse","build")]`) when a rename could plausibly
   drop either word.
2. Add the method to the domain.
3. Add a test that drives the real registry entry, not a hand-written candidate
   list — otherwise the test passes while the shipped fallbacks are broken.

If a lookup fails in the field, the fix is to inspect the live list
(`uefn-link tools --grep <word>`) and widen the registry entry. Do not add a
hardcoded call.

### Result shapes

UEFN toolsets usually return their payload as a **JSON string inside a text
content block**, not as structured content. So `extract_text` flattens and
`extract_structured` unwraps the JSON. A domain that needs a dict or list must
use `extract_structured`; indexing the raw MCP envelope will not work.

### Failure philosophy

- Preflight with `doctor` rather than letting a connection error stand in for
  every possible misconfiguration. Each check reports its own fix.
- Diagnostics that a workflow merely *wants* use `try_call_text` and degrade.
  Only what a workflow genuinely *needs* raises. See
  `SessionDomain.live_edit_cycle`, which tolerates a missing game-state tool.
- Reads that confirm a write are part of the API surface
  (`DeviceDomain.verify_property`), because UEFN can accept a property write and
  still reject it at validation.

## Testing conventions

Tests use a real HTTP server on a real socket (`tests/fake_uefn.py`), not mocks
of client internals. Keep it that way — the transport, SSE, pagination, and
discovery bugs this suite has caught were only visible end to end.

- `FakeUEFNServer(handler=...)` lets a test override tool behaviour.
- `FakeUEFNServer(tools=[...])` lets a test simulate a different editor build —
  use it for rename and missing-tool cases.
- `use_sse=True` and `page_size=N` exercise the SSE and pagination paths.

The only simulated component should be the editor itself.

## Domain rules worth not re-deriving

- **Assets go through the Unreal Python API, never `os`/`shutil`.** Moving asset
  files on disk breaks internal content references. There is a test asserting no
  generated script imports `os` or `shutil`.
- **Reimport over re-import-as-new** when updating textures: reimport preserves
  the asset identity, so every material referencing it updates.
- **Coordinates**: Python-facing toolsets use XYZ, UEFN uses left-up-forward.
  Convert through `uefn_link/coords.py` and never inline the flip.
- **Long timeouts for session/build operations.** A session start or Verse build
  legitimately takes minutes, and Epic lists editor hitching during MCP calls as
  a known issue.

## Environment

- Python 3.9+. No runtime dependencies.
- `pytest` and `pyflakes` for development only.
- The editor is loopback-only and unauthenticated. `HttpTransport` refuses
  non-local hosts on purpose; do not add a remote mode.

## Agent backend, verified facts

These cost real debugging time to establish. Do not re-derive them.

- The local backend is `agent-canvas --backend-only` from
  `@openhands/agent-canvas` (npm). It serves the agent-server REST API.
- It defaults to port **8000**, which collides with UEFN's own MCP server.
  The app uses **3000**. Never point the backend at 8000.
- **Only one agent-canvas can run per machine.** Besides its ingress port it
  binds fixed internal ports (agent-server **18000**, automation **18001**), so a
  second launch dies with `Cannot start: the following ports are already in use`
  *regardless of which ingress port you pass*. `start_backend` therefore probes
  first and returns `"already running"` instead of spawning. A busy ingress port
  is reported, not worked around — retrying on another port cannot help.
- `npm install -g` needs a writable global prefix. On Linux CI that is often
  `/usr/local` (not writable); on Windows it is `%APPDATA%\npm` (writable). The
  installer checks `os.access(prefix, os.W_OK)` and, if it fails, prints the
  `npm config set prefix` fix rather than npm's raw EACCES wall.
- **Never hand a bare tool name to `subprocess`.** On Windows `npm`, `node`, and
  `agent-canvas` are `.cmd`/`.bat` shims. `shutil.which` finds them, but
  `CreateProcess` only auto-appends `.exe`, so `subprocess.run(["npm", ...])`
  dies with `[WinError 2] The system cannot find the file specified` — which is
  exactly how the app reported it (`install backend — could not run npm:
  [WinError 2] ...`). Route every spawn through `resolve_command(name)`, which
  tries the bare name then `.cmd`/`.exe`/`.bat` when `IS_WINDOWS`. `IS_WINDOWS`
  is a module constant, not `os.name` at the call site, so tests can drive both
  branches on any platform.
- `POST /api/conversations` **requires** `agent.llm` (model, api_key, base_url).
  Omitting it is HTTP 422. Omitting `agent` entirely is also 422.
- The session key at `~/.openhands/agent-canvas/api-key.txt` authorizes the
  backend API. It is **not** an LLM credential; using it as one gives
  `LLMAuthenticationError`.
- `openhands login` (OAuth device flow) writes `~/.openhands/agent_settings.json`
  with the model, proxy URL, and a minted key. It mints that key from
  `GET /api/keys/llm/byor`, which returns **HTTP 402** until the account has
  credits. A `201` conversation that errors on run is almost always this.
- The app therefore resolves the LLM key *before* starting a conversation and
  refuses with a clear message if there is none, instead of failing later.

## UEFN editor settings, verified facts

What `POST /api/uefn/fix` writes, and why it is safe to write.

- UEFN's MCP server default is port **8000**, path **`/mcp`**.
- Editor Preferences → Model Context Protocol persists to
  `%LOCALAPPDATA%\UnrealEditorFortnite\Saved\Config\WindowsEditor\EditorPerProjectUserSettings.ini`
  under `[/Script/ModelContextProtocol.ModelContextProtocolSettings]`, keys
  `bAutoStartServer`, `ServerPortNumber`, `ServerUrlPath`. (On Linux/macOS the
  same path sits under `~/.config/`.) `set_ini_keys` edits only the named
  section and named keys, so unrelated prefs survive byte for byte; there is a
  test asserting a same-named key in another section is untouched.
- `.uefnproject` is JSON. The beta flags live at
  `dataSets.experimental.pythonExperimental.bEnablePythonForProject` and
  `dataSets.experimental.toolsets.bEnableToolsetsForProject`.
- Both writers back up to `<file>.uefn-link.bak` before the first change and are
  idempotent, so pressing Fix twice is harmless.
- **These edits are necessary but not always sufficient.** Epic gates "UEFN MCP
  Toolsets" behind account-level **Beta Access**, and the validation layer
  ignores manual INI overrides without it. Symptom: no Beta Access section in
  Project Settings, and `Python disabled via CVar
  'Engine.Python.IsEnabledByDefault'` in the Output Log. Do not try to defeat
  that gate in code — report it. The app's fix-all says so explicitly rather
  than claiming success.

## The failure-guide contract

Every failure the app can surface must tell the user how to clear it. This is a
hard rule, not a nicety: the target user double-clicks an exe and never sees a
terminal.

- `install_backend_dependencies`, `start_backend`, `configure_uefn_autostart`,
  and `enable_project_beta_flags` return a **dict**, never a tuple. Build it with
  `ok_result(detail)` or `fail_result(code, detail, **extra)`.
- A failure dict carries `ok`, `code`, `detail`, and `guide`. `guide_for(code)`
  looks the code up in `GUIDES`; an unknown code still yields a generic guide, so
  a missed entry degrades to "press Fix everything" instead of a dead end.
- A guide is `{title, steps, done}`: what went wrong, an ordered repair, and the
  observable state that means it worked. `steps` is always non-empty.
- Passing results carry `guide: None`. Do not attach a repair to a green row.
- `/api/status` attaches a guide to each red row, so the window explains itself
  before the user presses anything. The backend row uses `backend_failure_code()`
  to distinguish *not installed* from *installed but stopped* — different
  repairs, and the app can tell them apart for free.
- The page renders guides through one `renderGuide()` used by both the status
  card and the fix log. Add new UI surfaces through it rather than formatting
  steps inline.

When you add a failure path, add its code to `GUIDES` and to the expected set in
`tests/test_app.py::GuideCoverageTests`; that test fails on an uncovered code.


## Windows shell and port facts

- Never quote a portless agent-canvas command. agent-canvas defaults its ingress
  to 8000, which is UEFN's own MCP port, so a portless start dies with "the
  following ports are already in use: ingress: port 8000". Every command the app
  shows the user, and every doc line, must carry `--port <backend_port()>`.
  backend_start_command() builds that string and guide_for() substitutes it into
  the guides, so the value always tracks BACKEND_URL (which
  LOCAL_AGENT_SERVER_URL can override). start_backend defaults its port to
  backend_port() too, so the process it spawns and the URL it probes cannot drift
  apart. A test asserts no guide ever quotes a portless start command.
- PowerShell cannot run npm by default. Windows ships npm as npm.cmd, npm.ps1,
  and npm (a bash script). PowerShell resolves npm to npm.ps1 and refuses it
  under the default Restricted policy: "File ...npm.ps1 cannot be loaded because
  running scripts is disabled on this system". That is not an npm failure. The
  app therefore (a) prefers npm.cmd in npm_command() and never the .ps1, and
  (b) classifies the error text with npm_failure_code(), which routes it to the
  npm_powershell_blocked guide that says "use Command Prompt". Every guide that
  tells the user to run npm names Command Prompt explicitly. Do not suggest
  Set-ExecutionPolicy as the fix: it weakens a machine-wide security setting to
  work around a shell the app does not need.
- start_backend's wait is BACKEND_START_TIMEOUT (module constant, default 90s).
  Keep it injectable: tests set it to 0.1s, otherwise the spawn test sleeps the
  full 90s and the suite triples in runtime. When you change the wait, change the
  constant, not a literal buried in the loop.
