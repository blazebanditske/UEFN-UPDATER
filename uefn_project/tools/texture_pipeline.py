"""Texture pipeline, run inside UEFN by the link or from the editor console.

Two operations matter for "update textures": importing a new image as a
texture asset, and reimporting an existing asset after its source art changed.
Reimport is the one to prefer during iteration, because the asset keeps its
identity and every material that references it updates automatically.

Rules this file follows, from Epic's UEFN Python guidance:

* assets are touched only through the Unreal Python API, never through
  ``os``/``shutil``, because moving asset files on disk breaks internal
  content references;
* only properties visible in the UEFN UI are set, because anything else fails
  project validation even when the write appears to succeed.

Run standalone in the editor with: Tools > Execute Python Script.
"""
import json

import unreal

JSON_MARKER = "uefn-link-json:"


# --- import -----------------------------------------------------------------
def import_texture(source_file, destination_path, destination_name, settings=None):
    """Import an image file as a texture asset under ``destination_path``.

    ``destination_path`` is a content path such as ``/Game/Textures`` and
    ``destination_name`` is the asset name without extension, such as
    ``T_Rock``. Returns the imported object paths.
    """
    task = unreal.AssetImportTask()
    task.filename = source_file
    task.destination_path = destination_path
    task.destination_name = destination_name
    task.automated = True
    task.replace_existing = True
    task.save = False

    options = unreal.TextureImportOptions()
    options.compression_settings = unreal.TextureCompressionSettings.TC_DEFAULT
    for key, value in (settings or {}).items():
        if hasattr(options, key):
            setattr(options, key, value)
        else:
            unreal.log_warning("uefn-link: unknown import setting " + str(key))
    task.options = options

    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    paths = list(task.get_editor_property("imported_object_paths") or [])
    if not paths:
        unreal.log_error("uefn-link: import produced no assets from " + str(source_file))
    return paths


def reimport_texture(asset_path):
    """Reimport a texture from its source art, preserving all references.

    This is the update path: change the source image, reimport, and every
    material using the texture picks up the new pixels.
    """
    asset = unreal.EditorAssetLibrary.load_asset(asset_path)
    if asset is None:
        unreal.log_error("uefn-link: could not load " + str(asset_path))
        return False
    unreal.AssetToolsHelpers.get_asset_tools().reimport_assets([asset])
    unreal.EditorAssetLibrary.save_loaded_asset(asset)
    unreal.log("uefn-link: reimported " + str(asset_path))
    return True


# --- settings ---------------------------------------------------------------
def apply_texture_settings(asset_path, settings):
    """Apply sampler settings to an existing texture asset.

    Unknown properties are reported rather than raised: one rejected setting
    should not abandon the rest of a batch.
    """
    texture = unreal.EditorAssetLibrary.load_asset(asset_path)
    if texture is None:
        unreal.log_error("uefn-link: could not load " + str(asset_path))
        return False
    for key, value in (settings or {}).items():
        try:
            texture.set_editor_property(key, value)
        except Exception as exc:
            unreal.log_error(
                "uefn-link: setting %s on %s failed: %s" % (key, asset_path, exc)
            )
    unreal.EditorAssetLibrary.save_loaded_asset(texture)
    return True


def list_textures(directory="/Game", recursive=True):
    """Inventory texture assets so a caller can confirm names before writing."""
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    out = []
    for data in registry.get_assets_by_path(directory, recursive=recursive):
        class_name = ""
        if data.asset_class_path:
            class_name = str(data.asset_class_path.asset_name)
        if "Texture" not in class_name:
            continue
        out.append({
            "name": str(data.asset_name),
            "class": class_name,
            "package": str(data.package_name),
        })
    return out


def save_all():
    """Write dirty assets under /Game to disk."""
    unreal.EditorAssetLibrary.save_directory("/Game", only_if_is_dirty=True,
                                             recursive=True)
    unreal.log("uefn-link: saved dirty assets under /Game")


# --- machine-readable entry point ------------------------------------------
def main():
    """Print one JSON document describing the current texture inventory.

    The host client reads the marked line, so results are parsed rather than
    scraped out of LogPython output.
    """
    try:
        payload = {"textures": list_textures("/Game"), "ok": True}
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}
    print(JSON_MARKER + json.dumps(payload, default=str))


if __name__ == "__main__":
    main()
