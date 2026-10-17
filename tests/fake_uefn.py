"""A real HTTP server that speaks the MCP subset UEFN exposes.

This is not a mock of the client's dependencies: it is a genuine HTTP server
on a real socket, so the transport, SSE parsing, JSON-RPC handling, pagination,
and discovery logic are all exercised end to end. The only thing simulated is
the editor itself, which cannot run in CI.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional


class FakeUEFNServer:
    """Configurable stand-in for the editor's embedded Unreal MCP server."""

    def __init__(
        self,
        tools: Optional[List[Dict[str, Any]]] = None,
        handler: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        *,
        use_sse: bool = False,
        page_size: int = 0,
        protocol_version: str = "2025-06-18",
        server_name: str = "UnrealEditorFortnite",
    ) -> None:
        self.tools = tools if tools is not None else default_tools()
        self.handler = handler or default_handler
        self.use_sse = use_sse
        self.page_size = page_size
        self.protocol_version = protocol_version
        self.server_name = server_name

        self.requests: List[Dict[str, Any]] = []
        self.calls: List[Dict[str, Any]] = []
        self.session_ids: List[str] = []
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._session_counter = 0

    # -- lifecycle ------------------------------------------------------
    @property
    def port(self) -> int:
        assert self._httpd is not None, "server is not running"
        return self._httpd.server_address[1]

    def start(self) -> "FakeUEFNServer":
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # silence test output
                return

            def do_POST(self) -> None:  # noqa: N802 - stdlib naming
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8") if length else "{}"
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    self.send_error(400, "bad json")
                    return
                server.requests.append(message)
                if message.get("method") == "tools/call":
                    server.calls.append(message)

                if "id" not in message:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                response = server._respond(message)
                body = json.dumps(response).encode("utf-8")

                headers = {"Content-Type": "application/json"}
                if message.get("method") == "initialize":
                    server._session_counter += 1
                    sid = f"session-{server._session_counter}"
                    server.session_ids.append(sid)
                    headers["Mcp-Session-Id"] = sid

                if server.use_sse:
                    sse_body = f"event: message\ndata: {json.dumps(response)}\n\n".encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(sse_body)))
                    for key, value in headers.items():
                        if key != "Content-Type":
                            self.send_header(key, value)
                    self.end_headers()
                    self.wfile.write(sse_body)
                    return

                self.send_response(200)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                payload = {"jsonrpc": "2.0", "method": "notifications/message",
                           "params": {"level": "info", "data": "editor ready"}}
                body = f"event: message\ndata: {json.dumps(payload)}\n\n".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._httpd = None
        self._thread = None

    def __enter__(self) -> "FakeUEFNServer":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # -- protocol -------------------------------------------------------
    def _respond(self, message: Dict[str, Any]) -> Dict[str, Any]:
        method = message.get("method", "")
        params = message.get("params") or {}
        rid = message.get("id")

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": self.server_name, "version": "42.40"},
                },
            }

        if method == "tools/list":
            cursor = params.get("cursor")
            start = int(cursor) if cursor else 0
            if self.page_size:
                page = self.tools[start:start + self.page_size]
                nxt = start + self.page_size
                result: Dict[str, Any] = {"tools": page}
                if nxt < len(self.tools):
                    result["nextCursor"] = str(nxt)
            else:
                result = {"tools": self.tools}
            return {"jsonrpc": "2.0", "id": rid, "result": result}

        if method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments") or {}
            try:
                value = self.handler(name, args)
            except Exception as exc:  # surfaced as a JSON-RPC error, like the editor
                return {"jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32603, "message": str(exc)}}
            if isinstance(value, dict) and value.get("__error__"):
                return {"jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32000, "message": value["__error__"]}}
            return {"jsonrpc": "2.0", "id": rid, "result": _as_content(value)}

        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}


def _as_content(value: Any) -> Any:
    """Wrap a handler value the way MCP tool results are shaped."""
    if isinstance(value, dict) and "content" in value:
        return value
    return {"content": [{"type": "text", "text": json.dumps(value, default=str)}]}


# --- default tool set ------------------------------------------------------
def tool(name: str, description: str = "", required: Optional[List[str]] = None,
         properties: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    schema: Dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema}


def default_tools() -> List[Dict[str, Any]]:
    """A plausible UEFN tool list, including the names the registry expects."""
    return [
        tool("VerseToolset.ListFiles", "List Verse files"),
        tool("VerseToolset.ReadFile", "Read a Verse file", ["path"]),
        tool("VerseToolset.WriteFile", "Write a Verse file", ["path", "content"]),
        tool("VerseToolset.ReplaceInFile", "Replace text in a Verse file",
             ["path", "find", "replace"]),
        tool("VerseToolset.SearchFiles", "Search Verse files", ["query"]),
        tool("VerseToolset.Compile", "Compile Verse"),
        tool("EntityToolset.CreateEntity", "Create an entity", ["class"]),
        tool("EntityToolset.ListEntities", "List entities"),
        tool("EntityToolset.AddComponent", "Add a component",
             ["entity", "componentClass"]),
        tool("EntityToolset.GetComponentTransform", "Get a transform", ["entity"]),
        tool("EntityToolset.SetComponentTransform", "Set a transform", ["entity"]),
        tool("DeviceToolset.ListDeviceAssets", "List devices"),
        tool("DeviceToolset.PlaceDevice", "Place a device", ["asset"]),
        tool("DeviceToolset.GetDeviceProperties", "Get device properties", ["device"]),
        tool("DeviceToolset.SetDeviceProperty", "Set a device property",
             ["device", "property", "value"]),
        tool("ValkyrieToolset.SessionToolset.StartSession", "Start a session"),
        tool("ValkyrieToolset.SessionToolset.StopSession", "Stop a session"),
        tool("ValkyrieToolset.SessionToolset.GetSessionStatus", "Session status"),
        tool("ValkyrieToolset.SessionToolset.PushChanges", "Push changes"),
        tool("ValkyrieToolset.SessionToolset.GetClientLog", "Client log"),
        tool("ValkyrieToolset.ValkyriePythonToolset.ExecutePython", "Run Python", ["code"]),
        tool("ValkyrieToolset.ValkyriePythonToolset.EnablePythonInUEFN", "Enable Python"),
        tool("ProjectToolset.ValidateProject", "Validate the project"),
    ]


def default_handler(name: str, args: Dict[str, Any]) -> Any:
    """Deterministic behaviour for the default tool set."""
    if name == "VerseToolset.ListFiles":
        return {"files": ["Verse/hello.verse", "Verse/game_manager.verse"]}
    if name == "VerseToolset.ReadFile":
        return {"path": args.get("path"), "content": "hello_world():void=\n    Print(\"hi\")\n"}
    if name == "VerseToolset.WriteFile":
        return {"written": args.get("path"), "bytes": len(args.get("content", ""))}
    if name == "VerseToolset.ReplaceInFile":
        return {"replaced": 1}
    if name == "VerseToolset.Compile":
        return {"output": "Verse build succeeded, 0 errors", "errors": 0}
    if name == "EntityToolset.CreateEntity":
        return {"entity": "Entity_1", "class": args.get("class")}
    if name == "EntityToolset.ListEntities":
        return {"entities": [{"name": "Entity_1", "components": ["MeshComponent"]}]}
    if name == "EntityToolset.GetComponentTransform":
        return {"location": [100.0, 200.0, 300.0], "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0]}
    if name == "EntityToolset.SetComponentTransform":
        return {"applied": args}
    if name == "DeviceToolset.ListDeviceAssets":
        return {"devices": ["PlayerSpawner", "TimerDevice"]}
    if name == "DeviceToolset.PlaceDevice":
        return {"device": "PlayerSpawner_1"}
    if name == "DeviceToolset.GetDeviceProperties":
        return {"device": args.get("device"), "properties": {"RespawnTime": 3.0}}
    if name == "DeviceToolset.SetDeviceProperty":
        return {"set": args.get("property"), "value": args.get("value")}
    if name == "ValkyrieToolset.SessionToolset.GetSessionStatus":
        return {"status": "not running"}
    if name == "ValkyrieToolset.SessionToolset.StartSession":
        return {"status": "session started"}
    if name == "ValkyrieToolset.SessionToolset.GetClientLog":
        return {"log": "LogFortnite: session ready"}
    if name == "ValkyrieToolset.ValkyriePythonToolset.ExecutePython":
        return {"output": "LogPython: ran", "executed": True}
    if name == "ProjectToolset.ValidateProject":
        return {"valid": True, "errors": []}
    return {"ok": True, "tool": name}
