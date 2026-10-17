"""Verse source: read, edit, search, and compile.

Verse is the gameplay language, so this is where "create new mechanics" and
"fix bugs" actually land. The toolset is sandboxed to the creator's project by
UEFN, so paths are project-relative.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from uefn_link.domains.base import Domain


class VerseDomain(Domain):
    group = "verse"

    # -- reading --------------------------------------------------------
    def list_files(self, *, timeout: Optional[float] = None) -> str:
        return self.call_text("list_files", {}, timeout=timeout)

    def read(self, path: str, *, timeout: Optional[float] = None) -> str:
        return self.call_text("read_file", {"path": path}, timeout=timeout)

    def grep(self, query: str, *, path: Optional[str] = None,
             timeout: Optional[float] = None) -> str:
        args: Dict[str, Any] = {"query": query}
        if path:
            args["path"] = path
        return self.call_text("grep", args, timeout=timeout)

    # -- writing --------------------------------------------------------
    def write(self, path: str, content: str, *, timeout: Optional[float] = None) -> str:
        """Create or overwrite a Verse file."""
        return self.call_text(
            "write_file", {"path": path, "content": content}, timeout=timeout
        )

    def replace(self, path: str, find: str, replacement: str, *,
                timeout: Optional[float] = None) -> str:
        """Targeted edit that leaves the rest of the file untouched."""
        return self.call_text(
            "replace",
            {"path": path, "find": find, "replace": replacement},
            timeout=timeout,
        )

    def delete(self, path: str, *, timeout: Optional[float] = None) -> str:
        return self.call_text("delete_file", {"path": path}, timeout=timeout)

    def create_directory(self, path: str, *, timeout: Optional[float] = None) -> str:
        return self.call_text("create_directory", {"path": path}, timeout=timeout)

    # -- build ----------------------------------------------------------
    def compile(self, *, timeout: Optional[float] = 600.0) -> str:
        """Trigger a Verse build and return the compiler output.

        A build is the only honest way to know whether generated code is
        valid; UEFN reports errors here rather than at file-write time.
        """
        return self.call_text("compile", {}, timeout=timeout)

    # -- composite ------------------------------------------------------
    def apply_and_build(self, path: str, content: str, *,
                        timeout: Optional[float] = 600.0) -> Dict[str, Any]:
        """Write a file then compile, reporting both steps separately.

        Returning both results keeps a failure attributable: a write error and
        a compile error need different responses.
        """
        write_result = self.write(path, content)
        build_result = self.compile(timeout=timeout)
        return {
            "path": path,
            "written": write_result,
            "compiled": build_result,
            "build_ok": _build_ok(build_result),
        }

    def describe_operations(self) -> List[Dict[str, Any]]:
        out = []
        for op in ("list_files", "read_file", "write_file", "replace", "grep", "compile"):
            out.append(self.describe(op))
        return out


_ERROR_MARKERS = (
    "error", "failed", "failure", "compile error", "errors:", "1 error",
)


def _build_ok(output: str) -> bool:
    """Best-effort read of a build result.

    Verse tooling reports success in prose, so this checks for error markers
    and requires an explicit success signal. An unrecognised message counts as
    not-ok, which keeps a caller from treating silence as success.
    """
    if not output:
        return False
    low = output.lower()
    if "0 error" in low or "0 errors" in low:
        return True
    if any(marker in low for marker in _ERROR_MARKERS):
        return False
    return any(word in low for word in ("succeed", "success", "built", "up to date", "complete"))
