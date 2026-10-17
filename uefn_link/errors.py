"""Exception types shared across the package."""
from __future__ import annotations

from typing import Any, Optional


class UEFNLinkError(Exception):
    """Base class for every error raised by this package."""


class TransportError(UEFNLinkError):
    """The editor could not be reached, or the HTTP exchange failed."""


class NotConnectedError(TransportError):
    """No UEFN MCP endpoint answered.

    Usually means UEFN is closed, the project has not enabled both Python
    Editor Scripting and UEFN MCP Toolsets, or the port is wrong.
    """


class MCPError(UEFNLinkError):
    """The editor returned a JSON-RPC error object."""

    def __init__(self, code: int, message: str, data: Optional[Any] = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data


class DiscoveryError(UEFNLinkError):
    """A requested tool or toolset is not advertised by the editor."""


class ValidationError(UEFNLinkError):
    """Local validation failed before anything was sent to the editor."""
