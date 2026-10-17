"""Hint table mapping logical operations to editor-advertised tool names.

This is deliberately a *hint* table, not a contract. Epic ships UEFN MCP as a
beta and documents that toolsets are added and renamed between releases (and
that only a subset of the UE 5.8 toolsets exist in UEFN). So every entry lists
several plausible names plus loose fragments, and
:meth:`MCPClient.resolve` falls back to substring matching against whatever
``tools/list`` actually returns.

If a lookup fails, the fix is to inspect the live tool list rather than to
edit this file blindly::

    uefn-link tools --grep verse
"""
from __future__ import annotations

from typing import Dict, Sequence, Tuple

# Each value is (exact candidates in priority order, fragment fallbacks).
Lookup = Tuple[Sequence[str], Sequence[str]]


def _lookup(*candidates: str, fragments: Sequence[str] = ()) -> Lookup:
    return (candidates, fragments)


# --- Verse -----------------------------------------------------------------
# The Verse toolset reads, edits, and compiles project Verse source.
VERSE: Dict[str, Lookup] = {
    "list_files": _lookup(
        "VerseToolset.ListFiles", "Verse.ListFiles", "ListVerseFiles",
        fragments=("verse", "list"),
    ),
    "read_file": _lookup(
        "VerseToolset.ReadFile", "Verse.ReadFile", "ReadVerseFile",
        fragments=("verse", "read"),
    ),
    "write_file": _lookup(
        "VerseToolset.WriteFile", "Verse.WriteFile", "WriteVerseFile",
        fragments=("verse", "write"),
    ),
    "replace": _lookup(
        "VerseToolset.ReplaceInFile", "VerseToolset.Replace", "Verse.Replace",
        fragments=("verse", "replace"),
    ),
    "grep": _lookup(
        "VerseToolset.SearchFiles", "VerseToolset.Grep", "Verse.Search",
        fragments=("verse", "search"),
    ),
    "delete_file": _lookup(
        "VerseToolset.DeleteFile", "Verse.DeleteFile",
        fragments=("verse", "delete"),
    ),
    "move_file": _lookup(
        "VerseToolset.MoveFile", "Verse.MoveFile",
        fragments=("verse", "move"),
    ),
    "create_directory": _lookup(
        "VerseToolset.CreateDirectory", "Verse.CreateDirectory",
        fragments=("verse", "director"),
    ),
    "compile": _lookup(
        "VerseToolset.Compile", "VerseToolset.Build", "Verse.Compile",
        "BuildVerse", "VerseToolset.BuildAllVerseCode",
        fragments=[("verse", "compil"), ("verse", "build")],
    ),
}

# --- Verse Scene Graph (entities and components) ---------------------------
ENTITY: Dict[str, Lookup] = {
    "create": _lookup(
        "EntityToolset.CreateEntity", "SceneGraphToolset.CreateEntity",
        "VerseEntityToolset.CreateEntity", "CreateEntity",
        fragments=("entity", "create"),
    ),
    "destroy": _lookup(
        "EntityToolset.DestroyEntity", "SceneGraphToolset.DestroyEntity",
        "DestroyEntity",
        fragments=("entity", "destroy"),
    ),
    "list": _lookup(
        "EntityToolset.ListEntities", "SceneGraphToolset.ListEntities",
        "ListEntities",
        fragments=("entit", "list"),
    ),
    "add_component": _lookup(
        "EntityToolset.AddComponent", "SceneGraphToolset.AddComponent",
        "AddComponent",
        fragments=("component", "add"),
    ),
    "list_component_classes": _lookup(
        "EntityToolset.ListComponentClasses", "SceneGraphToolset.ListComponentClasses",
        "ListComponentClasses",
        fragments=("component", "class"),
    ),
    "get_transform": _lookup(
        "EntityToolset.GetComponentTransform", "EntityToolset.GetTransform",
        "GetComponentTransform", "GetEntityTransform",
        fragments=("transform", "get"),
    ),
    "set_transform": _lookup(
        "EntityToolset.SetComponentTransform", "EntityToolset.SetTransform",
        "SetComponentTransform", "SetEntityTransform",
        fragments=("transform", "set"),
    ),
    "get_property": _lookup(
        "EntityToolset.GetComponentProperty", "EntityToolset.GetProperty",
        fragments=("component", "propert", "get"),
    ),
    "set_property": _lookup(
        "EntityToolset.SetComponentProperty", "EntityToolset.SetProperty",
        fragments=("component", "propert", "set"),
    ),
}

