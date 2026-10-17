"""Client configuration: where the editor's MCP server lives.

The values come from UEFN itself. When the editor generates its client config
it writes an ``.mcp.json`` in the project root; reading that first means the
port does not have to be kept in sync by hand.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from uefn_link.mcp.transport import DEFAULT_HOST, DEFAULT_PATH, DEFAULT_PORT

# Names UEFN has used for the generated server entry.
_SERVER_KEYS = ("uefn", "unreal", "unreal-mcp", "UnrealMCP", "UEFN MCP")

DEFAULT_CONFIG_NAMES = (".mcp.json", "mcp.json")


@dataclass
class LinkConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    path: str = DEFAULT_PATH
    timeout: float = 120.0
    source: str = "defaults"

    def describe(self) -> Dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "path": self.path,
            "timeout": self.timeout,
            "source": self.source,
        }


def discover(start: Optional[Path] = None,
             extra_paths: Optional[List[Path]] = None) -> LinkConfig:
    """Resolve connection settings, most specific source first.

    Order: explicit paths, environment, an ``.mcp.json`` found while walking up
    from ``start``, then built-in defaults.
    """
    if extra_paths:
        for candidate in extra_paths:
            cfg = from_mcp_json(candidate)
            if cfg:
                return cfg

    cfg = from_env()
    if cfg:
        return cfg

    base = Path(start or Path.cwd()).resolve()
    for directory in (base, *base.parents):
        for name in DEFAULT_CONFIG_NAMES:
            candidate = directory / name
            if candidate.is_file():
                cfg = from_mcp_json(candidate)
                if cfg:
                    return cfg
    return LinkConfig()


def from_env(env: Optional[Dict[str, str]] = None) -> Optional[LinkConfig]:
    """Read ``UEFN_MCP_URL`` or ``UEFN_MCP_HOST``/``UEFN_MCP_PORT``."""
    env = env if env is not None else os.environ
    url = env.get("UEFN_MCP_URL")
    if url:
        parsed = parse_url(url)
        if parsed:
            host, port, path = parsed
            return LinkConfig(host=host, port=port, path=path,
                              timeout=_float(env.get("UEFN_MCP_TIMEOUT"), 120.0),
                              source="UEFN_MCP_URL")
    host = env.get("UEFN_MCP_HOST")
    port = env.get("UEFN_MCP_PORT")
    if host or port:
        return LinkConfig(
            host=host or DEFAULT_HOST,
            port=int(port) if port else DEFAULT_PORT,
            path=env.get("UEFN_MCP_PATH") or DEFAULT_PATH,
            timeout=_float(env.get("UEFN_MCP_TIMEOUT"), 120.0),
            source="UEFN_MCP_HOST/PORT",
        )
    return None


def from_mcp_json(path: Path) -> Optional[LinkConfig]:
    """Extract connection settings from an MCP client config file.

    Both the ``mcpServers`` shape used by agent CLIs and a flat ``url`` are
    accepted, since UEFN's generated file follows the former.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    servers = data.get("mcpServers") or data.get("servers") or {}
    entries: List[Dict[str, Any]] = []
    if isinstance(servers, dict):
        for key in _SERVER_KEYS:
            value = servers.get(key)
            if isinstance(value, dict):
                entries.append(value)
        if not entries:
            entries.extend(v for v in servers.values() if isinstance(v, dict))
    if isinstance(data.get("url"), str):
        entries.append(data)

    for entry in entries:
        url = entry.get("url") or entry.get("serverUrl")
        if isinstance(url, str) and url.strip():
            parsed = parse_url(url)
            if parsed:
                host, port, upath = parsed
                return LinkConfig(host=host, port=port, path=upath,
                                  source=str(path))
        # A stdio entry would carry a command; UEFN serves HTTP, so a stdio
        # entry here means the file is not the editor's config.
    return None


def parse_url(url: str) -> Optional[Tuple[str, int, str]]:
    """Parse ``http://host:port/path`` without pulling in urllib.parse."""
    text = url.strip()
    if "://" in text:
        scheme, _, rest = text.partition("://")
        if scheme not in ("http", "https"):
            return None
    else:
        rest = text
    authority, _, path = rest.partition("/")
    host, _, port_text = authority.partition(":")
    if not host:
        return None
    port = int(port_text) if port_text.isdigit() else DEFAULT_PORT
    return (host, port, "/" + path if path else DEFAULT_PATH)


def _float(value: Optional[str], default: float) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default
