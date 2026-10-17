"""Minimal Server-Sent Events parser.

Unreal MCP answers over HTTP and may reply either with a plain
``application/json`` body or with a ``text/event-stream`` body carrying the
JSON-RPC response as one or more ``data:`` events. This parser handles the
second case using only the standard library.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional


def iter_events(text: str) -> Iterable[Dict[str, str]]:
    """Yield ``{"event": ..., "data": ...}`` dicts from an SSE body.

    Multi-line ``data:`` fields are joined with newlines, per the SSE spec.
    Comment lines (starting with ``:``) are ignored.
    """
    event_type: Optional[str] = None
    data_lines: List[str] = []

    def flush() -> Optional[Dict[str, str]]:
        if not data_lines and event_type is None:
            return None
        return {"event": event_type or "message", "data": "\n".join(data_lines)}

    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if line == "":
            ev = flush()
            if ev is not None:
                yield ev
            event_type, data_lines = None, []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_type = value
        elif field == "data":
            data_lines.append(value)
        # id/retry fields are not needed for request/response MCP traffic

    ev = flush()
    if ev is not None:
        yield ev


def extract_messages(text: str) -> List[Dict[str, Any]]:
    """Return every JSON object found in an SSE body, in order."""
    out: List[Dict[str, Any]] = []
    for ev in iter_events(text):
        payload = ev.get("data", "").strip()
        if not payload:
            continue
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out
