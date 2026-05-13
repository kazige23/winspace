"""Unit tests for :mod:`winspace.core.scanner`.

These exercise both the workhorse :func:`directory_size` and the
top-level :func:`scan`. Reparse-point handling is verified by patching
:meth:`FileSystem.is_reparse_point` so the tests don't need junction
creation rights (those land in Task 6).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from winspace.core.fs import RealFileSystem
from winspace.core.scanner import ScanEntry, directory_size, scan

# --- helpers / fixtures ------------------------------------------------------


@pytest.fixture
def fs() -> RealFileSystem:
    return RealFileSystem()


def _make_tree(root: Path, layout: dict[str, int | dict[str, Any]]) -> None:
    """Recursively materialise ``layout`` under ``root``.

    Values that are ints become files of that size (filled with 'x').
    Dict values become subdirectories.
    """
    root.mkdir(parents=True, exist_ok=True)
    for name, value in layout.items():
        target = root / name
        if isinstance(value, int):
            target.write_text("x" * value)
        else:
            _make_tree(target, value)


# --- directory_size: baseline cases ------------------------------------------


def test_directory_size_of_flat_dir(fs: RealFileSystem, tmp_path: Path) -> None:
    _make_tree(tmp_path / "a", {"f1.txt": 5, "f2.txt": 10})
    assert directory_size(tmp_path / "a", fs=fs) == 15


def test_directory_size_of_nested_tree(fs: RealFileSystem, tmp_path: Path) -> None:
    _make_tree(
        tmp_path / "proj",
        {
            "a.txt": 100,
            "sub": {
                "b.txt": 50,
                "deep": {"c.txt": 25},
            },
            "empty": {},
        },
    )
    assert directory_size(tmp_path / "proj", fs=fs) == 175


def test_directory_size_of_empty_dir(fs: RealFileSystem, tmp_path: Path) -> None:
    (tmp_path / "void").mkdir()
    assert directory_size(tmp_path / "void", fs=fs) == 0


def test_directory_size_of_missing_path_returns_zero(fs: RealFileSystem, tmp_path: Path) -> None:
    assert directory_size(tmp_path / "does-not-exist", fs=fs) == 0


# --- directory_size: reparse-point handling ----------------------------------


def test_directory_size_skips_reparse_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If the root itself is a reparse point, size is 0 (don't follow)."""
    (tmp_path / "linky").mkdir()
    (tmp_path / "linky" / "victim.txt").write_text("a" * 1000)
    fs = RealFileSystem()
    monkeypatch.setattr(
        RealFileSystem,
        "is_reparse_point",
        lambda self, p: p == tmp_path / "linky",
    )
    assert directory_size(tmp_path / "linky", fs=fs) == 0


