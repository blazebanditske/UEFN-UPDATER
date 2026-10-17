"""MCP session: handshake, tool discovery, and tool invocation.

The client is deliberately discovery-first. Epic adds and renames toolsets
between UEFN releases, so nothing here hardcodes a tool name. Instead
:class:`MCPClient` reads ``tools/list`` and resolves a *logical* request such
as ``("Verse", "compile")`` to whatever the editor currently advertises, using
the candidate lists in :mod:`uefn_link.mcp.registry`.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from uefn_link.errors import DiscoveryError, TransportError
from uefn_link.mcp import jsonrpc
from uefn_link.mcp.transport import HttpTransport

CLIENT_NAME = "uefn-link"
CLIENT_VERSION = "0.1.0"
PROTOCOL_VERSION = "2025-06-18"

# Results are cached only for the lifetime of the object; the editor's tool
# list is static for a running session, and a restart is needed to change it.
_CACHE: Dict[str, Any] = {}


class ToolInfo:
    """One tool advertised by the editor."""

    __slots__ = ("name", "description", "input_schema", "raw")

    def __init__(self, name: str, description: str = "", input_schema: Optional[Dict[str, Any]] = None,
                 raw: Optional[Dict[str, Any]] = None) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema or {}
        self.raw = raw or {}

    @classmethod
    def from_wire(cls, payload: Dict[str, Any]) -> "ToolInfo":
        return cls(
            name=str(payload.get("name", "")),
            description=str(payload.get("description", "") or ""),
            input_schema=payload.get("inputSchema") or {},
            raw=payload,
        )

    def required_args(self) -> List[str]:
        return list(self.input_schema.get("required") or [])

    def properties(self) -> Dict[str, Any]:
        return dict(self.input_schema.get("properties") or {})

    def __repr__(self) -> str:
        return f"ToolInfo({self.name!r})"


class MCPClient:
    """Talks to the editor's MCP server and normalises tool discovery."""

    def __init__(self, transport: Optional[HttpTransport] = None) -> None:
        self.transport = transport or HttpTransport()
        self._initialized = False
        self._server_info: Dict[str, Any] = {}
        self._tools: List[ToolInfo] = []
        self._by_name: Dict[str, ToolInfo] = {}

    # -- properties -----------------------------------------------------
    @property
    def server_info(self) -> Dict[str, Any]:
        return dict(self._server_info)

    @property
    def tools(self) -> List[ToolInfo]:
        return list(self._tools)

    # -- lifecycle ------------------------------------------------------
    def initialize(self) -> Dict[str, Any]:
        """Perform the MCP handshake and send ``notifications/initialized``."""
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    "roots": {"listChanged": False},
                    "sampling": {},
                },
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        )
        if isinstance(result, dict):
            self._server_info = result
        self.transport.post(jsonrpc.notification("notifications/initialized"))
        self._initialized = True
        return self._server_info

    def ensure_initialized(self) -> None:
        if not self._initialized:
            self.initialize()

    # -- discovery ------------------------------------------------------
    def refresh_tools(self) -> List[ToolInfo]:
        """Fetch the full tool list, following pagination cursors."""
        self.ensure_initialized()
        collected: List[ToolInfo] = []
        cursor: Optional[str] = None
        while True:
            params: Dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            result = self._request("tools/list", params or None)
            if not isinstance(result, dict):
                break
            for item in result.get("tools") or []:
                if isinstance(item, dict) and item.get("name"):
                    collected.append(ToolInfo.from_wire(item))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        if not collected:
            raise DiscoveryError(
                "The editor advertised no tools. Confirm that UEFN MCP Toolsets "
                "is enabled in Project Settings > Beta Access."
            )
        self._tools = collected
        self._by_name = {t.name: t for t in collected}
        return list(self._tools)

    def tool_names(self) -> List[str]:
        if not self._tools:
            self.refresh_tools()
        return sorted(self._by_name)

    def find(self, name: str) -> Optional[ToolInfo]:
        if not self._tools:
            self.refresh_tools()
        if name in self._by_name:
            return self._by_name[name]
        lowered = name.lower()
        for tool_name, info in self._by_name.items():
            if tool_name.lower() == lowered:
                return info
        return None

    def find_all(self, *fragments: str) -> List[ToolInfo]:
        """Tools whose name contains every fragment (case-insensitive)."""
        if not self._tools:
            self.refresh_tools()
        needles = [f.lower() for f in fragments if f]
        out = []
        for info in self._tools:
            hay = info.name.lower()
            if all(n in hay for n in needles):
                out.append(info)
        return out

    def resolve(self, candidates: Sequence[str], *,
                fragments: Sequence[Any] = ()) -> ToolInfo:
        """Find the first candidate the editor actually advertises.

        ``candidates`` are exact tool names to try in order. ``fragments`` is a
        looser fallback, either one group of substrings or several alternative
        groups; the first group that matches anything wins. Alternatives matter
        because a renamed tool may preserve one naming idea but not another --
        a build tool renamed to ``BuildAllVerseCode`` keeps "verse" but loses
        "compile".

        Raises :class:`DiscoveryError` listing what was tried, so a renamed
        tool produces an actionable message.
        """
        if not self._tools:
            self.refresh_tools()
        for candidate in candidates:
            found = self.find(candidate)
            if found is not None:
                return found
        for group in _fragment_groups(fragments):
            matches = self.find_all(*group)
            if matches:
                return matches[0]
        raise DiscoveryError(
            f"None of {list(candidates)} are advertised by this editor build"
            + (f" (also tried fragments {list(fragments)})" if fragments else "")
            + ". Run `uefn-link tools --grep <word>` to list what is available."
        )

    # -- invocation -----------------------------------------------------
    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None,
                  *, timeout: Optional[float] = None) -> Any:
        """Invoke a tool by exact name and return its parsed result."""
        self.ensure_initialized()
        return self._request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=timeout,
        )

    # -- internals ------------------------------------------------------
    def _request(self, method: str, params: Optional[Dict[str, Any]] = None,
                 timeout: Optional[float] = None) -> Any:
        msg = jsonrpc.request(method, params)
        response = self.transport.post(msg, timeout=timeout)
        if response is None:
            raise TransportError(f"No response to {method!r} from the editor.")
        return jsonrpc.unwrap(response)

    # -- raw access -----------------------------------------------------
    def raw_request(self, method: str, params: Optional[Dict[str, Any]] = None,
                    timeout: Optional[float] = None) -> Any:
        """Escape hatch for MCP methods this client does not model."""
        return self._request(method, params, timeout=timeout)


