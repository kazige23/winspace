"""Unit tests for :mod:`winspace.core.fs`.

These tests run against a real filesystem inside ``tmp_path`` rather
than an in-memory fake — the abstraction's whole point is to call real
OS APIs, so faking them would prove nothing. Cross-platform behavior
is exercised on every runner; Windows-specific paths (robocopy, the
reparse-point bit) are covered with markers / monkeypatching so they
do not block CI on POSIX.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Any

import pytest

from winspace.core.errors import CrossVolumeRenameError, FsError
from winspace.core.fs import FileSystem, RealFileSystem, to_long_path

# --- shared fixtures ---------------------------------------------------------


@pytest.fixture
def fs() -> RealFileSystem:
    return RealFileSystem()


@pytest.fixture
def small_tree(tmp_path: Path) -> Path:
    """Create a small directory tree and return its root.

    Layout::

        root/
            a.txt           (5 bytes)
            b.txt           (10 bytes)
            sub/
                c.txt       (3 bytes)
                empty/
    """
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("hello")
    (root / "b.txt").write_text("helloworld")
    sub = root / "sub"
    sub.mkdir()
    (sub / "c.txt").write_text("abc")
    (sub / "empty").mkdir()
    return root


# --- protocol check ----------------------------------------------------------


def test_real_filesystem_satisfies_protocol(fs: RealFileSystem) -> None:
    assert isinstance(fs, FileSystem)


# --- existence / type checks -------------------------------------------------


def test_exists_returns_true_for_present_paths(fs: RealFileSystem, small_tree: Path) -> None:
    assert fs.exists(small_tree)
    assert fs.exists(small_tree / "a.txt")


def test_exists_returns_false_for_missing(fs: RealFileSystem, tmp_path: Path) -> None:
    assert not fs.exists(tmp_path / "nope")


def test_is_dir_and_is_file(fs: RealFileSystem, small_tree: Path) -> None:
    assert fs.is_dir(small_tree)
    assert fs.is_dir(small_tree / "sub")
    assert not fs.is_dir(small_tree / "a.txt")
    assert fs.is_file(small_tree / "a.txt")
    assert not fs.is_file(small_tree)


def test_is_reparse_point_false_for_regular_paths(fs: RealFileSystem, small_tree: Path) -> None:
    assert not fs.is_reparse_point(small_tree)
    assert not fs.is_reparse_point(small_tree / "a.txt")


def test_is_reparse_point_false_for_missing(fs: RealFileSystem, tmp_path: Path) -> None:
    assert not fs.is_reparse_point(tmp_path / "does-not-exist")


# --- enumeration -------------------------------------------------------------


def test_iterdir_yields_immediate_children_only(fs: RealFileSystem, small_tree: Path) -> None:
    names = sorted(p.name for p in fs.iterdir(small_tree))
    assert names == ["a.txt", "b.txt", "sub"]


def test_stat_returns_size(fs: RealFileSystem, small_tree: Path) -> None:
    st = fs.stat(small_tree / "b.txt")
    assert st.st_size == 10


# --- creation / removal ------------------------------------------------------


def test_mkdir_creates_directory(fs: RealFileSystem, tmp_path: Path) -> None:
    target = tmp_path / "fresh"
    fs.mkdir(target)
    assert target.is_dir()


def test_mkdir_parents_and_exist_ok(fs: RealFileSystem, tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "c"
    fs.mkdir(target, parents=True)
    fs.mkdir(target, exist_ok=True)  # second call is a no-op
    assert target.is_dir()


def test_unlink_removes_file(fs: RealFileSystem, small_tree: Path) -> None:
    fs.unlink(small_tree / "a.txt")
    assert not (small_tree / "a.txt").exists()


def test_rmdir_removes_empty_dir(fs: RealFileSystem, small_tree: Path) -> None:
    fs.rmdir(small_tree / "sub" / "empty")
    assert not (small_tree / "sub" / "empty").exists()


def test_rmtree_removes_full_tree(fs: RealFileSystem, small_tree: Path) -> None:
    fs.rmtree(small_tree)
    assert not small_tree.exists()


# --- copy --------------------------------------------------------------------


def test_copytree_recursive(fs: RealFileSystem, small_tree: Path, tmp_path: Path) -> None:
    dst = tmp_path / "copy"
    fs.copytree(small_tree, dst)
    assert (dst / "a.txt").read_text() == "hello"
    assert (dst / "sub" / "c.txt").read_text() == "abc"
    assert (dst / "sub" / "empty").is_dir()


def test_copytree_refuses_existing_destination(
    fs: RealFileSystem, small_tree: Path, tmp_path: Path
) -> None:
    dst = tmp_path / "copy"
    dst.mkdir()
    with pytest.raises(FsError, match="already exists"):
        fs.copytree(small_tree, dst)


def test_copytree_falls_back_when_robocopy_unavailable(
    fs: RealFileSystem,
    small_tree: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force the shutil fallback path even on Windows."""
    import winspace.core.fs as fs_module

    monkeypatch.setattr(fs_module.shutil, "which", lambda _: None)
    dst = tmp_path / "copy"
    fs.copytree(small_tree, dst)
    assert (dst / "a.txt").read_text() == "hello"


