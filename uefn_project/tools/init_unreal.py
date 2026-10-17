"""UEFN startup script: runs inside the editor, not on the host.

Place this file (or a copy) where UEFN looks for startup Python. UEFN executes
``init_unreal.py`` from the project's ``Content/Python`` folder when Python
editor scripting is enabled, which makes it the hook for enabling the MCP
toolset and registering project-local helpers without a GUI step.

This module deliberately does nothing destructive: it inspects and reports.
Anything that changes project content belongs in a script a human runs
deliberately.
"""
import unreal


def _log(message):
    unreal.log("uefn-link: " + str(message))


def _warn(message):
    unreal.log_warning("uefn-link: " + str(message))


def report_python_state():
    """Confirm Python scripting is live and report the engine version.

    If this never runs, Python editor scripting is not enabled for the project,
    which is the most common reason a host-side link cannot connect.
    """
    try:
        version = unreal.SystemLibrary.get_engine_version()
    except Exception as exc:  # pragma: no cover - depends on editor build
        version = "unknown (%s)" % exc
    _log("python editor scripting is active; engine " + str(version))
    return version


def report_project_paths():
    """Log the content roots so a host script can confirm it points at the
    right project when several islands are open."""
    try:
        project_dir = unreal.Paths.project_content_dir()
        _log("project content dir: " + str(project_dir))
        return project_dir
    except Exception as exc:  # pragma: no cover
        _warn("could not read project content dir: %s" % exc)
        return None


def main():
    report_python_state()
    report_project_paths()
    _log("startup hooks complete; MCP toolsets are published by the editor")


main()
