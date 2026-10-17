"""JSON-RPC 2.0 message helpers.

Only the subset MCP needs: request, notification, and result/error parsing.
Kept separate from transport so it can be unit-tested without a socket.
"""
from __future__ import annotations

import itertools
from typing import Any, Dict, Optional

from uefn_link.errors import MCPError

JSONRPC_VERSION = "2.0"

_ids = itertools.count(1)


def next_id() -> int:
    return next(_ids)


def request(method: str, params: Optional[Dict[str, Any]] = None,
            request_id: Optional[int] = None) -> Dict[str, Any]:
    """Build a JSON-RPC request object."""
    msg: Dict[str, Any] = {
        "jsonrpc": JSONRPC_VERSION,
        "id": next_id() if request_id is None else request_id,
        "method": method,
    }
    if params is not None:
        msg["params"] = params
    return msg


def notification(method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build a JSON-RPC notification (no id, no response expected)."""
    msg: Dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def unwrap(payload: Dict[str, Any]) -> Any:
    """Return the ``result`` of a response, or raise :class:`MCPError`.

    Servers occasionally answer a notification with an error; that still
    raises, because silently dropping an error hides real failures.
    """
    if "error" in payload and payload["error"]:
        err = payload["error"]
        if isinstance(err, dict):
            raise MCPError(
                int(err.get("code", -1)),
                str(err.get("message", "unknown error")),
                err.get("data"),
            )
        raise MCPError(-1, str(err))
    return payload.get("result")


def is_response(payload: Dict[str, Any]) -> bool:
    """True when the message is a response (has an id) rather than a request."""
    return "id" in payload and ("result" in payload or "error" in payload)