def test_copytree_falls_back_when_robocopy_errors(
    fs: RealFileSystem,
    small_tree: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If robocopy reports failure, shutil takes over and the copy still succeeds."""
    import winspace.core.fs as fs_module

    def fake_robocopy(_src: Path, _dst: Path) -> None:
        raise FsError("simulated robocopy failure")

    monkeypatch.setattr(fs_module.shutil, "which", lambda _: "fake.exe")
    monkeypatch.setattr(RealFileSystem, "_robocopy", staticmethod(fake_robocopy))
    dst = tmp_path / "copy"
    fs.copytree(small_tree, dst)
    assert (dst / "a.txt").read_text() == "hello"


# --- rename ------------------------------------------------------------------


def test_rename_same_volume(fs: RealFileSystem, small_tree: Path) -> None:
    new_path = small_tree.parent / "renamed"
    fs.rename(small_tree, new_path)
    assert new_path.is_dir()
    assert (new_path / "a.txt").read_text() == "hello"


def test_rename_cross_volume_raises_specific_error(
    fs: RealFileSystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synthesize a cross-volume OSError without needing two physical drives."""
    src = tmp_path / "src.txt"
    src.write_text("x")
    dst = tmp_path / "dst.txt"

    def fake_rename(_old: Any, _new: Any) -> None:
        raise OSError(errno.EXDEV, "fake cross-device")

    monkeypatch.setattr(os, "rename", fake_rename)
    with pytest.raises(CrossVolumeRenameError):
        fs.rename(src, dst)


def test_rename_cross_volume_via_winerror(
    fs: RealFileSystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Windows the errno may be missing — winerror 17 also triggers detection."""
    src = tmp_path / "src.txt"
    src.write_text("x")
    dst = tmp_path / "dst.txt"

    def fake_rename(_old: Any, _new: Any) -> None:
        err = OSError("fake")
        # winerror is set as an attribute by the Python runtime on Windows;
        # we set it manually so this test runs on POSIX CI too.
        err.winerror = 17  # type: ignore[attr-defined]
        raise err

    monkeypatch.setattr(os, "rename", fake_rename)
    with pytest.raises(CrossVolumeRenameError):
        fs.rename(src, dst)


def test_rename_other_oserror_becomes_fs_error(
    fs: RealFileSystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tmp_path / "src.txt"
    src.write_text("x")
    dst = tmp_path / "dst.txt"

    def fake_rename(_old: Any, _new: Any) -> None:
        raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(os, "rename", fake_rename)
    with pytest.raises(FsError, match="rename failed"):
        fs.rename(src, dst)


# --- text IO -----------------------------------------------------------------


def test_read_text(fs: RealFileSystem, small_tree: Path) -> None:
    assert fs.read_text(small_tree / "a.txt") == "hello"


def test_write_text_atomic_writes_full_content(fs: RealFileSystem, tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    fs.write_text_atomic(target, "新内容")
    assert target.read_text(encoding="utf-8") == "新内容"


def test_write_text_atomic_replaces_existing(fs: RealFileSystem, tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    target.write_text("old")
    fs.write_text_atomic(target, "new")
    assert target.read_text() == "new"
    # No leftover .tmp file
    assert not (tmp_path / "out.txt.tmp").exists()


# --- free space --------------------------------------------------------------


def test_get_free_space_returns_positive_int(fs: RealFileSystem, tmp_path: Path) -> None:
    free = fs.get_free_space(tmp_path)
    assert isinstance(free, int)
    assert free > 0


# --- long-path helper --------------------------------------------------------


def test_to_long_path_idempotent_on_prefixed(tmp_path: Path) -> None:
    # On POSIX the helper returns the resolved absolute path unchanged;
    # on Windows it adds the \\?\ prefix. Either way it must be idempotent
    # when applied twice.
    once = to_long_path(tmp_path)
    twice = to_long_path(Path(once)) if os.name != "nt" else once
    assert twice == once


@pytest.mark.windows
def test_to_long_path_prefixes_drive_on_windows(tmp_path: Path) -> None:
    r"""On Windows the helper must add \\?\ to a drive-letter path."""
    result = to_long_path(tmp_path)
    assert result.startswith("\\\\?\\")


@pytest.mark.windows
def test_to_long_path_handles_unc_share(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r"""UNC paths (``\\server\share\...``) must be transformed to ``\\?\UNC\``."""
    fake = Path(r"\\fileserver\public\folder")

    def fake_resolve(self: Path, *_: object, **__: object) -> Path:
        return fake

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    result = to_long_path(fake)
    assert result == "\\\\?\\UNC\\fileserver\\public\\folder"


def test_to_long_path_idempotent_when_already_prefixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r"""A path that already has ``\\?\`` returns unchanged on Windows."""
    pre = Path("\\\\?\\C:\\already\\prefixed")

    def fake_resolve(self: Path, *_: object, **__: object) -> Path:
        return pre

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    monkeypatch.setattr(os, "name", "nt")
    result = to_long_path(pre)
    assert result == "\\\\?\\C:\\already\\prefixed"


# --- additional copytree branch coverage -------------------------------------


def test_copytree_cleans_partial_destination_on_fallback(
    fs: RealFileSystem,
    small_tree: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If robocopy partially creates dst and then fails, the partial tree is removed
    before the shutil fallback runs (otherwise shutil.copytree would refuse).
    """
    import winspace.core.fs as fs_module

    def half_baked_robocopy(_src: Path, dst: Path) -> None:
        # Leave a stub directory behind to simulate a partial run.
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "junk").write_text("partial")
        raise FsError("simulated mid-flight robocopy failure")

    monkeypatch.setattr(fs_module.shutil, "which", lambda _: "fake.exe")
    monkeypatch.setattr(RealFileSystem, "_robocopy", staticmethod(half_baked_robocopy))

    dst = tmp_path / "copy"
    fs.copytree(small_tree, dst)
    # The shutil fallback ran and produced the real tree.
    assert (dst / "a.txt").read_text() == "hello"
    # The "junk" stub got cleaned up.
    assert not (dst / "junk").exists()


def test_robocopy_raises_fs_error_on_failure_exit(
    fs: RealFileSystem,
    small_tree: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_robocopy must convert exit codes >= 8 into FsError with stderr context."""
    import subprocess as _subprocess

    import winspace.core.fs as fs_module

    class FakeResult:
        returncode = 16
        stderr = "fake catastrophic robocopy failure"
        stdout = ""

    def fake_run(*_a: object, **_kw: object) -> FakeResult:
        return FakeResult()

    monkeypatch.setattr(fs_module.subprocess, "run", fake_run)
    # We deliberately bypass the public copytree to test the internal path.
    with pytest.raises(FsError, match="robocopy failed"):
        RealFileSystem._robocopy(small_tree, tmp_path / "copy")

    # Silence unused-import lint in case the future evolves.
    assert _subprocess is fs_module.subprocess
