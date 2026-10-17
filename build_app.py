"""Build the UEFN Link app into a single double-clickable executable.

Run this on the platform you want to ship for. PyInstaller does not
cross-compile, so:

    Windows  -> run on Windows   -> dist/UEFN Link.exe
    macOS    -> run on macOS     -> dist/UEFN Link.app
    Linux    -> run on Linux     -> dist/UEFN Link

Usage:
    python build_app.py            # build for this platform
    python build_app.py --clean    # remove previous build output first
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
ENTRY = os.path.join(ROOT, "uefn_link_app.py")
NAME = "UEFN Link"


def ensure_pyinstaller() -> bool:
    try:
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            capture_output=True, check=True,
        )
        return True
    except (OSError, subprocess.CalledProcessError):
        print("PyInstaller is not installed. Install it with:\n")
        print(f"    {sys.executable} -m pip install pyinstaller\n")
        return False


def clean() -> None:
    for path in ("build", "dist", f"{NAME}.spec"):
        target = os.path.join(ROOT, path)
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
            print(f"removed {path}/")
        elif os.path.isfile(target):
            os.remove(target)
            print(f"removed {path}")


def build() -> int:
    # --onefile so it is genuinely one thing to double-click. The app is stdlib
    # only, so there is nothing to bundle beyond the interpreter itself.
    args = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", NAME,
        "--noconfirm",
        "--clean",
        # The app talks to loopback only; these keep it from pulling in extras.
        "--exclude-module", "tkinter",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
        ENTRY,
    ]
    print("building:", " ".join(args), "\n")
    proc = subprocess.run(args, cwd=ROOT)
    if proc.returncode != 0:
        print("\nBuild failed.")
        return proc.returncode

    print("\nBuild succeeded.")
    dist = os.path.join(ROOT, "dist")
    if os.path.isdir(dist):
        for name in sorted(os.listdir(dist)):
            full = os.path.join(dist, name)
            size = os.path.getsize(full) / 1_048_576 if os.path.isfile(full) else 0
            print(f"  {name}  ({size:.1f} MB)" if size else f"  {name}/")
    print("\nDouble-click it, or run it, on a machine that has UEFN.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Build {NAME} as a standalone app.")
    parser.add_argument("--clean", action="store_true", help="remove build output first")
    args = parser.parse_args()

    if args.clean:
        clean()
    if not ensure_pyinstaller():
        return 1
    return build()


if __name__ == "__main__":
    sys.exit(main())
