"""Base class shared by every domain wrapper.

A domain turns a logical operation (``verse.compile``) into a concrete tool
call by resolving the operation against the editor's live tool list. Domains
never hardcode a wire name; they ask :mod:`uefn_link.mcp.registry` for
candidates and let the client pick.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from uefn_link.errors import DiscoveryError, UEFNLinkError
from uefn_link.mcp.client import MCPClient, extract_text
from uefn_link.mcp import registry


class Domain:
    """Common plumbing for the capability wrappers."""

    #: key into :data:`uefn_link.mcp.registry.GROUPS`
    group: str = ""

    def __init__(self, client: MCPClient) -> None:
        if not self.group:
            raise TypeError(f"{type(self).__name__} must set a registry group")
        self.client = client

    # -- resolution -----------------------------------------------------
    def lookup(self, operation: str) -> registry.Lookup:
        table = registry.GROUPS[self.group]
        if operation not in table:
            raise DiscoveryError(
                f"{self.group}.{operation} is not a known operation. "
                f"Known: {', '.join(sorted(table))}"
            )
        return table[operation]

    def lookup_all(self) -> Dict[str, registry.Lookup]:
        """Every operation this domain knows how to resolve."""
        return dict(registry.GROUPS[self.group])

    def resolve(self, operation: str):
        candidates, fragments = self.lookup(operation)
        return self.client.resolve(candidates, fragments=fragments)

    def call(self, operation: str, arguments: Optional[Dict[str, Any]] = None,
             *, timeout: Optional[float] = None) -> Any:
        """Resolve ``operation`` and invoke it, returning the parsed result."""
        tool = self.resolve(operation)
        return self.client.call_tool(tool.name, arguments, timeout=timeout)

    def call_text(self, operation: str, arguments: Optional[Dict[str, Any]] = None,
                  *, timeout: Optional[float] = None) -> str:
        """Like :meth:`call`, but flattened to readable text."""
        return extract_text(self.call(operation, arguments, timeout=timeout))

    def try_call_text(self, operation: str, arguments: Optional[Dict[str, Any]] = None,
                      *, timeout: Optional[float] = None) -> Optional[str]:
        """Call an operation, returning ``None`` when it is unavailable.

        Used for diagnostics whose absence should not fail a larger workflow:
        a build without the game-state tool still has a working session cycle.
        """
        try:
            return self.call_text(operation, arguments, timeout=timeout)
        except UEFNLinkError:
            return None

    def describe(self, operation: str) -> Dict[str, Any]:
        """Return the live schema of the tool behind an operation.

        Useful for an agent that needs the real argument names before calling,
        rather than guessing them.
        """
        tool = self.resolve(operation)
        return {
            "operation": f"{self.group}.{operation}",
            "tool": tool.name,
            "description": tool.description,
            "inputSchema": tool.input_schema,
        }