# --- Creative devices ------------------------------------------------------
DEVICE: Dict[str, Lookup] = {
    "list_assets": _lookup(
        "DeviceToolset.ListDeviceAssets", "DeviceToolset.ListAvailableDevices",
        "DeviceToolset.BrowseCatalog",
        fragments=("device", "list"),
    ),
    "place": _lookup(
        "DeviceToolset.PlaceDevice", "DeviceToolset.SpawnDevice", "PlaceDevice",
        fragments=("device", "place"),
    ),
    "get_properties": _lookup(
        "DeviceToolset.GetDeviceProperties", "DeviceToolset.GetProperties",
        fragments=("device", "propert", "get"),
    ),
    "set_property": _lookup(
        "DeviceToolset.SetDeviceProperty", "DeviceToolset.SetProperty",
        fragments=("device", "propert", "set"),
    ),
    "list_event_bindings": _lookup(
        "DeviceToolset.ListEventBindings", "DeviceToolset.GetEventBindings",
        fragments=("event", "list"),
    ),
    "add_event_binding": _lookup(
        "DeviceToolset.AddEventBinding", "DeviceToolset.BindEvent",
        fragments=("event", "add"),
    ),
    "remove_event_binding": _lookup(
        "DeviceToolset.RemoveEventBinding", "DeviceToolset.UnbindEvent",
        fragments=("event", "remove"),
    ),
    "get_binding_options": _lookup(
        "DeviceToolset.GetEventBindingOptions", "DeviceToolset.ListEventOptions",
        fragments=("event", "option"),
    ),
}

# --- Sessions (Play in Creator) -------------------------------------------
# UEFN plays in client (PIC), not in editor (PIE).
SESSION: Dict[str, Lookup] = {
    "start": _lookup(
        "ValkyrieToolset.SessionToolset.StartSession",
        "SessionToolset.StartSession", "StartSession", "LaunchSession",
        fragments=("session", "start"),
    ),
    "start_game": _lookup(
        "ValkyrieToolset.SessionToolset.StartGame",
        "SessionToolset.StartGame", "StartGame",
        fragments=("game", "start"),
    ),
    "stop": _lookup(
        "ValkyrieToolset.SessionToolset.StopSession",
        "SessionToolset.StopSession", "StopSession", "EndSession",
        fragments=("session", "stop"),
    ),
    "stop_game": _lookup(
        "ValkyrieToolset.SessionToolset.StopGame",
        "SessionToolset.StopGame", "StopGame",
        fragments=("game", "stop"),
    ),
    "status": _lookup(
        "ValkyrieToolset.SessionToolset.GetSessionStatus",
        "SessionToolset.GetSessionStatus", "GetSessionStatus",
        fragments=("session", "status"),
    ),
    "push_changes": _lookup(
        "ValkyrieToolset.SessionToolset.PushChanges",
        "SessionToolset.PushChanges", "PushChanges",
        fragments=("push", "change"),
    ),
    "game_state": _lookup(
        "ValkyrieToolset.SessionToolset.GetGameState",
        "SessionToolset.GetGameState", "GetGameState",
        fragments=("game", "state"),
    ),
    "client_log": _lookup(
        "ValkyrieToolset.SessionToolset.GetClientLog",
        "SessionToolset.GetClientLog", "GetClientLog", "GetSessionLog",
        fragments=("log", "get"),
    ),
}

# --- Python editor scripting ----------------------------------------------
# Python is the path for asset pipelines (textures, materials) and for any
# editor action the toolsets do not expose.
PYTHON: Dict[str, Lookup] = {
    "enable": _lookup(
        "ValkyrieToolset.ValkyriePythonToolset.EnablePythonInUEFN",
        "PythonToolset.EnablePython", "EnablePythonInUEFN",
        fragments=("python", "enable"),
    ),
    "execute": _lookup(
        "ValkyrieToolset.ValkyriePythonToolset.ExecutePython",
        "PythonToolset.ExecutePython", "ProgrammaticToolset.ExecuteToolScript",
        "ExecutePythonScript", "execute_tool_script",
        fragments=("python", "exec"),
    ),
}

# --- Project validation ----------------------------------------------------
# UEFN validates content against the project; anything outside the
# allow-list fails validation even when the write appears to succeed.
PROJECT: Dict[str, Lookup] = {
    "validate": _lookup(
        "ProjectToolset.ValidateProject", "ValidationToolset.ValidateContent",
        "ValidateProject",
        fragments=("valid",),
    ),
    "save": _lookup(
        "ProjectToolset.SaveAll", "EditorToolset.SaveAll", "SaveAll",
        fragments=("save",),
    ),
}

GROUPS: Dict[str, Dict[str, Lookup]] = {
    "verse": VERSE,
    "entity": ENTITY,
    "device": DEVICE,
    "session": SESSION,
    "python": PYTHON,
    "project": PROJECT,
}
