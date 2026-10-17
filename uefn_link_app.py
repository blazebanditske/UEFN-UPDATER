"""UEFN Link App: one window that connects UEFN to OpenHands agents.

Double-click it. It runs a small local web app on 127.0.0.1, opens your
browser, and gives you four things:

  1. Live status    -- is UEFN reachable, is an agent backend running?
  2. Uploads        -- drag in textures, videos, docs, concept art, anything.
  3. Instructions   -- type what you want changed, in plain language.
  4. Launch         -- starts an agent that edits the open UEFN project live.

Everything runs on your machine, so the agent's execution environment is the
same machine as the editor. That is the whole trick: UEFN's MCP server is
loopback-only, so the agent has to be local too.

Stdlib only. No install, no dependencies.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_NAME = "UEFN Link"
APP_VERSION = "1.0.0"
DEFAULT_APP_PORT = 8765
UEFN_MCP_URL = os.environ.get("UEFN_MCP_URL", "http://127.0.0.1:8000/mcp")
BACKEND_URL = os.environ.get("LOCAL_AGENT_SERVER_URL", "http://127.0.0.1:8000")
CLOUD_CANVAS_URL = "https://app.all-hands.dev/canvas/conversations"

HOME_DIR = os.path.expanduser("~")
WORKSPACE_DIR = os.environ.get("UEFN_AGENT_WORKSPACE", os.path.join(HOME_DIR, "UEFN-Agent"))
UPLOAD_DIR = os.path.join(WORKSPACE_DIR, "uploads")

# Uploads can be large (videos, level files). Cap generously but not without end.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

UEFN_CONTEXT = """\
You are working on a LIVE Unreal Editor for Fortnite (UEFN) project through its
embedded Unreal MCP server, using the `uefn-link` client available in this
workspace.

Hard rules:
- UEFN's MCP server is loopback-only and unauthenticated. Never bind, tunnel,
  or expose it. Reach it only at 127.0.0.1.
- UEFN MCP is a beta and tool names change. Resolve tools against the live list
  (`uefn-link tools`, `uefn-link capabilities`) instead of assuming names.
- ALWAYS compile Verse after writing it (`uefn-link verse build`). An
  uncompiled write proves nothing.
- Only properties visible in the UEFN interface can be written. Read back with
  `uefn-link device properties`, and run `uefn-link validate` after a batch.
- New assets (textures, meshes) need `uefn-link session cycle --restart` to
  appear. Verse edits only need `uefn-link session cycle`.
- Coordinates: Python-facing toolsets use XYZ, UEFN uses left-up-forward. Use
  the built-in conversion; do not hand-adjust numbers.

Begin by running `uefn-link doctor` and reporting the result in one line.
"""


# --- small helpers ---------------------------------------------------------
def sanitize_filename(name: str) -> str:
    """Reduce an uploaded name to something safe to join onto a directory."""
    base = os.path.basename(name or "").replace("\\", "/").split("/")[-1]
    base = SAFE_NAME.sub("_", base).strip("._-")
    return base[:120] or "upload"


def free_port(preferred: int) -> int:
    """Return `preferred` if free, else an OS-assigned port."""
    for candidate in (preferred, 0):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", candidate))
                return probe.getsockname()[1]
            except OSError:
                continue
    return preferred


def http_json(url, payload=None, headers=None, timeout=8):
    """JSON request helper. Returns (status_or_None, parsed_or_text)."""
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode()
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError:
                return response.status, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:300]
    except Exception as exc:
        return None, str(exc)


def probe_uefn(url: str) -> tuple[bool, str]:
    """Handshake with UEFN's MCP server. True means the editor is live."""
    status, body = http_json(
        url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "uefn-link-app", "version": APP_VERSION},
            },
        },
        {"Accept": "application/json, text/event-stream"},
    )
    if status is None:
        return False, "nothing listening"
    if status != 200:
        return False, f"HTTP {status}"
    return True, "connected"


