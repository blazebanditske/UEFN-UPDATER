"""Tests for config discovery, doctor, and the CLI.

The CLI is driven through ``main(argv)`` with a fake server standing in for the
editor, so argument parsing, dispatch, and exit codes are all covered.
"""
from __future__ import annotations

import json

import pytest

from uefn_link import cli
from uefn_link.config import LinkConfig, discover, from_env, from_mcp_json, parse_url
from uefn_link.doctor import diagnose

from tests.fake_uefn import FakeUEFNServer, tool


# --- url parsing -----------------------------------------------------------
@pytest.mark.parametrize("url,expected", [
    ("http://127.0.0.1:8000/mcp", ("127.0.0.1", 8000, "/mcp")),
    ("http://localhost:8001/mcp/", ("localhost", 8001, "/mcp/")),
    ("http://127.0.0.1/mcp", ("127.0.0.1", 8000, "/mcp")),
    ("127.0.0.1:9000/mcp", ("127.0.0.1", 9000, "/mcp")),
    ("http://127.0.0.1:8000", ("127.0.0.1", 8000, "/mcp")),
])
def test_parse_url(url, expected):
    assert parse_url(url) == expected


def test_parse_url_rejects_unsupported_scheme():
    assert parse_url("ftp://127.0.0.1:8000/mcp") is None


# --- env -------------------------------------------------------------------
def test_from_env_uses_full_url():
    cfg = from_env({"UEFN_MCP_URL": "http://127.0.0.1:8766/mcp"})
    assert (cfg.host, cfg.port, cfg.path) == ("127.0.0.1", 8766, "/mcp")
    assert cfg.source == "UEFN_MCP_URL"


def test_from_env_uses_host_and_port():
    cfg = from_env({"UEFN_MCP_PORT": "9001"})
    assert cfg.port == 9001


def test_from_env_returns_none_when_unset():
    assert from_env({}) is None


def test_from_env_ignores_bad_timeout():
    cfg = from_env({"UEFN_MCP_PORT": "9001", "UEFN_MCP_TIMEOUT": "abc"})
    assert cfg.timeout == 120.0


# --- .mcp.json -------------------------------------------------------------
def test_from_mcp_json_reads_generated_config(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps({
        "mcpServers": {"uefn": {"url": "http://127.0.0.1:8765/mcp"}}
    }))
    cfg = from_mcp_json(path)
    assert cfg.port == 8765
    assert cfg.source.endswith(".mcp.json")


def test_from_mcp_json_accepts_any_server_key(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({
        "mcpServers": {"whatever-name": {"url": "http://127.0.0.1:8111/mcp"}}
    }))
    assert from_mcp_json(path).port == 8111


def test_from_mcp_json_accepts_flat_url(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"url": "http://127.0.0.1:8222/mcp"}))
    assert from_mcp_json(path).port == 8222


def test_from_mcp_json_ignores_stdio_entries(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({
        "mcpServers": {"uefn": {"command": "python", "args": ["server.py"]}}
    }))
    assert from_mcp_json(path) is None


