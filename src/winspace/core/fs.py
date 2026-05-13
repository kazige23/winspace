"""Filesystem abstraction layer.

Every IO operation in winspace (mover, manifest, scanner, junction)
goes through a :class:`FileSystem` implementation. The default
:class:`RealFileSystem` is what production uses; tests substitute fakes
or run against a real ``tmp_path``.

Centralising IO here also lets us keep Windows quirks in one place —
long-path prefixing, reparse-point detection, cross-volume rename
diagnostics, and the robocopy/shutil copy fallback.
"""

from __future__ import annotations

import errno
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, runtime_checkable

from winspace.core.errors import CrossVolumeRenameError, FsError

# Robocopy exit codes 0..7 are success / informational. 8+ are failures.
# https://learn.microsoft.com/en-us/troubleshoot/windows-server/backup-and-storage/return-codes-used-robocopy-utility
_ROBOCOPY_SUCCESS_MAX = 7

# Windows file attribute bit for reparse points (junctions, symlinks, etc).
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


@runtime_checkable
class FileSystem(Protocol):
    """Every IO call winspace makes goes through one of these methods."""

    def exists(self, path: Path) -> bool: ...
    def is_dir(self, path: Path) -> bool: ...
    def is_file(self, path: Path) -> bool: ...
    def is_reparse_point(self, path: Path) -> bool: ...
    def iterdir(self, path: Path) -> Iterator[Path]: ...
    def stat(self, path: Path) -> os.stat_result: ...
    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None: ...
    def unlink(self, path: Path) -> None: ...
    def rmdir(self, path: Path) -> None: ...
    def rmtree(self, path: Path) -> None: ...
    def copytree(self, src: Path, dst: Path) -> None: ...
    def rename(self, old: Path, new: Path) -> None: ...
    def read_text(self, path: Path, encoding: str = "utf-8") -> str: ...
    def write_text_atomic(self, path: Path, content: str, encoding: str = "utf-8") -> None: ...
    def get_free_space(self, path: Path) -> int: ...


def to_long_path(path: Path) -> str:
    """Return ``path`` formatted with the Windows ``\\\\?\\`` long-path prefix.

    The prefix bypasses the legacy MAX_PATH = 260 limit and is required
    by some applications even on Windows 10+ when long path support has
    not been opted-in via the registry. The prefix is harmless on POSIX
    (we just return the resolved path) so this stays cross-platform for
    unit tests running on Linux CI.
    """
    abs_str = os.fsdecode(path.resolve())
    if os.name != "nt":
        return abs_str
    if abs_str.startswith("\\\\?\\"):
        return abs_str
    if abs_str.startswith("\\\\"):
        return "\\\\?\\UNC\\" + abs_str[2:]
    return "\\\\?\\" + abs_str


class RealFileSystem:
    """Default :class:`FileSystem` implementation backed by os / shutil."""

    # --- existence / type checks ---------------------------------------------

    def exists(self, path: Path) -> bool:
        return path.exists()

    def is_dir(self, path: Path) -> bool:
        return path.is_dir()

    def is_file(self, path: Path) -> bool:
        return path.is_file()

    def is_reparse_point(self, path: Path) -> bool:
        """True iff ``path`` is a symlink, junction, or any other reparse point.

        Uses ``lstat`` (not ``stat``) so junctions are detected even when
        their target is unreachable.
        """
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return False
        attrs = getattr(st, "st_file_attributes", 0)
        return bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT)

    # --- enumeration / inspection --------------------------------------------

    def iterdir(self, path: Path) -> Iterator[Path]:
        return path.iterdir()

    def stat(self, path: Path) -> os.stat_result:
        return os.stat(path)

    # --- creation / removal --------------------------------------------------

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        path.mkdir(parents=parents, exist_ok=exist_ok)

    def unlink(self, path: Path) -> None:
        path.unlink()

    def rmdir(self, path: Path) -> None:
        path.rmdir()

    def rmtree(self, path: Path) -> None:
        shutil.rmtree(path)

    # --- copy ----------------------------------------------------------------

    def copytree(self, src: Path, dst: Path) -> None:
        """Recursively copy ``src`` to ``dst``. ``dst`` must not exist.

        On Windows, prefers ``robocopy`` (faster, handles long paths, has
        better error reporting). Falls back to :func:`shutil.copytree` if
        robocopy is unavailable or fails with a non-recoverable error
        leaving ``dst`` empty.
        """
        if dst.exists():
            raise FsError(f"copytree destination already exists: {dst}")

        if os.name == "nt" and shutil.which("robocopy") is not None:
            try:
                self._robocopy(src, dst)
                return
            except FsError:
                # robocopy partially failed; clean dst and retry with shutil.
                if dst.exists():
                    shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)

    @staticmethod
    def _robocopy(src: Path, dst: Path) -> None:
        cmd = [
            "robocopy",
            str(src),
            str(dst),
            "/E",  # subdirs including empty
            "/COPY:DAT",  # data, attributes, timestamps (no ACL — v1 simplification)
            "/R:2",  # 2 retries on transient errors
            "/W:1",  # 1s between retries
            "/NJH",  # no job header
            "/NJS",  # no job summary
            "/NP",  # no progress %
            "/NS",  # no sizes
            "/NC",  # no class
            "/NDL",  # no directory list
            "/NFL",  # no file list
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode > _ROBOCOPY_SUCCESS_MAX:
            raise FsError(
                f"robocopy failed (exit {result.returncode}): "
                f"{(result.stderr or result.stdout).strip()}"
            )

    # --- rename --------------------------------------------------------------

    def rename(self, old: Path, new: Path) -> None:
        """Atomic same-volume rename. Cross-volume raises a specific error.

        Callers that need cross-volume moves should detect
        :class:`CrossVolumeRenameError` and use copy+delete via
        :meth:`copytree` + :meth:`rmtree`.
        """
        try:
            os.rename(old, new)
        except OSError as e:
            cross_volume = e.errno == errno.EXDEV or getattr(e, "winerror", None) == 17
            if cross_volume:
                raise CrossVolumeRenameError(f"cannot rename across volumes: {old} -> {new}") from e
            raise FsError(f"rename failed: {old} -> {new}: {e}") from e

    # --- text IO -------------------------------------------------------------

    def read_text(self, path: Path, encoding: str = "utf-8") -> str:
        return path.read_text(encoding=encoding)

    def write_text_atomic(self, path: Path, content: str, encoding: str = "utf-8") -> None:
        """Write text atomically: write to ``<path>.tmp`` then ``os.replace``.

        Atomicity is at the filesystem-entry level: a reader either sees
        the previous content or the full new content, never a partial
        write. We rely on ``os.replace`` which is atomic on both POSIX
        and NTFS.
        """
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(content, encoding=encoding)
        os.replace(tmp, path)

    # --- volume info ---------------------------------------------------------

    def get_free_space(self, path: Path) -> int:
        """Free bytes on the volume that contains ``path``."""
        usage = shutil.disk_usage(path)
        return usage.free