def backend_key() -> str | None:
    """Read the local backend's session key. Never logged or returned to the UI."""
    if os.environ.get("LOCAL_BACKEND_API_KEY"):
        return os.environ["LOCAL_BACKEND_API_KEY"]
    path = os.environ.get(
        "OH_SESSION_API_KEY_PATH",
        os.path.join(HOME_DIR, ".openhands", "agent-canvas", "api-key.txt"),
    )
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return None
    return None


def probe_backend() -> tuple[bool, str]:
    key = backend_key()
    status, _ = http_json(
        f"{BACKEND_URL}/server_info",
        headers={"X-Session-API-Key": key} if key else {},
        timeout=4,
    )
    if status == 200:
        return True, "running"
    if status == 401:
        return False, "running but key rejected"
    return False, "not running"


def has_openhands_cli() -> bool:
    return shutil.which("openhands") is not None


def staging_summary() -> list[dict]:
    """List staged uploads with sizes."""
    if not os.path.isdir(UPLOAD_DIR):
        return []
    items = []
    for name in sorted(os.listdir(UPLOAD_DIR)):
        path = os.path.join(UPLOAD_DIR, name)
        if os.path.isfile(path):
            items.append({"name": name, "bytes": os.path.getsize(path)})
    return items


# --- server ----------------------------------------------------------------
class AppHandler(BaseHTTPRequestHandler):
    server_version = f"UEFNLinkApp/{APP_VERSION}"

    def log_message(self, *args):
        pass  # keep the console readable

    # -- responses
    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text):
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD_BYTES:
            return None
        remaining, chunks = length, []
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    # -- routes
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._html(build_page())
        elif self.path == "/api/status":
            self._status()
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/upload":
            self._upload()
        elif self.path == "/api/start":
            self._start()
        elif self.path == "/api/register":
            self._register()
        elif self.path == "/api/clear":
            self._clear()
        else:
            self._json(404, {"error": "not found"})

    # -- handlers
    def _status(self):
        uefn_ok, uefn_msg = probe_uefn(UEFN_MCP_URL)
        back_ok, back_msg = probe_backend()
        self._json(200, {
            "uefn": {"ok": uefn_ok, "detail": uefn_msg, "url": UEFN_MCP_URL},
            "backend": {"ok": back_ok, "detail": back_msg, "url": BACKEND_URL},
            "cli": has_openhands_cli(),
            "workspace": WORKSPACE_DIR,
            "uploads": staging_summary(),
            "cloud_url": CLOUD_CANVAS_URL,
        })

    def _upload(self):
        raw_name = self.headers.get("X-Filename", "")
        if not raw_name.strip():
            return self._json(400, {"error": "missing filename"})
        name = sanitize_filename(raw_name)
        data = self._read_body()
        if data is None:
            return self._json(413, {"error": "file too large"})
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        # Keep the original extension but never overwrite silently.
        target = os.path.join(UPLOAD_DIR, name)
        stem, ext = os.path.splitext(name)
        counter = 1
        while os.path.exists(target):
            target = os.path.join(UPLOAD_DIR, f"{stem}-{counter}{ext}")
            counter += 1
        try:
            with open(target, "wb") as handle:
                handle.write(data)
        except OSError as exc:
            return self._json(500, {"error": f"could not save: {exc}"})
        self._json(200, {
            "saved": os.path.basename(target),
            "bytes": len(data),
            "uploads": staging_summary(),
        })

    def _register(self):
        if not has_openhands_cli():
            return self._json(400, {
                "error": "The `openhands` CLI was not found.",
                "fix": "Install it with:  uv tool install openhands --python 3.12",
            })
        proc = subprocess.run(
            ["openhands", "mcp", "add", "uefn", "--transport", "http", UEFN_MCP_URL],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            return self._json(500, {"error": (proc.stderr or proc.stdout).strip()[:400]})
        self._json(200, {"ok": True, "message": "Registered. New conversations only."})

    def _clear(self):
        removed = 0
        if os.path.isdir(UPLOAD_DIR):
            for name in os.listdir(UPLOAD_DIR):
                path = os.path.join(UPLOAD_DIR, name)
                if os.path.isfile(path):
                    try:
                        os.remove(path)
                        removed += 1
                    except OSError:
                        pass
        self._json(200, {"removed": removed, "uploads": staging_summary()})

    def _start(self):
        body = self._read_body() or b"{}"
        try:
            request = json.loads(body.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json(400, {"error": "malformed request"})

        task = (request.get("task") or "").strip()
        if not task:
            return self._json(400, {"error": "Describe what you want changed."})

        uefn_ok, uefn_detail = probe_uefn(UEFN_MCP_URL)
        if not uefn_ok:
            return self._json(400, {
                "error": f"UEFN is not reachable ({uefn_detail}).",
                "fix": "Open UEFN with your project, enable Python Editor Scripting "
                       "and UEFN MCP Toolsets, then restart the editor.",
            })

        back_ok, back_detail = probe_backend()
        if not back_ok:
            return self._json(400, {
                "error": f"The agent backend is not available ({back_detail}).",
                "fix": "Start it on this machine:  agent-canvas --backend-only",
            })

        uploads = [item["name"] for item in staging_summary()]
        context = UEFN_CONTEXT
        if uploads:
            context += (
                "\nThe user uploaded these files into `uploads/` in this workspace. "
                "Inspect them and use them as the source material:\n"
                + "\n".join(f"- uploads/{name}" for name in uploads)
                + "\n"
            )

        payload = {
            "agent": {
                "kind": "Agent",
                "tools": [
                    {"name": "terminal"},
                    {"name": "file_editor"},
                    {"name": "task_tracker"},
                ],
            },
            "workspace": {"kind": "LocalWorkspace", "working_dir": WORKSPACE_DIR},
            "initial_message": {
                "content": [{"text": f"{context}\nTask: {task}"}],
                "run": True,
            },
        }
        status, result = http_json(
            f"{BACKEND_URL}/api/conversations",
            payload,
            {"X-Session-API-Key": backend_key() or ""},
            timeout=60,
        )
        if status not in (200, 201) or not isinstance(result, dict):
            return self._json(500, {"error": f"Could not start (HTTP {status}).", "detail": str(result)[:300]})

        conversation_id = result.get("id") or result.get("conversation_id")
        self._json(200, {
            "ok": True,
            "id": conversation_id,
            "url": f"{BACKEND_URL}/conversations/{conversation_id}",
            "uploads_used": uploads,
        })


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__APP_NAME__</title>
<style>
:root{--bg:#0e1116;--panel:#161b22;--line:#242c37;--fg:#e6edf3;--dim:#8b949e;
--ok:#3fb950;--bad:#f85149;--warn:#d29922;--accent:#4493f8}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:22px;margin:0 0 4px;font-weight:600}
.sub{color:var(--dim);font-size:13px;margin-bottom:24px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:18px;margin-bottom:16px}
.card h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;
color:var(--dim);margin:0 0 14px;font-weight:600}
.row{display:flex;align-items:center;gap:10px;padding:7px 0}
.dot{width:9px;height:9px;border-radius:50%;flex:0 0 9px}
.dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}
.dot.warn{background:var(--warn)}
.label{flex:1}.meta{color:var(--dim);font-size:12.5px}
.drop{border:1.5px dashed var(--line);border-radius:10px;padding:28px 16px;
text-align:center;color:var(--dim);cursor:pointer;transition:.15s}
.drop:hover,.drop.over{border-color:var(--accent);color:var(--fg);
background:rgba(68,147,248,.06)}
.drop b{color:var(--fg)}
textarea{width:100%;min-height:96px;background:#0d1117;color:var(--fg);
border:1px solid var(--line);border-radius:8px;padding:11px;font:inherit;resize:vertical}
textarea:focus{outline:none;border-color:var(--accent)}
button{background:var(--accent);color:#fff;border:0;border-radius:8px;
padding:11px 18px;font:inherit;font-weight:600;cursor:pointer}
button:hover{filter:brightness(1.1)}button:disabled{opacity:.45;cursor:not-allowed}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--fg);
font-weight:500}
.actions{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}
ul.files{list-style:none;padding:0;margin:0}
ul.files li{display:flex;gap:10px;padding:6px 0;border-bottom:1px solid var(--line);
font-size:13.5px}ul.files li:last-child{border:0}
ul.files .sz{color:var(--dim);margin-left:auto;font-size:12.5px}
.msg{padding:11px 13px;border-radius:8px;margin-top:14px;font-size:13.5px;
display:none;white-space:pre-wrap;word-break:break-word}
.msg.show{display:block}
.msg.err{background:rgba(248,81,73,.12);border:1px solid rgba(248,81,73,.35)}
.msg.ok{background:rgba(63,185,80,.12);border:1px solid rgba(63,185,80,.35)}
code{background:#0d1117;padding:2px 6px;border-radius:5px;font-size:12.5px}
a{color:var(--accent)}
.note{color:var(--dim);font-size:12.5px;margin-top:12px}
</style></head><body><div class="wrap">
<h1>__APP_NAME__</h1>
<div class="sub">Connects Unreal Editor for Fortnite to OpenHands agents running on this machine.</div>

<div class="card"><h2>Status</h2>
<div class="row"><span class="dot" id="d-uefn"></span><span class="label">Unreal Editor for Fortnite</span><span class="meta" id="m-uefn">checking…</span></div>
<div class="row"><span class="dot" id="d-back"></span><span class="label">Agent backend (this machine)</span><span class="meta" id="m-back">checking…</span></div>
<div class="actions"><button class="ghost" onclick="refresh()">Re-check</button>
<button class="ghost" id="btn-reg" onclick="register()">Register UEFN with OpenHands</button></div>
<div class="note" id="workspace"></div>
</div>

<div class="card"><h2>Upload anything</h2>
<div class="drop" id="drop"><b>Drop files here</b> or click to choose<br>
<span style="font-size:12.5px">textures · meshes · videos · images · docs · reference links · concept art</span></div>
<input type="file" id="picker" multiple style="display:none">
<ul class="files" id="files"></ul>
<div class="actions"><button class="ghost" onclick="clearAll()">Clear uploads</button></div>
</div>

<div class="card"><h2>What should the agent do?</h2>
<textarea id="task" placeholder="e.g. Replace the rock textures with the uploaded art, keep the same material references, then compile and tell me if it built clean."></textarea>
<div class="actions">
<button id="btn-go" onclick="go()">Send to UEFN</button>
<button class="ghost" onclick="window.open('__BACKEND__','_blank')">Open conversation list</button>
</div>
<div class="msg" id="msg"></div>
<div class="note">The agent edits the project open in UEFN. Watch the editor while it works.</div>
</div>
</div>
<script>
const $=id=>document.getElementById(id);
function setDot(el,state){el.className='dot '+state}
function fmt(b){if(b<1024)return b+' B';if(b<1048576)return (b/1024).toFixed(1)+' KB';
if(b<1073741824)return (b/1048576).toFixed(1)+' MB';return (b/1073741824).toFixed(2)+' GB'}
function show(kind,text){const m=$('msg');m.className='msg show '+kind;m.textContent=text}
function hide(){$('msg').className='msg'}

async function refresh(){
  try{
    const s=await (await fetch('/api/status')).json();
    setDot($('d-uefn'),s.uefn.ok?'ok':'bad');
    $('m-uefn').textContent=s.uefn.ok?'connected':s.uefn.detail;
    setDot($('d-back'),s.backend.ok?'ok':'bad');
    $('m-back').textContent=s.backend.ok?'running':s.backend.detail;
    $('workspace').textContent='Workspace: '+s.workspace;
    renderFiles(s.uploads);
    $('btn-reg').style.display=s.cli?'':'none';
  }catch(e){show('err','Cannot reach the app server. Is it still running?')}
}
function renderFiles(items){
  const ul=$('files');ul.innerHTML='';
  (items||[]).forEach(f=>{
    const li=document.createElement('li');
    li.innerHTML='<span></span><span class="sz">'+fmt(f.bytes)+'</span>';
    li.firstChild.textContent=f.name;ul.appendChild(li);
  });
  if(!items||!items.length){
    ul.innerHTML='<li style="color:var(--dim);border:0">No uploads staged yet.</li>';
  }
}
async function uploadOne(file){
  const r=await fetch('/api/upload',{method:'POST',headers:{'X-Filename':file.name},body:file});
  return r.json();
}
async function handleFiles(list){
  const files=[...list];if(!files.length)return;
  show('ok','Uploading '+files.length+' file(s)…');
  let done=0;
  for(const f of files){await uploadOne(f);done++}
  show('ok','Uploaded '+done+' file(s).');
  refresh();
}
const drop=$('drop'),picker=$('picker');
drop.onclick=()=>picker.click();
picker.onchange=e=>handleFiles(e.target.files);
['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev,e=>{
  e.preventDefault();drop.classList.add('over')}));
