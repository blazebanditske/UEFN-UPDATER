"""Domain-layer tests against the real fake HTTP server.

Every test drives the actual client and transport; the editor is the only
simulated component.
"""
from __future__ import annotations

import pytest

from uefn_link import coords
from uefn_link.domains.python_assets import (
    JSON_MARKER,
    _parse_embedded_json,
    build_import_texture_script,
    build_list_assets_script,
)
from uefn_link.link import UEFNLink
from uefn_link.mcp.client import MCPClient
from uefn_link.mcp.transport import HttpTransport

from tests.fake_uefn import FakeUEFNServer, tool


def make_link(server: FakeUEFNServer) -> UEFNLink:
    """Build a link pointed at the fake editor, bypassing config discovery."""
    return UEFNLink(client=MCPClient(HttpTransport(port=server.port))).connect()


def test_link_discovers_port_from_environment(monkeypatch):
    """UEFNLink() with no arguments must honour discovered config.

    Otherwise the library would talk to port 8000 while `doctor` and the CLI
    talked to the configured port, and the two would disagree.
    """
    monkeypatch.setenv("UEFN_MCP_PORT", "9999")
    link = UEFNLink()
    assert link.transport.port == 9999


def test_link_explicit_port_overrides_discovery(monkeypatch):
    monkeypatch.setenv("UEFN_MCP_PORT", "9999")
    link = UEFNLink(port=1234)
    assert link.transport.port == 1234


def test_link_uses_injected_client():
    with FakeUEFNServer() as server:
        client = MCPClient(HttpTransport(port=server.port))
        link = UEFNLink(client=client).connect()
        assert link.tool_count() == len(server.tools)


# --- verse -----------------------------------------------------------------
def test_verse_list_read_write_compile():
    with FakeUEFNServer() as server:
        link = make_link(server)
        assert "hello.verse" in link.verse.list_files()

        content = link.verse.read("Verse/hello.verse")
        assert "hello_world" in content

        link.verse.write("Verse/new.verse", "new():void=\n    Print(\"x\")\n")
        written = [c for c in server.calls
                   if c["params"]["name"] == "VerseToolset.WriteFile"]
        assert written[0]["params"]["arguments"]["path"] == "Verse/new.verse"

        assert "succeeded" in link.verse.compile()


def test_verse_replace_passes_find_and_replace():
    with FakeUEFNServer() as server:
        link = make_link(server)
        link.verse.replace("Verse/a.verse", "old", "new")
        call = [c for c in server.calls
                if c["params"]["name"] == "VerseToolset.ReplaceInFile"][0]
        assert call["params"]["arguments"] == {
            "path": "Verse/a.verse", "find": "old", "replace": "new"
        }


def test_apply_and_build_reports_both_steps():
    with FakeUEFNServer() as server:
        link = make_link(server)
        result = link.verse.apply_and_build("Verse/x.verse", "code")
        assert result["path"] == "Verse/x.verse"
        assert result["build_ok"] is True
        assert "written" in result and "compiled" in result


def test_build_ok_detects_compile_failure():
    def handler(name, args):
        if name == "VerseToolset.Compile":
            return {"output": "Verse build failed: 2 errors"}
        return {"ok": True}

    with FakeUEFNServer(handler=handler) as server:
        link = make_link(server)
        result = link.verse.apply_and_build("Verse/x.verse", "bad code")
        assert result["build_ok"] is False


def test_build_ok_requires_explicit_success():
    """An unrecognised message must not be read as success."""
    def handler(name, args):
        if name == "VerseToolset.Compile":
            return {"output": ""}
        return {"ok": True}

    with FakeUEFNServer(handler=handler) as server:
        link = make_link(server)
        assert link.verse.apply_and_build("Verse/x.verse", "c")["build_ok"] is False


# --- entities --------------------------------------------------------------
def test_entity_create_list_and_component():
    with FakeUEFNServer() as server:
        link = make_link(server)
        link.entity.create("MeshEntity", name="Rock")
        assert "Entity_1" in link.entity.list_entities()
        link.entity.add_component("Entity_1", "StaticMeshComponent")
        call = [c for c in server.calls
                if c["params"]["name"] == "EntityToolset.AddComponent"][0]
        assert call["params"]["arguments"]["componentClass"] == "StaticMeshComponent"


