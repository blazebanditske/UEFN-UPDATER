"""Python editor scripting: the path for asset work the toolsets do not cover.

Importing and reconfiguring textures, creating material instances, and bulk
asset operations are editor-side tasks with no dedicated toolset, so they run
as Python inside the editor. This domain sends code, and the helpers below
build that code so callers do not hand-assemble Unreal Python API calls.

Two rules from Epic's documentation shape everything here:

* Use the Unreal Python API for assets, never ``os``/``shutil``, because
  moving asset files on disk breaks internal content references.
* Only properties visible in the UEFN UI are allow-listed; anything else fails
  project validation even when the write appears to succeed.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from uefn_link.domains.base import Domain

EXECUTE_TIMEOUT = 300.0


class PythonDomain(Domain):
    group = "python"

    def enable(self, *, timeout: Optional[float] = None) -> str:
        """Turn on Python editor scripting without the GUI.

        UEFN asks for this on project open; enabling it through the toolset
        keeps an unattended run from stalling on a dialog.
        """
        return self.call_text("enable", {}, timeout=timeout)

    def execute(self, code: str, *, timeout: Optional[float] = EXECUTE_TIMEOUT) -> str:
        """Run Python inside the editor and return its captured output."""
        return self.call_text("execute", {"code": code}, timeout=timeout)

    def execute_json(self, code: str, *, timeout: Optional[float] = EXECUTE_TIMEOUT) -> Any:
        """Run code expected to print a single JSON object.

        The helper snippet wraps the body so the editor prints exactly one
        JSON document, which makes the result machine-readable instead of
        scraping ``LogPython`` lines.
        """
        wrapped = _json_wrapper(code)
        raw = self.execute(wrapped, timeout=timeout)
        return _parse_embedded_json(raw)

    # -- asset pipelines ------------------------------------------------
    def import_texture(self, source_file: str, destination: str, *,
                       settings: Optional[Dict[str, Any]] = None,
                       timeout: Optional[float] = EXECUTE_TIMEOUT) -> str:
        """Import an image file as a texture asset.

        ``destination`` is a project content path such as
        ``/Game/Textures/T_Rock``. ``settings`` are applied as asset import
        options after import.
        """
        code = build_import_texture_script(source_file, destination, settings)
        return self.execute(code, timeout=timeout)

    def reimport_texture(self, asset_path: str, *,
                         timeout: Optional[float] = EXECUTE_TIMEOUT) -> str:
        """Reimport a texture asset from its source file.

        This is the update-textures path: the source art changes, the asset
        keeps its identity and references, and only the pixels refresh.
        """
        code = build_reimport_script(asset_path)
        return self.execute(code, timeout=timeout)

    def list_assets(self, directory: str = "/Game", *, class_name: Optional[str] = None,
                    recursive: bool = True, timeout: Optional[float] = EXECUTE_TIMEOUT) -> str:
        code = build_list_assets_script(directory, class_name, recursive)
        return self.execute(code, timeout=timeout)

    def set_texture_settings(self, asset_path: str, settings: Dict[str, Any], *,
                             timeout: Optional[float] = EXECUTE_TIMEOUT) -> str:
        """Adjust texture sampler settings on an existing asset."""
        code = build_texture_settings_script(asset_path, settings)
        return self.execute(code, timeout=timeout)

    def save(self, asset_paths: Optional[List[str]] = None, *,
             timeout: Optional[float] = EXECUTE_TIMEOUT) -> str:
        """Save specific assets, or every dirty package when given none.

        Assets changed by script are not written to disk until saved, so this
        belongs at the end of any asset pipeline.
        """
        code = build_save_script(asset_paths)
        return self.execute(code, timeout=timeout)

    def describe_operations(self) -> List[Dict[str, Any]]:
        return [self.describe(op) for op in ("enable", "execute")]


# --- generated editor-side scripts ----------------------------------------
# Each builder returns a self-contained snippet. They are kept as plain
# strings (not f-strings with embedded braces) so the generated Unreal Python
# stays readable when printed for debugging.


def build_import_texture_script(source_file: str, destination: str,
                                settings: Optional[Dict[str, Any]] = None) -> str:
    # The split is done here rather than in the generated code so the emitted
    # script contains explicit literals that are easy to read in a log.
    directory, _, asset_name = destination.rpartition("/")
    return f"""