['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{
  e.preventDefault();drop.classList.remove('over')}));
drop.addEventListener('drop',e=>handleFiles(e.dataTransfer.files));

async function go(){
  hide();const task=$('task').value.trim();
  if(!task){show('err','Describe what you want changed first.');return}
  $('btn-go').disabled=true;show('ok','Starting the agent…');
  try{
    const r=await fetch('/api/start',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({task})});
    const d=await r.json();
    if(d.ok){
      show('ok','Agent started'+(d.uploads_used.length?' with '+d.uploads_used.length+' upload(s)':'')+
        '.\\nOpening the conversation…');
      setTimeout(()=>window.open(d.url,'_blank'),700);
    }else{
      show('err',d.error+(d.fix?'\\n\\n'+d.fix:'')+(d.detail?'\\n\\n'+d.detail:''));
    }
  }catch(e){show('err','Request failed: '+e)}
  $('btn-go').disabled=false;
}
async function register(){
  hide();
  const r=await fetch('/api/register',{method:'POST'});const d=await r.json();
  if(d.ok){show('ok',d.message)}else{show('err',d.error+(d.fix?'\\n\\n'+d.fix:''))}
}
async function clearAll(){
  const r=await fetch('/api/clear',{method:'POST'});const d=await r.json();
  show('ok','Removed '+d.removed+' file(s).');refresh();
}
refresh();setInterval(refresh,10000);
</script></body></html>
"""


def build_page() -> str:
    return PAGE.replace("__APP_NAME__", APP_NAME).replace("__BACKEND__", BACKEND_URL)


def main(argv=None) -> int:
    global WORKSPACE_DIR, UPLOAD_DIR

    parser = argparse.ArgumentParser(description=f"{APP_NAME} - link UEFN to OpenHands agents.")
    parser.add_argument("--port", type=int, default=DEFAULT_APP_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--workspace", default=WORKSPACE_DIR)
    args = parser.parse_args(argv)

    WORKSPACE_DIR = args.workspace
    UPLOAD_DIR = os.path.join(WORKSPACE_DIR, "uploads")
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    port = free_port(args.port)
    server = ThreadingHTTPServer(("127.0.0.1", port), AppHandler)
    url = f"http://127.0.0.1:{port}"

    uefn_ok, uefn_detail = probe_uefn(UEFN_MCP_URL)
    back_ok, back_detail = probe_backend()

    print(f"{APP_NAME} {APP_VERSION}")
    print(f"  app        {url}")
    print(f"  workspace  {WORKSPACE_DIR}")
    print(f"  uefn       {'connected' if uefn_ok else 'NOT reachable (' + uefn_detail + ')'}")
    print(f"  backend    {'running' if back_ok else 'NOT running (' + back_detail + ')'}")
    print()
    if not uefn_ok:
        print("  UEFN is not reachable. Open UEFN with your project, enable")
        print("  Python Editor Scripting and UEFN MCP Toolsets, then restart it.")
        print()
    print("  Leave this window open. Press Ctrl+C to quit.")
    print()

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
