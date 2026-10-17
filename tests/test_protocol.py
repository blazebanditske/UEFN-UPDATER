"""Protocol-layer tests: JSON-RPC, SSE, transport, and discovery."""
from __future__ import annotations

import pytest

from uefn_link.errors import DiscoveryError, MCPError, NotConnectedError
from uefn_link.mcp import jsonrpc, sse
from uefn_link.mcp.client import MCPClient, ToolInfo, extract_text
from uefn_link.mcp.transport import HttpTransport

from tests.fake_uefn import FakeUEFNServer, default_tools, tool


# --- jsonrpc ---------------------------------------------------------------
def test_request_ids_increment():
    first = jsonrpc.request("tools/list")
    second = jsonrpc.request("tools/list")
    assert first["jsonrpc"] == "2.0"
    assert second["id"] > first["id"]
    assert "params" not in first


def test_notification_has_no_id():
    msg = jsonrpc.notification("notifications/initialized")
    assert "id" not in msg
    assert msg["method"] == "notifications/initialized"


def test_unwrap_raises_mcp_error():
    with pytest.raises(MCPError) as excinfo:
        jsonrpc.unwrap({"jsonrpc": "2.0", "id": 1,
                        "error": {"code": -32601, "message": "no such tool"}})
    assert excinfo.value.code == -32601
    assert "no such tool" in str(excinfo.value)


def test_unwrap_returns_result():
    assert jsonrpc.unwrap({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}) == {"ok": True}


def test_unwrap_passes_through_null_result():
    assert jsonrpc.unwrap({"jsonrpc": "2.0", "id": 1, "result": None}) is None


# --- SSE -------------------------------------------------------------------
def test_sse_parses_single_event():
    body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"a":1}}\n\n'
    messages = sse.extract_messages(body)
    assert messages == [{"jsonrpc": "2.0", "id": 1, "result": {"a": 1}}]


def test_sse_parses_multiple_events_in_order():
    body = (
        'data: {"id":1}\n\n'
        'data: {"id":2}\n\n'
        'data: {"id":3}\n\n'
    )
    assert [m["id"] for m in sse.extract_messages(body)] == [1, 2, 3]


def test_sse_joins_multiline_data_and_ignores_comments():
    body = ': keepalive\ndata: {"id":\ndata: 7}\n\n'
    assert sse.extract_messages(body) == [{"id": 7}]


def test_sse_skips_non_json_payloads():
    body = "data: not json\n\ndata: {\"ok\":true}\n\n"
    assert sse.extract_messages(body) == [{"ok": True}]


def test_sse_handles_missing_trailing_blank_line():
    assert sse.extract_messages('data: {"ok":1}') == [{"ok": 1}]


# --- transport -------------------------------------------------------------
def test_transport_refuses_non_loopback_host():
    with pytest.raises(Exception) as excinfo:
        HttpTransport(host="192.168.1.10")
    assert "loopback" in str(excinfo.value)


def test_probe_false_when_nothing_listening():
    # Port 1 is reserved and never has a listener in a test container.
    assert HttpTransport(port=1).probe() is False


def test_post_raises_not_connected_when_port_closed():
    transport = HttpTransport(port=1, timeout=1.0)
    with pytest.raises(NotConnectedError):
        transport.post(jsonrpc.request("tools/list"))


# --- client ----------------------------------------------------------------
def test_initialize_and_tool_discovery():
    with FakeUEFNServer() as server:
        client = MCPClient(HttpTransport(port=server.port))
        info = client.initialize()
        assert info["serverInfo"]["name"] == "UnrealEditorFortnite"
        assert len(client.refresh_tools()) == len(default_tools())


def test_initialize_sends_initialized_notification():
    with FakeUEFNServer() as server:
        client = MCPClient(HttpTransport(port=server.port))
        client.initialize()
        methods = [r.get("method") for r in server.requests]
        assert "notifications/initialized" in methods


def test_session_id_is_captured_and_reused():
    with FakeUEFNServer() as server:
        transport = HttpTransport(port=server.port)
        client = MCPClient(transport)
        client.initialize()
        client.refresh_tools()
        assert transport._session_id in server.session_ids


def test_sse_transport_round_trip():
    with FakeUEFNServer(use_sse=True) as server:
        client = MCPClient(HttpTransport(port=server.port))
        client.initialize()
        names = client.tool_names()
        assert "VerseToolset.Compile" in names


