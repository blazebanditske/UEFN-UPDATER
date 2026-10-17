"""Preflight diagnosis.

When the link does not work, the cause is almost always one of a small set of
setup problems, and each has a different fix. :func:`diagnose` walks them in
order and reports the first blocker with the specific remedy, rather than
letting a connection error stand in for all of them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from uefn_link.config import LinkConfig, discover
from uefn_link.errors import UEFNLinkError
from uefn_link.mcp.client import MCPClient
from uefn_link.mcp.transport import HttpTransport


def diagnose(config: Optional[LinkConfig] = None) -> Dict[str, Any]:
    """Run the checks and return a structured report.

    Checks, in order:
      1. a port answers on the configured host;
      2. the MCP handshake succeeds;
      3. the editor advertises tools;
      4. which capability groups resolve.
    """
    cfg = config or discover()
    report: Dict[str, Any] = {"config": cfg.describe(), "checks": []}

    transport = HttpTransport(host=cfg.host, port=cfg.port, path=cfg.path,
                              timeout=min(cfg.timeout, 15.0))

    reachable = transport.probe()
    _add(report, "port_reachable", reachable,
         "MCP port answered" if reachable else "nothing is listening",
         _port_hint(cfg) if not reachable else "")
    if not reachable:
        report["ok"] = False
        return report

    client = MCPClient(transport)
    try:
        info = client.initialize()
    except UEFNLinkError as exc:
        _add(report, "handshake", False, "MCP initialize failed", str(exc))
        report["ok"] = False
        report["hint"] = (
            "The port answered but is not an MCP endpoint. Another service may "
            "hold this port; set Server Port Number in UEFN Editor Preferences "
            "and update UEFN_MCP_PORT."
        )
        return report
    _add(report, "handshake", True, "MCP initialize succeeded", "")
    report["server_info"] = info

    try:
        tools = client.refresh_tools()
    except UEFNLinkError as exc:
        _add(report, "tools", False, "no tools advertised", str(exc))
        report["ok"] = False
        report["hint"] = (
            "Enable UEFN MCP Toolsets under Project Settings > Beta Access and "
            "restart the editor; toolsets are only published at startup."
        )
        return report
    _add(report, "tools", True, f"{len(tools)} tools advertised", "")
    report["tool_count"] = len(tools)

    groups = _group_availability(client)
    report["capabilities"] = groups
    report["ok"] = any(
        entry.get("available") for entry in groups.values() if isinstance(entry, dict)
    )
    if not report["ok"]:
        report["hint"] = (
            "Tools exist but none of the expected UEFN toolsets resolved. "
            "Run `uefn-link tools` and compare the names; UEFN MCP is beta and "
            "tool names change between releases."
        )
    return report


def _group_availability(client: MCPClient) -> Dict[str, Any]:
    from uefn_link.mcp import registry

    out: Dict[str, Any] = {}
    for group, table in registry.GROUPS.items():
        found = 0
        missing: List[str] = []
        for operation, (candidates, fragments) in table.items():
            try:
                client.resolve(candidates, fragments=fragments)
                found += 1
            except UEFNLinkError:
                missing.append(operation)
        out[group] = {
            "available": found > 0,
            "resolved": found,
            "total": len(table),
            "unresolved": missing,
        }
    return out


def _add(report: Dict[str, Any], name: str, passed: bool, detail: str, hint: str) -> None:
    entry = {"check": name, "passed": bool(passed), "detail": detail}
    if hint:
        entry["hint"] = hint
    report["checks"].append(entry)


def _port_hint(cfg: LinkConfig) -> str:
    return (
        f"Nothing is listening on {cfg.host}:{cfg.port}. Open UEFN with the "
        "project, confirm Python Editor Scripting and UEFN MCP Toolsets are "
        "enabled, and confirm the Server Port Number matches."
    )
