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
  The app uses **3010**. Never point the backend at 8000.
- **Only one agent-canvas can run per machine.** Besides its ingress port it
  binds fixed internal ports (agent-server **18000**, automation **18001**), so a
  second launch dies with `Cannot start: the following ports are already in use`
  *regardless of which ingress port you pass*. Verified against
  `@openhands/agent-canvas` 1.33.0: `config/defaults.json` pins those ports and
  `scripts/dev-with-automation.mjs` calls `assertPortsFree` before starting
  anything. The constants are `AGENT_CANVAS_INTERNAL_PORTS`.
- **A running agent-canvas is adoptable, not a competitor.** Its ingress routes
  `/server_info` and `/api` to the agent-server behind it (same routes the app
  already uses), so a full `agent-canvas` the user started themselves IS a
  usable backend. `start_backend` therefore probes for one and returns
  `adopted_url` instead of spawning. This is the fix for the reported "Agent
  backend (this machine) will not run" failure, which was always a second copy
  aborting on 18000/18001. Do not "fix" it by killing the user's process.
- **`backend_already_running` is distinct from `backend_port_busy`.** Held
  internal ports mean no second copy can start at all — reported immediately,
  with no spawn and no wait. A held ingress port alone is `backend_port_busy`.
  `agent_canvas_running_elsewhere()` is the test for the former, and it gates
  `backend_failure_code()` so the status row shows the right guide.
- **Discovery is opt-in per call and defers to an explicit URL.** `discover_backend()`
  probes `BACKEND_CANDIDATE_PORTS` (3010, 3000, 8000, 18000) but returns None
  when `LOCAL_AGENT_SERVER_URL` is set: an explicit instruction is not a hint. It
  also skips the configured URL, which the direct probe already covers. A probe
  that raises is swallowed — a candidate we cannot reach is simply not one.
- **Discovery has a hard wall-clock budget, and that is not optional.**
  `BACKEND_DISCOVERY_BUDGET` (1s) caps the whole search; the per-port budgets in
  `BACKEND_PROBE_TIMEOUTS` are only a guide, clamped to whatever is left of the
  deadline. This exists because an earlier version summed 4s + 4×1.5s = 10s,
  passed locally, then **failed CI on Windows**: that runner filters closed
  loopback ports instead of refusing them, so every probe waited out its full
  timeout, `/api/status` exceeded 10s, and two tests died with `TimeoutError`.
  `port_in_use()` is checked first for exactly this reason — a closed port is
  ruled out in microseconds and never reaches a probe. Measured worst case now:
  8.1s, which is UEFN's own 8s probe plus ~0.1s, comfortably inside the 10s
  poll. If you add a candidate, add its budget to `BACKEND_PROBE_TIMEOUTS`; one
  test asserts every candidate has one, another asserts the total stays small.
- **Never pipe `curl` into `grep -q` in the CI smoke test.** The runner uses
  `set -o pipefail`, `grep -q` exits on its first match, curl then dies of
  SIGPIPE with exit 23, and the step fails even though the check passed. Fetch
  to a file and grep the file — see `check()` in build-app.yml.
- **Backend choice is persisted in `backend.json`** under the workspace, via
  `load_backend_choice()`/`save_backend_choice()`. Modes are `auto` (adopt or
  start), `own` (always start on 3010), `custom` (pin a URL). The path is a
  *function*, `backend_settings_path()`, because `WORKSPACE_DIR` is reassigned
  by `main()` and by tests — a module constant would read the wrong file.
- **`active_backend()` is the single source of "is it up, and where".** The
  status row and `/api/start` both call it, so they cannot disagree. It runs on
  the status thread pool (see the cost rules below): it is cheap on the happy
  path because discovery only runs when the configured URL is dark.
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

## agent-canvas serving modes, verified facts

The app starts the backend with `--backend-only`. That choice has a visible
consequence, and it caused a real bug.

- `agent-canvas --backend-only` runs **agent-server + automation behind
  ingress and mounts no frontend**. The ingress then answers every page route
  (`/`, `/conversations/<id>`) with **HTTP 503 "No backend configured for this
  route"**. Verified live against `@openhands/agent-canvas` 1.34.0: the
  `/server_info`, `/api`, and `/sockets` routes work, page routes 503.
- A **full** `agent-canvas` (no `--backend-only`) additionally starts a static
  frontend service (port 3001) and passes `--default http://localhost:3001` to
  ingress, so `/*` serves the UI. Verified live: `/` and `/conversations/<id>`
  both return 200 with the 30 KB Agent Canvas HTML.
