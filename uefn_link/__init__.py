"""uefn-link: drive Unreal Editor for Fortnite from an agent.

The transport is UEFN's official embedded Unreal MCP server (JSON-RPC over
local HTTP). Everything in :mod:`uefn_link.domains` is a thin, typed wrapper
over toolsets that the editor itself advertises, so the client stays correct
as Epic adds or renames tools.
"""
from uefn_link.link import UEFNLink
from uefn_link.errors import (
    UEFNLinkError,
    TransportError,
    MCPError,
    DiscoveryError,
    NotConnectedError,
    ValidationError,
)

__all__ = [
    "UEFNLink",
    "UEFNLinkError",
    "TransportError",
    "MCPError",
    "DiscoveryError",
    "NotConnectedError",
    "ValidationError",
]
__version__ = "0.1.0"
