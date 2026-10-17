"""Project-level operations: validation and saving.

UEFN validates project content, and a scripted change that touches a
non-allow-listed property fails validation. Running validation after a batch of
edits turns that into a clear error at the point of change instead of a
surprise at publish time.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from uefn_link.domains.base import Domain


class ProjectDomain(Domain):
    group = "project"

    def validate(self, *, timeout: Optional[float] = 300.0) -> str:
        return self.call_text("validate", {}, timeout=timeout)

    def save(self, *, timeout: Optional[float] = 300.0) -> str:
        return self.call_text("save", {}, timeout=timeout)

    def describe_operations(self) -> List[Dict[str, Any]]:
        return [self.describe(op) for op in ("validate", "save")]
