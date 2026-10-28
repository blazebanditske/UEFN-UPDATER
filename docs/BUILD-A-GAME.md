# Build a playable Fortnite game with an OpenHands agent

This is the practical playbook for building a real, playable island by talking to
an agent, driven from **app.all-hands.dev** (OpenHands Cloud) as the window onto
an agent backend running on your UEFN machine.

It follows the workflow Epic and the community use, in the order that actually
works, and it names the commands to run at each step. The theory is in
[CONNECT-UEFN-TO-OPENHANDS.md](CONNECT-UEFN-TO-OPENHANDS.md); this document is
the "what do I type" version.

---

## The one thing to understand before you start

UEFN's MCP server is **loopback-only and has no password**. OpenHands Cloud
conversations run in **cloud sandboxes**, not on your PC. A cloud sandbox that
asks for `127.0.0.1:8000` is asking *itself*, where UEFN does not exist.

So there are two machines in play, and the rule is:

> **The agent backend must run on the same computer as UEFN.**

You then drive that backend from the app.all-hands.dev UI. That is a supported
setup, and it is what you want: hosted interface, local execution, live editor.

| | Backend | Where the agent runs | Reaches UEFN | Use it? |
| --- | --- | --- | --- | --- |
| **B1** | Local backend on your UEFN machine | Your PC | Yes | **Yes — this is the way** |
| B2 | Cloud backend | Cloud sandbox | No | No — cannot work |
| C | Cloud + tunnel | Cloud sandbox | Yes, via exposed port | Only if you truly need it |

The reason B2 cannot work is not a wrong URL. Cloud sandboxes cannot reach your
loopback interface, by design. If you see "0 tools" or a connection error on the
Cloud backend, that is this, and retrying will not change it.

Track C tunnels your editor's port to the internet. That port can **edit your
island and run Python inside your editor**, with no authentication. It is
acceptable for a short attended session over a private tunnel, and it is not
acceptable as a standing setup. Prefer B1.

---

## Setup (once)

Do all of this **on the machine running UEFN**.

### 1. Create the project

Launch UEFN, create a new project from **Island Templates → Blank**, brand
**Fortnite**, and leave **Lore** checked. Lore is UEFN's revision control — it is
your undo history, and you will want it before the agent starts changing things.

Leave the editor open for everything that follows.

### 2. Enable the two Beta settings

**Project → Project Settings → Beta Access**, then check **both**:

- **Python Editor Scripting**
- **UEFN MCP Toolsets**

Both are required. UEFN MCP does nothing without them, and the settings only take
effect after a restart.

### 3. Restart UEFN

The server starts and stops with the editor. Close and reopen, then reopen your
project. This step is the most common cause of "nothing is listening".

### 4. Confirm the editor's server is up

```bash
curl -s http://127.0.0.1:8000/mcp -X POST \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}'
```

Any JSON reply means the server is alive. Connection refused means the editor is
closed, the project is not open, or step 2 did not take.

Port 8000 is the default. If you changed **Server Port Number** in Editor
Preferences, use that instead.

### 5. Start the agent backend on this machine

The backend is what actually talks to UEFN, so it must be local:

```bash
agent-canvas --backend-only --port 3010
```

**Always pass `--port`.** agent-canvas defaults to `127.0.0.1:8000`, which is
UEFN's own port, so a portless start collides with the editor.

If you would rather let the app do it, launch **UEFN Link** and press **Start
backend**. It picks a free port and, if you already run agent-canvas, adopts it
instead of starting a doomed second copy.

### 6. Register UEFN with the backend

Settings, MCP servers, and LLM config are **scoped per backend**. Run this on the
UEFN machine, against the local backend:

```bash
openhands mcp add uefn --transport http http://127.0.0.1:8000/mcp
```

Or press **Register UEFN with OpenHands** in UEFN Link.

### 7. Point app.all-hands.dev at your local backend

