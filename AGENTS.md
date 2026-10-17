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