def test_entity_get_transform_returns_xyz_by_default():
    with FakeUEFNServer() as server:
        link = make_link(server)
        result = link.entity.get_transform("Entity_1")
        assert result["location"] == [100.0, 200.0, 300.0]


def test_entity_get_transform_luf_flips_y():
    with FakeUEFNServer() as server:
        link = make_link(server)
        result = link.entity.get_transform("Entity_1", luf=True)
        assert result["location"] == [100.0, -200.0, 300.0]


def test_entity_set_transform_converts_luf_input():
    with FakeUEFNServer() as server:
        link = make_link(server)
        link.entity.set_transform("Entity_1", location=[10.0, 20.0, 30.0], luf=True)
        call = [c for c in server.calls
                if c["params"]["name"] == "EntityToolset.SetComponentTransform"][0]
        assert call["params"]["arguments"]["location"] == [10.0, -20.0, 30.0]


def test_entity_move_reads_then_writes_offset():
    with FakeUEFNServer() as server:
        link = make_link(server)
        link.entity.move("Entity_1", [0.0, 0.0, 500.0])
        call = [c for c in server.calls
                if c["params"]["name"] == "EntityToolset.SetComponentTransform"][0]
        # 300 + 500 on Z, X/Y untouched
        assert call["params"]["arguments"]["location"] == [100.0, 200.0, 800.0]


def test_entity_move_raises_when_transform_unreadable():
    def handler(name, args):
        if name == "EntityToolset.GetComponentTransform":
            return {"unexpected": "shape"}
        return {"ok": True}

    with FakeUEFNServer(handler=handler) as server:
        link = make_link(server)
        with pytest.raises(ValueError):
            link.entity.move("Entity_1", [0.0, 0.0, 1.0])


# --- devices ---------------------------------------------------------------
def test_device_list_place_and_set_property():
    with FakeUEFNServer() as server:
        link = make_link(server)
        assert "PlayerSpawner" in link.device.list_assets()
        link.device.place("PlayerSpawner", location=[0.0, 0.0, 100.0])
        placed = [c for c in server.calls
                  if c["params"]["name"] == "DeviceToolset.PlaceDevice"][0]
        assert placed["params"]["arguments"]["location"] == [0.0, 0.0, 100.0]

        link.device.set_property("PlayerSpawner_1", "RespawnTime", 5.0)
        set_call = [c for c in server.calls
                    if c["params"]["name"] == "DeviceToolset.SetDeviceProperty"][0]
        assert set_call["params"]["arguments"]["value"] == 5.0


def test_device_configure_collects_per_property_failures():
    def handler(name, args):
        if name == "DeviceToolset.SetDeviceProperty" and args.get("property") == "Bad":
            raise RuntimeError("property not allow-listed")
        return {"ok": True}

    with FakeUEFNServer(handler=handler) as server:
        link = make_link(server)
        results = link.device.configure("D", {"Good": 1, "Bad": 2})
        assert results["Good"] == '{"ok": true}' or "ok" in results["Good"]
        assert results["Bad"].startswith("FAILED:")


def test_device_verify_property_reads_back():
    with FakeUEFNServer() as server:
        link = make_link(server)
        assert link.device.verify_property("D", "RespawnTime", 3.0) is True
        assert link.device.verify_property("D", "RespawnTime", 99.0) is False


# --- sessions --------------------------------------------------------------
def test_session_status_and_start():
    with FakeUEFNServer() as server:
        link = make_link(server)
        assert link.session.is_running() is False
        link.session.start()
        started = [c for c in server.calls
                   if c["params"]["name"] == "ValkyrieToolset.SessionToolset.StartSession"]
        assert started


def test_session_is_running_true_when_running():
    def handler(name, args):
        if name == "ValkyrieToolset.SessionToolset.GetSessionStatus":
            return {"status": "session running, client connected"}
        return {"ok": True}

    with FakeUEFNServer(handler=handler) as server:
        link = make_link(server)
        assert link.session.is_running() is True


def test_session_cycle_without_restart_pushes_changes():
    with FakeUEFNServer() as server:
        link = make_link(server)
        result = link.session.live_edit_cycle(settle=0.0)
        assert result["restart"] is False
        names = [c["params"]["name"] for c in server.calls]
        assert "ValkyrieToolset.SessionToolset.PushChanges" in names


