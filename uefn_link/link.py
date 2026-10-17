"""The top-level object an agent or script holds.

``UEFNLink`` owns one MCP session and exposes each capability area as an
attribute::

    link = UEFNLink().connect()
    link.verse.write("Verse/hello.verse", code)
    link.verse.compile()
    link.session.start()

The connection is lazy and the tool list is fetched once per session, because
the editor requires a restart to change which toolsets it serves.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from uefn_link import config
from uefn_link.domains import (
    DeviceDomain,
    EntityDomain,
    ProjectDomain,
    PythonDomain,
    SessionDomain,
    VerseDomain,
)
from uefn_link.domains.base import Domain
from uefn_link.mcp.client import MCPClient
from uefn_link.mcp.transport import HttpTransport


class UEFNLink:
    """A live link to a running UEFN editor.

    When no host/port is given, connection settings are discovered the same way
    the CLI discovers them: an explicit path, then the environment, then a
    generated ``.mcp.json``, then defaults. Passing ``client`` bypasses
    discovery entirely, which is what tests do.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        path: Optional[str] = None,
        timeout: Optional[float] = None,
        client: Optional[MCPClient] = None,
    ) -> None:
        if client is not None:
            self.client = client
        else:
            cfg = config.discover()
            self.client = MCPClient(HttpTransport(
                host=host if host is not None else cfg.host,
                port=port if port is not None else cfg.port,
                path=path if path is not None else cfg.path,
                timeout=timeout if timeout is not None else cfg.timeout,
            ))

        self.verse = VerseDomain(self.client)
        self.entity = EntityDomain(self.client)
        self.device = DeviceDomain(self.client)
        self.session = SessionDomain(self.client)
        self.python = PythonDomain(self.client)
        self.project = ProjectDomain(self.client)

    # -- lifecycle ------------------------------------------------------
    def connect(self, *, require_tools: bool = True) -> "UEFNLink":
        """Handshake and fetch the tool list.

        Failing here rather than on first use keeps a misconfigured project
        from surfacing as a confusing mid-task error.
        """
        self.client.initialize()
        if require_tools:
            self.client.refresh_tools()
        return self

    def close(self) -> None:
        """Release the session id; the editor owns the server process."""
        self.client.transport._session_id = None  # noqa: SLF001 - deliberate reset
        self.client._initialized = False  # noqa: SLF001

    def __enter__(self) -> "UEFNLink":
        return self.connect()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- introspection --------------------------------------------------
    @property
    def transport(self) -> HttpTransport:
        return self.client.transport

    def domains(self) -> Dict[str, Domain]:
        return {
            "verse": self.verse,
            "entity": self.entity,
            "device": self.device,
            "session": self.session,
            "python": self.python,
            "project": self.project,
        }

    def tool_count(self) -> int:
        return len(self.client.tools)

    def capabilities(self) -> Dict[str, Dict[str, Any]]:
        """Report which operations resolve against the live tool list.

        This is the honest answer to "what can this editor build actually do
        right now": each operation is resolved and marked available or not,
        without calling anything.
        """
        report: Dict[str, Dict[str, Any]] = {}
        for group, domain in self.domains().items():
            ops: Dict[str, Any] = {}
            for operation in sorted(domain.lookup_all()):
                try:
                    tool = domain.resolve(operation)
                    ops[operation] = {"available": True, "tool": tool.name}
                except Exception as exc:
                    ops[operation] = {"available": False, "reason": str(exc)}
            report[group] = ops
        return report

    def __repr__(self) -> str:
        return f"UEFNLink({self.transport.url!r})"
