# UEFN Link - the app

One file you double-click. It connects Unreal Editor for Fortnite to OpenHands
agents so you can upload assets, describe what you want, and watch the editor
change.

```
┌──────────────────────────────────────────────┐
│  UEFN Link                                   │
├──────────────────────────────────────────────┤
│  ● Unreal Editor for Fortnite    connected   │
│  ● Agent backend (this machine)  running     │
├──────────────────────────────────────────────┤
│  Drop files here, or click to choose         │
│    textures · meshes · videos · concept art  │
├──────────────────────────────────────────────┤
│  [ what should the agent do? ]               │
│  [        Send to UEFN        ]              │
└──────────────────────────────────────────────┘
```

---

## Get the app

### Option 1 - Download a built binary

Prebuilt, smoke-tested binaries are attached to the
[latest release](https://github.com/blazebanditske/UEFN-UPDATER/releases/latest):

| Platform | File | Size |
| --- | --- | --- |
| Windows | `UEFN-Link.exe` | ~8.6 MB |
| macOS | `UEFN-Link-macos` | ~7.6 MB |
| Linux | `UEFN-Link-linux` | ~19.7 MB |

Each is built on a real runner for that platform, and the build fails unless the
binary starts and serves its UI. No Python needed on the target machine.

> Windows SmartScreen may warn about an unknown publisher, because the binaries
> are unsigned. **More info → Run anyway** if you are comfortable with that. To
> avoid the warning entirely, build it yourself (Option 2).

### Option 2 - Build it (one command)

On the machine that runs UEFN:

```bash
python build_app.py
```

That produces a single file in `dist/`:

| Platform | Output | Double-click? |
| --- | --- | --- |
| Windows | `UEFN Link.exe` | Yes |
| macOS | `UEFN Link.app` | Yes |
| Linux | `UEFN Link` | Yes (needs `chmod +x`) |

> PyInstaller cannot cross-compile. Run `build_app.py` on the platform you want
> to ship for — build on Windows to get the `.exe`. The included GitHub Actions
> workflow does this for all three platforms automatically.

### Option 3 - Run it as a script

No build step, if you already have Python 3.9+:

```bash
python uefn_link_app.py
```

Opens your browser automatically. Stdlib only, no dependencies.

---

## Use it

The app is three steps, and the first one is a single button.

### 1. Get three dots green

The window shows three status rows. Press **Fix everything** and the app does
everything it can on its own, then tells you what is left.

What **Fix everything** does, in order:

| Step | What it does |
| --- | --- |
| install backend | Runs `npm install -g @openhands/agent-canvas` if it is missing |
| start backend | Starts `agent-canvas --backend-only --port 3010` and waits for it to answer |
| UEFN auto-start | Writes `bAutoStartServer=True` into UEFN's editor preferences |
| UEFN project flags | Turns on Python Editor Scripting and UEFN MCP Toolsets in `.uefnproject` |
| UEFN connection | Checks the editor is actually answering |
| model credential | Reports whether a model key exists |

Each row also has its own **Fix** button if you would rather do one thing at a
time.

Two of those steps touch files on disk. Both are additive and both keep a
backup the first time they write:

- UEFN's editor preferences
  (`%LOCALAPPDATA%\UnrealEditorFortnite\Saved\Config\WindowsEditor\EditorPerProjectUserSettings.ini`)
  gets a backup at `….ini.uefn-link.bak`. Only the three
  `ModelContextProtocol` keys are touched; the rest of the file is preserved
  byte for byte.
- Your project's `.uefnproject` gets a backup at `.uefnproject.uefn-link.bak`.
  Only the two beta flags are touched; everything else in the JSON is
  preserved.

**Unreal Editor for Fortnite** — the editor is open, with your project, and its
MCP server is answering.

The app can turn the two required settings on for you, but it cannot restart
the editor. After the first fix:

1. **Restart UEFN**, with your project open. Toolsets publish at startup only.
2. Click **Re-check**.

If it is still red, do it by hand: in UEFN open **Project Settings** and enable
**Python Editor Scripting** *and* **UEFN MCP Toolsets** (under **Beta Access**).
Both are required.

> **If Beta Access is missing entirely**, that is not something the app can fix.
> Epic gates this at the account level, and the validation layer ignores manual
> file edits without it. See [Limits](#limits-the-app-cannot-fix) below.

**Agent backend (this machine)** — the thing that actually runs the agent.

The app starts this for you when it launches, and **Fix everything** installs
and starts it if needed. To run it by hand:

```bash
agent-canvas --backend-only --port 3010
```

Install it first if needed: `npm install -g @openhands/agent-canvas`. On
Windows use Command Prompt, not PowerShell: PowerShell blocks npm's script by
default with "running scripts is disabled on this system".

Always pass `--port`. Without it agent-canvas defaults to port 8000, which is
where UEFN's own MCP server lives, and the two collide. The backend listens on
port 3010 by default.

> **One backend per machine.** agent-canvas also binds internal ports (18000
> and 18001) that a running instance already holds, so a second copy fails no
> matter which ingress port it is given. The app checks first and reuses a
> healthy backend instead of starting a second one. If some other process holds
> port 3010, the app says so rather than guessing.

**Model** — the account the agent bills and authenticates against.

If it is red, click **Set key** and paste one. The form has presets for
OpenHands Cloud, Anthropic, and OpenAI, and writes to
`~/.openhands/agent_settings.json` — the same file `openhands login` uses, so
the two paths agree. The file is written `chmod 600`, and the app never shows
the key again or sends it anywhere except the provider you chose.

This is the one step the app cannot do for you. A model key is account-bound,
and no program can conjure one. You have two options:

```bash
openhands login
```

That opens your browser, signs you in to your personal OpenHands Cloud account,
and saves the model, proxy URL, and minted key. The app reads that file.

> **Note — this needs credits on the account.** `openhands login` mints the key
> from `GET /api/keys/llm/byor`, which returns **HTTP 402** until the account
> has credits: *"BYOR key export is not enabled. Purchase credits to enable
> this feature."* On a fresh personal account (`credits: 0.00`) the login will
> succeed but no key is issued. Either add credits to the account, or use your
> own model key — see below.

#### Using your own model key instead

Use **Set key** in the app, or set these before launching:

| Variable | Example |
| --- | --- |
| `LLM_MODEL` | `anthropic/claude-sonnet-4-5-20250929` |
| `LLM_API_KEY` | your provider key |
| `LLM_BASE_URL` | `https://api.anthropic.com` (or your proxy) |

Environment variables win over `agent_settings.json`. With a key set this way
the third dot goes green and no Cloud account is involved.

All three dots green means the agent can reach your editor. That is the entire
prerequisite.

<a id="limits-the-app-cannot-fix"></a>
#### Limits the app cannot fix

Two things stay manual, and both are account-bound rather than technical:

1. **The model key.** Described above. Nothing can generate one for you.
2. **Epic's account-level beta access.** If UEFN's Project Settings has no
   **Beta Access** section at all, and the Output Log shows
   `Python disabled via CVar 'Engine.Python.IsEnabledByDefault'`, the app's
   file edits will be ignored. That gate is enforced by Epic, not by the files
   the app writes. You need to be allow-listed, or on a UEFN build where the
   toolset is generally available.

The app reports both honestly rather than claiming success.

### 2. Drop in whatever you have

Drag files onto the drop zone — or click to browse. There is no type restriction
and a 2 GB per-file limit. It is designed for:

- textures (`.png`, `.tga`, `.exr`), meshes, and other source art
- videos and reference footage
- images, concept art, mood boards
- design docs, specs, spreadsheets
- anything you want the agent to look at

Files are staged in `~/UEFN-Agent/uploads/` and the agent is told about them by
name when you launch. Nothing is uploaded to the internet; it stays on your
machine.

### 3. Say what you want, then click Send

Type an instruction in plain language:

> Replace the rock textures with the uploaded art, keep the same material
> references, compile the Verse, and tell me if it built clean.

The agent starts in a workspace containing your uploads, begins with
`uefn-link doctor`, and edits the project that is open in UEFN. Watch the editor
while it works.

The app opens the conversation automatically when it starts.

---

## Where `app.all-hands.dev` fits

Short version: **the agent runs on your machine, and you can still use the
hosted UI as your window into it.**

Why it cannot be the other way around — this is worth understanding once:

UEFN's MCP server binds to loopback and has **no authentication**. Epic states
it is unsafe to expose beyond the local machine. OpenHands Cloud runs
conversations in remote sandboxes, where `127.0.0.1` means *the sandbox*, not
your PC. So a cloud conversation cannot reach your editor, and the only way to
make it could would be to publish an unauthenticated port that lets anyone
edit your island and run Python in your editor.

So the app puts the agent where the editor is. Then, if you want the hosted
conversations page:

1. Run the backend on your UEFN machine (the app already needs this).
2. In the Agent Canvas UI, open **Manage Backends → Add Backend**.
3. Enter that backend's URL and its session API key.
4. Switch to it.

You now browse conversations at `app.all-hands.dev/canvas/conversations` while
the agent executes locally, next to UEFN. Same interface, correct execution
location.

### Your personal Cloud account as the backend

The third status dot is your OpenHands Cloud account being used for one thing
only: **the model**. The agent still executes locally. This is what
`openhands login` sets up — it signs in to your personal account and stores the
model, proxy URL, and key, which the app then sends with each conversation.

So "OpenHands Cloud - personal" and "this machine" are not competing options.
The account supplies the model; the machine supplies the execution and the
access to UEFN. The app needs both, which is why it shows both as separate
dots.

The one prerequisite is credits on the account — see the note in step 1.
Without them, use your own model key instead; everything else is identical.

### Uploading through the hosted UI instead

If you would rather upload through the web UI than drag into the app, the files
have to land somewhere the **agent** can read — which is the UEFN machine. Point
the hosted conversation at a repository, or use the app's uploader and let it
stage the files for you. Either way, the constraint is the same: the agent's
filesystem must be the one with UEFN on it.

### Large files

The 2 GB cap is per file. Very large uploads are limited by disk on the UEFN
machine, not by the app. For anything genuinely huge (level files, video
masters), point the agent at a local path instead of uploading — it can read
your disk directly, because it is running there.

---

## What the app does under the hood

It runs a small web server on `127.0.0.1` and opens your browser to it. Nothing
is exposed to the network.

| Route | Purpose |
| --- | --- |
| `GET /` | the window |
| `GET /api/status` | probes UEFN, the backend, and the model config |
| `POST /api/fix-all` | runs every automatic repair in order and reports each step |
| `POST /api/backend/install` | `npm install -g @openhands/agent-canvas` |
| `POST /api/backend/start` | starts `agent-canvas --backend-only --port 3010` and waits for it |
| `POST /api/uefn/fix` | writes UEFN's auto-start setting and the project beta flags |
| `POST /api/llm/save` | saves a model key to `agent_settings.json` |
| `POST /api/upload` | saves a file to `~/UEFN-Agent/uploads/` |
| `POST /api/start` | starts a conversation with your instruction |
| `POST /api/register` | runs `openhands mcp add uefn` |
| `POST /api/clear` | empties the uploads folder |

`POST /api/start` sends the model configuration with the conversation
(`agent.llm`). The backend rejects a conversation without it — HTTP 422 — so the
app resolves the key first and refuses to start if there is none.

The agent is started with a system prompt that encodes the rules that matter:
compile Verse after writing it, only write UI-visible properties, restart the
session for new assets, convert coordinates, and never expose the MCP port.

### The settings it writes

Two files, both additive, both backed up on first write:

| File | Keys written |
| --- | --- |
| `EditorPerProjectUserSettings.ini` | `bAutoStartServer`, `ServerPortNumber`, `ServerUrlPath` under `[/Script/ModelContextProtocol.ModelContextProtocolSettings]` |
| `.uefnproject` | `dataSets.experimental.pythonExperimental.bEnablePythonForProject`, `dataSets.experimental.toolsets.bEnableToolsetsForProject` |

The INI writer replaces only the exact keys it names, and only inside the named
section, so a same-named key in another section is left alone.

---

## Security

- Everything binds to `127.0.0.1`. Nothing is reachable from other machines.
- Uploaded files stay on your disk in `~/UEFN-Agent/uploads/`.
- The backend's session key is read from disk and sent only to the local
  backend. It is never logged or displayed.
- Filenames are sanitized, so an upload cannot write outside the uploads folder.
- The model key is written to `agent_settings.json` with `chmod 600`. It is
  never returned to the browser, never logged, and only ever sent to the
  provider you selected.
- The app writes to exactly two config files, both listed above, both with a
  backup. It does not edit anything else, and it does not install Node or
  Python — if those are missing it tells you where to get them.
- The agent has the tools you would expect it to have (`terminal`,
  `file_editor`, `task_tracker`) and it runs on your machine with your
  permissions. Treat it like running any local script.

---

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `UEFN: nothing listening` | UEFN closed, project not open, or the two project settings are off. Enable both and **restart UEFN**. |
| `UEFN: HTTP 403` | Something else is on port 8000. Change UEFN's port in **Editor Preferences**, then set `UEFN_MCP_URL`. |
| `backend: not running` | The app starts it automatically. If that failed, run `agent-canvas --backend-only --port 3010`, or press **Start backend**. |
| `backend: key rejected` | Stale key. Restart the backend; the app re-reads it. |
| `Model dot red` | No LLM key. Run `openhands login`, or set `LLM_MODEL` and `LLM_API_KEY`. |
| `login` succeeds but no key | The account has no credits, so BYOR key export is disabled (HTTP 402). Add credits, or use your own `LLM_API_KEY`. |
| Agent starts then errors immediately | Almost always the model key. Open the conversation and read the first error event. |
| "Register" button missing | The `openhands` CLI is not on PATH. `uv tool install openhands --python 3.12`. |
| Agent has no UEFN tools | MCP servers apply to **new** conversations. Start another. Also check the server is registered on the *same backend* the conversation uses. |
| Agent edits nothing | The project open in UEFN must be the one you mean. Check with `uefn-link doctor`. |
| Browser did not open | Go to the URL printed in the terminal window. |
| App says port busy | It picks a free port automatically. Check the terminal for the real URL. |

---

## Build notes

- **Stdlib only.** No `requirements.txt`, because there is nothing to install.
- **No `tkinter`.** The UI is a local web page, which is why the binary is
  small and why drag-and-drop works properly.
- **PyInstaller is build-time only.** It is never needed to run the app.
- Verified: `build_app.py` produces a working binary that detects a live editor
  and serves the UI.

```bash
python build_app.py --clean     # rebuild from scratch
python -m pytest tests/test_app.py -q
```
