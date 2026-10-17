"""Command-line interface.

Every subcommand is designed to be scriptable: machine-readable output with
``--json``, non-zero exit codes on failure, and no interactive prompts. An
agent harness can therefore drive UEFN through this without a bespoke client.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Optional, Sequence

from uefn_link import __version__
from uefn_link.config import LinkConfig, discover
from uefn_link.doctor import diagnose
from uefn_link.errors import UEFNLinkError
from uefn_link.link import UEFNLink

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_DISCOVERY = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uefn-link",
        description="Drive Unreal Editor for Fortnite through its embedded Unreal MCP server.",
    )
    parser.add_argument("--version", action="version", version=f"uefn-link {__version__}")
    parser.add_argument("--host", help="MCP host (loopback only; default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="MCP port (default 8000)")
    parser.add_argument("--path", help="MCP path (default /mcp)")
    parser.add_argument("--timeout", type=float, help="request timeout in seconds")
    parser.add_argument("--config", help="path to a generated .mcp.json")
    parser.add_argument("--json", action="store_true", help="emit JSON")

    subs = parser.add_subparsers(dest="command", required=True)

    subs.add_parser("doctor", help="preflight the link and report what is missing")
    subs.add_parser("capabilities", help="show which operations this editor build supports")

    tools = subs.add_parser("tools", help="list tools the editor advertises")
    tools.add_argument("--grep", help="only tools whose name contains this text")
    tools.add_argument("--schema", help="print the full schema for one tool name")

    call = subs.add_parser("call", help="call any tool by exact name")
    call.add_argument("tool")
    call.add_argument("--args", default="{}", help="JSON object of arguments")
    call.add_argument("--arg", action="append", default=[],
                      help="key=value argument (repeatable); values parsed as JSON when possible")
    call.add_argument("--timeout", type=float, dest="call_timeout")

    verse = subs.add_parser("verse", help="Verse source and build")
    vsub = verse.add_subparsers(dest="verse_command", required=True)
    vsub.add_parser("list", help="list Verse files")
    v_read = vsub.add_parser("read", help="read a Verse file")
    v_read.add_argument("path")
    v_grep = vsub.add_parser("grep", help="search Verse files")
    v_grep.add_argument("query")
    v_grep.add_argument("--path")
    v_write = vsub.add_parser("write", help="write a Verse file from a local path or stdin")
    v_write.add_argument("path")
    v_write.add_argument("--from-file", help="local file to send; omit to read stdin")
    v_write.add_argument("--build", action="store_true", help="compile after writing")
    v_replace = vsub.add_parser("replace", help="replace text inside a Verse file")
    v_replace.add_argument("path")
    v_replace.add_argument("--find", required=True)
    v_replace.add_argument("--replace", required=True)
    v_replace.add_argument("--build", action="store_true")
    vsub.add_parser("build", help="compile Verse and report the result")

    entity = subs.add_parser("entity", help="Scene Graph entities and components")
    esub = entity.add_subparsers(dest="entity_command", required=True)
    esub.add_parser("list", help="list entities in the level")
    e_create = esub.add_parser("create", help="create an entity")
    e_create.add_argument("class_name")
    e_create.add_argument("--name")
    e_add = esub.add_parser("add-component", help="add a component to an entity")
    e_add.add_argument("entity")
    e_add.add_argument("component_class")
    e_move = esub.add_parser("move", help="move an entity by a delta")
    e_move.add_argument("entity")
    e_move.add_argument("delta", nargs=3, type=float, metavar=("DX", "DY", "DZ"))
    e_move.add_argument("--luf", action="store_true", help="delta is in left-up-forward axes")
    e_move.add_argument("--component")
    e_xform = esub.add_parser("transform", help="read or write an entity transform")
    e_xform.add_argument("entity")
    e_xform.add_argument("--location", nargs=3, type=float)
    e_xform.add_argument("--rotation", nargs=3, type=float)
    e_xform.add_argument("--scale", nargs=3, type=float)
    e_xform.add_argument("--component")
    e_xform.add_argument("--luf", action="store_true")
    esub.add_parser("component-classes", help="list creatable component classes")

    device = subs.add_parser("device", help="Creative devices")
    dsub = device.add_subparsers(dest="device_command", required=True)
    d_list = dsub.add_parser("list", help="list placeable devices")
    d_list.add_argument("--filter")
    d_place = dsub.add_parser("place", help="place a device")
    d_place.add_argument("asset")
    d_place.add_argument("--location", nargs=3, type=float)
    d_props = dsub.add_parser("properties", help="read a device's properties")
    d_props.add_argument("device")
    d_set = dsub.add_parser("set", help="set a device property")
    d_set.add_argument("device")
    d_set.add_argument("property_name")
    d_set.add_argument("value", help="JSON or plain string")
    d_bind = dsub.add_parser("bind", help="bind a device event to a function")
    d_bind.add_argument("device")
    d_bind.add_argument("event")
    d_bind.add_argument("function")
    d_bind.add_argument("--target")
    d_bindings = dsub.add_parser("bindings", help="list a device's event bindings")
    d_bindings.add_argument("device")

    session = subs.add_parser("session", help="play sessions (Play in Creator)")
    ssub = session.add_subparsers(dest="session_command", required=True)
    ssub.add_parser("start", help="start a play session")
    ssub.add_parser("stop", help="stop the play session")
    ssub.add_parser("status", help="session status")
    ssub.add_parser("push", help="push Verse changes into the running session")
    ssub.add_parser("log", help="read the client log")
    s_cycle = ssub.add_parser("cycle", help="push or restart so the session matches the project")
    s_cycle.add_argument("--restart", action="store_true",
                         help="restart the session (required after adding new assets)")

    py = subs.add_parser("python", help="Python editor scripting and asset pipelines")
    psub = py.add_subparsers(dest="python_command", required=True)
    psub.add_parser("enable", help="enable Python editor scripting")
    p_exec = psub.add_parser("exec", help="run Python inside the editor")
    p_exec.add_argument("--from-file", help="local .py file to run; omit to read stdin")
    p_reimport = psub.add_parser("reimport-texture", help="reimport a texture from its source art")
    p_reimport.add_argument("asset_path")
    p_import = psub.add_parser("import-texture", help="import an image as a texture asset")
    p_import.add_argument("source_file")
    p_import.add_argument("destination")
    p_import.add_argument("--settings", default="{}", help="JSON import settings")
    p_assets = psub.add_parser("list-assets", help="list assets in a content directory")
    p_assets.add_argument("--directory", default="/Game")
    p_assets.add_argument("--class", dest="class_name")
    p_save = psub.add_parser("save", help="save assets")
    p_save.add_argument("--asset", action="append", default=[])

    subs.add_parser("validate", help="validate project content")
    subs.add_parser("listen", help="read the editor's SSE stream for notifications")

    return parser


# --- connection ------------------------------------------------------------
def resolve_config(args: argparse.Namespace) -> LinkConfig:
    cfg = discover()
    if args.config:
        from pathlib import Path
        from uefn_link.config import from_mcp_json

        found = from_mcp_json(Path(args.config))
        if found:
            cfg = found
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    if args.path:
        cfg.path = args.path
    if args.timeout:
        cfg.timeout = args.timeout
    return cfg


def connect(args: argparse.Namespace) -> UEFNLink:
    cfg = resolve_config(args)
    link = UEFNLink(host=cfg.host, port=cfg.port, path=cfg.path, timeout=cfg.timeout)
    return link.connect()


# --- output ----------------------------------------------------------------
def emit(args: argparse.Namespace, payload: Any, text: Optional[str] = None) -> None:
    """Print a result, as JSON when asked and otherwise as plain text."""
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return
    if text is not None:
        print(text)
        return
    if isinstance(payload, str):
        print(payload)
        return
    print(json.dumps(payload, indent=2, default=str))


def parse_value(raw: str) -> Any:
    """Interpret a CLI value as JSON when it looks like JSON, else as text."""
    stripped = raw.strip()
    if stripped == "":
        return ""
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return raw


def collect_args(args: argparse.Namespace) -> Dict[str, Any]:
    """Merge ``--args`` JSON with repeated ``--arg key=value`` pairs."""
    merged: Dict[str, Any] = {}
    if getattr(args, "args", None):
        try:
            parsed = json.loads(args.args)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--args is not valid JSON: {exc}")
        if not isinstance(parsed, dict):
            raise SystemExit("--args must be a JSON object")
        merged.update(parsed)
    for pair in getattr(args, "arg", []) or []:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"--arg needs key=value, got {pair!r}")
        merged[key] = parse_value(value)
    return merged


def read_stdin_or_file(path: Optional[str]) -> str:
    if path:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    return sys.stdin.read()


# --- dispatch --------------------------------------------------------------
def run(args: argparse.Namespace) -> int:
    if args.command == "doctor":
        report = diagnose(resolve_config(args))
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            _print_doctor(report)
        return EXIT_OK if report.get("ok") else EXIT_ERROR

    link = connect(args)

    try:
        return _dispatch(args, link)
    finally:
        link.close()


def _dispatch(args: argparse.Namespace, link: UEFNLink) -> int:
    cmd = args.command

    if cmd == "capabilities":
        caps = link.capabilities()
        if args.json:
            print(json.dumps(caps, indent=2, default=str))
        else:
            _print_capabilities(caps)
        return EXIT_OK

    if cmd == "tools":
        if args.schema:
            info = link.client.find(args.schema)
            if info is None:
                print(f"No tool named {args.schema!r}", file=sys.stderr)
                return EXIT_DISCOVERY
            print(json.dumps(
                {"name": info.name, "description": info.description,
                 "inputSchema": info.input_schema}, indent=2, default=str))
            return EXIT_OK
        names = link.client.tool_names()
        if args.grep:
            needle = args.grep.lower()
            names = [n for n in names if needle in n.lower()]
        if args.json:
            print(json.dumps(names, indent=2))
        else:
            for name in names:
                print(name)
        return EXIT_OK

    if cmd == "call":
        result = link.client.call_tool(args.tool, collect_args(args),
                                       timeout=args.call_timeout)
        emit(args, result)
        return EXIT_OK

    if cmd == "verse":
        return _verse(args, link)
    if cmd == "entity":
        return _entity(args, link)
    if cmd == "device":
        return _device(args, link)
    if cmd == "session":
        return _session(args, link)
    if cmd == "python":
        return _python(args, link)
    if cmd == "validate":
        emit(args, link.project.validate())
        return EXIT_OK
    if cmd == "listen":
        messages, raw = link.transport.get_sse_stream()
        if args.json:
            print(json.dumps(messages, indent=2, default=str))
        else:
            print(raw if raw.strip() else "(no messages)")
        return EXIT_OK

    print(f"Unknown command: {cmd}", file=sys.stderr)
    return EXIT_ERROR


def _verse(args: argparse.Namespace, link: UEFNLink) -> int:
    sub = args.verse_command
    if sub == "list":
        emit(args, link.verse.list_files())
    elif sub == "read":
        emit(args, link.verse.read(args.path))
    elif sub == "grep":
        emit(args, link.verse.grep(args.query, path=args.path))
    elif sub == "write":
        content = read_stdin_or_file(args.from_file)
        if args.build:
            emit(args, link.verse.apply_and_build(args.path, content))
        else:
            emit(args, link.verse.write(args.path, content))
    elif sub == "replace":
        if args.build:
            link.verse.replace(args.path, args.find, args.replace)
            emit(args, link.verse.compile())
        else:
            emit(args, link.verse.replace(args.path, args.find, args.replace))
    elif sub == "build":
        emit(args, link.verse.compile())
    return EXIT_OK


def _entity(args: argparse.Namespace, link: UEFNLink) -> int:
    sub = args.entity_command
    if sub == "list":
        emit(args, link.entity.list_entities())
    elif sub == "create":
        emit(args, link.entity.create(args.class_name, name=args.name))
    elif sub == "add-component":
        emit(args, link.entity.add_component(args.entity, args.component_class))
    elif sub == "move":
        emit(args, link.entity.move(args.entity, args.delta,
                                    component=args.component, timeout=None))
    elif sub == "transform":
        if args.location or args.rotation or args.scale:
            emit(args, link.entity.set_transform(
                args.entity, location=args.location, rotation=args.rotation,
                scale=args.scale, component=args.component, luf=args.luf))
        else:
            emit(args, link.entity.get_transform(args.entity,
                                                 component=args.component,
                                                 luf=args.luf))
    elif sub == "component-classes":
        emit(args, link.entity.list_component_classes())
    return EXIT_OK


def _device(args: argparse.Namespace, link: UEFNLink) -> int:
    sub = args.device_command
    if sub == "list":
        emit(args, link.device.list_assets(filter_text=args.filter))
    elif sub == "place":
        emit(args, link.device.place(args.asset, location=args.location))
    elif sub == "properties":
        emit(args, link.device.get_properties(args.device))
    elif sub == "set":
        emit(args, link.device.set_property(args.device, args.property_name,
                                            parse_value(args.value)))
    elif sub == "bind":
        emit(args, link.device.add_event_binding(args.device, args.event,
                                                 args.function, target=args.target))
    elif sub == "bindings":
        emit(args, link.device.list_event_bindings(args.device))
    return EXIT_OK


def _session(args: argparse.Namespace, link: UEFNLink) -> int:
    sub = args.session_command
    if sub == "start":
        emit(args, link.session.start())
    elif sub == "stop":
        emit(args, link.session.stop())
    elif sub == "status":
        emit(args, link.session.status())
    elif sub == "push":
        emit(args, link.session.push_changes())
    elif sub == "log":
        emit(args, link.session.client_log())
    elif sub == "cycle":
        emit(args, link.session.live_edit_cycle(restart=args.restart))
    return EXIT_OK


def _python(args: argparse.Namespace, link: UEFNLink) -> int:
    sub = args.python_command
    if sub == "enable":
        emit(args, link.python.enable())
    elif sub == "exec":
        emit(args, link.python.execute(read_stdin_or_file(args.from_file)))
    elif sub == "reimport-texture":
        emit(args, link.python.reimport_texture(args.asset_path))
    elif sub == "import-texture":
        emit(args, link.python.import_texture(args.source_file, args.destination,
                                              settings=json.loads(args.settings)))
    elif sub == "list-assets":
        emit(args, link.python.list_assets(args.directory, class_name=args.class_name))
    elif sub == "save":
        emit(args, link.python.save(args.asset or None))
    return EXIT_OK


# --- human-readable rendering ---------------------------------------------
def _print_doctor(report: Dict[str, Any]) -> None:
    cfg = report.get("config", {})
    print(f"endpoint   {cfg.get('host')}:{cfg.get('port')}{cfg.get('path')}  ({cfg.get('source')})")
    for check in report.get("checks", []):
        mark = "ok  " if check.get("passed") else "FAIL"
        print(f"[{mark}] {check.get('check'):<16} {check.get('detail')}")
        if check.get("hint"):
            print(f"         -> {check['hint']}")
    caps = report.get("capabilities")
    if caps:
        print("\ncapability groups")
        for group, entry in caps.items():
            state = f"{entry['resolved']}/{entry['total']}"
            print(f"  {group:<10} {state}")
    if report.get("hint") and not report.get("ok"):
        print(f"\n{report['hint']}")
    print(f"\nlink {'ready' if report.get('ok') else 'not ready'}")


def _print_capabilities(caps: Dict[str, Any]) -> None:
    for group, ops in caps.items():
        available = [name for name, info in ops.items() if info.get("available")]
        print(f"{group}  ({len(available)}/{len(ops)})")
        for name in sorted(ops):
            info = ops[name]
            if info.get("available"):
                print(f"  ok    {name:<22} -> {info['tool']}")
            else:
                print(f"  --    {name}")
        print()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except UEFNLinkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if isinstance(exc, type) or "advertised" in str(exc):
            print("hint: run `uefn-link doctor` to check the link.", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
