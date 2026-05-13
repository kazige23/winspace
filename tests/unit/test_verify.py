"""Unit tests for :mod:`winspace.core.verify`.

The fingerprint should be:
- deterministic (same input -> same output)
- location-independent (relative paths only)
- sensitive to: missing files, extra files, size changes, renames
- insensitive to: file content changes when name and size are the same
  (we accept this trade-off; robocopy + NTFS guard content)
- fast (1000-file tree in well under a second)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from winspace.core.fs import RealFileSystem
from winspace.core.verify import (
    Fingerprint,
    FingerprintDiff,
    compare,
    fingerprint,
    fingerprint_with_timing,
)

# --- helpers -----------------------------------------------------------------


def _make_tree(root: Path, layout: dict[str, int | dict[str, Any]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name, value in layout.items():
        target = root / name
        if isinstance(value, int):
            target.write_text("x" * value)
        else:
            _make_tree(target, value)


@pytest.fixture
def fs() -> RealFileSystem:
    return RealFileSystem()


# --- baseline behaviour ------------------------------------------------------


def test_fingerprint_of_missing_root_is_empty(tmp_path: Path) -> None:
    fp = fingerprint(tmp_path / "nope")
    assert fp.file_count == 0
    assert fp.total_bytes == 0
    # The empty SHA-256 is a known constant — locking it down catches
    # accidental hash-algorithm changes.
    assert fp.tree_hash == ("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")


def test_fingerprint_of_empty_dir_is_empty(tmp_path: Path) -> None:
    (tmp_path / "void").mkdir()
    fp = fingerprint(tmp_path / "void")
    assert fp.file_count == 0
    assert fp.total_bytes == 0


def test_fingerprint_counts_files_and_bytes(tmp_path: Path) -> None:
    _make_tree(
        tmp_path / "t",
        {"a.txt": 5, "b.txt": 10, "sub": {"c.txt": 100}},
    )
    fp = fingerprint(tmp_path / "t")
    assert fp.file_count == 3
    assert fp.total_bytes == 115


# --- determinism & location independence -------------------------------------


def test_fingerprint_is_deterministic(tmp_path: Path) -> None:
    _make_tree(tmp_path / "t", {"a": 10, "b": 20, "sub": {"c": 5}})
    a = fingerprint(tmp_path / "t")
    b = fingerprint(tmp_path / "t")
    assert a == b


def test_fingerprint_is_location_independent(tmp_path: Path) -> None:
    """Identical layouts at different absolute paths produce equal fingerprints."""
    _make_tree(tmp_path / "left", {"a": 10, "sub": {"b": 5}})
    _make_tree(tmp_path / "right", {"a": 10, "sub": {"b": 5}})
    assert fingerprint(tmp_path / "left") == fingerprint(tmp_path / "right")


# --- sensitivity to structural changes --------------------------------------


def test_missing_file_changes_fingerprint(tmp_path: Path) -> None:
    _make_tree(tmp_path / "left", {"a": 10, "b": 20})
    _make_tree(tmp_path / "right", {"a": 10})
    fp_l = fingerprint(tmp_path / "left")
    fp_r = fingerprint(tmp_path / "right")
    assert fp_l != fp_r
    assert fp_l.file_count != fp_r.file_count


def test_extra_file_changes_fingerprint(tmp_path: Path) -> None:
    _make_tree(tmp_path / "left", {"a": 10})
    _make_tree(tmp_path / "right", {"a": 10, "extra": 1})
    assert fingerprint(tmp_path / "left") != fingerprint(tmp_path / "right")


def test_size_change_changes_fingerprint(tmp_path: Path) -> None:
    _make_tree(tmp_path / "left", {"a": 10})
    _make_tree(tmp_path / "right", {"a": 11})
    fp_l = fingerprint(tmp_path / "left")
    fp_r = fingerprint(tmp_path / "right")
    assert fp_l != fp_r
    assert fp_l.total_bytes != fp_r.total_bytes


def test_rename_changes_fingerprint(tmp_path: Path) -> None:
    _make_tree(tmp_path / "left", {"a.txt": 10})
    _make_tree(tmp_path / "right", {"b.txt": 10})
    fp_l = fingerprint(tmp_path / "left")
    fp_r = fingerprint(tmp_path / "right")
    assert fp_l.file_count == fp_r.file_count
    assert fp_l.total_bytes == fp_r.total_bytes
    # Only the tree-hash captures the rename.
    assert fp_l.tree_hash != fp_r.tree_hash


# --- documented insensitivity to file content -------------------------------


def test_content_change_with_same_size_is_invisible_by_design(
    tmp_path: Path,
) -> None:
    """We deliberately do NOT hash file contents — robocopy guards them."""
    a = tmp_path / "a" / "f.txt"
    a.parent.mkdir()
    a.write_text("AAAAA")
    b = tmp_path / "b" / "f.txt"
    b.parent.mkdir()
    b.write_text("BBBBB")
    assert fingerprint(tmp_path / "a") == fingerprint(tmp_path / "b")


# --- reparse-point handling --------------------------------------------------


def test_fingerprint_skips_reparse_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "linky").mkdir()
    (tmp_path / "linky" / "big.txt").write_text("a" * 9999)
    monkeypatch.setattr(
        RealFileSystem,
        "is_reparse_point",
        lambda self, p: p == tmp_path / "linky",
    )
    fp = fingerprint(tmp_path / "linky")
    assert fp.file_count == 0
    assert fp.total_bytes == 0


def test_fingerprint_skips_reparse_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_tree(
        tmp_path / "t",
        {"real.txt": 5, "junction_child": {"big.txt": 9999}},
    )
    monkeypatch.setattr(
        RealFileSystem,
        "is_reparse_point",
        lambda self, p: p == tmp_path / "t" / "junction_child",
    )
    fp = fingerprint(tmp_path / "t")
    assert fp.file_count == 1
    assert fp.total_bytes == 5


# --- error tolerance ---------------------------------------------------------


def test_fingerprint_swallows_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_tree(tmp_path / "root", {"a.txt": 10, "locked": {"b.txt": 999}})
    real_iterdir = RealFileSystem.iterdir

    def flaky(self: RealFileSystem, path: Path) -> Any:
        if path.name == "locked":
            raise PermissionError("denied")
        return real_iterdir(self, path)

    monkeypatch.setattr(RealFileSystem, "iterdir", flaky)
    fp = fingerprint(tmp_path / "root")
    assert fp.file_count == 1
    assert fp.total_bytes == 10


def test_fingerprint_swallows_stat_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_tree(tmp_path / "root", {"good.txt": 10, "bad.txt": 999})
    real_stat = RealFileSystem.stat

    def flaky(self: RealFileSystem, path: Path) -> Any:
        if path.name == "bad.txt":
            raise OSError("simulated")
        return real_stat(self, path)

    monkeypatch.setattr(RealFileSystem, "stat", flaky)
    fp = fingerprint(tmp_path / "root")
    assert fp.file_count == 1
    assert fp.total_bytes == 10


# --- compare -----------------------------------------------------------------


def test_compare_equal_fingerprints() -> None:
    a = Fingerprint(file_count=10, total_bytes=100, tree_hash="abc")
    b = Fingerprint(file_count=10, total_bytes=100, tree_hash="abc")
    diff = compare(a, b)
    assert diff == FingerprintDiff(
        same=True,
        file_count_match=True,
        total_bytes_match=True,
        tree_hash_match=True,
    )


def test_compare_pinpoints_mismatched_axes() -> None:
    a = Fingerprint(file_count=10, total_bytes=100, tree_hash="abc")
    b = Fingerprint(file_count=10, total_bytes=200, tree_hash="def")
    diff = compare(a, b)
    assert diff.same is False
    assert diff.file_count_match is True
    assert diff.total_bytes_match is False
    assert diff.tree_hash_match is False


# --- timing helper -----------------------------------------------------------


def test_fingerprint_with_timing_returns_positive_duration(
    tmp_path: Path,
) -> None:
    _make_tree(tmp_path / "t", {"a.txt": 1})
    fp, dur = fingerprint_with_timing(tmp_path / "t")
    assert fp.file_count == 1
    assert dur >= 0  # perf_counter can return 0 for trivially-small trees


# --- performance smoke -------------------------------------------------------


@pytest.mark.windows
def test_fingerprint_of_thousand_files_under_one_second(tmp_path: Path) -> None:
    """Plan §6.8 acceptance: 1000 files fingerprint in < 1s."""
    root = tmp_path / "big"
    root.mkdir()
    for i in range(1000):
        (root / f"f{i:04d}.bin").write_bytes(b"y" * 64)
    _, dur = fingerprint_with_timing(root)
    assert dur < 1.0, f"fingerprint took {dur:.3f}s, expected < 1s"