def test_pagination_is_followed():
    with FakeUEFNServer(page_size=5) as server:
        client = MCPClient(HttpTransport(port=server.port))
        tools = client.refresh_tools()
        assert len(tools) == len(default_tools())
        # One request per page plus the first page.
        list_calls = [r for r in server.requests if r.get("method") == "tools/list"]
        assert len(list_calls) == (len(default_tools()) + 4) // 5


def test_empty_tool_list_raises_discovery_error():
    with FakeUEFNServer(tools=[]) as server:
        client = MCPClient(HttpTransport(port=server.port))
        with pytest.raises(DiscoveryError) as excinfo:
            client.refresh_tools()
        assert "UEFN MCP Toolsets" in str(excinfo.value)


def test_find_is_case_insensitive():
    with FakeUEFNServer() as server:
        client = MCPClient(HttpTransport(port=server.port))
        client.refresh_tools()
        assert client.find("versetoolset.compile") is not None


def test_resolve_uses_fragment_fallback_when_names_change():
    """A renamed tool should still resolve through the registry's fallbacks.

    This drives the real registry entry for ``verse.compile`` rather than a
    hand-written fragment list, so it verifies the shipped fallbacks actually
    cover a rename that drops the "compile" wording.
    """
    from uefn_link.mcp import registry

    renamed = [tool("VerseToolset.BuildAllVerseCode", "renamed compile tool")]
    with FakeUEFNServer(tools=renamed) as server:
        client = MCPClient(HttpTransport(port=server.port))
        client.refresh_tools()
        candidates, fragments = registry.VERSE["compile"]
        found = client.resolve(candidates, fragments=fragments)
        assert found.name == "VerseToolset.BuildAllVerseCode"


def test_resolve_prefers_exact_name_over_fragment_match():
    """An exact candidate must win even when a loose match comes first."""
    from uefn_link.mcp import registry

    tools = [
        tool("VerseToolset.BuildAllVerseCode", "loose match"),
        tool("VerseToolset.Compile", "exact match"),
    ]
    with FakeUEFNServer(tools=tools) as server:
        client = MCPClient(HttpTransport(port=server.port))
        client.refresh_tools()
        candidates, fragments = registry.VERSE["compile"]
        assert client.resolve(candidates, fragments=fragments).name == "VerseToolset.Compile"


def test_resolve_raises_actionable_error_when_missing():
    with FakeUEFNServer(tools=[tool("SomethingElse.Entirely")]) as server:
        client = MCPClient(HttpTransport(port=server.port))
        client.refresh_tools()
        with pytest.raises(DiscoveryError) as excinfo:
            client.resolve(["VerseToolset.Compile"], fragments=("verse", "compil"))
        assert "uefn-link tools" in str(excinfo.value)


def test_call_tool_surfaces_editor_errors():
    def handler(name, args):
        raise RuntimeError("device not found in level")

    with FakeUEFNServer(handler=handler) as server:
        client = MCPClient(HttpTransport(port=server.port))
        client.initialize()
        with pytest.raises(MCPError) as excinfo:
            client.call_tool("DeviceToolset.SetDeviceProperty",
                             {"device": "x", "property": "y", "value": 1})
        assert "device not found" in str(excinfo.value)


def test_call_tool_returns_content():
    with FakeUEFNServer() as server:
        client = MCPClient(HttpTransport(port=server.port))
        client.initialize()
        result = client.call_tool("ProjectToolset.ValidateProject")
        assert "content" in result


# --- result flattening -----------------------------------------------------
def test_extract_text_from_content_blocks():
    result = {"content": [{"type": "text", "text": "line one"},
                          {"type": "text", "text": "line two"}]}
    assert extract_text(result) == "line one\nline two"


def test_extract_text_marks_errors():
    result = {"isError": True, "content": [{"type": "text", "text": "boom"}]}
    assert extract_text(result).startswith("ERROR: boom")


def test_extract_text_handles_structured_content():
    result = {"structuredContent": {"entities": 2}}
    assert "entities" in extract_text(result)


def test_extract_text_passes_through_strings():
    assert extract_text("plain") == "plain"


# --- tool info -------------------------------------------------------------
def test_tool_info_exposes_required_and_properties():
    info = ToolInfo.from_wire(
        tool("T", "d", ["path", "content"], {"path": {"type": "string"}})
    )
    assert info.required_args() == ["path", "content"]
    assert "path" in info.properties()