1. Open Agent Canvas → **backend switcher** → **Manage Backends** → **Add Backend**.
2. Enter your backend's host URL and its session API key.
3. Switch to it.

Now the hosted UI is driving the agent that runs next to UEFN.

If you need to reach that backend from another device, put TLS and a strong key
in front of it first:

```bash
openssl rand -base64 32
```

Treat it as production: this backend can run arbitrary code on the machine
hosting UEFN.

### 8. Verify before you build anything

```bash
python tools/uefn_openhands_bridge.py check
```

```
UEFN MCP endpoint
  reachable yes
  handshake ok

Agent backend (must run on THIS machine)
  reachable yes

  verdict   an agent on this backend CAN edit your project
```

Do not proceed until both lines say yes. Then, in a new conversation, confirm
the agent can see the tools by asking it to **list its UEFN tools**. An agent
that cannot list them will invent them.

---

## The build loop

Six steps, repeated. The first two are reads — resist skipping them.

### Step 1 — Look at the level before touching it

> Using the UEFN MCP tools, review the current scene in my project and describe
> what is in it.

A blank island should report a tiled floor and spawn pads. If the agent
describes something else, you are pointed at the wrong project — stop and fix
that first.

### Step 2 — Find the assets before placing any

> I want to build a farm level. Identify all farm-related assets available in
> this project. Do not place anything yet.

Say **"do not place anything yet"**. Two reasons this matters:

- **Asset search is by name.** A perfectly good asset with an unrelated name will
  not be in the list. Browse the Content Drawer yourself for anything the agent
  misses.
- **Being in the editor is not the same as being allowed in an island.** Assets
  that place fine can fail validation at launch. You will meet this again in
  Step 6.

### Step 3 — Build, then make it review its own work

One-shot prompts produce scattered results. The fix is a second pass:

> Build a farmstead-style farm layout.

then

> Review the layout you just built and fix the spacing so the farm feels real.

The agent placing buildings and then *looking at what it placed* is the
difference between a pile of props and a place.

### Step 4 — Probe scale with one asset

> Place a single oak tree near the yard so I can check its scale.

Assets do not arrive at the size you expect. One tree, one tile, one fence
piece — check it, then commit to the row. This has saved more rework than any
other habit here.

### Step 5 — Add the gameplay in plain English

Verse is where "create new mechanics" and "fix bugs" actually land, and you do
not need to write it. Be specific about **the rules**, because the rules are the
specification:

> Add game logic in Verse. Chickens have escaped their pen. The player collects a
> chicken by running up to it; it then follows the player. When the player brings
> a chicken into the pen, it stops following and the player scores 1 point. The
> score renders on screen starting at zero. A celebration effect plays on
> delivery.

Notice what that pins down: how collection works, what counts as delivery, where
the score starts, and what happens on scoring. Vague rules produce code that
compiles and plays wrong.

Then **insist on a build**:

```bash
uefn-link verse build
```

A Verse file that was written but not compiled proves nothing. The compiler is
the only reliable correctness signal, and UEFN reports errors there rather than
at write time.

**Expect a manual step here.** A Verse device with `@editable` fields —
references to other devices — usually cannot be wired over MCP. The agent places
the device; you assign its fields in the Details panel. This is a real limit, not
a mistake you made. When it happens, ask the agent **which devices each field
expects** before you fill them in.

### Step 6 — Validate, then launch

```bash
uefn-link validate
uefn-link session start
```

Validation is the gate before a session can launch, and it is where disallowed
content surfaces. When it fails:

> The session will not launch. Here are the validation errors: <paste>. Review
> and fix all validation issues so I can launch.

The usual fix is swapping a disallowed asset for an allowed look-alike in the
same spot, which preserves the layout. When the session finally loads, UEFN
cooks assets first, so give it time — this is the slow part.

### Step 7 — Play it, then report bugs as evidence

**Logs are not gameplay.** A clean log and a broken game are entirely compatible.