def _fragment_groups(fragments: Sequence[Any]) -> List[Sequence[str]]:
    """Normalise the ``fragments`` argument into alternative substring groups.

    Accepts a single group (``("verse", "compil")``) or several alternatives
    (``[("verse", "compil"), ("verse", "build")]``). Groups are tried in order.
    """
    if not fragments:
        return []
    if all(isinstance(f, str) for f in fragments):
        return [list(fragments)]
    groups: List[Sequence[str]] = []
    for entry in fragments:
        if isinstance(entry, str):
            groups.append([entry])
        elif isinstance(entry, (list, tuple)):
            groups.append(list(entry))
    return groups


def extract_text(result: Any) -> str:
    """Flatten an MCP tool result into readable text.

    MCP tool results are ``{"content": [{"type": "text", "text": ...}, ...]}``.
    UEFN also returns structured payloads, so those are JSON-dumped rather
    than dropped.
    """
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return "\n".join(_flatten_item(item) for item in result)
    if isinstance(result, dict):
        if "content" in result:
            chunks = _flatten_item(result.get("content"))
            body = chunks if isinstance(chunks, str) else "\n".join(chunks)
            if body:
                return f"ERROR: {body}" if result.get("isError") else body
        if "structuredContent" in result:
            return json.dumps(result["structuredContent"], indent=2)
        if result.get("isError"):
            return f"ERROR: {json.dumps(result, default=str)}"
    return json.dumps(result, indent=2, default=str)


def _flatten_item(item: Any) -> str:
    """Render one content entry as text."""
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    if isinstance(item, list):
        return "\n".join(part for part in (_flatten_item(i) for i in item) if part)
    if isinstance(item, dict):
        if item.get("type") == "text" or "text" in item:
            return str(item.get("text", ""))
        if item.get("type") == "resource":
            resource = item.get("resource") or {}
            return str(resource.get("text", json.dumps(resource, default=str)))
        return json.dumps(item, default=str)
    return str(item)


def extract_structured(result: Any) -> Any:
    """Return a parsed object when a tool result carries JSON, else the text.

    UEFN toolsets commonly return their payload as a JSON string inside a text
    content block, so a caller that needs a dict cannot index the raw MCP
    envelope. This unwraps that layer, and falls back to the text when the
    result is prose (compiler output, logs).
    """
    text = extract_text(result)
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    for line in stripped.splitlines():
        candidate = line.strip()
        if candidate.startswith(("{", "[")) and candidate.endswith(("}", "]")):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    return text