def test_directory_size_skips_reparse_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reparse-point sub-directory contributes nothing — it is not recursed
    and its own bytes are not added.
    """
    _make_tree(tmp_path / "root", {"a.txt": 5, "junction_child": {"big.txt": 9999}})
    fs = RealFileSystem()
    monkeypatch.setattr(
        RealFileSystem,
        "is_reparse_point",
        lambda self, p: p == tmp_path / "root" / "junction_child",
    )
    assert directory_size(tmp_path / "root", fs=fs) == 5


# --- directory_size: error recovery ------------------------------------------


def test_directory_size_recovers_from_permission_error(
    fs: RealFileSystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_tree(tmp_path / "root", {"a.txt": 10, "locked": {"b.txt": 999}})
    original_iterdir = RealFileSystem.iterdir

    def failing_iterdir(self: RealFileSystem, path: Path) -> Any:
        if path.name == "locked":
            raise PermissionError("Access denied")
        return original_iterdir(self, path)

    monkeypatch.setattr(RealFileSystem, "iterdir", failing_iterdir)
    # 10 from a.txt; the locked subdir is unreadable and contributes 0.
    assert directory_size(tmp_path / "root", fs=fs) == 10


def test_directory_size_recovers_from_file_stat_error(
    fs: RealFileSystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_tree(tmp_path / "root", {"good.txt": 10, "bad.txt": 999})

    real_stat = RealFileSystem.stat

    def flaky_stat(self: RealFileSystem, path: Path) -> Any:
        if path.name == "bad.txt":
            raise OSError("simulated stat failure")
        return real_stat(self, path)

    monkeypatch.setattr(RealFileSystem, "stat", flaky_stat)
    assert directory_size(tmp_path / "root", fs=fs) == 10


# --- scan: ordering, filtering, capping --------------------------------------


def test_scan_returns_largest_first(fs: RealFileSystem, tmp_path: Path) -> None:
    _make_tree(
        tmp_path / "roots",
        {
            "small": {"a.txt": 10},
            "huge": {"a.txt": 10_000},
            "medium": {"a.txt": 500},
        },
    )
    result = scan(tmp_path / "roots", fs=fs)
    assert [e.path.name for e in result] == ["huge", "medium", "small"]


def test_scan_respects_min_size(fs: RealFileSystem, tmp_path: Path) -> None:
    _make_tree(
        tmp_path / "roots",
        {
            "small": {"a.txt": 10},
            "big": {"a.txt": 1_000},
        },
    )
    result = scan(tmp_path / "roots", min_size=100, fs=fs)
    assert [e.path.name for e in result] == ["big"]


def test_scan_respects_top_n(fs: RealFileSystem, tmp_path: Path) -> None:
    _make_tree(
        tmp_path / "roots",
        {f"d{i}": {"x.txt": i * 100 + 1} for i in range(10)},
    )
    result = scan(tmp_path / "roots", top_n=3, fs=fs)
    assert len(result) == 3
    sizes = [e.size_bytes for e in result]
    assert sizes == sorted(sizes, reverse=True)


# --- scan: safety integration ------------------------------------------------


def test_scan_returns_empty_for_never_root() -> None:
    """A root on the NEVER list is refused before any IO happens."""
    assert scan(Path("C:\\Windows")) == []


def test_scan_skips_never_children(fs: RealFileSystem, tmp_path: Path) -> None:
    """A child whose path matches NEVER does not appear in the ranked list.

    We construct a fake C:\\<rare-test-id>\\Windows-like layout by
    monkeypatching is_never to consider one specific child path NEVER.
    """
    import winspace.core.scanner as scanner_module

    _make_tree(
        tmp_path / "roots",
        {"normal": {"a.txt": 1_000}, "blocked": {"b.txt": 5_000}},
    )

    real_is_never = scanner_module.is_never

    def fake_is_never(p: Path | str | None) -> tuple[bool, str]:
        if p is not None and str(p).endswith("blocked"):
            return True, "synthetic-block"
        return real_is_never(p)

    import winspace.core.scanner

    monkeypatch_target = winspace.core.scanner
    monkeypatch_target.is_never = fake_is_never
    try:
        result = scan(tmp_path / "roots", fs=fs)
    finally:
        monkeypatch_target.is_never = real_is_never
    names = [e.path.name for e in result]
    assert "blocked" not in names
    assert "normal" in names


# --- scan: missing or non-dir root ------------------------------------------


def test_scan_missing_root_returns_empty(fs: RealFileSystem, tmp_path: Path) -> None:
    assert scan(tmp_path / "missing", fs=fs) == []


def test_scan_root_is_a_file_returns_empty(fs: RealFileSystem, tmp_path: Path) -> None:
    (tmp_path / "not-a-dir.txt").write_text("hi")
    assert scan(tmp_path / "not-a-dir.txt", fs=fs) == []


def test_scan_handles_iterdir_permission_error(
    fs: RealFileSystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_tree(tmp_path / "roots", {"a": {"x.txt": 1}})

    def boom(self: RealFileSystem, path: Path) -> Any:
        raise PermissionError("denied")

    monkeypatch.setattr(RealFileSystem, "iterdir", boom)
    assert scan(tmp_path / "roots", fs=fs) == []


# --- scan: reparse children appear in survey but not in ranked output --------


def test_scan_drops_reparse_children_from_ranked_result(
    fs: RealFileSystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_tree(
        tmp_path / "roots",
        {"real_data": {"a.txt": 1_000}, "junction_like": {"b.txt": 2_000}},
    )
    monkeypatch.setattr(
        RealFileSystem,
        "is_reparse_point",
        lambda self, p: p.name == "junction_like",
    )
    result = scan(tmp_path / "roots", fs=fs)
    # junction_like is a reparse point — it must NOT appear ranked even though
    # it would otherwise be the largest.
    assert [e.path.name for e in result] == ["real_data"]


# --- scan: files (non-directories) ignored at the immediate-child layer ------


def test_scan_ignores_file_children(fs: RealFileSystem, tmp_path: Path) -> None:
    _make_tree(tmp_path / "roots", {"data": {"x.txt": 100}})
    (tmp_path / "roots" / "loose.txt").write_text("y" * 99999)
    result = scan(tmp_path / "roots", fs=fs)
    assert [e.path.name for e in result] == ["data"]


# --- scan: ScanEntry shape ---------------------------------------------------


def test_scan_entry_fields(fs: RealFileSystem, tmp_path: Path) -> None:
    _make_tree(tmp_path / "roots", {"d": {"x.txt": 42}})
    [entry] = scan(tmp_path / "roots", fs=fs)
    assert isinstance(entry, ScanEntry)
    assert entry.path == tmp_path / "roots" / "d"
    assert entry.size_bytes == 42
    assert entry.is_reparse_point is False
    assert entry.safety_rule == ""


# --- performance smoke -------------------------------------------------------


@pytest.mark.windows
def test_directory_size_of_thousand_files_under_5s(fs: RealFileSystem, tmp_path: Path) -> None:
    """Plan's performance acceptance: ~1000 small files scan in < 5s."""
    root = tmp_path / "perf"
    root.mkdir()
    for i in range(1000):
        (root / f"f{i:04d}.bin").write_bytes(b"x" * 256)
    start = time.perf_counter()
    total = directory_size(root, fs=fs)
    elapsed = time.perf_counter() - start
    assert total == 1000 * 256
    assert elapsed < 5.0, f"directory_size took {elapsed:.2f}s, expected < 5s"