import unreal

source_file = {source_file!r}
destination_path = {directory!r}
destination_name = {asset_name!r}
settings = {json.dumps(settings or {})}

task = unreal.AssetImportTask()
task.filename = source_file
task.destination_path = destination_path
task.destination_name = destination_name
task.automated = True
task.replace_existing = True
task.save = False

options = unreal.TextureImportOptions()
options.compression_settings = unreal.TextureCompressionSettings.TC_DEFAULT
for key, value in settings.items():
    if hasattr(options, key):
        setattr(options, key, value)
task.options = options

unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
paths = list(task.get_editor_property('imported_object_paths') or [])
if not paths:
    unreal.log_error('uefn-link: import produced no assets from ' + source_file)
else:
    unreal.log('uefn-link: imported ' + ','.join(paths))
""".strip()


def build_reimport_script(asset_path: str) -> str:
    return f"""
import unreal

asset_path = {asset_path!r}
registry = unreal.AssetRegistryHelpers.get_asset_registry()
assets = registry.get_assets_by_package_name(asset_path)
if not assets:
    unreal.log_error('uefn-link: no asset at ' + asset_path)
else:
    for data in assets:
        unreal.AssetToolsHelpers.get_asset_tools().reimport_assets([data.get_asset()])
    unreal.log('uefn-link: reimported ' + asset_path)
""".strip()


def build_list_assets_script(directory: str, class_name: Optional[str],
                             recursive: bool) -> str:
    return f"""
import json
import unreal

registry = unreal.AssetRegistryHelpers.get_asset_registry()
assets = registry.get_assets_by_path({directory!r}, recursive={recursive!r})
filter_class = {class_name!r}
out = []
for data in assets:
    name = str(data.asset_name)
    cname = str(data.asset_class_path.asset_name) if data.asset_class_path else ''
    if filter_class and filter_class.lower() not in cname.lower():
        continue
    out.append({{'name': name, 'class': cname, 'package': str(data.package_name)}})
unreal.log('uefn-link-json:' + json.dumps({{'count': len(out), 'assets': out}}))
""".strip()


def build_texture_settings_script(asset_path: str, settings: Dict[str, Any]) -> str:
    return f"""
import unreal

asset_path = {asset_path!r}
settings = {json.dumps(settings)}
texture = unreal.EditorAssetLibrary.load_asset(asset_path)
if texture is None:
    unreal.log_error('uefn-link: could not load ' + asset_path)
else:
    for key, value in settings.items():
        try:
            texture.set_editor_property(key, value)
        except Exception as exc:
            unreal.log_error('uefn-link: setting ' + key + ' failed: ' + str(exc))
    unreal.EditorAssetLibrary.save_loaded_asset(texture)
    unreal.log('uefn-link: applied settings to ' + asset_path)
""".strip()


def build_save_script(asset_paths: Optional[List[str]]) -> str:
    if asset_paths:
        return f"""
import unreal

for path in {list(asset_paths)!r}:
    asset = unreal.EditorAssetLibrary.load_asset(path)
    if asset is None:
        unreal.log_error('uefn-link: could not load ' + path)
    else:
        unreal.EditorAssetLibrary.save_loaded_asset(asset)
        unreal.log('uefn-link: saved ' + path)
""".strip()
    return """
import unreal

unreal.EditorAssetLibrary.save_directory('/Game', only_if_is_dirty=True, recursive=True)
unreal.log('uefn-link: saved dirty assets under /Game')
""".strip()


# --- result handling -------------------------------------------------------
JSON_MARKER = "uefn-link-json:"


def _json_wrapper(code: str) -> str:
    return f"""
import json

_result = None
def _uefn_body():
    global _result
{_indent(code)}
    return _result

_result = _uefn_body()
print('{JSON_MARKER}' + json.dumps(_result, default=str))
""".strip()


def _indent(code: str, spaces: int = 4) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else line for line in code.splitlines())


def _parse_embedded_json(text: str) -> Any:
    """Pull the JSON document out of a marked line, else return raw text."""
    for line in (text or "").splitlines():
        idx = line.find(JSON_MARKER)
        if idx != -1:
            payload = line[idx + len(JSON_MARKER):].strip()
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                continue
    return text
