"""Unit tests for :mod:`winspace.core.junction`.

The happy-path tests actually create junctions via ``mklink /J``, so
they only run on Windows. Error-path tests that exercise the guard
clauses work cross-platform (they monkeypatch fs.* / subprocess.run
rather than touching real reparse points).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from winspace.core.errors import JunctionError
from winspace.core.fs import RealFileSystem
from winspace.core.junction import (
    create_junction,
    delete_junction,
    is_junction,
    read_junction_target,
)

windows_only = pytest.mark.skipif(os.name != "nt", reason="junctions are only available on Windows")


# --- happy path: real mklink /J cycle ----------------------------------------


@windows_only
@pytest.mark.windows
def test_full_lifecycle_create_read_delete(tmp_path: Path) -> None:
    target = tmp_path / "real-data"
    target.mkdir()
    (target / "marker.txt").write_text("hello-from-target")

    link = tmp_path / "link-to-data"
    create_junction(link, target)

    # Junction reports itself as such, target survives, contents readable.
    assert is_junction(link)
    assert link.exists()
    assert (link / "marker.txt").read_text() == "hello-from-target"
    # readlink may return a `\\?\` prefixed form on Windows; samefile()
    # normalises both sides so we compare what the path POINTS AT, not
    # the exact string representation.
    assert read_junction_target(link).samefile(target)

    delete_junction(link)

    # Junction gone; target intact.
    assert not link.exists()
    assert target.exists()
    assert (target / "marker.txt").read_text() == "hello-from-target"


@windows_only
@pytest.mark.windows
def test_is_junction_is_false_for_regular_directory(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    assert is_junction(tmp_path / "plain") is False


@windows_only
@pytest.mark.windows
def test_is_junction_is_false_for_regular_file(tmp_path: Path) -> None:
    (tmp_path / "plain.txt").write_text("hi")
    assert is_junction(tmp_path / "plain.txt") is False


@windows_only
@pytest.mark.windows
def test_is_junction_is_false_for_missing_path(tmp_path: Path) -> None:
    assert is_junction(tmp_path / "no-such-thing") is False


# --- guards on create --------------------------------------------------------


def test_create_rejects_existing_link(tmp_path: Path) -> None:
    target = tmp_path / "t"
    target.mkdir()
    link = tmp_path / "already-there"
    link.mkdir()
    with pytest.raises(JunctionError, match="already exists"):
        create_junction(link, target)


def test_create_rejects_missing_target(tmp_path: Path) -> None:
    target = tmp_path / "vanished"
    link = tmp_path / "link"
    with pytest.raises(JunctionError, match="target does not exist"):
        create_junction(link, target)


def test_create_rejects_target_that_is_a_file(tmp_path: Path) -> None:
    target = tmp_path / "not-a-dir.txt"
    target.write_text("hi")
    link = tmp_path / "link"
    with pytest.raises(JunctionError, match="not a directory"):
        create_junction(link, target)


def test_create_rejects_non_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "t"
    target.mkdir()
    link = tmp_path / "link"
    monkeypatch.setattr(os, "name", "posix")
    with pytest.raises(JunctionError, match="only available on Windows"):
        create_junction(link, target)


def test_create_propagates_mklink_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "t"
    target.mkdir()
    link = tmp_path / "link"

    class FakeResult:
        returncode = 1
        stdout = ""
        stderr = "Local NTFS volumes are required to complete the operation."

    def fake_run(*_a: object, **_kw: object) -> FakeResult:
        return FakeResult()

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(JunctionError, match="mklink /J failed"):
        create_junction(link, target)


def test_create_detects_silent_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If mklink returns 0 but the link wasn't actually created, we still raise."""
    target = tmp_path / "t"
    target.mkdir()
    link = tmp_path / "link-that-wont-appear"

    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(*_a: object, **_kw: object) -> FakeResult:
        return FakeResult()

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(JunctionError, match="success but"):
        create_junction(link, target)


# --- guards on read / delete -------------------------------------------------


def test_read_target_rejects_non_junction(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(JunctionError, match="not a junction"):
        read_junction_target(plain)


def test_delete_rejects_non_junction(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(JunctionError, match="not a junction"):
        delete_junction(plain)


def test_read_target_propagates_readlink_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_path = tmp_path / "fake-junction"
    fake_path.mkdir()

    monkeypatch.setattr(RealFileSystem, "is_reparse_point", lambda self, p: True)
    monkeypatch.setattr(RealFileSystem, "is_dir", lambda self, p: True)

    def boom(self: Path) -> Path:
        raise OSError("readlink failed")

    monkeypatch.setattr(Path, "readlink", boom)
    with pytest.raises(JunctionError, match="failed to read junction target"):
        read_junction_target(fake_path)


def test_delete_propagates_rmdir_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_path = tmp_path / "fake-junction"
    fake_path.mkdir()

    monkeypatch.setattr(RealFileSystem, "is_reparse_point", lambda self, p: True)
    monkeypatch.setattr(RealFileSystem, "is_dir", lambda self, p: True)

    def boom(self: Path) -> None:
        raise OSError("rmdir denied")

    monkeypatch.setattr(Path, "rmdir", boom)
    with pytest.raises(JunctionError, match="failed to delete junction"):
        delete_junction(fake_path)


# --- is_junction OSError tolerance ------------------------------------------


def test_is_junction_treats_unreadable_reparse_as_junction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dangling junction whose target is unreachable still reports as junction.

    This matters for the doctor command and for delete_junction — both must
    work on broken junctions.
    """
    fake = tmp_path / "dangling"
    fake.mkdir()  # actual dir, only the predicates lie

    monkeypatch.setattr(RealFileSystem, "is_reparse_point", lambda self, p: True)

    def boom_is_dir(self: RealFileSystem, p: Path) -> bool:
        raise OSError("target unreachable")

    monkeypatch.setattr(RealFileSystem, "is_dir", boom_is_dir)
    assert is_junction(fake) is True


def test_is_junction_false_when_not_reparse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setattr(RealFileSystem, "is_reparse_point", lambda self, p: False)
    assert is_junction(plain) is False


# --- default fs argument -----------------------------------------------------


def test_default_fs_is_real_filesystem(tmp_path: Path) -> None:
    """When no fs argument is passed, a RealFileSystem must be used.

    Verified by ensuring the call doesn't crash on a regular tmp_path —
    a fake fs without is_reparse_point would have already broken.
    """
    plain = tmp_path / "plain"
    plain.mkdir()
    # No fs= argument; if RealFileSystem isn't substituted, AttributeError.
    assert is_junction(plain) is False
