"""HTTP transport to the UEFN editor's embedded Unreal MCP server.

The server is loopback-only and has no auth layer, which is why the default
host is 127.0.0.1 and why nothing here supports remote endpoints.

Only the standard library is used so the client runs under the interpreter
that ships with UEFN as well as a normal system Python.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from uefn_link.errors import NotConnectedError, TransportError
from uefn_link.mcp import sse

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_PATH = "/mcp"

# Long operations (session start, Verse build, push changes) legitimately take
# minutes; the editor also hitches on tool calls, so the default is generous.
DEFAULT_TIMEOUT = 120.0


class HttpTransport:
    """POST JSON-RPC messages to an MCP HTTP endpoint and read responses."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        path: str = DEFAULT_PATH,
        timeout: float = DEFAULT_TIMEOUT,
        scheme: str = "http",
    ) -> None:
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise TransportError(
                "Unreal MCP binds to loopback only and has no authentication. "
                f"Refusing non-local host {host!r}."
            )
        self.host = host
        self.port = int(port)
        self.path = path if path.startswith("/") else "/" + path
        self.timeout = float(timeout)
        self.scheme = scheme
        self._session_id: Optional[str] = None

    # -- addressing -----------------------------------------------------
    @property
    def url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}{self.path}"

    def __repr__(self) -> str:
        return f"HttpTransport({self.url!r}, timeout={self.timeout})"

    # -- health ---------------------------------------------------------
    def probe(self, timeout: float = 2.0) -> bool:
        """Cheap TCP reachability check; never raises."""
        try:
            with socket.create_connection((self.host, self.port), timeout=timeout):
                return True
        except OSError:
            return False

    # -- exchange -------------------------------------------------------
    def post(self, message: Dict[str, Any], timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Send one JSON-RPC message.

        Returns the response dict for a request, or ``None`` for a
        notification (which by definition gets no response body).
        """
        body = json.dumps(message).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self._session_id = sid
                ctype = (resp.headers.get("Content-Type") or "").lower()
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:400]
            except Exception:  # pragma: no cover - best-effort diagnostics
                pass
            if exc.code in (404, 405):
                raise NotConnectedError(
                    f"{self.url} answered HTTP {exc.code}. UEFN is reachable but the "
                    "MCP endpoint is not served at this path. Check the Server Port "
                    "Number in Editor Preferences and that UEFN MCP Toolsets is enabled."
                ) from exc
            raise TransportError(f"HTTP {exc.code} from {self.url}: {detail}") from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            raise NotConnectedError(
                f"Could not reach UEFN MCP at {self.url}: {exc}. "
                "Is the editor open with Python Editor Scripting and UEFN MCP "
                "Toolsets enabled?"
            ) from exc
        except OSError as exc:
            raise NotConnectedError(f"Connection to {self.url} failed: {exc}") from exc

        if message.get("id") is None:
            return None  # notification: server replies 202 with an empty body

        if not raw.strip():
            raise TransportError(
                f"{self.url} returned an empty body for request id "
                f"{message.get('id')}."
            )

        if "text/event-stream" in ctype or raw.lstrip().startswith(("event:", "data:", ":")):
            return self._pick_response(sse.extract_messages(raw), message.get("id"))
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TransportError(
                f"{self.url} returned a non-JSON body: {raw[:200]!r}"
            ) from exc
        return self._pick_response(_as_list(parsed), message.get("id"))

    @staticmethod
    def _pick_response(messages: List[Dict[str, Any]], request_id: Any) -> Optional[Dict[str, Any]]:
        """Select the response matching ``request_id`` from a batch/stream."""
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if msg.get("id") == request_id and ("result" in msg or "error" in msg):
                return msg
        # Some servers stream a single response without echoing the id.
        for msg in messages:
            if isinstance(msg, dict) and ("result" in msg or "error" in msg):
                return msg
        if messages:
            return messages[-1]
        return None

    def get_sse_stream(self, timeout: Optional[float] = None) -> Tuple[List[Dict[str, Any]], str]:
        """Open the SSE endpoint and read whatever the server pushes.

        Used by ``listen`` to surface editor-side notifications. Returns the
        parsed messages and the raw body.
        """
        req = urllib.request.Request(
            self.url,
            headers={"Accept": "text/event-stream", "MCP-Protocol-Version": "2025-06-18"},
            method="GET",
        )
        if self._session_id:
            req.add_header("Mcp-Session-Id", self._session_id)
        try:
            with urllib.request.urlopen(req, timeout=timeout or 5.0) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise NotConnectedError(f"SSE stream at {self.url} failed: {exc}") from exc
        return sse.extract_messages(raw), raw


def _as_list(parsed: Any) -> List[Dict[str, Any]]:
    if isinstance(parsed, list):
        return [m for m in parsed if isinstance(m, dict)]
    if isinstance(parsed, dict):
        return [parsed]
    return []
