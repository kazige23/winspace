"""NTFS junction primitives.

A junction is a reparse point that redirects directory access from
``link`` to ``target``. We use it instead of symbolic links because
``mklink /J`` does not require admin or Developer Mode and is opaque
to most applications (they don't notice they're crossing a volume).

This module wraps four operations:

* :func:`create_junction` — uses ``cmd /c mklink /J``
* :func:`is_junction` — checks the reparse-point bit via fs.py
* :func:`read_junction_target` — uses :meth:`pathlib.Path.readlink`
* :func:`delete_junction` — deletes the junction entry, **leaves the
  target untouched**

All four guard against the common mistakes: creating a junction whose
target doesn't exist, dereferencing a non-junction, deleting something
that isn't a junction.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from winspace.core.errors import JunctionError
from winspace.core.fs import FileSystem, RealFileSystem


def create_junction(link: Path, target: Path, *, fs: FileSystem | None = None) -> None:
    """Create a junction at ``link`` pointing to ``target``.

    Both directories must exist and ``link`` must not. Raises
    :class:`JunctionError` if any of the following hold:

    * ``link`` already exists (we never overwrite blind)
    * ``target`` does not exist (mklink /J would happily create a dangling
      pointer otherwise)
    * ``target`` is not a directory
    * the platform is not Windows
    * ``mklink`` returns a non-zero exit code
    """
    fs = fs or RealFileSystem()

    if os.name != "nt":
        raise JunctionError("junctions are only available on Windows")
    if fs.exists(link):
        raise JunctionError(f"link path already exists: {link}")
    if not fs.exists(target):
        raise JunctionError(f"junction target does not exist: {target}")
    if not fs.is_dir(target):
        raise JunctionError(f"junction target is not a directory: {target}")

    # `cmd /c mklink` is the only stable way to invoke mklink from a
    # non-cmd shell on Windows. Using shell=False keeps quoting predictable.
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise JunctionError(
            f"mklink /J failed (exit {result.returncode}): "
            f"{(result.stderr or result.stdout).strip()}"
        )

    # mklink can occasionally return 0 even when the link is missing
    # (very old Windows builds, redirected stdout edge cases). Verify
    # before trusting it.
    if not fs.exists(link):
        raise JunctionError(f"mklink reported success but {link} does not exist")


def is_junction(path: Path, *, fs: FileSystem | None = None) -> bool:
    """True iff ``path`` is a directory-flavoured reparse point.

    Returns False for regular directories, files, missing paths, and
    file-symlink reparse points.
    """
    fs = fs or RealFileSystem()
    if not fs.is_reparse_point(path):
        return False
    # A junction's `is_dir` follows the link, so it returns True if the
    # target is reachable. Even if the target is missing (dangling
    # junction), the reparse bit is still set on the link itself.
    try:
        return fs.is_dir(path)
    except OSError:
        # Treat unreadable reparse points as junctions for diagnostic
        # purposes — callers can still call delete_junction on them.
        return True


def read_junction_target(path: Path, *, fs: FileSystem | None = None) -> Path:
    """Return the directory ``path`` redirects to.

    Raises :class:`JunctionError` if ``path`` is not a junction or if
    the target cannot be read.
    """
    fs = fs or RealFileSystem()
    if not is_junction(path, fs=fs):
        raise JunctionError(f"not a junction: {path}")
    try:
        return path.readlink()
    except OSError as e:
        raise JunctionError(f"failed to read junction target {path}: {e}") from e


def delete_junction(path: Path, *, fs: FileSystem | None = None) -> None:
    """Remove the junction entry. The target directory is left intact.

    Uses ``rmdir`` — on Windows this removes the reparse point without
    touching the target. Raises :class:`JunctionError` for any non-junction
    input so callers can never accidentally delete a real directory.
    """
    fs = fs or RealFileSystem()
    if not is_junction(path, fs=fs):
        raise JunctionError(f"not a junction: {path}")
    try:
        path.rmdir()
    except OSError as e:
        raise JunctionError(f"failed to delete junction {path}: {e}") from e
