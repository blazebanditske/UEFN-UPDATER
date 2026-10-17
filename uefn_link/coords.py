"""Coordinate conversion between UEFN's LUF axes and XYZ.

Epic's UEFN MCP known-issues list calls this out explicitly: the Python-facing
toolsets report transforms in XYZ while the editor itself uses a Left-Up-Forward
convention, and the mismatch is a common source of misplaced actors. Rather
than leaving each caller to remember the mapping, conversions live here.

UEFN/Unreal world convention: +X forward, +Y right, +Z up.
LUF convention: Forward, Left, Up -> (X, -Y, Z) relative to Unreal.

Keeping both directions in one place means a bug here is fixed once.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

Number = float


def xyz_to_luf(xyz: Sequence[Number]) -> Tuple[float, float, float]:
    """Convert an XYZ (Unreal) triple to (forward, left, up)."""
    x, y, z = _triple(xyz)
    return (x, -y, z)


def luf_to_xyz(luf: Sequence[Number]) -> Tuple[float, float, float]:
    """Convert a (forward, left, up) triple to XYZ (Unreal)."""
    forward, left, up = _triple(luf)
    return (forward, -left, up)


def transform_xyz_to_luf(transform: Dict) -> Dict:
    """Swap the translation/rotation frame of a transform dict in place-safe copy.

    Only ``location`` is converted: Unreal rotations are already expressed in
    the same axis order for both frames, so converting them would introduce an
    error rather than remove one.
    """
    out = dict(transform or {})
    for key in ("location", "Location", "translation", "Translation", "position", "Position"):
        if key in out and isinstance(out[key], (list, tuple)) and len(out[key]) == 3:
            out[key] = list(xyz_to_luf(out[key]))
    return out


def transform_luf_to_xyz(transform: Dict) -> Dict:
    """Inverse of :func:`transform_xyz_to_luf`."""
    out = dict(transform or {})
    for key in ("location", "Location", "translation", "Translation", "position", "Position"):
        if key in out and isinstance(out[key], (list, tuple)) and len(out[key]) == 3:
            out[key] = list(luf_to_xyz(out[key]))
    return out


def _triple(values: Sequence[Number]) -> Tuple[float, float, float]:
    seq: List[float] = [float(v) for v in values]
    if len(seq) != 3:
        raise ValueError(f"expected 3 components, got {len(seq)}: {values!r}")
    return (seq[0], seq[1], seq[2])
