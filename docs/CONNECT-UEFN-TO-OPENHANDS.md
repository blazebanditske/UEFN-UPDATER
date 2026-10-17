# Connect UEFN to OpenHands: full live setup

This is the step-by-step guide to driving **Unreal Editor for Fortnite (UEFN)**
from an **OpenHands** agent, so it can fix bugs, update textures, integrate
features and assets, and create new mechanics while you watch the editor change.

Read [The one thing that trips everyone up](#the-one-thing-that-trips-everyone-up)
first. It decides which track you follow, and picking the wrong one wastes an
afternoon.

---

## Contents

- [What you get when this is done](#what-you-get-when-this-is-done)
- [The one thing that trips everyone up](#the-one-thing-that-trips-everyone-up)
- [Choose your track](#choose-your-track)
- [Track A - Agent runs on your machine (recommended)](#track-a---agent-runs-on-your-machine-recommended)
- [Track B - Using the app.all-hands.dev web UI](#track-b---using-the-appall-handsdev-web-ui)
- [Track C - Remote sandbox via a tunnel (advanced)](#track-c---remote-sandbox-via-a-tunnel-advanced)
- [Verify the link works](#verify-the-link-works)
- [Everyday workflows](#everyday-workflows)
- [Troubleshooting](#troubleshooting)
- [Limits you cannot engineer away](#limits-you-cannot-engineer-away)

---

## What you get when this is done

The agent can, on your live project:

| Capability | What it means in practice |
| --- | --- |
| Read/write/search Verse | Fix a bug in a mechanic, add a new one |
| Compile Verse | Know whether generated code is actually valid |
| Inspect the level | List every entity and its components |
| Build the level | Create entities, attach components, move things |
| Place and tune devices | Any `@editable` property, plus event bindings |
| Run a play session | Play in Creator, then read the client log |
| Import/update textures | Reimport source art without breaking references |

---

## The one thing that trips everyone up

There are two machines involved, and they have to be the same one.

**1. UEFN's MCP server is local-only and has no password.**

Epic's documentation is explicit: the server binds to loopback
(`127.0.0.1`), has no authentication layer, and "is not safe to expose beyond
the local machine." It rejects requests whose `Origin` header is not local. It
is a debug interface that happens to be very useful.

**2. OpenHands Cloud sandboxes are not your machine.**

`app.all-hands.dev` runs your conversations in a managed cloud sandbox. OpenHands
docs put it plainly: "`localhost` works when the agent server runs on the same
machine as the proxy. If your conversations run in Docker or a remote sandbox,
use an address that environment can reach."

Put those together:

> If the agent runs in the cloud, `127.0.0.1:8000` is the *sandbox's* own
> localhost, not your PC. There is nothing there.

So the goal is **not** "expose UEFN to the internet." It is: **run the agent
backend on the same computer as UEFN, and use the OpenHands UI you like as the
window into it.**

That is a supported setup, it is what `Manage Backends` exists for, and it is
Track A below. It is also how you get a genuinely *live* link: the agent edits
the open project, and the editor updates as it goes.

---

## Choose your track

| | Where the agent runs | Live editing | Setup effort | Use when |
| --- | --- | --- | --- | --- |
| **A** | Your UEFN machine, driven by Agent Canvas UI | Yes | ~15 min | You want this working. Start here. |
| **B** | Your UEFN machine, Agent Canvas already installed | Yes | ~5 min | You already run Agent Canvas locally |
| **C** | A remote sandbox, tunneled to your PC | Yes, fragile | ~40 min | You specifically need cloud sandboxes |

All three end with the agent talking to UEFN. A and B differ only in whether you
are installing Agent Canvas for the first time.

---

## Track A - Agent runs on your machine (recommended)

Everything here happens on the computer running UEFN. Windows is the common case
for Fortnite work; notes for macOS and Linux follow where they differ.

### Step 1. Confirm UEFN opens your project

Launch UEFN and open the island you want to work on. Leave it open. Everything
below assumes it stays open.

### Step 2. Enable the two required settings

In UEFN: **Project Settings** → enable **both**:

- **Python Editor Scripting**
- **UEFN MCP Toolsets** (under **Beta Access**)

Both are mandatory. Enabling only Python gives you an editor with no MCP server;
enabling only the toolset gives you a server with no Python surface.

> If **Beta Access** is missing entirely, or the log shows
> `Python disabled via CVar 'Engine.Python.IsEnabledByDefault'`, your Epic
> account is not in the beta allow-list yet. Request access on the Epic
> Developer Community forums. This is account-level and cannot be worked
> around with ini edits.

### Step 3. Restart UEFN

**This is not optional.** UEFN publishes its MCP toolsets once, at startup.
Changing the settings without restarting leaves the server serving the old set —
or nothing at all.

### Step 4. Note the port

Default is `8000`. If something else on your machine already uses it, set
**Server Port Number** in **Editor Preferences** and remember the value. Every
step below that mentions `8000` uses yours instead.

### Step 5. Install the client and check the link

Install this repo's client on the same machine:

```bash
pip install -e .
uefn-link doctor
```

A healthy link looks like this:

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

**Do not continue until `doctor` says `link ready`.** It names the specific
blocker, and fixing it here is far easier than debugging it through an agent
later. `uefn-link capabilities` shows exactly which operations your build
supports — UEFN exposes only a subset of the Unreal Engine toolsets, and the set
changes between releases.

### Step 6. Install the OpenHands agent backend on this machine

Install `uv` if you do not have it, then install OpenHands:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install openhands --python 3.12

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv tool install openhands --python 3.12
```

Then start Agent Canvas, which runs the backend and the UI together:

```bash
agent-canvas
```

That serves the UI on `http://localhost:3000` with a local backend attached.

> **Running UEFN under Windows but preferring Linux tooling?** Everything the
> agent touches is reached over HTTP on `127.0.0.1`, so Agent Canvas can run in
> WSL on the same machine and still reach UEFN on the Windows side. If
> `127.0.0.1` does not resolve across that boundary, use the host gateway
> address (`host.docker.internal`, or the WSL host IP) — but see the security
> warning in Step 7 first.

### Step 7. Point the agent at the UEFN MCP server

Two ways. Either is fine; the second is what you want if you also want the
`app.all-hands.dev` UI.

**Option 1 — Agent Canvas UI (local):**

1. Open `http://localhost:3000`.
2. Go to **Customize → MCP Servers**.
3. Click **Add custom server**.
4. Fill in:

   | Field | Value |
   | --- | --- |
   | Server Type | `SHTTP` (Streamable HTTP) |
   | Name | `uefn` |
   | URL | `http://127.0.0.1:8000/mcp` |
   | Authentication | None |

5. Click **Test connection**. It should report a tool count.
6. Save.

**Option 2 — CLI, which also works for the cloud UI:**

```bash
openhands mcp add uefn --transport http http://127.0.0.1:8000/mcp
```

This writes `~/.openhands/mcp.json`.

> **Security:** keep the URL as `127.0.0.1`. UEFN's server has **no
> authentication** — anything that can reach the port can edit your project and
> run Python in the editor. Do not bind it to `0.0.0.0`, do not port-forward it,
> and do not put it behind a tunnel unless you have read Track C.

### Step 8. Start a conversation and confirm the tools

In Agent Canvas, start a new conversation and ask:

> List the MCP tools you have available that start with `VerseToolset`.

The agent should enumerate them. If it says it has no such tools, the server was
saved after the conversation started — **MCP servers apply to new conversations
only**. Start a fresh one.

### Step 9. Do something real, and watch the editor

Ask for a change you can see:

> Read `Verse/round_scorer.verse` and tell me what it does.

Then a change:

> Add a Print statement to the OnBegin handler, compile the Verse, and tell me
> if the build succeeded.

Switch to UEFN as it works. The file changes, the build runs, and the output log
shows the result. That round trip is the whole point of this setup.

### Step 10. (Optional) Bridge to the app.all-hands.dev UI

If you want the hosted UI rather than `localhost:3000`:

1. In Agent Canvas, click the **backend switcher** → **Manage Backends** →
   **Add Backend**.
2. Enter the local backend's host URL and its API key.
3. Switch to it.

The backend still runs on your UEFN machine, so the MCP URL stays
`127.0.0.1:8000`. See Track B for the details, since the same rules apply.

---

## Track B - Using the app.all-hands.dev web UI

You have two options, and only one of them is secure. Pick deliberately.

### B1 - Connect the web UI to a backend on your UEFN machine (recommended)

This gives you the hosted interface with a local backend. UEFN stays loopback.

1. On the UEFN machine, start the backend **as a backend only**:

   ```bash
   agent-canvas --backend-only
   ```

   This listens on `127.0.0.1:8000` by default. If that collides with UEFN's
   port, move the backend: `agent-canvas --backend-only --port 8001`.

2. If you want to reach it from another device, expose it through a reverse
   proxy with TLS and a strong API key. Generate the key with:

   ```bash
   openssl rand -base64 32
   ```

   The self-hosting guide covers nginx + Let's Encrypt. Treat this as
   production: the backend can run arbitrary code on the machine hosting UEFN.

3. In the Agent Canvas UI, open **Manage Backends** → **Add Backend**:

   | Field | Value |
   | --- | --- |
   | Host Name | `uefn-machine` |
   | Host | your backend URL, e.g. `https://uefn.yourdomain.com` |
   | Session API key | the key from step 2 |

4. Save, then select it from the backend switcher.

5. Now add the MCP server **to that backend**, not to Cloud. Settings, MCP
   servers, and LLM configuration are all **scoped per backend** — switching
   backends switches all of them. With the `uefn-machine` backend active:

   ```bash
   openhands mcp add uefn --transport http http://127.0.0.1:8000/mcp
   ```

   Run that **on the UEFN machine**. `127.0.0.1` is resolved by the backend
   process, which is local, so it reaches UEFN correctly.

6. Start a new conversation on that backend and confirm as in Track A Step 8.

The result: `app.all-hands.dev` UI, local execution, loopback-only UEFN.

### B2 - Add the MCP server to the Cloud backend directly (will not work)

To be unambiguous, because this is the thing people try first:

Adding `http://127.0.0.1:8000/mcp` under **Customize → MCP Servers** on the
**Cloud** backend **cannot work**. The Cloud sandbox resolves `127.0.0.1` to
itself. There is no UEFN there. You will see a connection error, or a
"0 tools" result, and no amount of retrying changes it.

It is not a misconfiguration you can fix with the right URL. Cloud sandboxes
cannot reach your loopback interface, by design. Use B1, or Track C if you
truly need cloud execution.

---

## Track C - Remote sandbox via a tunnel (advanced)

Only if you specifically need agents running in cloud sandboxes while UEFN is on
your desk.

**Understand what you are doing:** UEFN's MCP server has no authentication and
no rate limiting. Tunneling it exposes a port that can **modify your project and
execute Python inside your editor** to anything that finds the URL. This is
acceptable for a short, attended session over a private tunnel; it is not
acceptable as a standing setup.

If that is genuinely your situation:

1. Complete Track A Steps 1-5. The link must work locally first.
2. Start a tunnel to the UEFN port, keeping the connection private:

   ```bash
   # Example with Cloudflare Tunnel; any private tunnel works
   cloudflared tunnel --url http://127.0.0.1:8000
   ```

3. Note the public URL, e.g. `https://random-words.trycloudflare.com`.
4. On the **Cloud** backend in Agent Canvas, add the custom MCP server:

   | Field | Value |
   | --- | --- |
   | Server Type | `SHTTP` |
   | Name | `uefn` |
   | URL | `https://random-words.trycloudflare.com/mcp` |

5. Test the connection, then start a new conversation.

**Mandatory precautions:**

- Keep the tunnel up only while you are working. Kill it when you stop.
- Prefer a tunnel with access control (Cloudflare Access, an mTLS proxy) over a
  bare public URL.
- Treat the tunnel URL as a password. Anyone with it can edit your island.
- Expect it to be fragile: tunnels drop, and long operations (Verse builds,
  session starts) can exceed idle timeouts.

**Why this is not recommended:** you are trading a supported architecture for an
exposed unauthenticated editor, to gain remote execution you do not need. Track A
gives you the same live editing with none of the exposure.

---

## Verify the link works

Run these in order. Each isolates one layer, so a failure tells you where to look.

```bash
# 1. Is the editor's server up?
uefn-link doctor

# 2. What does this build actually support?
uefn-link capabilities

# 3. Raw tool names, in case a name changed
uefn-link tools

# 4. A real read
uefn-link verse list

# 5. A real write + compile
uefn-link verse write Verse/probe.verse --from-file ./probe.verse --build
```

Then, in the agent:

> Call the UEFN tool that lists Verse files and show me the result.

If 1-5 pass but the agent cannot call tools, the problem is on the OpenHands
side (server not saved, or saved after the conversation started). If 1 fails,
it is the editor side.

---

## Everyday workflows

Once the link is up, these are the patterns worth knowing.

### Fix a bug

```bash
uefn-link verse grep "GetPlayspace"
uefn-link verse read Verse/game_manager.verse
uefn-link verse replace Verse/game_manager.verse --find "..." --replace "..." --build
```

Or just describe the bug to the agent and let it iterate. **Insist that it
compiles.** A Verse write that was not compiled proves nothing — the compiler is
the only reliable correctness signal.

### Update a texture

```bash
uefn-link python reimport-texture /Game/Textures/T_Rock
```

Reimport preserves the asset's identity, so every material referencing it picks
up the new pixels automatically. Prefer this over re-importing as a new asset,
which orphans references.

After adding a **new** asset, the running session must be restarted — see below.

### Create a mechanic

1. Write the Verse (see `uefn_project/Verse/round_scorer.verse` for the shape:
   a `creative_device` subclass with `@editable` fields).
2. Compile it. Fix errors until the build is clean.
3. Place any devices it needs.
4. Wire device events to the mechanic.
5. Start a session and read the log.

### Go live

```bash
uefn-link session start          # launch a Play in Creator session
uefn-link session log            # read what the client printed
uefn-link session cycle          # push Verse changes into a running session
uefn-link session cycle --restart # restart so NEW assets are visible
uefn-link session stop
```

The distinction matters: `cycle` pushes Verse edits, but a texture or mesh added
since the session started is invisible until you `--restart`.

### Tune a device safely

```bash
uefn-link device properties Timer_1
uefn-link device set Timer_1 Duration 60
uefn-link validate               # confirm it did not break validation
```

UEFN can accept a property write and still reject it at validation time, so
read back and validate.

---

## Troubleshooting

Work top to bottom. `uefn-link doctor` automates the first four.

### `nothing is listening` on the port

- UEFN is not open, or the project is not open in it.
- Python Editor Scripting is not enabled for **this project**.
- You have not restarted UEFN since enabling it.
- Wrong port. Check **Editor Preferences → Server Port Number**.

### `Python disabled via CVar 'Engine.Python.IsEnabledByDefault'`

Your Epic account is not in the Python Editor Scripting beta. Request access on
the Epic Developer Community forums. Ini overrides do not help — UEFN validates
at the account level.

### The port answers but `handshake` fails

Something else is listening on that port. Change UEFN's port and update
`UEFN_MCP_PORT`, or stop the other service.

### `0 tools advertised`

Both project settings are not enabled, or UEFN was not restarted after enabling
them. Toolsets are published at startup only.

### `doctor` is happy but the agent has no UEFN tools

- MCP servers apply to **new** conversations. Start a fresh one.
- The server may be saved on a **different backend** than the conversation is
  running on. Settings are per backend. Check the backend switcher.
- The agent may need the URL reachable *from its own environment*. If it runs in
  a sandbox, `127.0.0.1` is the wrong machine — see Track A vs Track C.

### It worked, then stopped

- UEFN was restarted: MCP toolsets republish, but the agent may need a new
  conversation to see them.
- The tunnel dropped (Track C).
- A tool name changed. `uefn-link tools` lists current names; `uefn-link
  capabilities` shows what still resolves.

### Tool calls hang or the editor hitches

Epic lists editor hitching and hangs during MCP calls as a known issue. Long
operations legitimately take minutes — this client waits up to 10 minutes for a
session start or Verse build rather than failing early. Do not issue overlapping
tool calls; the editor serializes them on the game thread.

### Coordinates come out wrong

The Python-facing toolsets report XYZ while UEFN uses left-up-forward; Epic lists
this as a known issue. Use the built-in conversion (`--luf` on the CLI,
`luf=True` in the library) instead of adjusting numbers by hand.

---

## Limits you cannot engineer away

These are properties of UEFN, not of this client. Knowing them saves confusion.

- **The MCP toolset is a beta.** Epic changes the API between releases. This
  client resolves operations against the editor's *live* tool list rather than
  hardcoding names, so renames usually keep working — but verify with
  `uefn-link capabilities` after a UEFN update.
- **Play sessions are Play in Creator, not Play in Editor.** There is a separate
  client process, so starting a session means waiting on a client.
- **New assets need a session restart.** Pushing changes delivers Verse edits
  only.
- **Only UI-visible properties are allow-listed.** Writes to anything else can
  appear to succeed and then fail validation.
- **Some actions have no scripting surface.** Launch Session and the toolbar
  actions are clicked by a human. Epic exposes no tool for them.
- **The editor's server is loopback-only and unauthenticated.** Do not expose it
  beyond your machine without the precautions in Track C.
- **Some steps are yours to do.** Enabling the two project settings, approving
  the agent, and opening the project when UEFN stops at the hub are manual by
  design.

---

## Quick reference

```
UEFN MCP endpoint          http://127.0.0.1:8000/mcp
Both settings required     Python Editor Scripting + UEFN MCP Toolsets
Restart required           Yes, after enabling either setting
Client check               uefn-link doctor
Agent Canvas (local)       agent-canvas
Agent Canvas (backend)     agent-canvas --backend-only [--port 8001]
Add MCP (UI)               Customize > MCP Servers > Add custom server
Add MCP (CLI)              openhands mcp add uefn --transport http http://127.0.0.1:8000/mcp
MCP scope                  Per backend; new conversations only
app.all-hands.dev + UEFN   Only via a local backend (Track B1) or a tunnel (Track C)
```

## References

- [UEFN MCP](https://dev.epicgames.com/documentation/fortnite/uefn-mcp) - Epic's documentation for the server this connects to.
- [Python Tools in UEFN](https://dev.epicgames.com/documentation/fortnite/python-tools-in-uefn) - asset pipeline rules and validation caveats.
- [Unreal MCP in Unreal Editor](https://dev.epicgames.com/documentation/unreal-engine/unreal-mcp-in-unreal-editor) - shared architecture and limitations.
- [OpenHands MCP settings](https://docs.openhands.dev/openhands/usage/settings/mcp-settings) - adding custom servers in Agent Canvas.
- [OpenHands backends](https://docs.openhands.dev/openhands/usage/agent-canvas/backends) - connecting a frontend to a backend.
- [OpenHands CLI MCP servers](https://docs.openhands.dev/openhands/usage/cli/mcp-servers) - the `openhands mcp add` command.
- [OpenHands self-hosting](https://github.com/OpenHands/OpenHands/blob/main/docs/SELF_HOSTING.md) - running a backend on a VM or always-on machine.