- So `{backend}/conversations/{id}` is only a valid link when the backend was
  started as a full stack. Handing that URL out for a `--backend-only` backend
  opens the 503 page. `conversation_link()` probes for an HTML frontend and
  falls back to the hosted UI (`CLOUD_CANVAS_URL`) when there is none.
- `--backend-only` stays the default: it is the lean, reliable choice for the
  agent's execution, and the UI is reachable at app.all-hands.dev once the
  backend is added under **Manage Backends**.

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
- **The project file is named after the island: `<Island>.uefnproject`** (for
  example `myIsland.uefnproject`), not the literal `.uefnproject`. Verified
  across FModel, `undergroundrap/UEFN-TOOLBELT` (`-Filter '*.uefnproject'`),
  `vukefn/verse-auto-imports`, `KirChuvakov/uefn-mcp-server`, `UEFN-Ducky`, and
  UEFN's own log line `Opening project '<name>.uefnproject'`. Matching the
  literal name finds nothing and reports "No UEFN project folder found" for a
  project that is right there — that was a real bug. Match `*.uefnproject`.
- **Documents may be OneDrive-redirected.** With OneDrive "Back up folders" on,
  projects live under `%USERPROFILE%\OneDrive\Documents\Fortnite Projects` (or
  `OneDrive - <Org>\Documents\...`), and UEFN follows the redirect. Discovery
  must check all of these or it reports no project on a normal machine.
- When several projects exist, pick the most recently edited folder, not the
  alphabetically first — otherwise the flags get written to an unrelated island.
- Both writers back up to `<file>.uefn-link.bak` before the first change and are
  idempotent, so pressing Fix twice is harmless.
- **These edits are necessary but not always sufficient.** Epic gates "UEFN MCP
  Toolsets" behind account-level **Beta Access**, and the validation layer
  ignores manual INI overrides without it. Symptom: no Beta Access section in
  Project Settings, and `Python disabled via CVar
  'Engine.Python.IsEnabledByDefault'` in the Output Log. Do not try to defeat
  that gate in code — report it. The app's fix-all says so explicitly rather
  than claiming success.

## The build-loop playbook, verified facts

`docs/BUILD-A-GAME.md` is the executable form of the community workflow
(Epic's UEFN MCP doc + UnrealOps' "Build a Playable Fortnite Game with AI").
The workflow itself is the asset; these are the facts behind it.

- **The agent backend must run on the UEFN machine, whatever UI you drive it
  from.** OpenHands Cloud conversations execute in cloud sandboxes, so
  `127.0.0.1:8000` inside a Cloud conversation is the *sandbox's* loopback, not
  the user's PC. There is no configuration that fixes this. The supported setup
  is a local backend plus the app.all-hands.dev UI as the window onto it
  (Track B1). Do not suggest a Cloud-backend MCP entry as a fix.
- **MCP servers, LLM config, and settings are scoped per backend.** Adding the
  UEFN server to the wrong backend looks like a broken server. `openhands mcp
  add` must run on the UEFN machine.
- **Sessions are Play-in-Client.** There is a separate client process to wait
  on, and new assets need `session cycle --restart`; Verse edits do not.
- **Verse device `@editable` references generally cannot be set over MCP.** The
  agent places the device; a human assigns its device-reference fields in the
  Details panel. The workflow doc states this as a real limit so users do not
  read it as their own mistake. Do not claim the link is "fully" automatic.
- **A written Verse file proves nothing until it compiles.** `verse build` is the
  only correctness signal; UEFN reports errors at build time, not write time.
- **Validation gates the session launch**, and it is where disallowed content
  surfaces (assets that place fine in the editor can be rejected in an island).
  The usual fix is an allowed look-alike in the same spot.
- **Logs are not gameplay.** A clean client log and a broken game coexist. The
  workflow's verification step is playing the island, and bug reports are
  symptoms plus evidence, never a diagnosis.

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
- The status page is polled every 10s, so anything it touches must be cheap.
  _status runs probe_uefn, probe_backend, and llm_status on threads: in sequence
  their timeouts add up and a dead service stalls the whole page. For the same
  reason npm_global_prefix() caches its answer -- spawning "npm prefix -g" costs
  seconds on Windows, where npm is a shell script, and it used to run on every
  poll. A failed lookup is deliberately NOT cached, so an install that happens
  while the app is open is still picked up. Do not add a blocking call to the
  status path without giving it a short timeout and a cache.