Play the island yourself, then describe symptoms precisely:

> Two issues. First, chickens do not follow the player after being tamed — the
> player can carry one to the pen, but it will not follow. Second, if I tame two
> chickens and deliver only one, the counter goes up by two. Evaluate why these
> occur, plan a fix for both, implement the fixes, then test end-to-end.

Three things make that prompt work:

- **Symptom, not diagnosis.** You describe what happened; the agent finds the
  cause.
- **Both bugs together**, so the fixes do not fight each other.
- **"Plan a fix, then implement, then test"** — the same plan-first habit Epic
  recommends for multi-step requests.

Then play it again. The video's double-count bug and follow-distance bug were
both fixed this way, and both were confirmed in the running game rather than in
the logs.

---

## Everyday commands

```bash
# See what this editor build actually supports
uefn-link doctor
uefn-link tools --grep verse

# Fix a bug
uefn-link verse grep "GetPlayspace"
uefn-link verse read Verse/game_manager.verse
uefn-link verse replace Verse/game_manager.verse --find "..." --replace "..." --build

# Update a texture (keeps the asset's identity, so references survive)
uefn-link python reimport-texture /Game/Textures/T_Rock

# Live session
uefn-link session start
uefn-link session log              # runtime errors and prints
uefn-link session cycle            # push Verse edits into a running session
uefn-link session cycle --restart  # restart so NEW assets are visible
uefn-link session stop

# Tune a device safely
uefn-link device properties Timer_1
uefn-link device set Timer_1 Duration 60
uefn-link validate
```

`cycle` and `cycle --restart` are not interchangeable: pushing delivers Verse
edits, but a texture or mesh added since the session started is invisible until
the session restarts.

---

## Habits that decide whether this goes well

1. **Read before you write.** Describe the level and list the assets before
   placing anything.
2. **Probe one asset before placing a row.**
3. **Ask for a plan on anything multi-step**, and read it before approving.
4. **Insist on a compile after every Verse change.**
5. **Verify in the game, not in the log.**
6. **Keep changes small enough to check.** A large batch is hard to verify and
   easy to get subtly wrong.
7. **Name things specifically.** "the Timer device" beats "that thing I added".

---

## Honest limits

These are UEFN's, not the agent's. Knowing them up front saves confusion.

- **Some device wiring is manual.** `@editable` device references generally
  cannot be set over MCP; you assign them in the Details panel.
- **Coordinates disagree between layers.** The Python toolsets use XYZ; UEFN uses
  left-up-forward. This client converts explicitly (`--luf`) rather than
  guessing. Epic lists it as a known issue.
- **Tool calls can hitch the editor.** Epic lists editor hangs during MCP calls
  as a known issue. Long operations get long timeouts rather than failing early.
- **New assets need a session restart.** See `cycle --restart` above.
- **Only UI-visible properties are allow-listed.** A write that is not exposed in
  the UEFN interface can appear to succeed and then fail validation. Read back
  and validate.
- **Some actions have no scripting surface.** Where Epic provides a tool, this
  client calls it. Where it does not, there is nothing to call.
- **The Cloud backend cannot see UEFN.** By design. Use B1.

---

## References

- [UEFN MCP](https://dev.epicgames.com/documentation/fortnite/uefn-mcp) — Epic's documentation for the embedded server.
- [Python Tools in UEFN](https://dev.epicgames.com/documentation/fortnite/python-tools-in-uefn) — asset pipeline rules and validation caveats.
- [OpenHands Cloud](https://docs.openhands.dev/openhands/usage/cloud/openhands-cloud) — the hosted service.
- [Agent Canvas backends](https://docs.openhands.dev/openhands/usage/agent-canvas/backends) — local vs remote vs cloud.
- [MCP settings](https://docs.openhands.dev/openhands/usage/settings/mcp-settings) — how OpenHands registers MCP servers, and per-backend scoping.