def test_from_mcp_json_handles_missing_and_broken_files(tmp_path):
    assert from_mcp_json(tmp_path / "nope.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert from_mcp_json(broken) is None


def test_discover_walks_up_from_cwd(tmp_path, monkeypatch):
    project = tmp_path / "proj" / "Content"
    project.mkdir(parents=True)
    (tmp_path / "proj" / ".mcp.json").write_text(json.dumps({
        "mcpServers": {"uefn": {"url": "http://127.0.0.1:8333/mcp"}}
    }))
    monkeypatch.delenv("UEFN_MCP_URL", raising=False)
    monkeypatch.delenv("UEFN_MCP_HOST", raising=False)
    monkeypatch.delenv("UEFN_MCP_PORT", raising=False)
    assert discover(start=project).port == 8333


def test_discover_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("UEFN_MCP_URL", raising=False)
    monkeypatch.delenv("UEFN_MCP_HOST", raising=False)
    monkeypatch.delenv("UEFN_MCP_PORT", raising=False)
    cfg = discover(start=tmp_path)
    assert (cfg.host, cfg.port, cfg.path) == ("127.0.0.1", 8000, "/mcp")


# --- doctor ----------------------------------------------------------------
def test_diagnose_reports_unreachable_port():
    report = diagnose(LinkConfig(port=1, timeout=1.0))
    assert report["ok"] is False
    assert report["checks"][0]["passed"] is False
    assert "hint" in report["checks"][0]


def test_diagnose_reports_ready_link():
    with FakeUEFNServer() as server:
        report = diagnose(LinkConfig(port=server.port))
        assert report["ok"] is True
        assert report["tool_count"] == len(server.tools)
        assert report["capabilities"]["verse"]["resolved"] > 0


def test_diagnose_explains_missing_toolsets():
    with FakeUEFNServer(tools=[]) as server:
        report = diagnose(LinkConfig(port=server.port))
        assert report["ok"] is False
        assert "Beta Access" in report["hint"]


def test_diagnose_partial_capabilities_still_ready():
    with FakeUEFNServer(tools=[tool("VerseToolset.Compile", "compile only")]) as server:
        report = diagnose(LinkConfig(port=server.port))
        assert report["ok"] is True
        assert report["capabilities"]["device"]["resolved"] == 0


# --- CLI -------------------------------------------------------------------
def test_cli_doctor_json(capsys, tmp_path):
    with FakeUEFNServer() as server:
        code = cli.main(["--port", str(server.port), "--json", "doctor"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True


def test_cli_doctor_nonzero_when_unreachable(capsys):
    code = cli.main(["--port", "1", "--timeout", "1", "doctor"])
    assert code == 1
    assert "not ready" in capsys.readouterr().out


def test_cli_tools_lists_names(capsys):
    with FakeUEFNServer() as server:
        code = cli.main(["--port", str(server.port), "tools"])
    assert code == 0
    out = capsys.readouterr().out
    assert "VerseToolset.Compile" in out


def test_cli_tools_grep_filters(capsys):
    with FakeUEFNServer() as server:
        cli.main(["--port", str(server.port), "tools", "--grep", "device"])
    out = capsys.readouterr().out
    assert "DeviceToolset.PlaceDevice" in out
    assert "VerseToolset.Compile" not in out


def test_cli_tools_schema(capsys):
    with FakeUEFNServer() as server:
        code = cli.main(["--port", str(server.port), "tools", "--schema", "VerseToolset.ReadFile"])
    assert code == 0
    assert "inputSchema" in capsys.readouterr().out


def test_cli_tools_schema_unknown_exits_2(capsys):
    with FakeUEFNServer() as server:
        code = cli.main(["--port", str(server.port), "tools", "--schema", "Nope"])
    assert code == 2


def test_cli_call_with_json_args(capsys):
    with FakeUEFNServer() as server:
        code = cli.main(["--port", str(server.port), "--json", "call",
                         "VerseToolset.ReadFile", "--args", '{"path": "Verse/a.verse"}'])
    assert code == 0
    assert "hello_world" in capsys.readouterr().out


def test_cli_call_with_key_value_args(capsys):
    with FakeUEFNServer() as server:
        code = cli.main(["--port", str(server.port), "--json", "call",
                         "VerseToolset.WriteFile", "--arg", "path=Verse/a.verse",
                         "--arg", "content=hello"])
    assert code == 0


def test_cli_call_parses_json_values_in_args(capsys):
    with FakeUEFNServer() as server:
        cli.main(["--port", str(server.port), "call", "DeviceToolset.SetDeviceProperty",
                  "--arg", "device=D", "--arg", "property=RespawnTime",
                  "--arg", "value=5.5"])
        call = [c for c in server.calls
                if c["params"]["name"] == "DeviceToolset.SetDeviceProperty"][0]
        assert call["params"]["arguments"]["value"] == 5.5


def test_cli_verse_build(capsys):
    with FakeUEFNServer() as server:
        code = cli.main(["--port", str(server.port), "verse", "build"])
    assert code == 0
    assert "succeeded" in capsys.readouterr().out


def test_cli_verse_write_from_file(capsys, tmp_path):
    source = tmp_path / "mechanic.verse"
    source.write_text("my_mechanic():void=\n    Print(\"hi\")\n")
    with FakeUEFNServer() as server:
        code = cli.main(["--port", str(server.port), "verse", "write",
                         "Verse/mechanic.verse", "--from-file", str(source), "--build"])
    assert code == 0
    names = [c["params"]["name"] for c in server.calls]
    assert "VerseToolset.WriteFile" in names
    assert "VerseToolset.Compile" in names


def test_cli_verse_write_stdin(capsys, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("code():void=\n    Print(\"x\")\n"))
    with FakeUEFNServer() as server:
        code = cli.main(["--port", str(server.port), "verse", "write", "Verse/x.verse"])
    assert code == 0


def test_cli_entity_move(capsys):
    with FakeUEFNServer() as server:
        code = cli.main(["--port", str(server.port), "entity", "move",
                         "Entity_1", "0", "0", "500"])
    assert code == 0
    call = [c for c in server.calls
            if c["params"]["name"] == "EntityToolset.SetComponentTransform"][0]
    assert call["params"]["arguments"]["location"][2] == 800.0


def test_cli_entity_transform_read(capsys):
    with FakeUEFNServer() as server:
        code = cli.main(["--port", str(server.port), "--json", "entity",
                         "transform", "Entity_1"])
    assert code == 0
    assert "100" in capsys.readouterr().out


def test_cli_device_place_and_set(capsys):
    with FakeUEFNServer() as server:
        cli.main(["--port", str(server.port), "device", "place", "PlayerSpawner",
                  "--location", "0", "0", "100"])
        cli.main(["--port", str(server.port), "device", "set", "PlayerSpawner_1",
                  "RespawnTime", "5"])
    names = [c["params"]["name"] for c in server.calls]
    assert "DeviceToolset.PlaceDevice" in names
    assert "DeviceToolset.SetDeviceProperty" in names


def test_cli_session_cycle_restart(capsys):
    with FakeUEFNServer() as server:
        code = cli.main(["--port", str(server.port), "session", "cycle", "--restart"])
    assert code == 0


def test_cli_python_exec(capsys, tmp_path):
    script = tmp_path / "task.py"
    script.write_text("import unreal\nunreal.log('x')\n")
    with FakeUEFNServer() as server:
        code = cli.main(["--port", str(server.port), "python", "exec",
                         "--from-file", str(script)])
    assert code == 0


def test_cli_capabilities(capsys):
    with FakeUEFNServer() as server:
        code = cli.main(["--port", str(server.port), "capabilities"])
    assert code == 0
    out = capsys.readouterr().out
    assert "verse" in out


def test_cli_validate(capsys):
    with FakeUEFNServer() as server:
        code = cli.main(["--port", str(server.port), "validate"])
    assert code == 0


def test_cli_listen_reads_sse(capsys):
    with FakeUEFNServer() as server:
        code = cli.main(["--port", str(server.port), "listen"])
    assert code == 0
    assert "editor ready" in capsys.readouterr().out


def test_cli_reports_error_and_exit_code_when_editor_down(capsys):
    code = cli.main(["--port", "1", "--timeout", "1", "verse", "build"])
    assert code == 1
    assert "error:" in capsys.readouterr().err


def test_cli_bad_args_json_exits(capsys):
    with FakeUEFNServer() as server:
        with pytest.raises(SystemExit):
            cli.main(["--port", str(server.port), "call", "X", "--args", "{bad"])


def test_cli_arg_without_equals_exits(capsys):
    with FakeUEFNServer() as server:
        with pytest.raises(SystemExit):
            cli.main(["--port", str(server.port), "call", "X", "--arg", "noequals"])


# --- value parsing ---------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("5", 5), ("5.5", 5.5), ("true", True), ("null", None),
    ('[1,2]', [1, 2]), ('{"a":1}', {"a": 1}), ("hello", "hello"), ("", ""),
])
def test_parse_value(raw, expected):
    assert cli.parse_value(raw) == expected