def test_session_cycle_with_restart_starts_and_does_not_push():
    with FakeUEFNServer() as server:
        link = make_link(server)
        result = link.session.live_edit_cycle(restart=True, settle=0.0)
        assert result["restart"] is True
        names = [c["params"]["name"] for c in server.calls]
        assert "ValkyrieToolset.SessionToolset.StartSession" in names
        assert "ValkyrieToolset.SessionToolset.PushChanges" not in names


def test_session_client_log():
    with FakeUEFNServer() as server:
        link = make_link(server)
        assert "session ready" in link.session.client_log()


# --- python / assets -------------------------------------------------------
def test_python_execute_sends_code():
    with FakeUEFNServer() as server:
        link = make_link(server)
        link.python.execute("import unreal\nunreal.log('hi')")
        call = [c for c in server.calls
                if c["params"]["name"] == "ValkyrieToolset.ValkyriePythonToolset.ExecutePython"][0]
        assert "unreal.log" in call["params"]["arguments"]["code"]


def test_import_texture_script_uses_asset_tools_not_filesystem():
    script = build_import_texture_script("C:/art/rock.png", "/Game/Textures/T_Rock")
    assert "unreal.AssetImportTask()" in script
    assert "import os" not in script and "shutil" not in script
    assert "C:/art/rock.png" in script


def test_import_texture_script_splits_destination_path_and_name():
    script = build_import_texture_script("a.png", "/Game/Textures/T_Rock")
    assert "destination_path = '/Game/Textures'" in script
    assert "destination_name = 'T_Rock'" in script


def test_import_texture_script_applies_settings():
    script = build_import_texture_script("a.png", "/Game/T_X", {"srgb": False})
    assert '"srgb": false' in script


def test_list_assets_script_emits_marked_json():
    script = build_list_assets_script("/Game/Textures", "Texture2D", True)
    assert JSON_MARKER in script
    assert "'/Game/Textures'" in script


def test_parse_embedded_json_extracts_payload():
    text = f'LogPython: {JSON_MARKER}{{"count": 2, "assets": []}}'
    assert _parse_embedded_json(text) == {"count": 2, "assets": []}


def test_parse_embedded_json_falls_back_to_raw_text():
    assert _parse_embedded_json("LogPython: nothing here") == "LogPython: nothing here"


def test_asset_paths_never_use_filesystem_modules():
    """Epic's rule: asset moves go through the Unreal API, not os/shutil."""
    from uefn_link.domains.python_assets import (
        build_reimport_script,
        build_save_script,
        build_texture_settings_script,
    )
    for script in (
        build_reimport_script("/Game/T_X"),
        build_save_script(["/Game/T_X"]),
        build_save_script(None),
        build_texture_settings_script("/Game/T_X", {"srgb": False}),
    ):
        assert "import os" not in script
        assert "shutil" not in script


# --- project ---------------------------------------------------------------
def test_project_validate():
    with FakeUEFNServer() as server:
        link = make_link(server)
        assert "true" in link.project.validate().lower()


# --- capabilities ----------------------------------------------------------
def test_capabilities_reports_available_operations():
    with FakeUEFNServer() as server:
        link = make_link(server)
        caps = link.capabilities()
        assert caps["verse"]["compile"]["available"] is True
        assert caps["verse"]["compile"]["tool"] == "VerseToolset.Compile"


def test_capabilities_reports_missing_operations_without_raising():
    with FakeUEFNServer(tools=[tool("VerseToolset.Compile", "only this")]) as server:
        link = make_link(server)
        caps = link.capabilities()
        assert caps["verse"]["compile"]["available"] is True
        assert caps["device"]["place"]["available"] is False
        assert "reason" in caps["device"]["place"]


# --- coordinate helpers ----------------------------------------------------
def test_coord_round_trip():
    assert coords.luf_to_xyz(coords.xyz_to_luf([1.0, 2.0, 3.0])) == (1.0, 2.0, 3.0)


def test_coord_rejects_wrong_arity():
    with pytest.raises(ValueError):
        coords.xyz_to_luf([1.0, 2.0])
